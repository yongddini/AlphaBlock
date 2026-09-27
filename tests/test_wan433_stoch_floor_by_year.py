"""WAN-433: 연도 버킷과 **판정 상수**를 동작으로 고정한다.

이 모듈이 지키는 것 넷:

1. **축을 새로 만들지 않았다** — 하한 점·문턱·보유 봉·TF·종목·부호 자가 WAN-424/432의
   **그 객체**다(값을 베껴 쓰면 두 표의 자가 조용히 갈라진다 · WAN-91/95/112/123/159).
2. **판정 상수를 결과를 보고 옮기지 않는다** — `IS_YEARS`·`MULTI_YEAR_GATE`·과반 규칙이
   착수 전에 박힌 그 값이고, 판정문은 **코드가** 낸다(WAN-161).
3. **버킷이 거래를 잃지도 겹치지도 않는다** — 연도로 나눈 뒤 다시 합치면 한 덩어리로 센 것과
   같다(완료기준 3의 단위 테스트 판).
4. **미결정을 「없다」로 접지 않는다** — 2σ가 차이를 덮는 해는 별도 갈래이고 판정문에 개수가
   실린다(완료기준 4).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest import wan424_stoch_ob_arm as w424
from backtest import wan432_stoch_floor_robustness as w432
from backtest import wan433_stoch_floor_by_year as m

# --- 1. 축·상수가 이슈 사양 그대로인가 -------------------------------------------------


def test_axes_are_inherited_not_redefined() -> None:
    """하한 점·문턱·보유 봉·TF·종목은 **물려받은 그 객체**다 — 이 이슈가 더한 축은 연도 하나다."""
    assert m.FLOORS is w432.FLOORS
    assert m.ARM_FLOOR is w432.ARM_FLOOR
    assert m.ADOPTED_FLOOR is w432.ADOPTED_FLOOR
    assert m.SIGN_SIGMA is w432.SIGN_SIGMA
    assert m.NOISE_R is w432.NOISE_R
    assert m.CHECKSUM_TOL is w432.CHECKSUM_TOL
    assert m.THRESHOLDS is w424.THRESHOLDS
    assert m.HOLD_BARS is w424.HOLD_BARS
    assert m.TIMEFRAMES is w424.TIMEFRAMES
    assert m.SYMBOLS is w424.SYMBOLS


def test_contrast_pair_is_the_issue_pair() -> None:
    """대조 쌍은 채택 하한(4%) 대 **느슨한 하한 1%**다(바닥 0.5%가 아니다 — 이슈 제목·사양)."""
    assert m.LOOSE_FLOOR == 0.01
    assert m.ADOPTED_FLOOR == 0.04
    assert m.LOOSE_FLOOR in m.FLOORS and m.ADOPTED_FLOOR in m.FLOORS


def test_verdict_constants_are_pinned_before_the_run() -> None:
    """🚨 결과를 보고 옮기지 않는다(WAN-161) — 이 값이 바뀌면 판정문의 뜻이 바뀐다."""
    assert m.IS_YEARS == (2020, 2021, 2022, 2023)
    assert m.MULTI_YEAR_GATE == 2
    assert 2024 not in m.IS_YEARS, "2024는 2/3 경계가 걸치는 해라 앞구간 판정에서 뺀다."


def test_display_combo_comes_from_the_prior_reference_not_from_this_table() -> None:
    """표시 조합은 **WAN-423 §7 기준값**에서 파생한다 — 내 표의 최선 칸을 고르면 과최적화 보고다."""
    hold, threshold = m.display_combo()
    assert (hold, m.ADOPTED_FLOOR, threshold) in w424.WAN423_REFERENCE
    # 기준값 두 행 중 `full` 표본이 큰 쪽(성적이 아니라 표본 크기로 고른다).
    assert (hold, threshold) == (4, 25.0)
    best_n = w424.WAN423_REFERENCE[(hold, m.ADOPTED_FLOOR, threshold)]["full"][1]
    for (_h, f, _t), segs in w424.WAN423_REFERENCE.items():
        if round(f, 6) == round(m.ADOPTED_FLOOR, 6):
            assert segs["full"][1] <= best_n


# --- 2. 연도는 데이터 축이라 UTC다 -----------------------------------------------------


def test_year_of_is_utc_not_kst() -> None:
    """🚨 KST로 세면 같은 거래가 두 표에서 다른 해로 간다(WAN-172: 계산은 UTC · 표시만 KST)."""
    # 2021-01-01 00:30 UTC = 2021-01-01 09:30 KST → 어느 시간대든 2021.
    assert m.year_of(int(dt.datetime(2021, 1, 1, 0, 30, tzinfo=dt.UTC).timestamp() * 1000)) == 2021
    # 2020-12-31 20:00 UTC = 2021-01-01 05:00 KST → **UTC 기준 2020**이어야 한다.
    boundary = int(dt.datetime(2020, 12, 31, 20, 0, tzinfo=dt.UTC).timestamp() * 1000)
    assert m.year_of(boundary) == 2020


# --- 3. 버킷이 거래를 잃지도 겹치지도 않는다 (완료기준 3의 단위 판) --------------------


def _row(
    *,
    year: int,
    floor: float,
    n: int,
    net: float,
    gross: float,
    cost: float,
    se_net: float = 0.01,
    se_gross: float = 0.01,
    segment: str = "full",
    threshold: float | None = 25.0,
    hold: int = 4,
) -> m.YearRow:
    return m.YearRow(
        segment=segment,
        year=year,
        floor=floor,
        threshold=threshold,
        hold=hold,
        num_trades=n,
        win_rate=0.5,
        mean_net_r=net,
        se_net_r=se_net,
        sum_net_r=net * n,
        mean_gross_r=gross,
        se_gross_r=se_gross,
        mean_cost_r=cost,
        identity_max_abs=0.0,
        median_stop_width=0.05,
    )


def test_pool_years_is_trade_weighted_not_year_weighted() -> None:
    """🚨 연도별 평균을 다시 평균하면 **얇은 해가 과대 대표**된다(WAN-406이 파리티에서 잡은 함정).

    버킷 합은 **거래 가중**이어야 한다 — 그래서 `YearRow`가 평균만이 아니라 `sum_net_r`를 든다.
    """
    rows = [
        _row(year=2021, floor=0.04, n=1, net=+1.0, gross=+1.1, cost=0.1),
        _row(year=2022, floor=0.04, n=99, net=-1.0, gross=-0.9, cost=0.1),
    ]
    pooled = m.pool_years(rows)[(4, 0.04, 25.0, "full")]
    assert pooled["num_trades"] == 100
    naive_year_mean = (1.0 + -1.0) / 2
    trade_weighted = pooled["net"] / pooled["num_trades"]
    assert trade_weighted == pytest.approx((1.0 * 1 + -1.0 * 99) / 100)
    assert trade_weighted != pytest.approx(naive_year_mean)


def test_bucket_partition_reassembles_to_the_single_pass_value() -> None:
    """연도로 쪼갠 뒤 다시 합치면 한 덩어리로 센 것과 같다(부동소수 합산 순서 차이만 허용)."""
    trades = [(2021, +0.3), (2021, -1.0), (2022, +1.5), (2023, -1.0), (2023, +0.7), (2023, +0.2)]
    whole = sum(r for _, r in trades) / len(trades)
    rows = []
    for year in sorted({y for y, _ in trades}):
        rs = [r for y, r in trades if y == year]
        rows.append(
            _row(year=year, floor=0.04, n=len(rs), net=sum(rs) / len(rs), gross=0.0, cost=0.0)
        )
    pooled = m.pool_years(rows)[(4, 0.04, 25.0, "full")]
    assert pooled["num_trades"] == len(trades)
    assert pooled["net"] / pooled["num_trades"] == pytest.approx(whole, abs=1e-12)


def test_checksum_rejects_a_bucket_that_loses_a_trade(tmp_path: Path) -> None:
    """🚨 버킷이 거래를 잃으면 검산이 **거래 수 정수 불일치**로 죽어야 한다(조용히 통과 금지)."""
    ref = pd.DataFrame(
        [
            {
                "hold": 4,
                "floor": 0.04,
                "threshold": 25.0,
                "segment": "full",
                "num_trades": 10,
                "win_rate": 0.5,
                "mean_net_r": 0.1,
                "gross_r": 0.2,
                "cost_r": 0.1,
            }
        ]
    )
    csv = tmp_path / "ref.csv"
    ref.to_csv(csv, index=False)
    good = [_row(year=2021, floor=0.04, n=10, net=0.1, gross=0.2, cost=0.1)]
    lines, worst = m.checksum_wan432(good, reference_csv=csv)
    assert m.checksum_passes(worst), lines
    lost = [_row(year=2021, floor=0.04, n=9, net=0.1, gross=0.2, cost=0.1)]
    lines, worst = m.checksum_wan432(lost, reference_csv=csv)
    assert not m.checksum_passes(worst)
    assert any("거래 수 불일치" in line for line in lines)


def test_checksum_skips_on_narrowed_coordinates() -> None:
    """좁혀 돈 파일럿을 공개 CSV와 대조하면 **좌표 차이가 배선 오류처럼 보인다**(WAN-381)."""
    assert m.on_adopted_coordinates(
        symbols=m.SYMBOLS, timeframes=m.TIMEFRAMES, holds=m.HOLD_BARS, floors=m.FLOORS
    )
    assert not m.on_adopted_coordinates(
        symbols=("BTC/USDT:USDT",), timeframes=m.TIMEFRAMES, holds=m.HOLD_BARS, floors=m.FLOORS
    )
    assert not m.on_adopted_coordinates(
        symbols=m.SYMBOLS, timeframes=m.TIMEFRAMES, holds=m.HOLD_BARS, floors=(0.01, 0.04)
    )
    lines, worst = m.checksum_wan432([], adopted=False)
    assert m.checksum_passes(worst) and "건너뜀" in lines[0]


# --- 4. Δ와 판정 ----------------------------------------------------------------------


def _pair(
    *, year: int, hi: float, lo: float, se: float = 0.01, threshold: float | None = 25.0
) -> list[m.YearRow]:
    """한 해의 (채택 하한, 느슨한 하한) 두 행 — 하한 4%는 1%의 **부분집합**이라 거래가 적다."""
    return [
        _row(
            year=year,
            floor=m.ADOPTED_FLOOR,
            n=200,
            net=hi,
            gross=hi,
            cost=0.02,
            se_gross=se,
            threshold=threshold,
        ),
        _row(
            year=year,
            floor=m.LOOSE_FLOOR,
            n=900,
            net=lo,
            gross=lo,
            cost=0.05,
            se_gross=se,
            threshold=threshold,
        ),
    ]


def test_year_deltas_need_both_floors() -> None:
    """한쪽 하한에 그 해 거래가 없으면 Δ를 내지 않는다 — 0으로 채우면 없는 거래가 Δを 만든다."""
    rows = [_row(year=2021, floor=m.ADOPTED_FLOOR, n=5, net=0.1, gross=0.2, cost=0.1)]
    assert m.year_deltas(rows) == []


def test_year_delta_splits_market_and_cost() -> None:
    """Δ비용은 **감소량**(양수면 비용이 줄었다)이고 Δgross가 **시장 몫**이다(WAN-370 자)."""
    rows = _pair(year=2021, hi=+0.15, lo=+0.05)
    (d,) = m.year_deltas(rows)
    assert d.delta_net == pytest.approx(0.10)
    assert d.delta_gross == pytest.approx(0.10)
    assert d.delta_cost == pytest.approx(0.03)  # 0.05 → 0.02


def test_sign_gate_is_two_sigma_composite() -> None:
    """부호 관문은 `|Δ| > 2σ`이고 σ는 두 하한의 합성이다(WAN-381/412와 같은 자)."""
    loud = m.year_deltas(_pair(year=2021, hi=+0.20, lo=0.0, se=0.01))[0]
    quiet = m.year_deltas(_pair(year=2021, hi=+0.20, lo=0.0, se=0.30))[0]
    assert loud.gross_is_decided and not quiet.gross_is_decided
    assert loud.se_gross == pytest.approx(math.sqrt(0.01**2 + 0.01**2))


def test_market_year_needs_a_majority_of_combos() -> None:
    """한 조합의 부호가 아니라 **15조합의 과반**이 한 해를 「시장 몫 있는 해」로 만든다."""
    rows: list[m.YearRow] = []
    for i, thr in enumerate((15.0, 20.0, 25.0)):
        # 세 조합 중 둘만 결정된 양수 → 과반(2 > 1.5)이라 시장 몫 있는 해.
        se = 0.01 if i < 2 else 1.0
        rows += _pair(year=2021, hi=+0.20, lo=0.0, se=se, threshold=thr)
    (c,) = m.census(m.year_deltas(rows))
    assert (c.combos, c.gross_up, c.undecided) == (3, 2, 1)
    assert c.is_market_year and not c.is_undecided_year


def test_undecided_year_is_not_counted_as_absent() -> None:
    """🚨 2σ가 차이를 덮는 해는 **미결정**이고 「없다」와 다른 갈래다(완료기준 4)."""
    rows: list[m.YearRow] = []
    for thr in (15.0, 20.0, 25.0):
        rows += _pair(year=2021, hi=+0.20, lo=0.0, se=1.0, threshold=thr)
    (c,) = m.census(m.year_deltas(rows))
    assert not c.is_market_year and c.is_undecided_year
    assert "미결정 1개(2021)" in m.verdict([c])


def _census(year: int, *, up: int, combos: int = 15) -> m.YearCensus:
    return m.YearCensus(
        year=year,
        combos=combos,
        gross_up=up,
        gross_down=0,
        undecided=combos - up,
        median_delta_gross=+0.1 if up else 0.0,
        median_delta_net=+0.1 if up else 0.0,
        median_delta_cost=+0.03,
        trades_high=200,
    )


def test_verdict_multi_year_branch() -> None:
    """앞구간 연도 중 시장 몫 있는 해가 `MULTI_YEAR_GATE` 이상이면 「여러 해」다."""
    rows = [_census(2020, up=0), _census(2021, up=14), _census(2022, up=14), _census(2025, up=14)]
    out = m.verdict(rows)
    assert "여러 해에 걸쳐 보인다" in out
    assert "시장 몫 있는 해 2개(2021, 2022)" in out


def test_verdict_recent_only_branch() -> None:
    """앞구간에 한 해뿐이면 선을 못 넘는다 — 뒷구간이 아무리 커도 마찬가지다."""
    rows = [_census(2021, up=14), _census(2025, up=15), _census(2026, up=15)]
    out = m.verdict(rows)
    assert "최근 2년에만 있다" in out
    assert "1개" in out


def test_verdict_counts_years_decided_the_other_way() -> None:
    """시장 몫이 **반대**로 결정된 해는 미결정과 따로 센다(방향을 감추지 않는다)."""
    down = m.YearCensus(
        year=2021,
        combos=15,
        gross_up=0,
        gross_down=14,
        undecided=1,
        median_delta_gross=-0.1,
        median_delta_net=-0.1,
        median_delta_cost=+0.03,
        trades_high=200,
    )
    out = m.verdict([down])
    assert "반대**로 결정된 해 1개(2021)" in out


def test_verdict_without_is_years_refuses_to_judge() -> None:
    """앞구간 버킷이 없으면 판정하지 않는다(지어내지 않는다)."""
    assert "판정 불가" in m.verdict([_census(2025, up=15)])
    assert "판정 불가" in m.verdict([])


# --- 4b. 관문의 분해능·상쇄·장세 — 문장을 코드가 낸다 ------------------------------


def test_power_reading_says_when_the_gate_passes_nothing() -> None:
    """🚨 관문이 **뒷구간까지** 통과시키지 못하면 그 사실이 판정 옆에 서야 한다.

    이게 없으면 「최근 2년에만 있다」가 *앞구간이 아니라고 말했다*로 읽힌다 — 이 저장소가
    반복해 잡아 온 「라벨과 동작이 어긋남」의 산문 축이다.
    """
    none_pass = [_census(y, up=2) for y in (2021, 2022, 2025)]
    out = m.power_reading(none_pass)
    assert "어느 해도 통과시키지 않는다" in out and "0/3개" in out
    assert "과반선은 8" in out  # 15조합 기준
    assert "결정되지 않았다" in out
    some_pass = [_census(2021, up=14), _census(2025, up=14)]
    out2 = m.power_reading(some_pass)
    assert "통과한 해는 2/2개" in out2 and "어느 해도" not in out2
    assert m.power_reading([]).startswith("⚠️")


def test_half_split_groups_are_derived_from_is_years() -> None:
    """묶음(앞구간 · 걸치는 해 · 뒷구간)은 `IS_YEARS`에서 파생한다 — 리터럴을 두 번 적지 않는다."""
    groups = dict(m.half_groups([2020, 2021, 2022, 2023, 2024, 2025, 2026]))
    assert groups["앞구간 연도"] == m.IS_YEARS
    assert groups["걸치는 해"] == (max(m.IS_YEARS) + 1,)
    assert groups["뒷구간 연도"] == (2025, 2026)


def test_half_split_is_trade_weighted_within_a_group() -> None:
    """묶음 안에서도 **거래 가중**이다 — 얇은 해가 묶음 평균을 끌면 안 된다(WAN-406)."""
    rows = [
        _row(year=2020, floor=m.ADOPTED_FLOOR, n=1, net=+5.0, gross=+5.0, cost=0.02),
        _row(year=2021, floor=m.ADOPTED_FLOOR, n=99, net=+0.0, gross=+0.0, cost=0.02),
        _row(year=2020, floor=m.LOOSE_FLOOR, n=1, net=0.0, gross=0.0, cost=0.05),
        _row(year=2021, floor=m.LOOSE_FLOOR, n=99, net=0.0, gross=0.0, cost=0.05),
    ]
    (front,) = m.half_split(rows)
    assert front.label == "앞구간 연도" and front.combos == 1
    assert front.delta_gross == pytest.approx((5.0 * 1 + 0.0 * 99) / 100)


def test_cancellation_reading_names_the_one_negative_year() -> None:
    """앞구간 ≈0이 상쇄일 때 **어느 해가 그것을 만들었는지** 문장이 짚는다."""
    rows = [_census(y, up=0) for y in (2020, 2022, 2023)]
    rows = [dataclasses.replace(r, median_delta_gross=+0.08) for r in rows] + [
        dataclasses.replace(_census(2021, up=0), median_delta_gross=-0.22)
    ]
    out = m.cancellation_reading(rows, [])
    assert "상쇄" in out and "2021년 하나가 -0.220R" in out
    assert "전부 미결정" in out


def test_cancellation_reading_stays_quiet_when_the_years_are_split() -> None:
    """양수가 음수보다 많지 않으면 상쇄라고 부르지 않는다(과장 금지)."""
    rows = [
        dataclasses.replace(_census(2020, up=0), median_delta_gross=+0.05),
        dataclasses.replace(_census(2021, up=0), median_delta_gross=-0.05),
    ]
    out = m.cancellation_reading(rows, [])
    assert "상쇄" not in out and "양수 1개 · 음수 1개" in out


def test_character_reading_is_labelled_a_hypothesis() -> None:
    """🚨 보조 열 문장은 **판정이 아니다**라고 자기가 말해야 한다(이슈가 그렇게 정했다)."""
    census_rows = [
        dataclasses.replace(_census(2022, up=0), median_delta_gross=+0.08),
        dataclasses.replace(_census(2025, up=6), median_delta_gross=+0.19),
        dataclasses.replace(_census(2021, up=0), median_delta_gross=-0.22),
        dataclasses.replace(_census(2023, up=0), median_delta_gross=+0.06),
    ]
    btc = {2022: -0.64, 2025: -0.06, 2021: +0.60, 2023: +1.56}
    out = m.character_reading(census_rows, btc)
    assert "판정 아님" in out and "가설이지 판정이 아니다" in out
    assert "내린 해 2개 중 2개" in out and "오른 해 2개 중 1개" in out
    # 한쪽 장세만 있으면 아무 말도 하지 않는다(대조가 없으니).
    assert m.character_reading(census_rows, {2021: +0.6, 2023: +1.5}) == ""


# --- 5. 구간 경계는 주장하지 않고 실측한다 ---------------------------------------------


def test_boundary_census_flags_a_straddling_is_year() -> None:
    """🚨 앞구간 연도가 구간을 걸치면 판정이 흔들리므로 요약이 그 사실을 찍는다."""
    rows = [
        _row(year=2021, floor=0.04, n=10, net=0.1, gross=0.2, cost=0.1, segment=harness.SEGMENT_IS),
        _row(
            year=2023,
            floor=0.04,
            n=5,
            net=0.1,
            gross=0.2,
            cost=0.1,
            segment=harness.SEGMENT_OOS_WARM,
        ),
        _row(year=2023, floor=0.04, n=7, net=0.1, gross=0.2, cost=0.1, segment=harness.SEGMENT_IS),
    ]
    bounds = m.boundary_census(rows)
    labels = {b.year: b.label for b in bounds}
    assert labels == {2021: "앞구간", 2023: "걸침"}
    assert "2023가 구간을 걸친다" in m.boundary_reading(bounds)


def test_boundary_reading_confirms_when_no_is_year_straddles() -> None:
    rows = [
        _row(year=y, floor=0.04, n=3, net=0.1, gross=0.2, cost=0.1, segment=harness.SEGMENT_IS)
        for y in m.IS_YEARS
    ] + [
        _row(
            year=2025,
            floor=0.04,
            n=3,
            net=0.1,
            gross=0.2,
            cost=0.1,
            segment=harness.SEGMENT_OOS_WARM,
        )
    ]
    assert "전부 `is`에만" in m.boundary_reading(m.boundary_census(rows))


# --- 6. 표 --------------------------------------------------------------------------


def test_single_trade_bucket_prints_no_fake_sigma() -> None:
    """표본 1건이면 `±—`다 — `nan`을 숫자처럼 찍지 않는다(지어내지 않는다)."""
    rows = [
        _row(year=2021, floor=m.LOOSE_FLOOR, n=1, net=+1.5, gross=+1.6, cost=0.1, se_net=math.nan),
        _row(
            year=2021, floor=m.ADOPTED_FLOOR, n=1, net=+1.5, gross=+1.6, cost=0.1, se_net=math.nan
        ),
    ]
    out = m.render_summary(rows, adopted=False, btc={2021: 0.6})
    assert "±—" in out and "±nan" not in out
    assert "59.63%" not in out  # 보조 열은 소수 둘째 자리 퍼센트다.
    assert "60.00%" in out


def test_summary_states_the_nested_sample_caveat() -> None:
    """🚨 두 표본이 포개져 있다는 한계가 **표 안에** 있어야 한다(σ를 크기로 인용하지 않게)."""
    rows = _pair(year=2021, hi=+0.15, lo=+0.05)
    out = m.render_summary(rows, adopted=False)
    assert "부분집합" in out and "방향 판정에만" in out
    assert "채택 좌표가 아니다" in out
    assert "엣지 없음" in out


def test_frame_roundtrip_keeps_every_field() -> None:
    rows = _pair(year=2021, hi=+0.15, lo=+0.05)
    back = m.frame_to_rows(m.rows_to_frame(rows))
    assert back == rows


# --- 7. 실데이터 게이트 ---------------------------------------------------------------


@pytest.mark.skipif(not m._db_exists(), reason="저장 DB가 없다(CI).")
def test_btc_annual_returns_are_window_clipped() -> None:
    """보조 열 — 2020·2026은 **부분 연도**이고 창 밖 해는 나오지 않는다."""
    out = m.btc_annual_returns()
    if not out:
        pytest.skip("BTC 1d가 저장돼 있지 않다.")
    assert min(out) >= 2020 and max(out) <= 2026
    assert all(-1.0 < v < 20.0 for v in out.values())


@pytest.mark.skipif(not m._db_exists(), reason="저장 DB가 없다(CI).")
def test_real_cell_bucket_sum_equals_the_wan424_row() -> None:
    """🚨 완료기준 3의 **실데이터** 판 — 한 칸에서 연도 버킷을 다시 합치면 `wan424.place`와 같다.

    보고의 검산은 공개 CSV 대조인데, 그것만 두면 「버킷이 거래를 잃었다」를 **표를 다 돌린 뒤**
    (수 시간)에야 안다. 이 테스트는 같은 등식을 한 칸에서 20초 안에 건다 — 그리고 라벨이 아니라
    **동작**으로 건다(배치 함수를 두 경로가 공유하는지).
    """
    probe = harness.load_market_data(
        harness.normalize_symbol("BTCUSDT"), "1d", start_ms=0, end_ms=None, need_1m=False
    )
    if probe.empty:
        pytest.skip("실데이터(data/ohlcv.db) 없음 — 통합 실행은 로컬에서만(CI 빈 DB).")
    symbols = (harness.normalize_symbol("BTCUSDT"),)
    payloads = w424.build_base_payloads(
        jobs=1, payload_dir=m.DEFAULT_PAYLOAD_DIR, symbols=symbols, timeframes=("1d",)
    )
    cells = w424.build_arm_cells(payloads, jobs=1, min_width=m.ARM_FLOOR)
    hold, threshold = m.display_combo()
    filtered = w424.filtered_payloads(
        payloads, cells, hold=hold, floor=m.ADOPTED_FLOOR, threshold=threshold
    )
    want = {
        r.segment: r
        for r in w424.place(filtered, hold=hold, floor=m.ADOPTED_FLOOR, threshold=threshold)
    }
    got = m.bucket_by_year(filtered, hold=hold, floor=m.ADOPTED_FLOOR, threshold=threshold)
    assert got, "실데이터 칸에서 버킷이 하나도 안 나왔다 — 이 테스트가 아무것도 안 걸고 있다."
    pooled = m.pool_years(got)
    for (_hold, _floor, _thr, segment), acc in pooled.items():
        ref = want[segment]
        assert int(acc["num_trades"]) == ref.num_trades
        assert acc["net"] / acc["num_trades"] == pytest.approx(ref.mean_net_r, abs=1e-12)


# --- 8. 위임한 배치가 채택 회계를 쓰는지 값으로 확인한다 (WAN-370/373 축) ---------------


def test_adopted_accounting_assertion_fires_on_the_legacy_value() -> None:
    """🚨 이 모듈에는 북 인자가 없다 — 위임이 축을 잃으면 **시끄럽게 죽어야** 한다.

    가드가 라벨이 아니라 **값**을 보는지 돌연변이로 확인한다: 옛 회계(테이커 익절)를 넣으면
    반드시 실패해야 하고, 채택 회계(메이커)면 통과해야 한다.
    """
    from backtest.models import BacktestConfig

    ok = BacktestConfig(take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY)
    m.assert_adopted_accounting(ok)  # 예외가 없어야 한다.
    legacy = BacktestConfig(take_profit_liquidity=harness.LEGACY_TAKE_PROFIT_LIQUIDITY)
    with pytest.raises(AssertionError, match="채택 익절 회계"):
        m.assert_adopted_accounting(legacy)


def test_delegated_placement_really_carries_the_adopted_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위임 대상(`wan424.place_segments`)이 **지금** 그 값을 넘기는지 소스가 아니라 호출로 건다."""
    captured: dict[str, object] = {}

    def spy(*_args: object, **kwargs: object) -> Iterator[object]:
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(w424, "iter_book_segments", spy)
    w424.place_segments([])
    assert captured["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
