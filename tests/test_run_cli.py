"""backtest.run (범용 백테스트 CLI) 단위 테스트 (WAN-101).

실데이터 스윕은 `data/ohlcv.db`가 있어야 하므로, 여기서는 합성 데이터를 로더 자리에
끼워 넣어 **격자 전개 · 경로 스위치 · 구간 분할 · 출력 형식 · 인자 검증**을 검증한다.
CLI가 채택 기본값과 같은 엔진을 타는지(회귀 검증)는 `tests/test_harness.py`가 함께
고정한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import MarketData, RunRow
from backtest.run import (
    Combo,
    Grid,
    RunOptions,
    build_parser,
    grid_from_args,
    iter_combos,
    main,
    options_from_args,
    parse_date_ms,
    run_grid,
    split_floats,
    split_ints,
    split_list,
)
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from data.models import FundingRate
from strategy.models import ConfluenceParams

_TIMEFRAME = "1h"


def _market(symbol: str, timeframe: str, *, bars: int = 200) -> MarketData:
    htf = make_synthetic_ohlcv(timeframe=timeframe, bars=bars, seed=7)
    htf_ms = timeframe_to_ms(timeframe)
    start = int(htf["open_time"].iloc[0])
    minutes = bars * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    interval = 8 * 60 * 60_000
    rates = [
        FundingRate(symbol=symbol, funding_time=t, rate=0.0001)
        for t in range(start, int(htf["open_time"].iloc[-1]), interval)
    ]
    return MarketData(symbol, timeframe, htf, one_min, rates)


@pytest.fixture
def synthetic_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """저장소 로더를 합성 데이터로 갈아끼운다(실 DB 없이 CLI 전체 경로를 돈다)."""

    def _load(symbol: str, timeframe: str, **kwargs: object) -> MarketData:
        market = _market(symbol, timeframe)
        if not kwargs.get("need_1m", True):
            return MarketData(
                symbol, timeframe, market.htf_df, pd.DataFrame(), market.funding_rates
            )
        return market

    monkeypatch.setattr("backtest.run.load_market_data", _load)


# ---------------------------------------------------- 인자 파싱


def test_split_list_trims_and_drops_blanks() -> None:
    assert split_list("a, b ,c") == ("a", "b", "c")
    assert split_list("a,,b,") == ("a", "b")


def test_split_floats_dedupes_and_keeps_order() -> None:
    """격자 축의 중복은 접는다 — 같은 조합을 두 번 돌 이유가 없다."""
    assert split_floats("1.0,1.5,2.0", label="--tp-r") == (1.0, 1.5, 2.0)
    assert split_floats("1.5,1.5,1.0", label="--tp-r") == (1.5, 1.0)


def test_split_floats_rejects_non_numeric() -> None:
    with pytest.raises(ValueError, match="--tp-r"):
        split_floats("1.0,many", label="--tp-r")
    with pytest.raises(ValueError, match="비어"):
        split_floats(" , ", label="--tp-r")


def test_split_ints_parses_seed_lists() -> None:
    assert split_ints("0,1,2", label="--seeds") == (0, 1, 2)
    with pytest.raises(ValueError, match="--seeds"):
        split_ints("0,x", label="--seeds")


def test_parse_date_ms_reads_utc_dates() -> None:
    assert parse_date_ms("1970-01-01") == 0
    assert parse_date_ms("2024-01-01") == 1_704_067_200_000
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_date_ms("2024/01/01")


# ---------------------------------------------------- 격자 전개


def _grid_from(argv: list[str]) -> Grid:
    return grid_from_args(build_parser().parse_args(argv))


def test_cli_defaults_produce_the_adopted_engine() -> None:
    """인자 없는 실행 = 채택 기본값(지정가 + 롱 온리 + 1.5R) 격자 1칸."""
    grid = _grid_from([])
    assert grid.entry_modes == ("zone_limit",)
    assert grid.take_profit_rs == (ConfluenceParams().take_profit_r,)
    # 채택 기본값을 그대로 물려받아야 한다 — 여기에 0.0을 하드코딩하면 CLI 기본 실행만
    # 혼자 옛 엔진을 돌게 된다(WAN-112).
    assert grid.offsets_bps == (ConfluenceParams().zone_limit_offset_bps,) == (2.0,)
    assert tuple(f.name for f in grid.fills) == ("baseline",)
    assert grid.short_enabled is None  # 기본값을 덮어쓰지 않는다.
    assert len(iter_combos(grid)) == 1


def test_comma_values_expand_to_cartesian_product() -> None:
    """완료기준: `--tp-r 1.0,1.5,2.0,3.0 --tf 15m,1h` 형태가 조합별 1행을 낸다."""
    grid = _grid_from(["--tf", "15m,1h", "--tp-r", "1.0,1.5,2.0,3.0", "--offset-bps", "0,5"])
    assert grid.timeframes == ("15m", "1h")
    combos = iter_combos(grid)
    assert len(combos) == 4 * 2  # tp_r × offset (TF·심볼은 격자 바깥 루프)
    assert len({(c.take_profit_r, c.offset_bps) for c in combos}) == 8


def test_symbols_are_normalized_from_shorthand() -> None:
    """완료기준의 `--symbol BTCUSDT,ETHUSDT` 형태가 저장소 표기로 정규화된다."""
    grid = _grid_from(["--symbol", "BTCUSDT,ETHUSDT"])
    assert grid.symbols == ("BTC/USDT:USDT", "ETH/USDT:USDT")


def test_fill_axis_expands_presets_with_their_seeds() -> None:
    """탈락이 있는 가정은 시드마다 한 번씩 돈다(단일 시드의 운 배제, WAN-96)."""
    grid = _grid_from(["--fill", "baseline,pen_5bp,pen_5bp_drop_50"])
    combos = iter_combos(grid)
    by_fill: dict[str, list[Combo]] = {}
    for combo in combos:
        by_fill.setdefault(combo.fill.name, []).append(combo)
    assert len(by_fill["baseline"]) == 1
    assert len(by_fill["pen_5bp"]) == 1  # 탈락 없음 → 시드 1개.
    assert len(by_fill["pen_5bp_drop_50"]) == 5  # 탈락 있음 → 프리셋 시드 5개.


def test_seeds_flag_overrides_preset_seeds() -> None:
    grid = _grid_from(["--fill", "pen_5bp_drop_50", "--seeds", "7,8"])
    assert sorted(c.seed for c in iter_combos(grid)) == [7, 8]


def test_custom_fill_flags_build_an_inline_preset() -> None:
    grid = _grid_from(["--fill-penetration-bps", "3", "--fill-dropout-rate", "0.25"])
    assert tuple(f.name for f in grid.fills) == ("custom",)
    assert grid.fills[0].penetration_bps == 3.0
    assert grid.fills[0].dropout_rate == 0.25


def test_custom_fill_flags_conflict_with_fill_preset() -> None:
    with pytest.raises(ValueError, match="함께 쓸 수 없습니다"):
        _grid_from(["--fill", "pen_5bp", "--fill-penetration-bps", "3"])


def test_long_only_and_short_enabled_map_to_the_side_gate() -> None:
    assert _grid_from(["--long-only"]).short_enabled is False
    assert _grid_from(["--short-enabled"]).short_enabled is True


def test_close_entry_rejects_zone_limit_only_knobs() -> None:
    """종가 진입에 지정가 노브를 섞으면 조용히 무시하지 않고 거부한다(WAN-95의 교훈)."""
    with pytest.raises(ValueError, match="--offset-bps"):
        _grid_from(["--entry-mode", "close", "--offset-bps", "5"])
    with pytest.raises(ValueError, match="--fill"):
        _grid_from(["--entry-mode", "close", "--fill", "pen_5bp"])


def test_unknown_entry_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="진입 방식"):
        _grid_from(["--entry-mode", "market"])


def test_grid_needs_1m_only_for_zone_limit() -> None:
    """종가 전용 실행은 1분봉을 읽지 않는다 — 수백 MB를 공연히 읽지 않기 위해."""
    assert _grid_from([]).needs_1m is True
    assert _grid_from(["--entry-mode", "close"]).needs_1m is False
    assert _grid_from(["--entry-mode", "close,zone_limit"]).mixes_entry_modes is True


# ---------------------------------------------------- 실행 배선


def test_run_grid_returns_one_row_per_combination(synthetic_loader: None) -> None:
    """격자의 모든 조합이 빠짐없이 1행씩 나온다."""
    grid = _grid_from(["--symbol", "BTCUSDT,ETHUSDT", "--tp-r", "1.5,2.0"])
    rows = run_grid(grid, RunOptions(), log=False)
    assert len(rows) == 2 * 2
    assert {r.symbol for r in rows} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    assert {r.take_profit_r for r in rows} == {1.5, 2.0}
    assert all(r.segment == "full" for r in rows)


def test_run_grid_routes_entry_modes_to_their_engines(synthetic_loader: None) -> None:
    """A안/B안이 각자 경로를 타고 한 표에 함께 나온다 — 체결률 축의 유무로 구분된다."""
    grid = _grid_from(["--symbol", "BTCUSDT", "--entry-mode", "close,zone_limit"])
    rows = run_grid(grid, RunOptions(), log=False)
    by_mode = {r.entry_mode: r for r in rows}
    assert set(by_mode) == {"close", "zone_limit"}
    assert by_mode["close"].fill_rate is None  # 종가는 미체결 개념이 없다.
    assert by_mode["zone_limit"].fill_rate is not None


def test_run_grid_oos_split_produces_full_is_oos_segments(synthetic_loader: None) -> None:
    """완료기준: OOS 분할이 구간별 행을 낸다."""
    grid = _grid_from(["--symbol", "BTCUSDT"])
    rows = run_grid(grid, RunOptions(oos=True), log=False)
    assert {r.segment for r in rows} == {"full", "is", "oos"}
    segments = {r.segment: r for r in rows}
    assert segments["is"].end_time is not None and segments["oos"].start_time is not None
    assert segments["is"].end_time < segments["oos"].start_time
    # 각 구간은 독립 백테스트라 전 구간보다 봉이 적다.
    assert segments["is"].num_bars < segments["full"].num_bars


def test_run_grid_walkforward_produces_rolling_windows(synthetic_loader: None) -> None:
    grid = _grid_from(["--symbol", "BTCUSDT"])
    rows = run_grid(grid, RunOptions(walkforward=2), log=False)
    assert {r.window for r in rows} == {0, 1}
    assert {r.segment for r in rows} == {"is", "oos"}
    assert len(rows) == 4


def test_run_grid_skips_symbols_without_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """심볼 하나가 없다고 격자 전체가 죽으면 안 된다."""

    def _load(symbol: str, timeframe: str, **kwargs: object) -> MarketData:
        if symbol.startswith("ETH"):
            return MarketData(symbol, timeframe, pd.DataFrame(), pd.DataFrame(), [])
        return _market(symbol, timeframe)

    monkeypatch.setattr("backtest.run.load_market_data", _load)
    rows = run_grid(_grid_from(["--symbol", "BTCUSDT,ETHUSDT"]), RunOptions(), log=False)
    assert {r.symbol for r in rows} == {"BTC/USDT:USDT"}


def test_run_grid_skips_zone_limit_when_1m_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """1분봉이 없으면 지정가는 평가 불가 — 조용히 종가로 되돌리지 않고 건너뛴다."""

    def _load(symbol: str, timeframe: str, **kwargs: object) -> MarketData:
        market = _market(symbol, timeframe)
        return MarketData(symbol, timeframe, market.htf_df, pd.DataFrame(), market.funding_rates)

    monkeypatch.setattr("backtest.run.load_market_data", _load)
    assert run_grid(_grid_from(["--symbol", "BTCUSDT"]), RunOptions(), log=False) == []


def test_options_from_args_maps_costs_and_window() -> None:
    args = build_parser().parse_args(
        ["--years", "2", "--fee", "0.0005", "--no-funding", "--start", "2024-01-01"]
    )
    options = options_from_args(args)
    assert options.years == 2.0
    assert options.fee_rate == 0.0005
    assert options.funding is False
    assert options.start_ms == parse_date_ms("2024-01-01")


def test_run_grid_respects_no_funding(synthetic_loader: None) -> None:
    """`--no-funding`이면 펀딩 커버리지 축이 사라진다(= 펀딩을 안 썼음이 드러난다)."""
    rows = run_grid(_grid_from(["--symbol", "BTCUSDT"]), RunOptions(funding=False), log=False)
    assert all(r.funding_coverage is None for r in rows)


# ---------------------------------------------------- main / 출력


def test_main_prints_table_by_default(
    synthetic_loader: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--symbol", "BTCUSDT", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "return%" in out and "sharpe" in out


def test_main_writes_csv_to_out_path(synthetic_loader: None, tmp_path: Path) -> None:
    """완료기준: CSV 출력."""
    out = tmp_path / "sweep.csv"
    argv = ["--symbol", "BTCUSDT", "--tp-r", "1.5,2.0", "--format", "csv", "--out", str(out)]
    assert main([*argv, "--quiet"]) == 0
    frame = pd.read_csv(out)
    assert len(frame) == 2
    assert set(frame["take_profit_r"]) == {1.5, 2.0}


def test_main_writes_json_to_out_path(synthetic_loader: None, tmp_path: Path) -> None:
    """완료기준: JSON 출력."""
    out = tmp_path / "sweep.json"
    assert main(["--symbol", "BTCUSDT", "--format", "json", "--out", str(out), "--quiet"]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["entry_mode"] == "zone_limit"


def test_main_keeps_stdout_clean_for_piping(
    synthetic_loader: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """진행 로그는 stderr로 — stdout이 오염되면 `--format csv | ...` 파이프가 깨진다."""
    assert main(["--symbol", "BTCUSDT", "--format", "csv"]) == 0
    captured = capsys.readouterr()
    frame = pd.read_csv(pd.io.common.StringIO(captured.out))
    assert len(frame) == 1
    assert "[run]" in captured.err


def test_main_reports_bad_arguments_as_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    """잘못된 조합은 트레이스백이 아니라 사람이 읽을 오류로 끝난다."""
    assert main(["--entry-mode", "close", "--offset-bps", "5"]) == 2
    assert "오류" in capsys.readouterr().err


def test_main_rejects_oos_with_walkforward(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--oos", "--walkforward", "3"]) == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err


def test_run_row_columns_cover_issue_required_metrics() -> None:
    """이슈가 요구한 성과 열이 행 스키마에 모두 있다."""
    fields = set(RunRow.model_fields)
    required = {
        "total_return",
        "win_rate",
        "max_drawdown",
        "num_trades",
        "fill_rate",
        "mean_r",
        "sharpe",
    }
    assert required <= fields
