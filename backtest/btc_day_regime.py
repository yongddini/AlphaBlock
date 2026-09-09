"""BTC의 **KST 일간 등락** — 성적을 가르는 설명 변수 (WAN-412).

WAN-408/411이 「손실이 몰리는 날」을 찾고 WAN-412가 그 날들이 **BTC가 내린 날**임을 잰다.
그 축을 쓰는 리포트가 저마다 날을 다시 정의하면 두 표가 조용히 갈라지므로(WAN-146의 교훈)
정의를 **한 곳**에 둔다.

## 정의 (이슈가 못 박은 그대로)

* 하루 = **KST 달력일**(`open_time` UTC + 9h · WAN-172 표시 규약의 날 축 판).
* 그날의 등락 = 그날 첫 1h 봉의 `open` → 마지막 1h 봉의 `close`.
* 자료는 **저장 1h 봉**(`ohlcv`)이고 우리 거래 가격이 아니다.

🚨 **우리 거래의 진입가에서 BTC 방향을 뽑으면 안 된다** — 롱 지정가는 「가격이 내려온
자리」에 조건부라 표본이 편향된다(PM이 실제로 그렇게 해서 「오른 날 44 : 내린 날 146」이라는
불가능한 분포를 얻었다 — 진짜 시세로는 390 : 370이다).

⚠️ **설명 변수이지 진입 조건이 아니다** — 「그날 BTC가 오를지」는 그날이 끝나야 알므로
이것으로 거르는 것은 인과적으로 불가능하다(WAN-408이 「폭락일을 지우면」에 건 함정과 같은
자리). 성적을 **가르는 축**으로만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest import harness
from data.storage import OhlcvStore

#: KST 오프셋(ms) — 저장은 UTC epoch, 표시·일 경계만 KST(WAN-172).
KST_OFFSET_MS = 9 * 3600 * 1000

#: 방향 축을 뽑는 기준 종목·TF. 1h는 12종목 전부가 6년을 덮는 축이다(WAN-182/307).
REGIME_SYMBOL = "BTC/USDT:USDT"
REGIME_TIMEFRAME = "1h"

#: 5분위 — 이슈가 지정한 해상도.
NUM_QUANTILES = 5


@dataclass(frozen=True)
class DailyRegime:
    """(KST 일 → 그날 BTC 등락) 표와 그 위에서 잰 분위."""

    frame: pd.DataFrame
    """인덱스 `kst_day`(YYYY-MM-DD) · 열 `open`/`close`/`high`/`low`/`bars`/`ret`."""

    def returns_for(self, days: pd.Series) -> pd.Series:
        """일 라벨 시리즈를 그날 등락으로 옮긴다(없는 날은 NaN)."""
        return days.map(self.frame["ret"])

    def quantiles_for(self, days: pd.Series, *, q: int = NUM_QUANTILES) -> pd.DataFrame:
        """**거래가 실제로 난 날들만**으로 분위를 낸다.

        🚨 분위를 전 기간 일봉에서 내면 거래가 없는 날이 경계를 움직여, 「이 표가 가르는
        날들」과 「경계를 정한 날들」이 어긋난다. 반환 프레임은 인덱스가 `kst_day`이고
        열은 `ret`·`quantile`(1 = 가장 많이 내린 날)이다.
        """
        present = sorted({d for d in days.dropna().unique() if d in self.frame.index})
        if not present:
            return pd.DataFrame(columns=["ret", "quantile"])
        sub = self.frame.loc[present, ["ret"]].copy()
        sub["quantile"] = pd.qcut(sub["ret"], q, labels=list(range(1, q + 1)))
        return sub


def kst_day_of(ms: int | float) -> str:
    """UTC epoch(ms) → KST 달력일 라벨."""
    return str(pd.Timestamp(int(ms) + KST_OFFSET_MS, unit="ms").strftime("%Y-%m-%d"))


def load_daily_regime(
    *,
    symbol: str = REGIME_SYMBOL,
    timeframe: str = REGIME_TIMEFRAME,
    db_path: str = harness.DB_PATH,
) -> DailyRegime:
    """저장 1h 봉에서 KST 일봉을 접는다 (`open`=그날 첫 봉 · `close`=마지막 봉)."""
    df = OhlcvStore(db_path).load(symbol, timeframe).reset_index(drop=True)
    if df.empty:
        raise ValueError(
            f"{symbol} {timeframe}: 저장된 봉이 없습니다 — BTC 방향 축을 낼 수 없습니다."
        )
    day = pd.to_datetime(df["open_time"] + KST_OFFSET_MS, unit="ms").dt.strftime("%Y-%m-%d")
    grouped = df.assign(kst_day=day).groupby("kst_day", sort=True)
    frame = pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "close": grouped["close"].last(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "bars": grouped.size(),
        }
    )
    frame["ret"] = frame["close"] / frame["open"] - 1.0
    return DailyRegime(frame=frame)
