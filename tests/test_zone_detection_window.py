"""탐지 컨텍스트 창이 존 목록을 바꾸는가 — WAN-343 §2-3의 실측 고정.

## 왜 이 검사가 필요한가

WAN-342가 관찰한 「짝 없는 셋업의 76.5%가 `(a) 존 없음`」의 후보 원인 하나가 **탐지 컨텍스트
창**이었다(이슈 §2-3): 라이브 러너는 확정 상위TF 봉 `live_signal_lookback_bars`(기본 **1500**)
개를 탐지기에 먹이고, 대조 백테는 `DEFAULT_WARMUP_DAYS`(**120일**)치를 먹인다. 두 창은 TF마다
크게 다르고(15m은 백테가 7.7배 길고, 4h는 라이브가 3.5배 길다) **방향도 반대**다.

이 검사가 고정하는 사실: **두 창이 함께 보는 구간에서는 탐지가 같다.** 존 정체성뿐 아니라
생애 필드(`break_time`·`swept_time`·`tapped_times`)까지 같다 — 즉 창 길이는 그 구간의 존을
만들지도 지우지도, 더 일찍/늦게 죽이지도 않는다. 차이는 **창 시작 근처 몇 봉**에만 남는다
(탐지기가 스윙 피벗을 세우는 데 앞선 봉이 필요하다 — 워밍업 경계 효과).

🚨 **이 검사가 죽으면 결론이 바뀐다** — 탐지가 창에 민감해지면 `(a) 존 없음`의 원인 후보로
창이 되살아나고, 나아가 「라이브와 백테가 같은 엔진」이라는 파리티 전제 자체가 흔들린다.

⚠️ **검사하지 않는 것**: 창 밖(오래된) 존은 당연히 한쪽에만 있다 — 그건 창 길이의 정의이지
탐지 차이가 아니다. 그 갈래는 `live.zone_audit`의 `창 밖` 사유가 따로 센다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data.models import timeframe_to_ms
from strategy.models import OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import OrderBlockDetector

_REAL_DB = Path("data/ohlcv.db")
_DAY_MS = 86_400_000
#: 라이브 러너의 창(`Settings.live_signal_lookback_bars` 기본값)과 대조 백테의 워밍업
#: (`live.live_vs_backtest.DEFAULT_WARMUP_DAYS`) — 두 기본값에서 읽는다(리터럴 재기입 금지).
#: 값이 바뀌면 이 검사가 조용히 옛 창을 재는 것을 막는다.
_BOUNDARY_BARS = 50
"""창 시작에서 이 봉 수 이내의 존은 **워밍업 경계 효과**로 갈릴 수 있다. 로컬 실측(6종목 ×
15m·1h·4h · 공통구간 존 666개)에서 불일치 6개가 전부 경계+5~25봉이었다 — 넉넉히 잡되 창
길이(1500봉)에 비하면 여전히 4% 미만이다."""

#: 못 박은 칸 — 1h는 두 창(1500봉 vs 2880봉)이 크게 다르면서도 탐지가 빠르다.
_CELLS = [("BTC/USDT:USDT", "1h"), ("ETH/USDT:USDT", "1h")]


def _load(
    conn: sqlite3.Connection, symbol: str, timeframe: str, start_ms: int, end_ms: int
) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume FROM ohlcv"
        " WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ?"
        " AND closed = 1 ORDER BY open_time",
        conn,
        params=(symbol, timeframe, start_ms, end_ms),
    )


def _zones(df: pd.DataFrame) -> dict[tuple[bool, int, int], tuple[object, ...]]:
    """존 정체성 → 생애 필드. 정체성만 비교하면 「같은 존을 다르게 죽이는」 차이를 놓친다."""
    result = OrderBlockDetector(OrderBlockParams()).run(df)
    return {
        (ob.direction is OrderBlockDirection.BULLISH, ob.start_time, ob.confirmed_time): (
            ob.break_time,
            ob.swept_time,
            tuple(ob.tapped_times),
        )
        for ob in result.order_blocks
    }


@pytest.mark.parametrize(("symbol", "timeframe"), _CELLS)
def test_detection_agrees_where_both_windows_look(symbol: str, timeframe: str) -> None:
    from config.settings import Settings
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS

    if not _REAL_DB.exists():
        pytest.skip("실데이터(data/ohlcv.db) 없음")
    lookback = Settings().live_signal_lookback_bars
    tf_ms = timeframe_to_ms(timeframe)
    conn = sqlite3.connect(_REAL_DB)
    try:
        row = conn.execute(
            "SELECT MAX(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = ? AND closed = 1",
            (symbol, timeframe),
        ).fetchone()
        if not row or row[0] is None:
            pytest.skip(f"{symbol} {timeframe} 실데이터 없음")
        now = int(row[0])
        live_df = _load(conn, symbol, timeframe, now - (lookback + 50) * tf_ms, now).tail(lookback)
        backtest_df = _load(conn, symbol, timeframe, now - DEFAULT_WARMUP_DAYS * _DAY_MS, now)
    finally:
        conn.close()
    if len(live_df) < lookback // 2 or backtest_df.empty:
        pytest.skip("창을 채울 실데이터가 부족합니다")

    live_zones, backtest_zones = _zones(live_df), _zones(backtest_df)
    common_start = max(int(live_df["open_time"].iloc[0]), int(backtest_df["open_time"].iloc[0]))
    settled = common_start + _BOUNDARY_BARS * tf_ms

    def _inside(keys: set[tuple[bool, int, int]]) -> set[tuple[bool, int, int]]:
        return {key for key in keys if key[1] >= settled}

    live_inside = _inside(set(live_zones))
    backtest_inside = _inside(set(backtest_zones))
    assert live_inside, "공통 구간에 존이 없어 공허한 검사입니다(창을 바꾸세요)."
    # 정체성이 같고 …
    assert live_inside == backtest_inside
    # … 생애까지 같다(같은 존을 더 일찍/늦게 죽이지 않는다).
    assert all(live_zones[key] == backtest_zones[key] for key in live_inside)
