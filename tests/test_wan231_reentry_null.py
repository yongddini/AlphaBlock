"""WAN-231 (B) 재진입 매칭 널의 자(尺)를 동작으로 고정한다.

백테스트 전체는 안 돌린다 — 순위 p·유의 게이트·널 진입 시뮬레이션·표본추출·파생 속성·CSV
왕복을 합성 값으로 검증하고, 실제 팔이 WAN-228 census와 비트 일치함은 실데이터 게이트
테스트가(있을 때만) 못 박는다. 고정하는 함정들:

1. **순위 p** — `(1 + #{널 ≥ 실제}) / (1 + K)`, K=20이면 하한 0.048(WAN-142/152/154 관행).
2. **유의 게이트 세 조건** — n≥20 **그리고** p≤0.05 **그리고** 실제>널평균(하나만 빠져도 무의).
3. **널 진입 = 그 서브스텝 종가 진입** — 엔진 시뮬레이터를 그대로 타고, 기하 무효(롱=진입가가
   손절 이하)면 None.
4. **유효 인덱스** — 창 [lo, hi) · 무효화 컷 · 기하 필터가 함께 작동한다.
5. **표본추출** — 풀 ≥ k면 비복원(중복 없음), 풀 < k면 복원(항상 k개).
6. **판정 (a)/(b)** — OOS 유의 셀이 하나라도 있으면 (a), 없으면 (b)로 갈린다.
7. **파생 속성·CSV 왕복** — sig_oos·leave-one-out·프레임 왕복(널 p None 보존)이 값을 지킨다.
"""

from __future__ import annotations

from pathlib import Path

from backtest import harness
from backtest.models import PositionSide
from backtest.substep import SubStep
from backtest.wan231_reentry_null import (
    ALPHA,
    MIN_TRADES_GATE,
    SEEDS,
    NullRow,
    _net_pp_of_entry,
    _sample,
    _short,
    _valid_indices,
    is_significant,
    leave_one_out,
    oos_actual_symbol_mean,
    rank_p_value,
    significant_counts,
    verdict,
)
from strategy.models import ConfluenceParams

HTF_MS = 3_600_000  # 1h


