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
from backtest.portfolio import PortfolioParams
from backtest.run import (
    JOBS_AUTO,
    Combo,
    Grid,
    RunOptions,
    build_parser,
    grid_from_args,
    iter_combos,
    main,
    options_from_args,
    parse_date_ms,
    parse_jobs,
    resolve_jobs,
    run_grid,
    split_floats,
    split_ints,
    split_list,
)
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.zone_limit_backtest import run_zone_limit_portfolio_backtest
from data.models import Candle, FundingRate
from data.storage import OhlcvStore
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


def test_parse_jobs_reads_counts_and_auto() -> None:
    assert parse_jobs("1") == 1
    assert parse_jobs("6") == 6
    assert parse_jobs("auto") == JOBS_AUTO == 0
    assert parse_jobs("AUTO") == JOBS_AUTO


def test_parse_jobs_rejects_negative_and_non_numeric() -> None:
    """`--jobs -1`을 조용히 직렬로 접지 않는다 — 느린 이유를 모르게 되기 때문."""
    with pytest.raises(ValueError, match="--jobs"):
        parse_jobs("-1")
    with pytest.raises(ValueError, match="정수 또는 auto"):
        parse_jobs("many")


def test_resolve_jobs_auto_uses_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backtest.run.os.cpu_count", lambda: 8)
    assert resolve_jobs(JOBS_AUTO, task_count=100) == 8
    # cpu_count()가 None을 줄 수 있다(문서화된 반환값) — 그때도 직렬로 산다.
    monkeypatch.setattr("backtest.run.os.cpu_count", lambda: None)
    assert resolve_jobs(JOBS_AUTO, task_count=100) == 1


def test_resolve_jobs_never_exceeds_task_count() -> None:
    """작업보다 많은 워커는 프로세스만 띄우고 논다."""
    assert resolve_jobs(8, task_count=3) == 3
    assert resolve_jobs(1, task_count=100) == 1
    assert resolve_jobs(8, task_count=0) == 1  # 빈 격자도 죽지 않는다.


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
    # 재탭 정책도 채택 기본값을 물려받는다(WAN-138) — 하드코딩하면 기본값이 바뀔 때
    # CLI 기본 실행만 조용히 갈라진다.
    assert grid.retap_modes == (ConfluenceParams().retap_mode,) == ("every_tap",)
    assert grid.short_enabled is None  # 기본값을 덮어쓰지 않는다.
    assert len(iter_combos(grid)) == 1


def test_retap_mode_axis_expands_to_both_arms() -> None:
    """WAN-138: `--retap-mode every_tap,once`가 두 팔을 한 표에서 낸다."""
    grid = _grid_from(["--retap-mode", "every_tap,once"])
    assert grid.retap_modes == ("every_tap", "once")
    combos = iter_combos(grid)
    assert len(combos) == 2
    assert {c.retap_mode for c in combos} == {"every_tap", "once"}


def test_retap_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="재탭 정책"):
        _grid_from(["--retap-mode", "twice"])


def test_positions_axis_defaults_to_the_adopted_single_position_path() -> None:
    """WAN-130: `--positions`를 안 주면 축이 열리지 않는다(채택 기본값 = 동시 1포지션)."""
    grid = _grid_from([])
    assert grid.portfolio_leverages == (None,)
    (combo,) = iter_combos(grid)
    assert combo.portfolio_leverage is None
    assert combo.portfolio is None  # 포트폴리오 경로를 타지 않는다.


def test_positions_axis_expands_single_and_multi_arms() -> None:
    """WAN-130: `--positions single,3`이 단일·다중 두 팔을 한 표에 낸다."""
    grid = _grid_from(["--positions", "single,3"])
    assert grid.portfolio_leverages == (None, 3.0)
    combos = iter_combos(grid)
    assert [c.portfolio_leverage for c in combos] == [None, 3.0]
    multi = combos[1].portfolio
    assert multi is not None
    # WAN-103/108이 발표 수치를 낸 설정 그대로여야 비교 대상이 같다.
    assert (multi.leverage, multi.max_concurrent, multi.one_per_zone) == (3.0, None, True)


def test_positions_rejects_unknown_and_non_positive_values() -> None:
    with pytest.raises(ValueError, match="--positions"):
        _grid_from(["--positions", "multi"])
    with pytest.raises(ValueError, match="0보다 커야"):
        _grid_from(["--positions", "0"])


def test_close_entry_rejects_multi_position() -> None:
    """다중 포지션 회계는 B안 전용 — 종가 팔에 섞으면 라벨만 붙는다(WAN-95의 교훈)."""
    with pytest.raises(ValueError, match="--positions"):
        _grid_from(["--entry-mode", "close", "--positions", "3"])


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
    grid = _grid_from(["--symbol", "BTCUSDT,ETHUSDT", "--tf", "1h", "--tp-r", "1.5,2.0"])
    rows = run_grid(grid, RunOptions(), log=False)
    assert len(rows) == 2 * 2
    assert {r.symbol for r in rows} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    assert {r.take_profit_r for r in rows} == {1.5, 2.0}
    assert all(r.segment == "full" for r in rows)


