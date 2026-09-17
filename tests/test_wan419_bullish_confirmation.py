"""WAN-419 — 양봉 확인 라벨링: 갈래 · 인과 · 동가 · 판정 관문 순서를 **동작으로** 고정한다."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backtest import wan419_bullish_confirmation as mod

TF_MS = 3_600_000  # 1h
T0 = 1_700_000_000_000 - (1_700_000_000_000 % TF_MS)


def _bars(colors: list[str], start: int = T0) -> pd.DataFrame:
    """봉 색 목록 → 시가·종가. `u` 양봉 · `d` 음봉 · `=` 동가."""
    rows = []
    for i, c in enumerate(colors):
        o = 100.0
        close = {"u": 101.0, "d": 99.0, "=": 100.0}[c]
        rows.append({"open_time": start + i * TF_MS, "open": o, "close": close})
    return pd.DataFrame(rows)


def _setup(
    *,
    entry_offset_min: int = 5,
    exit_offset_bars: float = 10.0,
    tp: bool = False,
    tp_exit_offset_bars: float = 10.0,
) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "segment": "oos_warm",
        "tap_index": 0,
        "zone": 1,
        "trigger_time": T0,
        "entry_time": T0 + entry_offset_min * 60_000,
        "entry_price": 99.0,
        "stop_price": 98.0,
        "exit_time": T0 + int(exit_offset_bars * TF_MS),
        "tp_on_reason": "take_profit" if tp else "stop_loss",
        "tp_on_exit_time": T0 + int(tp_exit_offset_bars * TF_MS),
        "net_r": 1.4 if tp else -1.1,
        "is_stop": not tp,
    }


def _label(setups: list[dict[str, object]], colors: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(setups)
    out = mod.label_group(frame, _bars(colors), TF_MS)
    return pd.concat([frame, out], axis=1)


def test_branches_a_b_follow_bar_colors() -> None:
    both_up = _label([_setup()], ["d", "u", "u", "u"]).iloc[0]
    assert both_up["k1_branch"] == mod.BRANCH_A
    assert both_up["k2_branch"] == mod.BRANCH_A
    second_down = _label([_setup()], ["d", "u", "d", "u"]).iloc[0]
    assert second_down["k1_branch"] == mod.BRANCH_A
    assert second_down["k2_branch"] == mod.BRANCH_B
    first_down = _label([_setup()], ["d", "d", "u", "u"]).iloc[0]
    assert first_down["k1_branch"] == mod.BRANCH_B
    assert first_down["k2_branch"] == mod.BRANCH_B


def test_stop_before_last_confirm_bar_close_is_c_not_b() -> None:
    """🚨 K 마감 전에 손절난 거래는 (B)가 아니라 (C) — 섞으면 동어반복이다."""
    row = _label([_setup(exit_offset_bars=2.5)], ["d", "u", "d", "u"]).iloc[0]
    assert row["k1_branch"] == mod.BRANCH_A  # N+1 마감(2h) 후 청산 → 1봉확인은 기다릴 수 있었다
    assert row["k2_branch"] == mod.BRANCH_C  # N+2 마감(3h) 전 청산 → 2봉확인은 못 기다린다
    stopped_in_first = _label([_setup(exit_offset_bars=1.2)], ["d", "d", "u", "u"]).iloc[0]
    assert stopped_in_first["k1_branch"] == mod.BRANCH_C


def test_fill_at_or_after_confirmation_entry_is_d() -> None:
    """기준 체결이 `N+K+1` 시가 이후면 (D) — 팔마다 기준이 다르다."""
    row = _label(
        [_setup(entry_offset_min=2 * 60 + 10, exit_offset_bars=10)], ["d", "u", "u", "u"]
    ).iloc[0]
    assert row["gap_bars"] == 2
    assert row["k1_branch"] == mod.BRANCH_D  # N+2 시가 이후 체결
    assert row["k2_branch"] == mod.BRANCH_A  # N+3 시가 전 체결


def test_doji_counts_as_bullish_but_is_flagged() -> None:
    row = _label([_setup()], ["d", "=", "u", "u"]).iloc[0]
    assert bool(row["k1_cond"]) is True
    assert bool(row["k1_cond_strict"]) is False


def test_missing_bar_is_c_data_end_not_neighbor() -> None:
    """구멍이 있으면 옆 봉을 주워 오지 않는다 — (C) 데이터 끝."""
    bars = _bars(["d", "u", "u", "u"]).drop(index=2)
    frame = pd.DataFrame([_setup()])
    out = mod.label_group(frame, bars, TF_MS).iloc[0]
    assert out["k1_branch"] == mod.BRANCH_C
    assert bool(out["k1_c_data_end"]) is True


def test_multiplier_uses_confirmation_bar_open_and_is_causal() -> None:
    bars = _bars(["d", "u", "u", "u"])
    bars.loc[2, "open"] = 100.0  # N+2 시가
    frame = pd.DataFrame([_setup()])
    out = mod.label_group(frame, bars, TF_MS).iloc[0]
    assert out["k1_mult"] == pytest.approx((100.0 - 98.0) / (99.0 - 98.0))
    assert out["k1_label_last_close"] <= out["k1_entry_time"]
    assert out["k2_entry_time"] == T0 + 3 * TF_MS


def test_tp_before_confirmation_is_flagged() -> None:
    row = _label([_setup(tp=True, tp_exit_offset_bars=1.5)], ["d", "u", "u", "u"]).iloc[0]
    assert bool(row["k1_tp_before"]) is True  # 1.5h < N+2 시가(2h)
    later = _label([_setup(tp=True, tp_exit_offset_bars=2.5)], ["d", "u", "u", "u"]).iloc[0]
    assert bool(later["k1_tp_before"]) is False
    assert bool(later["k2_tp_before"]) is True


def test_include_d_variant_moves_d_by_condition() -> None:
    frame = _label(
        [
            _setup(entry_offset_min=2 * 60 + 10),
            _setup(),
        ],
        ["d", "u", "u", "u"],
    )
    excl = mod._arm_subset(frame, mod.ARM_ONE, mod.VARIANT_EXCL_D)
    incl = mod._arm_subset(frame, mod.ARM_ONE, mod.VARIANT_INCL_D)
    assert len(excl[mod.BRANCH_D]) == 1 and len(excl[mod.BRANCH_A]) == 1
    assert len(incl[mod.BRANCH_D]) == 0 and len(incl[mod.BRANCH_A]) == 2


def test_verdict_gate_order_drift_first() -> None:
    kwargs: dict[str, Any] = dict(
        n_a=500, n_b=500, delta=0.5, sigma=0.05, delta_no_crash=0.5, delta_pooled=0.5
    )
    assert mod.verdict_for(mult_median_a=1.55, **kwargs) == mod.VERDICT_DRIFT
    assert mod.verdict_for(mult_median_a=1.549, **kwargs) == mod.VERDICT_PASS


def test_verdict_selection_and_crash_gates() -> None:
    base: dict[str, Any] = dict(n_a=500, n_b=500, mult_median_a=1.2)
    z = mod.decision_z()
    assert (
        mod.verdict_for(
            delta=0.1, sigma=0.1 / (z * 0.99), delta_no_crash=0.1, delta_pooled=0.1, **base
        )
        == mod.VERDICT_NO_SELECTION
    )
    assert (
        mod.verdict_for(delta=0.004, sigma=1e-6, delta_no_crash=0.1, delta_pooled=0.1, **base)
        == mod.VERDICT_NO_SELECTION
    )  # 노이즈선 안
    assert (
        mod.verdict_for(delta=-0.3, sigma=0.01, delta_no_crash=-0.3, delta_pooled=-0.3, **base)
        == mod.VERDICT_REVERSE
    )
    assert (
        mod.verdict_for(delta=0.3, sigma=0.01, delta_no_crash=-0.01, delta_pooled=0.3, **base)
        == mod.VERDICT_CRASH_PROXY
    )
    assert (
        mod.verdict_for(delta=0.3, sigma=0.01, delta_no_crash=0.3, delta_pooled=None, **base)
        == mod.VERDICT_CRASH_PROXY
    )
    assert (
        mod.verdict_for(
            n_a=99,
            n_b=500,
            mult_median_a=1.2,
            delta=0.3,
            sigma=0.01,
            delta_no_crash=0.3,
            delta_pooled=0.3,
        )
        == mod.VERDICT_UNDECIDED
    )


def test_decision_z_is_bonferroni_over_eight_points() -> None:
    assert mod.TESTS == 8
    assert mod.decision_z() > mod.SIGMA_MULTIPLE
    assert math.isclose(mod.decision_z(), 2.765, abs_tol=0.01)


def test_second_bar_tradeoff_excludes_stopped_in_second_bar() -> None:
    """🚨 `N+2` 안에서 손절난 셋업은 「둘째 봉 음봉」으로 세지 않는다."""
    frame = _label(
        [
            _setup(),  # 살아 있음 · N+2 양봉
            _setup(exit_offset_bars=2.5),  # N+2 안 손절 → 뺀다
        ],
        ["d", "u", "u", "u"],
    )
    frame["zone"] = [1, 2]
    rows = mod.second_bar_rows(frame)
    row = next(r for r in rows if r.timeframe == "1h" and r.segment == "oos_warm")
    assert row.n_second_bull == 1
    assert row.n_second_bear == 0
    assert row.n_dropped_c == 1


def test_checksum_subset_structure_holds_on_synthetic() -> None:
    frame = _label([_setup()], ["d", "u", "d", "u"])
    two = frame["k2_cond"].astype(bool)
    one = frame["k1_cond"].astype(bool)
    assert not bool((two & ~one).any())
    assert np.all(frame["k2_label_last_close"] <= frame["k2_entry_time"])
