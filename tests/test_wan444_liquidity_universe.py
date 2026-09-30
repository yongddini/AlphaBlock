"""WAN-444 — 거래대금 상위 목록(인과) · 판정 두 조건 · 겹침."""

from __future__ import annotations

import dataclasses

import numpy as np

from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq

DAY = m.DAY_MS


def _daily(closes: list[float], volumes: list[float]) -> m.Daily:
    ot = np.arange(len(closes), dtype=np.int64) * DAY
    cl = np.asarray(closes, dtype=float)
    return m.Daily(ot, cl, np.asarray(volumes, dtype=float) * cl)


def test_constants_are_the_issue_spec() -> None:
    assert liq.LIQ_DAYS == 30


def test_liquidity_is_mean_quote_of_last_30_days() -> None:
    n = 200
    d = _daily([10.0] * n, [1.0] * (n - 30) + [3.0] * 30)
    assert liq.liquidity(d, n - 1) == 30.0  # 마지막 30봉만(3 × 10)
    assert liq.liquidity(d, n - 31) == 10.0


def test_select_is_causal_future_bars_do_not_matter() -> None:
    n = 300
    a = {"A": _daily([10.0] * n, [2.0] * n), "B": _daily([10.0] * n, [1.0] * n)}
    moved = {"A": a["A"], "B": _daily([10.0] * n, [1.0] * 250 + [100.0] * 50)}
    t = 250 * DAY  # 250일 봉은 아직 안 닫혔다
    assert liq.select_liquidity(a, t, n=1) == liq.select_liquidity(moved, t, n=1) == {"A"}
    assert liq.select_liquidity(moved, 290 * DAY, n=1) == {"B"}


def test_select_top_bottom_and_eligibility() -> None:
    n = 200
    daily = {
        "BIG": _daily([10.0] * n, [5.0] * n),
        "MID": _daily([10.0] * n, [3.0] * n),
        "SMALL": _daily([10.0] * n, [1.0] * n),
        "YOUNG": _daily([10.0] * 30, [99.0] * 30),  # 상장 90일 미만 — 자격 없음
    }
    t = n * DAY
    assert liq.select_liquidity(daily, t, n=2) == {"BIG", "MID"}
    assert liq.select_liquidity(daily, t, n=2, top=False) == {"SMALL", "MID"}


def _pick(config: str, front: float, back: float, stage: str) -> m.Pick:
    return m.Pick(
        mode=m.MODE_DROP,
        config=config,
        stage=stage,
        trades=10,
        forced=0,
        front_risk=0.02,
        front_cagr=front,
        back_ratio=back,
        back_return=0.1,
        back_mdd=0.1,
        back_cagr=0.05,
        back_stop_ratio=back,
        full_risk=0.02,
        full_cagr=0.1,
        full_stop_cagr=0.1,
        fixed_cagr=0.1,
        fixed_mdd=0.3,
    )


def test_verdict_needs_both_back_rank_and_front_median() -> None:
    randoms = [_pick(f"r{i}", 0.10 + i * 0.001, 1.0, liq.STAGE_RANDOM) for i in range(20)]
    ok, lines = liq.verdict(_pick("x", 0.2, 2.0, liq.STAGE_RULE), randoms)
    assert ok and len(lines) == 2
    assert not liq.verdict(_pick("x", 0.05, 2.0, liq.STAGE_RULE), randoms)[0]  # 앞구간 미달
    assert not liq.verdict(_pick("x", 0.2, 0.5, liq.STAGE_RULE), randoms)[0]  # 뒷구간 미달
    tie = [dataclasses.replace(r, back_ratio=2.0) if i < 2 else r for i, r in enumerate(randoms)]
    assert not liq.verdict(_pick("x", 0.2, 2.0, liq.STAGE_RULE), tie)[0]  # 같은 값 2개 = 3등


def test_overlap_counts_hand_picked_members() -> None:
    hand = sorted(liq.SYMBOLS_31)
    rot = m.Rotation((0, 1), (frozenset(hand[:10]), frozenset([*hand[:5], "X/USDT:USDT"])))
    assert liq.overlap_with_31(rot) == 7.5
    share = dict(liq.membership_share(rot))
    assert share[hand[0]] == 1.0 and share["X/USDT:USDT"] == 0.5
