"""WAN-114 ablation의 사다리 정의·집계 로직 테스트 (실데이터 없이 합성 행으로).

이 리포트가 **라벨과 실제 실행이 갈라질** 자리들을 고정한다: 사다리의 각 단이 이웃과
정확히 한 부품만 다른지(두 개가 같이 움직이면 "그 부품의 한계 기여"라는 말이 거짓이 된다),
`L2`가 정말 채택 기본값인지(아니면 표의 "채택 기본값" 라벨이 거짓말이다), `L0`이 WAN-100
배선(첫 탭 무조건 진입)을 지키는지, 그리고 판정 문장이 표의 숫자에서 실제로 계산되는지.
"""

from __future__ import annotations

import pytest

from backtest.harness import FILL_PRESETS_BY_NAME, build_params, fill_preset
from backtest.wan114_entry_rule_ablation import (
    ADOPTED_RUNG,
    ALL_SYMBOLS,
    BASE_RUNG,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_TIMEFRAMES,
    LADDER,
    LENS_NAMES,
    RUNGS,
    RUNGS_BY_NAME,
    AblationRow,
    incremental,
    per_symbol,
    rung_params,
    rung_summary,
    segments,
    verdict,
)

_BASELINE = fill_preset("baseline")


def _row(
    *,
    level: str,
    symbol: str = "BTC/USDT:USDT",
    total_return: float = 0.1,
    timeframe: str = "15m",
    segment: str = "oos",
    fill: str = "baseline",
    seed: int = 0,
    num_trades: int = 100,
    max_drawdown: float = 0.1,
) -> AblationRow:
    return AblationRow(
        level=level,
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
        end_time=1000,
        num_bars=100,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=max_drawdown,
        sharpe=1.0,
        profit_factor=1.2,
        mean_r=0.2,
        fill_rate=0.5,
        eligible_setups=200,
        num_filled=num_trades,
        funding_coverage=1.0,
    )


# --------------------------------------------------------------------------- #
# 사다리 정의 — 한 번에 한 부품만 움직이는가
# --------------------------------------------------------------------------- #


def test_adopted_rung_is_exactly_the_adoption_default() -> None:
    """`L2` == `ConfluenceParams()` 채택 기본값.

    이것이 이 리포트의 중심 주장이다: 표의 `L2` 행이 곧 채택 기본값이고, 그래서 이슈의
    `L3`을 따로 돌 필요가 없다. 어긋나면 "규칙 층 전체 기여"가 채택 기본값이 아닌 무언가에
    대한 값이 된다 — 라벨만 맞고 실체가 다른 WAN-95식 사고다.
    """
    adopted = rung_params(RUNGS_BY_NAME[ADOPTED_RUNG], fill=_BASELINE)
    assert adopted == build_params(entry_mode="zone_limit", fill=_BASELINE)


def test_ladder_changes_one_component_per_step() -> None:
    """이웃한 두 단은 **정확히 한 부품 묶음**만 다르다.

    두 부품이 같이 움직이면 그 단의 델타는 "그 부품의 한계 기여"가 아니다. `L0→L0r`만
    두 필드가 함께 움직이는데, 그건 재탭이 없으면 게이트도 존재할 수 없기 때문이다
    (첫 탭은 어차피 면제라 게이트는 재탭에만 산다) — 그 묶음이 곧 "재탭 노출"이다.
    """
    knobs = ("retap_mode", "rsi_gate_mode", "deviation_filter")
    seen = [{k: getattr(rung_params(r, fill=_BASELINE), k) for k in knobs} for r in RUNGS]
    diffs = [
        {k for k in knobs if before[k] != after[k]}
        for before, after in zip(seen, seen[1:], strict=False)
    ]
    assert diffs[0] == {"retap_mode", "rsi_gate_mode"}, "L0→L0r = 재탭 노출(게이트는 재탭에만 산다)"
    assert diffs[1] == {"rsi_gate_mode"}, "L0r→L1 = RSI 게이트만"
    assert diffs[2] == {"deviation_filter"}, "L1→L2 = 볼린저만"


def test_ladder_holds_the_foundation_fixed() -> None:
    """토대(진입 방식·오프셋 2bp·익절 1.5R·롱 온리)는 모든 단에서 고정 입력이다.

    부품 하나를 재는 표에서 토대가 단마다 다르면 델타가 무엇의 값인지 알 수 없다.
    오프셋·익절 R을 상수로 박지 않고 채택 기본값에서 가져오는지도 함께 고정한다 —
    박아 두면 재-베이스라인(WAN-112 같은) 때 이 리포트만 옛 엔진을 돈다.
    """
    defaults = build_params(entry_mode="zone_limit", fill=_BASELINE)
    for rung in RUNGS:
        params = rung_params(rung, fill=_BASELINE)
        assert params.entry_mode == "zone_limit"
        assert params.rsi_mode == "realtime", "지정가와 실시간 RSI는 한 세트다(WAN-41/95)"
        assert params.zone_limit_offset_bps == defaults.zone_limit_offset_bps
        assert params.take_profit_r == defaults.take_profit_r
        assert params.take_profit_mode == "fixed_r", "고정 R 익절은 모든 단에 공통이다"
        assert params.short_enabled is False, "롱 온리는 토대다(WAN-87)"


