"""WAN-263 조건부 재진입 매칭 널의 자(尺)를 동작으로 고정한다.

백테스트 전체는 안 돌린다 — same-count 매칭 널·문턱 얼리기·조건 keep 마스크·심볼 간 풀링·
판정·CSV 왕복을 합성 값으로 검증하고, 실제 팔의 무필터 합이 WAN-228 census와 비트 일치함은
실데이터 게이트 테스트가(있을 때만) 못 박는다. 고정하는 함정들:

1. **문턱 = IS 중앙값**(argmax 아님) · trend은 문턱 없음(이진).
2. **keep 마스크 방향** — zone_width ≤ 문턱(좁은 존) · price_adv ≥ 문턱(유리) · trend=추세순.
3. **same-count 매칭 널** — 전체 풀에서 m개를 뽑되 시드 SEEDS개, m=0이면 전부 0.
4. **풀링 = 심볼 간 seed-정렬 합** — 각 심볼 널합을 같은 시드 인덱스끼리 더한다.
5. **유의 = n≥20 & p≤0.05 & 실제>널평균**(하나만 빠져도 무의) · 풀드/셀 양쪽.
6. **(c) prior_exit은 데이터 축이 아니다** — CONDITIONS에 없고 판정이 그 사실을 밝힌다.
7. **CSV 왕복** — null_sums(JSON)·None 문턱 보존 · --append 병합.
"""

from __future__ import annotations

import json
from pathlib import Path

from backtest.wan231_reentry_null import ALPHA, MIN_TRADES_GATE, SEEDS, rank_p_value
from backtest.wan263_reentry_selection import (
    CONDITIONS,
    TREND_EMA_LENGTH,
    CondRow,
    Pooled,
    _AnnotatedReentry,
    cells_from_csv,
    cells_to_frame,
    is_threshold,
    keep_mask,
    leave_one_out,
    matched_null_sums,
    merge_rows,
    oos_kept_symbol_mean,
    per_cell_sig_counts,
    pool,
    verdict,
)


def _ev(
    net: float, *, zw: float | None = 1.0, adv: float = 0.5, trend: bool = True
) -> _AnnotatedReentry:
    return _AnnotatedReentry(
        entry_time=0, net_pp=net, zone_width_atr=zw, price_adv_r=adv, with_trend=trend
    )


# --------------------------------------------------------------------------- #
# 1. 문턱 = IS 중앙값
# --------------------------------------------------------------------------- #


def test_is_threshold_is_median_not_argmax() -> None:
    events = [_ev(0.0, zw=0.5), _ev(0.0, zw=1.0), _ev(0.0, zw=3.0)]
    assert is_threshold(events, "zone_width") == 1.0  # 중앙값(argmax=3.0이 아니다)
    events2 = [_ev(0.0, adv=0.2), _ev(0.0, adv=0.4), _ev(0.0, adv=0.9)]
    assert is_threshold(events2, "price_adv") == 0.4
    # trend은 이진 → 문턱 없음.
    assert is_threshold(events, "trend") is None
    # 값이 하나도 없으면 None(예: 전부 zone_width None).
    assert is_threshold([_ev(0.0, zw=None)], "zone_width") is None


# --------------------------------------------------------------------------- #
# 2. keep 마스크 방향
# --------------------------------------------------------------------------- #


def test_keep_mask_directions() -> None:
    events = [_ev(0.0, zw=0.5), _ev(0.0, zw=1.0), _ev(0.0, zw=2.0)]
    # zone_width: 유리 = 좁은 존(≤ 문턱).
    assert keep_mask(events, "zone_width", 1.0) == [True, True, False]
    # None 값은 zone_width로 못 고른다.
    assert keep_mask([_ev(0.0, zw=None)], "zone_width", 1.0) == [False]
    adv = [_ev(0.0, adv=0.1), _ev(0.0, adv=0.5), _ev(0.0, adv=0.9)]
    # price_adv: 유리 = 클수록(≥ 문턱).
    assert keep_mask(adv, "price_adv", 0.5) == [False, True, True]
    tr = [_ev(0.0, trend=True), _ev(0.0, trend=False)]
    # trend: 추세순만(문턱 무관).
    assert keep_mask(tr, "trend", None) == [True, False]
    # 연속 축인데 문턱 None(IS 값 없음) → 아무것도 안 남긴다.
    assert keep_mask(events, "zone_width", None) == [False, False, False]


# --------------------------------------------------------------------------- #
# 3. same-count 매칭 널
# --------------------------------------------------------------------------- #


def test_matched_null_sums_shape_and_zero() -> None:
    pool_net = [1.0, 2.0, 3.0, 4.0, 5.0]
    sums = matched_null_sums(pool_net, 2)
    assert len(sums) == SEEDS
    # 각 합은 두 원소의 합이라 [1+2, 4+5] 범위 안.
    assert all(3.0 <= s <= 9.0 for s in sums)
    # m=0 → 전부 0.
    assert matched_null_sums(pool_net, 0) == [0.0] * SEEDS
    # m == 풀 크기 → 항상 전체 합(무작위성 없음).
    full = matched_null_sums(pool_net, len(pool_net))
    assert all(abs(s - sum(pool_net)) < 1e-9 for s in full)


