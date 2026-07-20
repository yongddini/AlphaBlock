"""WAN-115 룩어헤드 재검의 사다리 정의·집계·판정 테스트 (실데이터 없이 합성 행으로).

이 리포트가 **라벨과 실제 실행이 갈라질** 자리들을 고정한다: 세 단이 오직 `band_bar`만
다른지(다른 게 같이 움직이면 "룩어헤드의 몫"이라는 말이 거짓이 된다), `L1`·`L2`가 정말
WAN-114의 같은 단인지(아니면 "그 CSV와 검산된다"가 거짓말이다), `L2`가 채택 기본값이고
`L2p`가 그 교정인지, 그리고 판정 문장이 표의 숫자에서 실제로 계산되는지.
"""

from __future__ import annotations

import pytest

from backtest.harness import LEGACY_BAND_BAR, LEGACY_RSI_GATE_MODE, fill_preset
from backtest.wan114_entry_rule_ablation import RUNGS_BY_NAME as WAN114_RUNGS
from backtest.wan114_entry_rule_ablation import rung_params as wan114_rung_params
from backtest.wan115_bollinger_lookahead_recheck import (
    BASE_RUNG,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_TIMEFRAMES,
    LADDER,
    NO_BASE_LABEL,
    PREV_RUNG,
    RUNGS,
    RUNGS_BY_NAME,
    TAP_RUNG,
    RecheckRow,
    _verdict_label,
    adopted_delta,
    incremental,
    per_symbol,
    rung_params,
    rung_summary,
    segments,
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
) -> RecheckRow:
    return RecheckRow(
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


def test_ladder_is_the_three_rungs_in_order() -> None:
    assert LADDER == ("L1", "L2", "L2p")
    assert (BASE_RUNG, TAP_RUNG, PREV_RUNG) == ("L1", "L2", "L2p")


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


def test_prev_rung_differs_from_tap_only_by_band_bar() -> None:
    """`L2` → `L2p`가 **오직 `band_bar`만** 다르다.

    이게 깨지면 이 리포트의 `L2→L2p` 차이를 "룩어헤드의 몫"이라고 부를 수 없다 —
    두 부품이 같이 움직인 값이 된다.
    """
    tap = rung_params(RUNGS_BY_NAME[TAP_RUNG], fill=_BASELINE)
    prev = rung_params(RUNGS_BY_NAME[PREV_RUNG], fill=_BASELINE)
    assert tap.deviation_filter is not None
    assert prev.deviation_filter is not None
    assert tap.deviation_filter.band_bar == "tap"
    assert prev.deviation_filter.band_bar == "prev_closed"
    # `band_bar`만 되돌리면 완전히 같은 파라미터여야 한다(다른 필드는 손대지 않았다).
    assert (
        prev.model_copy(
            update={
                "deviation_filter": prev.deviation_filter.model_copy(update={"band_bar": "tap"})
            }
        )
        == tap
    )


def test_base_rung_has_bollinger_off() -> None:
    """`L1`은 볼린저가 꺼진 바닥 — 증분의 기준선이다."""
    assert rung_params(RUNGS_BY_NAME[BASE_RUNG], fill=_BASELINE).deviation_filter is None


@pytest.mark.parametrize("level", ["L1", "L2"])
def test_shared_rungs_match_wan114_bit_for_bit(level: str) -> None:
    """`L1`·`L2`가 WAN-114의 같은 단과 **완전히 같은 파라미터**다.

    이 동치가 이 리포트의 검산 근거다("`wan114_entry_ablation.csv`의 같은 단과 일치").
    깨지면 두 표의 `L1→L2`를 비교할 수 없고, 이슈가 인용한 +16.20%p도 이 표와 무관해진다.
    """
    mine = rung_params(RUNGS_BY_NAME[level], fill=_BASELINE)
    theirs = wan114_rung_params(WAN114_RUNGS[level], fill=_BASELINE)
    assert mine == theirs


def test_window_is_pinned_like_wan114() -> None:
    """창이 미끄러지면(`--years N`) 두 리포트가 다른 기간을 봐 검산이 깨진다."""
    from backtest.wan114_entry_rule_ablation import DEFAULT_END as W114_END
    from backtest.wan114_entry_rule_ablation import DEFAULT_START as W114_START

    assert (DEFAULT_START, DEFAULT_END) == (W114_START, W114_END)


def test_segments_are_is_two_thirds_then_oos() -> None:
    is_seg, oos_seg = segments()
    assert (is_seg.name, oos_seg.name) == ("is", "oos")
    assert is_seg.start_fraction == 0.0
    assert is_seg.end_fraction == oos_seg.start_fraction
    assert oos_seg.end_fraction == 1.0


# --------------------------------------------------------------------------- #
# 집계 — 증분이 심볼별로 짝지어지나
# --------------------------------------------------------------------------- #


def test_incremental_pairs_deltas_per_symbol() -> None:
    """증분은 두 단의 **같은 심볼**끼리 뺀 값의 평균이다."""
    rows = [
        _row(level="L1", symbol="BTC/USDT:USDT", total_return=0.0),
        _row(level="L2", symbol="BTC/USDT:USDT", total_return=0.20),
        _row(level="L2p", symbol="BTC/USDT:USDT", total_return=0.05),
        _row(level="L1", symbol="ETH/USDT:USDT", total_return=0.0),
        _row(level="L2", symbol="ETH/USDT:USDT", total_return=0.10),
        _row(level="L2p", symbol="ETH/USDT:USDT", total_return=-0.05),
    ]
    steps = incremental(per_symbol(rows)).set_index("step")
    assert float(steps.loc["L1→L2", "delta_return"]) == pytest.approx(0.15)
    assert float(steps.loc["L1→L2p", "delta_return"]) == pytest.approx(0.0)
    # BTC만 교정 후에도 플러스 — 평균이 0이어도 방향은 갈린다.
    assert int(steps.loc["L1→L2p", "symbols_up"]) == 1
    assert int(steps.loc["L1→L2p", "symbols"]) == 2


def test_incremental_only_measures_steps_from_the_base_rung() -> None:
    """증분은 `L1→L2`·`L1→L2p` 둘뿐 — `L2→L2p`는 두 증분의 차로 읽는다."""
    rows = [_row(level=level) for level in LADDER]
    assert set(incremental(per_symbol(rows))["step"]) == {"L1→L2", "L1→L2p"}


def test_per_symbol_folds_seeds_before_symbol_average() -> None:
    """시드를 심볼 **안에서** 먼저 접는다(WAN-111 순서). 안 그러면 델타를 짝지을 수 없다."""
    rows = [
        _row(level="L2p", fill="pen_5bp_drop_50", seed=seed, total_return=value)
        for seed, value in enumerate((0.0, 0.2))
    ]
    frame = per_symbol(rows)
    assert len(frame) == 1
    assert float(frame.iloc[0]["total_return"]) == pytest.approx(0.1)
    assert int(frame.iloc[0]["seeds"]) == 2


# --------------------------------------------------------------------------- #
# 판정 — 문장이 숫자에서 나오나
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kept", "expected"),
    [
        (1.0, "(a) 대체로 유지"),
        (0.70, "(a) 대체로 유지"),
        (0.50, "(b) 상당 부분 소멸"),
        (0.30, "(b) 상당 부분 소멸"),
        (0.10, "(c) 전부 소멸"),
        (-0.5, "(c) 전부 소멸"),
    ],
)
def test_verdict_label_maps_kept_ratio_to_issue_branches(kept: float, expected: str) -> None:
    """이슈가 문장으로 요구한 (a)/(b)/(c)를 잔존 비율에서 기계적으로 낸다."""
    assert _verdict_label(kept, base=0.2) == expected


