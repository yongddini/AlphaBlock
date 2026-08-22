"""1분봉 서브스텝 존-지정가 진입 시뮬레이션 (WAN-41, B안).

상위TF(15m/1h/4h/1d) OHLC만으로는 봉 내부 경로를 알 수 없어 "존에 닿는 순간 진입 +
그 순간 실시간 RSI" 규칙을 검증할 수 없다. 이 모듈은 **1분봉을 서브스텝**으로 써서
봉 형성 과정을 재구성하고, 한 오더블록 셋업의 지정가 대기 → 체결 → 청산을 1분
해상도로 시뮬레이션한다.

## 규칙 (이슈 WAN-41)

1. 활성 오더블록 존 근단(proximal, `ConfluenceParams.zone_limit_price`)에 지정가를
   걸어 둔다. 각 1분 스텝에서 가격이 지정가에 **닿으면**(롱 `low <= 지정가`, 숏
   `high >= 지정가`) 체결 후보가 된다. 이 "닿으면 체결"은 **낙관적 가정**이다 —
   실거래에는 큐 우선순위가 있어 닿아도 체결되지 않을 수 있다. `penetration_bps`
   (WAN-96)로 일정 폭 관통을 요구해 이 가정을 보수화할 수 있다(기본은 현행 유지).
2. 체결 후보 스텝에서 **실시간 RSI**(`strategy.realtime_rsi`, 진행 중 상위TF 봉의
   임시 종가 = 그 1분봉 종가)를 계산해 조건을 판정한다 — 롱: `RSI <= rsi_oversold`,
   숏: `RSI >= rsi_overbought`. 충족하면 그 시점·지정가로 진입, 아니면 주문을
   유지하거나(기본) 취소(`cancel_on_condition_fail`)한다.
3. 미체결 주문은 `limit_valid_bars` 상위TF 봉이 경과하면 취소하고, 오더블록이
   무효화(`invalidation_time`)되면 즉시 취소한다.

## ⚠️ 낙관 편향 방지 (이 모듈의 핵심)

가격이 존을 **관통**해 손절선까지 내려간 1분 스텝에서는 **같은 스텝에서 체결 + 손절**이
발생한다. 이를 누락하면 "좋은 진입가만 챙기고 손실은 안 나는" 가짜 성과가 나온다.
따라서 체결이 일어난 스텝에서 곧바로 손절·익절을 재판정하며, 손절·익절이 같은 스텝에
동시 충족되면 **손절을 우선**한다(`stop_before_tp`, 보수적). 1분봉 내부 경로는 여전히
알 수 없으므로 애매하면 항상 불리한 쪽으로 가정한다.

라이브·백테스트가 동일한 `RealtimeRsi` 상태 머신을 공유하므로 실시간 RSI 값이 두
경로에서 일치한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import Protocol, runtime_checkable

import pandas as pd

from strategy.models import OrderBlockDirection, RsiGateMode, SignalExitReason, rsi_gate_passes
from strategy.realtime_rsi import RealtimeRsi

_SUBSTEP_COLUMNS = ("open_time", "high", "low", "close")


class ZoneLimitStatus(StrEnum):
    """존-지정가 셋업의 최종 상태."""

    NO_TOUCH = "no_touch"
    """유효 기간 내 지정가에 닿지 않음(또는 데이터 종료까지 미체결)."""
    CANCELLED_EXPIRED = "cancelled_expired"
    """`limit_valid_bars` 경과로 미체결 취소. `limit_valid_bars=None`이면 발생하지
    않는다(WAN-73 — 존 무효화까지 무기한 대기)."""
    CANCELLED_INVALIDATED = "cancelled_invalidated"
    """오더블록 무효화로 미체결 취소."""
    CANCELLED_CONDITION_FAILED = "cancelled_condition_failed"
    """지정가에 닿았으나 실시간 RSI 조건 미충족 + `cancel_on_condition_fail`."""
    FILLED_OPEN = "filled_open"
    """체결됐으나 데이터 종료까지 청산되지 않음(보유 중)."""
    FILLED_EXITED = "filled_exited"
    """체결 후 손절/익절로 청산 완료."""


@dataclass(frozen=True)
class SubStep:
    """1분봉 서브스텝 하나.

    `htf_bar_time`은 이 1분봉이 속한 상위TF 봉의 `open_time`(ms)으로, 이 값이 바뀌면
    직전 상위TF 봉이 마감된 것으로 보고 실시간 RSI 상태를 커밋한다.
    """

    time: int
    high: float
    low: float
    close: float
    htf_bar_time: int


class LiveLimitProvider(Protocol):
    """봉내에 **움직이는 지정가**를 공급하는 계약 (WAN-119).

    `band_bar="intrabar_live"`는 밴드의 20번째 표본이 현재가라 봉 내부에서 값이 계속
    변한다 — 즉 지정가가 탭 봉 시점에 한 번 정해지는 상수가 아니다. 이 시뮬레이터는
    오더블록·밴드·오프셋 규칙을 모르므로(셋업 하나의 체결·청산만 본다), 그 재산정을
    호출부가 이 계약으로 주입한다. 구현은 `backtest.zone_limit_backtest`에 있다.

    `RealtimeRsi`와 **같은 생애주기**를 갖는다: 상위TF 봉이 마감되면 `commit`으로 상태를
    굴리고, 매 서브스텝 `limit_price(현재가)`로 그 순간의 주문 가격을 읽는다.
    """

    def commit(self, closed_price: float) -> None:
        """상위TF 봉 마감 — 그 확정 종가로 밴드 상태를 굴린다."""
        ...

    def limit_price(self, live_price: float) -> float | None:
        """이 순간 주문판에 걸려 있는 지정가. `None`이면 **주문이 없다**.

        `None`인 경우: 밴드 워밍업이라 값을 못 내거나(WAN-75), 밴드가 존 전체보다 불리해
        진입하지 않는 구간(WAN-75 규칙 3). 밴드가 움직이므로 이 판정은 서브스텝마다
        달라질 수 있다 — 지금 주문이 없어도 다음 스텝에 생길 수 있다.
        """
        ...

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
        """체결가가 정해진 **그 순간** 산출하는 `(손절 참조가, 익절 목표가)`.

        진입가가 봉내에 정해지므로 1R(진입가→무효화 경계)도, 그 배수인 고정 R 익절
        목표도 체결 전에는 알 수 없다. 익절이 `None`이면 목표 없음(무효화까지 홀딩)이고,
        **반환값 전체가 `None`이면** 이 셋업은 유효한 청산 규칙을 못 만든다는 뜻이라
        진입하지 않는다(WAN-143: `stop_loss_override`가 장벽을 못 낼 때).

        ⚠️ 손절과 익절을 **한 번에** 내는 이유는 순서 의존을 없애기 위해서다 — 익절
        오버라이드(WAN-137/143)는 손절가를 문맥으로 받으므로 손절이 먼저 정해져야
        하는데, 두 메서드로 나누면 호출 순서가 조용한 계약이 된다.
        """
        ...


@runtime_checkable
class PathProbeProvider(Protocol):
    """봉내 지정가를 **부작용 없이** 조회할 수 있는 공급자 (WAN-328, 측정 전용).

    `LiveLimitProvider`의 **선택적 확장**이다 — 필수 멤버로 넣지 않는 이유는 그러면 기존
    라이브·테스트 공급자 전부가 측정 전용 메서드 하나 때문에 계약 위반이 되기 때문이다.
    `observe_path_fill`을 켠 호출만 이 계약을 요구하고, 없으면 **거부한다**(조용히 관측을
    건너뛰면 표에 빈칸이 생긴 이유를 알 수 없다).

    `probe_limit`은 `limit_price`와 같은 값을 내되 내부 상태(인과 모드의 지연선)를 굴리지
    않는다 — 봉내 경로를 되짚어 여러 번 물어보는 조회라, 그 조회가 상태를 굴리면 시뮬레이션
    자체가 달라져 **관측이 대상을 바꾼다**.
    """

    def probe_limit(self, live_price: float) -> float | None:
        """`live_price`를 밴드 표본으로 썼을 때의 지정가(주문이 없으면 `None`)."""
        ...


@dataclass(frozen=True)
class PartialExit:
    """부분 청산 체결 하나 (WAN-323 반익절 래더).

    `fraction`은 **진입 수량 대비 비율**이다(잔량 대비가 아니다) — 래더가 2단이라 비율의
    합이 1을 넘지 않고, 호출부가 수량·수수료를 진입 수량 하나로 환산할 수 있다.
    """

    time: int
    price: float
    fraction: float
    reason: SignalExitReason


@dataclass(frozen=True)
class ZoneLimitOutcome:
    """`simulate_zone_limit_trade`의 결과."""

    status: ZoneLimitStatus
    entry_time: int | None = None
    entry_price: float | None = None
    entry_rsi: float | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: SignalExitReason | None = None
    mfe_r: float | None = None
    """보유 구간의 최대유리이탈(MFE), **R 단위**(WAN-90). 체결됐을 때만 값이 있다.

    1R = 진입가 → 무효화 경계(손절 참조가)까지의 거리다. 롱이면
    `(구간 최고가 − 진입가) / 1R`, 숏이면 `(진입가 − 구간 최저가) / 1R`. 구간은
    체결 스텝부터 청산 스텝까지(둘 다 포함)이며 **청산 이후 봉은 보지 않는다**(look-ahead
    금지). 손절/익절이 진입가에 매우 가깝게 붙지 않는 한 보통 0 이상이지만, 진입 봉에서
    곧바로 손절된 경우 음수가 될 수 있다(순수 관측값이라 0에서 절단하지 않는다).

    ⚠️ 이 값은 **실제로 일어난 청산까지의** 경로만 잰다 — 고정 R 익절이 켜진 채택
    엔진에서는 승자가 익절 목표에서 잘리므로 MFE도 대체로 그 목표 부근에서 검열된다.
    "거래가 익절 없이 어디까지 갔는가"를 보려면 익절을 끈(먼 목표) 실행에서 재야 한다.
    """
    mae_r: float | None = None
    """보유 구간의 최대불리이탈(MAE), **R 단위**(WAN-90). 체결됐을 때만 값이 있다.

    롱이면 `(구간 최저가 − 진입가) / 1R`, 숏이면 `(진입가 − 구간 최고가) / 1R`. 통상
    0 이하이며, 손절로 청산된 거래는 손절선을 관통했다면 −1R 아래로도 내려갈 수 있다.
    """
    stop_price: float | None = None
    """이 거래에 **실제로 적용된** 손절 참조가 (WAN-143). 체결됐을 때만 값이 있다.

    보통은 호출부가 넘긴 `stop_price` 그대로다. `live_limit`(봉내 라이브 밴드)에서
    `stop_loss_override`가 걸려 있으면 손절이 **체결 순간**에 정해지므로, 호출부가
    1R 사이징에 쓸 값을 여기로 돌려준다 — 지어내지 않게 하려는 것이다.
    """
    take_profit_price: float | None = None
    """이 거래에 **실제로 적용된** 익절 목표가 (WAN-346). 체결됐을 때만 값이 있다.

    위 `stop_price`의 거울이다 — 1R이 체결가에서야 정해지므로(WAN-143 `resolve_exits`)
    익절 목표도 체결 순간에 확정되고, 그 값을 지어내지 않고 그대로 돌려준다. 익절이 꺼진
    변형(`take_profit_price=None`)에서는 체결돼도 `None`이다.

    **순수 관측이다** — 체결·청산·손익 어디에도 쓰이지 않고, 값을 싣는 것만으로는 어떤
    기존 수치도 움직이지 않는다(WAN-90 `mfe_r` · WAN-276 `exit_extreme`과 같은 부류).
    북 거래별 CSV(WAN-346 §0)가 「손절가·익절가」 열을 지어내지 않게 하려는 것이다.
    """
    exit_extreme: float | None = None
    """손절로 청산된 봉의 **불리 극값**(롱=저가, 숏=고가) (WAN-276). 손절 체결 시에만 값이
    있고 익절·데이터종료 청산이면 `None`이다.

    손절 갭-체결 민감도(WAN-276) 측정용 순수 관측값이다: 시장가 손절 슬리피지 α 스윕은
    `exit_price = active_stop − α·(active_stop − exit_extreme)`(롱, 숏 대칭)라, 이 극값만
    있으면 후보를 다시 시뮬레이션하지 않고 α를 사후 변환으로 얹을 수 있다(`stop_slippage_alpha`
    엔진 인자와 같은 값을 낸다). α=0(기본)에서는 `exit_price`가 `active_stop` 그대로라 이
    필드가 있어도 손익은 비트 단위로 불변이다."""
    exit_at_breakeven: bool = False
    """손절 청산이 **본절 스탑**(진입가로 옮긴 것)에서 났는지 (WAN-323).

    참이면 **존 무효화 경계는 건드려지지 않았다** — 우리가 스스로 일찍 나온 것이라 그
    오더블록은 아직 살아 있다. 재진입 배선이 「익절이면 재무장, 손절이면 존 종료」로
    갈라지므로(WAN-228/273) 이 구분이 없으면 본절 청산이 **멀쩡한 존을 죽인다**.
    래더를 안 켜면 손절선이 안 움직여 언제나 거짓이다."""
    partial_exits: tuple[PartialExit, ...] = ()
    """부분 청산 체결들 (WAN-323, 옵트인). 래더를 안 켜면 **항상 비어 있다**.

    `partial_take_profit_r`을 주면 진입 순간 확정된 1R의 그 배수 지점에서 진입 수량의
    `partial_take_profit_fraction`만큼을 먼저 청산하고 잔량을 계속 끌고 간다. 이 튜플에
    담긴 체결은 최종 청산(`exit_time`/`exit_price`)과 **별개**이며, 최종 청산은 남은
    수량에 대한 것이다 — 손익 환산은 호출부(`backtest.zone_limit_backtest._to_trade`)가
    진입 수량 × 각 비율로 한다.

    ⚠️ **같은 봉에서 분할 지점과 손절이 동시 도달하면 손절이 이겨 부분 청산은 일어나지
    않는다**(기존 `stop_before_tp` 관행과 같은 보수적 처리) — 1분봉 안의 경로를 모르므로
    애매하면 불리한 쪽으로 간다."""
    path_fill_price: float | None = None
    """같은 체결 봉을 **틱 추종 모델**로 봤을 때의 체결가 (WAN-328, 측정 전용 · 옵트인).

    `observe_path_fill=True`일 때만 값이 있고(그 밖에는 항상 `None`), **손익·체결 판정에는
    쓰이지 않는다** — `exit_extreme`(WAN-276)과 같은 순수 관측 필드다.

    엔진은 봉내 라이브 밴드의 표본으로 **1분봉 종가**를 쓰면서 터치는 **저가**로 판정한다
    (롱). 라이브 러너는 틱마다 같은 가격으로 표본과 터치를 **동시에** 굴리므로 그 비대칭이
    없다(WAN-256 틱 피드 기본). 이 필드는 그 차이를 같은 봉 안에서 잰다: 가격이 봉 범위를
    지나는 동안 `p <= 지정가(p)`(롱)가 처음 성립하는 고정점 `p*`이 틱 추종 체결가다.

    ⚠️ **1분봉 OHLC 근사이지 틱 데이터가 아니다**(WAN-98 Canceled) — 봉 안의 경로를 모르므로
    「가격이 [저가, 고가]의 모든 값을 지난다」는 연속성만 쓴다. 같은 봉 안의 체결가 차이만
    재고, 틱 모델이 **더 이른 봉**에서 체결했을 가능성은 재지 않는다."""
    order_rested: bool = True
    """이 셋업에 주문이 **한 번이라도 주문판에 걸렸는지** (WAN-119).

    상수 지정가(`limit_price`)면 언제나 참이다 — 주문 가격이 탭 봉에서 이미 정해져 있다.
    `live_limit`이면 밴드가 움직이며 "지금은 주문 없음"(워밍업·WAN-75 규칙 3 기각)이
    나올 수 있고, 끝까지 한 번도 걸리지 않으면 거짓이다.

    체결률(`filled/eligible`)의 **분모를 모드 간에 맞추는** 값이다: 정적 모드는 밴드가
    기각한 셋업을 탭 봉에서 걸러내 분모에 넣지 않는데, live 모드가 그것까지 세면 같은
    표의 체결률 열이 서로 다른 것을 재게 된다."""

    @property
    def filled(self) -> bool:
        return self.status in (ZoneLimitStatus.FILLED_OPEN, ZoneLimitStatus.FILLED_EXITED)


def build_substeps(df_1m: pd.DataFrame, htf_ms: int) -> list[SubStep]:
    """1분봉 DataFrame과 상위TF 주기(ms)로 `SubStep` 리스트를 만든다.

    각 1분봉의 상위TF 봉 시각은 `floor(open_time / htf_ms) * htf_ms`로 정렬한다.
    입력은 `open_time`(ms)·`high`·`low`·`close` 컬럼을 가지며 시간 오름차순으로
    정렬한다. `closed` 컬럼이 있으면 확정봉만 사용한다.
    """
    if htf_ms <= 0:
        raise ValueError(f"htf_ms는 양수여야 합니다: {htf_ms}")
    missing = [c for c in _SUBSTEP_COLUMNS if c not in df_1m.columns]
    if missing:
        raise ValueError(f"1분봉 DataFrame에 필요한 컬럼이 없습니다: {missing}")
    frame = df_1m
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    frame = frame.sort_values("open_time")
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    highs = [float(v) for v in frame["high"].astype(float).tolist()]
    lows = [float(v) for v in frame["low"].astype(float).tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    return [
        SubStep(time=t, high=h, low=lo, close=c, htf_bar_time=(t // htf_ms) * htf_ms)
        for t, h, lo, c in zip(times, highs, lows, closes, strict=True)
    ]


def _path_fill_price(
    live_limit: PathProbeProvider,
    step: SubStep,
    *,
    is_long: bool,
    grid: int = 16,
    refine: int = 40,
) -> float | None:
    """이 봉을 **틱 추종**으로 봤을 때의 체결가 (WAN-328, 측정 전용).

    지정가 `L(p)`는 밴드 표본 `p`에 대해 단조 증가한다(SMA20의 20번째 표본이 `p`다). 롱은
    `p <= L(p)`가 성립하는 순간 체결되므로 체결 집합은 `(-inf, p*]`이고, 가격이 위에서
    내려오며 처음 만나는 점이 고정점 `p*`다 — 그 순간의 체결가는 `L(p*) = p*`. 봉 전체가
    이미 체결 집합 안이면(`고가`에서도 성립) 봉이 열리자마자 체결이라 `L(고가)`를 낸다.

    `L(p)`가 `None`인 구간(밴드가 존보다 불리 = 주문 없음, WAN-75 규칙 3)이 있으므로
    구간을 격자로 훑어 「주문이 있고 이미 닿은」 가장 유리한 점을 찾고, 그 이웃과의 사이만
    이분법으로 좁힌다. 두 끝의 주문 유무가 갈리면 좁히지 않고 격자 점을 그대로 낸다
    (근사임을 숨기지 않는다 — 격자 간격이 그 셋업의 해상도 한계다).
    """
    lo, hi = float(step.low), float(step.high)
    if not (hi >= lo):  # pragma: no cover - 데이터 이상
        return None

    def reached(price: float) -> float | None:
        """주문이 있으면 `지정가 - 가격`(롱) 부호 여유, 없으면 None. >= 0 이면 체결."""
        limit = live_limit.probe_limit(price)
        if limit is None:
            return None
        return (limit - price) if is_long else (price - limit)

    # 가격이 지나는 순서: 롱은 고가 → 저가(내려오며 체결), 숏은 저가 → 고가.
    span = hi - lo
    points = [
        (hi - span * i / grid) if is_long else (lo + span * i / grid) for i in range(grid + 1)
    ]
    hit_at: int | None = None
    for i, price in enumerate(points):
        slack = reached(price)
        if slack is not None and slack >= 0.0:
            hit_at = i
            break
    if hit_at is None:
        return None
    if hit_at == 0:
        # 봉이 열리는 쪽 끝에서 이미 체결 — 그 순간 걸려 있던 지정가가 체결가다.
        return live_limit.probe_limit(points[0])
    near, far = points[hit_at - 1], points[hit_at]
    if reached(near) is None:
        return live_limit.probe_limit(far)  # 미정의 구간과 맞물렸다 — 격자 해상도로 답한다.
    for _ in range(refine):
        mid = (near + far) / 2.0
        slack = reached(mid)
        if slack is None:
            break
        if slack >= 0.0:
            far = mid
        else:
            near = mid
    return live_limit.probe_limit(far)


def simulate_zone_limit_trade(
    *,
    direction: OrderBlockDirection,
    limit_price: float | None = None,
    live_limit: LiveLimitProvider | None = None,
    stop_price: float,
    substeps: Sequence[SubStep],
    start: int = 0,
    rsi_state: RealtimeRsi,
    rsi_oversold: float,
    rsi_overbought: float,
    take_profit_price: float | None = None,
    limit_valid_bars: int | None = 24,
    invalidation_time: int | None = None,
    cancel_on_condition_fail: bool = False,
    stop_before_tp: bool = True,
    rsi_gate_mode: RsiGateMode = "extreme",
    rsi_neutral_band: tuple[float, float] = (40.0, 60.0),
    penetration_bps: float = 0.0,
    first_tap_free: bool = False,
    stop_slippage_alpha: float = 0.0,
    limit_stop_nonfill: bool = False,
    partial_take_profit_r: float | None = None,
    partial_take_profit_fraction: float = 0.5,
    breakeven_after_partial: bool = False,
    observe_path_fill: bool = False,
    no_same_step_tp: bool = False,
    no_same_step_tp_minutes: frozenset[int] | None = None,
) -> ZoneLimitOutcome:
    """한 오더블록 셋업의 존-지정가 진입·청산을 1분 서브스텝으로 시뮬레이션한다.

    `rsi_state`는 이 셋업의 첫 서브스텝이 속한 상위TF 봉 **직전까지** 확정봉으로
    시딩돼 있어야 한다(`RealtimeRsi.seed_from_closed`). 서브스텝이 상위TF 봉 경계를
    넘을 때마다 직전 봉 종가를 커밋해 상태를 굴린다. `rsi_state`는 호출 중 갱신되므로
    재사용하려면 복사해 넘긴다.

    반환값은 체결·취소·청산 여부와 진입/청산 시각·가격·사유를 담는다. 수수료·슬리피지
    등 비용 모델은 이 시뮬레이터의 관심사가 아니며, 집계 계층에서 A·B 동일하게
    적용한다.

    `penetration_bps`(WAN-96)를 0보다 크게 주면 가격이 지정가를 그만큼(bp) **관통해야**
    체결로 인정한다 — 기본값 0.0은 현행 "닿으면 체결"이다. 체결가는 관통 여부와 무관하게
    항상 `limit_price`다(관통은 체결 여부의 대리 변수일 뿐 더 유리한 체결가가 아니다).

    `first_tap_free`(WAN-100)는 이 셋업이 존(병합 존 포함) 확정 후 **첫 탭**이라는
    호출부의 통보다 — `rsi_gate_mode="first_tap_free"`(WAN-81~122 기본값)의 첫 탭 면제는
    `tap_index`를 아는 호출부만 판정할 수 있고, 이 시뮬레이터는 셋업 하나만 보므로
    스스로 알 수 없다. 참이면 RSI 게이트를 건너뛰고 **워밍업(RSI None)이어도** 지정가
    터치 즉시 체결한다(따라서 `cancel_on_condition_fail`의 조건 실패 취소도 타지 않는다).

    `rsi_gate_mode="unconditional"`(WAN-123 채택 기본값)은 그 면제를 **모든 탭**으로
    넓힌 것이라 `first_tap_free`와 같은 자리에서 판정한다 — 호출부의 통보가 필요 없다
    (탭 순서를 안 보므로 시뮬레이터가 스스로 안다). ⚠️ `"none"`은 이것과 **다르다**:
    게이트 판정만 통과시킬 뿐 `live_rsi is not None`(워밍업) 요구는 그대로라 워밍업
    구간 탭이 막힌다(WAN-114 `L0r`이 그 의미로 고정돼 있다).

    지정가는 `limit_price`(상수) **또는** `live_limit`(봉내 재산정, WAN-119) 중 정확히
    하나로 준다. `live_limit`을 쓰면 익절 목표도 체결 순간에 그 계약이 내므로
    `take_profit_price`를 함께 줄 수 없다 — 둘 다 주면 어느 쪽이 이겼는지 결과만 보고는
    알 수 없어(WAN-95의 "라벨과 실제 실행이 갈라진다") 조용히 무시하지 않고 거부한다.

    `live_limit`이면 **손절 참조가도 체결 순간에** 그 계약이 낼 수 있다(WAN-143
    `resolve_exits`). 인자 `stop_price`는 그때까지의 기본값(존 무효화 경계)이고, 계약이
    다른 값을 내면 그것이 청산·MFE/MAE 기준이 되며 `ZoneLimitOutcome.stop_price`로
    돌려준다. 계약이 `None`을 내면 유효한 청산 규칙이 없다는 뜻이라 체결시키지 않고
    `CANCELLED_CONDITION_FAILED`로 끝낸다(정적 경로가 탭 봉에서 셋업을 빼는 것의 봉내 판(版)).

    ## 손절 갭-체결 민감도 (WAN-276, 옵트인 · 기본은 현행과 비트 동일)

    현행 엔진은 손절 발동 봉에서 손절가 `active_stop` **그 값**으로 체결한다 — "지정가처럼
    정확한 가격 + 시장가처럼 무조건 체결"을 공짜로 가정한 것이다. 급락 갭 날엔 그 조합이
    현실에 없다. 두 인자로 그 가정을 보수화한다(둘은 다른 청산 모델이라 함께 켤 수 없다).

    * `stop_slippage_alpha`(팔 1, **시장가 손절 슬리피지**) ∈ [0, 1]: 손절이 발동한 봉에서
      `exit_price = active_stop − α·(active_stop − step.low)`(롱, 숏 대칭 = 봉 고가 쪽)로
      체결한다. α=0(기본)이면 `active_stop` 그대로라 예전과 비트 동일하고, α=1이면 봉 저가
      = 1분 해상도 안의 최악 체결이다. 체결 봉·시각은 안 바뀌고 체결 **가격**만 나빠진다.
    * `limit_stop_nonfill`(팔 2, **지정가 손절 미체결**): 손절 봉이 손절가를 **갭 관통**
      (롱: 봉 전체가 손절가 아래 = `step.high < active_stop`)하면 그 봉에서 미체결로 두고
      포지션을 계속 끌고 간다. 이후 가격이 손절가로 되돌아온 봉(롱: `step.high >= active_stop`)
      에서 **손절가 그대로**(지정가는 자기 가격에 체결 — 슬리피지 없음) 청산하고, 끝까지
      안 돌아오면 데이터 종료까지 홀드한다(`FILLED_OPEN` → 호출부가 마지막 종가로 강제 청산).
      봉 범위가 손절가를 품는 정상 터치(`step.low <= active_stop <= step.high`)는 예전처럼
      즉시 손절가 체결이라, 팔 2는 **진짜 갭 관통 봉에서만** 현행과 갈린다. α는 지정가
      체결에 안 붙으므로 두 인자를 동시에 주는 것은 무의미해 거부한다.

    ## 반익절 래더 + 본절 스탑 (WAN-323, 옵트인 · 기본은 현행과 비트 동일)

    `partial_take_profit_r`(양수)을 주면 **분할 지점**(롱 `진입가 + k·1R`, 숏 대칭)에서
    진입 수량의 `partial_take_profit_fraction`(기본 0.5)을 먼저 청산하고 잔량을 원래 익절
    목표까지 끌고 간다. 체결은 `ZoneLimitOutcome.partial_exits`에 남고, 최종 청산
    (`exit_price`·`exit_reason`)은 **잔량**에 대한 것이다.

    * **룩어헤드 없음** — 1R은 체결 순간 확정되는 `|진입가 − 손절 참조가|`이고 분할 지점은
      그 배수라 진입 시점에 전부 계산된다(봉내 라이브 밴드 계약 유지, WAN-132/143).
    * **손절 우선** — 같은 스텝에서 분할 지점과 손절이 동시 도달하면 **손절이 이긴다**
      (부분 청산 없이 전량 손절). 기존 `stop_before_tp`와 같은 보수적 관행이다.
    * `breakeven_after_partial`을 켜면 첫 부분 청산 **직후** 손절을 **진입가**로 옮긴다.
      옮긴 스탑은 **그다음 스텝부터** 적용된다 — 부분 체결이 관측된 뒤에야 주문을 옮길 수
      있고, 그 1분봉 안의 경로는 알 수 없기 때문이다(그 스텝의 손절 판정은 이미 원래
      손절선으로 끝났다). 본절 스탑은 부분 익절이 있어야 뜻이 있으므로 래더 없이 켜면
      거부한다(라벨만 붙는 조용한 실패 방지 — WAN-95/112/123 관행).
    * **MFE/MAE·`stop_price`·사이징 기준 1R은 진입 시점 값으로 고정**된다 — 본절 이동이
      그 자를 갈아치우면 R 단위 지표가 팔마다 다른 것을 재게 된다.

    ## 진입 스텝 익절 금지 (WAN-336, 옵트인 · 기본은 현행과 비트 동일)

    `no_same_step_tp`를 켜면 **체결된 바로 그 1분 스텝에서는 익절(과 분할 지점)을 판정하지
    않는다** — 다음 스텝부터 평소대로 본다.

    왜 축이 되나: 1분봉은 그 1분의 시·고·저·종 **네 숫자만** 알려 주고 **그 안의 순서는
    모른다**. 롱 지정가 진입은 가격이 **내려와야** 체결되고 고정 R 익절은 **올라가야** 닿으므로,
    「같은 1분에 진입 + 익절」이 성립하려면 **저가가 먼저 · 고가가 나중**이어야 한다. 기본
    엔진은 그렇다고 **가정**한다(체결 직후 같은 스텝에서 곧바로 청산을 재판정한다). 반대로
    손절 쪽에는 이미 보수성이 있다 — 같은 스텝에서 손절·익절이 함께 닿으면 `stop_before_tp`
    가 손절을 이기게 하고, 진입과 손절이 같은 1분인 건수는 WAN-46 감사(`penetrations`)가
    센다. **익절 쪽에는 그 장치가 없었다.**

    ## 표적 반사실 (WAN-359, 옵트인 · 위와 함께 줄 수 없다)

    `no_same_step_tp_minutes`에 **1분 스텝 `open_time`의 집합**을 주면 그 분에 체결된
    셋업에서만 같은 스텝 익절을 막는다 — `no_same_step_tp`가 「전부 끔」(반대쪽 극단)이라면
    이쪽은 「틱이 지지하지 않는 그 거래들만 끔」이다(WAN-348이 잰 판정을 실제 회계에 얹는다).

    * **표적 단위가 (칸, 1분)인 이유는 증거의 단위가 그것**이기 때문이다 — 자료는 그 1분의
      체결내역이고, 같은 분에 여러 번 체결하는 재진입 사슬은 진입가·익절가가 같아 틱으로
      갈리지 않는다. 집합은 **칸(종목·TF)별로 걸러서** 줘야 한다(이 시뮬레이터는 자기가 어느
      칸인지 모른다).
    * `None`(기본)이면 이 검사를 아예 하지 않아 **비트 단위로 예전과 같다**.

    * ⚠️ **이것도 진값이 아니라 반대쪽 극단이다.** 순서가 실제로 반대였다면 그 거래는
      「손실」이 아니라 **더 오래 보유**이고 결과는 미지다 — 이 팔은 그 미지를 「그 스텝에는
      익절 없음」으로 눌러 본 것뿐이다. 진값은 두 극단 사이에 있고 **그 폭**이 산출물이다.
      해상도로 좁히는 것은 틱·호가(WAN-98, Canceled) 소관이다.
    * 🚨 **체결 보수화(`penetration_bps`)로는 이 축이 안 잡힌다** — 그쪽은 *「주문이 채워지느냐」*
      (큐 우선순위)를 묻고 이건 *「채워진 뒤 그 1분 안의 순서」*를 묻는다. 다른 질문이라 이
      저장소의 모든 체결 보수화 관문이 이 낙관을 통과시켜 왔다.
    * **손절은 그대로 진입 스텝에서 판정한다** — 익절만 미루는 것이 이 팔의 정의다(양쪽을
      다 미루면 그냥 진입을 한 스텝 늦춘 것이 되어 다른 실험이 된다).
    * 끄면(기본) `just_entered` 가지가 통째로 죽어 **예전과 비트 단위로 같다**.
    """
    if not substeps:
        # 서브스텝이 없으면 live 밴드는 값을 낼 기회조차 없었다 = 주문이 걸린 적 없다.
        return ZoneLimitOutcome(status=ZoneLimitStatus.NO_TOUCH, order_rested=live_limit is None)
    if penetration_bps < 0.0:
        raise ValueError(f"penetration_bps는 음수일 수 없습니다: {penetration_bps}")
    if not 0.0 <= stop_slippage_alpha <= 1.0:
        raise ValueError(f"stop_slippage_alpha는 [0, 1] 범위여야 합니다: {stop_slippage_alpha}")
    if limit_stop_nonfill and stop_slippage_alpha != 0.0:
        # 지정가는 자기 가격에 체결(슬리피지 없음)이라 α와 함께 켜는 것은 모순이다(WAN-276).
        raise ValueError(
            "limit_stop_nonfill(지정가 미체결)과 stop_slippage_alpha(시장가 슬리피지)는 "
            "다른 청산 모델이라 함께 켤 수 없습니다(WAN-276)."
        )
    if partial_take_profit_r is not None and partial_take_profit_r <= 0.0:
        raise ValueError(f"partial_take_profit_r은 양수여야 합니다: {partial_take_profit_r}")
    if not 0.0 < partial_take_profit_fraction < 1.0:
        raise ValueError(
            "partial_take_profit_fraction은 (0, 1) 범위여야 합니다(전량 청산은 래더가 "
            f"아니다): {partial_take_profit_fraction}"
        )
    if breakeven_after_partial and partial_take_profit_r is None:
        # 부분 익절이 없으면 "첫 부분 청산 직후"가 영영 오지 않아 아무 일도 안 한다 —
        # 라벨만 붙은 채 현행 엔진이 도는 조용한 실패라 거부한다(WAN-95/112/123 관행).
        raise ValueError(
            "breakeven_after_partial은 partial_take_profit_r 없이는 아무 동작도 하지 "
            "않습니다(WAN-323)."
        )
    if no_same_step_tp and no_same_step_tp_minutes is not None:
        # 「전부 끔」과 「이 분들만 끔」은 같은 스위치의 두 값이라 함께 주면 어느 쪽이
        # 이겼는지 결과만 보고는 알 수 없다 — 조용히 하나를 고르지 않고 거부한다
        # (WAN-95/112/123/159 관행).
        raise ValueError(
            "no_same_step_tp(전부)와 no_same_step_tp_minutes(표적)는 같은 축의 두 값이라 "
            "함께 줄 수 없습니다(WAN-359)."
        )
    if (limit_price is None) == (live_limit is None):
        raise ValueError("limit_price와 live_limit 중 정확히 하나를 줘야 합니다.")
    if observe_path_fill and not isinstance(live_limit, PathProbeProvider):
        # 상수 지정가는 봉내에 안 움직여 「틱 추종 체결가」가 정의되지 않고, 공급자가
        # `probe_limit`을 안 내면 되짚을 방법이 없다 — 켜 봐야 언제나 None이라 라벨만
        # 붙는다(WAN-95/112/123 관행: 조용히 무시하지 않는다).
        raise ValueError(
            "observe_path_fill은 probe_limit을 내는 live_limit(봉내 라이브 밴드)에서만 "
            "뜻이 있습니다(WAN-328)."
        )
    if live_limit is not None and take_profit_price is not None:
        raise ValueError(
            "live_limit을 쓰면 익절 목표는 체결 순간에 산출되므로 "
            "take_profit_price를 함께 줄 수 없습니다."
        )

    is_long = direction is OrderBlockDirection.BULLISH

    # WAN-90: 보유 구간의 유리/불리 극값을 추적해 MFE/MAE를 R 단위로 낸다. 체결 스텝부터
    # 청산 스텝까지(둘 다 포함)의 서브스텝 고가/저가만 보고, 청산 이후는 보지 않는다.
    hold_high: float | None = None
    hold_low: float | None = None

    def _excursions() -> tuple[float | None, float | None]:
        """추적한 극값으로 (MFE_R, MAE_R)을 낸다. 1R을 못 재면 (None, None).

        1R은 **진입 시점** 손절 참조가로 고정한다(WAN-323) — 본절 스탑이 손절선을 옮겨도
        자가 바뀌면 안 되기 때문이다. 래더를 안 켜면 `active_stop`은 체결 후 불변이라
        예전과 같은 값이다.
        """
        if hold_high is None or hold_low is None or entry_price is None:
            return None, None
        risk = entry_risk if entry_risk is not None else abs(entry_price - active_stop)
        if risk <= 0:
            return None, None
        if is_long:
            return (hold_high - entry_price) / risk, (hold_low - entry_price) / risk
        return (entry_price - hold_low) / risk, (entry_price - hold_high) / risk

    def _fill_trigger(price: float) -> float:
        """체결로 인정할 가격 문턱. 롱은 지정가 아래로, 숏은 위로 그만큼 관통해야 한다."""
        penetration = price * (penetration_bps / 10_000.0)
        return price - penetration if is_long else price + penetration

    # 상수 지정가면 문턱도 상수다(`live_limit`이면 서브스텝마다 다시 낸다).
    static_trigger = None if limit_price is None else _fill_trigger(limit_price)
    # 청산 판정에 쓰는 익절 목표·손절선. `live_limit`이면 둘 다 체결 순간에 정해진다(WAN-143).
    active_tp = take_profit_price
    active_stop = stop_price

    def _stop_fill_price(extreme: float) -> float:
        """arm(1) 시장가 손절 슬리피지: 손절가에서 봉 극값 쪽으로 α만큼 나쁘게 체결(WAN-276).

        롱은 `active_stop − α·(active_stop − 봉저가)`, 숏은 `active_stop + α·(봉고가 −
        active_stop)`. α=0이면 `active_stop` 그대로다. `extreme`은 손절 봉의 불리 극값
        (롱=저가, 숏=고가)이고 손절 발동 봉에서 `active_stop`보다 불리하므로 슬리피지는 항상
        비음(≥0)이다."""
        if is_long:
            return active_stop - stop_slippage_alpha * (active_stop - extreme)
        return active_stop + stop_slippage_alpha * (extreme - active_stop)

    # WAN-276 팔 2: 지정가 손절이 갭 관통으로 미체결돼 손절가 복귀를 기다리는 상태.
    stop_armed = False

    # 상수 지정가는 탭 봉부터 이미 주문판에 걸려 있다. live는 밴드가 값을 낸 순간부터다.
    order_rested = live_limit is None
    # WAN-203 성능: 호출부가 전체 `substeps`(수백만 개)와 시작 오프셋 `start`를 넘긴다 —
    # `substeps[start:]`로 복사하면 후보마다 O(전체 길이)라 15m·긴 창이 초선형으로 느려진다.
    # `islice`로 복사 없이 `start`부터 순회하면 **비트 단위로 동일**(같은 순서·같은 원소).
    current_htf = substeps[start].htf_bar_time
    htf_elapsed = 0  # 주문 이후 마감된 상위TF 봉 수
    running_close: float | None = None
    position_open = False
    path_fill_price: float | None = None
    entry_time: int | None = None
    entry_price: float | None = None
    entry_rsi: float | None = None
    # WAN-323 래더 상태. 래더를 안 켜면 `partial_price`가 끝까지 None이라 전부 비활성이다.
    entry_stop: float | None = None
    """진입 시점 손절 참조가 — 1R 사이징·MFE/MAE의 자. 본절 이동이 이 값을 바꾸지 않는다."""
    entry_tp: float | None = None
    """진입 시점 익절 목표가(WAN-346, 순수 관측). 익절이 꺼져 있으면 체결돼도 None이다."""
    entry_risk: float | None = None
    partial_price: float | None = None
    partials: list[PartialExit] = []
    pending_breakeven = False

    def _entry_stop() -> float:
        """호출부에 돌려줄 손절 참조가(진입 시점 값). 체결 전이면 현재 손절선."""
        return entry_stop if entry_stop is not None else active_stop

    for step in islice(substeps, start, None):
        # WAN-336: 이 스텝에서 방금 체결됐는가 — `no_same_step_tp`가 익절 판정을 미루는
        # 단 하나의 조건이다. 매 스텝 초기화하므로 체결 다음 스텝부터는 거짓이다.
        just_entered = False
        # 상위TF 봉 경계: 직전 봉을 확정 종가로 커밋하고 경과 봉 수를 늘린다.
        if step.htf_bar_time != current_htf:
            if running_close is not None:
                rsi_state.commit(running_close)
                if live_limit is not None:
                    live_limit.commit(running_close)
            current_htf = step.htf_bar_time
            htf_elapsed += 1
        running_close = step.close

        if not position_open:
            # 미체결 취소: 오더블록 무효화가 먼저(보수적), 그다음 유효기간 경과.
            if invalidation_time is not None and step.time >= invalidation_time:
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.CANCELLED_INVALIDATED, order_rested=order_rested
                )
            if limit_valid_bars is not None and htf_elapsed >= limit_valid_bars:
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.CANCELLED_EXPIRED, order_rested=order_rested
                )

            if live_limit is None:
                assert static_trigger is not None
                current_limit, fill_trigger = limit_price, static_trigger
            else:
                # WAN-119: 밴드가 현재가를 표본으로 쓰므로 지정가가 봉내에 움직인다.
                # `None`이면 지금 주문판에 주문이 없다 — 다음 스텝에 생길 수 있으므로
                # 셋업을 끝내지 않고 그냥 넘어간다.
                current_limit = live_limit.limit_price(step.close)
                if current_limit is None:
                    continue
                order_rested = True
                fill_trigger = _fill_trigger(current_limit)

            assert current_limit is not None
            touched = step.low <= fill_trigger if is_long else step.high >= fill_trigger
            if touched:
                live_rsi = rsi_state.value(step.close)
                # WAN-100: 첫 탭 면제는 RSI 값 자체를 보지 않는다 — 워밍업(None)이어도
                # 통과다. A안 `ConfluenceStrategy._evaluate_entry`와 같은 규칙이다.
                # WAN-123: `unconditional`은 그 면제를 모든 탭으로 넓힌 것이라 여기서
                # 함께 판정한다(`none`은 아래 워밍업 요구를 그대로 받는다 — 둘은 다르다).
                condition = (
                    first_tap_free
                    or rsi_gate_mode == "unconditional"
                    or (
                        live_rsi is not None
                        and rsi_gate_passes(
                            live_rsi,
                            is_long=is_long,
                            mode=rsi_gate_mode,
                            rsi_oversold=rsi_oversold,
                            rsi_overbought=rsi_overbought,
                            rsi_neutral_band=rsi_neutral_band,
                        )
                    )
                )
                if condition:
                    if live_limit is not None:
                        # 1R = 진입가→무효화 경계라 체결가가 정해진 지금에야 손절·익절이
                        # 나온다(WAN-143: 오버라이드가 걸려 있으면 그 규칙이 낸다).
                        exits = live_limit.resolve_exits(current_limit)
                        if exits is None:
                            return ZoneLimitOutcome(
                                status=ZoneLimitStatus.CANCELLED_CONDITION_FAILED,
                                order_rested=order_rested,
                            )
                        active_stop, active_tp = exits
                    if observe_path_fill and isinstance(live_limit, PathProbeProvider):
                        # WAN-328 측정 전용: 같은 봉을 틱 추종으로 봤다면 어디서 체결됐나.
                        # 체결 여부·가격·손익은 이 값을 보지 않는다(순수 관측).
                        path_fill_price = _path_fill_price(live_limit, step, is_long=is_long)
                    position_open = True
                    just_entered = True
                    entry_time = step.time
                    entry_price = current_limit
                    entry_rsi = live_rsi
                    # WAN-323: 1R과 분할 지점을 **체결 순간에** 못 박는다(룩어헤드 없음).
                    entry_stop = active_stop
                    # WAN-346: 익절 목표도 체결 순간에 확정된다(손절과 같은 자리) — 순수
                    # 관측이라 아래 판정은 여전히 `active_tp`를 본다.
                    entry_tp = active_tp
                    entry_risk = abs(entry_price - entry_stop)
                    if partial_take_profit_r is not None and entry_risk > 0.0:
                        offset = partial_take_profit_r * entry_risk
                        partial_price = entry_price + offset if is_long else entry_price - offset
                    # 관통 방지: 같은 스텝에서 손절/익절을 곧바로 재판정한다(아래로 진행).
                elif cancel_on_condition_fail:
                    return ZoneLimitOutcome(
                        status=ZoneLimitStatus.CANCELLED_CONDITION_FAILED,
                        order_rested=order_rested,
                    )

        if position_open:
            if pending_breakeven:
                # WAN-323: 직전 스텝에서 부분 청산이 체결됐다 — 이제야 손절을 진입가로
                # 옮긴다. 같은 스텝에 옮기지 않는 이유는 그 1분봉 안의 경로(분할 체결이
                # 먼저인지 되돌림이 먼저인지)를 알 수 없기 때문이다.
                assert entry_price is not None
                active_stop = entry_price
                pending_breakeven = False
            # WAN-90: 이 스텝(진입 스텝·청산 스텝 포함)의 고가/저가를 극값에 반영한 뒤
            # 청산을 판정한다 — 청산 봉의 범위까지가 보유 구간이고 그 이후는 보지 않는다.
            hold_high = step.high if hold_high is None else max(hold_high, step.high)
            hold_low = step.low if hold_low is None else min(hold_low, step.low)
            # WAN-336(옵트인): 진입 스텝에서는 익절을 판정하지 않는다. 1분봉은 그 1분
            # **안의 순서**를 모르는데, 「같은 1분에 진입도 하고 익절도 했다」가 성립하려면
            # 롱 기준 **저가가 먼저 · 고가가 나중**이어야 한다 — 기본값은 그렇다고 가정한다
            # (낙관). 켜면 그 가정을 반대쪽 극단으로 미뤄 익절을 **다음 스텝부터** 판정한다.
            # 손절은 이 스텝에서 그대로 판정하므로(관통 감사 WAN-46의 그 자리) 켠 팔에서는
            # 「손절만 같은 분에 인정」이 되어 `stop_before_tp`와 방향이 대칭이 된다.
            # ⚠️ 이것도 진값이 아니라 **반대쪽 극단**이다 — 진값은 두 극단 사이에 있고 그
            # 폭이 WAN-336의 산출물이다(틱 해상도는 WAN-98 소관, Canceled).
            # WAN-359(옵트인): 「전부」가 아니라 **틱이 지지하지 않는 그 분들만** 끈다.
            # 표적 단위가 (칸, 1분)인 이유는 증거의 단위가 그것이기 때문이다 — 그 1분의
            # 체결내역이 자료이고, 같은 분에 여러 번 체결한 재진입 사슬은 진입가·익절가가
            # 같아 틱으로 갈리지 않는다(WAN-359 §1). 호출부가 **칸별로** 거른 집합을 준다.
            same_step_tp_blocked = just_entered and (
                no_same_step_tp
                or (no_same_step_tp_minutes is not None and step.time in no_same_step_tp_minutes)
            )
            tp_hit = (
                not same_step_tp_blocked
                and active_tp is not None
                and (step.high >= active_tp if is_long else step.low <= active_tp)
            )
            # WAN-276: 손절 체결을 두 모델 중 하나로 판정한다. 기본(둘 다 끔)은 현행 그대로
            # "손절가 터치 즉시 손절가 체결"이라 예전과 비트 단위로 같다.
            stop_fill = False
            stop_exit_price = active_stop
            stop_extreme: float | None = None
            if limit_stop_nonfill:
                # 팔 2 지정가 미체결: 정상 터치(봉 범위가 손절가를 품음)면 즉시 손절가 체결,
                # 갭 관통(봉 전체가 손절가 너머)이면 미체결로 무장하고 손절가 복귀를 기다린다.
                if not stop_armed:
                    reached = step.low <= active_stop if is_long else step.high >= active_stop
                    if reached:
                        spans = step.high >= active_stop if is_long else step.low <= active_stop
                        if spans:
                            stop_fill = True
                            stop_extreme = step.low if is_long else step.high
                        else:
                            stop_armed = True  # 갭 관통 — 그 봉엔 미체결, 계속 끌고 간다.
                else:
                    returned = step.high >= active_stop if is_long else step.low <= active_stop
                    if returned:  # 가격이 손절가로 되돌아옴 → 지정가 그대로 체결.
                        stop_fill = True
                        stop_extreme = step.low if is_long else step.high
            else:
                # 팔 1/기본: 손절가 터치 즉시 시장가 체결(α 슬리피지, α=0이면 손절가 그대로).
                stop_fill = step.low <= active_stop if is_long else step.high >= active_stop
                if stop_fill:
                    stop_extreme = step.low if is_long else step.high
                    stop_exit_price = _stop_fill_price(stop_extreme)
            if stop_fill and (not tp_hit or stop_before_tp):
                # WAN-323: 같은 스텝에서 분할 지점도 닿았을 수 있으나 **손절이 이긴다**
                # (보수적 — `stop_before_tp`와 같은 관행). 그래서 여기서 부분 청산을
                # 먼저 체결시키지 않는다.
                mfe_r, mae_r = _excursions()
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.FILLED_EXITED,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    entry_rsi=entry_rsi,
                    exit_time=step.time,
                    exit_price=stop_exit_price,
                    exit_reason=SignalExitReason.STOP_LOSS,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    stop_price=_entry_stop(),
                    take_profit_price=entry_tp,
                    path_fill_price=path_fill_price,
                    exit_extreme=stop_extreme,
                    exit_at_breakeven=entry_stop is not None and active_stop != entry_stop,
                    partial_exits=tuple(partials),
                    order_rested=order_rested,
                )
            if partial_price is not None and not same_step_tp_blocked:
                # 분할 지점도 **위쪽** 목표라 같은 가정 위에 선다 — 익절만 미루고
                # 분할은 인정하면 한 팔 안에서 자가 갈린다(WAN-336).
                # WAN-323 분할 지점 도달 — 진입 수량의 일부를 먼저 청산하고 잔량을 끌고
                # 간다. 지점은 한 번만 쓰이므로 곧바로 비활성화한다(2단 래더).
                partial_hit = step.high >= partial_price if is_long else step.low <= partial_price
                if partial_hit:
                    partials.append(
                        PartialExit(
                            time=step.time,
                            price=partial_price,
                            fraction=partial_take_profit_fraction,
                            reason=SignalExitReason.TAKE_PROFIT,
                        )
                    )
                    partial_price = None
                    pending_breakeven = breakeven_after_partial
            if tp_hit:
                mfe_r, mae_r = _excursions()
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.FILLED_EXITED,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    entry_rsi=entry_rsi,
                    exit_time=step.time,
                    exit_price=active_tp,
                    exit_reason=SignalExitReason.TAKE_PROFIT,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    stop_price=_entry_stop(),
                    take_profit_price=entry_tp,
                    path_fill_price=path_fill_price,
                    partial_exits=tuple(partials),
                    order_rested=order_rested,
                )

    if position_open:
        mfe_r, mae_r = _excursions()
        return ZoneLimitOutcome(
            status=ZoneLimitStatus.FILLED_OPEN,
            entry_time=entry_time,
            entry_price=entry_price,
            entry_rsi=entry_rsi,
            mfe_r=mfe_r,
            mae_r=mae_r,
            stop_price=_entry_stop(),
            take_profit_price=entry_tp,
            path_fill_price=path_fill_price,
            partial_exits=tuple(partials),
            order_rested=order_rested,
        )
    return ZoneLimitOutcome(status=ZoneLimitStatus.NO_TOUCH, order_rested=order_rested)
