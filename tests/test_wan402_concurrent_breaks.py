"""WAN-402 §0 `concurrent_breaks` — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것:

1. **인과성** — 진입 시각 `t` 이전에 **닫힌 봉**에서 알려진 무효화만 센다(`break_known = break_time
   + TF`). 창 `(t − W, t]`의 경계가 정확하고, 같은 종목은 전 TF가 빠진다.
2. **생존 수(분모)** — 창 시작 시점에 「살아 있다고 알 수 있는」 다른 종목 존 수다.
3. **판정은 코드가 낸다** — 착수 전에 못 박은 규칙 셋(앞구간 단조·뒷구간 같은 방향·Bonferroni 2σ)이
   각각 실제로 걸리고, 분위 뭉침·표본 미달은 「판정 불가」로 찍는다(지어내지 않는다).
4. **모집단 규칙** — `band` · 첫 탭 · 가드 통과 · 데이터 끝 제외만 남고, 없으면 시끄럽게 죽는다.
5. **WAN-410 자를 그대로 쓴다** — 실현 누계가 `realized_r_before(convention="settled")`의 값이다.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest import wan402_concurrent_breaks as wan402
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.wan375_conditional_rr import CostRates, cost_r
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan402_concurrent_breaks import (
    HYPOTHESIS_SIGN,
    MIN_BIN_N,
    MIN_BINS,
    QUANTILES,
    SIGMA_MULTIPLE,
    TESTS,
    VERDICT_GO,
    VERDICT_NO,
    VERDICT_UNDECIDED,
    WINDOWS,
    BinRow,
    BreakIndex,
    ZoneEvent,
    assign_bins,
    bin_edges,
    decision_z,
    label_setups,
    load_population,
    verdict_for_window,
    zones_from_frame,
)

_H = 3_600_000
_REAL_DB = Path("data/ohlcv.db")


# --------------------------------------------------------------------------- #
# 1·2 — 인과성 · 창 경계 · 자기 제외 · 생존 수
# --------------------------------------------------------------------------- #


def _ledger() -> BreakIndex:
    return BreakIndex(
        [
            ZoneEvent("ETH", "1h", confirmed_known=0, break_known=10 * _H),
            ZoneEvent("ETH", "4h", confirmed_known=0, break_known=None),
            ZoneEvent("SOL", "15m", confirmed_known=5 * _H, break_known=12 * _H),
            ZoneEvent("BTC", "1h", confirmed_known=0, break_known=11 * _H),
            ZoneEvent("BTC", "4h", confirmed_known=0, break_known=11 * _H),
        ]
    )


def test_window_is_left_open_right_closed_and_causal() -> None:
    index = _ledger()
    # 무효화가 알려진 시각과 정확히 같은 t에서는 센다(닫힌 봉의 정보다).
    assert index.breaks_in(exclude="BTC", lo=9 * _H, hi=10 * _H) == 1
    # 1ms 앞에서는 아직 모른다.
    assert index.breaks_in(exclude="BTC", lo=9 * _H, hi=10 * _H - 1) == 0
    # 창의 왼쪽 끝은 열려 있다 — lo에 정확히 놓인 사건은 다음 창의 몫이다.
    assert index.breaks_in(exclude="BTC", lo=10 * _H, hi=11 * _H) == 0
    assert index.breaks_in(exclude="BTC", lo=10 * _H - 1, hi=12 * _H) == 2


def test_same_symbol_is_excluded_on_every_timeframe() -> None:
    index = _ledger()
    # BTC의 1h·4h 무효화(11h)는 BTC 진입에서 둘 다 빠진다 — 동어반복 방지.
    assert index.breaks_in(exclude="BTC", lo=0, hi=13 * _H) == 2
    assert index.breaks_in(exclude="ETH", lo=0, hi=13 * _H) == 3


def test_alive_count_uses_known_times() -> None:
    index = _ledger()
    # t=4h: ETH 1h·4h 확정 알려짐(0), SOL은 5h에야 알려진다, BTC는 제외.
    assert index.alive_at(exclude="BTC", at=4 * _H) == 2
    assert index.alive_at(exclude="BTC", at=5 * _H) == 3
    # ETH 1h가 10h에 깨졌다고 알려지면 그 순간부터 생존에서 빠진다.
    assert index.alive_at(exclude="BTC", at=10 * _H) == 2
    assert index.alive_at(exclude="BTC", at=10 * _H - 1) == 3


def test_break_before_confirmation_is_rejected() -> None:
    with pytest.raises(AssertionError):
        BreakIndex([ZoneEvent("ETH", "1h", confirmed_known=10, break_known=5)])


def test_zone_events_survive_csv_roundtrip_with_missing_break() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["ETH", "SOL"],
            "timeframe": ["1h", "1h"],
            "confirmed_known": [1, 2],
            "break_known": [float("nan"), 7.0],
        }
    )
    events = zones_from_frame(frame)
    assert events[0].break_known is None
    assert events[1].break_known == 7


# --------------------------------------------------------------------------- #
# 라벨링 — 창 셋 × (건수 · 생존 · 비율 · 다른 칸 손절) ＋ WAN-410 자
# --------------------------------------------------------------------------- #


def _population() -> pd.DataFrame:
    day = 20 * _H  # KST 자정에서 떨어진 자리(모든 시각이 같은 KST 날에 놓이도록 작게 잡는다)
    rows = [
        # symbol, tf, segment, entry, exit, reason, width
        ("BTC", "1h", "is", day + 12 * _H, day + 13 * _H, "stop_loss", 0.01),
        ("ETH", "1h", "is", day + 12 * _H + 1, day + 14 * _H, "take_profit", 0.01),
        ("SOL", "1h", "oos_warm", day + 14 * _H, day + 15 * _H, "stop_loss", 0.02),
        ("BTC", "4h", "oos_warm", day + 14 * _H, day + 16 * _H, "take_profit", 0.02),
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "timeframe",
            "segment",
            "entry_time",
            "tp_on_exit_time",
            "tp_on_reason",
            "stop_width",
        ],
    )
    frame["is_long"] = True
    frame["tap_index"] = 0
    frame["arm"] = "band"
    frame["net_r"] = wan402.net_r_column(frame)
    frame["is_stop"] = frame["tp_on_reason"] == "stop_loss"
    return frame


def test_labels_count_breaks_alive_ratio_and_other_stops() -> None:
    day = 20 * _H
    index = BreakIndex(
        [
            ZoneEvent("ETH", "1h", confirmed_known=day, break_known=day + 11 * _H + 1),
            ZoneEvent("SOL", "1h", confirmed_known=day, break_known=None),
            ZoneEvent("BTC", "1h", confirmed_known=day, break_known=day + 11 * _H + 1),
        ]
    )
    labeled = label_setups(_population(), index)
    btc = labeled[(labeled["symbol"] == "BTC") & (labeled["timeframe"] == "1h")].iloc[0]
    # 1h 창 (11h, 12h] 안에 ETH의 무효화(11h+1ms)가 있고 BTC 자신의 것은 빠진다.
    assert btc["cb_count_1h"] == 1
    # 창 시작(11h)에 살아 있던 다른 종목 존 = ETH·SOL 둘.
    assert btc["cb_alive_1h"] == 2
    assert btc["cb_ratio_1h"] == pytest.approx(0.5)
    # 포개진 창은 건수가 단조다.
    assert btc["cb_count_1h"] <= btc["cb_count_4h"] <= btc["cb_count_24h"]
    # 다른 칸 손절: SOL(14h 진입 · 15h 손절)에서 1h 창 (14h, 15h]에 BTC 손절(13h)은 없다.
    sol = labeled[labeled["symbol"] == "SOL"].iloc[0]
    assert sol["other_stops_1h"] == 0
    assert sol["other_stops_4h"] == 1  # (11h, 15h]에 BTC 1h 손절 13h.


def test_realized_today_before_is_wan410_settled_ruler() -> None:
    from backtest.wan408_loss_clustering import TradeFact
    from backtest.wan410_daily_loss_circuit_breaker import realized_r_before

    labeled = label_setups(_population(), BreakIndex([]))
    facts = [
        TradeFact(
            symbol=str(r["symbol"]),
            timeframe=str(r["timeframe"]),
            entry_time=int(r["entry_time"]),
            exit_time=int(r["tp_on_exit_time"]),
            is_stop=bool(r["is_stop"]),
            net_r=float(r["net_r"]),
            is_reentry=False,
        )
        for r in labeled.to_dict("records")
    ]
    expected = realized_r_before(facts, convention="settled")
    assert labeled["realized_today_before"].tolist() == pytest.approx(expected)
    # SOL(14h 진입)은 그 전에 청산된 BTC 손절(13h)·ETH 익절(14h, 경계 포함)을 둘 다 안다.
    sol = labeled[labeled["symbol"] == "SOL"].iloc[0]
    btc, eth = labeled.iloc[0], labeled.iloc[1]
    assert sol["realized_today_before"] == pytest.approx(btc["net_r"] + eth["net_r"])


# --------------------------------------------------------------------------- #
# 분위 — 앞구간 경계 · 뭉침
# --------------------------------------------------------------------------- #


def test_zero_inflated_values_collapse_to_fewer_bins() -> None:
    values = pd.Series([0.0] * 80 + [0.1, 0.2, 0.3, 0.4] * 5)
    edges = bin_edges(values)
    assert len(edges) - 1 < QUANTILES
    bins = assign_bins(values, edges)
    assert int(bins.min()) == 0
    assert int(bins.max()) == len(edges) - 2


def test_assign_bins_uses_is_edges_and_clamps_outside() -> None:
    edges = [0.0, 1.0, 2.0, 3.0]
    bins = assign_bins(pd.Series([0.5, 1.0, 2.5, 99.0, float("nan")]), edges)
    assert bins.tolist()[:4] == [0, 1, 2, 2]
    assert pd.isna(bins.iloc[4])


# --------------------------------------------------------------------------- #
# 판정 — 규칙 셋이 각각 걸린다
# --------------------------------------------------------------------------- #


def _rows(
    is_curve: list[float], oos_curve: list[float], *, n: int = 500, se: float = 0.01
) -> list[BinRow]:
    rows: list[BinRow] = []
    for segment, curve in (("is", is_curve), ("oos_warm", oos_curve)):
        for b, value in enumerate(curve):
            rows.append(
                BinRow(
                    window="4h",
                    measure="ratio",
                    segment=segment,
                    bin=b,
                    bins=len(curve),
                    lo=float(b),
                    hi=float(b + 1),
                    n=n,
                    stops=n // 2,
                    stop_rate=0.5,
                    mean_net_r=value,
                    net_r_stderr=se,
                    mean_feature=float(b),
                )
            )
    return rows


def test_pinned_constants_are_what_the_issue_fixed() -> None:
    assert [w for w, _ in WINDOWS] == ["1h", "4h", "24h"]
    assert QUANTILES == 5 and MIN_BINS == 3 and MIN_BIN_N == 100
    assert SIGMA_MULTIPLE == 2.0 and TESTS == 3 and HYPOTHESIS_SIGN == -1
    # Bonferroni는 2σ보다 **엄격**해야 한다 — 느슨해지면 보정이 아니다.
    assert decision_z() > SIGMA_MULTIPLE
    # 양측 α = 2·(1−Φ(2)) ≈ 4.55% → /3 → z ≈ 2.43.
    assert decision_z() == pytest.approx(2.428, abs=0.005)


def test_verdict_goes_only_when_all_three_gates_pass() -> None:
    go = verdict_for_window(
        _rows([0.3, 0.2, 0.1, 0.0, -0.1], [0.2, 0.1, 0.0, -0.1, -0.2]), window="4h", measure="ratio"
    )
    assert go.verdict == VERDICT_GO and go.is_monotone and go.oos_decided


def test_verdict_rejects_non_monotone_is_curve() -> None:
    v = verdict_for_window(
        _rows([0.3, 0.1, 0.2, 0.0, -0.1], [0.2, 0.1, 0.0, -0.1, -0.2]), window="4h", measure="ratio"
    )
    assert v.verdict == VERDICT_NO and "비단조" in v.reason


def test_verdict_rejects_direction_against_hypothesis() -> None:
    v = verdict_for_window(
        _rows([-0.1, 0.0, 0.1, 0.2, 0.3], [-0.2, -0.1, 0.0, 0.1, 0.2]), window="4h", measure="ratio"
    )
    assert v.verdict == VERDICT_NO and "가설과 반대" in v.reason


def test_verdict_rejects_oos_sign_flip() -> None:
    v = verdict_for_window(
        _rows([0.3, 0.2, 0.1, 0.0, -0.1], [-0.2, -0.1, 0.0, 0.1, 0.2]), window="4h", measure="ratio"
    )
    assert v.verdict == VERDICT_NO and "뒷구간 방향" in v.reason


def test_verdict_rejects_undecided_sign_after_bonferroni() -> None:
    # 차 0.03R, σ = sqrt(2)·0.01 ≈ 0.0141 → z ≈ 2.12: 2σ는 넘지만 Bonferroni(2.39)는 못 넘는다.
    v = verdict_for_window(
        _rows([0.3, 0.2, 0.1, 0.0, -0.1], [0.02, 0.01, 0.0, -0.005, -0.01]),
        window="4h",
        measure="ratio",
    )
    assert v.oos_z is not None and SIGMA_MULTIPLE < v.oos_z < decision_z()
    assert v.verdict == VERDICT_NO and "Bonferroni" in v.reason


def test_verdict_is_undecided_when_bins_collapse_or_samples_are_thin() -> None:
    few = verdict_for_window(_rows([0.3, 0.1], [0.2, 0.0]), window="4h", measure="ratio")
    assert few.verdict == VERDICT_UNDECIDED and "분위" in few.reason
    thin = verdict_for_window(
        _rows([0.3, 0.2, 0.1, 0.0, -0.1], [0.2, 0.1, 0.0, -0.1, -0.2], n=MIN_BIN_N - 1),
        window="4h",
        measure="ratio",
    )
    assert thin.verdict == VERDICT_UNDECIDED and "표본" in thin.reason


# --------------------------------------------------------------------------- #
# 모집단 — 규칙대로만 남고, 없으면 시끄럽게 죽는다
# --------------------------------------------------------------------------- #


def test_net_r_column_matches_wan375_cost_ruler() -> None:
    frame = pd.DataFrame(
        {
            "timeframe": ["1h", "1h"],
            "stop_width": [0.01, 0.02],
            "tp_on_reason": ["take_profit", "stop_loss"],
        }
    )
    target_r = harness.build_params().take_profit_r
    rates = CostRates.from_config(harness.build_config("1h"))
    c_win, _ = cost_r(0.01, target_r, rates)
    _, c_loss = cost_r(0.02, target_r, rates)
    values = wan402.net_r_column(frame).tolist()
    assert values[0] == pytest.approx(target_r - c_win)
    assert values[1] == pytest.approx(-1.0 - c_loss)
    with pytest.raises(AssertionError):
        wan402.net_r_column(frame.assign(tp_on_reason=["hold", "stop_loss"]))


def test_load_population_keeps_only_the_declared_rows(tmp_path: Path) -> None:
    base = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "segment": "oos_warm",
        "is_long": True,
        "trigger_time": 1,
        "entry_time": 2,
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_time": 3,
        "exit_reason": "stop_loss",
        "tp_on_exit_time": 3,
    }
    rows = [
        {**base, "arm": "band", "tap_index": 0, "stop_width": 0.01, "tp_on_reason": "stop_loss"},
        {**base, "arm": "band", "tap_index": 0, "stop_width": 0.01, "tp_on_reason": "take_profit"},
        {
            **base,
            "arm": "zone_top",
            "tap_index": 0,
            "stop_width": 0.01,
            "tp_on_reason": "stop_loss",
        },
        {**base, "arm": "band", "tap_index": 1, "stop_width": 0.01, "tp_on_reason": "stop_loss"},
        {
            **base,
            "arm": "band",
            "tap_index": 0,
            "stop_width": ADOPTED_STOP_GUARD / 2,
            "tp_on_reason": "stop_loss",
        },
        {
            **base,
            "arm": "band",
            "tap_index": 0,
            "stop_width": 0.01,
            "tp_on_reason": ExitReason.END_OF_DATA.value,
        },
    ]
    path = tmp_path / "setups.csv.gz"
    pd.DataFrame(rows).to_csv(path, index=False)
    population = load_population(path)
    assert len(population) == 2
    assert set(population["tp_on_reason"]) == {"stop_loss", "take_profit"}
    assert population["symbol"].iloc[0] == harness.normalize_symbol("BTCUSDT")
    with pytest.raises(FileNotFoundError):
        load_population(tmp_path / "missing.csv.gz")


# --------------------------------------------------------------------------- #
# 실데이터 — 존 대장이 엔진의 그 아카이브다(있을 때만)
# --------------------------------------------------------------------------- #


def test_real_zone_ledger_uses_bar_close_known_times() -> None:
    if not _REAL_DB.exists():
        pytest.skip("실데이터가 없어 건너뜁니다(CI 기본).")
    task = wan402._ZoneTask(
        symbol=harness.normalize_symbol("BTCUSDT"),
        timeframe="4h",
        start_ms=parse_date_ms("2024-01-01"),
        end_ms=parse_date_ms("2024-03-01"),
    )
    market = harness.load_market_data(
        task.symbol, "4h", start_ms=task.start_ms, end_ms=task.end_ms, need_1m=False, funding=False
    )
    if market.empty:
        pytest.skip("실데이터가 없어 건너뜁니다(CI 기본).")
    events = wan402.zone_events_for_cell(task)
    from strategy.models import OrderBlockDirection, OrderBlockParams

    archive = [
        ob
        for ob in harness.detect_order_blocks(market, OrderBlockParams()).order_blocks
        if ob.direction is OrderBlockDirection.BULLISH
    ]
    assert len(events) == len(archive) > 0
    tf_ms = 4 * _H
    for event, ob in zip(events, archive, strict=True):
        assert event.confirmed_known == ob.confirmed_time + tf_ms
        assert event.break_known == (None if ob.break_time is None else ob.break_time + tf_ms)
        assert event.break_known is None or event.break_known >= event.confirmed_known
    assert not math.isnan(float(len(events)))