def test_matched_null_deterministic() -> None:
    pool_net = [float(i) for i in range(10)]
    assert matched_null_sums(pool_net, 3) == matched_null_sums(pool_net, 3)


# --------------------------------------------------------------------------- #
# 4·5. 풀링 · 유의
# --------------------------------------------------------------------------- #


def _row(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    condition: str = "zone_width",
    bucket: str = "oos",
    n_bucket: int = 60,
    n_kept: int = 30,
    actual: float = 10.0,
    null_sums: list[float] | None = None,
    p_value: float | None = None,
) -> CondRow:
    sums = null_sums if null_sums is not None else [1.0] * SEEDS
    return CondRow(
        symbol=symbol,
        timeframe=timeframe,
        condition=condition,
        bucket=bucket,
        window_start=0,
        window_end=1_000,
        n_bucket=n_bucket,
        n_kept=n_kept,
        threshold=1.0,
        all_net_pp=20.0,
        actual_net_pp=actual,
        null_mean_net_pp=sum(sums) / SEEDS,
        null_sums=json.dumps(sums),
        p_value=p_value if p_value is not None else rank_p_value(actual, sums),
        funding_coverage=1.0,
    )


def test_pool_seed_aligned_sum() -> None:
    a = _row(symbol="BTC/USDT:USDT", n_kept=25, actual=10.0, null_sums=[1.0] * SEEDS)
    b = _row(symbol="ETH/USDT:USDT", n_kept=25, actual=20.0, null_sums=[2.0] * SEEDS)
    p = pool([a, b], "1h", "zone_width", "oos")
    assert p is not None
    assert p.n_kept == 50
    assert abs(p.actual_net_pp - 30.0) < 1e-9  # 실제 합
    assert abs(p.null_mean_net_pp - 3.0) < 1e-9  # 시드별 1+2=3의 평균
    # 실제 30 > 모든 널합 3 → p 하한.
    assert p.p_value is not None and abs(p.p_value - 1 / (SEEDS + 1)) < 1e-12
    assert p.significant is True
    # 없는 조합 → None.
    assert pool([a, b], "4h", "zone_width", "oos") is None


def test_pooled_significance_gates() -> None:
    # n_kept 합 < 20 → 무효.
    small = Pooled(
        timeframe="1h",
        condition="trend",
        bucket="oos",
        n_kept=19,
        n_bucket=40,
        all_net_pp=5.0,
        actual_net_pp=10.0,
        null_mean_net_pp=1.0,
        p_value=0.048,
    )
    assert small.valid is False and small.significant is False
    # 실제 ≤ 널 → 무의.
    down = Pooled(
        timeframe="1h",
        condition="trend",
        bucket="oos",
        n_kept=40,
        n_bucket=80,
        all_net_pp=5.0,
        actual_net_pp=1.0,
        null_mean_net_pp=5.0,
        p_value=0.048,
    )
    assert down.significant is False


def test_per_cell_sig_counts() -> None:
    rows = [
        _row(symbol="ETH/USDT:USDT", n_kept=30, actual=9.0, null_sums=[1.0] * SEEDS),  # 유의
        _row(symbol="SOL/USDT:USDT", n_kept=15),  # n<20 → 유효 아님
        _row(symbol="XRP/USDT:USDT", n_kept=40, actual=-5.0, null_sums=[1.0] * SEEDS),  # 무의
    ]
    valid, sig = per_cell_sig_counts(rows, "1h", "zone_width")
    assert valid == 2
    assert sig == 1


def test_cond_row_significant_property() -> None:
    good = _row(n_kept=30, actual=10.0, null_sums=[1.0] * SEEDS)
    assert good.significant is True
    assert good.null_sum_list == [1.0] * SEEDS
    assert _row(n_kept=10).significant is False  # n<20


# --------------------------------------------------------------------------- #
# LOO · 심볼평균
# --------------------------------------------------------------------------- #


def test_leave_one_out_and_symbol_mean() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", actual=6.0),
        _row(symbol="ETH/USDT:USDT", actual=12.0),
    ]
    assert oos_kept_symbol_mean(rows, "1h", "zone_width") == 9.0
    assert leave_one_out(rows, "1h", "zone_width", "ETH") == 6.0
    assert leave_one_out(rows, "1h", "zone_width", "DOGE") is None


# --------------------------------------------------------------------------- #
# 6. 판정 · (c) prior_exit 축 부재
# --------------------------------------------------------------------------- #


def test_conditions_exclude_prior_exit() -> None:
    assert "prior_exit" not in CONDITIONS
    assert set(CONDITIONS) == {"zone_width", "price_adv", "trend"}


def test_verdict_a_when_any_pooled_oos_significant() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", n_kept=25, actual=10.0, null_sums=[1.0] * SEEDS),
        _row(symbol="ETH/USDT:USDT", n_kept=25, actual=10.0, null_sums=[1.0] * SEEDS),
    ]
    out = verdict(rows)
    assert out.startswith("**(a)")
    # (c) 축 부재를 반드시 밝힌다.
    assert "prior_exit" in out
    assert "WAN-108" in out  # 자유 파라미터 경고


