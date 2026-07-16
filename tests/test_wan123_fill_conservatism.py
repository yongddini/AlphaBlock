"""backtest.wan123_fill_conservatism 테스트 (WAN-124 3단).

실데이터 격자 산출은 `backtest/reports/wan123_fill_conservatism_summary.md`가 낸다. 여기서
지키는 것은 **표를 읽을 수 있게 만드는 계약들**이다: 게이트 축이 진짜 게이트만 움직이는가,
부호 함정을 막는가, WAN-111 검산이 어긋남을 실제로 잡는가.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest.harness import LEGACY_RSI_GATE_MODE, RunRow, build_params
from backtest.wan123_fill_conservatism import (
    ADOPTED_ARM,
    GATE_ARMS,
    LENS_NAMES,
    OFFICIAL_LENS,
    GateRow,
    asymmetry_table,
    build_conclusion,
    build_grid,
    build_summary_markdown,
    check_against_wan111,
    gate_delta_table,
    per_symbol,
    rows_from_csv,
    rows_to_frame,
    symbol_mean,
)
from strategy.models import ConfluenceParams


def _row(
    *,
    gate: str = ADOPTED_ARM,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
    fill: str = OFFICIAL_LENS,
    seed: int = 0,
    total_return: float = 0.1,
    num_trades: int = 100,
    fill_rate: float | None = 0.8,
) -> GateRow:
    return GateRow(
        gate=gate,
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        window=0,
        entry_mode="zone_limit",
        take_profit_r=1.5,
        offset_bps=2.0,
        fill=fill,
        seed=seed,
        start_time=0,
        end_time=1,
        num_bars=1000,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.1,
        sharpe=1.0,
        profit_factor=1.2,
        mean_r=0.2,
        fill_rate=fill_rate,
        eligible_setups=200,
        num_filled=num_trades,
        funding_coverage=1.0,
    )


# --------------------------------------------------------- 게이트 축


def test_gate_arms_use_unconditional_not_none() -> None:
    """🚨 `none`은 게이트를 끄지 않는다 — 워밍업 필터만 남는다(WAN-123 §경고).

    이 팔에 `none`이 들어가면 「게이트를 뺀 엔진」이라 이름 붙인 채 실제로는 워밍업 구간
    탭이 막힌 다른 엔진을 재게 된다. 라벨이 아니라 값으로 고정한다.
    """
    assert GATE_ARMS[ADOPTED_ARM] == "unconditional"
    assert "none" not in GATE_ARMS.values()


def test_adopted_arm_is_the_current_default() -> None:
    """채택 팔이 진짜 지금 돌고 있는 엔진인지 — 기본값이 움직이면 이 표의 라벨이 거짓이 된다."""
    assert GATE_ARMS[ADOPTED_ARM] == ConfluenceParams().rsi_gate_mode


def test_legacy_arm_is_the_pre_wan123_default() -> None:
    assert GATE_ARMS["first_tap_free"] == LEGACY_RSI_GATE_MODE == "first_tap_free"


def test_grid_pins_the_gate_and_follows_adopted_defaults_elsewhere() -> None:
    """게이트만이 축이어야 이동을 게이트의 몫으로 읽을 수 있다."""
    defaults = build_params()
    grid = build_grid(["BTCUSDT"], "unconditional", ("1h",))

    assert grid.rsi_gate_mode == "unconditional"
    # 익절 R·오프셋을 상수로 박으면 기본값이 움직일 때 이 리포트만 옛 엔진을 돈다.
    assert grid.take_profit_rs == (defaults.take_profit_r,)
    assert grid.offsets_bps == (defaults.zone_limit_offset_bps,)
    assert grid.entry_modes == ("zone_limit",)
    assert tuple(f.name for f in grid.fills) == LENS_NAMES
    assert grid.symbols == ("BTC/USDT:USDT",)  # 축약형 정규화.


def test_gate_row_keeps_the_run_row_columns() -> None:
    """열이 `RunRow`와 같아야 WAN-111 CSV와 나란히 놓고 읽을 수 있다."""
    assert set(RunRow.model_fields) <= set(GateRow.model_fields)
    assert "gate" in GateRow.model_fields


# --------------------------------------------------------- 비대칭 · 부호 함정


def _asym_rows(
    *, base: float, pen: float, base_trades: int = 100, pen_trades: int = 96
) -> list[GateRow]:
    return [
        _row(fill=OFFICIAL_LENS, total_return=base, num_trades=base_trades),
        _row(fill="pen_5bp", total_return=pen, num_trades=pen_trades),
    ]


def test_asymmetry_reports_trade_and_return_retention() -> None:
    """WAN-96의 진단 — 거래는 96% 남는데 수익은 40%만 남으면 그 비대칭이 요점이다."""
    table = asymmetry_table(_asym_rows(base=0.10, pen=0.04))
    row = table.iloc[0]

    assert row["trade_retention"] == pytest.approx(0.96)
    assert row["return_retention"] == pytest.approx(0.4)


def test_return_retention_is_none_when_the_base_is_negative() -> None:
    """🚨 부호 함정 — 기준이 음수면 비율이 뜻을 잃는다(WAN-115가 1h 증분에서 겪었다).

    −10% → −20%는 **더 나빠진 것**인데 비율로는 200%가 나와 「유지」로 읽힌다. 그런 셀은
    비율을 내지 않는다.
    """
    table = asymmetry_table(_asym_rows(base=-0.10, pen=-0.20))
    row = table.iloc[0]

    assert row["return_retention"] is None
    assert row["trade_retention"] == pytest.approx(0.96)  # 거래 잔존은 여전히 뜻이 있다.
    assert "—" in build_summary_markdown(_asym_rows(base=-0.1, pen=-0.2), csv_path=Path("x"))


def test_return_retention_is_none_when_the_base_is_zero() -> None:
    table = asymmetry_table(_asym_rows(base=0.0, pen=0.05))
    assert table.iloc[0]["return_retention"] is None


# --------------------------------------------------------- 시드 접기


def test_seeds_are_folded_inside_the_symbol_before_averaging() -> None:
    """스트레스 렌즈는 시드 5개를 도는데 **심볼 안에서** 먼저 접어야 한다(WAN-111 규칙).

    순서를 바꾸면(시드별 심볼평균 → 시드평균) 심볼 축이 사라진다.
    """
    rows = [
        _row(fill="pen_5bp_drop_50", seed=s, total_return=r)
        for s, r in enumerate((0.0, 0.1, 0.2, 0.3, 0.4))
    ]
    view = per_symbol(rows)

    assert len(view) == 1  # 심볼 하나로 접힌다.
    assert view.iloc[0]["total_return"] == pytest.approx(0.2)  # 시드 5개의 평균.


def test_symbol_mean_counts_plus_symbols() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", total_return=0.1),
        _row(symbol="ETH/USDT:USDT", total_return=-0.2),
    ]
    view = symbol_mean(rows)

    assert int(view.iloc[0]["plus_symbols"]) == 1
    assert int(view.iloc[0]["symbols"]) == 2


# --------------------------------------------------------- 게이트 델타


def test_gate_delta_contrasts_the_two_arms() -> None:
    rows = [
        _row(gate="first_tap_free", total_return=0.08, num_trades=100, fill_rate=0.51),
        _row(gate=ADOPTED_ARM, total_return=0.12, num_trades=125, fill_rate=0.81),
    ]
    table = gate_delta_table(rows)
    row = table.iloc[0]

    assert row["return_delta"] == pytest.approx(0.04)
    assert row["trade_growth"] == pytest.approx(0.25)
    assert row["old_fill_rate"] == pytest.approx(0.51)
    assert row["new_fill_rate"] == pytest.approx(0.81)


def test_conclusion_reads_numbers_from_the_rows() -> None:
    """문장에 숫자를 박아 두면 재실행 때 표와 갈라진다 — 행에서 계산해야 한다."""
    rows = [
        _row(gate="first_tap_free", fill=OFFICIAL_LENS, total_return=0.08, num_trades=100),
        _row(gate="first_tap_free", fill="pen_5bp", total_return=0.04, num_trades=96),
        _row(gate=ADOPTED_ARM, fill=OFFICIAL_LENS, total_return=0.12, num_trades=125),
        _row(gate=ADOPTED_ARM, fill="pen_5bp", total_return=0.03, num_trades=120),
    ]
    conclusion = build_conclusion(rows)

    assert "1h OOS" in conclusion
    assert "25.00%" in conclusion  # 거래 증가율이 행에서 계산된다.


# --------------------------------------------------------- WAN-111 검산


def _wan111_csv(tmp_path: Path, rows: list[GateRow], **overrides: object) -> Path:
    frame = rows_to_frame(rows).drop(columns=["gate"])
    for column, value in overrides.items():
        frame[column] = value
    path = tmp_path / "wan111.csv"
    frame.to_csv(path, index=False)
    return path


def test_check_passes_when_the_legacy_arm_reproduces_wan111(tmp_path: Path) -> None:
    rows = [_row(gate="first_tap_free")]
    assert "검산 통과" in check_against_wan111(rows, _wan111_csv(tmp_path, rows))


def test_check_fails_loudly_when_the_legacy_arm_drifts(tmp_path: Path) -> None:
    """🚨 어긋나면 게이트 말고 다른 것이 함께 움직였다는 뜻이다.

    그걸 모른 채 델타를 「게이트 효과」로 읽는 것이 이 저장소가 반복해 겪은 사고다.
    """
    rows = [_row(gate="first_tap_free", total_return=0.10)]
    path = _wan111_csv(tmp_path, rows, total_return=0.99)

    report = check_against_wan111(rows, path)
    assert "검산 실패" in report
    assert "total_return" in report


def test_check_is_explicit_when_it_cannot_run(tmp_path: Path) -> None:
    """검산을 못 했으면 조용히 넘어가지 않고 그렇다고 적는다."""
    assert "검산 생략" in check_against_wan111([_row()], tmp_path / "missing.csv")
    adopted_only = [_row(gate=ADOPTED_ARM)]
    assert "검산 생략" in check_against_wan111(adopted_only, _wan111_csv(tmp_path, [_row()]))


def test_check_reports_when_no_coordinates_overlap(tmp_path: Path) -> None:
    rows = [_row(gate="first_tap_free", symbol="BTC/USDT:USDT")]
    other = [_row(gate="first_tap_free", symbol="DOGE/USDT:USDT")]
    assert "검산 불가" in check_against_wan111(rows, _wan111_csv(tmp_path, other))


# --------------------------------------------------------- 왕복 · 렌더


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    rows = [_row(), _row(gate="first_tap_free", fill="pen_5bp_drop_50", seed=3)]
    path = tmp_path / "fill.csv"
    rows_to_frame(rows).to_csv(path, index=False)

    assert rows_from_csv(path) == rows


def test_summary_keeps_the_queue_priority_warning() -> None:
    """체결률이 올랐다고 큐 걱정이 준 게 아니다 — 그 경고가 산출물에서 사라지면 안 된다."""
    summary = build_summary_markdown([_row()], csv_path=Path("x.csv"))

    assert "체결률이 올랐다고 큐 우선순위 걱정이 준 것이 아니다" in summary
    assert "wan95" in summary  # 창이 다른 수치와 섞어 인용하지 말라는 경고.
    assert "매칭 널은" in summary  # 엣지 판정은 이 표의 소관이 아니라는 경계.
