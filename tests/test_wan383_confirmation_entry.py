"""WAN-383 §0/§1: 확인 진입 트리거 관측 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 일곱:

1. **`P*`가 정확히 경계다** — 그 아래는 진한 빨강, 그 위는 아니다. 부등호를 한 칸 옮기거나
   `hist[1] ≥ 0` 갈래를 빠뜨리면 죽는다.
2. **관측 필드는 순수하다** — `observe_confirmation`을 켜도 후보·체결·손익·북 배치가 하나도
   안 움직인다(실데이터). 관측이 대상을 바꾸면 그 순간 이 측정은 무효다(WAN-328/372/376 선례).
3. **봉 안에서 잘라도 이미 발동한 트리거가 비트 동일하다** — WAN-377이 만든 절단 자를 그대로
   쓴다. 미래 봉을 보고 트리거를 매기면 이 테스트가 죽는다.
4. **재진입 거래도 관측을 받는다** — 채택 북은 재진입 ON이라(WAN-273) 한쪽만 배선하면 표가
   거래의 상당 부분을 조용히 놓친다. 🚨 **인자를 넘기는 줄이 아니라 재진입 후보에 실제로
   관측이 붙었는지**로 건다(WAN-345의 교훈).
5. **사다리가 오프셋 조회에 정확하다** — 팔 `C`의 첫 터치가 사다리에서 정확히(1bp 안) 나온다.
6. **「같은 1분」은 확인 팔에 유리한 쪽으로 분류된다** — 그래야 「닫아라」 판정이 순서 가정에
   안 기댄다.
7. **§2의 갈림이 코드다** — 이익 비중이 압도적이거나 도달률이 무너지거나 놓친 셋업이 오히려
   이겼으면 그 팔은 탈락한다(판정 문장이 아니라 반환값으로).
"""

from __future__ import annotations

import math
import random
from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest.book_cli import iter_book_segments
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import ExitReason, PositionSide, Trade, TradeFill
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan383_confirmation_entry import (
    ARM_BAR_CLOSE,
    ARM_CROSS,
    ARM_OFFSET,
    CAT_AFTER_SL,
    CAT_AFTER_TP,
    CAT_HOLDING,
    CAT_NO_TRIGGER,
    MIN_REACH_RATE,
    OVERWHELMING_GAIN_SHARE,
    arm_trigger,
    categorize,
    mean_cross_offset,
)
from backtest.zone_limit_backtest import (
    CONFIRMATION_MAX_OFFSET,
    ConfirmationProbe,
    build_zone_limit_candidates,
)
from strategy.realtime_macd import (
    MacdColor,
    RealtimeMacd,
    strong_red_exit_price,
)
from tests.test_wan377_intrabar_cut_invariance import (
    _SYNTHETIC_FIXTURES,
    _SYNTHETIC_TF,
    _engine_params,
    _synthetic_1m,
    aggregate_1m,
    cut_world_intrabar,
    intrabar_cuts_for,
)

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-10-01"


def _shared_kwargs() -> dict[str, Any]:
    """채택 좌표 그대로 — **핀 없음**(존폭 필터는 채택 기본값이 곧 「끔」이다, WAN-384)."""
    return {
        "start": _REAL_START,
        "end": _REAL_END,
        "jobs": 1,
        "cold_segments": False,
        "engine_check": False,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        **ADOPTED_CELL_KWARGS,
    }


def _skip_without_real_data() -> None:
    """🚨 게이트는 `run_cells` **호출 전에** 판정한다 — 안 그러면 CI의 빈 DB가 실패로 끝난다."""
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")


# --------------------------------------------------------------------------- #
# 1 · `P*`가 정확히 경계다
# --------------------------------------------------------------------------- #


def _seeded_macd(seed: int, bars: int = 80) -> tuple[RealtimeMacd, float]:
    rng = random.Random(seed)
    state = RealtimeMacd()
    price = 100.0
    for _ in range(bars):
        price *= 1.0 + rng.gauss(0.0, 0.012)
        state.commit(price)
    return state, price


