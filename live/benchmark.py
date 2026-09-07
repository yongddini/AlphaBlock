"""롱온리 성과의 벤치마크 — 같은 창·같은 유니버스의 「그냥 사서 들고 있기」 (WAN-407 §4).

우리는 **롱 온리**다. 오르는 장에서는 진입 규칙에 실력이 없어도 계좌가 는다 — 그래서
CLAUDE.md(WAN-393)가 *「롱온리 성과를 인용할 때는 같은 창·같은 유니버스의 buy&hold를 반드시
병기한다」*를 원칙으로 못 박아 뒀다. 이 모듈은 그 병기를 **파리티 리포트와 같은 실행에서**
낸다 — 따로 짜면 두 표가 다른 실행·다른 창에서 나와 나란히 놓을 수 없다.

🚨 **이 표는 「엣지 있음/없음」을 묻지 않는다.** buy&hold를 이겨도 「엣지 있음」이 아니다 —
「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386)이 묻는 것은 *「같은 존에 **무작위 시각**
으로 들어가도 비슷한가」*이고 **다른 질문**이다. 이 표가 막는 것은 **한 방향의 오독** 하나다:
오르는 장의 플러스를 실력으로 읽는 것. 2026-08-31에 실제로 난 사고가 그것이다(페이퍼가
+8.5%로 보였는데 같은 창 12종목 buy&hold가 +22.55%였고, 비용을 제대로 세니 −9.4%였다 —
WAN-392/393).

📌 **평균만 내지 않는다** — 한 종목이 평균을 통째로 끌어올린다(WAN-346이 「평균 −6.6% vs
중앙값 −26.6%」로 겪은 자리). 종목별 · 단순평균 · **중앙값** · 오른 종목 수를 함께 낸다.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from data.storage import OhlcvStore

#: 종가를 읽을 TF 선호 순서 — 창 안에 2봉 이상 있는 첫 TF를 쓴다. 짧은 창(며칠)에서도
#: 창 경계에 가까운 종가를 잡으려면 촘촘한 TF가 낫고, 없으면 성긴 쪽으로 내려간다.
BENCHMARK_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "2h", "4h", "1d")


@dataclass(frozen=True)
class BuyHoldRow:
    """한 종목의 「창 첫 종가에 사서 마지막 종가에 판다」 결과.

    `timeframe`이 None이면 그 창에 봉이 없다(= 측정 불가). 0으로 세지 않는다 — 「안 올랐다」와
    「데이터가 없었다」가 표에서 같아 보이면 안 된다(WAN-95 부류).
    """

    symbol: str
    timeframe: str | None
    first_close: float | None
    last_close: float | None
    bars: int

    @property
    def total_return(self) -> float | None:
        """구간 수익률(그로스). 측정 불가면 None."""
        if self.first_close is None or self.last_close is None or self.first_close <= 0:
            return None
        return self.last_close / self.first_close - 1.0


@dataclass(frozen=True)
class BuyHoldSummary:
    """창 하나의 유니버스 buy&hold 요약."""

    start_ms: int
    end_ms: int
    rows: tuple[BuyHoldRow, ...]

    @property
    def measured(self) -> tuple[BuyHoldRow, ...]:
        """측정된 행만(데이터 없는 종목 제외)."""
        return tuple(r for r in self.rows if r.total_return is not None)

    @property
    def n(self) -> int:
        return len(self.measured)

    @property
    def missing(self) -> tuple[str, ...]:
        """창에 봉이 없어 못 잰 종목 — 감추지 않고 표에 밝힌다."""
        return tuple(r.symbol for r in self.rows if r.total_return is None)

    @property
    def mean(self) -> float | None:
        values = [r.total_return for r in self.measured if r.total_return is not None]
        return statistics.fmean(values) if values else None

    @property
    def median(self) -> float | None:
        values = [r.total_return for r in self.measured if r.total_return is not None]
        return statistics.median(values) if values else None

    @property
    def up_count(self) -> int:
        return sum(1 for r in self.measured if (r.total_return or 0.0) > 0.0)


def buy_hold_row(
    store: OhlcvStore,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    timeframes: Sequence[str] = BENCHMARK_TIMEFRAMES,
) -> BuyHoldRow:
    """한 종목의 창 안 buy&hold 한 줄. 봉이 2개 미만이면 측정 불가로 둔다."""
    for timeframe in timeframes:
        frame = store.load(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
        if len(frame) < 2:
            continue
        closes = frame["close"].astype(float)
        return BuyHoldRow(
            symbol=symbol,
            timeframe=timeframe,
            first_close=float(closes.iloc[0]),
            last_close=float(closes.iloc[-1]),
            bars=len(frame),
        )
    return BuyHoldRow(symbol=symbol, timeframe=None, first_close=None, last_close=None, bars=0)


def buy_hold_summary(
    db_path: str,
    symbols: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    timeframes: Sequence[str] = BENCHMARK_TIMEFRAMES,
) -> BuyHoldSummary:
    """유니버스 전체의 창 buy&hold 요약(종목 순서 보존)."""
    with OhlcvStore(db_path) as store:
        rows = [
            buy_hold_row(store, symbol, start_ms=start_ms, end_ms=end_ms, timeframes=timeframes)
            for symbol in symbols
        ]
    return BuyHoldSummary(start_ms=start_ms, end_ms=end_ms, rows=tuple(rows))
