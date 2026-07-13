"""오더블록 탐지·시그널 출력 모델과 파라미터.

Fluxchart "Volumized Order Blocks" (`strategy/reference/`) 로직에 대응하는
불변 값 객체들이다. 파라미터는 원본 인디케이터 입력값과 1:1로 대응한다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ZONE_COUNT_LIMITS: dict[str, int] = {"high": 10, "medium": 5, "low": 3, "one": 1}

#: 컨플루언스 EMA 배열의 기본 세트(사용자 트레이딩뷰 설정, `indicators.DEFAULT_EMA_LENGTHS`와 동일).
DEFAULT_CONFLUENCE_EMA_LENGTHS: tuple[int, ...] = (20, 60, 120, 240, 365)


class OrderBlockDirection(StrEnum):
    """오더블록 방향. 원본의 `obType` ("Bull"/"Bear")에 대응."""

    BULLISH = "bull"
    BEARISH = "bear"


class OrderBlockParams(BaseModel):
    """오더블록 탐지 파라미터. 원본 인디케이터 설정값과 대응한다."""

    model_config = ConfigDict(frozen=True)

    swing_length: int = Field(default=10, ge=3)
    zone_invalidation: Literal["wick", "close"] = "wick"
    zone_count: Literal["high", "medium", "low", "one"] = "low"
    combine_obs: bool = True
    max_atr_mult: float = Field(default=3.5, gt=0)
    atr_length: int = Field(default=10, ge=1)
    max_order_blocks: int = Field(default=30, ge=1)
    """(WAN-47) 탐지 아카이브에는 더 이상 상한을 적용하지 않는다(전체 생애 보존).
    원본의 표시 개수 캡은 렌더 뷰의 `zone_limit`으로만 남는다. 하위호환용으로 유지."""
    max_distance_to_last_bar: int = Field(default=1750, ge=1)
    """(WAN-47) 렌더 뷰의 **최근성 필터**: 마지막 봉에서 이 봉 수 이내에 확정된 존만
    "현재 그림"(`rendered_order_blocks`)에 그린다. 원본에서는 탐지 스캔 상한이었으나,
    탐지/렌더 분리 후 아카이브(`order_blocks`)는 전체 히스토리를 스캔한다."""

    @property
    def zone_limit(self) -> int:
        """`zone_count` 문자열을 방향별 렌더/채택 개수로 변환."""
        return _ZONE_COUNT_LIMITS[self.zone_count]


class OrderBlock(BaseModel):
    """탐지된 오더블록 하나. 원본 `orderBlockInfo`에 대응.

    이 값 객체는 존의 **전체 생애주기**를 담는다 (WAN-47). 원본 인디케이터는
    깨진 뒤 되쓸린 존을 `box.delete()`로 삭제하지만, 백테스트 신호원으로 쓰려면
    생애 기록이 소실되면 안 된다(생존자 편향). 그래서 탐지기는 존을 지우지 않고
    `break_time`(무효화)·`swept_time`(소멸)·`tapped_times`(재진입)로 상태 전이만
    기록한다. "지금 차트에 그릴 박스"는 `OrderBlockResult.active_at()` 렌더링
    뷰가 이 아카이브에서 파생한다.
    """

    model_config = ConfigDict(frozen=True)

    direction: OrderBlockDirection
    top: float
    bottom: float
    start_time: int
    """존이 시작되는(박스 왼쪽 변) 봉의 `open_time`(ms). 원본 `startTime`."""
    confirmed_time: int
    """오더블록이 실제로 확정(탐지)된 봉의 `open_time`(ms). 존의 생성 시각."""
    ob_volume: float
    ob_low_volume: float
    ob_high_volume: float
    breaker: bool = False
    break_time: int | None = None
    """존이 반대편으로 돌파되어 breaker로 무효화된 봉의 `open_time`(ms). 없으면 None."""
    swept_time: int | None = None
    """breaker 존이 되쓸려 완전 소멸(원본은 `box.delete()`)한 봉의 `open_time`(ms).

    없으면 아직 차트에 존재. 현재까지 살아있는(active·breaker) 존은 None.
    무효화(`break_time`) 이후에만 발생하므로 `swept_time`이 있으면 `break_time`도 있다.
    """
    tapped_times: tuple[int, ...] = ()
    """확정 이후 가격이 존 범위(`bottom`~`top`)에 재진입(tap)한 봉들의 `open_time`(ms).

    바깥→안 전이 시각만 기록한다(존 안에 머무는 연속 봉은 첫 진입만)."""
    combined: bool = False
    """`combine_obs`로 다른 존과 병합되어 생성된 존인지 여부."""

    def alive_at(self, time_ms: int) -> bool:
        """`time_ms` 시점에 이 존이 차트에 존재(생존)하는지.

        확정 이후(`confirmed_time <= time_ms`)이고 아직 소멸하지 않았으면
        (`swept_time`이 없거나 `time_ms` 이후이면) True. breaker(무효화)는 소멸이
        아니므로, 되쓸리기 전까지는 여전히 생존으로 본다(원본이 breaker 박스를
        재색칠해 계속 그리는 것과 동일).
        """
        if time_ms < self.confirmed_time:
            return False
        return self.swept_time is None or time_ms < self.swept_time


class SignalExitReason(StrEnum):
    """전략이 계획한 청산의 사유 (WAN-23)."""

    TAKE_PROFIT = "take_profit"
    """진입가 너머 가장 가까운 EMA/VWMA 선에 도달(전량 익절)."""
    STOP_LOSS = "stop_loss"
    """진입 근거 오더블록이 breaker로 무효화(손절)."""


class PlannedExit(BaseModel):
    """전략이 진입 시점에 계획한 명시적 청산 이벤트 (WAN-23).

    익절(선 도달)·손절(오더블록 무효화)이 모두 봉마다 달라지는 **동적** 규칙이라,
    전략이 진입가를 기준으로 청산 봉·참조가·사유를 미리 산출해 시그널에 실어
    보내면 백테스트(`backtest.run_backtest`)가 이를 그대로 소비한다.
    """

    model_config = ConfigDict(frozen=True)

    time: int
    """청산이 발생하는 봉의 `open_time`(ms)."""
    price: float
    """청산 참조가. 익절=도달한 선 가격, 손절=무효화 봉 종가."""
    reason: SignalExitReason


class OrderBlockSignal(BaseModel):
    """오더블록 기반 진입 후보 시그널 (AlphaBlock 확장, 원본에는 없음)."""

    model_config = ConfigDict(frozen=True)

    direction: OrderBlockDirection
    trigger_time: int
    """가격이 오더블록 존에 재진입(tap)한 봉의 `open_time`(ms)."""
    price: float
    order_block: OrderBlock
    status: Literal["active", "cancelled"] = "active"
    planned_exit: PlannedExit | None = None
    """컨플루언스 전략이 계획한 청산(WAN-23). 없으면 백테스트의 고정 %TP/SL 경로를 따른다."""


def _merge_max_optional(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _ob_area(ob: OrderBlock, now: int) -> float:
    end = ob.break_time if ob.break_time is not None else now
    return (end - ob.start_time) * (ob.top - ob.bottom)


def obs_touch(a: OrderBlock, b: OrderBlock, now: int) -> bool:
    """두 존이 병합 대상으로 겹치는지(IoU 교집합/합집합 * 100 > 0). 원본 `doOBsTouch`.

    시간축(존의 왼쪽 변~오른쪽 변)과 가격축(bottom~top)의 교집합 면적을 합집합
    면적으로 나눈 IoU가 양수면 병합한다. `now`는 아직 무효화되지 않은 존의 오른쪽
    변(면적 계산용) 센티넬이다. 방향(obType)은 이 함수에서 검사하지 않으므로 호출부가
    같은 방향끼리만 넘겨야 한다(`combine_order_blocks`·`_generate_merged_signals`).
    """
    # 가격축 교집합을 먼저 본다 — 가격대가 다른 대다수 쌍은 여기서 즉시 걸러져
    # 시간축·면적 계산을 건너뛴다(WAN-56 성능: 매 봉 O(존²) 병합의 핫패스).
    intersection_price = min(a.top, b.top) - max(a.bottom, b.bottom)
    if intersection_price <= 0.0:
        return False
    a_end = a.break_time if a.break_time is not None else now
    b_end = b.break_time if b.break_time is not None else now
    intersection_time = min(a_end, b_end) - max(a.start_time, b.start_time)
    if intersection_time <= 0:
        return False
    intersection = intersection_time * intersection_price
    union = _ob_area(a, now) + _ob_area(b, now) - intersection
    if union <= 0:
        return False
    return (intersection / union) * 100.0 > 0


def combine_order_blocks(obs: list[OrderBlock], now: int) -> list[OrderBlock]:
    """겹치는(IoU 교집합>0) 동일 방향 존을 병합. 원본 `combineOBsFunc`에 대응.

    렌더링 뷰(`select_active`)와 백테스트 시그널(`_generate_merged_signals`,
    WAN-56)이 공유한다 — 탐지 아카이브(`order_blocks`) 자체는 원본 단위로 남긴다.
    `now`는 아직 무효화되지 않은 존의 오른쪽 변(면적 계산용) 센티넬이다.
    """

    def touch(a: OrderBlock, b: OrderBlock) -> bool:
        return obs_touch(a, b, now)

    items = list(obs)
    merged = True
    while merged:
        merged = False
        for i in range(len(items)):
            for j in range(len(items)):
                if i == j:
                    continue
                a, b = items[i], items[j]
                if a.direction != b.direction:
                    continue
                if touch(a, b):
                    new_ob = OrderBlock(
                        direction=a.direction,
                        top=max(a.top, b.top),
                        bottom=min(a.bottom, b.bottom),
                        start_time=min(a.start_time, b.start_time),
                        confirmed_time=min(a.confirmed_time, b.confirmed_time),
                        ob_volume=a.ob_volume + b.ob_volume,
                        ob_low_volume=a.ob_low_volume + b.ob_low_volume,
                        ob_high_volume=a.ob_high_volume + b.ob_high_volume,
                        breaker=a.breaker or b.breaker,
                        break_time=_merge_max_optional(a.break_time, b.break_time),
                        swept_time=_merge_max_optional(a.swept_time, b.swept_time),
                        tapped_times=tuple(sorted(set(a.tapped_times) | set(b.tapped_times))),
                        combined=True,
                    )
                    remove_indices = {i, j}
                    items = [x for k, x in enumerate(items) if k not in remove_indices]
                    items.append(new_ob)
                    merged = True
                    break
            if merged:
                break
    return items


def select_active(
    order_blocks: list[OrderBlock],
    time_ms: int,
    *,
    limit: int | None = None,
    combine: bool = False,
) -> list[OrderBlock]:
    """`time_ms` 시점에 차트에 그려질 존(렌더링 뷰)을 아카이브에서 파생한다 (WAN-47).

    각 방향별로 그 시점에 생존(`alive_at`)한 존을 최신 확정순으로 정렬해 `limit`개만
    채택하고, `combine`이면 겹치는 존을 병합한다. 트레이딩뷰 원본이 그 시점에
    그렸을 박스 집합과 동일한 패리티를 유지한다.
    """
    result: list[OrderBlock] = []
    for direction in (OrderBlockDirection.BULLISH, OrderBlockDirection.BEARISH):
        alive = [ob for ob in order_blocks if ob.direction is direction and ob.alive_at(time_ms)]
        alive.sort(key=lambda ob: ob.confirmed_time, reverse=True)
        if limit is not None:
            alive = alive[:limit]
        # 각 존을 `time_ms` 시점 상태로 클리핑한다 — 그 시점에 아직 일어나지 않은
        # 무효화/탭은 렌더에 반영되지 않아야 한다(미래 정보 유출 방지, 트레이딩뷰가
        # 그 시점에 그렸을 그림과 정확히 일치).
        clipped = [_clip_to_time(ob, time_ms) for ob in alive]
        if combine:
            clipped = combine_order_blocks(clipped, time_ms + 1)
        result.extend(clipped)
    return result


def _clip_to_time(ob: OrderBlock, time_ms: int) -> OrderBlock:
    """존의 생애주기 필드를 `time_ms` 시점까지로 자른 뷰 사본을 만든다.

    `time_ms` 이후에야 일어나는 무효화(`break_time`)는 아직 breaker가 아닌 것으로,
    아직 없던 탭은 제외해 반영한다. `alive_at`이 참인 존만 넘어오므로 소멸
    (`swept_time`)은 항상 미래(또는 없음)이라 뷰에서는 항상 None이다.
    """
    broke = ob.break_time is not None and ob.break_time <= time_ms
    tapped = tuple(t for t in ob.tapped_times if t <= time_ms)
    if broke and ob.swept_time is None and tapped == ob.tapped_times:
        return ob  # 이미 시점 상태와 동일 — 불필요한 복사 회피.
    return ob.model_copy(
        update={
            "breaker": broke,
            "break_time": ob.break_time if broke else None,
            "swept_time": None,
            "tapped_times": tapped,
        }
    )


class OrderBlockResult(BaseModel):
    """`OrderBlockDetector.run()`의 반환값 (WAN-47).

    `order_blocks`는 **생성된 모든 존의 전체 생애주기 아카이브**다(트리밍·병합·삭제
    없음). 트레이딩뷰와 동일한 "지금 그릴 박스"는 `rendered_order_blocks`(또는 임의
    시점 `active_at()`)로 파생한다. 백테스트 신호(`signals`)는 아카이브 전체를 소비해
    생존자 편향 없이 산출된다.
    """

    model_config = ConfigDict(frozen=True)

    order_blocks: list[OrderBlock]
    """생성된 모든 존의 전체 아카이브(생애주기 필드 포함, 병합 전)."""
    signals: list[OrderBlockSignal]
    rendered_order_blocks: list[OrderBlock] = Field(default_factory=list)
    """마지막 봉 시점의 렌더링 뷰(트레이딩뷰 패리티: 방향별 `zone_limit`개, 병합 적용)."""

    def active_at(
        self, time_ms: int, *, limit: int | None = None, combine: bool = False
    ) -> list[OrderBlock]:
        """`time_ms` 시점에 그려질 존을 아카이브에서 파생한다(`select_active` 위임)."""
        return select_active(self.order_blocks, time_ms, limit=limit, combine=combine)


class ConfluenceParams(BaseModel):
    """오더블록 + RSI 진입 / EMA·VWMA 선 익절 / 오더블록 무효화 손절 규칙 (WAN-23).

    사용자의 실제 매매 방식에 맞춘 규칙이다. **EMA·VWMA는 진입 판정에 쓰지 않고**
    오직 익절 목표선으로만 쓴다. 기본값은 사용자 트레이딩뷰 설정과 일치한다
    (`indicators.py` 기본값 참고). 필드는 `config.Settings`에서
    `ALPHABLOCK_CONFLUENCE__*` 환경변수로 덮어쓸 수 있다.

    ## 진입 (오더블록 탭 + RSI, 필수 조건)

    기준 신호는 활성(비-breaker) 오더블록 탭(tap). 탭 봉의 RSI가

    * **롱**(강세 오더블록): RSI ≤ `rsi_oversold`(과매도)면 진입.
    * **숏**(약세 오더블록): RSI ≥ `rsi_overbought`(과매수)면 진입.

    RSI가 워밍업 중(NaN)이면 진입하지 않는다. EMA·VWMA는 진입에 영향이 없다.

    ## 익절 (EMA/VWMA 중 진입가 너머 가장 가까운 선 도달)

    대상 선은 `tp_ema_lengths` EMA들과 `tp_vwma_length` VWMA. 진입 이후 매 봉,
    진입가 **너머**(롱은 위·숏은 아래)에 있는 대상 선들 중 **가장 가까운 선**에
    고가(롱)/저가(숏)가 도달하면 그 선 가격에서 **전량 익절**한다. 선은 봉마다
    움직이므로 매 봉 재평가한다. 진입가 너머에 대상 선이 하나도 없으면 익절 목표가
    없어 손절/청산에만 의존한다.

    ## 손절 (오더블록 무효화)

    진입 근거였던 오더블록이 breaker로 무효화되면(존 반대편 돌파 — 무효화 기준
    Wick/Close는 `OrderBlockParams.zone_invalidation`을 재사용) 그 봉에서 손절한다.

    ## 우선순위

    같은 봉에서 손절·익절이 동시에 충족되면 `stop_before_take_profit`(기본 True)이면
    **손절을 우선**한다(보수적). 한 오더블록당 진입은 첫 탭 1회로 제한된다.
    """

    model_config = ConfigDict(frozen=True)

    # --- 진입: RSI (필수 조건) ---
    rsi_length: int = Field(default=14, ge=1)
    rsi_overbought: float = Field(default=70.0, gt=0, lt=100)
    rsi_oversold: float = Field(default=30.0, gt=0, lt=100)

    # --- 익절: EMA/VWMA 목표선 ---
    use_line_take_profit: bool = True
    """익절(선 도달) 규칙 on/off. False면 손절/강제청산에만 의존."""
    tp_ema_lengths: tuple[int, ...] = DEFAULT_CONFLUENCE_EMA_LENGTHS
    """익절 목표로 쓸 EMA 길이들. 비우면 EMA 목표선 없음."""
    tp_vwma_length: int | None = Field(default=100, ge=1)
    """익절 목표로 쓸 VWMA 길이. None이면 VWMA 목표선 없음."""

    # --- 손절: 오더블록 무효화 ---
    use_order_block_stop: bool = True
    """손절(오더블록 무효화) 규칙 on/off."""

    # --- 우선순위 ---
    stop_before_take_profit: bool = True
    """동일 봉에서 손절·익절 동시 충족 시 손절 우선(보수적)."""

    # --- 진입 방식 전환 (WAN-41) ---
    entry_mode: Literal["close", "zone_limit"] = "close"
    """진입 방식. **기본 `close`(A안, 현행 보존)**: 탭 봉 종가에 시장가 진입.

    `zone_limit`(B안): 활성 오더블록 존 근단(proximal)에 지정가를 걸어 두고 가격이
    닿는 순간 체결한다(`backtest.substep`가 1분봉 서브스텝으로 시뮬레이션). 실제
    트레이딩뷰 매매(존에 닿는 순간 진입)를 재현한다.
    """
    rsi_mode: Literal["closed_bar", "realtime"] = "closed_bar"
    """RSI 판정 기준. **기본 `closed_bar`(A안, 현행 보존)**: 확정봉 RSI로 판정.

    `realtime`(B안): 체결 순간의 실시간(봉내) RSI로 판정한다(`strategy.realtime_rsi`).
    라이브·백테스트가 동일 상태 머신을 공유한다.
    """
    zone_limit_ref: Literal["proximal", "mid", "distal"] = "proximal"
    """`entry_mode=zone_limit`일 때 지정가를 걸 존 내 기준선.

    `proximal`(기본): 존 근단(롱=존 상단, 숏=존 하단) — 가장 먼저 닿는 경계.
    `mid`: 존 중앙. `distal`: 존 원단(무효화 경계에 가장 가까움 — 더 깊은 진입).
    """
    limit_valid_bars: int = Field(default=24, ge=1)
    """미체결 지정가 주문이 유효한 상위TF 봉 수. 경과하면 취소한다(`zone_limit`)."""
    cancel_limit_on_condition_fail: bool = False
    """지정가에 닿았지만 실시간 RSI 조건 미충족 시 주문을 취소할지 여부.

    기본 False면 조건이 충족될 때까지(또는 만료·무효화까지) 주문을 유지한다.
    """

    source: str = "close"

    @model_validator(mode="after")
    def _validate(self) -> ConfluenceParams:
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError("rsi_oversold는 rsi_overbought보다 작아야 합니다.")
        if any(length < 1 for length in self.tp_ema_lengths):
            raise ValueError("tp_ema_lengths의 모든 길이는 1 이상이어야 합니다.")
        if len(set(self.tp_ema_lengths)) != len(self.tp_ema_lengths):
            raise ValueError("tp_ema_lengths에 중복된 길이가 있습니다.")
        if self.use_line_take_profit and not self.tp_ema_lengths and self.tp_vwma_length is None:
            raise ValueError(
                "use_line_take_profit=True면 tp_ema_lengths 또는 tp_vwma_length 중 "
                "최소 하나의 익절 목표선이 필요합니다."
            )
        return self

    def zone_limit_price(self, order_block: OrderBlock) -> float:
        """`zone_limit_ref`에 따른 지정가(존 내 기준선)를 반환한다 (WAN-41).

        롱(강세 오더블록)은 존 상단이 근단(proximal, 먼저 닿음)·하단이 원단(distal,
        무효화 경계), 숏(약세 오더블록)은 그 반대다. `mid`는 존 중앙.
        """
        top, bottom = order_block.top, order_block.bottom
        if self.zone_limit_ref == "mid":
            return (top + bottom) / 2.0
        is_long = order_block.direction is OrderBlockDirection.BULLISH
        proximal, distal = (top, bottom) if is_long else (bottom, top)
        return proximal if self.zone_limit_ref == "proximal" else distal

    @property
    def tp_vwma_key(self) -> str | None:
        """익절 VWMA 선의 스냅샷 키(`vwma_<길이>`). 미사용이면 None."""
        return None if self.tp_vwma_length is None else f"vwma_{self.tp_vwma_length}"

    @property
    def sorted_tp_ema_lengths(self) -> list[int]:
        """익절 EMA 길이들을 오름차순 정렬한 리스트."""
        return sorted(self.tp_ema_lengths)
