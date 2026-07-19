"""WAN-137 Phase 1 저항 거리 측정 테스트.

완료 기준(이슈 §회귀 테스트)은 **라벨이 아니라 동작으로** 두 규칙을 고정하는 것이다:

* **①(뚫린 저항 제외)** — 진입 시각 이전에 무효화(breaker)된 약세 존은 저항으로 세지
  않는다. `alive_at`만 믿으면 놓치는 함정이라, 클리핑→`breaker` 필터를 거친 뒤 실제로
  더 가까운 뚫린 존을 **고르지 않는지**를 산출물로 확인한다.
* **②(확정 시점 준수 · 룩어헤드)** — 진입 시각 이후 확정된 미래 약세 존은 질의 뷰에
  나타나지 않는다(`indexed_zone_provider` 클리핑). 미래 저항을 보고 파는 리포트를 막는다.
"""

from __future__ import annotations

import pandas as pd

from backtest.multi_tf_overlap import indexed_zone_provider
from backtest.wan137_resistance_distance import (
    COMBINED_TF,
    DistRow,
    SetupResistance,
    aggregate,
    nearest_resistance,
    source_breakdown,
)
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockResult

BEAR = OrderBlockDirection.BEARISH
BULL = OrderBlockDirection.BULLISH


def _bear(
    bottom: float,
    top: float,
    *,
    confirmed_time: int = 0,
    break_time: int | None = None,
    swept_time: int | None = None,
    breaker: bool = False,
) -> OrderBlock:
    return OrderBlock(
        direction=BEAR,
        top=top,
        bottom=bottom,
        start_time=confirmed_time,
        confirmed_time=confirmed_time,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=breaker,
        break_time=break_time,
        swept_time=swept_time,
    )


# --------------------------------------------------------------------------- #
# 순수 함수 — 근단·거리·필터
# --------------------------------------------------------------------------- #


def test_nearest_resistance_picks_lowest_bottom_above_entry() -> None:
    zones = [_bear(104.0, 106.0), _bear(101.0, 103.0), _bear(108.0, 110.0)]
    hit = nearest_resistance(zones, entry_price=100.0, risk=2.0, timeframe="15m")
    assert hit is not None
    assert hit.target == 101.0  # 근단(아랫변, §③), 가장 낮은 것을 먼저 닿는다.
    assert hit.distance_r == 0.5  # (101 − 100) / 2.


def test_nearest_resistance_ignores_zones_at_or_below_entry() -> None:
    # bottom ≤ 진입가인 존은 저항이 아니다(가격이 위에서 안 만난다).
    zones = [_bear(99.0, 101.0), _bear(100.0, 102.0)]
    assert nearest_resistance(zones, entry_price=100.0, risk=2.0, timeframe="x") is None


def test_nearest_resistance_none_when_risk_nonpositive() -> None:
    zones = [_bear(110.0, 112.0)]
    assert nearest_resistance(zones, entry_price=100.0, risk=0.0, timeframe="x") is None
    assert nearest_resistance(zones, entry_price=100.0, risk=-1.0, timeframe="x") is None


# --------------------------------------------------------------------------- #
# ① 뚫린 저항 제외 — 동작으로 고정 (이슈의 최대 함정)
# --------------------------------------------------------------------------- #


def test_broken_resistance_is_excluded_even_when_nearer() -> None:
    """더 가까운 저항이 breaker(무효화)면 고르지 않고 다음 유효 저항을 고른다."""
    broken = _bear(101.0, 103.0, breaker=True)  # 더 가깝지만 이미 뚫린 = 뒤집힌 지지.
    valid = _bear(105.0, 107.0)
    hit = nearest_resistance([broken, valid], entry_price=100.0, risk=2.0, timeframe="15m")
    assert hit is not None
    assert hit.target == 105.0  # 뚫린 101을 건너뛰고 105를 고른다.


def test_broken_resistance_excluded_through_provider_clipping() -> None:
    """엔드투엔드 ①: `break_time <= 진입시각`인 존은 provider 클리핑에서 breaker=True가
    되고, `nearest_resistance`가 그것을 뺀다 — 산출물(선택된 target)로 확인한다."""
    entry_time = 1_000
    # 근단 101, 진입 전(900)에 무효화 → 저항 아님. 유효 저항은 105.
    # (실 아카이브의 뚫린 존은 breaker=True로 저장된다 — 클리핑이 시각에 맞춰 되돌린다.)
    broken = _bear(101.0, 103.0, confirmed_time=100, break_time=900, breaker=True)
    valid = _bear(105.0, 107.0, confirmed_time=100)
    result = OrderBlockResult(order_blocks=[broken, valid], signals=[], retap_signals=[])
    provider = indexed_zone_provider({"15m": result}, combine=False)
    zones = provider("15m", entry_time, BEAR)
    # 클리핑된 뷰에서 뚫린 존은 breaker=True로 표시된다(동작 검증의 핵심).
    assert any(z.breaker and z.bottom == 101.0 for z in zones)
    hit = nearest_resistance(zones, entry_price=100.0, risk=2.0, timeframe="15m")
    assert hit is not None and hit.target == 105.0