def test_base_rung_keeps_first_tap_free_wiring() -> None:
    """`L0`은 첫 탭 면제를 **유지**한다 — WAN-100 배선(첫 탭은 호출부 책임).

    `retap_mode="once"`라 모든 시그널이 `tap_index=0`이고, `rsi_gate_mode=
    "first_tap_free"`면 `build_zone_limit_candidates`의
    `first_tap_free = (mode == "first_tap_free" and tap_index == 0)`이 항상 참이 된다.
    여기를 `none`으로 바꾸면 워밍업(RSI None) 첫 탭이 조용히 막혀 "존-단독 하한선"이
    존-단독이 아니게 된다.
    """
    base = rung_params(RUNGS_BY_NAME[BASE_RUNG], fill=_BASELINE)
    assert base.retap_mode == "once"
    assert base.rsi_gate_mode == "first_tap_free"
    assert base.deviation_filter is None, "존-단독은 볼린저 재산정이 없다(존 근단 그대로)"


def test_ladder_starts_at_zone_only_and_ends_at_adoption() -> None:
    assert LADDER[0] == BASE_RUNG
    assert LADDER[-1] == ADOPTED_RUNG
    assert len(set(LADDER)) == len(LADDER)


def test_fill_penetration_flows_into_every_rung() -> None:
    """렌즈(체결 가정)는 사다리 전체에 **동일하게** 얹힌다 — 단마다 다르면 비교가 깨진다."""
    for rung in RUNGS:
        params = rung_params(rung, fill=fill_preset("pen_5bp_drop_50"), seed=3)
        assert params.fill_penetration_bps == 5.0
        assert params.fill_dropout_rate == 0.5
        assert params.fill_dropout_seed == 3


# --------------------------------------------------------------------------- #
# 축 — 다른 리포트와 같은 뜻인가
# --------------------------------------------------------------------------- #


def test_lens_names_exist_in_harness() -> None:
    """렌즈 이름이 harness 프리셋과 일대일이어야 이 표를 WAN-96/107/110/111 표와 나란히 놓는다."""
    for name in LENS_NAMES:
        assert name in FILL_PRESETS_BY_NAME
    assert LENS_NAMES[0] == "baseline", "공식 렌즈가 첫 자리여야 판정이 그것을 읽는다(토대 2)"


def test_timeframes_are_both_working_tfs() -> None:
    """WAN-107 공동 작업 TF 병기 — 한쪽만 내면 그 결정의 요구사항을 어긴다."""
    assert set(DEFAULT_TIMEFRAMES) == {"15m", "1h"}


def test_symbol_universe_is_six() -> None:
    """WAN-111 유니버스 — 3심볼 표본이 채택 수치를 이고 있었으므로 부품 델타도 6심볼에서 잰다."""
    assert len(ALL_SYMBOLS) == 6


def test_window_is_pinned_not_years() -> None:
    """창을 못 박는다 — `--years`는 마지막 봉 기준이라 심볼마다 창이 어긋난다(CLAUDE.md)."""
    assert DEFAULT_START < DEFAULT_END


def test_segments_are_is_and_oos_only() -> None:
    """IS/OOS만 낸다(이슈 완료기준). 두 구간은 겹치지 않고 이어 붙으면 전체가 된다."""
    is_seg, oos_seg = segments()
    assert (is_seg.name, oos_seg.name) == ("is", "oos")
    assert is_seg.start_fraction == 0.0
    assert is_seg.end_fraction == oos_seg.start_fraction
    assert oos_seg.end_fraction == 1.0


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def test_per_symbol_folds_seeds_within_symbol_and_level() -> None:
    """시드를 (단, 심볼) **안에서** 먼저 접는다 — 그래야 증분 델타를 심볼별로 짝짓는다."""
    rows = [
        _row(level="L0", total_return=0.10, fill="pen_5bp_drop_50", seed=0),
        _row(level="L0", total_return=0.20, fill="pen_5bp_drop_50", seed=1),
        _row(level="L2", total_return=0.40, fill="pen_5bp_drop_50", seed=0),
        _row(level="L2", total_return=0.60, fill="pen_5bp_drop_50", seed=1),
    ]
    frame = per_symbol(rows).set_index("level")
    assert frame.loc["L0", "total_return"] == pytest.approx(0.15)
    assert frame.loc["L2", "total_return"] == pytest.approx(0.50)
    assert frame.loc["L0", "seeds"] == 2


