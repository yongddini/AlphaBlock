"""WAN-210 과열 반등 회피 필터 — 순수 로직 회귀 테스트.

격자 실행(DB·수분)이 아니라 **지속-심화 특징의 정확성·룩어헤드 없음·워밍업 제외 · 편상관
수학 · 매칭 널 결정성 · 상한 게이트 인덱스 계약 · 독립성 판정 분기**를 손으로 만든
데이터로 고정한다. 후보 재빌드/시퀀싱 정합은 `--checksum`(실데이터)이 맡는다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backtest.harness import SEGMENT_OOS
from backtest.wan117_zone_failure_autopsy import harness_prepare
from backtest.wan150_instant_death_autopsy import Label
from backtest.wan210_reverse_rsi_filter import (
    DEEPEN_FEATURES,
    DEEPEN_HYPOTHESIS_SIGN,
    DEEPEN_WINDOWS,
    GATE_FEATURES,
    GATE_HYPOTHESIS_SIGN,
    CorrRow,
    VerdictKind,
    _DeepenExtractor,
    _partial_corr,
    _pooled_quantile,
    _residualize,
    corr_rows_from_labeled,
    death_null_rows_from_labeled,
    deepening_features,
    gate_keep_indices,
    independence_verdict,
)

# --------------------------------------------------------------------------- #
# 상수 정합성
# --------------------------------------------------------------------------- #


def test_every_deepen_feature_has_hypothesis_sign() -> None:
    for feature in DEEPEN_FEATURES:
        assert feature in DEEPEN_HYPOTHESIS_SIGN
    for window in DEEPEN_WINDOWS:
        assert f"deepen_run_{window}" in DEEPEN_FEATURES
        assert f"monotone_fall_{window}" in DEEPEN_FEATURES
        assert f"slope_trend_{window}" in DEEPEN_FEATURES


def test_gate_features_have_hypothesis_sign() -> None:
    for feature in GATE_FEATURES:
        assert GATE_HYPOTHESIS_SIGN[feature] == 1  # 즉사일수록 값이 크다(WAN-150 반증).


# --------------------------------------------------------------------------- #
# §3-bis 지속-심화 특징 — 정확성
# --------------------------------------------------------------------------- #


def test_deepening_features_pure_deepening() -> None:
    """계속 더 음수로 벌어지는 기울기 → run이 창을 다 채우고 monotone=1, trend<0."""
    # E가 아래로 볼록하게 가속 하강: 차분이 −1,−2,−3,... 로 심화.
    # E[k] = -0.5*k^2 → slope[k] = E[k]-E[k-1] = -(k-0.5), 계속 더 음수.
    e = [-0.5 * k * k for k in range(20)]
    prev = 15
    window = 4
    feats = deepening_features(e, prev, window)
    assert feats[f"deepen_run_{window}"] == float(window)  # 창 전부 심화.
    assert feats[f"monotone_fall_{window}"] == 1.0  # 전부 음수.
    trend = feats[f"slope_trend_{window}"]
    assert trend is not None and trend < 0  # 가속 하강 = 추세 음수.


def test_deepening_features_flat_is_not_deepening() -> None:
    """평평(기울기 0)하면 run=0, monotone=0(음수 아님)."""
    e = [5.0] * 20
    feats = deepening_features(e, 15, 5)
    assert feats["deepen_run_5"] == 0.0
    assert feats["monotone_fall_5"] == 0.0
    assert feats["slope_trend_5"] == 0.0


def test_deepening_features_steady_fall_monotone_but_no_deepening() -> None:
    """일정한 하락(기울기 −c 고정)은 monotone=1이지만 심화가 아니라 run 짧다."""
    e = [-1.0 * k for k in range(20)]  # slope = −1 고정.
    feats = deepening_features(e, 15, 5)
    assert feats["monotone_fall_5"] == 1.0  # 전부 음수.
    assert feats["deepen_run_5"] == 0.0  # s_j < s_{j-1} 이 성립 안 함(같으므로).
    trend = feats["slope_trend_5"]
    assert trend is not None and abs(trend) < 1e-9  # 기울기 일정 → 추세 0.


def test_deepening_features_warmup_returns_none() -> None:
    e = [1.0, 2.0, 3.0]
    feats = deepening_features(e, 2, 5)  # prev-window-1 < 0.
    for key in feats:
        assert feats[key] is None


def test_deepening_features_nan_returns_none() -> None:
    e = [float("nan")] + [-0.5 * k * k for k in range(19)]
    # prev 창이 NaN을 포함하도록.
    feats = deepening_features(e, 5, 4)
    for key in feats:
        assert feats[key] is None


# --------------------------------------------------------------------------- #
# §3-bis 룩어헤드 없음 (핵심 회귀 테스트)
# --------------------------------------------------------------------------- #


def _synthetic_frame(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    open_ = close + rng.normal(0, 0.3, n)
    return pd.DataFrame(
        {
            "open_time": np.arange(n, dtype="int64") * 3_600_000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.abs(rng.normal(100, 10, n)),
        }
    )


def test_deepen_extractor_no_lookahead() -> None:
    """탭 시각의 지속-심화 특징은 그 이후 봉을 잘라도 비트 동일해야 한다."""
    frame = harness_prepare(_synthetic_frame(n=200))
    trigger_pos = 120
    trigger_time = int(frame["open_time"].iloc[trigger_pos])
    full = _DeepenExtractor.build(frame)
    trunc = _DeepenExtractor.build(frame.iloc[: trigger_pos + 1].reset_index(drop=True))
    a = full.features_for_time(trigger_time)
    b = trunc.features_for_time(trigger_time)
    for key in DEEPEN_FEATURES:
        av, bv = a[key], b[key]
        assert av is not None, key
        assert bv is not None, key
        assert abs(av - bv) < 1e-12, f"{key} leaked future: {av} != {bv}"


def test_deepen_extractor_unknown_time_returns_none() -> None:
    frame = harness_prepare(_synthetic_frame(n=100))
    ext = _DeepenExtractor.build(frame)
    feats = ext.features_for_time(999_999_999_999)  # 존재하지 않는 탭 시각.
    for key in DEEPEN_FEATURES:
        assert feats[key] is None


# --------------------------------------------------------------------------- #
# 편상관 수학
# --------------------------------------------------------------------------- #


def test_residualize_removes_linear_component() -> None:
    control = [1.0, 2.0, 3.0, 4.0, 5.0]
    values = [2.0, 4.0, 6.0, 8.0, 10.0]  # values = 2*control → 잔차 0.
    resid = _residualize(values, control)
    assert all(abs(r) < 1e-9 for r in resid)


def test_partial_corr_zero_when_target_driven_by_control() -> None:
    """target이 control로 대부분 설명되고 values가 control과 독립이면 편상관 ≈ 0."""
    rng = np.random.default_rng(5)
    n = 200
    control = rng.normal(0, 1, n)
    target = (2.0 * control + rng.normal(0, 0.3, n)).tolist()  # target ≈ f(control)+잡음.
    values = rng.normal(0, 1, n).tolist()  # control·target과 무관.
    pc = _partial_corr(values, control.tolist(), target)
    assert pc is not None
    assert abs(pc) < 0.2  # control을 걷어내면 values는 target과 무관.


def test_partial_corr_survives_when_independent_of_control() -> None:
    """values·target이 서로 상관되고 control과 무관하면 편상관이 raw와 비슷하게 남는다."""
    rng = np.random.default_rng(1)
    n = 200
    control = rng.normal(0, 1, n).tolist()
    base = rng.normal(0, 1, n)
    values = base.tolist()
    target = (base + rng.normal(0, 0.5, n)).tolist()
    from backtest.wan150_instant_death_autopsy import _corr

    raw = _corr(values, target)
    pc = _partial_corr(values, control, target)
    assert raw is not None and pc is not None
    assert abs(pc - raw) < 0.1  # control이 무관하니 편상관이 거의 안 줄어든다.


def test_pooled_quantile_basic() -> None:
    vals = list(range(101))  # 0..100.
    assert _pooled_quantile(vals, 0.75) == 75.0
    assert _pooled_quantile([1.0], 0.5) is None  # 표본 < 3.


# --------------------------------------------------------------------------- #
# 상한 게이트 인덱스 계약
# --------------------------------------------------------------------------- #


def test_gate_keep_removes_values_at_or_above_threshold() -> None:
    feats: list[dict[str, float | None]] = [
        {"tap_rsi": 10.0},
        {"tap_rsi": 50.0},
        {"tap_rsi": 60.0},
        {"tap_rsi": None},  # 잴 수 없음 → 남긴다.
    ]
    keep = gate_keep_indices([0, 1, 2, 3], feats, "tap_rsi", threshold=55.0)
    assert keep == [0, 1, 3]  # 60(≥55)만 스킵, None은 유지.


def test_gate_keep_subset_monotonic_in_threshold() -> None:
    feats: list[dict[str, float | None]] = [{"tap_rsi": float(v)} for v in range(10)]
    seg = list(range(10))
    low = set(gate_keep_indices(seg, feats, "tap_rsi", threshold=4.0))
    high = set(gate_keep_indices(seg, feats, "tap_rsi", threshold=7.0))
    assert low <= high  # 문턱을 올리면(덜 조이면) 남는 집합이 커진다(부분집합).


# --------------------------------------------------------------------------- #
# 매칭 널 — 게이트가 즉사를 줄이고 결정적이다
# --------------------------------------------------------------------------- #


def _labeled_df(n: int = 600, seed: int = 3) -> pd.DataFrame:
    """tap_rsi가 높을수록 즉사가 잦은 합성 라벨 원자료(즉사·승자만, IS/OOS 반반)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        tap = float(rng.uniform(20, 60))
        # 높은 tap일수록 즉사 확률↑.
        p_death = 0.1 + 0.6 * (tap - 20) / 40
        death = rng.random() < p_death
        rows.append(
            {
                "symbol": f"S{i % 3}/USDT:USDT",
                "timeframe": "1h",
                "segment": "is" if i % 2 == 0 else "oos",
                "side": "long",
                "trigger_time": i,
                "label": Label.INSTANT_DEATH.value if death else Label.WINNER.value,
                "tap_rsi": tap,
                "rsi_ema_slope": float(rng.normal(0, 1)),
                "zone_width_atr": float(rng.uniform(0.5, 1.5)),
            }
        )
    return pd.DataFrame(rows)


