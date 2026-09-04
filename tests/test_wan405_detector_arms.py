"""WAN-405: 탐지기 축·겹침 게이트의 **배선**을 동작으로 고정한다.

라벨이 아니라 동작으로 거는 것이 요점이다 — 이 저장소가 반복해 데인 실패가
「축을 켰다고 믿는데 안 켜진 것」과 그 거울인 「안 켰다고 믿는데 켜진 것」이다
(WAN-91/95/112/123/159/305).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import (
    _Task,
    lux_bullish_taps,
    overlap_gated_candidates,
    run_cells,
    zones_overlap,
)
from backtest.wan366_causal_ablation import (
    ADOPTED_RETAP,
    BASE_ARM,
    DETECTOR_ARMS,
    DETECTOR_ARMS_BY_NAME,
    DETECTOR_CSV_KEYS,
    DETECTOR_LOO_KEYS,
    GATE_ARM,
    LUX_ARM,
    RANK_EDGES,
    RAW_RETAP,
    primary_retap_view,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import OrderBlock, OrderBlockDirection


def _zone(top: float, bottom: float, *, confirmed: int = 0) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=confirmed,
        confirmed_time=confirmed,
        ob_volume=1.0,
        ob_low_volume=0.0,
        ob_high_volume=0.0,
    )


def _candidate(zone: OrderBlock, trigger: int) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=trigger,
        entry_price=zone.top,
        exit_time=trigger + 1,
        exit_price=zone.top,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=zone.bottom,
        order_block=zone,
        trigger_time=trigger,
    )


# --------------------------------------------------------------------------- #
# 1. 게이트의 정의 — 「겹침 > 0」이고 문턱이 없다
# --------------------------------------------------------------------------- #


def test_touching_zones_do_not_count_as_overlap() -> None:
    """닿기만 한 것(겹침 폭 0)은 겹침이 **아니다** — 사용자 결정이 「겹침 > 0」이다."""
    assert zones_overlap(10.0, 9.0, 9.5, 8.0)
    assert not zones_overlap(10.0, 9.0, 9.0, 8.0)
    assert not zones_overlap(10.0, 9.0, 8.9, 8.0)


def test_gate_keeps_only_candidates_whose_bar_has_an_overlapping_lux_tap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트는 **같은 봉 ＋ 가격 겹침** 둘을 함께 요구한다."""
    kept_zone = _zone(10.0, 9.0)
    same_bar_no_overlap = _zone(20.0, 19.0)
    other_bar = _zone(10.0, 9.0)
    candidates = [
        _candidate(kept_zone, 100),
        _candidate(same_bar_no_overlap, 100),
        _candidate(other_bar, 200),
    ]
    monkeypatch.setattr(
        "backtest.wan169_leverage_book.lux_bullish_taps",
        lambda _window: {100: [(9.5, 8.0)]},
    )
    kept = overlap_gated_candidates(candidates, object())  # type: ignore[arg-type]
    assert [c.trigger_time for c in kept] == [100]
    assert kept[0] is candidates[0], "게이트는 **고르기만** 해야 한다(후보를 만들지 않는다)."


def test_gate_refuses_candidates_without_a_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """근거 존이 없으면 겹침을 판정할 수 없다 — 조용히 버리지 않고 죽는다."""
    zone = _zone(10.0, 9.0)
    broken = _candidate(zone, 100).__class__(
        **{**_candidate(zone, 100).__dict__, "order_block": None}
    )
    monkeypatch.setattr(
        "backtest.wan169_leverage_book.lux_bullish_taps", lambda _window: {100: [(9.5, 8.0)]}
    )
    with pytest.raises(AssertionError, match="근거 오더블록"):
        overlap_gated_candidates([broken], object())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 2. 축 배선 — 기본값이면 예전 그대로, 잘못 조합하면 거부
# --------------------------------------------------------------------------- #


def test_task_defaults_are_the_adopted_detector_and_no_gate() -> None:
    """「아무것도 안 하면 페이퍼와 같은 선상」(WAN-305)."""
    task = _Task(symbol="X", timeframe="1h", start_ms=0, end_ms=1)
    assert task.detector == harness.ADOPTED_DETECTOR == "flux"
    assert task.overlap_gate is False


def test_gate_is_rejected_on_the_lux_arm() -> None:
    """「lux 존을 lux 탭으로 거른다」는 뜻이 없다."""
    with pytest.raises(ValueError, match="overlap_gate"):
        run_cells(
            ["BTCUSDT"],
            ["1h"],
            start="2024-01-01",
            end="2024-02-01",
            detector="lux",
            overlap_gate=True,
        )


def test_gate_is_rejected_with_shorts() -> None:
    """판정 축은 롱이다 — 숏 후보를 강세 탭으로 거르면 라벨이 거짓이 된다."""
    with pytest.raises(ValueError, match="롱 축 전용"):
        run_cells(
            ["BTCUSDT"],
            ["1h"],
            start="2024-01-01",
            end="2024-02-01",
            overlap_gate=True,
            short_enabled=True,
        )