def test_run_grid_routes_entry_modes_to_their_engines(synthetic_loader: None) -> None:
    """A안/B안이 각자 경로를 타고 한 표에 함께 나온다 — 체결률 축의 유무로 구분된다."""
    # 존폭 필터를 끈다(WAN-159 기본값 1.28) — 합성 존은 1.28×ATR보다 넓어 켜 두면 zone_limit
    # 팔이 0체결이 되어 체결률 축이 사라진다. 이 테스트가 보는 것은 경로 라우팅이지 필터가 아니다.
    grid = _grid_from(
        ["--symbol", "BTCUSDT", "--entry-mode", "close,zone_limit", "--max-zone-width-atr", "none"]
    )
    rows = run_grid(grid, RunOptions(), log=False)
    by_mode = {r.entry_mode: r for r in rows}
    assert set(by_mode) == {"close", "zone_limit"}
    assert by_mode["close"].fill_rate is None  # 종가는 미체결 개념이 없다.
    assert by_mode["zone_limit"].fill_rate is not None


def test_run_grid_multi_position_arm_routes_to_the_portfolio_engine(
    synthetic_loader: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WAN-130: 다중 팔은 **실제로** 포트폴리오 엔진(WAN-103)을 타고, 단일 팔은 안 탄다.

    라벨이 아니라 동작으로 고정한다 — `position_mode="multi"` 문자열만 붙고 실제로는
    단일 시퀀서가 돌면 "다중을 확인했다"는 표가 거짓이 된다(WAN-95/112/123 부류).
    """
    seen: list[PortfolioParams] = []
    original = run_zone_limit_portfolio_backtest

    def _spy(*args: object, **kwargs: object) -> object:
        portfolio = kwargs["portfolio"]
        assert isinstance(portfolio, PortfolioParams)
        seen.append(portfolio)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("backtest.harness.run_zone_limit_portfolio_backtest", _spy)

    baseline_rows = run_grid(
        _grid_from(["--symbol", "BTCUSDT", "--tf", "1h"]), RunOptions(), log=False
    )
    assert seen == []  # 단일 팔은 포트폴리오 경로를 건드리지 않는다.

    rows = run_grid(
        _grid_from(["--symbol", "BTCUSDT", "--tf", "1h", "--positions", "single,3"]),
        RunOptions(),
        log=False,
    )
    single, multi = rows
    assert (single.position_mode, single.portfolio_leverage) == ("single", None)
    assert (multi.position_mode, multi.portfolio_leverage) == ("multi", 3.0)
    assert [p.leverage for p in seen] == [3.0]  # 다중 팔에서만 한 번.
    # 단일 팔은 `--positions` 이전과 비트 단위로 같다.
    assert single.model_dump() == baseline_rows[0].model_dump()
    # 두 팔은 같은 셋업 풀에서 나온다 — 다른 건 배치 회계뿐이다.
    assert multi.eligible_setups == single.eligible_setups


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


def test_run_grid_warm_oos_adds_warm_row_and_keeps_cold_rows_bit_identical(
    synthetic_loader: None,
) -> None:
    """WAN-166: `--oos-warm`은 따뜻한 행(주 수치)을 **더할 뿐**이다.

    full/is/oos 행이 `--oos`와 비트 단위로 같아야 한다 — 특히 IS 비트 동일이 완료기준이고,
    차가운 OOS가 흔들리면 스트레스 병기의 대조 축이 사라진다. 따뜻한 행의 좌표
    (`start_time`·`num_bars`)는 차가운 OOS와 같은 평가 기간을 가리켜야 한다(전 구간을
    태웠다는 이유로 전 구간 좌표가 붙으면 "다른 기간을 쟀다"로 읽힌다).
    """
    grid = _grid_from(["--symbol", "BTCUSDT", "--tf", "1h"])
    cold_rows = run_grid(grid, RunOptions(oos=True), log=False)
    warm_rows = run_grid(grid, RunOptions(warm_oos=True), log=False)
    assert [r.segment for r in warm_rows] == ["full", "is", "oos_warm", "oos"]
    by = {r.segment: r for r in warm_rows}
    cold_by = {r.segment: r for r in cold_rows}
    for segment in ("full", "is", "oos"):
        assert by[segment].model_dump() == cold_by[segment].model_dump()
    assert by["oos_warm"].start_time == by["oos"].start_time
    assert by["oos_warm"].end_time == by["oos"].end_time
    assert by["oos_warm"].num_bars == by["oos"].num_bars


def test_run_grid_warm_oos_rejects_close_entry_and_multi_positions(
    synthetic_loader: None,
) -> None:
    """따뜻한 연속 OOS는 B안 단일 포지션 전용 — 격자를 반쯤 돌린 뒤가 아니라 시작 전에
    거부한다(WAN-95 부류의 조용한 무시 방지)."""
    with pytest.raises(ValueError, match="oos-warm"):
        run_grid(
            _grid_from(["--symbol", "BTCUSDT", "--entry-mode", "close"]),
            RunOptions(warm_oos=True),
            log=False,
        )
    with pytest.raises(ValueError, match="oos-warm"):
        run_grid(
            _grid_from(["--symbol", "BTCUSDT", "--positions", "3"]),
            RunOptions(warm_oos=True),
            log=False,
        )


def test_run_grid_walkforward_produces_rolling_windows(synthetic_loader: None) -> None:
    grid = _grid_from(["--symbol", "BTCUSDT", "--tf", "1h"])
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


# ---------------------------------------------------- 병렬 실행 (WAN-121)


_PARALLEL_SYMBOLS: tuple[tuple[str, str, int], ...] = (
    ("BTCUSDT", "BTC/USDT:USDT", 7),
    ("ETHUSDT", "ETH/USDT:USDT", 17),
)
"""(CLI 축약형, 저장소 표기, 시드). 시드를 심볼마다 달리해 두 셀이 **실제로 다른 숫자**를
내게 한다 — 같은 데이터면 행이 뒤바뀌어도 리스트 비교가 통과해 순서 보장을 못 잰다."""

_PARALLEL_BARS = 600
"""상위TF 봉 수. 시드 7이 이 길이에서 종가 진입 거래를 실제로 낸다(0행끼리 비교하는
테스트를 피하려고 고른 값)."""


def _candles(frame: pd.DataFrame, symbol: str, timeframe: str) -> list[Candle]:
    return [
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=int(row.open_time),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            closed=bool(row.closed),
        )
        for row in frame.itertuples()
    ]


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    """합성 봉을 **실제 sqlite**에 넣고 (db_path, cache_dir)를 준다.

    다른 테스트처럼 `monkeypatch`로 로더를 갈아끼울 수 없다 — 워커는 별도 프로세스라
    부모의 몽키패치를 보지 못하고(spawn이면 모듈을 새로 임포트한다), 패치된 로더를
    믿고 짠 테스트는 병렬 경로에서 조용히 실 저장소를 읽는다. 병렬-직렬 동일성을 진짜로
    재려면 워커가 자기 힘으로 읽을 수 있는 데이터가 있어야 하므로 여기서만 DB를 만든다.
    """
    root = tmp_path_factory.mktemp("wan121")
    db_path, cache_dir = root / "ohlcv.db", root / "cache"
    minutes = _PARALLEL_BARS * (timeframe_to_ms(_TIMEFRAME) // 60_000)
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        for _, symbol, seed in _PARALLEL_SYMBOLS:
            htf = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=_PARALLEL_BARS, seed=seed)
            one_min = make_synthetic_ohlcv(
                timeframe="1m",
                bars=minutes,
                seed=seed + 4,
                start_time_ms=int(htf["open_time"].iloc[0]),
                swing_period=180,
            )
            store.upsert_candles(_candles(htf, symbol, _TIMEFRAME))
            store.upsert_candles(_candles(one_min, symbol, "1m"))
    return str(db_path), str(cache_dir)


_PARALLEL_ARGV: tuple[str, ...] = (
    "--symbol",
    "BTCUSDT,ETHUSDT",
    "--entry-mode",
    "close,zone_limit",
    "--tp-r",
    "1.5,2.0",
)
"""대조 격자: 2심볼 × 2진입방식 × 2익절R = 8행, fan-out 단위((심볼,TF))는 2개.

진입 방식을 **둘 다** 넣는 이유는 A안 팔이 이 합성 데이터에서 실제 거래를 내기
때문이다 — 지정가(B안) 팔은 볼린저 기본 필터가 합성 데이터의 후보를 모두 걸러
0거래가 되고(`tests/test_zone_limit_backtest.py`가 같은 성질을 기록한다), 0행끼리
비교하면 병렬이 숫자를 바꿔도 통과한다. 손익이 실제로 흐르는 팔을 한쪽에 둔다."""


def _parallel_options(synthetic_db: tuple[str, str]) -> RunOptions:
    db_path, cache_dir = synthetic_db
    return RunOptions(funding=False, db_path=db_path, cache_dir=cache_dir)


def test_run_grid_is_serial_by_default() -> None:
    """기본값은 직렬 — 병렬은 옵트인이다(회귀 보존)."""
    assert build_parser().parse_args([]).jobs == "1"
    assert parse_jobs(build_parser().parse_args([]).jobs) == 1


def test_run_grid_jobs_produces_identical_rows_to_serial(synthetic_db: tuple[str, str]) -> None:
    """완료기준: `N>1`의 결과 행이 직렬과 **순서까지** 완전히 동일하다.

    병렬화는 일을 나눠 맡길 뿐 계산을 바꾸지 않으므로, `--jobs`가 숫자를 움직이면
    그건 최적화가 아니라 버그다.
    """
    grid = _grid_from(list(_PARALLEL_ARGV))
    options = _parallel_options(synthetic_db)
    serial = run_grid(grid, options, log=False, jobs=1)
    parallel = run_grid(grid, options, log=False, jobs=2)

    # 0행·0거래끼리 같은 건 아무것도 증명하지 않는다 — 손익이 실제로 흘렀는지 먼저 본다.
    assert len(serial) == 2 * 2 * 2
    assert any(row.num_trades > 0 and row.total_return != 0.0 for row in serial), (
        "합성 데이터가 거래를 내지 않았다 — 이 대조는 0끼리 비교하는 중이다"
    )
    assert [r.model_dump() for r in parallel] == [r.model_dump() for r in serial]


def test_run_grid_jobs_auto_matches_serial(synthetic_db: tuple[str, str]) -> None:
    """`--jobs auto`도 같은 결과 — 코어 수가 결과를 바꾸면 안 된다."""
    grid = _grid_from(list(_PARALLEL_ARGV))
    options = _parallel_options(synthetic_db)
    serial = run_grid(grid, options, log=False, jobs=1)
    auto = run_grid(grid, options, log=False, jobs=JOBS_AUTO)
    assert [r.model_dump() for r in auto] == [r.model_dump() for r in serial]


def test_main_jobs_keeps_stdout_and_stderr_identical(
    synthetic_db: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """완료기준: stdout(CSV)·stderr(진행 로그) 모두 직렬 실행과 비트 단위 동일.

    stderr까지 보는 이유: 워커가 stderr에 직접 쓰면 줄이 섞여 순서가 실행마다 달라진다.
    `_CellOutcome`이 로그를 값으로 들고 나와 부모가 제출 순서로 찍는 설계가 지켜지는지를
    여기서 잡는다.
    """
    db_path, cache_dir = synthetic_db
    argv = [
        *_PARALLEL_ARGV,
        "--format",
        "csv",
        "--no-funding",
        "--db-path",
        db_path,
        "--cache-dir",
        cache_dir,
    ]
    assert main([*argv, "--jobs", "1"]) == 0
    serial = capsys.readouterr()
    assert main([*argv, "--jobs", "2"]) == 0
    parallel = capsys.readouterr()

    assert parallel.out == serial.out
    assert parallel.err == serial.err
    assert "[run]" in serial.err


def test_main_rejects_bad_jobs_value(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--jobs", "-1"]) == 2
    assert "--jobs" in capsys.readouterr().err


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
    argv = [
        "--symbol",
        "BTCUSDT",
        "--tf",
        "1h",
        "--tp-r",
        "1.5,2.0",
        "--format",
        "csv",
        "--out",
        str(out),
    ]
    assert main([*argv, "--quiet"]) == 0
    frame = pd.read_csv(out)
    assert len(frame) == 2
    assert set(frame["take_profit_r"]) == {1.5, 2.0}


def test_main_writes_json_to_out_path(synthetic_loader: None, tmp_path: Path) -> None:
    """완료기준: JSON 출력."""
    out = tmp_path / "sweep.json"
    assert (
        main(
            ["--symbol", "BTCUSDT", "--tf", "1h", "--format", "json", "--out", str(out), "--quiet"]
        )
        == 0
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["entry_mode"] == "zone_limit"


def test_main_keeps_stdout_clean_for_piping(
    synthetic_loader: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """진행 로그는 stderr로 — stdout이 오염되면 `--format csv | ...` 파이프가 깨진다."""
    assert main(["--symbol", "BTCUSDT", "--tf", "1h", "--format", "csv"]) == 0
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


def test_main_rejects_warm_oos_with_walkforward_and_wrong_paths(
    synthetic_loader: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """WAN-166: `--oos-warm`의 금지 조합이 트레이스백 없이 종료 코드 2로 끝난다."""
    assert main(["--oos-warm", "--walkforward", "3"]) == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err
    assert main(["--symbol", "BTCUSDT", "--oos-warm", "--entry-mode", "close"]) == 2
    assert "oos-warm" in capsys.readouterr().err
    assert main(["--symbol", "BTCUSDT", "--oos-warm", "--positions", "3"]) == 2
    assert "oos-warm" in capsys.readouterr().err


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
