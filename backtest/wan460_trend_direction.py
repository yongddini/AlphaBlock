"""WAN-460 — 추세 신호로 팔의 방향을 정한다.

갈래 A(지금 그대로) · B(하락 추세면 팔 쉼) · C(하락 추세면 팔 숏).

🚧 **이 모듈은 아직 측정이 아니다 — 토대 한 조각(추세 신호의 인과적 정의 · 갈래 배정)이다.**
이슈의 판정 표(가장 나쁜 해 · 앞/뒤 · 두 크기)는 다음 두 선행이 끝나야 나온다(이슈 코멘트에 실측
근거와 함께 적었다).

1. **정본 입력 재구축** — WAN-449 정본이 읽는 캐시(`backtest/cache/wan442_markets.pkl` ·
   `backtest/cache/wan447_funding.pkl` · `data/cache/wan438-roots` · `data/cache/wan440-roots`)가
   이 머신에 없다(2026-10-19 확인 · gitignore라 작업 트리를 지울 때 함께 사라진 것으로 보인다).
   갈래 A의 검산 (a)(WAN-449 해마다 수익 소수점까지)는 이 재구축 없이는 걸 수 없다.
2. **팔 숏 거울(갈래 C)** — WAN-436 계층(`build_entries` · `arm_exit` · `mtm_path` ·
   `rule_decision`)과 WAN-448 체결 α가 **롱 전용**이다(저가만 들고 다니고 `entry − stop`을 가정).

## 여기서 못 박는 것(이슈 「측정 전에 못 박는다」)

* **추세 신호** = BTC 엔진 C 신호 `mean(sign(P_t / P_{t−L} − 1))`(L ∈ 20·60·120·250일)의 **부호**.
  계산은 `strategy.tsmom.target_from_daily_closes`와 **같은 경로**(수익 → 누적곱 가격 → 룩백
  비교)다 — 페이퍼가 쓰는 함수와 갈라지지 않게 그 모듈의 상수·식을 그대로 쓴다.
* **시점** = 진입 시각 `t`에 **이미 닫힌 마지막 일봉** D(`D + 1일 ≤ t`)의 신호. 그날 봉은 안 쓴다.
* **신호 0 · 이력 부족(NaN)** = 갈래 A처럼(팔 롱) — 이슈 규정.

**측정 전용 · 기본값·토대·페이퍼 불변 · 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, TypeVar

import numpy as np
import pandas as pd

from strategy import tsmom

DAY_MS = tsmom.DAY_MS

Branch = Literal["A", "B", "C"]
BRANCHES: tuple[Branch, ...] = ("A", "B", "C")
BRANCH_LABELS: dict[Branch, str] = {
    "A": "A · 지금 그대로",
    "B": "B · 하락 추세면 팔 쉼",
    "C": "C · 하락 추세면 팔 숏",
}
Side = Literal["long", "short"]


def daily_trend_signal(open_time_ms: Sequence[int], close: Sequence[float]) -> pd.Series:
    """확정 일봉마다 BTC 추세 신호(−1 · −0.5 · 0 · 0.5 · 1 · 이력 부족은 NaN).

    index = 일봉 open_time(ms).

    `strategy.tsmom.target_from_daily_closes`와 같은 경로로 계산한다(그 함수는 마지막 날 하나만
    내므로 날마다 다시 부르면 O(N²) — 여기서는 한 번에 낸다). 등식은 회귀 테스트가 날마다 건다.
    🚨 확정봉만 줄 것(형성 중인 오늘 봉이 섞이면 룩어헤드다). 날짜가 비면 거부한다.
    """
    times = pd.Series(np.asarray(open_time_ms, dtype=np.int64))
    closes = pd.Series(np.asarray(close, dtype=float))
    if len(times) != len(closes):
        raise ValueError("open_time과 close의 길이가 다르다")
    if len(times) == 0:
        raise ValueError("확정 일봉이 없다")
    steps = times.diff().dropna()
    if len(steps) and not (steps == DAY_MS).all():
        raise ValueError("일봉이 연속이 아니다 — 「L일 전」이 다른 날을 가리킨다")
    if closes.isna().any() or (closes <= 0).any():
        raise ValueError("종가에 결측·비양수가 있다")
    returns = closes.pct_change()
    price = (1.0 + returns.fillna(0.0)).cumprod()
    sig = sum(np.sign(price / price.shift(lb) - 1.0) for lb in tsmom.LOOKBACKS) / len(
        tsmom.LOOKBACKS
    )
    out = pd.Series(sig.to_numpy(float), index=times.to_numpy(np.int64))
    out.index.name = "open_time"
    return out


@dataclass(frozen=True)
class TrendLookup:
    """진입 시각 → 그 순간 이미 닫힌 마지막 일봉의 추세 신호."""

    open_times: tuple[int, ...]
    signals: tuple[float, ...]

    @classmethod
    def from_series(cls, signal: pd.Series) -> TrendLookup:
        idx = [int(v) for v in signal.index]
        if idx != sorted(idx):
            raise ValueError("일봉 open_time이 오름차순이 아니다")
        return cls(tuple(idx), tuple(float(v) for v in signal.to_numpy(float)))

    def signal_at(self, entry_ms: int) -> float:
        """`D + 1일 ≤ entry_ms`인 마지막 D의 신호 — 없으면 NaN."""
        # 닫힌 봉 = open_time ≤ entry − 1일
        i = bisect.bisect_right(self.open_times, int(entry_ms) - DAY_MS) - 1
        return self.signals[i] if i >= 0 else float("nan")

    def sign_at(self, entry_ms: int) -> int:
        """+1 · 0 · −1(NaN은 0 — 갈래 A처럼 다룬다)."""
        s = self.signal_at(entry_ms)
        if not np.isfinite(s) or s == 0.0:
            return 0
        return 1 if s > 0 else -1


def branch_side(branch: Branch, sign: int) -> Side | None:
    """그 갈래 · 그 추세에서 팔이 낼 방향(None = 쉼). 이슈 표 그대로."""
    if branch not in BRANCHES:
        raise ValueError(f"갈래는 {BRANCHES} 중 하나: {branch!r}")
    if sign >= 0 or branch == "A":
        return "long"
    return None if branch == "B" else "short"


T = TypeVar("T")


def select_for_branch(
    items: Sequence[T],
    *,
    branch: Branch,
    side: Side,
    entry_ms: Callable[[T], int],
    lookup: TrendLookup,
) -> list[T]:
    """`side` 방향 후보 중 그 갈래가 그 시각에 허용하는 것만 남긴다(순서 보존).

    갈래 B의 롱 = 갈래 A의 롱 중 「추세 아래가 아닌」 것 — 검산 (b)가 그 부분집합을 건다.
    """
    return [x for x in items if branch_side(branch, lookup.sign_at(entry_ms(x))) == side]