def test_death_null_gate_beats_random_when_signal_present() -> None:
    """tap_rsi↔즉사가 심긴 데이터에서 게이트 즉사율 < 기본, p_death 작다."""
    df = _labeled_df()

    # DEFAULT_TIMEFRAMES가 1h를 포함하도록 강제(합성이 1h만 가짐) — 모듈 상수를 쓰지 않고
    # 함수가 1h 셀을 처리하는지만 본다.
    rows = death_null_rows_from_labeled(df)
    oos = [r for r in rows if r.timeframe == "1h" and r.segment == "oos" and r.feature == "tap_rsi"]
    assert oos, "1h OOS tap_rsi 행이 있어야 한다"
    for r in oos:
        assert r.gate_death_rate is not None
        assert r.gate_death_rate < r.base_death_rate  # 게이트가 즉사를 줄인다.
        assert r.p_death is not None and r.p_death <= 0.2  # 무작위보다 낫다.
        assert r.winner_removed_rate is not None  # 오폭 계량됨.


def test_death_null_deterministic() -> None:
    df = _labeled_df()
    a = death_null_rows_from_labeled(df)
    b = death_null_rows_from_labeled(df)
    assert [r.model_dump() for r in a] == [r.model_dump() for r in b]


def test_corr_rows_partial_present_for_gate_features() -> None:
    df = _labeled_df()
    rows = corr_rows_from_labeled(df, permutations=200)
    gate = [r for r in rows if r.feature == "tap_rsi" and r.timeframe == "1h"]
    assert gate
    assert all(r.partial_correlation is not None for r in gate)  # 편상관 계산됨.


