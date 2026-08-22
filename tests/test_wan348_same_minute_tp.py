"""「같은 분 익절」 틱 검증 테스트 (WAN-348).

네트워크·DB를 타지 않는다. 여기서 고정하는 것은 라벨이 아니라 **동작**이다:

* **순서와 결과를 가르는 판정** — 「익절가에 먼저 닿았지만 체결 뒤 다시 닿았다」는 가정만
  틀렸고 손익은 그대로 나므로 `인공물`이 아니다. 둘을 합치면 할인율이 과대평가된다.
* **재구성은 엔진 값을 재현해야만 성공** — 엉뚱한 존으로 틱을 굴려 놓고 표에 숫자가 찍히는
  것이 이 저장소가 가장 경계하는 실패다(WAN-91/95/112/123/159).
* **되살린 익절가는 지어낸 값이 아니다** — CSV에 값이 있는 행이 그 되살림을 검산한다.
* **층 배분이 헤드라인을 흔들지 않는다** — 바닥 배분 때문에 4h가 모집단 비중보다 많이
  뽑히므로 전체 p는 층 가중으로 낸다.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from backtest.wan348_same_minute_tp import (
    ARM_STATIC,
    TARGET_CSV,
    VERDICT_ARTIFACT,
    VERDICT_NO_FILL,
    VERDICT_NO_TICKS,
    VERDICT_NOT_SAME_MINUTE,
    VERDICT_ORDER_FLIPPED_STILL,
    VERDICT_REAL,
    CellBars,
    Reconstruction,
    Target,
    allocate,
    derive_take_profit,
    draw_sample,
    load_targets,
    measure_band,
    measure_static,
    ohlc_matches,
    reconstruct,
    take_profit_checksum,
    weighted_ratio,
    wilson_interval,
)
from data.agg_trade_archive import Tick
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection
from strategy.realtime_band import RealtimeBand

_MINUTE = 1_724_424_480_000
_POPULATION = {"15m": 336, "1h": 70, "2h": 40, "4h": 21}


def _target(**overrides: object) -> Target:
    base = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "15m",
        "entry_ms": _MINUTE,
        "entry_price": 100.0,
        "stop_price": 98.0,
        "take_profit_price": 103.0,
        "is_reentry": False,
        "net_r": 1.4,
        "pnl": 10.0,
    }
    base.update(overrides)
    return Target(**base)  # type: ignore[arg-type]


def _ticks(*prices: float) -> list[Tick]:
    return [Tick(time_ms=_MINUTE + i * 100, price=p, qty=1.0) for i, p in enumerate(prices)]


# --------------------------------------------------------------------------- #
# §2 표본 설계
# --------------------------------------------------------------------------- #


def test_allocate_gives_every_stratum_a_floor_and_sums_to_total() -> None:
    quota = allocate(_POPULATION, 100, 10)
    assert sum(quota.values()) == 100
    assert all(count >= 10 for count in quota.values())
    assert quota["15m"] > quota["1h"] > quota["2h"] >= quota["4h"], "무게 순서가 뒤집혔다"


def test_allocate_never_asks_for_more_than_a_stratum_holds() -> None:
    quota = allocate({"15m": 5, "4h": 3}, 100, 10)
    assert quota == {"15m": 5, "4h": 3}, "모집단보다 많이 뽑을 수는 없다"


def test_allocate_rejects_a_non_positive_size() -> None:
    with pytest.raises(ValueError):
        allocate(_POPULATION, 0, 10)


def test_draw_sample_is_reproducible_and_ignores_input_order() -> None:
    targets = [
        _target(timeframe=tf, entry_ms=_MINUTE + i * 60_000, entry_price=100.0 + i)
        for tf in ("15m", "1h")
        for i in range(20)
    ]
    first = draw_sample(targets, size=10, floor=3, seed=7)
    shuffled = list(reversed(targets))
    second = draw_sample(shuffled, size=10, floor=3, seed=7)
    assert [t.entry_ms for t in first] == [t.entry_ms for t in second]
    other = draw_sample(targets, size=10, floor=3, seed=8)
    assert [t.entry_ms for t in first] != [t.entry_ms for t in other], "시드가 무시됐다"


# --------------------------------------------------------------------------- #
# §3 판정 — 순서와 결과는 다른 질문이다
# --------------------------------------------------------------------------- #


def test_fill_then_take_profit_is_real() -> None:
    got = measure_static(_ticks(101.0, 99.5, 103.5), _target())
    assert got.verdict == VERDICT_REAL
    assert got.order_ok and got.outcome_ok


def test_take_profit_first_and_never_again_is_an_artifact() -> None:
    got = measure_static(_ticks(103.5, 99.5, 100.5), _target())
    assert got.verdict == VERDICT_ARTIFACT
    assert not got.order_ok and not got.outcome_ok


def test_take_profit_first_but_reached_again_after_the_fill_still_pays() -> None:
    """가정만 틀리고 손익은 그대로 난다 — `인공물`과 합치면 할인율이 과대평가된다."""
    got = measure_static(_ticks(103.5, 99.5, 104.0), _target())
    assert got.verdict == VERDICT_ORDER_FLIPPED_STILL
    assert not got.order_ok
    assert got.outcome_ok


def test_filled_but_target_never_reached_is_not_a_same_minute_take_profit() -> None:
    got = measure_static(_ticks(101.0, 99.5, 100.2), _target())
    assert got.verdict == VERDICT_NOT_SAME_MINUTE
    assert not got.outcome_ok


def test_never_touching_the_limit_is_flagged_not_silently_counted() -> None:
    got = measure_static(_ticks(101.0, 100.5), _target())
    assert got.verdict == VERDICT_NO_FILL
    assert not got.decidable, "판정 불가가 분모에 들어가면 비율이 희석된다"


def test_a_minute_with_no_trades_is_flagged() -> None:
    got = measure_static([], _target())
    assert got.verdict == VERDICT_NO_TICKS
    assert not got.decidable


# --------------------------------------------------------------------------- #
# 재구성 — 엔진 값을 재현해야만 성공한다
# --------------------------------------------------------------------------- #


def _cell(zone_top: float, zone_bottom: float, closes: list[float]) -> CellBars:
    block = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=zone_top,
        bottom=zone_bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    times = [i * 900_000 for i in range(len(closes))]
    return CellBars(times=times, closes=closes, order_blocks=[block])


def _expected_limit(cell: CellBars, close: float, params: ConfluenceParams) -> float | None:
    assert params.deviation_filter is not None
    band = RealtimeBand.seed_from_closed(cell.closes, params.deviation_filter, end=len(cell.closes))
    return Reconstruction(
        order_block=cell.order_blocks[0],
        band=band,
        params=params,
        minute_close=close,
        reconstructed_entry=float("nan"),
    ).limit_at(close)


def test_reconstruct_accepts_only_the_zone_that_reproduces_the_engine_price() -> None:
    params = ConfluenceParams()
    closes = [100.0 + (i % 3) for i in range(40)]
    cell = _cell(zone_top=99.0, zone_bottom=95.0, closes=closes)
    minute_close = 99.5
    expected = _expected_limit(cell, minute_close, params)
    assert expected is not None, "픽스처가 주문 없는 구간을 골랐다"

    target = _target(
        timeframe="15m",
        entry_ms=len(closes) * 900_000,
        entry_price=expected,
        stop_price=95.0,
    )
    recon = reconstruct(target, cell, minute_close, params=params)
    assert recon is not None
    assert math.isclose(recon.reconstructed_entry, expected, rel_tol=1e-12)

    wrong = _target(
        timeframe="15m",
        entry_ms=len(closes) * 900_000,
        entry_price=expected * 1.01,
        stop_price=95.0,
    )
    assert reconstruct(wrong, cell, minute_close, params=params) is None


def test_reconstruct_requires_the_stop_to_be_the_zone_invalidation_edge() -> None:
    params = ConfluenceParams()
    closes = [100.0 + (i % 3) for i in range(40)]
    cell = _cell(zone_top=99.0, zone_bottom=95.0, closes=closes)
    expected = _expected_limit(cell, 99.5, params)
    assert expected is not None
    target = _target(entry_price=expected, stop_price=94.0)  # 존 원단이 아니다
    assert reconstruct(target, cell, 99.5, params=params) is None


def test_band_arm_reprices_the_target_from_its_own_fill() -> None:
    """체결가가 달라지면 1R도 익절 목표도 달라진다 — 엔진 목표를 재사용하면 섞인 자가 된다."""
    params = ConfluenceParams()
    closes = [100.0 + (i % 3) for i in range(40)]
    cell = _cell(zone_top=99.0, zone_bottom=95.0, closes=closes)
    minute_close = 99.5
    expected = _expected_limit(cell, minute_close, params)
    assert expected is not None
    target = _target(entry_price=expected, stop_price=95.0, take_profit_price=1e9)
    recon = reconstruct(target, cell, minute_close, params=params)
    assert recon is not None

    # 봉내 라이브 밴드는 「가격이 내려와 지정가를 만나는」 고정점에서 체결한다 — 그 아래로
    # 내려온 틱이 있어야 체결이고, 체결가는 그 순간 걸려 있던 지정가다.
    touch = 96.0
    fill_price = recon.limit_at(touch)
    assert fill_price is not None and touch <= fill_price
    tp = derive_take_profit(fill_price, 95.0, params.take_profit_r)
    ticks = [
        Tick(time_ms=_MINUTE, price=minute_close, qty=1.0),
        Tick(time_ms=_MINUTE + 100, price=touch, qty=1.0),
        Tick(time_ms=_MINUTE + 200, price=tp + 1.0, qty=1.0),
    ]
    got = measure_band(ticks, target, recon, take_profit_r=params.take_profit_r)
    assert got.verdict == VERDICT_REAL
    assert got.take_profit_price is not None
    assert math.isclose(got.take_profit_price, tp, rel_tol=1e-12)


def test_ohlc_cross_check_catches_the_wrong_file() -> None:
    """두 자료(수집기 1분봉 · 거래소 아카이브)가 어긋나면 엉뚱한 파일을 펼쳤다는 뜻이다."""
    ticks = _ticks(101.0, 99.5, 103.5)
    assert ohlc_matches(ticks, (103.5, 99.5, 100.0)) is True
    assert ohlc_matches(ticks, (110.0, 99.5, 100.0)) is False
    assert ohlc_matches(ticks, (103.5, 90.0, 100.0)) is False


def test_ohlc_cross_check_is_none_when_there_is_nothing_to_compare() -> None:
    assert ohlc_matches([], (1.0, 1.0, 1.0)) is None
    assert ohlc_matches(_ticks(1.0), None) is None


# --------------------------------------------------------------------------- #
# 통계
# --------------------------------------------------------------------------- #


def test_wilson_interval_stays_inside_zero_one_at_the_edges() -> None:
    low, high = wilson_interval(0, 20)
    assert low == 0.0 and 0.0 < high < 1.0
    low, high = wilson_interval(20, 20)
    assert high == 1.0 and 0.0 < low < 1.0


def test_weighted_ratio_uses_population_weights_not_sample_counts() -> None:
    """4h가 표본에서 과대 대표되므로 단순 합과 층 가중은 달라야 한다."""
    per_stratum = {"15m": (0, 50), "4h": (13, 13)}
    weights = {"15m": 336, "4h": 21}
    simple = 13 / 63
    assert weighted_ratio(per_stratum, weights) < simple
    assert math.isclose(weighted_ratio(per_stratum, weights), 21 / 357, rel_tol=1e-12)


def test_weighted_ratio_ignores_strata_with_nothing_decidable() -> None:
    assert math.isclose(
        weighted_ratio({"15m": (5, 10), "4h": (0, 0)}, {"15m": 336, "4h": 21}), 0.5, rel_tol=1e-12
    )


# --------------------------------------------------------------------------- #
# 대상 목록 — 되살린 익절가는 지어낸 값이 아니다
# --------------------------------------------------------------------------- #


def _target_csv(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "trades.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _csv_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "같은분익절": True,
        "칸(종목)": "BTC/USDT:USDT",
        "칸(TF)": "15m",
        "진입시각(UTC)": "2024-08-23 14:48",
        "진입가": 100.0,
        "손절가": 98.0,
        "익절가": 103.0,
        "재진입": False,
        "net R": 1.4,
        "손익": 10.0,
    }
    base.update(overrides)
    return base


def test_load_targets_rebuilds_the_missing_take_profit_of_reentry_rows(tmp_path: Path) -> None:
    path = _target_csv(
        tmp_path,
        [
            _csv_row(),
            _csv_row(재진입=True, 익절가=float("nan"), **{"진입시각(UTC)": "2024-08-23 14:49"}),
            _csv_row(같은분익절=False, **{"진입시각(UTC)": "2024-08-23 14:50"}),
        ],
    )
    targets = load_targets(path, take_profit_r=1.5)
    assert len(targets) == 2, "같은 분 익절이 아닌 행이 섞였다"
    recorded, derived = targets
    assert not recorded.take_profit_derived
    assert derived.take_profit_derived
    assert math.isclose(derived.take_profit_price, 103.0, rel_tol=1e-12)


def test_load_targets_dies_instead_of_returning_an_empty_list(tmp_path: Path) -> None:
    path = _target_csv(tmp_path, [_csv_row(같은분익절=False)])
    with pytest.raises(ValueError):
        load_targets(path)
    missing = tmp_path / "no-columns.csv"
    pd.DataFrame([{"a": 1}]).to_csv(missing, index=False)
    with pytest.raises(ValueError):
        load_targets(missing)


@pytest.mark.skipif(not TARGET_CSV.exists(), reason="WAN-346 거래별 CSV가 없다")
def test_real_target_csv_confirms_the_rebuilt_take_profit() -> None:
    """되살림이 맞는지 **실데이터로** 확인한다 — 값이 적힌 행이 그 규칙을 재현해야 한다."""
    known, matched, max_rel = take_profit_checksum()
    assert known > 0
    assert matched == known, f"고정 R 규칙이 기록된 익절가를 재현하지 못한다(최대 {max_rel:.2e})"


@pytest.mark.skipif(not TARGET_CSV.exists(), reason="WAN-346 거래별 CSV가 없다")
def test_real_target_csv_has_the_population_wan336_reported() -> None:
    targets = load_targets()
    assert len(targets) == 467
    by_tf: dict[str, int] = {}
    for target in targets:
        by_tf[target.timeframe] = by_tf.get(target.timeframe, 0) + 1
    assert by_tf == _POPULATION


@pytest.mark.skipif(not TARGET_CSV.exists(), reason="WAN-346 거래별 CSV가 없다")
def test_real_sample_is_stratified_and_stable() -> None:
    sample = draw_sample(load_targets())
    assert len(sample) == 100
    by_tf: dict[str, int] = {}
    for target in sample:
        by_tf[target.timeframe] = by_tf.get(target.timeframe, 0) + 1
    assert by_tf == allocate(_POPULATION, 100, 10)
    assert [t.entry_ms for t in sample] == [t.entry_ms for t in draw_sample(load_targets())]


def test_measurement_row_arm_labels_are_the_ones_the_summary_reads() -> None:
    got = measure_static(_ticks(101.0, 99.5, 103.5), _target())
    assert ARM_STATIC == "static" and got.decidable