@pytest.mark.parametrize("base", [0.0, -0.0118])
def test_verdict_label_refuses_to_judge_when_the_base_increment_is_not_positive(
    base: float,
) -> None:
    """기준 증분이 0 이하면 잔존 비율이 뜻을 잃는다 — 판정하지 않는다.

    1h가 실제로 이 셀이다(`L1→L2` = −1.18%p). 교정이 **더 깎는데도**(−2.03%p) 비율은
    172%라 가드가 없으면 "(a) 대체로 유지"로 찍힌다 — "유지될 플러스가 없다"를 "잘
    유지됐다"로 읽게 만드는 부호 함정이다.
    """
    assert _verdict_label(1.72, base=base) == NO_BASE_LABEL


def test_verdict_reads_kept_ratio_and_lookahead_share_from_the_numbers() -> None:
    """판정 문장의 잔존 비율·룩어헤드 몫이 표의 숫자에서 실제로 계산된다."""
    rows = [
        _row(level="L1", timeframe="15m", total_return=0.0),
        _row(level="L2", timeframe="15m", total_return=0.20),
        _row(level="L2p", timeframe="15m", total_return=0.05),
    ]
    steps = incremental(per_symbol(rows))
    line = "\n".join(verdict(steps, segment="oos"))
    assert "+20.00%p" in line  # 룩어헤드 포함 증분
    assert "+5.00%p" in line  # 교정 후 증분
    assert "25.0% 잔존" in line  # 0.05 / 0.20
    assert "-15.00%p" in line  # 룩어헤드의 몫
    assert "(c) 전부 소멸" in line