@pytest.mark.parametrize("seed", range(12))
def test_p_star_is_exactly_the_strong_red_boundary(seed: int) -> None:
    """`P*` 바로 아래는 진한 빨강, 바로 위는 아니다 — 두 갈래(`hist[1]` 부호) 모두."""
    state, _price = _seeded_macd(seed)
    p_star = state.strong_red_exit_price()
    assert p_star is not None and p_star > 0.0

    below = state.value(p_star * (1.0 - 1e-9))
    above = state.value(p_star * (1.0 + 1e-9))
    assert below is not None and above is not None
    assert below.color is MacdColor.STRONG_RED, "P* 바로 아래가 진한 빨강이 아니다."
    assert above.color is not MacdColor.STRONG_RED, "P* 바로 위가 아직 진한 빨강이다."

    # `hist[1]`의 부호가 갈래를 정한다 — 두 갈래가 실제로 다른 목표를 푼다.
    assert state.closed_hist is not None
    at = state.value(p_star)
    assert at is not None
    expected = state.closed_hist if state.closed_hist < 0.0 else 0.0
    assert math.isclose(at.hist, expected, abs_tol=1e-12)


def test_p_star_is_strictly_increasing_in_price() -> None:
    """히스토그램이 현재가의 **순증가** 함수라는 것이 해의 유일성 근거다."""
    state, _price = _seeded_macd(7)
    samples = [state.value(p) for p in (90.0, 100.0, 110.0, 120.0)]
    hists = [s.hist for s in samples if s is not None]
    assert len(hists) == 4
    assert hists == sorted(hists) and len(set(hists)) == 4


def test_p_star_matches_the_closed_form_in_the_issue() -> None:
    """이슈 본문의 닫힌 식 `P* = (macd + 0.25·hist[1] − c) / 0.0797721`과 같은 값이다."""
    state, _price = _seeded_macd(0)
    assert state.fast_ema is not None and state.slow_ema is not None
    assert state.signal_ema is not None and state.closed_hist is not None
    assert state.closed_hist < 0.0, "이 픽스처는 `hist[1] < 0` 갈래를 재려고 고른 것이다."
    macd = state.fast_ema - state.slow_ema
    c = (11.0 / 13.0) * state.fast_ema - (25.0 / 27.0) * state.slow_ema
    k = 2.0 / 13.0 - 2.0 / 27.0
    expected = (macd + 0.25 * state.closed_hist - c) / k
    got = strong_red_exit_price(
        fast_ema=state.fast_ema,
        slow_ema=state.slow_ema,
        signal_ema=state.signal_ema,
        closed_hist=state.closed_hist,
    )
    assert math.isclose(got, expected, rel_tol=1e-12)


def test_p_star_is_none_during_warmup() -> None:
    """워밍업이면 값을 **지어내지 않는다** — `value`와 같은 계약."""
    state = RealtimeMacd()
    for _ in range(5):
        state.commit(100.0)
    assert not state.ready
    assert state.strong_red_exit_price() is None


# --------------------------------------------------------------------------- #
# 2 · 관측 필드는 순수하다 (실데이터)
# --------------------------------------------------------------------------- #


def _bare(candidates: Any) -> list[tuple[Any, ...]]:
    """관측 필드를 뺀 후보 지문 — 「관측이 대상을 안 바꿨나」를 재는 자."""
    return [
        (c.entry_time, c.entry_price, c.exit_time, c.exit_price, c.reason, c.stop_price, c.mfe_r)
        for c in candidates
    ]


