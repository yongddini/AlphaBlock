"""WAN-120 엄격 인과 밴드 격자의 사다리 정의·집계·판정 테스트 (합성 행으로).

이 리포트가 **라벨과 실제 실행이 갈라질** 자리들을 고정한다: 네 단이 오직 `band_bar`만
다른지(다른 게 같이 움직이면 "지연 한 칸의 몫"이라는 말이 거짓이 된다), `L1`·`L2`·`L2i`가
정말 WAN-119의 같은 단인지(아니면 "그 CSV와 검산된다"가 거짓말이다), 그리고 **판정이
부호로** 내려지는지 — 이 이슈는 "어느 밴드가 더 버나"가 아니라 "잔여 1분이 판정을
흔드나"를 묻기 때문에, 크기로 판정하는 순간 질문이 바뀐다.
"""

from __future__ import annotations

import pytest

from backtest.harness import LEGACY_BAND_BAR, LEGACY_RSI_GATE_MODE, fill_preset
from backtest.wan119_intrabar_live_band import RUNGS_BY_NAME as WAN119_RUNGS
from backtest.wan119_intrabar_live_band import rung_params as wan119_rung_params
from backtest.wan120_strict_causal_band import (
    BASE_RUNG,
    CAUSAL_RUNG,
    DEFAULT_END,
    DEFAULT_START,
    LADDER,
    LIVE_RUNG,
    RUNGS_BY_NAME,
    SHAKEN_DELTA,
    SHAKEN_RETURN,
    STABLE,
    TAP_RUNG,
    CausalBandRow,
    _shake_label,
    incremental,
    per_symbol,
    per_symbol_spread,
    rung_params,
    rung_summary,
    segments,
    selection_vs_price,
    verdict,
)
from strategy.models import ConfluenceParams

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
) -> CausalBandRow:
    return CausalBandRow(
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
# 사다리 정의 — 이 리포트의 주장이 참인지
# --------------------------------------------------------------------------- #


def test_ladder_is_the_four_rungs_in_order() -> None:
    assert LADDER == ("L1", "L2", "L2i", "L2c")
    assert (BASE_RUNG, TAP_RUNG, LIVE_RUNG, CAUSAL_RUNG) == ("L1", "L2", "L2i", "L2c")


def test_tap_rung_is_exactly_the_adopted_default() -> None:
    """`L2`가 **WAN-122까지의** 채택 기본값이라는 표의 라벨이 참이어야 한다.

    ⚠️ WAN-123(RSI 게이트 제거)이 이 동일성을 **한 필드만큼** 깼고, WAN-132(밴드 정본을
    `intrabar_live`로)가 **두 번째 필드**를 깼다: 사다리는 `rsi_gate_mode="first_tap_free"`와
    `band_bar="tap"`을 고정하는데 현재 기본값은 `unconditional`·`intrabar_live`다.
    그 고정은 의도적이다 — 풀어 두면 이 표가 WAN-114/115와 비트 단위로 맞물리지 않게
    되고, `L1→L2` 증분이 "볼린저의 기여"가 아니라 "볼린저 + 게이트 제거 + 밴드 전환"이 된다.
    나머지 필드는 여전히 기본값을 물려받아야 하므로 그것을 확인한다.
    """
    adopted = ConfluenceParams()
    tap = rung_params(RUNGS_BY_NAME[TAP_RUNG], fill=_BASELINE)
    assert adopted.deviation_filter is not None and tap.deviation_filter is not None
    # 밴드 표본만 빼면 볼린저 정의(SMA20 ± 2σ)는 채택 기본값 그대로다.
    assert tap.deviation_filter == adopted.deviation_filter.model_copy(
        update={"band_bar": LEGACY_BAND_BAR}
    )
    assert tap.retap_mode == adopted.retap_mode
    # WAN-123/132: 고정한 게이트·밴드 ≠ 현재 기본값. 이 부등호가 깨지면 사다리가 조용히
    # 새 엔진으로 옮겨 갔다는 뜻이다.
    assert tap.rsi_gate_mode == LEGACY_RSI_GATE_MODE != adopted.rsi_gate_mode
    assert tap.deviation_filter.band_bar == LEGACY_BAND_BAR != adopted.deviation_filter.band_bar
    # 토대: 오프셋 2bp(WAN-112) · 고정 1.5R(WAN-81)를 기본값에서 물려받는다.
    assert tap.zone_limit_offset_bps == adopted.zone_limit_offset_bps == pytest.approx(2.0)
    assert tap.take_profit_r == adopted.take_profit_r == pytest.approx(1.5)


@pytest.mark.parametrize(
    ("level", "band_bar"),
    [(TAP_RUNG, "tap"), (LIVE_RUNG, "intrabar_live"), (CAUSAL_RUNG, "intrabar_causal")],
)
def test_each_bollinger_rung_carries_its_band_bar(level: str, band_bar: str) -> None:
    filt = rung_params(RUNGS_BY_NAME[level], fill=_BASELINE).deviation_filter
    assert filt is not None
    assert filt.band_bar == band_bar


@pytest.mark.parametrize("level", [LIVE_RUNG, CAUSAL_RUNG])
def test_rungs_differ_from_the_adopted_default_only_by_band_bar(level: str) -> None:
    """`L2` → `L2i`/`L2c`가 **오직 `band_bar`만** 다르다.

    이게 깨지면 `L2i`↔`L2c`의 차이를 "지연 한 칸의 몫"이라고 부를 수 없다 — 두 부품이
    같이 움직인 값이 된다.
    """
    tap = rung_params(RUNGS_BY_NAME[TAP_RUNG], fill=_BASELINE)
    other = rung_params(RUNGS_BY_NAME[level], fill=_BASELINE)
    assert tap.deviation_filter is not None
    assert other.deviation_filter is not None
    reverted = other.model_copy(
        update={"deviation_filter": other.deviation_filter.model_copy(update={"band_bar": "tap"})}
    )
    assert reverted == tap


def test_base_rung_has_bollinger_off() -> None:
    """`L1`은 볼린저가 꺼진 바닥 — 증분의 기준선이다."""
    assert rung_params(RUNGS_BY_NAME[BASE_RUNG], fill=_BASELINE).deviation_filter is None


@pytest.mark.parametrize("level", ["L1", "L2", "L2i"])
def test_shared_rungs_match_wan119_bit_for_bit(level: str) -> None:
    """`L1`·`L2`·`L2i`가 WAN-119의 같은 단과 **완전히 같은 파라미터**다.

    이 동치가 이 리포트의 검산 근거다("`wan119_intrabar_live_band.csv`의 같은 단과 일치").
    깨지면 세 단을 비교할 수 없고, 이슈가 인용한 +16.70%p도 이 표와 무관해진다.
    """
    mine = rung_params(RUNGS_BY_NAME[level], fill=_BASELINE)
    theirs = wan119_rung_params(WAN119_RUNGS[level], fill=_BASELINE)
    assert mine == theirs


def test_window_is_pinned_like_wan119() -> None:
    """창이 미끄러지면(`--years N`) 두 리포트가 다른 기간을 봐 검산이 깨진다."""
    from backtest.wan119_intrabar_live_band import DEFAULT_END as W119_END
    from backtest.wan119_intrabar_live_band import DEFAULT_START as W119_START

    assert (DEFAULT_START, DEFAULT_END) == (W119_START, W119_END)


def test_segments_are_is_two_thirds_then_oos() -> None:
    is_seg, oos_seg = segments()
    assert (is_seg.name, oos_seg.name) == ("is", "oos")
    assert is_seg.end_fraction == oos_seg.start_fraction


# --------------------------------------------------------------------------- #
# 판정 — 부호로 내려지는가
# --------------------------------------------------------------------------- #


def test_shake_label_is_stable_when_both_signs_hold() -> None:
    assert (
        _shake_label(live_return=0.04, causal_return=0.03, live_delta=0.16, causal_delta=0.15)
        == STABLE
    )


def test_shake_label_flags_a_flipped_adopted_return() -> None:
    """채택 기본값이 플러스 → 마이너스로 넘어가면 WAN-119의 판정이 그 1분에 기댄 것이다."""
    assert (
        _shake_label(live_return=0.04, causal_return=-0.01, live_delta=0.16, causal_delta=0.15)
        == SHAKEN_RETURN
    )


def test_shake_label_flags_a_flipped_increment() -> None:
    """절대 수익률이 버텨도 볼린저 증분의 부호가 뒤집히면 흔들린 것이다."""
    assert (
        _shake_label(live_return=0.04, causal_return=0.03, live_delta=0.16, causal_delta=-0.02)
        == SHAKEN_DELTA
    )


def test_shake_label_does_not_care_about_magnitude() -> None:
    """크기로 판정하지 않는다 — 큰 손실이어도 부호가 살면 판정은 유지다.

    이 이슈의 질문은 「어느 밴드가 더 버나」가 **아니다**. 크기로 판정하면 정확성 문제가
    조용히 최적화 문제로 바뀐다(WAN-119 §6-1).
    """
    assert (
        _shake_label(live_return=0.04, causal_return=0.001, live_delta=0.16, causal_delta=0.001)
        == STABLE
    )


def test_verdict_reads_the_numbers_from_the_frame() -> None:
    """판정 문장이 표의 숫자에서 실제로 계산된다(사람이 손으로 적으면 재실행 때 갈라진다)."""
    rows = [
        _row(level="L1", symbol=s, total_return=-0.10) for s in ("BTC/USDT:USDT", "ETH/USDT:USDT")
    ]
    rows += [
        _row(level="L2i", symbol=s, total_return=0.05) for s in ("BTC/USDT:USDT", "ETH/USDT:USDT")
    ]
    rows += [
        _row(level="L2c", symbol=s, total_return=0.03) for s in ("BTC/USDT:USDT", "ETH/USDT:USDT")
    ]
    frame = per_symbol(rows)
    lines = verdict(incremental(frame), rung_summary(frame))
    assert len(lines) == 1
    assert "+5.00%" in lines[0] and "+3.00%" in lines[0]
    assert STABLE in lines[0]


def test_verdict_marks_a_shaken_cell() -> None:
    rows = [_row(level="L1", total_return=-0.10)]
    rows += [_row(level="L2i", total_return=0.05)]
    rows += [_row(level="L2c", total_return=-0.02)]
    frame = per_symbol(rows)
    lines = verdict(incremental(frame), rung_summary(frame))
    assert SHAKEN_RETURN in lines[0]


# --------------------------------------------------------------------------- #
# 집계 — 기준을 어디에 두나
# --------------------------------------------------------------------------- #


def test_selection_vs_price_is_measured_against_live_not_tap() -> None:
    """이 이슈의 대조는 `L2i`↔`L2c`다 — 기준이 `L2`면 다른 질문의 답이 나온다.

    WAN-119는 같은 헬퍼의 기준을 `L2`(채택 기본값)에 뒀지만, 여기서 재려는 손상은
    **지연 한 칸**의 몫이므로 기준이 `L2i`여야 한다.
    """
    rows = [_row(level="L2", total_return=0.99, num_trades=999)]
    rows += [_row(level="L2i", total_return=0.10, num_trades=100)]
    rows += [_row(level="L2c", total_return=0.08, num_trades=101)]
    lines = selection_vs_price(per_symbol(rows))
    assert len(lines) == 1
    # `L2i`(0.10) → `L2c`(0.08) = −2%p. `L2`가 기준이면 −91%p가 나온다.
    assert "-2.00%p" in lines[0]
    assert "가격" in lines[0]


def test_per_symbol_spread_counts_direction_not_just_the_mean() -> None:
    """WAN-119 §5의 「방향이 갈린다」를 6심볼로 올리는 칸 — 평균만 적으면 그게 안 보인다."""
    rows = [_row(level="L2i", symbol="BTC/USDT:USDT", total_return=0.10)]
    rows += [_row(level="L2i", symbol="ETH/USDT:USDT", total_return=0.10)]
    rows += [_row(level="L2c", symbol="BTC/USDT:USDT", total_return=0.12)]  # 인과가 나음
    rows += [_row(level="L2c", symbol="ETH/USDT:USDT", total_return=0.02)]  # 인과가 나쁨
    lines = per_symbol_spread(per_symbol(rows))
    assert "1/2" in lines[0]
    assert "BTC +2.00%p" in lines[0] and "ETH -8.00%p" in lines[0]


def test_incremental_pairs_deltas_per_symbol() -> None:
    """증분은 심볼별로 짝지어 뺀 뒤 평균한다 — 그래야 `symbols_up`을 셀 수 있다."""
    rows = [_row(level="L1", symbol="BTC/USDT:USDT", total_return=0.0)]
    rows += [_row(level="L1", symbol="ETH/USDT:USDT", total_return=0.0)]
    rows += [_row(level="L2c", symbol="BTC/USDT:USDT", total_return=0.10)]
    rows += [_row(level="L2c", symbol="ETH/USDT:USDT", total_return=-0.04)]
    steps = incremental(per_symbol(rows))
    row = steps[steps["step"] == "L1→L2c"].iloc[0]
    assert row["delta_return"] == pytest.approx(0.03)
    assert row["symbols_up"] == 1.0
    assert row["symbols"] == 2.0