def test_resistance_broken_after_entry_still_counts() -> None:
    """진입 이후에야 뚫리는 존은 진입 시점엔 유효 저항이다(클리핑이 미래 무효화를 숨긴다)."""
    entry_time = 1_000
    later_broken = _bear(101.0, 103.0, confirmed_time=100, break_time=2_000, breaker=True)
    result = OrderBlockResult(order_blocks=[later_broken], signals=[], retap_signals=[])
    provider = indexed_zone_provider({"15m": result}, combine=False)
    zones = provider("15m", entry_time, BEAR)
    hit = nearest_resistance(zones, entry_price=100.0, risk=2.0, timeframe="15m")
    assert hit is not None and hit.target == 101.0  # 진입 시점엔 아직 저항.


# --------------------------------------------------------------------------- #
# ② 확정 시점 준수 (룩어헤드) — 동작으로 고정
# --------------------------------------------------------------------------- #


def test_future_confirmed_resistance_not_visible_at_entry() -> None:
    entry_time = 1_000
    past = _bear(101.0, 103.0, confirmed_time=entry_time - 100)
    future = _bear(101.0, 103.0, confirmed_time=entry_time + 100)  # 진입 이후 확정.
    result = OrderBlockResult(order_blocks=[past, future], signals=[], retap_signals=[])
    provider = indexed_zone_provider({"15m": result}, combine=False)
    at_entry = provider("15m", entry_time, BEAR)
    assert len(at_entry) == 1  # 미래 존은 안 보인다.
    hit = nearest_resistance(at_entry, entry_price=100.0, risk=2.0, timeframe="15m")
    assert hit is not None and hit.target == 101.0
    # 나중 시각엔 둘 다 보인다(클리핑이 시각에 반응함).
    assert len(provider("15m", entry_time + 200, BEAR)) == 2


# --------------------------------------------------------------------------- #
# 집계 — 비율·저항 없음·combined 출처
# --------------------------------------------------------------------------- #


def _setup(
    by_tf: dict[str, float], mfe_r: float, combined: tuple[float, str] | None
) -> SetupResistance:
    return SetupResistance(
        symbol="BTC/USDT:USDT",
        entry_tf="15m",
        segment="oos",
        mfe_r=mfe_r,
        by_tf=by_tf,
        combined_r=None if combined is None else combined[0],
        combined_source=None if combined is None else combined[1],
    )


def test_aggregate_fractions_and_no_resistance() -> None:
    setups = [
        _setup({"15m": 0.3, "1m": 0.1}, mfe_r=1.0, combined=(0.1, "1m")),  # 15m 0.3R, 1m<0.5R
        _setup({"15m": 2.0}, mfe_r=1.0, combined=(2.0, "15m")),  # 15m MFE 너머
        _setup({}, mfe_r=1.0, combined=None),  # 저항 없음
    ]
    rows = {(r.resistance_tf): r for r in aggregate(setups, combine=False)}
    r15 = rows["15m"]
    assert r15.n_setups == 3
    assert r15.n_with_resistance == 2
    assert abs(r15.frac_no_resistance - 1 / 3) < 1e-9
    assert r15.frac_below_near == 0.5  # 0.3<0.5R, 2.0>=0.5R → 1/2.
    assert r15.frac_within_mfe == 0.5  # 0.3<=1 도달, 2.0>1 미도달.
    r1m = rows["1m"]
    assert r1m.n_with_resistance == 1
    assert r1m.frac_below_near == 1.0  # 0.1 < 0.5R.
    # combined도 셀로 남는다.
    assert rows[COMBINED_TF].n_with_resistance == 2


def test_aggregate_keeps_empty_resistance_cells() -> None:
    """저항이 한 번도 안 잡힌 TF도 표에 frac_no_resistance=1.0으로 남는다."""
    setups = [_setup({"15m": 1.0}, mfe_r=2.0, combined=(1.0, "15m"))]
    rows = {r.resistance_tf: r for r in aggregate(setups, combine=False)}
    assert set(rows) >= {"15m", "5m", "1m", COMBINED_TF}
    assert rows["1m"].n_with_resistance == 0
    assert rows["1m"].frac_no_resistance == 1.0
    assert rows["1m"].dist_median is None


def test_source_breakdown_counts_lowest_tf_bias() -> None:
    setups = [
        _setup({"1m": 0.2}, mfe_r=1.0, combined=(0.2, "1m")),
        _setup({"1m": 0.3}, mfe_r=1.0, combined=(0.3, "1m")),
        _setup({"15m": 1.0}, mfe_r=1.0, combined=(1.0, "15m")),
    ]
    frame = source_breakdown(setups)
    row = frame[(frame["entry_tf"] == "15m") & (frame["segment"] == "oos")].iloc[0]
    assert row["n"] == 3
    assert abs(row["from_1m"] - 2 / 3) < 1e-9
    assert abs(row["from_15m"] - 1 / 3) < 1e-9


def test_distrow_roundtrips_through_frame() -> None:
    setups = [_setup({"15m": 1.0}, mfe_r=2.0, combined=(1.0, "15m"))]
    rows = aggregate(setups, combine=False)
    frame = pd.DataFrame([r.model_dump() for r in rows])
    back = [DistRow.model_validate(rec) for rec in frame.to_dict(orient="records")]
    assert len(back) == len(rows)