def test_observation_field_moves_nothing() -> None:
    """켜도 후보·체결·청산이 **비트 단위로 같다** — 관측이 대상을 바꾸면 이 측정은 무효다."""
    _skip_without_real_data()
    off = run_cells([_REAL_SYMBOL], [_REAL_TF], **_shared_kwargs())
    on = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_confirmation=True, **_shared_kwargs())
    a = off[0].candidates[harness.SEGMENT_FULL]
    b = on[0].candidates[harness.SEGMENT_FULL]
    assert a, "후보가 없어 검사가 성립하지 않습니다."
    assert _bare(a) == _bare(b)
    assert all(c.confirmation is None for c in a), "안 켰는데 값이 실렸습니다."
    assert any(c.confirmation is not None for c in b), "켰는데 값이 안 실렸습니다."
    assert [r.model_dump() for r in off[0].rows] == [r.model_dump() for r in on[0].rows]


def test_book_placement_is_unchanged_by_the_observation() -> None:
    """북 배치(거래 수·수익·MDD)도 비트 단위로 같다 — 리포트 검산 (a)와 같은 자다."""
    _skip_without_real_data()
    start_ms, end_ms = parse_date_ms(_REAL_START), parse_date_ms(_REAL_END)

    def book_row(observe: bool) -> tuple[int, float, float]:
        payloads = run_cells(
            [_REAL_SYMBOL], [_REAL_TF], observe_confirmation=observe, **_shared_kwargs()
        )
        proxied, _note = apply_funding_proxy(payloads)
        segment = iter_book_segments(
            proxied,
            book=LeverageBookParams(),
            segments=[harness.SEGMENT_FULL],
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
            take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        )[0]
        return (segment.row.num_trades, segment.row.total_return, segment.row.max_drawdown)

    assert book_row(observe=False) == book_row(observe=True)


# --------------------------------------------------------------------------- #
# 3 · 재진입 거래도 관측을 받는다 (WAN-345 부류의 동작 가드)
# --------------------------------------------------------------------------- #


def test_reentry_candidates_also_carry_the_probe() -> None:
    """🚨 인자를 넘기는 줄이 아니라 **재진입 후보에 실제로 관측이 붙었는지**로 건다."""
    _skip_without_real_data()
    payloads = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_confirmation=True, **_shared_kwargs())
    reentries = payloads[0].reentry_candidates.get(harness.SEGMENT_FULL, ())
    if not reentries:
        pytest.skip("이 창에 재진입 후보가 없어 검사가 성립하지 않습니다.")
    missing = [c for c in reentries if c.confirmation is None]
    assert not missing, f"재진입 후보 {len(missing)}건이 관측 없이 흘렀습니다(WAN-345 부류)."


# --------------------------------------------------------------------------- #
# 4 · 봉 안에서 잘라도 이미 발동한 트리거가 비트 동일하다
# --------------------------------------------------------------------------- #


def _trigger_keys(candidates: Any, cut_ms: int) -> list[tuple[Any, ...]]:
    """절단 이전에 **이미 발동한** 트리거의 지문.

    자를 두 겹으로 좁힌다: ① 그 셋업의 청산이 절단 전에 끝났고(WAN-377 규약) ② 트리거도
    절단 전에 왔다. 그 둘은 절단 시점에 **이미 관측 가능한 사실**이라 미래를 안 봤다면
    비트 단위로 같아야 한다. 아직 안 온 트리거는 미래가 정하므로 당연히 비교 대상이 아니다.
    """
    keys: list[tuple[Any, ...]] = []
    for candidate in candidates:
        probe = candidate.confirmation
        if probe is None or candidate.reason is ExitReason.END_OF_DATA:
            continue
        if candidate.exit_time >= cut_ms:
            continue
        for time_ms, price in (
            (probe.bar_close_time, probe.bar_close_price),
            (probe.cross_time, probe.cross_price),
        ):
            if time_ms is not None and time_ms < cut_ms:
                keys.append((candidate.entry_time, candidate.entry_price, time_ms, price))
    return sorted(keys)