def _substeps(bars: list[tuple[int, float, float, float]]) -> list[SubStep]:
    """(분오프셋, high, low, close) → SubStep 리스트. htf_bar_time은 분오프셋으로 슬롯팅."""
    out: list[SubStep] = []
    for minute, high, low, close in bars:
        t = minute * 60_000
        out.append(
            SubStep(time=t, high=high, low=low, close=close, htf_bar_time=(t // HTF_MS) * HTF_MS)
        )
    return out


# --------------------------------------------------------------------------- #
# 1·2. 순위 p · 유의 게이트
# --------------------------------------------------------------------------- #


def test_rank_p_value_floor_and_correction() -> None:
    # 실제가 모든 널을 이기면 ge=0 → p = 1/(1+20) = 0.0476… (하한, WAN-142/152/154).
    nulls = [float(i) for i in range(SEEDS)]  # 0..19, 전부 실제보다 작음
    p = rank_p_value(100.0, nulls)
    assert p is not None
    assert abs(p - 1 / (SEEDS + 1)) < 1e-12
    # 실제가 전부에게 지면 ge=20 → p = 21/21 = 1.0.
    assert rank_p_value(-1.0, nulls) == 1.0
    # 절반이 실제 이상: ge=10 → 11/21.
    assert rank_p_value(9.5, nulls) == 11 / 21
    # 널 없음 → None.
    assert rank_p_value(1.0, []) is None


def test_is_significant_needs_all_three() -> None:
    # 셋 다 충족.
    assert is_significant(n=30, p_value=0.048, actual=5.0, null_mean=1.0)
    # n < 20 → 탈락.
    assert not is_significant(n=19, p_value=0.048, actual=5.0, null_mean=1.0)
    # p > 0.05 → 탈락.
    assert not is_significant(n=30, p_value=0.10, actual=5.0, null_mean=1.0)
    # 실제 ≤ 널평균 → 탈락(방향).
    assert not is_significant(n=30, p_value=0.048, actual=1.0, null_mean=1.0)
    # p None → 탈락.
    assert not is_significant(n=30, p_value=None, actual=5.0, null_mean=1.0)


# --------------------------------------------------------------------------- #
# 3·4. 널 진입 시뮬레이션 · 유효 인덱스
# --------------------------------------------------------------------------- #


def _params() -> ConfluenceParams:
    return ConfluenceParams()


def test_null_entry_wins_and_loses() -> None:
    """서브스텝 종가에 진입해 엔진 시뮬레이터로 청산 — 익절은 +, 손절은 −."""
    htf_times = [0]
    htf_closes = [100.0]
    # 진입 서브스텝(index 0): 종가 100 · 손절 90(1R=10) · 목표 115(1.5R).
    win_bars = _substeps([(0, 101.0, 99.0, 100.0), (5, 116.0, 108.0, 115.0)])
    net_win = _net_pp_of_entry(
        start=0,
        cand_side=PositionSide.LONG,
        stop_price=90.0,
        take_profit_r=1.5,
        substeps=win_bars,
        htf_times=htf_times,
        htf_closes=htf_closes,
        params=_params(),
        cfg=harness.build_config("1h"),
        funding_rates=None,
        invalidation_time=None,
    )
    assert net_win is not None and net_win > 0.0

    lose_bars = _substeps([(0, 101.0, 99.0, 100.0), (5, 95.0, 89.0, 90.5)])
    net_lose = _net_pp_of_entry(
        start=0,
        cand_side=PositionSide.LONG,
        stop_price=90.0,
        take_profit_r=1.5,
        substeps=lose_bars,
        htf_times=htf_times,
        htf_closes=htf_closes,
        params=_params(),
        cfg=harness.build_config("1h"),
        funding_rates=None,
        invalidation_time=None,
    )
    assert net_lose is not None and net_lose < 0.0


def test_null_entry_rejects_invalid_geometry() -> None:
    """롱인데 진입가(종가)가 손절 이하면 그 시각엔 들어갈 수 없다 → None."""
    bars = _substeps([(0, 86.0, 84.0, 85.0)])  # 종가 85 ≤ 손절 90
    out = _net_pp_of_entry(
        start=0,
        cand_side=PositionSide.LONG,
        stop_price=90.0,
        take_profit_r=1.5,
        substeps=bars,
        htf_times=[0],
        htf_closes=[100.0],
        params=_params(),
        cfg=harness.build_config("1h"),
        funding_rates=None,
        invalidation_time=None,
    )
    assert out is None


def test_valid_indices_window_and_geometry() -> None:
    # 종가: idx0=100(>손절), idx1=85(≤손절, 롱 무효), idx2=100(>손절), idx3=100(창 밖).
    bars = _substeps([(1, 101, 99, 100), (2, 86, 84, 85), (3, 101, 99, 100), (30, 101, 99, 100)])
    times = [s.time for s in bars]
    # 창 [0, 5분) · 롱 · 손절 90. idx1은 기하 탈락, idx3은 창 밖 → {0, 2}.
    idx = _valid_indices(
        0,
        5 * 60_000,
        cand_side=PositionSide.LONG,
        stop_price=90.0,
        substeps=bars,
        substep_times=times,
        invalidation_time=None,
    )
    assert idx == (0, 2)
    # 무효화 컷(2.5분)이 idx2(time=3분)를 자른다 → 기하 유효한 idx0만 남는다.
    idx_cut = _valid_indices(
        0,
        5 * 60_000,
        cand_side=PositionSide.LONG,
        stop_price=90.0,
        substeps=bars,
        substep_times=times,
        invalidation_time=int(2.5 * 60_000),
    )
    assert idx_cut == (0,)
    # lo_time '직후' 배타: lo=1분이면 idx0(time=1분)은 빠지고 idx2만.
    idx_lo = _valid_indices(
        60_000,
        5 * 60_000,
        cand_side=PositionSide.LONG,
        stop_price=90.0,
        substeps=bars,
        substep_times=times,
        invalidation_time=None,
    )
    assert idx_lo == (2,)


# --------------------------------------------------------------------------- #
# 5. 표본추출
# --------------------------------------------------------------------------- #


def test_sample_no_replacement_then_replacement() -> None:
    import random

    rng = random.Random(0)
    pool = (10, 11, 12, 13, 14)
    # 풀 ≥ k → 비복원(중복 없음).
    drawn = _sample(rng, pool, 3)
    assert len(drawn) == 3
    assert len(set(drawn)) == 3
    assert all(d in pool for d in drawn)
    # 풀 < k → 복원(항상 k개, 중복 허용).
    drawn2 = _sample(random.Random(0), (7,), 4)
    assert drawn2 == [7, 7, 7, 7]


# --------------------------------------------------------------------------- #
# 6·7. 판정 · 파생 속성 · 집계 · CSV 왕복
# --------------------------------------------------------------------------- #


def _row(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    re_oos_n: int = 30,
    actual_oos: float = 10.0,
    null_oos: float = 1.0,
    p_oos: float | None = 0.048,
    re_is_n: int = 30,
    actual_is: float = 5.0,
    null_is: float = 1.0,
    p_is: float | None = 0.20,
) -> NullRow:
    return NullRow(
        symbol=symbol,
        timeframe=timeframe,
        window_start=0,
        window_end=1_000,
        seeds=SEEDS,
        adopted_entries=100,
        tp_entries=60,
        reentries_total=re_oos_n + re_is_n,
        re_is_n=re_is_n,
        actual_is_net_pp=actual_is,
        null_is_mean_net_pp=null_is,
        p_is=p_is,
        re_oos_n=re_oos_n,
        actual_oos_net_pp=actual_oos,
        null_oos_mean_net_pp=null_oos,
        p_oos=p_oos,
        funding_coverage=1.0,
    )


def test_sig_properties() -> None:
    assert _row().sig_oos is True
    # n<20 → 무의.
    assert _row(re_oos_n=15).sig_oos is False
    # 실제 ≤ 널 → 무의.
    assert _row(actual_oos=0.5).sig_oos is False
    # p None → 무의.
    assert _row(p_oos=None).sig_oos is False


def test_significant_counts() -> None:
    rows = [
        _row(symbol="ETH/USDT:USDT", re_oos_n=30, p_oos=0.048, actual_oos=9.0),  # OOS 유의
        _row(symbol="SOL/USDT:USDT", re_oos_n=15),  # n<20 → 유효 아님
        _row(symbol="XRP/USDT:USDT", re_oos_n=40, p_oos=0.5),  # 유효하나 무의
    ]
    valid_oos, sig_oos, _sig_is = significant_counts(rows, "1h")
    assert valid_oos == 2  # n≥20인 셀 둘
    assert sig_oos == 1


def test_verdict_a_when_any_oos_significant() -> None:
    rows = [_row(re_oos_n=30, p_oos=0.048, actual_oos=9.0, null_oos=1.0)]
    out = verdict(rows)
    assert out.startswith("**(a) 실제 > 무작위-시각")
    # (a)는 「타이밍이 아니라 가격」 경고(WAN-131)를 반드시 달고 나온다.
    assert "가격" in out and "WAN-131" in out


def test_verdict_b_when_none_significant() -> None:
    rows = [_row(re_oos_n=30, p_oos=0.9, actual_oos=1.0, null_oos=5.0)]
    assert verdict(rows).startswith("**(b) 무의")


def test_leave_one_out_and_symbol_mean() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", actual_oos=6.0),
        _row(symbol="ETH/USDT:USDT", actual_oos=12.0),
    ]
    assert oos_actual_symbol_mean(rows) == 9.0
    assert leave_one_out(rows, "ETH") == 6.0  # ETH 빼면 BTC만
    assert leave_one_out(rows, "DOGE") is None  # 없는 심볼


def test_short_helper() -> None:
    assert _short("BTC/USDT:USDT") == "BTC"
    assert _short("DOGE/USDT:USDT") == "DOGE"


def test_frame_roundtrip_preserves_none_p(tmp_path: Path) -> None:
    from backtest.wan231_reentry_null import cells_from_csv, cells_to_frame

    rows = [
        _row(symbol="BTC/USDT:USDT", timeframe="4h", p_oos=None, re_oos_n=5),
        _row(symbol="ETH/USDT:USDT", timeframe="1h", actual_oos=-3.0),
    ]
    path = tmp_path / "wan231.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    restored = cells_from_csv(path)
    assert len(restored) == 2
    by_symbol = {r.symbol: r for r in restored}
    assert by_symbol["BTC/USDT:USDT"].p_oos is None  # None이 NaN→None으로 보존
    assert by_symbol["ETH/USDT:USDT"].actual_oos_net_pp == -3.0


def test_thresholds_are_constants() -> None:
    assert SEEDS == 20
    assert MIN_TRADES_GATE == 20
    assert 0.0 < ALPHA < 1.0


def test_merge_rows_appends_new_tf_and_overrides_same_cell() -> None:
    """`--append` 병합(WAN-242): 새 TF는 덧붙고, 같은 (심볼,TF)는 새 행이 이긴다."""
    from backtest.wan231_reentry_null import merge_rows

    existing = [
        _row(symbol="BTC/USDT:USDT", timeframe="4h", actual_oos=1.0),
        _row(symbol="BTC/USDT:USDT", timeframe="1h", actual_oos=2.0),
    ]
    new = [
        _row(symbol="BTC/USDT:USDT", timeframe="15m", actual_oos=3.0),  # 새 TF → 덧붙음
        _row(symbol="BTC/USDT:USDT", timeframe="1h", actual_oos=9.0),  # 겹침 → 갱신
    ]
    merged = merge_rows(existing, new)
    # 4h는 유지, 1h는 새 값, 15m 추가 → 총 3행.
    assert len(merged) == 3
    by_tf = {r.timeframe: r for r in merged}
    assert by_tf["4h"].actual_oos_net_pp == 1.0  # 건드리지 않음
    assert by_tf["1h"].actual_oos_net_pp == 9.0  # 새 행이 이김
    assert by_tf["15m"].actual_oos_net_pp == 3.0  # 새 TF 덧붙음
    # 기존 유지 행이 앞, 새 행이 뒤(diff 최소화 순서).
    assert merged[0].timeframe == "4h"
    assert merged[-1].timeframe == "1h"  # new의 마지막


def test_15m_actual_arm_matches_wan229_census() -> None:
    """완료기준 4 — 15m 실제 팔 재진입 수가 WAN-229 census(15m)와 비트 일치.

    널 모듈의 실제 팔은 WAN-228 census의 `reentry_events`를 그대로 재사용하므로 TF와
    무관하게 census와 같은 재진입을 낸다. 무거운 15m 백테스트를 CI에서 다시 돌리지 않고
    **커밋된 두 산출물 CSV를 정적으로 대조**해 `reentries_total`·`re_is_n`·`re_oos_n`가 한
    글자도 다르지 않음을 못 박는다(WAN-229 census(15m) 기준). 15m 행이 아직 없으면 skip.
    """
    import pytest

    from backtest.wan229_reentry_census_15m import DEFAULT_CELLS_CSV as census_15m_csv
    from backtest.wan231_reentry_null import DEFAULT_CELLS_CSV as null_csv
    from backtest.wan231_reentry_null import cells_from_csv

    if not null_csv.exists() or not census_15m_csv.exists():
        pytest.skip("wan231/wan229 산출물 CSV가 없어 15m 검산을 건너뜁니다.")
    null_15m = {r.symbol: r for r in cells_from_csv(null_csv) if r.timeframe == "15m"}
    if not null_15m:
        pytest.skip("wan231 CSV에 15m 행이 아직 없습니다(WAN-242 실행 전).")

    import pandas as pd

    census = pd.read_csv(census_15m_csv)
    assert len(census) > 0
    for rec in census.to_dict(orient="records"):
        n = null_15m.get(str(rec["symbol"]))
        assert n is not None, f"census 15m 심볼 {rec['symbol']}가 널 CSV에 없습니다."
        assert n.reentries_total == int(rec["reentries_total"])
        assert n.re_is_n == int(rec["re_is_n"])
        assert n.re_oos_n == int(rec["re_oos_n"])


# --------------------------------------------------------------------------- #
# 검산(완료기준 4) — 실제 팔 ≡ WAN-228 census (실데이터 있을 때만 · 짧은 창)
# --------------------------------------------------------------------------- #


def test_actual_arm_bit_matches_wan228_census() -> None:
    """널 모듈의 실제 팔이 WAN-228 census와 비트 일치한다(같은 `reentry_events` 재사용).

    6년 CSV와 맞추면 비싸므로 짧은 못 박은 창에서 census와 널을 각각 돌려 재진입 수·버킷
    개수·격리 순수익 합이 한 글자도 다르지 않음을 못 박는다. 실데이터 없으면 skip(CI 기본).
    """
    import pytest

    from backtest import wan228_reentry_census as census
    from backtest import wan231_reentry_null as null
    from backtest.run import parse_date_ms

    symbol, tf = "BTC/USDT:USDT", "1h"
    start_ms, end_ms = parse_date_ms("2024-01-01"), parse_date_ms("2024-04-01")
    probe = harness.load_market_data(symbol, tf, start_ms=start_ms, end_ms=end_ms, need_1m=False)
    if probe.empty:
        pytest.skip("BTC 1h 실데이터가 없어 census 검산을 건너뜁니다(CI 기본).")

    c_task = census._Task(symbol=symbol, timeframe=tf, start_ms=start_ms, end_ms=end_ms)
    n_task = null._Task(symbol=symbol, timeframe=tf, start_ms=start_ms, end_ms=end_ms)
    c_row = census.run_cell(c_task, log=False)
    n_row = null.run_cell(n_task, log=False)
    assert c_row is not None and n_row is not None

    assert n_row.reentries_total == c_row.reentries_total
    assert n_row.re_is_n == c_row.re_is_n
    assert n_row.re_oos_n == c_row.re_oos_n
    assert abs(n_row.actual_is_net_pp - c_row.re_is_net_pp_sum) < 1e-9
    assert abs(n_row.actual_oos_net_pp - c_row.re_oos_net_pp_sum) < 1e-9
