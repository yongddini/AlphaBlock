"""WAN-229 15m census의 얇은 배선을 동작으로 고정한다.

census 엔진(재무장 루프·판정·행 모델)은 WAN-228 소관이라 `test_wan228_reentry_census.py`가
이미 고정한다. 여기서는 이 스핀아웃이 더한 것 셋만 못 박는다:

1. **census 엔진 재사용** — WAN-229의 `CellRow`가 WAN-228의 바로 그 클래스다(CSV 교차검산).
2. **15m 기본 · 출력 분리** — TF 기본값이 15m이고 산출물이 4h·1h CSV를 덮지 않는 별도 경로다.
3. **WAN-229 요약 렌더** — 제목·15m 재현 명령·15m 표·판정(WAN-228 `verdict`/`_cell_table` 재사용).
"""

from __future__ import annotations

from pathlib import Path

from backtest import wan228_reentry_census as wan228
from backtest import wan229_reentry_census_15m as wan229
from backtest.wan228_reentry_census import CellRow

WINDOW = (1_600_128_000_000, 1_784_678_400_000)


def _cell(
    *,
    symbol: str = "BTC/USDT:USDT",
    adopted_entries: int = 200,
    reentries_total: int = 60,
    re_oos_net_pp_sum: float = 4.0,
) -> CellRow:
    return CellRow(
        symbol=symbol,
        timeframe="15m",
        window_start=WINDOW[0],
        window_end=WINDOW[1],
        window_days=2136.0,
        adopted_entries=adopted_entries,
        tp_entries=adopted_entries // 2,
        reentries_total=reentries_total,
        re_is_n=reentries_total // 2,
        re_is_wins=0,
        re_is_stops=0,
        re_is_gross_r_sum=0.0,
        re_is_net_pp_sum=0.0,
        re_oos_n=reentries_total // 2,
        re_oos_wins=6,
        re_oos_stops=4,
        re_oos_gross_r_sum=5.0,
        re_oos_net_pp_sum=re_oos_net_pp_sum,
        funding_coverage=1.0,
    )


def test_reuses_wan228_census_engine() -> None:
    # 같은 CellRow 클래스여야 CSV가 두 이슈 사이에서 교차검산된다.
    assert wan229.CellRow is wan228.CellRow
    assert wan229.run_report is wan228.run_report
    assert wan229.verdict is wan228.verdict


def test_defaults_are_15m_and_isolated_outputs() -> None:
    assert wan229.TIMEFRAME == "15m"
    # 4h·1h 원본을 덮지 않게 별도 파일이어야 한다.
    assert wan229.DEFAULT_CELLS_CSV != wan228.DEFAULT_CELLS_CSV
    assert wan229.DEFAULT_SUMMARY != wan228.DEFAULT_SUMMARY
    assert "wan229" in wan229.DEFAULT_CELLS_CSV.name
    assert "15m" in wan229.DEFAULT_CELLS_CSV.name


def test_summary_is_wan229_titled_15m() -> None:
    rows = [_cell(symbol="BTC/USDT:USDT"), _cell(symbol="ETH/USDT:USDT")]
    md = wan229.build_summary_markdown(rows, cells_csv=Path("x.csv"))
    assert md.startswith("# WAN-229")
    assert "### 15m" in md  # WAN-228 `_cell_table`가 찍는 TF 헤더
    assert "backtest.wan229_reentry_census_15m --jobs 9" in md
    # 두 자 판정(비율 30% + 순수익 유의) → GO 문장이 렌더된다.
    assert "(a) GO" in md


def test_funding_gap_symbol_flagged() -> None:
    # 신규 3종목은 표에서 †로 표시된다(펀딩 0행 · 순수익 낙관).
    rows = [_cell(symbol="DOGE/USDT:USDT")]
    md = wan229.build_summary_markdown(rows, cells_csv=Path("x.csv"))
    assert "DOGE†" in md


def test_from_csv_roundtrip(tmp_path: Path) -> None:
    rows = [_cell(symbol="BTC/USDT:USDT"), _cell(symbol="SOL/USDT:USDT", re_oos_net_pp_sum=-2.0)]
    path = tmp_path / "wan229_reentry_census_15m.csv"
    wan229.cells_to_frame(rows).to_csv(path, index=False)
    restored = wan229.cells_from_csv(path)
    assert {r.symbol for r in restored} == {"BTC/USDT:USDT", "SOL/USDT:USDT"}
    assert all(r.timeframe == "15m" for r in restored)
