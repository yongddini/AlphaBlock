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

⚠️ skip 판정은 **파일 존재가 아니라 실제 데이터 유무**로 한다. `data/ohlcv.db` 파일이
있다고 봉이 들어 있는 건 아니다 — `OhlcvStore.__init__`이 `sqlite3.connect`로 빈 DB를
스키마만 만들어 놓고, 실제로 `dashboard.app` 임포트(= `tests/test_dashboard_app.py`
수집)만으로도 그 빈 파일이 생긴다. 그 모듈이 이 모듈보다 알파벳순으로 앞이라, 파일
존재로 판정하면 CI에서 skip이 안 걸리고 "0행" 실패가 난다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import RunRow, load_market_data
from backtest.run import JOBS_AUTO, RunOptions, build_parser, grid_from_args, run_grid

_WAN95_CSV = Path("backtest/reports/wan95_zone_limit_recompute.csv")
_WAN99_CSV = Path("backtest/reports/wan99_zone_limit_offset.csv")

#: 대조 심볼·TF. 1분봉 로딩이 실행 시간을 지배하므로 1셀로 좁힌다.
_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"
_YEARS = 3.0

#: `--jobs` 대조용(WAN-121). fan-out 단위가 (심볼, TF)라 **심볼이 2개는 돼야** 워커가
#: 실제로 둘로 갈린다(1셀이면 `resolve_jobs`가 1로 접어 직렬과 같은 경로를 탄다 —
#: 그러면 "병렬이 같다"를 증명한 게 아니라 병렬을 안 돈 것이다).
_JOBS_SYMBOLS = "BTCUSDT,ETHUSDT"
#: 병렬 대조는 리포트 셀과 맞출 필요가 없으므로 창을 좁혀 비용을 줄인다(실데이터 유지).
_JOBS_YEARS = 0.5


@pytest.fixture(autouse=True)
def _require_real_data() -> None:
    """대조 심볼의 봉이 실제로 있을 때만 돈다(없으면 skip).

    1분봉은 읽지 않는다 — 존재 확인에 수백 MB를 읽을 이유가 없다.
    """
    market = load_market_data(_SYMBOL, _TIMEFRAME, years=_YEARS, need_1m=False, funding=False)
    if market.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터가 없어 회귀 대조를 건너뜁니다(CI 기본).")


def _run(argv: list[str]) -> RunRow:
    """CLI 인자로 한 셀을 돌려 그 행을 낸다.

    데이터 유무는 픽스처가 이미 확인했으므로, 여기서 0행이 나오면 그건 진짜 배선 버그다.
    """
    grid = grid_from_args(build_parser().parse_args(argv))
    rows = run_grid(grid, RunOptions(years=_YEARS), log=False)
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
    """`--fill pen_5bp --offset-bps 0` == WAN-99의 (full, 오프셋 0, pen_5bp) 셀.

    `mean_r`·`fill_rate`까지 맞아야 한다 — 거래 수만 같고 손익이 다르면 비용·사이징
    배선이 갈린 것이고, 그건 표를 나란히 읽는 순간 드러나지 않는 종류의 오차다.

    ⚠️ `--offset-bps 0`을 **명시**해야 한다(WAN-112): 채택 기본 오프셋이 2bp가 되면서
    CLI 기본 실행은 더 이상 WAN-99의 오프셋 0 셀이 아니다. 이 인자가 "옛 셀과 대조하려고
    옛 엔진을 요청한다"는 사실을 드러낸다 — 빼면 다른 엔진의 숫자를 같은 셀로 착각한다.
    """
    row = _run(
        ["--symbol", "BTCUSDT", "--tf", _TIMEFRAME, "--fill", "pen_5bp", "--offset-bps", "0"]
    )
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


def _jobs_rows(jobs: int) -> list[RunRow]:
    grid = grid_from_args(
        build_parser().parse_args(["--symbol", _JOBS_SYMBOLS, "--tf", _TIMEFRAME])
    )
    return run_grid(grid, RunOptions(years=_JOBS_YEARS), log=False, jobs=jobs)


def test_jobs_does_not_change_real_data_numbers() -> None:
    """완료기준(WAN-121): `--jobs`가 **실데이터 숫자**를 바꾸지 않는다.

    합성 데이터 대조(`tests/test_run_cli.py`)는 배선·순서·pickle을 잡지만, 지정가 팔이
    0거래라 손익까지는 못 잰다(볼린저 기본 필터가 합성 후보를 전부 거른다). 실데이터는
    체결·손익·펀딩이 전부 흐르는 유일한 축이라, 병렬이 숫자를 흔드는지는 **여기서만**
    진짜로 증명된다. `--jobs`는 성능 노브이지 결과 축이 아니다.
    """
    serial = _jobs_rows(1)
    assert len(serial) == 2, f"2심볼 대조가 아니다: {len(serial)}행"
    assert any(row.num_trades > 0 for row in serial), "실데이터가 거래를 내지 않았다"

    for jobs in (2, JOBS_AUTO):
        parallel = _jobs_rows(jobs)
        assert [r.model_dump() for r in parallel] == [r.model_dump() for r in serial], (
            f"--jobs {jobs}의 결과가 직렬과 다르다"
        )