def test_incremental_pairs_symbols_and_counts_direction() -> None:
    """델타는 심볼별로 짝지어 계산하고, 같은 방향으로 움직인 심볼 수를 센다."""
    rows = [_row(level="L0", symbol=s, total_return=0.10) for s in ALL_SYMBOLS]
    # 5심볼은 오르고 1심볼은 내린다 → 평균은 플러스지만 방향은 5/6이어야 한다.
    rows += [_row(level="L0r", symbol=s, total_return=0.20) for s in ALL_SYMBOLS[:5]]
    rows += [_row(level="L0r", symbol=ALL_SYMBOLS[5], total_return=-0.20)]
    steps = incremental(per_symbol(rows)).set_index("step")
    assert steps.loc["L0→L0r", "delta_return"] == pytest.approx((0.10 * 5 - 0.30) / 6)
    assert steps.loc["L0→L0r", "symbols_up"] == 5
    assert steps.loc["L0→L0r", "symbols"] == 6


def test_incremental_reports_trade_count_change() -> None:
    """수익 델타 옆에 거래 수 변화가 있어야 WAN-96 비대칭을 부품 축에서 읽는다."""
    rows = [_row(level="L0", num_trades=100, total_return=0.1)]
    rows += [_row(level="L0r", num_trades=150, total_return=0.2)]
    steps = incremental(per_symbol(rows)).set_index("step")
    assert steps.loc["L0→L0r", "delta_trades_pct"] == pytest.approx(0.5)


def test_incremental_walks_the_whole_ladder() -> None:
    """사다리의 모든 이웃 쌍이 나온다 — 하나라도 빠지면 그 부품의 기여가 표에서 사라진다."""
    rows = [_row(level=name, symbol=s) for name in LADDER for s in ALL_SYMBOLS]
    steps = incremental(per_symbol(rows))
    assert list(steps["step"]) == [f"{a}→{b}" for a, b in zip(LADDER, LADDER[1:], strict=False)]


def test_rung_summary_counts_positive_symbols() -> None:
    """플러스 심볼 수가 실제 부호를 센다 — 평균이 플러스여도 심볼은 갈릴 수 있다(WAN-111)."""
    rows = [_row(level="L2", symbol=s, total_return=-0.1) for s in ALL_SYMBOLS[:4]]
    rows += [_row(level="L2", symbol=s, total_return=0.5) for s in ALL_SYMBOLS[4:]]
    summary = rung_summary(per_symbol(rows)).set_index("level")
    assert summary.loc["L2", "positive"] == 2
    assert summary.loc["L2", "symbols"] == 6


# --------------------------------------------------------------------------- #
# 판정 — 숫자에서 계산되는가
# --------------------------------------------------------------------------- #


def test_verdict_reads_official_lens_only() -> None:
    """판정은 `baseline`(공식)만 읽는다 — 민감도가 판정을 흔들면 토대 2 위반이다."""
    rows = [
        _row(level=n, symbol=s, total_return=0.1, fill="baseline")
        for n in LADDER
        for s in ALL_SYMBOLS
    ]
    rows += [
        _row(level=n, symbol=s, total_return=-0.9, fill="pen_5bp")
        for n in LADDER
        for s in ALL_SYMBOLS
    ]
    frame = per_symbol(rows)
    text = "\n".join(verdict(rung_summary(frame), incremental(frame)))
    assert "-90.00%" not in text


def test_verdict_says_rules_add_nothing_when_ladder_is_flat() -> None:
    """존-단독과 채택 기본값이 같으면 판정이 **「값을 더하지 못한다」로 계산된다**.

    이 이슈의 귀무가설이 실제로 문장이 되는지를 고정한다 — 사람이 손으로 적으면 재실행 때
    숫자와 갈라진다(WAN-95).
    """
    rows = [_row(level=n, symbol=s, total_return=0.1) for n in LADDER for s in ALL_SYMBOLS]
    frame = per_symbol(rows)
    text = "\n".join(verdict(rung_summary(frame), incremental(frame)))
    assert "+0.00%p" in text
    assert "값을 더하지 못한다" in text


def test_verdict_reports_gap_when_rules_help() -> None:
    """규칙이 실제로 더 벌면 격차가 그대로 계산돼 나온다."""
    rows = [_row(level=n, symbol=s, total_return=0.1) for n in LADDER[:-1] for s in ALL_SYMBOLS]
    rows += [_row(level=ADOPTED_RUNG, symbol=s, total_return=0.3) for s in ALL_SYMBOLS]
    frame = per_symbol(rows)
    text = "\n".join(verdict(rung_summary(frame), incremental(frame)))
    assert "+10.00%(6/6)" in text
    assert "+30.00%(6/6)" in text
    assert "+20.00%p" in text
    assert "규칙이 값을 더한다" in text


def test_verdict_covers_both_working_tfs() -> None:
    rows = [
        _row(level=n, symbol=s, timeframe=tf)
        for n in LADDER
        for s in ALL_SYMBOLS
        for tf in DEFAULT_TIMEFRAMES
    ]
    frame = per_symbol(rows)
    text = "\n".join(verdict(rung_summary(frame), incremental(frame)))
    assert "15m oos" in text
    assert "1h oos" in text
