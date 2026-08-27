"""실시간(봉내) MACD 히스토그램 계산기 — 관측 전용 상태 머신 (WAN-372).

사용자 제안(2026-08-25): *"오더블록에 진입했을 때 진한 빨간색인 경우에만 진입했을 때
상황을 좀 알고싶거든"*. 이 모듈은 그 **색**을 체결 순간에 읽을 수 있게 한다.

트레이딩뷰가 화면에 그리는 MACD는 **매 틱마다 진행 중인 봉을 다시 계산**한 값이다. 그때
`close`는 확정 종가가 아니라 **현재가**이고, 세 EMA 상태(빠른·느린·시그널)는 직전 확정봉
값에서 고정한 채 현재 봉만 새로 얹는다(Pine의 rollback). `strategy.realtime_rsi.RealtimeRsi`
· `strategy.realtime_band.RealtimeBand`와 **같은 모양**이다(`seed_from_closed` / `commit` /
`value`).

MACD **수식**은 `strategy.indicators.ema`(= `ewm(span, adjust=False)`)와 완전히 동일하다 —
"실시간"은 수식이 아니라 **마지막 표본에 무엇을 넣느냐**의 문제다::

    fast_live   = a_f * price + (1 - a_f) * fast_ema_확정
    slow_live   = a_s * price + (1 - a_s) * slow_ema_확정
    macd_live   = fast_live - slow_live
    signal_live = a_g * macd_live + (1 - a_g) * signal_ema_확정
    hist_live   = macd_live - signal_live

`hist_prev`(직전 봉 히스토그램)는 **직전 확정봉** 값 그대로다 — 확정이라 룩어헤드가 없다.

## 🚨 왜 「체결 순간」인가 (사용자 확인 2026-08-27)

MACD는 종가로 계산되는데 **진입은 봉 내부**에서 일어난다(지정가 체결). 시점을 안 정하면
반드시 틀린다 — 볼린저 밴드가 정확히 이 문제로 WAN-115 → 119 → 120 → 132를 거쳤다.

| 시점 | 판정 | 이유 |
| -- | -- | -- |
| 탭 봉 종가 | ❌ | 그 봉이 어떻게 끝날지 알아야 나오는 값 = **룩어헤드** |
| 직전 확정봉 | ❌ | 인과적이지만 **가격이 존까지 내려온 그 구간을 통째로 버린다**(WAN-119) |
| **체결 순간 현재가** | ✅ | 사용자가 트레이딩뷰에서 그때 실제로 보는 색 · 진입가 정본과 같은 자 |

⚠️ **EMA는 재귀적이라 볼린저(단순평균)보다 까다롭다** — 봉내 현재가가 움직이면 `hist`가
계속 변하고 **색이 봉 안에서 바뀔 수 있다**. 그래서 **체결 순간의 색**을 그 거래의 색으로
확정하고, 그 봉이 나중에 어떻게 끝나든 바꾸지 않는다.

⚠️ **한계 — 1분봉 근사(틱 아님)**: 백테스트에서 공급되는 "현재가"의 최대 해상도는 1분봉
서브스텝이다(`backtest.substep`). `RealtimeRsi`·`RealtimeBand`가 이미 쓰고 있는 것과 **같은
관행**이며, 잔여 성질은 `docs/decisions/wan119.md` §한계와 같다.

## 파라미터 — 고정하고 시작한다 (스윕 금지)

**12 / 26 / 9 · 오실레이터 EMA · 신호선 EMA · 소스 종가.** 사용자의 트레이딩뷰 실제 설정이자
Pine 기본값이다. 🚨 이 셋을 흔들면 자유 파라미터가 셋 늘어 **앞구간 승자를 찾는 기계**가
된다(WAN-161: 익절 배수 최적값이 앞구간 8칸 중 7칸에서 뒷구간에 뒤집혔다).

## 관측 전용

이 모듈은 체결·청산·손익 어디에도 쓰이지 않는다 — `ZoneLimitOutcome.mfe_r`(WAN-90) ·
`exit_extreme`(WAN-276) · `path_fill_price`(WAN-328)와 **같은 부류**의 순수 관측이다.
색으로 거르는 팔(필터)은 이 이슈 범위 밖이고, 그때는 **북에서 다시 재야 한다**(색으로
거래를 걸러내면 공유 자본 경합이 달라진다 — WAN-341/323).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

#: Pine 원본의 히스토그램 색 코드 — 표·문서가 같은 상수를 인용하도록 여기 한 곳에 둔다.
COLOR_HEX: dict[str, str] = {
    "strong_green": "#26A69A",
    "weak_green": "#B2DFDB",
    "weak_red": "#FFCDD2",
    "strong_red": "#FF5252",
}


class MacdColor(StrEnum):
    """MACD 히스토그램 막대의 네 색 (Pine 원본 규칙 그대로).

    ::

        color = hist >= 0 ? (hist[1] < hist ? #26A69A : #B2DFDB)
                          : (hist[1] < hist ? #FFCDD2 : #FF5252)
    """

    STRONG_GREEN = "strong_green"
    """진한 초록 `#26A69A` — `hist ≥ 0` and `hist > hist[1]` = 상승 가속."""
    WEAK_GREEN = "weak_green"
    """연한 초록 `#B2DFDB` — `hist ≥ 0` and `hist ≤ hist[1]` = 상승 둔화."""
    WEAK_RED = "weak_red"
    """연한 빨강 `#FFCDD2` — `hist < 0` and `hist > hist[1]` = 하락 둔화."""
    STRONG_RED = "strong_red"
    """진한 빨강 `#FF5252` — `hist < 0` and `hist ≤ hist[1]` = **하락 가속**(사용자 관심)."""

    @property
    def hex(self) -> str:
        return COLOR_HEX[self.value]

    @property
    def label(self) -> str:
        """표에 찍는 한국어 이름."""
        return _COLOR_LABEL[self]


_COLOR_LABEL: dict[MacdColor, str] = {
    MacdColor.STRONG_GREEN: "진한 초록",
    MacdColor.WEAK_GREEN: "연한 초록",
    MacdColor.WEAK_RED: "연한 빨강",
    MacdColor.STRONG_RED: "진한 빨강",
}

#: 워밍업이라 색을 판정할 수 없는 셋업의 라벨 — **표에서 보인다**(어느 색으로 흡수시키면
#: 분포가 거짓말을 한다). 관측을 켠 실행에서만 나타날 수 있다.
WARMUP_LABEL = "워밍업"

#: 표·CSV의 고정 순서(초록 → 빨강, 각각 진한 것 먼저). 분포가 어떻든 열 순서는 안 바뀐다.
COLOR_ORDER: tuple[MacdColor, ...] = (
    MacdColor.STRONG_GREEN,
    MacdColor.WEAK_GREEN,
    MacdColor.WEAK_RED,
    MacdColor.STRONG_RED,
)


def macd_color(hist: float, hist_prev: float) -> MacdColor:
    """Pine 원본 그대로의 색 판정.

    ⚠️ 경계는 **원본과 같게** 잡는다: `hist == 0`은 초록 쪽(`hist >= 0`)이고,
    `hist == hist_prev`(변화 없음)는 **연한 쪽**(`hist[1] < hist`가 거짓)이다. 부등호를
    한 칸 옮기면 같은 봉이 다른 색이 되므로 여기 한 곳에서만 판정한다.
    """
    rising = hist_prev < hist
    if hist >= 0.0:
        return MacdColor.STRONG_GREEN if rising else MacdColor.WEAK_GREEN
    return MacdColor.WEAK_RED if rising else MacdColor.STRONG_RED


@dataclass(frozen=True)
class MacdParams:
    """MACD 파라미터 — 기본값이 사용자의 트레이딩뷰 설정이자 Pine 기본값(12/26/9)."""

    fast_length: int = 12
    slow_length: int = 26
    signal_length: int = 9

    def __post_init__(self) -> None:
        for name in ("fast_length", "slow_length", "signal_length"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name}는 1 이상이어야 합니다: {getattr(self, name)}")
        if self.fast_length >= self.slow_length:
            # 빠른 EMA가 느린 EMA보다 길면 MACD 선의 부호 의미가 뒤집혀 색 규칙이 거짓말이 된다.
            raise ValueError(
                f"fast_length({self.fast_length})는 slow_length({self.slow_length})보다 "
                "짧아야 합니다."
            )

    @property
    def warmup_bars(self) -> int:
        """값을 낼 수 있다고 보는 최소 확정봉 수 = `slow + signal`.

        `ewm(adjust=False)`는 첫 봉을 시드로 삼아 **1봉부터도 숫자를 낸다** — 하지만 그
        숫자는 26봉·9봉 창을 아직 못 본 값이라 히스토그램의 부호·기울기가 뜻을 갖지
        못한다. 지어내지 않고 `None`을 내고, 그 셋업은 표에서 「워밍업」으로 **보인다**
        (조용히 어느 색으로 흡수시키면 분포가 거짓말을 한다).
        """
        return self.slow_length + self.signal_length


DEFAULT_MACD_PARAMS = MacdParams()


@dataclass(frozen=True)
class MacdSample:
    """체결 순간에 읽은 히스토그램 한 쌍 — 색은 이 둘에서 파생된다."""

    hist: float
    """봉내 라이브 히스토그램(직전 확정봉 EMA 상태 ＋ 체결 순간 현재가)."""
    hist_prev: float
    """직전 **확정봉**의 히스토그램(`hist[1]`)."""

    @property
    def color(self) -> MacdColor:
        return macd_color(self.hist, self.hist_prev)


def _alpha(length: int) -> float:
    """`ta.ema`/`ewm(span=length, adjust=False)`의 평활 계수."""
    return 2.0 / (length + 1.0)


@dataclass
class RealtimeMacd:
    """봉내 실시간 MACD 히스토그램 상태 머신 (O(1) 증분, 관측 전용).

    사용법::

        macd = RealtimeMacd.seed_from_closed(closed_htf_closes)
        for step in substeps:                  # 1분 서브스텝
            if step.htf_bar_time != current:   # 상위TF 봉 마감
                macd.commit(last_close_of_that_bar)
            sample = macd.value(step.close)    # None = 워밍업

    `value`는 상태를 바꾸지 않고 현재가를 마지막 표본으로 얹은 히스토그램을 낸다. 봉이
    마감되면 `commit`으로 확정 종가를 굴린다 — `RealtimeRsi`·`RealtimeBand`와 같은 계약이다.
    """

    params: MacdParams = DEFAULT_MACD_PARAMS
    #: 직전 확정봉까지의 EMA 상태. 아무 봉도 커밋 안 됐으면 None.
    fast_ema: float | None = None
    slow_ema: float | None = None
    signal_ema: float | None = None
    #: 직전 확정봉의 히스토그램(`hist[1]`). 커밋 때마다 함께 굴린다.
    closed_hist: float | None = None
    #: 커밋된 확정봉 수(워밍업 판정용).
    committed: int = 0

    @classmethod
    def seed_from_closed(
        cls,
        closes: Sequence[float],
        params: MacdParams = DEFAULT_MACD_PARAMS,
        *,
        end: int | None = None,
    ) -> RealtimeMacd:
        """확정봉 종가 시퀀스로 시딩된 상태 머신을 만든다.

        `closes`는 **탭 봉 직전까지의** 상위TF 확정봉 종가(시간 오름차순)여야 한다 — 탭 봉
        자신의 종가를 넣으면 그것이 곧 WAN-115가 잡아낸 룩어헤드다. 각 종가를 순서대로
        `commit`한 것과 동일하다.

        `end`가 주어지면 `closes[:end]`(반개구간)까지만 시딩한 것과 같다 — 호출부가
        `closes[:cut]` 사본을 만들지 않고 원본을 그대로 넘길 수 있게 한다.

        ⚠️ **EMA는 재귀적이라 `RealtimeBand`처럼 「꼬리만」 커밋할 수 없다** — 창이 없고
        상태가 처음부터의 모든 표본에 의존한다. 셋업마다 처음부터 재시딩하면 O(신호수 × n)
        이므로 호출부는 증분 시더(`backtest.zone_limit_backtest._IncrementalMacdSeeder`)를
        쓴다(`RealtimeRsi`와 같은 이유·같은 해법).
        """
        state = cls(params=params)
        hi = len(closes) if end is None else min(end, len(closes))
        for i in range(hi):
            state.commit(float(closes[i]))
        return state

    @property
    def ready(self) -> bool:
        """실시간 값을 낼 수 있을 만큼 시딩됐는지 여부."""
        return self.committed >= self.params.warmup_bars

    def commit(self, closed_price: float) -> None:
        """봉이 마감됐을 때 그 확정 종가로 세 EMA와 `hist[1]`을 굴린다."""
        price = float(closed_price)
        p = self.params
        if self.fast_ema is None or self.slow_ema is None:
            # `ewm(adjust=False)`의 시드 규칙 — 첫 표본이 곧 EMA다(`indicators.ema`와 동일).
            self.fast_ema = price
            self.slow_ema = price
        else:
            a_f, a_s = _alpha(p.fast_length), _alpha(p.slow_length)
            self.fast_ema = a_f * price + (1.0 - a_f) * self.fast_ema
            self.slow_ema = a_s * price + (1.0 - a_s) * self.slow_ema
        macd = self.fast_ema - self.slow_ema
        if self.signal_ema is None:
            self.signal_ema = macd
        else:
            a_g = _alpha(p.signal_length)
            self.signal_ema = a_g * macd + (1.0 - a_g) * self.signal_ema
        self.closed_hist = macd - self.signal_ema
        self.committed += 1

    def value(self, current_price: float) -> MacdSample | None:
        """상태를 바꾸지 않고 현재가를 얹은 (라이브 hist, 직전 확정봉 hist)를 낸다.

        워밍업이라 판정할 수 없으면 `None`. NaN 가격은 값을 지어내지 않고 `None`이다.
        """
        if not self.ready:
            return None
        assert self.fast_ema is not None
        assert self.slow_ema is not None
        assert self.signal_ema is not None
        assert self.closed_hist is not None
        price = float(current_price)
        if math.isnan(price):
            return None
        p = self.params
        a_f, a_s, a_g = _alpha(p.fast_length), _alpha(p.slow_length), _alpha(p.signal_length)
        fast_live = a_f * price + (1.0 - a_f) * self.fast_ema
        slow_live = a_s * price + (1.0 - a_s) * self.slow_ema
        macd_live = fast_live - slow_live
        signal_live = a_g * macd_live + (1.0 - a_g) * self.signal_ema
        hist_live = macd_live - signal_live
        if math.isnan(hist_live) or math.isnan(self.closed_hist):
            return None
        return MacdSample(hist=hist_live, hist_prev=self.closed_hist)

    def copy(self) -> RealtimeMacd:
        """현재 상태의 독립 사본 — 시뮬레이터가 상태를 굴리므로 재사용 전에 뜬다."""
        return RealtimeMacd(
            params=self.params,
            fast_ema=self.fast_ema,
            slow_ema=self.slow_ema,
            signal_ema=self.signal_ema,
            closed_hist=self.closed_hist,
            committed=self.committed,
        )
