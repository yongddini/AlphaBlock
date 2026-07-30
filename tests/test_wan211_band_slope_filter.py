"""WAN-211 볼린저 하단 기울기 과열 진입 회피 필터 — 순수 로직 회귀 테스트.

격자 실행(DB·수분)이 아니라 **밴드 기울기 특징의 정확성·룩어헤드 없음·워밍업 제외 · 다중
통제 편상관 수학 · 매칭 널 결정성 · 상한 게이트 인덱스 계약 · P&L/독립성 판정 분기**를 손으로
만든 데이터로 고정한다. 후보 재빌드/시퀀싱 정합은 `--checksum`(실데이터)이 맡는다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backtest.harness import SEGMENT_OOS
from backtest.wan117_zone_failure_autopsy import harness_prepare
from backtest.wan150_instant_death_autopsy import Label
from backtest.wan211_band_slope_filter import (
    GATE_FEATURES,
    GATE_HYPOTHESIS_SIGN,
    CorrRow,
    PnlTestRow,
    VerdictKind,
    _BandSlopeExtractor,
    _partial_corr,
    _pooled_quantile,
    _residualize,
    band_lower_slope_features,
    corr_rows_from_labeled,
    death_null_rows_from_labeled,
    gate_keep_indices,
    independence_verdict,
    pnl_verdict,
)

# --------------------------------------------------------------------------- #
# 상수 정합성
# --------------------------------------------------------------------------- #


def test_gate_features_are_band_lower_slope() -> None:
    assert set(GATE_FEATURES) == {
        "band_lower_slope_3_atr",
        "band_lower_slope_5_atr",
        "band_lower_slope_3_pct",
        "band_lower_slope_5_pct",
    }


def test_gate_features_have_positive_hypothesis_sign() -> None:
    for feature in GATE_FEATURES:
        # 즉사일수록 값이 크다(WAN-209 §C 부호 반증: 하단 밴드가 오를수록 즉사).
        assert GATE_HYPOTHESIS_SIGN[feature] == 1


# --------------------------------------------------------------------------- #
# 밴드 기울기 특징 — 정확성
# --------------------------------------------------------------------------- #


def test_band_slope_rising_lower_band_is_positive() -> None:
    """하단 밴드가 오르면(양의 기울기) ÷ATR·가격% 둘 다 양수."""
    band = [float(i) for i in range(20)]  # 매 봉 +1.
    closes = [100.0] * 20
    atr14 = [2.0] * 20
    feats = band_lower_slope_features(band, closes, atr14, prev=10)
    assert feats["band_lower_slope_3_atr"] == (1.0) / 2.0  # slope=(10-7)/3=1 → /atr 2.
    assert feats["band_lower_slope_5_atr"] == (1.0) / 2.0
    assert feats["band_lower_slope_3_pct"] == 1.0 / 100.0
    for v in feats.values():
        assert v is not None and v > 0


def test_band_slope_falling_lower_band_is_negative() -> None:
    band = [float(-i) for i in range(20)]  # 매 봉 −1.
    feats = band_lower_slope_features(band, [100.0] * 20, [2.0] * 20, prev=10)
    for v in feats.values():
        assert v is not None and v < 0


def test_band_slope_warmup_returns_none() -> None:
    band = [1.0, 2.0, 3.0]
    feats = band_lower_slope_features(band, [100.0] * 3, [2.0] * 3, prev=1)  # prev-k<0.
    assert all(v is None for v in feats.values())


def test_band_slope_nan_returns_none_for_that_scale() -> None:
    band = [float("nan")] + [float(i) for i in range(19)]
    feats = band_lower_slope_features(band, [100.0] * 20, [2.0] * 20, prev=3)
    # prev-5<... prev=3: k=3 uses band[0]=nan → None; k=5 prev-5<0 → None.
    assert feats["band_lower_slope_3_atr"] is None
    assert feats["band_lower_slope_5_atr"] is None


def test_band_slope_zero_atr_skips_atr_scale() -> None:
    band = [float(i) for i in range(20)]
    feats = band_lower_slope_features(band, [100.0] * 20, [0.0] * 20, prev=10)
    assert feats["band_lower_slope_3_atr"] is None  # atr 0 → 스킵.
    assert feats["band_lower_slope_3_pct"] is not None  # 가격% 는 살아있다.


# --------------------------------------------------------------------------- #
# 룩어헤드 없음 (핵심 회귀 테스트)
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


def test_band_extractor_no_lookahead() -> None:
    """탭 시각의 밴드 기울기 특징은 그 이후 봉을 잘라도 비트 동일해야 한다."""
    frame = harness_prepare(_synthetic_frame(n=200))
    trigger_pos = 120
    trigger_time = int(frame["open_time"].iloc[trigger_pos])
    full = _BandSlopeExtractor.build(frame)
    trunc = _BandSlopeExtractor.build(frame.iloc[: trigger_pos + 1].reset_index(drop=True))
    a = full.features_for_time(trigger_time)
    b = trunc.features_for_time(trigger_time)
    for key in GATE_FEATURES:
        av, bv = a[key], b[key]
        assert av is not None, key
        assert bv is not None, key
        assert abs(av - bv) < 1e-12, f"{key} leaked future: {av} != {bv}"


def test_band_extractor_unknown_time_returns_none() -> None:
    frame = harness_prepare(_synthetic_frame(n=100))
    ext = _BandSlopeExtractor.build(frame)
    feats = ext.features_for_time(999_999_999_999)  # 존재하지 않는 탭 시각.
    for key in GATE_FEATURES:
        assert feats[key] is None


# --------------------------------------------------------------------------- #
# 편상관 수학 (다중 통제)
# --------------------------------------------------------------------------- #


def test_residualize_removes_linear_component_single() -> None:
    control = [1.0, 2.0, 3.0, 4.0, 5.0]
    values = [2.0, 4.0, 6.0, 8.0, 10.0]  # values = 2*control → 잔차 0.
    resid = _residualize(values, [control])
    assert all(abs(r) < 1e-9 for r in resid)


def test_residualize_removes_two_controls() -> None:
    rng = np.random.default_rng(0)
    n = 100
    c1 = rng.normal(0, 1, n)
    c2 = rng.normal(0, 1, n)
    values = (3.0 * c1 - 2.0 * c2 + 1.0).tolist()  # 두 통제의 선형결합 → 잔차 0.
    resid = _residualize(values, [c1.tolist(), c2.tolist()])
    assert all(abs(r) < 1e-9 for r in resid)


def test_partial_corr_zero_when_target_driven_by_controls() -> None:
    rng = np.random.default_rng(5)
    n = 300
    c1 = rng.normal(0, 1, n)
    c2 = rng.normal(0, 1, n)
    target = (2.0 * c1 - 1.0 * c2 + rng.normal(0, 0.3, n)).tolist()
    values = rng.normal(0, 1, n).tolist()  # 통제·타깃과 무관.
    pc = _partial_corr(values, [c1.tolist(), c2.tolist()], target)
    assert pc is not None and abs(pc) < 0.2


def test_partial_corr_survives_when_independent_of_controls() -> None:
    rng = np.random.default_rng(1)
    n = 300
    c1 = rng.normal(0, 1, n).tolist()
    c2 = rng.normal(0, 1, n).tolist()
    base = rng.normal(0, 1, n)
    values = base.tolist()
    target = (base + rng.normal(0, 0.5, n)).tolist()
    from backtest.wan150_instant_death_autopsy import _corr

    raw = _corr(values, target)
    pc = _partial_corr(values, [c1, c2], target)
    assert raw is not None and pc is not None
    assert abs(pc - raw) < 0.1  # 통제가 무관하니 편상관이 거의 안 줄어든다.


def test_pooled_quantile_basic() -> None:
    vals = list(range(101))  # 0..100.
    assert _pooled_quantile(vals, 0.75) == 75.0
    assert _pooled_quantile([1.0], 0.5) is None  # 표본 < 3.


def test_no_nan_in_pooled_quantile() -> None:
    assert not math.isnan(_pooled_quantile([1.0, 2.0, 3.0, 4.0], 0.5) or float("nan"))


# --------------------------------------------------------------------------- #
# 상한 게이트 인덱스 계약
# --------------------------------------------------------------------------- #


def test_gate_keep_removes_values_at_or_above_threshold() -> None:
    feats: list[dict[str, float | None]] = [
        {"band_lower_slope_3_atr": -0.5},
        {"band_lower_slope_3_atr": 0.1},
        {"band_lower_slope_3_atr": 0.9},
        {"band_lower_slope_3_atr": None},  # 잴 수 없음 → 남긴다.
    ]
    keep = gate_keep_indices([0, 1, 2, 3], feats, "band_lower_slope_3_atr", threshold=0.5)
    assert keep == [0, 1, 3]  # 0.9(≥0.5)만 스킵, None은 유지.


def test_gate_keep_subset_monotonic_in_threshold() -> None:
    feats: list[dict[str, float | None]] = [{"band_lower_slope_3_atr": float(v)} for v in range(10)]
    seg = list(range(10))
    low = set(gate_keep_indices(seg, feats, "band_lower_slope_3_atr", threshold=4.0))
    high = set(gate_keep_indices(seg, feats, "band_lower_slope_3_atr", threshold=7.0))
    assert low <= high  # 문턱을 올리면(덜 조이면) 남는 집합이 커진다(부분집합).


# --------------------------------------------------------------------------- #
# 매칭 널 — 게이트가 즉사를 줄이고 결정적이다
# --------------------------------------------------------------------------- #


def _labeled_df(n: int = 600, seed: int = 3) -> pd.DataFrame:
    """band_lower_slope가 높을수록 즉사가 잦은 합성 라벨 원자료(즉사·승자만, IS/OOS 반반)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        slope = float(rng.uniform(-1.0, 1.0))
        p_death = 0.1 + 0.6 * (slope + 1.0) / 2.0  # 높은 기울기일수록 즉사↑.
        death = rng.random() < p_death
        rows.append(
            {
                "symbol": f"S{i % 3}/USDT:USDT",
                "timeframe": "1h",
                "segment": "is" if i % 2 == 0 else "oos",
                "side": "long",
                "trigger_time": i,
                "label": Label.INSTANT_DEATH.value if death else Label.WINNER.value,
                "band_lower_slope_3_atr": slope,
                "band_lower_slope_5_atr": slope + float(rng.normal(0, 0.1)),
                "band_lower_slope_3_pct": slope * 0.01,
                "band_lower_slope_5_pct": slope * 0.01,
                "zone_width_atr": float(rng.uniform(0.5, 1.5)),
                "stop_width_atr": float(rng.uniform(0.3, 1.0)),
            }
        )
    return pd.DataFrame(rows)