# --------------------------------------------------------------------------- #
# 독립성 판정 분기
# --------------------------------------------------------------------------- #


def _corr_row(**kw: object) -> CorrRow:
    base: dict[str, object] = {
        "timeframe": "1h",
        "segment": SEGMENT_OOS,
        "feature": "tap_rsi",
        "n": 500,
        "positive_rate": 0.3,
        "correlation": 0.12,
        "p_value": 0.01,
        "partial_correlation": 0.09,
        "p_partial": 0.02,
        "hypothesis_sign": 1,
        "direction_matches": True,
    }
    base.update(kw)
    return CorrRow(**base)


def test_verdict_independent() -> None:
    v = independence_verdict([_corr_row()], timeframe="1h")
    assert v.kind is VerdictKind.INDEPENDENT


def test_verdict_collinear_when_partial_collapses() -> None:
    v = independence_verdict([_corr_row(partial_correlation=0.02, p_partial=0.4)], timeframe="1h")
    assert v.kind is VerdictKind.COLLINEAR


def test_verdict_collinear_when_raw_not_significant() -> None:
    v = independence_verdict([_corr_row(p_value=0.3)], timeframe="1h")
    assert v.kind is VerdictKind.COLLINEAR


def test_verdict_indeterminate_when_missing() -> None:
    v = independence_verdict([], timeframe="1h")
    assert v.kind is VerdictKind.INDETERMINATE


def test_verdict_partial() -> None:
    # raw 유의, partial 유의하되 절반 미만 잔존 → (c) 부분 독립.
    v = independence_verdict(
        [_corr_row(correlation=0.20, partial_correlation=0.05, p_partial=0.02)],
        timeframe="1h",
    )
    assert v.kind is VerdictKind.PARTIAL


def test_no_nan_in_pooled_quantile() -> None:
    assert not math.isnan(_pooled_quantile([1.0, 2.0, 3.0, 4.0], 0.5) or float("nan"))
