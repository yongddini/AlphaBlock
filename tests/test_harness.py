"""backtest.harness 단위 테스트 (WAN-101).

하네스는 CLI(`backtest.run`)와 리포트 스크립트가 공유하는 골격이다. 여기서는 파라미터
조립·경로 스위치·구간 분할·렌더를 검증하고, **하네스가 기존 리포트(WAN-95/96/99)와
같은 엔진 정의를 쓰는지**를 고정한다 — 그 고정이 깨지면 CLI가 낸 숫자를 기존 리포트와
나란히 놓고 읽을 수 없다.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from backtest.harness import (
    BASELINE_FILL,
    FILL_PRESETS,
    IS_FRACTION,
    LEGACY_BAND_BAR,
    LEGACY_RSI_GATE_MODE,
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    FillPreset,
    MarketData,
    RunRow,
    Segment,
    build_config,
    build_params,
    build_row,
    detect_order_blocks,
    fill_preset,
    iter_seeds,
    mean_r,
    normalize_symbol,
    pin_band_bar,
    render,
    render_csv,
    render_json,
    render_table,
    run_once,
    segments_for,
    slice_market,
)
from backtest.metrics import build_metrics
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from data.models import FundingRate
from strategy.models import ConfluenceParams

_SYMBOL = "TEST/USDT:USDT"
_TIMEFRAME = "1h"


def _market(bars: int = 200, span: int = 200) -> MarketData:
    """상위TF 전 구간을 1분봉이 덮는 합성 시장 데이터."""
    htf = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=bars, seed=7)
    htf_ms = timeframe_to_ms(_TIMEFRAME)
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    interval = 8 * 60 * 60_000
    rates = [
        FundingRate(symbol=_SYMBOL, funding_time=t, rate=0.0001)
        for t in range(int(htf["open_time"].iloc[0]), int(htf["open_time"].iloc[-1]), interval)
    ]
    return MarketData(_SYMBOL, _TIMEFRAME, htf, one_min, rates)


# ---------------------------------------------------- 심볼 정규화


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("BTCUSDT", "BTC/USDT:USDT"),
        ("btcusdt", "BTC/USDT:USDT"),
        ("ETHUSDC", "ETH/USDC:USDC"),
        ("BTC/USDT:USDT", "BTC/USDT:USDT"),
        ("BTC/USDT", "BTC/USDT:USDT"),
        ("  SOLUSDT  ", "SOL/USDT:USDT"),
    ],
)
def test_normalize_symbol_accepts_shorthand_and_canonical(given: str, expected: str) -> None:
    """축약형이든 정식이든 저장소 표기 하나로 모인다 — 완료기준의 `--symbol BTCUSDT` 형태."""
    assert normalize_symbol(given) == expected


def test_normalize_symbol_rejects_unknown_notation() -> None:
    """모르는 표기를 조용히 넘기면 사용자는 '데이터 없음'과 '오타'를 구분할 수 없다."""
    with pytest.raises(ValueError, match="심볼 표기"):
        normalize_symbol("BTCXYZ")
    with pytest.raises(ValueError):
        normalize_symbol("")


# ---------------------------------------------------- 파라미터 조립 (회귀 고정)


def test_default_params_are_the_adopted_defaults_untouched() -> None:
    """인자 없는 `build_params()`는 채택 기본값 그 자체다(완료기준: 회귀 검증).

    CLI가 자기만의 기본값을 갖는 순간 기존 리포트와 다른 엔진을 돌리게 되고, 두 숫자를
    비교하는 모든 논의가 무의미해진다.
    """
    assert build_params() == ConfluenceParams()


def test_default_params_match_wan95_zone_limit_baseline() -> None:
    """WAN-95 리포트의 채택 기준선 파라미터와 동일해야 한다."""
    from backtest.wan95_zone_limit_report import ZONE_LIMIT_PARAMS

    assert build_params() == ZONE_LIMIT_PARAMS


def test_default_offset_is_two_bps_not_the_fraction() -> None:
    """채택 기본 오프셋 = **2bp**(WAN-112). 단위는 bp지 분수가 아니다.

    WAN-112 이슈 본문이 "0.0 → 0.0002"로 적었는데 그건 분수 표기다. 그 값을 그대로 넣으면
    0.0002bp = 진입가를 2e-8만큼 미는 **사실상 무효과**라, 결정은 반영했다고 믿으면서
    실제로는 0bp를 돌리게 된다 — 그 조용한 실패를 이 테스트가 막는다.
    """
    assert build_params().zone_limit_offset_bps == 2.0
    price = 100.0
    shifted = ConfluenceParams().apply_zone_limit_offset(price, is_long=True)
    assert shifted == pytest.approx(100.02), "2bp = 0.02% — 롱은 위로 민다"


def test_explicit_zero_offset_matches_wan99_zero_offset_baseline() -> None:
    """`offset_bps=0.0`을 **명시**하면 WAN-99의 오프셋 0 × baseline 셀과 동일하다.

    WAN-112 전에는 `build_params()` 기본이 이 셀이었다. 이제 기본은 2bp이므로 그 등식은
    깨졌고, 대신 **명시적 0bp가 옛 셀을 계속 재현**한다 — 0bp로 고정한 과거 리포트
    (WAN-88/96/103/110)가 재현되는 근거가 이 등식이다.

    ⚠️ WAN-123/132부터 되돌릴 것이 **셋**이다: 오프셋(0bp) + RSI 게이트(`first_tap_free`)
    + 밴드 표본(`tap`). 옛 셀을 재현하려면 그 리포트가 고정한 엔진을 통째로 요청해야 한다.
    """
    from backtest.wan99_zone_limit_offset_report import FILL_ASSUMPTIONS

    assert build_params(
        offset_bps=0.0,
        base=pin_band_bar(
            ConfluenceParams(rsi_gate_mode=LEGACY_RSI_GATE_MODE, max_zone_width_atr=None)
        ),
    ) == FILL_ASSUMPTIONS[0].params(offset_bps=0.0, seed=0)


def test_default_gate_is_off_and_legacy_pin_differs() -> None:
    """채택 기본값 = 게이트 없음(WAN-123), 그리고 `LEGACY_RSI_GATE_MODE`는 그것과 **다르다**.

    두 번째 단언이 핵심이다: 핀이 기본값과 같아지는 순간 옛 리포트들의 "명시 고정"이
    전부 무의미한 no-op이 되면서, 그 사실이 아무 데서도 드러나지 않는다.
    """
    assert build_params().rsi_gate_mode == "unconditional"
    assert LEGACY_RSI_GATE_MODE == "first_tap_free" != build_params().rsi_gate_mode


def test_default_band_is_intrabar_live_and_legacy_pin_differs() -> None:
    """채택 기본값 = 봉내 라이브 밴드(WAN-132), 그리고 `LEGACY_BAND_BAR`는 그것과 **다르다**.

    게이트 핀과 같은 이유의 단언이다 — 핀이 기본값과 같아지면 wan84/88/96/99/104/110/111/
    114/117/126/133/134/137의 "명시 고정"이 한꺼번에 no-op이 되는데, 그 사실이 아무 데서도
    드러나지 않는다.
    """
    band = build_params().deviation_filter
    assert band is not None and band.band_bar == "intrabar_live"
    # 핀과 기본값을 `str`로 비교한다 — 리터럴끼리 비교하면 타입 검사기가 "겹치지 않는
    # 비교"라며 거부하는데, 여기서 재려는 것은 **런타임 값이 갈라져 있다**는 사실이다.
    assert str(LEGACY_BAND_BAR) == "tap"
    assert str(LEGACY_BAND_BAR) != str(band.band_bar)


def test_legacy_band_pin_flows_through_base() -> None:
    """`base=`로 준 밴드 핀이 조립을 통과해 살아남는다 — 옛 리포트 고정의 배선 검사."""
    pinned = build_params(base=pin_band_bar(ConfluenceParams(max_zone_width_atr=None)))
    assert pinned.deviation_filter is not None
    assert pinned.deviation_filter.band_bar == LEGACY_BAND_BAR
    with_axes = build_params(
        offset_bps=0.0,
        take_profit_r=2.0,
        base=pin_band_bar(ConfluenceParams(max_zone_width_atr=None)),
    )
    assert with_axes.deviation_filter is not None
    assert with_axes.deviation_filter.band_bar == LEGACY_BAND_BAR
    assert with_axes.zone_limit_offset_bps == 0.0
    assert with_axes.take_profit_r == 2.0


def test_legacy_gate_pin_flows_through_base() -> None:
    """`base=`로 준 게이트가 조립을 통과해 살아남는다 — 옛 리포트 고정의 배선 검사.

    `build_params`가 `update` 딕셔너리로 필드를 덮어쓰므로, 거기에 `rsi_gate_mode`가
    끼어들면 base의 핀이 조용히 지워진다. 그러면 "고정했다"고 적힌 리포트가 새 게이트로
    돈다 — WAN-95(라벨과 실행이 갈라짐)의 재발이다.
    """
    pinned = build_params(
        base=ConfluenceParams(rsi_gate_mode=LEGACY_RSI_GATE_MODE, max_zone_width_atr=None)
    )
    assert pinned.rsi_gate_mode == LEGACY_RSI_GATE_MODE
    # 다른 축을 같이 줘도 핀이 살아남아야 한다.
    with_axes = build_params(
        offset_bps=0.0,
        take_profit_r=2.0,
        fill=fill_preset("pen_5bp"),
        base=ConfluenceParams(rsi_gate_mode=LEGACY_RSI_GATE_MODE, max_zone_width_atr=None),
    )
    assert with_axes.rsi_gate_mode == LEGACY_RSI_GATE_MODE
    assert with_axes.zone_limit_offset_bps == 0.0
    assert with_axes.take_profit_r == 2.0


def test_offset_none_defers_to_adopted_default() -> None:
    """`offset_bps=None`은 "채택 기본값에 맡긴다", 0.0은 "0bp를 달라"다.

    둘을 가르지 않으면 CLI가 `ConfluenceParams`의 기본 오프셋을 말없이 덮어써서, 기본값이
    2bp로 올라가도 CLI 기본 실행만 혼자 0bp로 도는 갈라짐이 생긴다.
    """
    assert build_params(offset_bps=None).zone_limit_offset_bps == 2.0
    assert build_params(offset_bps=0.0).zone_limit_offset_bps == 0.0


def test_fill_presets_match_wan96_conservatism_levels() -> None:
    """`--fill` 프리셋이 WAN-96 보수화 레벨과 **같은 파라미터**를 만든다.

    이름만 같고 값이 다르면 `--fill pen_5bp` 결과를 WAN-96 표의 `pen_5bp` 행과 나란히
    읽을 수 없다 — 이 테스트가 그 조용한 갈라짐을 막는다.
    """
    from backtest.wan96_fill_conservatism_report import CONSERVATISM_LEVELS

    for level in CONSERVATISM_LEVELS:
        preset = fill_preset(level.name)
        assert preset.penetration_bps == level.penetration_bps
        assert preset.dropout_rate == level.dropout_rate
        assert preset.seeds == level.seeds
        for seed in level.seeds:
            # WAN-96은 오프셋 0bp + 게이트 `first_tap_free`에 고정돼 있다(당시 엔진 기록).
            # CLI 기본은 이제 2bp · 게이트 없음이므로 나란히 읽으려면 둘 다 그쪽에 맞춰야
            # 한다 — 이 인자들이 "옛 엔진을 요청한다"는 사실을 드러낸다(WAN-112/123).
            assert build_params(
                fill=preset,
                seed=seed,
                offset_bps=0.0,
                base=pin_band_bar(
                    ConfluenceParams(rsi_gate_mode=LEGACY_RSI_GATE_MODE, max_zone_width_atr=None)
                ),
            ) == level.params(seed)


def test_fill_presets_match_wan99_fill_assumptions() -> None:
    """WAN-99가 쓴 가정(오프셋 축 포함)도 같은 프리셋으로 재현된다.

    WAN-123 이후 게이트를 그쪽 고정값으로 되돌려야 한다 — 오프셋은 WAN-99가 **축으로
    명시**해 돌리므로 여기서 그대로 주지만, 게이트는 그 리포트가 **핀**으로 잡은 값이다.
    """
    from backtest.wan99_zone_limit_offset_report import FILL_ASSUMPTIONS

    legacy = pin_band_bar(
        ConfluenceParams(rsi_gate_mode=LEGACY_RSI_GATE_MODE, max_zone_width_atr=None)
    )
    for assumption in FILL_ASSUMPTIONS:
        preset = fill_preset(assumption.name)
        assert preset.penetration_bps == assumption.penetration_bps
        assert preset.dropout_rate == assumption.dropout_rate
        for offset in (0.0, 5.0, 20.0):
            for seed in assumption.seeds:
                assert build_params(
                    fill=preset, seed=seed, offset_bps=offset, base=legacy
                ) == assumption.params(offset, seed)


def test_build_params_pairs_rsi_mode_with_entry_mode() -> None:
    """`entry_mode`와 `rsi_mode`는 한 세트다(WAN-41) — 어긋나면 판정 시점이 체결과 갈린다."""
    assert build_params(entry_mode="zone_limit").rsi_mode == "realtime"
    assert build_params(entry_mode="close").rsi_mode == "closed_bar"


def test_build_params_rejects_zone_limit_knobs_on_close_entry() -> None:
    """종가 진입에 지정가 노브를 주면 거부한다 — 조용히 무시하면 라벨이 거짓말을 한다."""
    with pytest.raises(ValueError, match="오프셋"):
        build_params(entry_mode="close", offset_bps=5.0)
    with pytest.raises(ValueError, match="체결 가정"):
        build_params(entry_mode="close", fill=fill_preset("pen_5bp"))


def test_build_params_rejects_unknown_entry_mode() -> None:
    with pytest.raises(ValueError, match="진입 방식"):
        build_params(entry_mode="market")


def test_build_params_only_touches_requested_axes() -> None:
    """격자 축 밖의 전략 파라미터는 건드리지 않는다 — 무엇이 성과를 냈는지 귀속하려면."""
    tunable = {
        "entry_mode",
        "rsi_mode",
        "zone_limit_offset_bps",
        "fill_penetration_bps",
        "fill_dropout_rate",
        "fill_dropout_seed",
        "take_profit_r",
        "short_enabled",
        "max_zone_width_atr",
    }
    default = ConfluenceParams().model_dump()
    params = build_params(entry_mode="close", take_profit_r=3.0, short_enabled=True).model_dump()
    diff = {k for k, v in params.items() if v != default[k]}
    assert diff <= tunable


def test_fill_preset_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="알 수 없는 체결 가정"):
        fill_preset("nope")


def test_iter_seeds_runs_one_seed_when_no_dropout() -> None:
    """탈락이 없으면 시드가 결과를 안 바꾼다 — 여러 개 도는 건 같은 숫자를 N번 계산하는 낭비."""
    assert list(iter_seeds(BASELINE_FILL)) == [0]
    assert list(iter_seeds(BASELINE_FILL, [1, 2, 3])) == [0]
    drop = fill_preset("drop_50")
    assert list(iter_seeds(drop)) == list(drop.seeds)
    assert list(iter_seeds(drop, [7, 8])) == [7, 8]


# ---------------------------------------------------- 비용 설정


def test_build_config_keeps_risk_sizing_from_shared_factory() -> None:
    """공용 팩토리를 거쳐야 `risk_sizing`이 붙는다 — 빠지면 전 거래가 자본 100%를 쓴다(WAN-65)."""
    cfg = build_config(_TIMEFRAME)
    assert cfg.risk_sizing is not None
    assert cfg.annualization_factor is not None


def test_build_config_applies_cost_overrides_only_when_given() -> None:
    base = build_config(_TIMEFRAME)
    cfg = build_config(_TIMEFRAME, fee_rate=0.0, maker_fee_rate=0.0, slippage=0.0)
    assert (cfg.fee_rate, cfg.maker_fee_rate, cfg.slippage) == (0.0, 0.0, 0.0)
    assert cfg.risk_sizing == base.risk_sizing
    assert build_config(_TIMEFRAME, funding_enabled=False).funding_enabled is False


# ---------------------------------------------------- 구간 분할


def test_segments_default_to_full_window_only() -> None:
    assert segments_for() == (Segment(SEGMENT_FULL, 0, 0.0, 1.0),)


def test_segments_for_oos_splits_two_thirds_and_keeps_full_baseline() -> None:
    """IS/OOS는 앞 2/3 · 뒤 1/3이고, 전 구간도 함께 낸다(비교 기준선)."""
    segments = segments_for(oos=True)
    assert [s.name for s in segments] == [SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS]
    is_seg, oos_seg = segments[1], segments[2]
    assert is_seg.end_fraction == pytest.approx(IS_FRACTION)
    assert oos_seg.start_fraction == pytest.approx(IS_FRACTION)
    assert oos_seg.end_fraction == 1.0


def test_walkforward_windows_are_consecutive_and_do_not_overlap() -> None:
    """롤링 창은 겹치지 않는다 — 겹치면 OOS가 다른 창의 IS를 다시 보는 셈이다."""
    segments = segments_for(walkforward=3)
    assert len(segments) == 6
    assert {s.window for s in segments} == {0, 1, 2}
    for window in (0, 1, 2):
        pair = [s for s in segments if s.window == window]
        assert [s.name for s in pair] == [SEGMENT_IS, SEGMENT_OOS]
        assert pair[0].end_fraction == pytest.approx(pair[1].start_fraction)
    # 창 사이도 이어붙는다(빈틈 없음).
    ordered = sorted(segments, key=lambda s: s.start_fraction)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.end_fraction == pytest.approx(nxt.start_fraction)


def test_segments_for_rejects_negative_walkforward() -> None:
    with pytest.raises(ValueError):
        segments_for(walkforward=-1)


def test_segment_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError, match="구간 비율"):
        Segment(name="bad", window=0, start_fraction=0.6, end_fraction=0.4)


def test_slice_market_splits_htf_and_1m_and_funding_by_time() -> None:
    """구간 분할은 상위TF뿐 아니라 1분봉·펀딩비도 같이 잘라야 한다."""
    market = _market()
    is_part = slice_market(market, Segment(SEGMENT_IS, 0, 0.0, IS_FRACTION))
    oos_part = slice_market(market, Segment(SEGMENT_OOS, 0, IS_FRACTION, 1.0))

    # 합치면 전체다 — 어느 봉도 두 구간 사이로 사라지지 않는다.
    assert len(is_part.htf_df) + len(oos_part.htf_df) == len(market.htf_df)
    assert is_part.start_ms == market.start_ms
    assert oos_part.end_ms == market.end_ms
    assert is_part.end_ms < oos_part.start_ms
    assert len(is_part.df_1m) + len(oos_part.df_1m) <= len(market.df_1m)
    assert all(r.funding_time < oos_part.start_ms for r in is_part.funding_rates)
    assert not set(is_part.funding_rates) & set(oos_part.funding_rates)


def test_slice_market_full_segment_is_identity() -> None:
    market = _market()
    assert slice_market(market, Segment(SEGMENT_FULL, 0, 0.0, 1.0)) is market


# ---------------------------------------------------- 경로 스위치


def test_run_once_zone_limit_matches_report_engine_call() -> None:
    """하네스의 지정가 경로 == 리포트가 직접 부르는 엔진 호출(엔진이 조용히 갈라지지 않음).

    완료기준의 '회귀 검증'을 합성 데이터로 고정한다: CLI가 다른 함수를 타거나 인자를
    빠뜨리면 여기서 숫자가 갈린다.
    """
    from backtest.zone_limit_backtest import run_zone_limit_backtest_verbose

    market = _market()
    cfg = build_config(_TIMEFRAME)
    params = build_params()
    ob_result = detect_order_blocks(market)

    outcome = run_once(market, params=params, cfg=cfg, order_block_result=ob_result)
    expected, expected_stats = run_zone_limit_backtest_verbose(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        confluence_params=params,
        backtest_config=cfg,
        order_block_result=ob_result,
        funding_rates=market.funding_rates,
    )
    assert outcome.result.metrics == expected.metrics
    assert outcome.stats == expected_stats


def test_run_once_close_entry_matches_sweep_evaluate() -> None:
    """종가 경로 == `backtest.sweep.evaluate` 그대로(A안 엔진)."""
    from backtest.sweep import evaluate

    market = _market()
    cfg = build_config(_TIMEFRAME)
    params = build_params(entry_mode="close")
    ob_result = detect_order_blocks(market)

    outcome = run_once(market, params=params, cfg=cfg, order_block_result=ob_result)
    expected = evaluate(
        market.htf_df,
        confluence_params=params,
        backtest_config=cfg,
        order_block_result=ob_result,
        funding_rates=market.funding_rates,
    )
    assert outcome.result.metrics == expected.metrics
    assert outcome.stats is None  # 종가 진입은 체결률 축이 없다.


def test_run_once_close_entry_fair_window_limits_to_1m_coverage() -> None:
    """공정 창을 켜면 종가 거래가 1분봉 커버 구간으로 한정된다(WAN-41/95)."""
    market = _market(bars=400, span=100)
    cfg = build_config(_TIMEFRAME)
    params = build_params(entry_mode="close")
    ob_result = detect_order_blocks(market)

    full = run_once(market, params=params, cfg=cfg, order_block_result=ob_result)
    windowed = run_once(
        market, params=params, cfg=cfg, order_block_result=ob_result, fair_window=True
    )
    start = int(market.df_1m["open_time"].iloc[0])
    assert windowed.result.metrics.num_trades <= full.result.metrics.num_trades
    assert all(t.entry_time >= start for t in windowed.result.trades)


def test_run_once_portfolio_matches_the_multi_position_engine() -> None:
    """WAN-130: 하네스의 다중 포지션 경로 == WAN-103 엔진 직접 호출."""
    from backtest.portfolio import PortfolioParams
    from backtest.zone_limit_backtest import run_zone_limit_portfolio_backtest

    market = _market()
    cfg = build_config(_TIMEFRAME)
    params = build_params()
    ob_result = detect_order_blocks(market)
    portfolio = PortfolioParams(leverage=3.0)

    outcome = run_once(
        market, params=params, cfg=cfg, order_block_result=ob_result, portfolio=portfolio
    )
    expected, expected_stats, _pf = run_zone_limit_portfolio_backtest(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        portfolio=portfolio,
        confluence_params=params,
        backtest_config=cfg,
        order_block_result=ob_result,
        funding_rates=market.funding_rates,
    )
    assert outcome.result.metrics == expected.metrics
    assert outcome.stats == expected_stats


def test_run_once_portfolio_rejects_close_entry() -> None:
    """다중 포지션 회계는 B안 전용 — A안에 붙이면 조용히 무시하지 않고 거부한다."""
    from backtest.portfolio import PortfolioParams

    market = _market()
    with pytest.raises(ValueError, match="다중 포지션"):
        run_once(
            market,
            params=build_params(entry_mode="close"),
            cfg=build_config(_TIMEFRAME),
            portfolio=PortfolioParams(leverage=2.0),
        )


def test_run_row_survives_csv_round_trip_with_empty_leverage() -> None:
    """단일 포지션 행의 빈 레버리지 칸이 `--from-csv` 왕복에서 `NaN`으로 돌아오지 않는다."""
    import io

    market = _market()
    params = build_params()
    row = build_row(
        run_once(market, params=params, cfg=build_config(_TIMEFRAME)),
        market,
        segment=Segment(SEGMENT_FULL, 0, 0.0, 1.0),
        params=params,
        fill_name="baseline",
    )
    frame = pd.read_csv(io.StringIO(render_csv([row])))
    restored = RunRow.model_validate(frame.to_dict(orient="records")[0])
    assert restored.portfolio_leverage is None
    assert (restored.position_mode, restored.retap_mode) == (row.position_mode, row.retap_mode)


def test_build_row_labels_position_mode_from_the_portfolio_object() -> None:
    """행의 `position_mode`가 실제로 넘긴 포트폴리오에서 나온다(라벨 조작 불가)."""
    from backtest.portfolio import PortfolioParams

    market = _market()
    cfg = build_config(_TIMEFRAME)
    params = build_params()
    segment = Segment(SEGMENT_FULL, 0, 0.0, 1.0)
    outcome = run_once(market, params=params, cfg=cfg)

    single = build_row(outcome, market, segment=segment, params=params, fill_name="baseline")
    multi = build_row(
        outcome,
        market,
        segment=segment,
        params=params,
        fill_name="baseline",
        portfolio=PortfolioParams(leverage=2.5),
    )
    assert (single.position_mode, single.portfolio_leverage) == ("single", None)
    assert (multi.position_mode, multi.portfolio_leverage) == ("multi", 2.5)


def test_run_once_zone_limit_without_1m_data_fails_loudly() -> None:
    """1분봉 없이 지정가를 돌리라는 요구는 조용히 종가로 되돌리지 않고 거부한다."""
    market = _market()
    dry = MarketData(_SYMBOL, _TIMEFRAME, market.htf_df, pd.DataFrame(), [])
    with pytest.raises(ValueError, match="1분봉"):
        run_once(dry, params=build_params(), cfg=build_config(_TIMEFRAME))


# ---------------------------------------------------- 평균 R


def _trade(reason: ExitReason) -> Trade:
    return Trade(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=0.0,
        exits=[TradeFill(time=1, price=101.0, quantity=1.0, fee=0.0, reason=reason)],
        realized_pnl=1.0,
        return_pct=0.01,
    )


def _result(reasons: list[ExitReason]) -> BacktestResult:
    trades = [_trade(r) for r in reasons]
    return BacktestResult(
        config=BacktestConfig(),
        trades=trades,
        equity_curve=[],
        metrics=build_metrics(initial_capital=1.0, equities=[1.0], trades=trades),
    )


def test_mean_r_scores_by_exit_reason_and_skips_unresolved() -> None:
    """손절 = −1R, 고정 R 익절 = +take_profit_r. 미청산은 R이 확정되지 않아 분모에서 빠진다."""
    assert mean_r(_result([ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT]), 1.5) == 0.25
    assert mean_r(_result([ExitReason.STOP_LOSS, ExitReason.END_OF_DATA]), 1.5) == -1.0
    assert mean_r(_result([]), 1.5) is None


def test_mean_r_matches_wan99_definition() -> None:
    """WAN-99가 쓰던 정의와 동일하다(그 리포트가 이 구현으로 위임됐다)."""
    from backtest.wan99_zone_limit_offset_report import mean_r as wan99_mean_r

    assert wan99_mean_r is mean_r


# ---------------------------------------------------- 렌더


def _rows() -> list[RunRow]:
    market = _market()
    cfg = build_config(_TIMEFRAME)
    ob_result = detect_order_blocks(market)
    rows: list[RunRow] = []
    for tp_r in (1.5, 2.0):
        params = build_params(take_profit_r=tp_r)
        outcome = run_once(market, params=params, cfg=cfg, order_block_result=ob_result)
        rows.append(
            build_row(
                outcome,
                market,
                segment=Segment(SEGMENT_FULL, 0, 0.0, 1.0),
                params=params,
                fill_name="baseline",
            )
        )
    return rows


def test_build_row_records_axes_next_to_metrics() -> None:
    """행에 좌표가 같이 남아야 CSV만 봐도 어떤 설정의 숫자인지 안다(WAN-65)."""
    row = _rows()[0]
    assert row.symbol == _SYMBOL
    assert row.timeframe == _TIMEFRAME
    assert row.entry_mode == "zone_limit"
    assert row.take_profit_r == 1.5
    assert row.offset_bps == 2.0  # 채택 기본값(WAN-112)
    assert row.fill == "baseline"
    assert row.eligible_setups is not None  # 지정가는 체결률 축이 있다.
    assert row.num_bars > 0


def test_render_table_shows_metrics_required_by_the_issue() -> None:
    """표에 완료기준의 성과 열이 모두 있어야 한다."""
    table = render_table(_rows())
    for header in ("return%", "win%", "mdd%", "trades", "fill%", "meanR", "sharpe"):
        assert header in table


def test_render_table_folds_constant_axes_and_shows_varying_ones() -> None:
    """값이 하나뿐인 축은 접고, 실제로 스윕한 축만 열로 펼친다(줄이 좁아야 읽힌다)."""
    table = render_table(_rows())
    assert "tp_r" in table  # 1.5 vs 2.0으로 갈리는 축은 열로.
    assert "[고정]" in table
    assert f"symbol={_SYMBOL}" in table


def test_render_table_handles_empty_rows() -> None:
    assert "실행 결과가 없습니다" in render_table([])


def test_render_csv_keeps_every_column_including_folded_axes() -> None:
    """CSV는 표에서 접힌 축까지 전부 남긴다 — 사후 분석의 입력이므로."""
    text = render_csv(_rows())
    frame = pd.read_csv(pd.io.common.StringIO(text))
    assert len(frame) == 2
    for column in ("symbol", "timeframe", "entry_mode", "take_profit_r", "fill", "total_return"):
        assert column in frame.columns


def test_render_json_round_trips_to_records() -> None:
    payload = json.loads(render_json(_rows()))
    assert len(payload) == 2
    assert payload[0]["take_profit_r"] == 1.5
    assert "total_return" in payload[0]


def test_render_dispatches_by_format_name() -> None:
    rows = _rows()
    assert render(rows, "table") == render_table(rows)
    assert render(rows, "csv") == render_csv(rows)
    assert render(rows, "json") == render_json(rows)
    with pytest.raises(ValueError, match="출력 형식"):
        render(rows, "yaml")


def test_fill_presets_are_uniquely_named_and_start_at_baseline() -> None:
    names = [p.name for p in FILL_PRESETS]
    assert len(names) == len(set(names))
    assert names[0] == "baseline"
    assert BASELINE_FILL.penetration_bps == 0.0
    assert BASELINE_FILL.dropout_rate == 0.0


def test_custom_fill_preset_is_usable_without_registration() -> None:
    """`--fill-penetration-bps`가 만드는 즉석 프리셋도 파라미터로 조립된다."""
    custom = FillPreset(name="custom", penetration_bps=3.0, dropout_rate=0.1)
    params = build_params(fill=custom, seed=2)
    assert params.fill_penetration_bps == 3.0
    assert params.fill_dropout_rate == 0.1
    assert params.fill_dropout_seed == 2