def test_verdict_omits_kept_ratio_when_the_base_increment_is_negative() -> None:
    """볼린저가 원래 값을 깎던 셀(1h)에서는 잔존 비율을 아예 적지 않는다."""
    rows = [
        _row(level="L1", timeframe="1h", total_return=0.0),
        _row(level="L2", timeframe="1h", total_return=-0.0118),
        _row(level="L2p", timeframe="1h", total_return=-0.0203),
    ]
    line = "\n".join(verdict(incremental(per_symbol(rows)), segment="oos"))
    assert "잔존" not in line
    assert NO_BASE_LABEL in line
    assert "-0.85%p" in line  # 룩어헤드의 몫은 부호와 무관하게 읽을 수 있다


def test_verdict_kept_ratio_precision_does_not_contradict_the_label() -> None:
    """경계값(0.6988)이 "70% 잔존"으로 반올림되면 (b) 라벨과 자기모순처럼 읽힌다.

    이 셀이 실제 15m OOS다(+11.32 / +16.20 = 69.88%).
    """
    rows = [
        _row(level="L1", timeframe="15m", total_return=0.0),
        _row(level="L2", timeframe="15m", total_return=0.1620),
        _row(level="L2p", timeframe="15m", total_return=0.1132),
    ]
    line = "\n".join(verdict(incremental(per_symbol(rows)), segment="oos"))
    assert "69.9% 잔존" in line
    assert "(b) 상당 부분 소멸" in line


def test_verdict_only_reports_the_official_lens() -> None:
    """공식 렌즈는 `baseline`(토대 2) — 민감도 렌즈는 판정 문장에 섞이지 않는다."""
    rows = [
        _row(level=level, fill="pen_5bp", total_return=value)
        for level, value in (("L1", 0.0), ("L2", 0.2), ("L2p", 0.2))
    ]
    assert verdict(incremental(per_symbol(rows)), segment="oos") == []


def test_adopted_delta_reports_absolute_move_of_the_default() -> None:
    """`L2` → `L2p`는 채택 기본값 **성적표**의 이동이다(증분과 다른 질문)."""
    rows = [
        _row(level="L2", timeframe="15m", total_return=0.20),
        _row(level="L2p", timeframe="15m", total_return=0.05),
    ]
    line = "\n".join(adopted_delta(rung_summary(per_symbol(rows)), segment="oos"))
    assert "+20.00%" in line
    assert "+5.00%" in line
    assert "-15.00%p" in line


def test_rung_summary_counts_positive_symbols() -> None:
    """평균 옆의 `positive` — 평균만 보면 심볼 하나가 끌어올린 게 안 보인다(WAN-111)."""
    rows = [
        _row(level="L2p", symbol="BTC/USDT:USDT", total_return=0.5),
        _row(level="L2p", symbol="ETH/USDT:USDT", total_return=-0.1),
        _row(level="L2p", symbol="SOL/USDT:USDT", total_return=-0.1),
    ]
    view = rung_summary(per_symbol(rows)).set_index("level")
    assert int(view.loc["L2p", "positive"]) == 1
    assert int(view.loc["L2p", "symbols"]) == 3


def test_default_timeframes_are_the_working_pair() -> None:
    """WAN-107 공동 작업 TF — 후속 이슈는 두 축을 병기해야 한다."""
    assert DEFAULT_TIMEFRAMES == ("15m", "1h")


def test_every_rung_is_zone_limit_with_realtime_rsi() -> None:
    """진입 방식·RSI 판정은 한 세트다(WAN-41/95) — 단마다 갈라지면 축이 오염된다."""
    for rung in RUNGS:
        params = rung_params(rung, fill=_BASELINE)
        assert params.entry_mode == "zone_limit"
        assert params.rsi_mode == "realtime"
