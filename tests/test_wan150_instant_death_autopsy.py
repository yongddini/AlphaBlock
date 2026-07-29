"""WAN-150 즉사 부검 — 3분류 라벨링·즉사 축 검정·판정·RSI-EMA 룩어헤드 테스트.

격자 실행(DB·수분)이 아니라 **3분류 로직·두 축 순열·(a)/(b) 게이트·RSI-EMA 룩어헤드
없음**을 손으로 만든 라벨/합성 프레임으로 고정한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.models import ExitReason
from backtest.wan150_instant_death_autopsy import (
    _DEATH_VS_REST,
    _DEATH_VS_WINNER,
    _RSI_EMA_SPAN,
    _RSI_LENGTH,
    FEATURES,
    HYPOTHESIS_SIGN,
    Label,
    LabeledTrade,
    _Wan150Extractor,
    classify,
    permutation_test,
    quantile_rows,
    s1_gate_verdict,
    s1_survivors,
)
from strategy.indicators import rsi

# --------------------------------------------------------------------------- #
# 상수 정합성
# --------------------------------------------------------------------------- #


def test_every_feature_has_hypothesis_sign() -> None:
    for feature in FEATURES:
        assert feature in HYPOTHESIS_SIGN


# --------------------------------------------------------------------------- #
# 3분류 라벨링
# --------------------------------------------------------------------------- #


def test_classify_take_profit_is_winner() -> None:
    assert classify(ExitReason.TAKE_PROFIT, None) is Label.WINNER
    assert classify(ExitReason.TAKE_PROFIT, 5.0) is Label.WINNER


def test_classify_stop_below_threshold_is_instant_death() -> None:
    assert classify(ExitReason.STOP_LOSS, 0.0) is Label.INSTANT_DEATH
    assert classify(ExitReason.STOP_LOSS, 0.49) is Label.INSTANT_DEATH


def test_classify_stop_at_or_above_threshold_is_ambiguous() -> None:
    assert classify(ExitReason.STOP_LOSS, 0.5) is Label.AMBIGUOUS
    assert classify(ExitReason.STOP_LOSS, 1.4) is Label.AMBIGUOUS


def test_classify_stop_missing_mfe_is_unclassifiable() -> None:
    assert classify(ExitReason.STOP_LOSS, None) is None
    assert classify(ExitReason.STOP_LOSS, float("nan")) is None


def test_classify_end_of_data_is_none() -> None:
    assert classify(ExitReason.END_OF_DATA, 2.0) is None


# --------------------------------------------------------------------------- #
# LabeledTrade 헬퍼 + 분위표
# --------------------------------------------------------------------------- #


def _lt(
    *,
    label: Label,
    value: float | None,
    feature: str = "trend_dev",
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
        mfe_r=0.0 if label is Label.INSTANT_DEATH else (2.0 if label is Label.WINNER else 1.0),
        r_multiple=1.5 if label is Label.WINNER else -1.0,
        features={feature: value},
    )


def test_quantile_rows_three_class_rates_sum_to_one() -> None:
    labeled = [_lt(label=Label.INSTANT_DEATH, value=float(i)) for i in range(10)]
    labeled += [_lt(label=Label.WINNER, value=float(i)) for i in range(20, 30)]
    rows = quantile_rows(labeled, timeframe="1h", segment="oos", feature="trend_dev")
    assert len(rows) == 3
    for r in rows:
        assert abs(r.death_rate + r.ambiguous_rate + r.winner_rate - 1.0) < 1e-9
    ordered = sorted(rows, key=lambda r: r.quantile_rank)
    # 낮은 분위 = 전부 즉사, 높은 분위 = 전부 승자.
    assert ordered[0].death_rate == 1.0
    assert ordered[-1].winner_rate == 1.0


# --------------------------------------------------------------------------- #
# 즉사 축 순열 (두 축)
# --------------------------------------------------------------------------- #


def test_permutation_death_vs_rest_detects_association() -> None:
    # 낮은 값=즉사, 높은 값=승자 → 강한 음의 상관, 낮은 p, 가설방향(trend_dev=-1) 일치.
    labeled = [_lt(label=Label.INSTANT_DEATH, value=0.0 + 0.01 * i) for i in range(15)]
    labeled += [_lt(label=Label.WINNER, value=1.0 + 0.01 * i) for i in range(15)]
    subset, positive = _DEATH_VS_REST
    row = permutation_test(
        labeled,
        timeframe="1h",
        segment="oos",
        feature="trend_dev",
        axis="death_vs_rest",
        subset=subset,
        positive=positive,
        permutations=500,
    )
    assert row.correlation is not None and row.correlation < -0.9
    assert row.p_value is not None and row.p_value < 0.02
    assert row.direction_matches is True
    assert row.n == 30


def test_permutation_death_vs_winner_excludes_ambiguous() -> None:
    # 애매 10건을 섞어도 death_vs_winner 부분집합은 즉사+승자만 센다.
    labeled = [_lt(label=Label.INSTANT_DEATH, value=0.0) for _ in range(12)]
    labeled += [_lt(label=Label.WINNER, value=1.0) for _ in range(12)]
    labeled += [_lt(label=Label.AMBIGUOUS, value=0.5) for _ in range(10)]
    subset, positive = _DEATH_VS_WINNER
    row = permutation_test(
        labeled,
        timeframe="1h",
        segment="oos",
        feature="trend_dev",
        axis="death_vs_winner",
        subset=subset,
        positive=positive,
        permutations=300,
    )
    assert row.n == 24  # 애매 10건 제외.
    assert row.positive_rate == 0.5


def test_permutation_below_min_trades_returns_null() -> None:
    labeled = [_lt(label=Label.INSTANT_DEATH, value=float(i)) for i in range(5)]
    subset, positive = _DEATH_VS_REST
    row = permutation_test(
        labeled,
        timeframe="1h",
        segment="oos",
        feature="trend_dev",
        axis="death_vs_rest",
        subset=subset,
        positive=positive,
        permutations=100,
    )
    assert row.p_value is None
    assert row.permutations == 0


# --------------------------------------------------------------------------- #
# 게이트 판정
# --------------------------------------------------------------------------- #


def _strong_cell(feature: str, timeframe: str) -> list[LabeledTrade]:
    """IS·OOS 모두 강한 즉사↔특징 연관을 갖는 30+30건 셀."""
    out: list[LabeledTrade] = []
    for segment in ("is", "oos"):
        for i in range(18):
            out.append(
                _lt(
                    label=Label.INSTANT_DEATH,
                    value=0.0 + 0.01 * i,
                    feature=feature,
                    segment=segment,
                )
            )
        for i in range(18):
            out.append(
                _lt(label=Label.WINNER, value=1.0 + 0.01 * i, feature=feature, segment=segment)
            )
    return out


def test_gate_a_when_feature_beats_random() -> None:
    labeled = _strong_cell("trend_dev", "1h")
    perm = []
    for segment in ("is", "oos"):
        for axis, (subset, positive) in (
            ("death_vs_rest", _DEATH_VS_REST),
            ("death_vs_winner", _DEATH_VS_WINNER),
        ):
            perm.append(
                permutation_test(
                    labeled,
                    timeframe="1h",
                    segment=segment,
                    feature="trend_dev",
                    axis=axis,
                    subset=subset,
                    positive=positive,
                    permutations=500,
                )
            )
    code, _ = s1_gate_verdict(perm, timeframe="1h")
    assert code == "a"
    surv = s1_survivors(perm, timeframe="1h")
    assert "trend_dev" in surv["death_vs_rest"] or "trend_dev" in surv["death_vs_winner"]


def test_gate_b_when_no_signal() -> None:
    # 라벨과 특징이 무관(전부 같은 값 아님 — 무작위 배정) → 유의 없음.
    rng = np.random.default_rng(0)
    labeled: list[LabeledTrade] = []
    for segment in ("is", "oos"):
        for _ in range(40):
            lab = Label.INSTANT_DEATH if rng.random() < 0.3 else Label.WINNER
            labeled.append(
                _lt(label=lab, value=float(rng.random()), feature="trend_dev", segment=segment)
            )
    perm = []
    for segment in ("is", "oos"):
        for axis, (subset, positive) in (
            ("death_vs_rest", _DEATH_VS_REST),
            ("death_vs_winner", _DEATH_VS_WINNER),
        ):
            perm.append(
                permutation_test(
                    labeled,
                    timeframe="1h",
                    segment=segment,
                    feature="trend_dev",
                    axis=axis,
                    subset=subset,
                    positive=positive,
                    permutations=500,
                )
            )
    code, _ = s1_gate_verdict(perm, timeframe="1h")
    assert code == "b"


# --------------------------------------------------------------------------- #
# §3 RSI-EMA 룩어헤드 없음 (핵심 회귀 테스트)
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


def test_rsi_ema_matches_indicators_ema_seed_rule() -> None:
    frame = _synthetic_frame()
    ext = _Wan150Extractor.build(frame)
    expected = rsi(frame, length=_RSI_LENGTH).ewm(span=_RSI_EMA_SPAN, adjust=False).mean()
    got = pd.Series(ext.rsi_ema)
    # 워밍업 뒤 값 일치(끝자락 확인).
    assert np.allclose(got.iloc[50:].to_numpy(), expected.iloc[50:].to_numpy(), equal_nan=True)


def test_rsi_ema_shape_has_no_lookahead() -> None:
    """탭 직전 확정봉(prev)의 RSI-EMA 곡률은 prev 이후 봉을 잘라도 불변이어야 한다.

    미래 봉을 넣거나 빼도 pos−1까지의 값만 쓰므로 결과가 비트 동일해야 한다 —
    체결 순간까지의 정보만 쓴다는 성질을 동작으로 고정한다.
    """
    frame = _synthetic_frame(n=200)
    prev = 120
    full = _Wan150Extractor.build(frame)
    # prev 이후를 통째로 자른 프레임(미래를 모른다).
    trunc = _Wan150Extractor.build(frame.iloc[: prev + 1].reset_index(drop=True))

    full_shape = full._rsi_ema_shape(prev)
    trunc_shape = trunc._rsi_ema_shape(prev)
    for key in ("rsi_ema_slope", "rsi_ema_curv", "rsi_ema_death_shape"):
        a, b = full_shape[key], trunc_shape[key]
        assert a is not None and b is not None
        assert abs(a - b) < 1e-12, f"{key} leaked future info: {a} != {b}"


def test_rsi_ema_shape_warmup_returns_none() -> None:
    frame = _synthetic_frame(n=50)
    ext = _Wan150Extractor.build(frame)
    # prev=1 이면 prev−2<0 → 워밍업 제외(None), 조용한 통과 금지.
    shape = ext._rsi_ema_shape(1)
    assert shape["rsi_ema_slope"] is None
    assert shape["rsi_ema_curv"] is None
    assert shape["rsi_ema_death_shape"] is None


def test_rsi_ema_death_shape_boolean() -> None:
    frame = _synthetic_frame()
    ext = _Wan150Extractor.build(frame)
    for prev in range(30, 190):
        shape = ext._rsi_ema_shape(prev)
        flag = shape["rsi_ema_death_shape"]
        if flag is None:
            continue
        d1, d2 = shape["rsi_ema_slope"], shape["rsi_ema_curv"]
        assert d1 is not None and d2 is not None
        assert flag == (1.0 if (d1 < 0 and d2 < 0) else 0.0)
