"""CLI ↔ 기존 리포트 회귀 검증 (WAN-101 완료기준).

`python -m backtest.run`의 **기본값 실행**이 WAN-95/99 리포트의 해당 셀과 숫자까지
일치하는지 실데이터로 확인한다 — "엔진이 조용히 달라지지 않았음"의 직접 증거다.
`tests/test_harness.py`는 같은 보장을 파라미터·호출 경로 수준에서 CI 안전하게 고정하고,
이 파일은 그 위에 **실제 산출 숫자**를 얹는다.

실데이터(`data/ohlcv.db`, 약 580MB)는 저장소에 없으므로 CI에서는 통째로 skip된다.
로컬에 데이터가 있으면 자동으로 돈다. 비용을 감당 가능하게 두려고 심볼 1개 × TF 1개로
좁혔다 — 전 격자 대조는 아래 재현 커맨드로 수행한다:

```
python -m backtest.run --symbol BTCUSDT,ETHUSDT,SOLUSDT --tf 1h --fill pen_5bp --format csv
```
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import RunRow
from backtest.run import RunOptions, build_parser, grid_from_args, run_grid

_DB_PATH = Path("data/ohlcv.db")
_WAN95_CSV = Path("backtest/reports/wan95_zone_limit_recompute.csv")
_WAN99_CSV = Path("backtest/reports/wan99_zone_limit_offset.csv")

#: 대조 심볼·TF. 1분봉 로딩이 실행 시간을 지배하므로 1셀로 좁힌다.
_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"

pytestmark = pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason=f"실데이터({_DB_PATH})가 없어 회귀 대조를 건너뜁니다(CI 기본).",
)


def _run(argv: list[str]) -> RunRow:
    """CLI 인자로 한 셀을 돌려 그 행을 낸다."""
    grid = grid_from_args(build_parser().parse_args(argv))
    rows = run_grid(grid, RunOptions(years=3.0), log=False)
    assert len(rows) == 1, f"대조는 한 셀이어야 합니다: {len(rows)}행"
    return rows[0]


def _report_cell(csv: Path, **filters: object) -> pd.Series:
    frame = pd.read_csv(csv)
    for column, value in filters.items():
        frame = frame[frame[column] == value]
    assert len(frame) == 1, f"{csv.name}에서 셀을 특정하지 못했습니다: {filters} → {len(frame)}행"
    return frame.iloc[0]


def _assert_matches(row: RunRow, cell: pd.Series, columns: list[str]) -> None:
    for column in columns:
        actual = getattr(row, column)
        assert actual == pytest.approx(float(cell[column]), abs=1e-9), (
            f"{column}: CLI {actual} != 리포트 {cell[column]}"
        )


@pytest.mark.skipif(not _WAN95_CSV.exists(), reason="WAN-95 리포트 CSV 없음")
def test_cli_defaults_reproduce_wan95_zone_limit_cell() -> None:
    """인자 없는 기본 실행 == WAN-95 채택 기준선(지정가 + 롱 온리 + 1.5R) 셀."""
    row = _run(["--symbol", "BTCUSDT", "--tf", _TIMEFRAME])
    cell = _report_cell(_WAN95_CSV, symbol=_SYMBOL, timeframe=_TIMEFRAME, entry_mode="zone_limit")
    _assert_matches(
        row,
        cell,
        ["total_return", "win_rate", "max_drawdown", "num_trades", "fill_rate"],
    )


@pytest.mark.skipif(not _WAN99_CSV.exists(), reason="WAN-99 리포트 CSV 없음")
def test_cli_reproduces_wan99_pen_5bp_cell() -> None:
    """`--fill pen_5bp` == WAN-99의 (full, 오프셋 0, pen_5bp) 셀.

    `mean_r`·`fill_rate`까지 맞아야 한다 — 거래 수만 같고 손익이 다르면 비용·사이징
    배선이 갈린 것이고, 그건 표를 나란히 읽는 순간 드러나지 않는 종류의 오차다.
    """
    row = _run(["--symbol", "BTCUSDT", "--tf", _TIMEFRAME, "--fill", "pen_5bp"])
    cell = _report_cell(
        _WAN99_CSV,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        segment="full",
        offset_bps=0.0,
        assumption="pen_5bp",
    )
    _assert_matches(
        row,
        cell,
        [
            "total_return",
            "win_rate",
            "max_drawdown",
            "num_trades",
            "fill_rate",
            "mean_r",
            "sharpe",
        ],
    )