@pytest.mark.parametrize(("seed", "swing_period"), _SYNTHETIC_FIXTURES)
def test_triggers_survive_an_intrabar_cut(seed: int, swing_period: int) -> None:
    """「그 시점에 알 수 있던 것만」으로 다시 돌려도 이미 발동한 트리거가 **비트 동일**하다."""
    minutes = _synthetic_1m(seed, swing_period)
    params = _engine_params()
    cfg = harness.build_config(_SYNTHETIC_TF)

    def build(htf: pd.DataFrame, mins: pd.DataFrame) -> Any:
        candidates, _stats = build_zone_limit_candidates(
            htf, mins, _SYNTHETIC_TF, params=params, cfg=cfg, observe_confirmation=True
        )
        return candidates

    full = build(aggregate_1m(minutes, _SYNTHETIC_TF, allow_partial=False), minutes)
    assert any(c.confirmation is not None for c in full), "관측이 하나도 안 붙어 검사가 공허하다."

    compared = 0
    for cut in intrabar_cuts_for(full, _SYNTHETIC_TF):
        cut_htf, cut_1m = cut_world_intrabar(minutes, _SYNTHETIC_TF, cut)
        expected = _trigger_keys(full, cut)
        assert expected == _trigger_keys(build(cut_htf, cut_1m), cut), f"T={cut}에서 갈렸다."
        compared += len(expected)
    assert compared > 0, "비교한 트리거가 없어 이 테스트는 아무것도 안 지켰다."


# --------------------------------------------------------------------------- #
# 5 · 사다리·분류·판정 (순수 함수)
# --------------------------------------------------------------------------- #


def _probe(**kwargs: Any) -> ConfirmationProbe:
    base: dict[str, Any] = {"entry_time": 1_000, "entry_price": 100.0}
    base.update(kwargs)
    return ConfirmationProbe(**base)


def test_ladder_answers_any_offset_within_a_basis_point() -> None:
    """팔 `C`의 첫 터치가 사다리에서 정확히 나온다 — 성글게 해도 오차가 1bp 안이다."""
    probe = _probe(
        rise_ladder=((1_000, 100.5), (2_000, 101.0), (3_000, 103.0)),
    )
    for offset, expected_time in ((0.004, 1_000), (0.008, 2_000), (0.025, 3_000)):
        touch = probe.first_touch(offset)
        assert touch is not None
        assert touch[0] == expected_time
        assert math.isclose(touch[1], 100.0 * (1.0 + offset), rel_tol=1e-12)
    assert probe.first_touch(0.05) is None, "사다리가 못 닿은 수준은 지어내지 않는다."


def test_ladder_only_answers_what_it_actually_recorded() -> None:
    """사다리가 기록한 고점 안에서만 답한다 — 못 닿은 수준은 `None`이지 추정이 아니다."""
    probe = _probe(rise_ladder=((1_000, 100.0 * (1.0 + CONFIRMATION_MAX_OFFSET)),))
    assert probe.first_touch(CONFIRMATION_MAX_OFFSET) is not None
    assert probe.first_touch(CONFIRMATION_MAX_OFFSET * 2) is None


def _pair(exit_time: int, reason: ExitReason, probe: ConfirmationProbe | None) -> Any:
    trade = Trade(
        side=PositionSide.LONG,
        entry_time=1_000,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=0.0,
        exits=[TradeFill(time=exit_time, price=101.0, quantity=1.0, fee=0.0, reason=reason)],
        realized_pnl=1.0,
        return_pct=0.01,
    )
    placement = PlacedSetup(
        cell=("BTCUSDT", "1h"),
        equity=1_000.0,
        risk_amount=1.0,
        realized_pnl=1.0,
        stop_price=99.0,
        confirmation=probe,
    )
    return trade, placement