def test_verdict_b_when_none_significant() -> None:
    rows = [_row(symbol="BTC/USDT:USDT", n_kept=40, actual=-5.0, null_sums=[1.0] * SEEDS)]
    assert verdict(rows).startswith("**(b)")


# --------------------------------------------------------------------------- #
# 7. CSV 왕복 · 병합
# --------------------------------------------------------------------------- #


def test_frame_roundtrip_preserves_null_sums_and_none(tmp_path: Path) -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", timeframe="4h", condition="trend", bucket="oos"),
        CondRow(
            symbol="ETH/USDT:USDT",
            timeframe="1h",
            condition="zone_width",
            bucket="is",
            window_start=0,
            window_end=1_000,
            n_bucket=5,
            n_kept=0,
            threshold=None,
            all_net_pp=0.0,
            actual_net_pp=0.0,
            null_mean_net_pp=0.0,
            null_sums=json.dumps([0.0] * SEEDS),
            p_value=None,
            funding_coverage=None,
        ),
    ]
    path = tmp_path / "wan263.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    restored = cells_from_csv(path)
    assert len(restored) == 2
    by_key = {(r.symbol, r.condition): r for r in restored}
    eth = by_key[("ETH/USDT:USDT", "zone_width")]
    assert eth.threshold is None  # None 보존
    assert eth.p_value is None
    assert eth.null_sum_list == [0.0] * SEEDS  # JSON 리스트 왕복
    btc = by_key[("BTC/USDT:USDT", "trend")]
    assert len(btc.null_sum_list) == SEEDS


def test_merge_rows_appends_new_tf_and_overrides_same_key() -> None:
    existing = [
        _row(symbol="BTC/USDT:USDT", timeframe="4h", condition="trend", actual=1.0),
        _row(symbol="BTC/USDT:USDT", timeframe="1h", condition="trend", actual=2.0),
    ]
    new = [
        _row(symbol="BTC/USDT:USDT", timeframe="15m", condition="trend", actual=3.0),  # 새 TF
        _row(symbol="BTC/USDT:USDT", timeframe="1h", condition="trend", actual=9.0),  # 겹침
    ]
    merged = merge_rows(existing, new)
    assert len(merged) == 3
    by_tf = {r.timeframe: r for r in merged}
    assert by_tf["4h"].actual_net_pp == 1.0
    assert by_tf["1h"].actual_net_pp == 9.0
    assert by_tf["15m"].actual_net_pp == 3.0


def test_thresholds_are_constants() -> None:
    assert SEEDS == 20
    assert MIN_TRADES_GATE == 20
    assert 0.0 < ALPHA < 1.0
    assert TREND_EMA_LENGTH == 60


# --------------------------------------------------------------------------- #
# 검산 — 무필터 합 ≡ WAN-228 census (실데이터 있을 때만 · 짧은 창)
# --------------------------------------------------------------------------- #


def test_all_net_pp_matches_wan228_census() -> None:
    """조건 없이 다 들어갈 때(`all_net_pp`)의 버킷 합이 WAN-228 census와 비트 일치.

    이 모듈의 재진입 이벤트는 census의 `_iter_reentries`를 그대로 재사용하므로 무필터 합은
    census의 `re_*_net_pp_sum`과 한 글자도 다르지 않아야 한다. 실데이터 없으면 skip(CI 기본).
    """
    import pytest

    from backtest import harness
    from backtest import wan228_reentry_census as census
    from backtest import wan263_reentry_selection as sel
    from backtest.run import parse_date_ms

    symbol, tf = "BTC/USDT:USDT", "1h"
    start_ms, end_ms = parse_date_ms("2022-01-01"), parse_date_ms("2024-01-01")
    probe = harness.load_market_data(symbol, tf, start_ms=start_ms, end_ms=end_ms, need_1m=False)
    if probe.empty:
        pytest.skip("BTC 1h 실데이터가 없어 census 검산을 건너뜁니다(CI 기본).")

    c_row = census.run_cell(
        census._Task(symbol=symbol, timeframe=tf, start_ms=start_ms, end_ms=end_ms), log=False
    )
    assert c_row is not None
    if c_row.reentries_total == 0:
        pytest.skip("이 창에 재진입이 없어 검산할 값이 없습니다.")
    sel_rows = sel.run_cell(
        sel._Task(symbol=symbol, timeframe=tf, start_ms=start_ms, end_ms=end_ms), log=False
    )
    assert sel_rows
    # 각 조건은 같은 전체 풀을 보므로 all_net_pp는 조건과 무관하게 같다.
    by_bucket = {r.bucket: r for r in sel_rows if r.condition == "zone_width"}
    assert abs(by_bucket["is"].all_net_pp - c_row.re_is_net_pp_sum) < 1e-9
    assert abs(by_bucket["oos"].all_net_pp - c_row.re_oos_net_pp_sum) < 1e-9
    assert by_bucket["is"].n_bucket == c_row.re_is_n
    assert by_bucket["oos"].n_bucket == c_row.re_oos_n
