"""WAN-421 — 존이 뚫리기 전 맥락 라벨: 합성 입력으로 정의·인과·판정 관문을 동작으로 고정한다."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtest import wan421_pre_break_context as w

TF_MS = 4 * 3_600_000
DAY_MS = 86_400_000


def _bars(lows: list[float], *, span: float = 5.0) -> pd.DataFrame:
    n = len(lows)
    low = np.asarray(lows, dtype=float)
    return pd.DataFrame(
        {
            "open_time": np.arange(n, dtype=np.int64) * TF_MS,
            "open": low + 1.0,
            "high": low + span,
            "low": low,
            "close": low + 2.0,
            "volume": np.ones(n),
        }
    )


def _daily(n: int = 400, price: float = 110.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": (np.arange(n, dtype=np.int64) - 300) * DAY_MS,
            "open": np.full(n, price),
            "high": np.full(n, price + 1),
            "low": np.full(n, price - 1),
            "close": np.full(n, price),
            "volume": np.ones(n),
        }
    )


def _base_lows(n: int = 200) -> list[float]:
    # 주기가 짧은 톱니 — 좌우 10봉 안에 같은 값이 반드시 있어 피벗이 없다(동률 = 피벗 아님).
    return [110.0 + (i % 3) * 0.5 for i in range(n)]


TAP = 180
ZONES = [(100.0, 95.0)]


def _setup(trigger_pos: int = TAP) -> pd.DataFrame:
    return pd.DataFrame(
        {"trigger_time": [trigger_pos * TF_MS], "zone": [0], "stop_price": [95.0]}, index=[7]
    )


def _label(lows: list[float]) -> pd.Series:
    frame = w.label_frame(_setup(), bars=_bars(lows), daily=_daily(), zones=ZONES, tf_ms=TF_MS)
    return frame.loc[7]


def test_pivot_lows_mirror_ties_are_not_pivots() -> None:
    lows = np.array([5.0] * 30)
    lows[15] = 1.0
    got = w.pivot_lows(lows, 10)
    assert got.tolist().count(True) == 1 and bool(got[15])
    lows[5] = 1.0  # 왼쪽 10봉 안의 동률 — 피벗 아님
    assert not bool(w.pivot_lows(lows, 10)[15])


def test_a_class_none_when_no_pivot_above_zone() -> None:
    assert _label(_base_lows())["a_class"] == w.CLASS_NONE


def test_a_class_unswept_then_swept() -> None:
    lows = _base_lows()
    lows[150] = 105.0  # 존 윗변(100) 위 · ATR 근처의 피벗
    rec = _label(lows)
    assert rec["a_class"] == w.CLASS_UNSWEPT
    assert int(rec["a_eligible_pivots"]) == 1
    lows[170] = 104.0  # 확정(160) 뒤 · 탭 봉 전에 그 저가를 깬다
    assert _label(lows)["a_class"] == w.CLASS_SWEPT


def test_a_ignores_pivot_inside_zone_and_unconfirmed_pivot() -> None:
    lows = _base_lows()
    lows[150] = 99.0  # 존 안 — 존 자체의 저가라 대상 아님
    assert _label(lows)["a_class"] == w.CLASS_NONE
    lows = _base_lows()
    lows[TAP - 5] = 105.0  # 확정 봉(c+10)이 탭 봉 뒤 — 탭 시점엔 모른다
    assert _label(lows)["a_class"] == w.CLASS_NONE


def test_labels_are_causal_under_future_truncation() -> None:
    """탭 봉 이후를 통째로 잘라도 라벨이 비트 동일 — 룩어헤드가 섞이면 죽는다(WAN-377 정신)."""
    lows = _base_lows()
    lows[150] = 105.0
    lows[170] = 104.0
    bars = _bars(lows)
    daily = _daily()
    full = w.label_frame(_setup(), bars=bars, daily=daily, zones=ZONES, tf_ms=TF_MS)
    trig = TAP * TF_MS
    cut = w.label_frame(
        _setup(),
        bars=bars[bars["open_time"] <= trig].reset_index(drop=True),
        daily=daily[daily["open_time"] + DAY_MS <= trig].reset_index(drop=True),
        zones=ZONES,
        tf_ms=TF_MS,
    )
    pd.testing.assert_frame_equal(full, cut)
    for col in ("a_last_close", "d_last_close", "b_last_close", "c_last_close"):
        assert int(full.loc[7, col]) <= trig


def test_future_bars_change_nothing() -> None:
    """미래 봉을 극단적으로 바꿔도 라벨이 그대로다(위와 같은 성질의 반대 방향 확인)."""
    lows = _base_lows()
    lows[150] = 105.0
    a = _label(lows)
    lows2 = list(lows)
    for i in range(TAP, len(lows2)):
        lows2[i] = 1.0
    b = _label(lows2)
    pd.testing.assert_series_equal(a, b)


def test_d_counts_lines_inside_zone() -> None:
    # 모든 선이 110 근처라 존(95~100) 밖 → 0. 존을 선 위로 옮기면 6개 전부 들어온다.
    rec = _label(_base_lows())
    assert rec["d_count"] == 0.0
    frame = w.label_frame(
        _setup(), bars=_bars(_base_lows()), daily=_daily(), zones=[(130.0, 95.0)], tf_ms=TF_MS
    )
    assert frame.loc[7, "d_count"] == 6.0
    assert frame.loc[7, "d_count_ex20"] == 5.0


def test_d_contrast_is_any_overlap_vs_none() -> None:
    """D = 「하나라도 겹치면 진입」 — 1개 이상 대 0개(개수 등급 없음 · 사용자 정의 2026-09-18)."""
    frame = pd.DataFrame(
        {
            "segment": ["is"] * 5,
            "timeframe": ["4h"] * 5,
            "d_count": [0.0, 1.0, 2.0, 6.0, float("nan")],
            "d_count_ex20": [0.0, 0.0, 1.0, 5.0, float("nan")],
        }
    )
    spec = w.spec_for(frame, w.AXIS_D, "4h")
    assert spec.edges == (0.0, 1.0)
    good, bad = w.contrast_masks(frame, spec)
    assert good.tolist() == [False, True, True, True, False]
    assert bad.tolist() == [True, False, False, False, False]
    good_x, bad_x = w.contrast_masks(frame, spec, column="d_count_ex20")
    assert good_x.tolist() == [False, False, True, True, False]
    assert bad_x.tolist() == [True, True, False, False, False]


def test_b_contrast_is_middle_minus_ends() -> None:
    spec = w.AxisSpec(w.AXIS_B, "4h", (0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    frame = pd.DataFrame({"b_energy": [0.5, 1.5, 2.5, 3.5, 4.5]})
    good, bad = w.contrast_masks(frame, spec)
    assert good.tolist() == [False, True, True, True, False]
    assert bad.tolist() == [True, False, False, False, True]


def test_bonferroni_denominator_is_axes_times_tfs() -> None:
    assert w.TESTS == 16
    assert w.decision_z() > 2.0


def _v(**kw: object) -> str:
    base: dict[str, object] = {
        "n_good_is": 500,
        "n_bad_is": 500,
        "n_good_oos": 500,
        "n_bad_oos": 500,
        "delta_is": 0.1,
        "delta_oos": 0.2,
        "sigma_oos": 0.02,
        "delta_no_crash": 0.1,
        "delta_pooled": 0.1,
        "delta_ex20": 0.1,
        "is_line_axis": False,
    }
    base.update(kw)
    return w.verdict_for(**base)  # type: ignore[arg-type]


def test_verdict_gates_in_order() -> None:
    assert _v() == w.VERDICT_PASS
    assert _v(n_bad_oos=99) == w.VERDICT_UNDECIDED
    assert _v(sigma_oos=0.2) == w.VERDICT_NO_SELECTION
    assert _v(delta_oos=0.004, sigma_oos=0.0001) == w.VERDICT_NO_SELECTION  # 노이즈선 안
    assert _v(delta_oos=-0.2) == w.VERDICT_REVERSE
    assert _v(delta_is=-0.01) == w.VERDICT_IS_MISMATCH
    assert _v(delta_no_crash=-0.01) == w.VERDICT_CRASH_PROXY
    assert _v(delta_pooled=None) == w.VERDICT_CRASH_PROXY
    assert _v(is_line_axis=True, delta_ex20=-0.01) == w.VERDICT_BOLLINGER_ALIAS
    assert _v(is_line_axis=False, delta_ex20=-0.01) == w.VERDICT_PASS


def test_missing_tap_bar_is_labelled_missing_not_guessed() -> None:
    frame = w.label_frame(
        _setup(trigger_pos=TAP).assign(trigger_time=[TAP * TF_MS + 1]),
        bars=_bars(_base_lows()),
        daily=_daily(),
        zones=ZONES,
        tf_ms=TF_MS,
    )
    rec = frame.loc[7]
    assert rec["a_class"] == w.CLASS_MISSING
    assert math.isnan(float(rec["d_count"]))
    assert math.isnan(float(rec["b_energy"]))


@pytest.mark.parametrize("n_is", [50, 200])
def test_undecided_when_sample_small(n_is: int) -> None:
    got = _v(n_good_is=n_is)
    assert got == (w.VERDICT_UNDECIDED if n_is < w.MIN_GROUP_N else w.VERDICT_PASS)


def test_bucket_rows_one_row_per_bucket() -> None:
    """버킷 표는 (축, TF, 구간, 버킷)마다 한 줄 — 키를 셋업 수만큼 되풀이하던 결함의 회귀."""
    frame = pd.DataFrame(
        {
            "segment": ["is"] * 6 + ["oos_warm"] * 6,
            "timeframe": ["4h"] * 12,
            "d_count": [0, 0, 1, 1, 2, 0] * 2,
            "net_r": [0.1, -1.0, 1.4, -1.1, 0.2, 0.3] * 2,
            "is_stop": [False, True, False, True, False, False] * 2,
            "stop_width": [0.01] * 12,
        }
    )
    spec = w.AxisSpec(w.AXIS_D, "4h", (0.0, 1.0))
    rows = w.bucket_rows(frame, [spec])
    keys = [(r.axis, r.timeframe, r.segment, r.bucket) for r in rows]
    assert len(keys) == len(set(keys)) == 4
    assert sum(r.n for r in rows) == len(frame)
