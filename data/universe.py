"""스토캐스틱 팔의 주간 종목 목록 규칙 — 백테스트와 페이퍼가 같이 쓰는 **단일 소스** (WAN-451).

정본(WAN-444 · WAN-449): 매주 월요일 00:00 UTC에 **직전 30일 평균 거래대금**(확정 일봉의
`거래량 × 종가`) 상위 31종목을 고른다. 편입 자격은 둘이다.

* **신선도** — 직전 확정 일봉이 3일 안에 있다(상폐·거래 정지 종목이 옛 점수로 자리를 차지하지 않게).
* **나이** — 첫 일봉부터 90일 이상 지났다.

동점은 심볼 이름순이다. 목록에서 빠져도 보유 포지션은 유지하고 **새 진입만 막는다**
(`force_close=False`, WAN-449 채택).

이 함수들은 `backtest/wan442_momentum_rotation.py`·`wan444_liquidity_universe.py`·
`wan440_universe.py`에 있던 것을 옮긴 것이다(그 모듈들은 이제 여기서 import한다 — 두 벌로
갈라지면 페이퍼가 백테스트와 다른 목록을 고른다, WAN-305). `data/`에 둔 이유는 수집기
(`data.universe_pool`)가 쓰기 때문이다 — `data`는 `strategy`를 import할 수 없다(레이어 규칙).

⚠️ **후보 풀은 정본과 다르다**(사용자 결정 ② 나, 2026-10-14): 정본은 「2020-09-15 이전 상장
53종목 고정」이고 페이퍼는 「지금 상장된 USDT 무기한 전체」다. **규칙(순위·자격)은 같고 풀만
다르다.** 이 풀의 백테스트는 없다.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS

# --- 정본 상수(WAN-442/444 착수 전 고정 — 페이퍼 결과를 보고 바꾸지 않는다) -----------------
TOP_N = 31
REBALANCE_DAYS = 7
"""리밸런싱 간격(일) — 월요일 00:00 UTC 격자."""
MIN_AGE_DAYS = 90
FRESH_DAYS = 3
"""직전 확정 일봉이 이 일수 안에 있어야 편입 자격(상폐 · 정지 종목 배제)."""
LIQ_DAYS = 30
"""거래대금 평균 창(일, 직전 확정 일봉 포함)."""
QUOTE = "USDT"

#: 팔이 매매하는 TF(정본 `wan424.TIMEFRAMES` 9개 + 15m — WAN-430/440).
ARM_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d", "1w")
#: 수집기가 원천 kline으로 받는 TF(1분봉 포함). 2h는 저장소가 1h에서 파생하고(WAN-24),
#: 3h는 바이낸스에 없어 수집기가 1h에서 접어 저장한다(`BUILT_TIMEFRAMES`).
ARM_STREAM_TIMEFRAMES: tuple[str, ...] = ("1m", "15m", "1h", "4h", "6h", "8h", "12h", "1d", "1w")
#: 수집기가 만들어 저장하는 TF → 원천 TF.
BUILT_TIMEFRAMES: dict[str, str] = {"3h": "1h"}

#: 1970-01-01은 목요일이라 월요일 00:00 UTC 격자는 epoch에서 4일 밀려 있다.
_MONDAY_OFFSET_MS = 4 * DAY_MS


@dataclass(frozen=True)
class Daily:
    open_time: np.ndarray
    close: np.ndarray
    quote: np.ndarray
    """거래대금 = 거래량 × 종가."""


def is_usdt_perp(symbol: str) -> bool:
    """`…USDT`이고 만기물(`_YYMMDD`)이 아닌 것(거래소 심볼 이름 기준, 예 `BTCUSDT`).

    비ASCII 이름은 2020년 이후 상장이라 정본 풀 밖이었다 — 페이퍼 풀도 같은 판정을 쓴다.
    """
    return symbol.isascii() and symbol.endswith(QUOTE) and "_" not in symbol


def last_closed(d: Daily, t: int) -> int:
    """시각 t에 이미 닫힌 마지막 일봉의 인덱스(`open_time + 1일 ≤ t`) · 없으면 −1."""
    return int(np.searchsorted(d.open_time, t - DAY_MS, side="right")) - 1


def eligible(d: Daily, t: int) -> int | None:
    """편입 자격이 있으면 직전 확정 일봉 인덱스, 없으면 None."""
    j = last_closed(d, t)
    if j < 0:
        return None
    if int(d.open_time[j]) < t - FRESH_DAYS * DAY_MS:
        return None
    if int(d.open_time[j]) - int(d.open_time[0]) < MIN_AGE_DAYS * DAY_MS:
        return None
    return j


def liquidity(d: Daily, j: int, days: int = LIQ_DAYS) -> float | None:
    """확정 일봉 j까지 직전 `days`일(j 포함) 평균 거래대금. 없거나 0이면 None."""
    lo = int(np.searchsorted(d.open_time, int(d.open_time[j]) - days * DAY_MS, side="right"))
    window = d.quote[lo : j + 1]
    if len(window) == 0:
        return None
    mean = float(window.mean())
    return mean if mean > 0.0 else None


def liquidity_scores(daily: Mapping[str, Daily], t: int) -> dict[str, float]:
    """시각 t에 자격이 있는 종목의 거래대금 점수(자격 없음·점수 없음은 빠진다)."""
    scores: dict[str, float] = {}
    for sym, d in daily.items():
        j = eligible(d, t)
        if j is None:
            continue
        v = liquidity(d, j)
        if v is not None:
            scores[sym] = v
    return scores


def rank_symbols(scores: Mapping[str, float], *, top: bool = True) -> list[str]:
    """점수 순(동점은 이름순) 전체 순위."""
    return sorted(scores, key=lambda s: (-scores[s] if top else scores[s], s))


def select_liquidity(
    daily: Mapping[str, Daily], t: int, *, top: bool = True, n: int = TOP_N
) -> frozenset[str]:
    """시각 t의 목록 — 편입 자격이 있는 종목 중 거래대금 순 n개(동점은 이름순)."""
    return frozenset(rank_symbols(liquidity_scores(daily, t), top=top)[:n])


def rebalance_times(start_ms: int, end_ms: int, days: int = REBALANCE_DAYS) -> list[int]:
    """`start_ms` 이하의 월요일 00:00 UTC부터 `days`일 간격(1970-01-01은 목요일)."""
    t = start_ms - ((start_ms - _MONDAY_OFFSET_MS) % WEEK_MS)
    out: list[int] = []
    while t < end_ms:
        out.append(t)
        t += days * DAY_MS
    return out


def rebalance_at(t: int) -> int:
    """시각 t에 유효한 목록의 리밸런싱 시각 = t 이하의 마지막 월요일 00:00 UTC."""
    return t - ((t - _MONDAY_OFFSET_MS) % WEEK_MS)


@dataclass(frozen=True)
class Rotation:
    times: tuple[int, ...]
    members: tuple[frozenset[str], ...]

    def index_at(self, t: int) -> int:
        return bisect.bisect_right(self.times, t) - 1


class _DailySource(Protocol):
    def load(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int | None = ...,
        end_ms: int | None = ...,
    ) -> pd.DataFrame: ...


def load_daily(
    stores: Sequence[_DailySource], symbols: Sequence[str], start_ms: int, end_ms: int
) -> dict[str, Daily]:
    """저장된 **확정** 일봉 → `Daily`. 여러 저장소면 그 심볼이 처음 나오는 저장소를 쓴다."""
    out: dict[str, Daily] = {}
    for s in symbols:
        for st in stores:
            f = st.load(s, "1d", start_ms=start_ms, end_ms=end_ms)
            if "closed" in f.columns:
                f = f[f["closed"].astype(bool)]
            if len(f):
                f = f.sort_values("open_time")
                close = f["close"].to_numpy(float)
                out[s] = Daily(
                    f["open_time"].to_numpy(np.int64),
                    close,
                    f["volume"].to_numpy(float) * close,
                )
                break
    return out