def test_lux_taps_are_bullish_only() -> None:
    """게이트가 보는 것은 **강세** 탭이다(판정 축, WAN-405 §③)."""
    step = 3_600_000
    rows = []
    for i in range(60):
        base = 100.0 if i <= 3 else (100.0 - (i - 3) * 3.0 if i <= 8 else 85.0 + (i - 8) * 2.0)
        rows.append((base, base + 1.0, base - 1.0, base, 99.0 if i == 20 else 1.0))
    frame = pd.DataFrame(
        {
            "open_time": [i * step for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [r[4] for r in rows],
            "closed": [True] * len(rows),
        }
    )
    market = harness.MarketData(
        symbol="TEST", timeframe="1h", htf_df=frame, df_1m=pd.DataFrame(), funding_rates=[]
    )
    taps = lux_bullish_taps(market)
    lux = harness.detect_order_blocks(market, detector="lux")
    bullish = {
        s.trigger_time
        for s in lux.retap_signals
        if s.order_block.direction is OrderBlockDirection.BULLISH
    }
    assert set(taps) == bullish


# --------------------------------------------------------------------------- #
# 3. §3 팔 정의 — 축이 둘뿐이고 나머지는 `L0` 그대로
# --------------------------------------------------------------------------- #


def test_arms_change_exactly_one_thing_each() -> None:
    """기준 팔은 채택 탐지기 · 게이트 끔이고, 다른 두 팔은 축을 **하나씩만** 켠다."""
    base = DETECTOR_ARMS_BY_NAME[BASE_ARM]
    lux = DETECTOR_ARMS_BY_NAME[LUX_ARM]
    gate = DETECTOR_ARMS_BY_NAME[GATE_ARM]
    assert (base.detector, base.overlap_gate) == (harness.ADOPTED_DETECTOR, False)
    assert (lux.detector, lux.overlap_gate) == ("lux", False)
    assert (gate.detector, gate.overlap_gate) == (harness.ADOPTED_DETECTOR, True)
    # 생성 그룹이 셋이어야 한다 — 같으면 두 팔이 payload를 공유해 라벨만 다른 같은
    # 숫자가 나온다(WAN-149가 못 박은 함정).
    assert len({a.gen for a in DETECTOR_ARMS}) == len(DETECTOR_ARMS)


def test_rank_edges_include_the_original_render_count() -> None:
    """원본 파인이 그리는 개수(3)가 절단점에 있어야 그 표가 답이 된다."""
    assert 3 in RANK_EDGES


# --------------------------------------------------------------------------- #
# 4. 재탭 축 — 「생짜」는 존당 한 번이다 (사용자 결정 2026-09-03)
# --------------------------------------------------------------------------- #


def test_raw_arms_block_retaps_by_default() -> None:
    """§3의 기본 재탭 정책이 `once`(존당 한 번)인가 — 🚨 채택 기본값과 **다르다**.

    「생짜로 얼마나 먹나」를 묻는데 같은 존에 몇 번씩 들어가면 그 질문이 흐려진다. 특히
    LuxAlgo는 존이 2.6배 많고 얇아 재탭이 거래 수를 크게 부풀린다.
    """
    from strategy.models import ConfluenceParams

    assert RAW_RETAP == "once"
    assert ADOPTED_RETAP == ConfluenceParams().retap_mode == "every_tap"
    assert RAW_RETAP != ADOPTED_RETAP, "두 판이 같아지면 이 축의 라벨이 뜻을 잃는다."


def test_retap_mode_is_part_of_the_row_key() -> None:
    """두 판이 한 CSV에 살 수 있으므로 **키에 들어가야** 덮어쓰지 않는다.

    🚨 키에서 빠지면 `once` 판이 `every_tap` 판을 **조용히 덮는다** — 그러면 「어느 표를
    읽고 있는지」를 알 수 없게 된다(`timeframes` 축에서 WAN-316/330이 데인 자리).
    """
    assert "retap_mode" in DETECTOR_CSV_KEYS
    assert "retap_mode" in DETECTOR_LOO_KEYS


def test_summary_reads_only_the_judgment_view() -> None:
    """렌더가 판정 판(`once`)만 본다 — 안 거르면 두 판이 한 표에 섞인다."""
    frame = pd.DataFrame(
        {
            "arm": [BASE_ARM, BASE_ARM],
            "retap_mode": [RAW_RETAP, ADOPTED_RETAP],
            "timeframes": ["1h", "1h"],
            "segment": ["oos_warm", "oos_warm"],
            "mean_net_r": [-0.1, -0.2],
        }
    )
    view = primary_retap_view(frame)
    assert list(view["retap_mode"]) == [RAW_RETAP]
    assert list(view["mean_net_r"]) == [-0.1]
