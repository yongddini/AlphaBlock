"""「같은 분 왕복」의 방향 대조 테스트 (WAN-362).

여기서 고정하는 것은 라벨이 아니라 **동작**이다:

* **기하 판정이 엔진의 수를 재현한다** — 재현하지 못하면 이 표는 「엔진이 아닌 무언가」를
  재고 있는 것이라 한 줄도 인용할 수 없다(WAN-91/95/112/123/159 부류의 조용한 실패).
* **손절 우선 규칙을 되돌려야 익절 수가 맞는다** — 같은 봉에서 둘 다 닿으면 손절이 이기므로
  (`stop_before_tp`) 기하의 `can_tp`에서 그만큼 빼야 기록과 같아진다.
* **`can_stop`은 체결가와 무관하고 `can_tp`는 강하게 의존한다** — 이 비대칭이 §2-C의
  근거이고, 뒤집히면 결론 문장이 통째로 틀린다.
* **같은 분은 같은 1분 칸이다** — 라이브 장부는 틱 시각(ms)이라 분으로 내림해야 백테스트의
  1분봉과 같은 자가 된다.
* **판정은 Wilson 구간이 기준을 포함하는지로 낸다** — 표본이 7건일 때 정규근사는 성립하지 않는다.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from backtest.wan362_same_minute_roundtrip import (
    BACKTEST_REFERENCE,
    TRADES_CSV,
    binomial_tail_p,
    fill_placement_counterfactual,
    geometry_check,
    judge,
    load_trades,
    measure,
    position_table,
    required_sample,
    take_profit_checksum,
)
from live.same_minute_census import (
    census,
    is_same_minute,
    leave_one_day_out,
    minute_bucket,
)
from paper.store import PaperTradeRecord
from strategy.models import OrderBlockDirection, SignalExitReason

MINUTE = 60_000


def _trade(
    *,
    entry: float,
    stop: float,
    tp: float | None,
    low: float,
    high: float,
    reason: str = "익절",
    same_minute: bool = True,
    timeframe: str = "15m",
) -> dict[str, object]:
    return {
        "방향": "롱",
        "칸(종목)": "BTC/USDT:USDT",
        "칸(TF)": timeframe,
        "진입시각(UTC)": "2025-01-01 00:00",
        "청산시각(UTC)": "2025-01-01 00:00" if same_minute else "2025-01-01 01:00",
        "진입가": entry,
        "손절가": stop,
        "익절가": tp,
        "청산사유": reason,
        "같은분익절": str(reason == "익절" and same_minute),
        "재진입": "False",
        "bar_open": (low + high) / 2,
        "bar_high": high,
        "bar_low": low,
        "bar_close": (low + high) / 2,
    }


def _measured(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["entry_ms"] = 1_735_689_600_000
    frame["R"] = frame["진입가"] - frame["손절가"]
    frame["tp_derived"] = frame["익절가"].isna()
    frame["tp"] = frame["익절가"].fillna(frame["진입가"] + 1.5 * frame["R"])
    frame["same_minute"] = frame["진입시각(UTC)"] == frame["청산시각(UTC)"]
    return measure(frame)


# --------------------------------------------------------------------------- #
# 기하 판정 — 엔진의 수를 재현하는가
# --------------------------------------------------------------------------- #


def test_geometry_reproduces_recorded_counts() -> None:
    """익절 하나 · 손절 하나 · 아무것도 아닌 것 하나를 기하가 그대로 센다."""
    measured = _measured(
        [
            # 고가가 익절가를 넘고 저가는 손절가 위 → 같은 분 익절.
            _trade(entry=100.0, stop=99.0, tp=101.5, low=99.6, high=102.0),
            # 저가가 손절가에 닿음 → 같은 분 손절.
            _trade(entry=100.0, stop=99.0, tp=101.5, low=99.0, high=100.4, reason="손절"),
            # 봉이 어느 쪽에도 못 닿음 → 같은 분 왕복 아님.
            _trade(entry=100.0, stop=99.0, tp=101.5, low=99.6, high=100.2, same_minute=False),
        ]
    )
    check = geometry_check(measured)
    assert check.can_stop == 1
    assert check.recorded_same_sl == 1
    assert check.stop_matches
    assert check.can_tp - check.stop_wins == check.recorded_same_tp == 1
    assert check.tp_matches
    assert check.below_zone_bottom == 0
    assert check.exact_touch == 1


def test_stop_priority_is_undone_before_comparing_take_profits() -> None:
    """같은 봉에서 둘 다 닿으면 손절이 이긴다 — 그만큼 빼지 않으면 익절 수가 하나 많다."""
    measured = _measured(
        [_trade(entry=100.0, stop=99.0, tp=101.5, low=99.0, high=103.0, reason="손절")]
    )
    check = geometry_check(measured)
    assert check.can_tp == 1  # 기하로는 익절도 닿는다
    assert check.stop_wins == 1  # 그런데 손절이 이겼다
    assert check.recorded_same_tp == 0
    assert check.tp_matches, "손절 우선 규칙을 되돌리지 않으면 익절 수가 어긋난다"


def test_a_bar_below_the_zone_bottom_is_flagged_not_swallowed() -> None:
    """§3 논증이 깨지는 자리 — 저가가 존 바닥 **아래**면 그 사실이 드러나야 한다."""
    measured = _measured(
        [_trade(entry=100.0, stop=99.0, tp=101.5, low=98.0, high=100.4, reason="손절")]
    )
    check = geometry_check(measured)
    assert check.below_zone_bottom == 1
    assert check.exact_touch == 0


# --------------------------------------------------------------------------- #
# 비대칭 — §2-C 결론 문장의 근거
# --------------------------------------------------------------------------- #


def test_same_minute_stop_is_fill_independent_but_take_profit_is_not() -> None:
    """체결가를 옮기면 익절 가능 수는 움직이고 손절 가능 수는 **한 건도 안 움직인다**."""
    measured = _measured(
        [
            _trade(entry=100.0, stop=99.0, tp=101.5, low=99.6, high=102.0),
            _trade(entry=100.0, stop=99.0, tp=101.5, low=99.5, high=101.0),
        ]
    )
    table = fill_placement_counterfactual(measured)
    assert set(table["같은분손절 가능"]) == {0}, "손절 가능 수가 체결가에 의존하면 §2-C가 틀린다"
    assert table["같은분익절 가능"].nunique() > 1, (
        "익절 가능 수가 체결가에 안 움직이면 민감도가 없다"
    )


def test_fill_position_and_r_scaled_excursions() -> None:
    """봉 내 위치와 R 환산 이탈이 정의대로 나온다."""
    measured = _measured([_trade(entry=100.0, stop=99.0, tp=101.5, low=99.5, high=101.5)])
    row = measured.iloc[0]
    assert row["fill_position"] == pytest.approx(0.25)  # (100 − 99.5) / 2.0
    assert row["entry_bar_mae_r"] == pytest.approx(0.5)  # 0.5 / 1R
    assert row["entry_bar_mfe_r"] == pytest.approx(1.5)  # 1.5 / 1R


def test_short_trades_are_rejected_not_silently_counted() -> None:
    """채택 엔진은 롱 온리다 — 숏이 섞이면 아래 기하가 통째로 뒤집혀야 하므로 죽는다."""
    frame = pd.DataFrame([_trade(entry=100.0, stop=99.0, tp=101.5, low=99.5, high=101.5)])
    frame.loc[0, "방향"] = "숏"
    path = Path("tests/.wan362-short.csv")
    frame.to_csv(path, index=False)
    try:
        with pytest.raises(ValueError, match="롱이 아닌"):
            load_trades(path)
    finally:
        path.unlink()


def test_missing_columns_fail_loudly() -> None:
    path = Path("tests/.wan362-empty.csv")
    pd.DataFrame([{"a": 1}]).to_csv(path, index=False)
    try:
        with pytest.raises(ValueError, match="필요한 열"):
            load_trades(path)
    finally:
        path.unlink()


def test_position_table_keeps_the_missing_bars_out_but_reports_them() -> None:
    """봉을 못 붙인 행은 분포에서 빠지되 **몇 건인지 남는다**(조용히 표본을 줄이지 않는다)."""
    rows = [
        _trade(entry=100.0, stop=99.0, tp=101.5, low=99.6, high=102.0),
        _trade(entry=100.0, stop=99.0, tp=101.5, low=99.6, high=102.0),
    ]
    measured = _measured(rows)
    measured.loc[measured.index[1], ["bar_low", "bar_high", "bar_close"]] = math.nan
    measured = measure(measured)
    table = position_table(measured)
    assert int(table[table["구간"] == "전체"]["거래"].iloc[0]) == 1
    assert geometry_check(measured).missing_bars == 1


# --------------------------------------------------------------------------- #
# §1 인구조사 — 같은 자로 세는가
# --------------------------------------------------------------------------- #


def _record(
    *, entry_ms: int, exit_ms: int, reason: SignalExitReason, symbol: str = "BTC/USDT:USDT"
) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol=symbol,
        timeframe="15m",
        direction=OrderBlockDirection.BULLISH,
        entry_time=entry_ms,
        entry_price=100.0,
        exit_time=exit_ms,
        exit_price=101.0,
        reason=reason,
        gross_pct=1.0,
        fee_pct=0.04,
        funding_pct=0.0,
        net_pct=0.96,
        realized_pnl=10.0,
    )


def test_same_minute_uses_the_minute_bucket_not_equality() -> None:
    """라이브 장부는 틱 시각(ms)이다 — 분으로 내림해야 백테스트의 1분봉과 같은 자다."""
    base = 1_735_689_600_000
    assert minute_bucket(base + 59_999) == base
    assert is_same_minute(
        _record(entry_ms=base + 2_000, exit_ms=base + 58_000, reason=SignalExitReason.STOP_LOSS)
    )
    assert not is_same_minute(
        _record(
            entry_ms=base + 59_000, exit_ms=base + MINUTE + 1_000, reason=SignalExitReason.STOP_LOSS
        )
    )


def test_census_splits_by_day_symbol_and_timeframe() -> None:
    base = 1_735_689_600_000  # 2025-01-01 00:00 UTC = 09:00 KST
    records = [
        _record(entry_ms=base, exit_ms=base + 10_000, reason=SignalExitReason.STOP_LOSS),
        _record(
            entry_ms=base + MINUTE, exit_ms=base + 3 * MINUTE, reason=SignalExitReason.TAKE_PROFIT
        ),
        _record(
            entry_ms=base + 86_400_000,
            exit_ms=base + 86_400_000 + 20_000,
            reason=SignalExitReason.TAKE_PROFIT,
            symbol="ETH/USDT:USDT",
        ),
    ]
    rows = census(records)
    total = next(row for row in rows if row.group == "전체")
    assert (total.trades, total.same_minute, total.same_tp, total.same_sl) == (3, 2, 1, 1)
    assert {row.label for row in rows if row.group == "종목"} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    assert len({row.label for row in rows if row.group == "날짜(KST)"}) == 2
    dropped = dict(leave_one_day_out(records))
    assert len(dropped) == 2
    assert all(row.trades < total.trades for row in dropped.values())


def test_verdict_uses_wilson_coverage_not_a_normal_approximation() -> None:
    """7건짜리 표본에서도 기준이 구간 밖이면 판정이 선다 — 정규근사는 여기서 성립하지 않는다."""
    verdict = judge("구성", 2, 7, BACKTEST_REFERENCE.take_profit_share)
    assert verdict.decided
    assert 0.0 < verdict.low < verdict.rate < verdict.high < 1.0
    assert verdict.p_value < 1e-6
    assert verdict.required == 1

    undecided = judge("빈도", 3, 32, BACKTEST_REFERENCE.same_minute_rate)
    assert not undecided.decided, "관측이 기준과 가까우면 판정이 서면 안 된다"


def test_binomial_tail_and_required_sample_are_consistent() -> None:
    assert binomial_tail_p(0, 1, 1.0, upper=False) == pytest.approx(0.0)
    assert binomial_tail_p(0, 5, 0.5, upper=False) == pytest.approx(0.5**5)
    assert binomial_tail_p(5, 5, 0.5, upper=True) == pytest.approx(0.5**5)
    # 관측이 기준과 같으면 어떤 표본으로도 안 갈린다.
    assert required_sample(0.5, 0.5) is None
    # 차이가 클수록 적은 표본으로 갈린다.
    near = required_sample(0.10, 0.075)
    far = required_sample(0.50, 0.075)
    assert far is not None and (near is None or far < near)


def test_backtest_reference_matches_wan336() -> None:
    """대조군 상수가 WAN-336이 낸 수 그대로인지 — 바뀌면 §1 판정이 통째로 움직인다."""
    assert BACKTEST_REFERENCE.same_minute == 474
    assert BACKTEST_REFERENCE.same_minute_rate == pytest.approx(474 / 6336)
    assert BACKTEST_REFERENCE.take_profit_share == pytest.approx(467 / 474)


# --------------------------------------------------------------------------- #
# 실데이터 — 있을 때만 (CI의 빈 저장소에서는 skip)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not TRADES_CSV.exists(), reason="WAN-346 거래별 CSV가 없다")
def test_real_trades_csv_has_the_population_wan336_reported() -> None:
    frame = load_trades()
    assert len(frame) == 6336
    assert int((frame["같은분익절"].astype(str) == "True").sum()) == 467
    same_stop = frame["same_minute"] & (frame["청산사유"] == "손절")
    assert int(same_stop.sum()) == 7


@pytest.mark.skipif(not TRADES_CSV.exists(), reason="WAN-346 거래별 CSV가 없다")
def test_real_take_profit_rebuild_reproduces_recorded_values() -> None:
    known, matched, max_rel = take_profit_checksum(load_trades())
    assert known > 0
    assert matched == known, f"고정 R 규칙이 기록된 익절가를 재현하지 못한다(최대 {max_rel:.2e})"
