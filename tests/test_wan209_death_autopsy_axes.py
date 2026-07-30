"""WAN-209 남은 세 축 — 특징 추출 룩어헤드·문턱 스윕·통제 순열·축 판정 테스트.

격자 실행(DB·수분)이 아니라 §A(RVOL)·§B(상위TF 조회)·§C(밴드 기울기)의 로직과 존폭/손절폭
통제(부분상관) 관문·축 판정을 손으로 만든 프레임/라벨로 고정한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.models import ExitReason, PositionSide
from backtest.wan150_instant_death_autopsy import (
    _DEATH_VS_REST,
    Label,
    LabeledTrade,
    PermutationRow,
    permutation_test,
)
from backtest.wan209_death_autopsy_axes import (
    _CONTROL_FEATURES,
    _REGIME_HTFS,
    AXIS_FEATURES,
    FEATURES,
    HYPOTHESIS_SIGN,
    S_A_FEATURES,
    S_B_FEATURES,
    S_C_FEATURES,
    _annotate_percentiles,
    _RegimeTable,
    _residualize,
    _Wan209Extractor,
    axis_verdict,
    collinearity,
    partial_correlation_test,
    threshold_sweep,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import OrderBlock, OrderBlockDirection

# --------------------------------------------------------------------------- #
# 상수 정합성
# --------------------------------------------------------------------------- #


def test_feature_lists_partition_cleanly() -> None:
    assert set(FEATURES) == set(S_A_FEATURES) | set(S_B_FEATURES) | set(S_C_FEATURES)
    assert len(FEATURES) == len(set(FEATURES))
    assert set(AXIS_FEATURES["A"]) == set(S_A_FEATURES)
    assert set(AXIS_FEATURES["B"]) == set(S_B_FEATURES)
    assert set(AXIS_FEATURES["C"]) == set(S_C_FEATURES)


def test_every_feature_has_hypothesis_sign() -> None:
    for feature in FEATURES:
        assert feature in HYPOTHESIS_SIGN
        assert HYPOTHESIS_SIGN[feature] in (-1, +1)


# --------------------------------------------------------------------------- #
# 합성 프레임 헬퍼
# --------------------------------------------------------------------------- #


def _frame(n: int = 200, seed: int = 7, tf_ms: int = 3_600_000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    open_ = close + rng.normal(0, 0.3, n)
    return pd.DataFrame(
        {
            "open_time": np.arange(n, dtype="int64") * tf_ms,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.abs(rng.normal(100, 10, n)) + 1.0,
        }
    )


def _candidate(*, trigger_time: int, start_time: int) -> _Candidate:
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=101.0,
        bottom=99.0,
        start_time=start_time,
        confirmed_time=start_time,
        ob_volume=100.0,
        ob_low_volume=40.0,
        ob_high_volume=60.0,
    )
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=trigger_time,
        entry_price=100.0,
        exit_time=trigger_time + 3_600_000,
        exit_price=98.5,
        reason=ExitReason.STOP_LOSS,
        stop_price=99.0,
        order_block=ob,
        trigger_time=trigger_time,
        mfe_r=0.2,
    )


# --------------------------------------------------------------------------- #
# §B 상위TF 조회 — 룩어헤드 없음
# --------------------------------------------------------------------------- #


def test_regime_index_at_is_lookahead_safe() -> None:
    htf_ms = 14_400_000  # 4h
    table = _RegimeTable.build("4h", _frame(n=300, tf_ms=htf_ms))
    times = table.open_times
    k = 100
    close_time = times[k] + htf_ms
    # 봉 k가 막 닫힌 순간 → 봉 k까지 본다.
    assert table.index_at(close_time) == k
    # 봉 k가 1ms 앞에서 아직 진행 중 → 봉 k−1까지만.
    assert table.index_at(close_time - 1) == k - 1


def test_regime_features_none_before_any_close() -> None:
    table = _RegimeTable.build("1d", _frame(n=50, tf_ms=86_400_000))
    # 첫 봉이 닫히기 전 진입 → 조회 불가(None).
    feats = table.features_at(0)
    for value in feats.values():
        assert value is None


# --------------------------------------------------------------------------- #
# §A RVOL
# --------------------------------------------------------------------------- #


def test_rvol_matches_volume_over_sma() -> None:
    frame = _frame(n=120)
    ext = _Wan209Extractor.build(frame, timeframe="1h", regime={})
    fpos = 80
    start_time = int(frame["open_time"].iloc[fpos])
    cand = _candidate(trigger_time=start_time + 3_600_000 * 5, start_time=start_time)
    feats = ext._rvol(cand)
    for n in (20, 50):
        expected = ext.volume[fpos] / ext.sma_vol[n][fpos]
        assert feats[f"rvol_sma{n}"] is not None
        assert abs(feats[f"rvol_sma{n}"] - expected) < 1e-12  # type: ignore[operator]


def test_rvol_none_when_formation_bar_missing() -> None:
    frame = _frame(n=120)
    ext = _Wan209Extractor.build(frame, timeframe="1h", regime={})
    # 프레임에 없는 start_time → 형성 봉 위치 없음 → None.
    cand = _candidate(trigger_time=999, start_time=-12345)
    feats = ext._rvol(cand)
    for n in (20, 50):
        assert feats[f"rvol_sma{n}"] is None


# --------------------------------------------------------------------------- #
# §C 밴드 하단 기울기 — 룩어헤드 없음
# --------------------------------------------------------------------------- #


def test_band_lower_slope_no_lookahead() -> None:
    frame = _frame(n=200)
    prev = 120
    full = _Wan209Extractor.build(frame, timeframe="1h", regime={})
    trunc = _Wan209Extractor.build(
        frame.iloc[: prev + 1].reset_index(drop=True), timeframe="1h", regime={}
    )
    a = full._band_lower_slope(prev)
    b = trunc._band_lower_slope(prev)
    for key in S_C_FEATURES:
        assert a[key] is not None and b[key] is not None
        assert abs(a[key] - b[key]) < 1e-12  # type: ignore[operator]


def test_band_lower_slope_warmup_none() -> None:
    frame = _frame(n=60)
    ext = _Wan209Extractor.build(frame, timeframe="1h", regime={})
    # prev=1 이면 prev−3<0 이라 창 3·5 둘 다 None.
    slope = ext._band_lower_slope(1)
    for key in S_C_FEATURES:
        assert slope[key] is None


def test_band_lower_slope_sign_declining() -> None:
    # 단조 하락 종가 → 하단 밴드도 하락 → 기울기 음수.
    n = 80
    close = np.linspace(200.0, 100.0, n)
    frame = pd.DataFrame(
        {
            "open_time": np.arange(n, dtype="int64") * 3_600_000,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 100.0),
        }
    )
    ext = _Wan209Extractor.build(frame, timeframe="1h", regime={})
    slope = ext._band_lower_slope(70)
    v3 = slope["band_lower_slope_3_atr"]
    v5 = slope["band_lower_slope_5_pct"]
    assert v3 is not None and v3 < 0
    assert v5 is not None and v5 < 0


# --------------------------------------------------------------------------- #
# 라벨 헬퍼
# --------------------------------------------------------------------------- #


def _lt(
    *,
    label: Label,
    features: dict[str, float | None],
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
) -> LabeledTrade:
    return LabeledTrade(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        side="long",
        trigger_time=0,
        label=label,
        mfe_r=0.0 if label is Label.INSTANT_DEATH else 2.0,
        r_multiple=1.5 if label is Label.WINNER else -1.0,
        features=features,
    )


# --------------------------------------------------------------------------- #
# 상위TF 분위 순위화
# --------------------------------------------------------------------------- #


def test_annotate_percentiles_ranks_within_cell() -> None:
    raw_key = "_raw_reg_4h_vol_pctl"
    final_key = "reg_4h_vol_pctl"
    labeled = [_lt(label=Label.WINNER, features={raw_key: float(i)}) for i in range(5)]
    _annotate_percentiles(labeled, {raw_key: final_key})
    ranks = sorted(lt.features[final_key] for lt in labeled)  # type: ignore[type-var]
    assert ranks == [0.0, 0.25, 0.5, 0.75, 1.0]
    for lt in labeled:
        assert raw_key not in lt.features


def test_annotate_percentiles_none_stays_none() -> None:
    raw_key = "_raw_reg_1d_dev_pctl"
    final_key = "reg_1d_dev_pctl"
    labeled = [_lt(label=Label.WINNER, features={raw_key: None})]
    _annotate_percentiles(labeled, {raw_key: final_key})
    assert labeled[0].features[final_key] is None


# --------------------------------------------------------------------------- #
# §A 문턱 스윕
# --------------------------------------------------------------------------- #


def test_threshold_sweep_splits_low_high() -> None:
    # 저 RVOL(0.5)은 전부 즉사, 고 RVOL(1.5)은 전부 승자 → Δ(저−고)=+100%p.
    labeled = [_lt(label=Label.INSTANT_DEATH, features={"rvol_sma20": 0.5}) for _ in range(15)]
    labeled += [_lt(label=Label.WINNER, features={"rvol_sma20": 1.5}) for _ in range(15)]
    rows = threshold_sweep(labeled, timeframe="1h", segment="oos", feature="rvol_sma20")
    row = next(r for r in rows if r.threshold == 1.0)
    assert row.n_low == 15 and row.n_high == 15
    assert row.death_rate_low == 1.0 and row.death_rate_high == 0.0
    assert abs(row.death_diff - 1.0) < 1e-12


# --------------------------------------------------------------------------- #
# 잔차화 · 부분상관 통제
# --------------------------------------------------------------------------- #


def test_residualize_removes_a_control_column() -> None:
    values = [float(i) for i in range(20)]
    control = [2.0 * v + 3.0 for v in values]  # 완전 선형 종속.
    resid = _residualize(values, [control])
    assert resid is not None
    assert max(abs(r) for r in resid) < 1e-9  # 통제로 전부 설명 → 잔차 ~0.


def test_partial_corr_collapses_when_feature_is_geometry_proxy() -> None:
    # 특징 = 통제 변수(존폭·손절폭)와 동일 → 존폭을 통제하면 남는 신호가 없다.
    labeled: list[LabeledTrade] = []
    for i in range(40):
        x = float(i)
        lab = Label.INSTANT_DEATH if i < 20 else Label.WINNER
        labeled.append(
            _lt(
                label=lab,
                features={"band_lower_slope_3_atr": x, "zone_width_atr": x, "stop_width_atr": x},
            )
        )
    row = partial_correlation_test(
        labeled,
        timeframe="1h",
        segment="oos",
        feature="band_lower_slope_3_atr",
        axis="death_vs_rest",
        subset=_DEATH_VS_REST[0],
        positive=_DEATH_VS_REST[1],
        permutations=200,
    )
    # 잔차가 0에 가까워 부분상관이 소멸(None) 또는 무의미 — 기하 대리변수(c) 서명.
    assert row.partial_correlation is None or row.p_value is None or row.p_value > 0.5


def test_partial_corr_survives_independent_signal() -> None:
    # 특징은 즉사를 강하게 가르고 통제 변수(존폭·손절폭)와는 무관 → 통제 뒤에도 남는다.
    rng = np.random.default_rng(0)
    labeled: list[LabeledTrade] = []
    for i in range(40):
        lab = Label.INSTANT_DEATH if i < 20 else Label.WINNER
        fval = 0.0 + 0.01 * i if i < 20 else 1.0 + 0.01 * i
        labeled.append(
            _lt(
                label=lab,
                features={
                    "band_lower_slope_3_atr": fval,
                    "zone_width_atr": float(rng.random()),
                    "stop_width_atr": float(rng.random()),
                },
            )
        )
    row = partial_correlation_test(
        labeled,
        timeframe="1h",
        segment="oos",
        feature="band_lower_slope_3_atr",
        axis="death_vs_rest",
        subset=_DEATH_VS_REST[0],
        positive=_DEATH_VS_REST[1],
        permutations=400,
    )
    assert row.partial_correlation is not None
    assert abs(row.partial_correlation) > 0.8
    assert row.p_value is not None and row.p_value < 0.05


def test_collinearity_reports_correlation_with_control() -> None:
    labeled = [
        _lt(label=Label.WINNER, features={"rvol_sma20": float(i), "zone_width_atr": float(i)})
        for i in range(30)
    ]
    row = collinearity(
        labeled, timeframe="1h", segment="oos", feature="rvol_sma20", control="zone_width_atr"
    )
    assert row.correlation is not None and row.correlation > 0.99
    assert _CONTROL_FEATURES == ("zone_width_atr", "stop_width_atr")


# --------------------------------------------------------------------------- #
# 축 판정 (a/b/c)
# --------------------------------------------------------------------------- #


def _perm_rows(labeled: list[LabeledTrade], feature: str) -> list[PermutationRow]:
    perm = []
    for segment in ("is", "oos"):
        for axis, (subset, positive) in (
            ("death_vs_rest", _DEATH_VS_REST),
            ("death_vs_winner", (_DEATH_VS_REST[0], _DEATH_VS_REST[1])),
        ):
            perm.append(
                permutation_test(
                    labeled,
                    timeframe="1h",
                    segment=segment,
                    feature=feature,
                    axis=axis,
                    subset=subset,
                    positive=positive,
                    permutations=400,
                )
            )
    return perm


def _strong_labeled(feature: str) -> list[LabeledTrade]:
    out: list[LabeledTrade] = []
    for segment in ("is", "oos"):
        for i in range(18):
            out.append(
                _lt(
                    label=Label.INSTANT_DEATH,
                    features={
                        feature: 0.0 + 0.01 * i,
                        "zone_width_atr": 0.0,
                        "stop_width_atr": 0.0,
                    },
                    segment=segment,
                )
            )
        for i in range(18):
            out.append(
                _lt(
                    label=Label.WINNER,
                    features={
                        feature: 1.0 + 0.01 * i,
                        "zone_width_atr": 0.0,
                        "stop_width_atr": 0.0,
                    },
                    segment=segment,
                )
            )
    return out


def test_axis_verdict_b_when_no_signal() -> None:
    feature = S_C_FEATURES[0]
    rng = np.random.default_rng(1)
    labeled: list[LabeledTrade] = []
    for segment in ("is", "oos"):
        for _ in range(40):
            lab = Label.INSTANT_DEATH if rng.random() < 0.3 else Label.WINNER
            labeled.append(
                _lt(
                    label=lab,
                    features={feature: float(rng.random())},
                    segment=segment,
                )
            )
    perm = _perm_rows(labeled, feature)
    code, _ = axis_verdict(perm, [], axis_name="C", features=(feature,), timeframe="1h")
    assert code == "b"


def test_axis_verdict_a_when_survivor_and_partial_survives() -> None:
    feature = S_C_FEATURES[0]
    labeled = _strong_labeled(feature)
    perm = _perm_rows(labeled, feature)
    # 부분상관이 OOS에서 유의(p<0.05)한 생존자 → (a).
    partial = [
        partial_correlation_test(
            labeled,
            timeframe="1h",
            segment=segment,
            feature=feature,
            axis="death_vs_rest",
            subset=_DEATH_VS_REST[0],
            positive=_DEATH_VS_REST[1],
            permutations=400,
        )
        for segment in ("is", "oos")
    ]
    code, _ = axis_verdict(perm, partial, axis_name="C", features=(feature,), timeframe="1h")
    assert code == "a"


def test_axis_verdict_c_when_survivor_but_geometry_proxy() -> None:
    feature = S_C_FEATURES[0]
    # 특징 == 통제 변수 → 주 검정은 넘지만 부분상관이 무너진다 → (c).
    out: list[LabeledTrade] = []
    for segment in ("is", "oos"):
        for i in range(18):
            v = 0.0 + 0.01 * i
            out.append(
                _lt(
                    label=Label.INSTANT_DEATH,
                    features={feature: v, "zone_width_atr": v, "stop_width_atr": v},
                    segment=segment,
                )
            )
        for i in range(18):
            v = 1.0 + 0.01 * i
            out.append(
                _lt(
                    label=Label.WINNER,
                    features={feature: v, "zone_width_atr": v, "stop_width_atr": v},
                    segment=segment,
                )
            )
    perm = _perm_rows(out, feature)
    partial = [
        partial_correlation_test(
            out,
            timeframe="1h",
            segment=segment,
            feature=feature,
            axis="death_vs_rest",
            subset=_DEATH_VS_REST[0],
            positive=_DEATH_VS_REST[1],
            permutations=200,
        )
        for segment in ("is", "oos")
    ]
    code, _ = axis_verdict(perm, partial, axis_name="C", features=(feature,), timeframe="1h")
    assert code == "c"


def test_regime_htfs_are_above_entry_only() -> None:
    # 상위TF는 4h·1d뿐 — 상수 정합성.
    assert _REGIME_HTFS == ("4h", "1d")


def test_regime_only_uses_strictly_higher_tf() -> None:
    """4h 진입은 reg_4h_*(같은 TF)를 쓰면 안 된다 — 규제 테이블에 4h가 있어도 None."""
    frame = _frame(n=400, tf_ms=14_400_000)  # 진입 프레임 = 4h
    reg_4h = _RegimeTable.build("4h", frame)
    reg_1d = _RegimeTable.build("1d", _frame(n=400, tf_ms=86_400_000))
    ext = _Wan209Extractor.build(frame, timeframe="4h", regime={"4h": reg_4h, "1d": reg_1d})
    tt = int(frame["open_time"].iloc[250])  # EMA200 워밍업 이후(idx 250) → 표가 값이 있음.
    cand = _candidate(trigger_time=tt, start_time=int(frame["open_time"].iloc[245]))
    feats = ext.features_for(cand)
    assert feats is not None
    # 4h 표 자체는 이 시각에 non-None을 낸다 — 그런데 4h 진입이라 필터로 걷힌다.
    assert reg_4h.features_at(tt)["reg_4h_trend"] is not None
    assert feats["reg_4h_trend"] is None
    assert feats["_raw_reg_4h_vol_pctl"] is None
    # 엄격히 큰 1d 축 키는 존재한다(값은 워밍업에 따라 다름).
    assert "reg_1d_trend" in feats


def test_regime_higher_tf_is_used_when_strictly_above() -> None:
    """1h 진입은 4h(> 1h)를 상위TF로 실제 조회한다(강제 None이 아니다)."""
    frame = _frame(n=900, tf_ms=3_600_000)  # 진입 프레임 = 1h
    reg_4h = _RegimeTable.build("4h", _frame(n=400, tf_ms=14_400_000))
    ext = _Wan209Extractor.build(frame, timeframe="1h", regime={"4h": reg_4h})
    tt = int(frame["open_time"].iloc[850])  # reg_4h 조회 idx가 워밍업 이후가 되도록 충분히 뒤.
    cand = _candidate(trigger_time=tt, start_time=int(frame["open_time"].iloc[800]))
    feats = ext.features_for(cand)
    assert feats is not None
    assert feats["reg_4h_trend"] is not None
