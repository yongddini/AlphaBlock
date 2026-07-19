"""WAN-117 존 실패 부검 — 집계·검정·판정 로직 테스트.

격자 실행(DB·수분)이 아니라 **분위표·점이연 상관·심볼 층화 순열·(a)/(b) 판정**을 손으로
만든 라벨/행으로 고정한다. 엔진 정확성(라벨링·특징 룩어헤드)은 별도 관심사이고, 여기서는
사후 통계 계층이 우연·부호뒤집힘·표본부족을 올바로 다루는지를 못 박는다.
"""

from __future__ import annotations

import random

from backtest.wan117_zone_failure_autopsy import (
    FEATURES,
    HYPOTHESIS_SIGN,
    LabeledTrade,
    PermutationRow,
    _point_biserial,
    bonferroni_alpha,
    monotonicity_verdict,
    permutation_test,
    quantile_rows,
    verdict_for_tf,
)


def _lt(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
    broke: bool,
    feature: str = "trend_dev",
    value: float | None,
    r_multiple: float | None = None,
) -> LabeledTrade:
    r = r_multiple if r_multiple is not None else (-1.0 if broke else 1.5)
    return LabeledTrade(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        side="long",
        trigger_time=0,
        broke=broke,
        r_multiple=r,
        features={feature: value},
    )


# --------------------------------------------------------------------------- #
# 상수 정합성
# --------------------------------------------------------------------------- #


def test_every_feature_has_hypothesis_sign() -> None:
    for feature in FEATURES:
        assert feature in HYPOTHESIS_SIGN


def test_bonferroni_alpha() -> None:
    assert bonferroni_alpha(10) == 0.005
    assert bonferroni_alpha(0) == 0.05


# --------------------------------------------------------------------------- #
# 1단계: 분위표 · 단조성
# --------------------------------------------------------------------------- #


def test_quantile_rows_broke_rate_and_mean_r() -> None:
    # 특징이 낮을수록 뚫린다: 하위 분위 100% 뚫림, 상위 분위 0% 뚫림.
    labeled = [_lt(broke=True, value=float(i)) for i in range(10)]
    labeled += [_lt(broke=False, value=float(i)) for i in range(20, 30)]
    rows = quantile_rows(labeled, timeframe="1h", segment="oos", feature="trend_dev")
    assert len(rows) == 3
    ordered = sorted(rows, key=lambda r: r.quantile_rank)
    assert ordered[0].broke_rate == 1.0
    assert ordered[0].mean_r == -1.0
    assert ordered[-1].broke_rate == 0.0
    assert ordered[-1].mean_r == 1.5
    assert monotonicity_verdict(rows) == "단조 감소"


def test_quantile_rows_skips_none_feature() -> None:
    labeled = [_lt(broke=True, value=None) for _ in range(5)]
    assert quantile_rows(labeled, timeframe="1h", segment="oos", feature="trend_dev") == []


# --------------------------------------------------------------------------- #
# 점이연 상관
# --------------------------------------------------------------------------- #


def test_point_biserial_sign_and_bounds() -> None:
    # 완전 분리: 낮은 값=뚫림 → 음의 상관.
    values = [0.0, 0.0, 1.0, 1.0]
    broke = [True, True, False, False]
    corr = _point_biserial(values, broke)
    assert corr is not None
    assert corr < -0.99


def test_point_biserial_zero_variance_returns_none() -> None:
    assert _point_biserial([1.0, 1.0, 1.0], [True, False, True]) is None
    assert _point_biserial([1.0, 2.0, 3.0], [True, True, True]) is None


# --------------------------------------------------------------------------- #
# 2단계: 순열 검정
# --------------------------------------------------------------------------- #


def test_permutation_detects_strong_association() -> None:
    # 30건 완전 분리 → 매우 낮은 p, 상관은 음(가설방향 trend_dev=-1과 일치).
    labeled = [_lt(broke=True, value=0.0 + 0.01 * i) for i in range(15)]
    labeled += [_lt(broke=False, value=1.0 + 0.01 * i) for i in range(15)]
    row = permutation_test(labeled, timeframe="1h", segment="oos", feature="trend_dev")
    assert row.correlation is not None and row.correlation < -0.9
    assert row.p_value is not None and row.p_value < 0.01
    assert row.direction_matches is True


def test_permutation_noise_high_p_and_deterministic() -> None:
    rng = random.Random(0)
    labeled = [_lt(broke=rng.random() < 0.5, value=rng.random()) for _ in range(60)]
    a = permutation_test(labeled, timeframe="1h", segment="oos", feature="trend_dev", seed=7)
    b = permutation_test(labeled, timeframe="1h", segment="oos", feature="trend_dev", seed=7)
    assert a.p_value == b.p_value  # 같은 시드 → 결정적.
    assert a.p_value is not None and a.p_value > 0.05  # 노이즈는 유의하지 않다.


def test_permutation_sample_gate() -> None:
    labeled = [_lt(broke=i % 2 == 0, value=float(i)) for i in range(10)]  # 20건 미만.
    row = permutation_test(labeled, timeframe="1h", segment="oos", feature="trend_dev")
    assert row.p_value is None
    assert row.permutations == 0


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _perm(
    *,
    feature: str,
    segment: str,
    corr: float | None,
    p: float | None,
    timeframe: str = "1h",
) -> PermutationRow:
    return PermutationRow(
        timeframe=timeframe,
        segment=segment,
        feature=feature,
        n=100,
        broke_rate=0.5,
        correlation=corr,
        p_value=p,
        hypothesis_sign=HYPOTHESIS_SIGN.get(feature, 0),
        direction_matches=corr is not None and corr < 0,
        permutations=2000,
    )


def test_verdict_a_when_oos_significant_and_is_same_sign() -> None:
    rows = [
        _perm(feature="trend_dev", segment="oos", corr=-0.4, p=0.0001),
        _perm(feature="trend_dev", segment="is", corr=-0.3, p=0.001),
        _perm(feature="tap_rsi", segment="oos", corr=0.02, p=0.8),
        _perm(feature="tap_rsi", segment="is", corr=0.01, p=0.9),
    ]
    verdict = verdict_for_tf(rows, timeframe="1h")
    assert "(a) 실패를 가르는" in verdict
    assert "trend_dev" in verdict


def test_verdict_b_when_sign_flips_is_to_oos() -> None:
    # OOS는 유의하지만 IS에서 부호가 반대 → 제외 → (b).
    rows = [
        _perm(feature="trend_dev", segment="oos", corr=-0.4, p=0.0001),
        _perm(feature="trend_dev", segment="is", corr=+0.3, p=0.001),
    ]
    verdict = verdict_for_tf(rows, timeframe="1h")
    assert "(b) 실패도 예측 불가" in verdict


def test_verdict_b_when_none_significant() -> None:
    rows = [
        _perm(feature="trend_dev", segment="oos", corr=-0.05, p=0.4),
        _perm(feature="trend_dev", segment="is", corr=-0.04, p=0.5),
    ]
    assert "(b) 실패도 예측 불가" in verdict_for_tf(rows, timeframe="1h")


def test_verdict_bonferroni_blocks_marginal_p() -> None:
    # p=0.03은 단독이면 유의하지만 10개 검정 Bonferroni(α'=0.005)는 못 넘는다.
    rows = []
    for i in range(10):
        feature = FEATURES[i]
        rows.append(_perm(feature=feature, segment="oos", corr=-0.1, p=0.03))
        rows.append(_perm(feature=feature, segment="is", corr=-0.1, p=0.03))
    assert "(b) 실패도 예측 불가" in verdict_for_tf(rows, timeframe="1h")