def test_same_minute_is_classified_generously_to_the_confirmation_arm() -> None:
    """트리거와 청산이 같은 1분이면 「아직 들고 있음」이다 — 순서를 모르기 때문이다.

    그래야 그러고도 판정이 「닫아라」로 나오면 그 판정이 순서 가정에 안 기댄다.
    """
    probe = _probe(cross_time=5_000, cross_price=100.5, cross_ref_price=100.0)
    assert categorize(_pair(5_000, ExitReason.TAKE_PROFIT, probe), ARM_CROSS, offset=0.0) == (
        CAT_HOLDING
    )
    assert categorize(_pair(4_999, ExitReason.TAKE_PROFIT, probe), ARM_CROSS, offset=0.0) == (
        CAT_AFTER_TP
    )
    assert categorize(_pair(4_999, ExitReason.STOP_LOSS, probe), ARM_CROSS, offset=0.0) == (
        CAT_AFTER_SL
    )
    assert categorize(_pair(9_999, ExitReason.TAKE_PROFIT, None), ARM_CROSS, offset=0.0) == (
        CAT_NO_TRIGGER
    )


def test_arm_two_never_fills_below_the_market() -> None:
    """`P*`가 이미 현재가 아래면 진입가는 `P*`가 아니라 **현재가**다(없는 이점 금지)."""
    probe = _probe(cross_time=5_000, cross_price=99.0, cross_ref_price=100.4)
    trigger = arm_trigger(probe, ARM_CROSS, offset=0.0)
    assert trigger == (5_000, 100.4)


def test_bar_close_arm_needs_both_time_and_price() -> None:
    probe = _probe(bar_close_time=5_000, bar_close_price=None)
    assert arm_trigger(probe, ARM_BAR_CLOSE, offset=0.0) is None
    probe = _probe(bar_close_time=5_000, bar_close_price=100.7)
    assert arm_trigger(probe, ARM_BAR_CLOSE, offset=0.0) == (5_000, 100.7)


def test_arm_c_offset_beyond_the_ladder_cap_is_refused() -> None:
    """팔 `2`의 평균 거리가 사다리 상한을 넘으면 조용히 답하지 않고 **거부**한다."""

    class _Segment:
        segment = harness.SEGMENT_FULL

        @staticmethod
        def trades_with_placements() -> list[Any]:
            probe = _probe(cross_time=2_000, cross_price=120.0, cross_ref_price=100.0)
            return [_pair(3_000, ExitReason.TAKE_PROFIT, probe)]

    with pytest.raises(ValueError, match="사다리 상한"):
        mean_cross_offset([_Segment()], segment=harness.SEGMENT_FULL)  # type: ignore[list-item]


def test_arm_c_offset_is_clamped_at_zero() -> None:
    """음수 평균 거리는 「위에 거는 트리거」로 표현되지 않으므로 0에서 자른다."""

    class _Segment:
        segment = harness.SEGMENT_FULL

        @staticmethod
        def trades_with_placements() -> list[Any]:
            probe = _probe(cross_time=2_000, cross_price=90.0, cross_ref_price=95.0)
            return [_pair(3_000, ExitReason.TAKE_PROFIT, probe)]

    assert mean_cross_offset([_Segment()], segment=harness.SEGMENT_FULL) == 0.0  # type: ignore[list-item]


# --------------------------------------------------------------------------- #
# 6 · §2의 갈림이 코드다 (문장이 아니라 반환값으로)
# --------------------------------------------------------------------------- #


def _census_frame(*, gain_share: float, arm: str = ARM_CROSS) -> pd.DataFrame:
    rows = []
    for category in (CAT_AFTER_TP, CAT_AFTER_SL, CAT_HOLDING, CAT_NO_TRIGGER):
        rows.append(
            {
                "segment": "oos_warm",
                "axis": "overall",
                "bucket": "전체",
                "arm": arm,
                "category": category,
                "num_trades": 100,
                "trade_share": 0.25,
                "net_r_sum": 1.0,
                "gain_share": gain_share if category == CAT_AFTER_TP else 0.1,
                "loss_share": 0.25,
                "win_rate": 0.5,
            }
        )
    return pd.DataFrame(rows)


