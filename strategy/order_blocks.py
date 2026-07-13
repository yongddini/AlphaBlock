"""오더블록(Order Block) 탐지 & 시그널 생성.

Fluxchart "Volumized Order Blocks" (TradingView, MPL-2.0)의 탐지 로직을
`strategy/reference/README.md` 명세를 기준으로 pandas OHLCV 입력에 대해
이식한 순수 함수/클래스. 원본은 존 탐지·무효화까지만 하며, 진입 시그널
레이어는 AlphaBlock의 확장이다 (`strategy/reference/README.md` 참고).

입력 DataFrame은 `data.storage.OhlcvStore.load()`가 반환하는 스키마
(`open_time`(ms), `open`, `high`, `low`, `close`, `volume`, 선택적 `closed`)
를 따른다. `closed` 컬럼이 있으면 확정봉(`closed=True`)만 사용한다 — 원본이
`barstate.isconfirmed`에서만 갱신하는 것과 동일한 제약.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

import pandas as pd

from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
    obs_touch,
    select_active,
)

_REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


@dataclass(eq=False)
class _SwingPoint:
    """지연 스윙 지점. 원본 `obSwing`에 대응."""

    index: int
    price: float
    crossed: bool = False


@dataclass(eq=False)
class _RawOrderBlock:
    """탐지 진행 중 상태를 갖는 오더블록. 원본 `orderBlockInfo`에 대응.

    WAN-47: 존은 삭제되지 않고 전체 생애주기를 기록한다. `swept`로 소멸 여부를,
    `swept_time`으로 소멸 시각을, `tapped_times`로 재진입 시각들을 보존한다.
    `_inside`는 tap 전이(바깥→안) 판정을 위한 내부 상태다.
    """

    top: float
    bottom: float
    ob_volume: float
    direction: OrderBlockDirection
    start_time: int
    confirmed_time: int
    ob_low_volume: float
    ob_high_volume: float
    breaker: bool = False
    break_time: int | None = None
    swept: bool = False
    swept_time: int | None = None
    tapped_times: list[int] = field(default_factory=list)
    _inside: bool = False

    def to_model(self, *, combined: bool = False) -> OrderBlock:
        return OrderBlock(
            direction=self.direction,
            top=self.top,
            bottom=self.bottom,
            start_time=self.start_time,
            confirmed_time=self.confirmed_time,
            ob_volume=self.ob_volume,
            ob_low_volume=self.ob_low_volume,
            ob_high_volume=self.ob_high_volume,
            breaker=self.breaker,
            break_time=self.break_time,
            swept_time=self.swept_time,
            tapped_times=tuple(self.tapped_times),
            combined=combined,
        )


def _true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    n = len(highs)
    tr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
    return tr


def _wilder_rma(values: list[float], length: int) -> list[float | None]:
    """Wilder's RMA (`ta.rma`). 처음 `length-1`개는 `None`(미확정)."""
    n = len(values)
    result: list[float | None] = [None] * n
    if n < length:
        return result
    seed = sum(values[:length]) / length
    result[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = (prev * (length - 1) + values[i]) / length
        result[i] = prev
    return result


def _atr(
    highs: list[float], lows: list[float], closes: list[float], length: int
) -> list[float | None]:
    return _wilder_rma(_true_range(highs, lows, closes), length)


def _rolling_max(values: list[float], length: int) -> list[float]:
    result = pd.Series(values).rolling(window=length, min_periods=1).max().tolist()
    return [float(v) for v in result]


def _rolling_min(values: list[float], length: int) -> list[float]:
    result = pd.Series(values).rolling(window=length, min_periods=1).min().tolist()
    return [float(v) for v in result]


def _generate_signals(
    order_blocks: list[OrderBlock],
    times: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[OrderBlockSignal]:
    """활성(비-breaker) 존에 가격이 재진입(tap)하면 진입 후보 시그널 생성.

    원본에는 없는 AlphaBlock 확장(기본 골격). 존이 확정된 이후 첫 재진입만
    시그널로 기록하고, breaker로 전환된 존은 `status="cancelled"`로 표시한다.
    세부 규칙(리테스트 확인, 손절 등)은 WAN-8/9에서 확정한다.

    WAN-47: `order_blocks`는 **전체 아카이브**(살아남은 존뿐 아니라 깨지고 소멸한
    존까지)를 받는다. 이로써 생존자 편향 없이 모든 존의 탭·손절이 백테스트에
    반영된다. **look-ahead 금지** — 각 존의 신호는 그 존이 확정(`confirmed_time`)된
    이후, 무효화(`break_time`) 이전(무효화 봉 포함)의 자기 시간축에서만 나온다.
    시각 `t`의 신호는 `t` 시점에 이미 확정·미소멸한 존만 근거로 하므로, 데이터를
    미래에서 잘라도 과거 신호는 바뀌지 않는다.
    """
    signals: list[OrderBlockSignal] = []
    n = len(times)
    # WAN-49: times는 정렬된 배열이므로 존별 구간 경계를 이진 탐색으로 O(log n)에 구한다.
    # (이전에는 매 존마다 0부터 선형 스캔 → O(존수 × 봉수)로 3년치에서 수천만 회.)
    # bisect_right(confirmed_time): 확정 시각을 **초과**하는 첫 봉(구버전 `<= ` 루프와 동일).
    # bisect_left(break_time): 무효화 시각 **이상**인 첫 봉(구버전 `< ` 루프와 동일).
    # break_time > confirmed_time 이 항상 성립하므로 break_pos >= start_pos 가 보장된다.
    for ob in order_blocks:
        start_pos = bisect.bisect_right(times, ob.confirmed_time)
        # 무효화 봉의 위치. 이 봉까지(포함) 탭을 살핀다.
        break_pos: int | None = None
        if ob.break_time is not None:
            break_pos = bisect.bisect_left(times, ob.break_time)
        end_pos = n if break_pos is None else min(n, break_pos + 1)

        for i in range(start_pos, end_pos):
            if lows[i] <= ob.top and highs[i] >= ob.bottom:
                # WAN-47: 상태는 존의 **최종** breaker 여부가 아니라 **이 탭이 무효화
                # 전인지**로 정한다. 무효화 봉 자체에서의 탭만 cancelled고, 그 전의
                # 탭은 유효한 진입(나중에 무효화되면 손절)이다. 최종 상태로 판정하면
                # 결국 깨질 존의 정상 진입까지 모두 배제돼 생존자 편향이 재발한다.
                is_break_bar = break_pos is not None and i >= break_pos
                signals.append(
                    OrderBlockSignal(
                        direction=ob.direction,
                        trigger_time=times[i],
                        price=closes[i],
                        order_block=ob,
                        status="cancelled" if is_break_bar else "active",
                    )
                )
                break
    return signals


@dataclass(eq=False)
class _MergedGroup:
    """어떤 봉 시점의 병합 존 하나. 원본 `combineOBsFunc` 결과 박스에 대응(WAN-56).

    같은 방향·겹치는(touch) 활성 존들의 연결 요소(connected component)다. `top`/`bottom`은
    구성 존들의 **합집합** 경계이고, `merged_ob`는 그 병합 존을 백테스트 시그널에 실어
    보낼 값 객체다(단일 존이면 원본 존 그대로 — `combine_obs=False` 경로와 동일 시그널).
    `member_indices`는 아카이브 인덱스 집합으로, "병합 단위당 1회 진입(R1)" 불변식을
    유지하는 데 쓴다.
    """

    direction: OrderBlockDirection
    top: float
    bottom: float
    latest_confirmed: int
    break_time: int | None
    member_indices: frozenset[int]
    merged_ob: OrderBlock


def _make_merged_group(
    direction: OrderBlockDirection, members: list[tuple[int, OrderBlock]]
) -> _MergedGroup:
    """연결 요소(같은 방향, 서로 touch) 존들을 하나의 병합 존으로 접는다.

    병합 존의 무효화(`break_time`)는 **원단(distal) 경계를 정의하는 구성 존**의 무효화와
    같다. 강세 병합 존의 distal은 `bottom = min(구성 bottom)`이고, 그 최저 bottom을 가진
    구성 존이 깨지는 순간(가격이 그 bottom 아래로) 병합 존도 무효화된다(합집합 경계 이탈).
    약세는 `top = max(구성 top)`의 최고 top 존이 distal이다. 따라서 병합 존의 손절선은
    구성 존 중 가장 바깥 존의 손절선과 일치한다(WAN-56 영향 #2: 손절 거리 확장).
    """
    obs = [ob for _, ob in members]
    indices = frozenset(idx for idx, _ in members)
    if len(obs) == 1:
        # 단일 존: 원본 존을 그대로 실어 `combine_obs=False`와 완전히 동일한 시그널을 낸다.
        ob = obs[0]
        return _MergedGroup(
            direction=direction,
            top=ob.top,
            bottom=ob.bottom,
            latest_confirmed=ob.confirmed_time,
            break_time=ob.break_time,
            member_indices=indices,
            merged_ob=ob,
        )

    is_bullish = direction is OrderBlockDirection.BULLISH
    top = max(ob.top for ob in obs)
    bottom = min(ob.bottom for ob in obs)
    # 원단(distal) 경계를 정의하는 구성 존 → 병합 존의 무효화/소멸 시각을 이 존에서 취한다.
    distal = min(obs, key=lambda ob: ob.bottom) if is_bullish else max(obs, key=lambda ob: ob.top)
    tapped: set[int] = set()
    for ob in obs:
        tapped.update(ob.tapped_times)
    merged_ob = OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=min(ob.start_time for ob in obs),
        confirmed_time=min(ob.confirmed_time for ob in obs),
        ob_volume=sum(ob.ob_volume for ob in obs),
        ob_low_volume=sum(ob.ob_low_volume for ob in obs),
        ob_high_volume=sum(ob.ob_high_volume for ob in obs),
        breaker=distal.break_time is not None,
        break_time=distal.break_time,
        swept_time=distal.swept_time,
        tapped_times=tuple(sorted(tapped)),
        combined=True,
    )
    return _MergedGroup(
        direction=direction,
        top=top,
        bottom=bottom,
        latest_confirmed=max(ob.confirmed_time for ob in obs),
        break_time=distal.break_time,
        member_indices=indices,
        merged_ob=merged_ob,
    )


def _build_merged_groups(alive: list[tuple[int, OrderBlock]], now: int) -> list[_MergedGroup]:
    """현재 활성 존들을 방향별로 touch 연결 요소(병합 단위)로 묶는다.

    원본 `combineOBsFunc`는 매 봉 병합을 처음부터 다시 계산하므로, 여기서도 그 시점의
    활성 집합만으로 union-find 연결 요소를 구한다. 병합은 touch 관계의 추이 폐포이고,
    연결 요소의 합집합 경계가 원본의 반복 병합 결과와 동일하다(연결된 존들은 가격·시간축
    모두 연속이라 합집합 박스에 새로 닿는 제3의 존은 반드시 어떤 구성 존과도 닿는다).
    """
    groups: list[_MergedGroup] = []
    for direction in (OrderBlockDirection.BULLISH, OrderBlockDirection.BEARISH):
        members = [(idx, ob) for idx, ob in alive if ob.direction is direction]
        m = len(members)
        if m == 0:
            continue
        # bottom 오름차순 스윕: 가격축이 겹치는 후보끼리만 touch를 검사한다(O(존²) 회피).
        # 정렬 후 뒤쪽 존일수록 bottom이 크므로, 열린 존의 top이 현재 bottom 이하가 되면
        # 이후 어떤 존과도 가격이 겹칠 수 없어 후보에서 제거한다(구간 겹침 스윕).
        members.sort(key=lambda p: p[1].bottom)
        parent = list(range(m))

        def find(x: int, parent: list[int] = parent) -> int:
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        open_idx: list[int] = []
        for k in range(m):
            ob_k = members[k][1]
            bottom_k = ob_k.bottom
            open_idx = [o for o in open_idx if members[o][1].top > bottom_k]
            rk = find(k)
            for o in open_idx:
                # 가격은 이미 겹침(top_o > bottom_k) — touch는 시간축까지 확인한다.
                if find(o) != rk and obs_touch(members[o][1], ob_k, now):
                    parent[find(o)] = rk
            open_idx.append(k)

        components: dict[int, list[tuple[int, OrderBlock]]] = {}
        for i in range(m):
            components.setdefault(find(i), []).append(members[i])
        for comp in components.values():
            groups.append(_make_merged_group(direction, comp))
    return groups


def _generate_merged_signals(
    archive: list[OrderBlock],
    times: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[OrderBlockSignal]:
    """병합 존(combine_obs) 기준으로 탭 진입 시그널을 **시간 순 재생**으로 생성한다 (WAN-56).

    렌더링에만 적용되던 존 병합을 백테스트 시그널까지 끌어올린다. 원본이 매 봉
    `combineOBsFunc`을 호출하듯, 각 봉 `t`에서 **그 시점까지 확정·미소멸한 존들**의 병합
    상태를 구성하고, 그 병합 존에 대해 탭(재진입)을 판정한다. 미래에 생길 존과 미리
    합치지 않으므로 look-ahead가 없다(데이터를 미래에서 잘라도 과거 신호 불변).

    "병합 단위당 첫 탭 1회"(R1)는 `entered`(진입 완료된 구성 존의 아카이브 인덱스 집합)로
    유지한다: 어떤 병합 존이 진입하면 그 순간의 구성 존을 모두 진입 처리하고, 이후 그
    구성 존을 포함하는(성장한) 병합 존은 재진입하지 않는다. 구성 존이 모두 소멸한 뒤 같은
    가격대에 새로 생긴 병합 존은 진입 대상이 다시 된다.

    성능(WAN-49): 병합 상태는 활성 집합이 바뀔 때(존 확정·무효화·소멸)만 재계산하고,
    변화 없는 봉에서는 캐시한 병합 존에 탭만 확인한다. 겹치지 않는(단일) 존은
    `combine_obs=False`와 동일한 시그널을 내므로, 병합의 영향은 겹치는 클러스터에 한정된다.
    """
    n = len(times)
    if n == 0 or not archive:
        return []

    # 확정 시각 오름차순으로 활성 집합에 편입(동시각은 아카이브 인덱스로 안정 정렬).
    to_add = sorted(enumerate(archive), key=lambda p: (p[1].confirmed_time, p[0]))
    add_ptr = 0
    alive: list[tuple[int, OrderBlock]] = []
    entered: set[int] = set()
    groups: list[_MergedGroup] = []
    dirty = True
    signals: list[OrderBlockSignal] = []

    for t in range(n):
        now = times[t]
        while add_ptr < len(to_add) and to_add[add_ptr][1].confirmed_time <= now:
            alive.append(to_add[add_ptr])
            add_ptr += 1
            dirty = True
        kept: list[tuple[int, OrderBlock]] = []
        for idx, ob in alive:
            if ob.swept_time is not None and ob.swept_time <= now:
                dirty = True  # 소멸 → 활성 집합에서 제외(원본 box.delete와 동일).
                continue
            if ob.break_time is not None and ob.break_time == now:
                dirty = True  # 무효화 상태 전이 → 병합 경계(면적/end) 재계산 필요.
            kept.append((idx, ob))
        alive = kept

        if dirty:
            groups = _build_merged_groups(alive, now)
            dirty = False

        for g in groups:
            if g.member_indices & entered:
                continue  # 이미 진입한 병합 단위 — R1.
            if now <= g.latest_confirmed:
                continue  # 병합 존 형성 봉의 자기-포함 탭 배제(원본 확정 봉 제외와 동일).
            if g.break_time is not None and now > g.break_time:
                continue  # 무효화 이후 탭은 진입이 아니다(_generate_signals와 동일 창).
            if lows[t] <= g.top and highs[t] >= g.bottom:
                is_break_bar = g.break_time is not None and now >= g.break_time
                signals.append(
                    OrderBlockSignal(
                        direction=g.direction,
                        trigger_time=now,
                        price=closes[t],
                        order_block=g.merged_ob,
                        status="cancelled" if is_break_bar else "active",
                    )
                )
                entered |= g.member_indices
    return signals


class OrderBlockDetector:
    """오더블록 탐지 & 시그널 생성기.

    사용법::

        detector = OrderBlockDetector(OrderBlockParams())
        result = detector.run(ohlcv_df)
    """

    def __init__(self, params: OrderBlockParams | None = None) -> None:
        self.params = params or OrderBlockParams()

    def run(self, df: pd.DataFrame) -> OrderBlockResult:
        frame = self._prepare(df)
        n = len(frame)
        if n == 0:
            return OrderBlockResult(order_blocks=[], signals=[])

        highs = frame["high"].astype(float).tolist()
        lows = frame["low"].astype(float).tolist()
        closes = frame["close"].astype(float).tolist()
        opens = frame["open"].astype(float).tolist()
        volumes = frame["volume"].astype(float).tolist()
        times = frame["open_time"].astype("int64").tolist()

        params = self.params
        swing_length = params.swing_length
        atr = _atr(highs, lows, closes, params.atr_length)
        upper = _rolling_max(highs, swing_length)
        lower = _rolling_min(lows, swing_length)

        use_wick = params.zone_invalidation == "wick"

        swing_type = 0
        top: _SwingPoint | None = None
        bottom: _SwingPoint | None = None

        # WAN-49: 아카이브(전체 생애 보존)와 활성 리스트(아직 소멸 안 한 존)를 분리한다.
        # `bullish_obs`/`bearish_obs`는 WAN-47 그대로 생성된 모든 존을 담는 아카이브이고,
        # `active_bull`/`active_bear`는 `_invalidate()`의 **순회 대상**이다. 존이 소멸(swept)
        # 하면 활성 리스트에서만 빠지고 아카이브에는 남는다. 아카이브의 내용·순서는
        # WAN-47과 100% 동일하다(순회 집합만 좁혔을 뿐, 존별 상태 전이는 불변).
        bullish_obs: list[_RawOrderBlock] = []
        bearish_obs: list[_RawOrderBlock] = []
        active_bull: list[_RawOrderBlock] = []
        active_bear: list[_RawOrderBlock] = []

        # WAN-47: 탐지는 **전체 히스토리**를 스캔한다. 원본의 max_distance_to_last_bar는
        # "마지막 봉에서 N봉 이내만 탐지"하는 스캔 상한이었지만, 그러면 백테스트 기간의
        # 앞부분에서 당시 유효했던 존이 아카이브에서 통째로 빠진다(생존자 편향의 또 다른
        # 얼굴). 탐지/렌더 분리에 따라 이 상한은 **렌더 최근성 필터**로 옮겨(아래
        # rendered 계산), 아카이브는 생성된 모든 존을 담는다.
        for t in range(n):
            if t >= swing_length:
                lag = t - swing_length
                if highs[lag] > upper[t]:
                    new_swing_type = 0
                elif lows[lag] < lower[t]:
                    new_swing_type = 1
                else:
                    new_swing_type = swing_type
                if new_swing_type == 0 and swing_type != 0:
                    top = _SwingPoint(index=lag, price=highs[lag])
                if new_swing_type == 1 and swing_type != 1:
                    bottom = _SwingPoint(index=lag, price=lows[lag])
                swing_type = new_swing_type

            active_bull = self._invalidate(
                active_bull,
                is_bullish=True,
                use_wick=use_wick,
                t=t,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                times=times,
            )
            top = self._create_bullish(
                top, bullish_obs, active_bull, params, t, highs, lows, closes, volumes, times, atr
            )

            active_bear = self._invalidate(
                active_bear,
                is_bullish=False,
                use_wick=use_wick,
                t=t,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                times=times,
            )
            bottom = self._create_bearish(
                bottom,
                bearish_obs,
                active_bear,
                params,
                t,
                highs,
                lows,
                closes,
                volumes,
                times,
                atr,
            )

        # WAN-47: 탐지(archive)와 렌더링(view)을 분리한다. 아카이브는 생성된 모든
        # 존의 전체 생애주기를 담고(트리밍·삭제 없음), 신호는 아카이브 전체에서
        # 생성한다(생존자 편향 제거). "지금 차트에 그릴 박스"는 렌더링 뷰가 파생한다.
        archive = [ob.to_model() for ob in bullish_obs] + [ob.to_model() for ob in bearish_obs]
        # WAN-56: `combine_obs`면 병합 존 기준으로 시그널을 낸다(트레이딩뷰 렌더와 동일한
        # 존 집합을 백테스트도 본다). `combine_obs=False`는 원본 단위 경로를 유지해 비교
        # 가능하게 남긴다. 아카이브(`order_blocks`)는 두 경로 모두 원본 단위로 보존한다.
        if params.combine_obs:
            signals = _generate_merged_signals(archive, times, highs, lows, closes)
        else:
            signals = _generate_signals(archive, times, highs, lows, closes)

        # 렌더 뷰(트레이딩뷰 "현재 그림"): 마지막 봉에서 max_distance_to_last_bar봉 이내에
        # 확정된 존만 대상으로, 방향별 zone_limit개를 병합해 낸다. 데이터가 스캔 상한보다
        # 짧으면(대부분의 테스트·픽스처) 필터는 무효라 기존 동작과 동일하다.
        cutoff_index = max(0, (n - 1) - params.max_distance_to_last_bar + 1)
        cutoff_time = times[cutoff_index]
        recent = [ob for ob in archive if ob.confirmed_time >= cutoff_time]
        rendered = select_active(
            recent, times[-1], limit=params.zone_limit, combine=params.combine_obs
        )

        return OrderBlockResult(
            order_blocks=archive, signals=signals, rendered_order_blocks=rendered
        )

    @staticmethod
    def _invalidate(
        obs: list[_RawOrderBlock],
        *,
        is_bullish: bool,
        use_wick: bool,
        t: int,
        opens: list[float],
        highs: list[float],
        lows: list[float],
        closes: list[float],
        times: list[int],
    ) -> list[_RawOrderBlock]:
        """활성 존만 순회해 무효화·소멸·탭을 갱신하고, **아직 소멸 안 한 존**을 반환한다.

        WAN-49: `obs`는 활성 리스트(비-swept 존만)다. 이번 봉에 소멸(`swept`)한 존은
        반환 리스트에서 빠지고(다음 봉부터 순회 제외), 아카이브에는 그대로 남는다.
        구버전은 전체 아카이브를 순회하며 `if ob.swept: continue`로 건너뛰었는데,
        소멸 존이 누적되면서 매 봉 순회 길이가 최대 존수까지 커져 O(봉수 × 존수)로
        퇴화했다. 순회 집합만 좁혔을 뿐 존별 상태 전이 로직은 완전히 동일하다.
        """
        still_active: list[_RawOrderBlock] = []
        for ob in obs:
            if not ob.breaker:
                if is_bullish:
                    cmp_value = lows[t] if use_wick else min(opens[t], closes[t])
                    if cmp_value < ob.bottom:
                        ob.breaker = True
                        ob.break_time = times[t]
                else:
                    cmp_value = highs[t] if use_wick else max(opens[t], closes[t])
                    if cmp_value > ob.top:
                        ob.breaker = True
                        ob.break_time = times[t]
            else:
                # WAN-47: 되쓸린 존을 리스트에서 지우지 않고 소멸 시각만 기록한다.
                # (원본은 여기서 box.delete() — 렌더링에는 옳지만 백테스트 기록을 지운다.)
                if is_bullish:
                    if highs[t] > ob.top:
                        ob.swept = True
                        ob.swept_time = times[t]
                else:
                    if lows[t] < ob.bottom:
                        ob.swept = True
                        ob.swept_time = times[t]

            # tap(재진입) 전이 기록: 확정 이후, 존 범위에 바깥→안으로 진입한 시각.
            if not ob.swept:
                inside = lows[t] <= ob.top and highs[t] >= ob.bottom
                if inside and not ob._inside and times[t] > ob.confirmed_time:
                    ob.tapped_times.append(times[t])
                ob._inside = inside
                still_active.append(ob)
        return still_active

    @staticmethod
    def _create_bullish(
        top: _SwingPoint | None,
        bullish_obs: list[_RawOrderBlock],
        active_bull: list[_RawOrderBlock],
        params: OrderBlockParams,
        t: int,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        volumes: list[float],
        times: list[int],
        atr: list[float | None],
    ) -> _SwingPoint | None:
        if top is None or top.crossed or closes[t] <= top.price:
            return top
        top.crossed = True

        lo, hi = 1, t - top.index - 1
        sel = min(range(t - hi, t - lo + 1), key=lambda i: lows[i]) if hi >= lo else t - 1
        box_bottom, box_top, box_loc = lows[sel], highs[sel], times[sel]

        ob_volume = volumes[t] + volumes[t - 1] + volumes[t - 2]
        ob_low_volume = volumes[t - 2]
        ob_high_volume = volumes[t] + volumes[t - 1]

        atr_t = atr[t]
        if atr_t is not None and abs(box_top - box_bottom) <= atr_t * params.max_atr_mult:
            new_ob = _RawOrderBlock(
                top=box_top,
                bottom=box_bottom,
                ob_volume=ob_volume,
                direction=OrderBlockDirection.BULLISH,
                start_time=box_loc,
                confirmed_time=times[t],
                ob_low_volume=ob_low_volume,
                ob_high_volume=ob_high_volume,
            )
            # WAN-47: 아카이브는 개수 캡으로 오래된 존을 버리지 않는다(전체 생애 보존).
            # 표시 개수 제한은 렌더링 뷰(`select_active`)에서만 적용한다.
            bullish_obs.insert(0, new_ob)
            # WAN-49: 새 존은 다음 봉부터 무효화 순회 대상이 된다(활성 리스트에 추가).
            active_bull.append(new_ob)
        return top

    @staticmethod
    def _create_bearish(
        bottom: _SwingPoint | None,
        bearish_obs: list[_RawOrderBlock],
        active_bear: list[_RawOrderBlock],
        params: OrderBlockParams,
        t: int,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        volumes: list[float],
        times: list[int],
        atr: list[float | None],
    ) -> _SwingPoint | None:
        if bottom is None or bottom.crossed or closes[t] >= bottom.price:
            return bottom
        bottom.crossed = True

        lo, hi = 1, t - bottom.index - 1
        sel = max(range(t - hi, t - lo + 1), key=lambda i: highs[i]) if hi >= lo else t - 1
        box_top, box_bottom, box_loc = highs[sel], lows[sel], times[sel]

        ob_volume = volumes[t] + volumes[t - 1] + volumes[t - 2]
        ob_low_volume = volumes[t] + volumes[t - 1]
        ob_high_volume = volumes[t - 2]

        atr_t = atr[t]
        if atr_t is not None and abs(box_top - box_bottom) <= atr_t * params.max_atr_mult:
            new_ob = _RawOrderBlock(
                top=box_top,
                bottom=box_bottom,
                ob_volume=ob_volume,
                direction=OrderBlockDirection.BEARISH,
                start_time=box_loc,
                confirmed_time=times[t],
                ob_low_volume=ob_low_volume,
                ob_high_volume=ob_high_volume,
            )
            bearish_obs.insert(0, new_ob)
            active_bear.append(new_ob)
        return bottom

    @staticmethod
    def _prepare(df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"OHLCV DataFrame에 필요한 컬럼이 없습니다: {missing}")
        frame = df
        if "closed" in df.columns:
            frame = frame[frame["closed"].astype(bool)]
        return frame.sort_values("open_time").reset_index(drop=True)


def detect_order_blocks(
    df: pd.DataFrame, params: OrderBlockParams | None = None
) -> OrderBlockResult:
    """`OrderBlockDetector(params).run(df)`의 편의 함수."""
    return OrderBlockDetector(params).run(df)