def test_death_null_gate_beats_random_when_signal_present() -> None:
    df = _labeled_df()
    rows = death_null_rows_from_labeled(df)
    oos = [
        r
        for r in rows
        if r.timeframe == "1h" and r.segment == "oos" and r.feature == "band_lower_slope_3_atr"
    ]
    assert oos, "1h OOS band_lower_slope_3_atr 행이 있어야 한다"
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
    gate = [r for r in rows if r.feature == "band_lower_slope_3_atr" and r.timeframe == "1h"]
    assert gate
    assert all(r.partial_correlation is not None for r in gate)  # 편상관 계산됨.


# --------------------------------------------------------------------------- #
# §2 독립성 판정 분기
# --------------------------------------------------------------------------- #


def _corr_row(**kw: object) -> CorrRow:
    base: dict[str, object] = {
        "timeframe": "15m",
        "segment": SEGMENT_OOS,
        "feature": "band_lower_slope_3_atr",
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


def test_independence_verdict_independent() -> None:
    v = independence_verdict([_corr_row()], timeframe="15m")
    assert v.kind is VerdictKind.INDEPENDENT


def test_independence_verdict_collinear_when_partial_collapses() -> None:
    v = independence_verdict([_corr_row(partial_correlation=0.02, p_partial=0.4)], timeframe="15m")
    assert v.kind is VerdictKind.COLLINEAR


def test_independence_verdict_collinear_when_raw_not_significant() -> None:
    v = independence_verdict([_corr_row(p_value=0.3)], timeframe="15m")
    assert v.kind is VerdictKind.COLLINEAR


def test_independence_verdict_indeterminate_when_missing() -> None:
    v = independence_verdict([], timeframe="15m")
    assert v.kind is VerdictKind.INDETERMINATE


def test_independence_verdict_partial() -> None:
    v = independence_verdict(
        [_corr_row(correlation=0.20, partial_correlation=0.05, p_partial=0.02)],
        timeframe="15m",
    )
    assert v.kind is VerdictKind.PARTIAL


# --------------------------------------------------------------------------- #
# P&L 판정 분기 (주 산출물)
# --------------------------------------------------------------------------- #


def _pnl_test_row(**kw: object) -> PnlTestRow:
    base: dict[str, object] = {
        "timeframe": "15m",
        "segment": SEGMENT_OOS,
        "feature": "band_lower_slope_3_atr",
        "remove_fraction": 1.0 / 3.0,
        "lens": "baseline",
        "n_symbols": 9,
        "default_return": 0.20,
        "filter_return": 0.10,
        "matched_return_mean": 0.12,
        "p_return": 0.5,
        "default_death": 0.3,
        "filter_death": 0.25,
        "default_mdd": 0.10,
        "filter_mdd": 0.10,
    }
    base.update(kw)
    return PnlTestRow(**base)


def _pnl_cells(
    *, filter_return: float, p_return: float, lens: str = "baseline"
) -> list[PnlTestRow]:
    """4특징 × 3제거비율 = 12셀을 같은 값으로 만든다(판정 헬퍼)."""
    rows: list[PnlTestRow] = []
    for feature in GATE_FEATURES:
        for fraction in (0.25, 1.0 / 3.0, 0.5):
            rows.append(
                _pnl_test_row(
                    feature=feature,
                    remove_fraction=fraction,
                    filter_return=filter_return,
                    p_return=p_return,
                    lens=lens,
                )
            )
    return rows


def test_pnl_verdict_loss_when_filter_below_default() -> None:
    """필터가 default(20%)를 못 넘고 무작위도 못 이기면 (b) net loss."""
    v = pnl_verdict(_pnl_cells(filter_return=0.05, p_return=0.9), timeframe="15m")
    assert v.kind is VerdictKind.PNL_LOSS


def test_pnl_verdict_gain_when_filter_beats_all() -> None:
    """필터가 default를 넘고(30%>20%) 무작위를 이기며(p작음) pen_5bp도 유지하면 (a)."""
    cells = _pnl_cells(filter_return=0.30, p_return=0.02)
    cells += _pnl_cells(filter_return=0.30, p_return=0.02, lens="pen_5bp")  # pen 유지.
    v = pnl_verdict(cells, timeframe="15m")
    assert v.kind is VerdictKind.PNL_GAIN


def test_pnl_verdict_mixed_when_pen_flips() -> None:
    """baseline은 이기지만 pen_5bp에서 default 아래로 뒤집히면 (c)."""
    cells = _pnl_cells(filter_return=0.30, p_return=0.02)
    cells += _pnl_cells(filter_return=0.05, p_return=0.9, lens="pen_5bp")  # pen 붕괴.
    v = pnl_verdict(cells, timeframe="15m")
    assert v.kind is VerdictKind.PNL_MIXED


def test_pnl_verdict_indeterminate_when_empty() -> None:
    v = pnl_verdict([], timeframe="15m")
    assert v.kind is VerdictKind.INDETERMINATE


def test_pnl_verdict_indeterminate_when_symbols_sparse() -> None:
    """유효 종목이 5개 미만이면(4h 표본 붕괴) 판정 불가(대조군)."""
    cells = [_pnl_test_row(feature=f, n_symbols=2) for f in GATE_FEATURES]
    v = pnl_verdict(cells, timeframe="4h")
    assert v.kind is VerdictKind.INDETERMINATE