def _reach_frame(
    *, reach_rate: float, missed_mean_net_r: float | None, arm: str = ARM_CROSS
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "segment": "oos_warm",
                "axis": "overall",
                "bucket": "전체",
                "arm": arm,
                "num_trades": 400,
                "triggered": int(400 * reach_rate),
                "reach_rate": reach_rate,
                "same_minute": 0,
                "median_rise_pct": 0.004,
                "median_stop_multiple": 1.8,
                "no_rise_share": 0.1,
                "missed_trades": 100,
                "missed_win_rate": 0.4,
                "missed_mean_net_r": missed_mean_net_r,
                "window_closed_share": 0.9,
            }
        ]
    )


@pytest.mark.parametrize(
    ("gain_share", "reach_rate", "missed_mean", "alive", "why"),
    [
        (OVERWHELMING_GAIN_SHARE + 0.01, 0.6, -0.5, False, "이익 비중이 압도적"),
        (0.1, MIN_REACH_RATE - 0.01, -0.5, False, "도달률 붕괴"),
        (0.1, 0.6, +0.5, False, "놓친 셋업이 오히려 이겼다"),
        (0.1, 0.6, -0.5, True, "생존"),
    ],
)
def test_the_gate_is_code_not_a_sentence(
    gain_share: float, reach_rate: float, missed_mean: float, alive: bool, why: str
) -> None:
    """§2가 착수 전에 못 박은 네 갈림이 **반환값**으로 갈린다."""
    from backtest.wan383_confirmation_entry import arm_verdict

    got, sentence = arm_verdict(
        _census_frame(gain_share=gain_share),
        _reach_frame(reach_rate=reach_rate, missed_mean_net_r=missed_mean),
        arm=ARM_CROSS,
        segment="oos_warm",
        base_mean_net_r=-0.12,
    )
    assert got is alive, f"{why}: 기대 {alive}인데 {got}"
    assert ("생존" in sentence) is alive


def test_no_survivor_closes_phase_two() -> None:
    """살아남은 팔이 없으면 요약이 「Phase 2를 안 돌리고 닫는다」를 낸다 — 그것도 결론이다."""
    from backtest.wan383_confirmation_entry import verdict

    census = pd.concat(
        [_census_frame(gain_share=0.9, arm=arm) for arm in (ARM_BAR_CLOSE, ARM_CROSS, ARM_OFFSET)]
    )
    reach = pd.concat(
        [
            _reach_frame(reach_rate=0.6, missed_mean_net_r=-0.5, arm=arm)
            for arm in (ARM_BAR_CLOSE, ARM_CROSS, ARM_OFFSET)
        ]
    )
    sentences, survivors = verdict(census, reach, [], segment="oos_warm")
    assert survivors == []
    assert len(sentences) == 3


# --------------------------------------------------------------------------- #
# 7 · 격자를 두 번 돌지 않는다 (비용 가드)
# --------------------------------------------------------------------------- #


def test_the_cli_builds_the_grid_exactly_once() -> None:
    """🚨 `main`이 요약을 쓰려고 후보를 **다시** 만들면 실행 시간이 두 배가 된다.

    이 좌표에서 비용의 전부가 후보 생성이라(WAN-372 실측: 48칸 8,156초 중 8,148초) 그
    실수는 몇 시간짜리다. `run_report`가 북을 함께 돌려주므로 `main`은 그것을 쓰면 된다 —
    라벨이 아니라 **AST에 그 호출이 없다**로 건다.
    """
    import ast
    import inspect

    from backtest import wan383_confirmation_entry as module

    tree = ast.parse(inspect.getsource(module.main))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "build_payloads" not in called, "main이 격자를 다시 만듭니다(비용 두 배)."
    assert "place_book" not in called, "main이 북을 다시 배치합니다."
    assert "run_report" in called
