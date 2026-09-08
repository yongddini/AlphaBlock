"""WAN-410 — 하루 손실 서킷브레이커 (엔진 축 · §1-0 정의 · 매칭 대조군).

## 이 파일이 고정하는 것

1. **안 켜면 비트 재현** — 서킷브레이커 인자를 안 주면 거래·손익이 예전과 **같다**.
2. **라벨이 아니라 동작** — 문턱을 넘은 뒤 진입한 거래가 **실제로 0건**이고, **이미 열린
   포지션은 안 건드린다**(청산 시각·사유 불변).
3. **범위 셋이 실제로 다르게 돈다** — `entry`/`reentry`/`both`가 서로 다른 거래를 막는다.
4. **경계와 하루 규약** — 문턱에 **닿으면** 발동(`<=`)하고, KST 자정에 **리셋**된다.
5. **§1-0 대수 항등** — (b) 두 규약의 차가 정확히 `#{진입 == 청산 == t}`다.
6. **널이 항등으로 퇴화하면 죽는다** — 재배정이 아무것도 안 하면 테스트가 실패한다
   (WAN-408 §0-2가 「종목 안 순열은 통계량을 안 바꾼다」를 몰라 헛돌린 선례).
"""

from __future__ import annotations

import random

import pytest

from backtest.leverage_book import BookCell, LeverageBookParams, run_leverage_book
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.wan408_loss_clustering import TradeFact
from backtest.wan410_daily_loss_circuit_breaker import (
    Arm,
    day_start_ms,
    definition_rows,
    null_pvalue,
    open_cells_before,
    realized_r_before,
    shuffled_schedule,
    wallet_defined,
)
from backtest.zone_limit_backtest import _Candidate
from execution.sizing import PositionSizingParams

_MINUTE = 60_000
_DAY = 86_400_000

#: KST 자정 두 개 — 「하루」가 사람이 읽는 하루와 같은지 거는 데 쓴다(WAN-172).
DAY1 = day_start_ms("2024-03-05")
DAY2 = day_start_ms("2024-03-06")


def _cand(
    entry_time: int,
    exit_time: int,
    *,
    exit_price: float,
    reason: ExitReason,
    is_reentry: bool = False,
) -> _Candidate:
    """실제 엔진 자료형 그대로 — 대역을 쓰면 사이징·비용 검증이 라벨 검증으로 퇴화한다."""
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=exit_price,
        reason=reason,
        stop_price=90.0,
        trigger_time=entry_time,
        is_reentry=is_reentry,
    )


def _loser(entry: int, exit_: int, *, is_reentry: bool = False) -> _Candidate:
    """손절 하나 = 정확히 −1R(진입 100 · 손절 90)."""
    return _cand(entry, exit_, exit_price=90.0, reason=ExitReason.STOP_LOSS, is_reentry=is_reentry)


def _winner(entry: int, exit_: int, *, is_reentry: bool = False) -> _Candidate:
    return _cand(
        entry, exit_, exit_price=115.0, reason=ExitReason.TAKE_PROFIT, is_reentry=is_reentry
    )


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=1_000_000.0,
        fee_rate=0.0,
        maker_fee_rate=0.0,
        slippage=0.0,
        risk_sizing=PositionSizingParams(
            sizing_mode="risk_pct",
            risk_per_trade=0.01,
            leverage=100.0,
            min_stop_distance_fraction=0.0,
        ),
    )


def _cells(*groups: tuple[str, list[_Candidate]]) -> list[BookCell]:
    return [BookCell(symbol=sym, timeframe="1h", candidates=cands) for sym, cands in groups]


def _run(cells: list[BookCell], **kwargs: object) -> tuple[list[int], object]:
    outcome = run_leverage_book(
        cells,
        _cfg(),
        LeverageBookParams(leverage_mode="cap_only", leverage_multiple=5.0),
        **kwargs,  # type: ignore[arg-type]
    )
    return [t.entry_time for t in outcome.trades], outcome


# --------------------------------------------------------------------------- #
# 1. 안 켜면 비트 재현
# --------------------------------------------------------------------------- #


def test_breaker_off_is_bit_identical() -> None:
    """인자를 안 주면 예전 경로와 **거래·손익이 같다** — 옵트인 계약의 본문이다."""
    cells = _cells(
        (
            "BTCUSDT",
            [_loser(DAY1, DAY1 + _MINUTE), _winner(DAY1 + 2 * _MINUTE, DAY1 + 3 * _MINUTE)],
        ),
        ("ETHUSDT", [_loser(DAY1 + _MINUTE, DAY1 + 2 * _MINUTE)]),
    )
    plain, out_plain = _run(cells)
    explicit, out_explicit = _run(
        cells, daily_loss_limit_r=None, daily_stop_limit=None, circuit_breaker_scope="both"
    )
    assert plain == explicit
    assert [t.realized_pnl for t in out_plain.trades] == [  # type: ignore[attr-defined]
        t.realized_pnl
        for t in out_explicit.trades  # type: ignore[attr-defined]
    ]
    assert out_plain.stats.skipped_circuit_breaker == 0  # type: ignore[attr-defined]
    assert out_plain.stats.circuit_breaker_days == {}  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# 2. 라벨이 아니라 동작
# --------------------------------------------------------------------------- #


def test_breaker_blocks_entries_after_trip_and_leaves_open_positions_alone() -> None:
    """문턱을 넘은 **뒤** 진입은 0건이고, **이미 열린 포지션은 그대로** 끝난다.

    🚨 이 테스트가 이 PR의 핵심 계약이다 — 「막는다」와 「던진다」는 다른 규칙이고, 후자를
    하면 이 축은 *「언제 매매를 멈추나」*가 아니라 *「언제 던지나」*가 된다.
    """
    # BTC: DAY1 00:00 진입 → 00:02 손절(−1R). ETH: 00:01 진입 → **00:10 청산**(발동을 걸친다).
    # SOL: 00:05 진입 — 발동(누계 −1R ≤ −1R) **뒤**라 막혀야 한다.
    cells = _cells(
        ("BTCUSDT", [_loser(DAY1, DAY1 + 2 * _MINUTE)]),
        ("ETHUSDT", [_winner(DAY1 + _MINUTE, DAY1 + 10 * _MINUTE)]),
        ("SOLUSDT", [_winner(DAY1 + 5 * _MINUTE, DAY1 + 6 * _MINUTE)]),
    )
    base_entries, base_out = _run(cells)
    assert base_entries == [DAY1, DAY1 + _MINUTE, DAY1 + 5 * _MINUTE]

    entries, out = _run(cells, daily_loss_limit_r=-1.0)
    assert entries == [DAY1, DAY1 + _MINUTE], "발동 뒤 진입이 남아 있다 = 팔이 라벨뿐이다"
    assert out.stats.skipped_circuit_breaker == 1  # type: ignore[attr-defined]
    assert out.stats.circuit_breaker_days == {"2024-03-05": 1}  # type: ignore[attr-defined]
    assert out.stats.circuit_breaker_first_trip == {  # type: ignore[attr-defined]
        "2024-03-05": DAY1 + 5 * _MINUTE
    }

    # 걸쳐 있던 ETH 포지션은 **손도 안 댄다** — 청산 시각·사유·손익 전부 같다.
    base_eth = base_out.trades[1]  # type: ignore[attr-defined]
    kept_eth = out.trades[1]  # type: ignore[attr-defined]
    assert kept_eth.exits[-1].time == base_eth.exits[-1].time
    assert kept_eth.exits[-1].reason is base_eth.exits[-1].reason
    assert kept_eth.realized_pnl == pytest.approx(base_eth.realized_pnl)


def test_breaker_resets_at_kst_midnight() -> None:
    """하루는 **KST 날짜**다 — 자정을 넘으면 누계가 리셋돼 다시 진입한다(WAN-172)."""
    cells = _cells(
        ("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]),
        ("ETHUSDT", [_winner(DAY1 + 2 * _MINUTE, DAY1 + 3 * _MINUTE)]),
        # 다음 KST 하루의 첫 분 — 어제 손실은 안 따라온다.
        ("SOLUSDT", [_winner(DAY2, DAY2 + _MINUTE)]),
    )
    entries, out = _run(cells, daily_loss_limit_r=-1.0)
    assert DAY1 + 2 * _MINUTE not in entries, "같은 날인데 안 막혔다"
    assert DAY2 in entries, "자정을 넘었는데도 어제 손실이 따라왔다"
    assert set(out.stats.circuit_breaker_days) == {"2024-03-05"}  # type: ignore[attr-defined]


def test_threshold_boundary_trips_on_touch() -> None:
    """경계는 **`<=`** — 문턱에 **닿으면** 발동한다(라벨이 아니라 값으로 고정)."""
    cells = _cells(
        ("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]),
        ("ETHUSDT", [_winner(DAY1 + 2 * _MINUTE, DAY1 + 3 * _MINUTE)]),
    )
    touched, _ = _run(cells, daily_loss_limit_r=-1.0)
    assert DAY1 + 2 * _MINUTE not in touched, "−1R에 닿았는데 안 막혔다"
    not_yet, _ = _run(cells, daily_loss_limit_r=-2.0)
    assert DAY1 + 2 * _MINUTE in not_yet, "−1R뿐인데 −2R 문턱이 발동했다"


def test_settlement_boundary_includes_same_instant_exit() -> None:
    """경계 규약: **`청산 ≤ 진입`**(북이 그 순간 아는 것) — 같은 ms 청산도 누계에 든다.

    🚨 WAN-408 §0-3의 관측(`청산 < 진입`)과 **다른 수**다. 스위치는 집행이라 북의 정산
    규약을 따라야 하고, §1-0이 그 차이를 숫자로 낸다.
    """
    cells = _cells(
        ("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]),
        # 손절과 **정확히 같은 ms**에 진입하려는 후보.
        ("ETHUSDT", [_winner(DAY1 + _MINUTE, DAY1 + 2 * _MINUTE)]),
    )
    entries, _ = _run(cells, daily_loss_limit_r=-1.0)
    assert DAY1 + _MINUTE not in entries, "같은 ms 청산이 누계에 안 들었다(strict로 돌았다)"


# --------------------------------------------------------------------------- #
# 3. 범위 셋이 실제로 다르게 돈다
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("scope", "expect_new", "expect_reentry"),
    [("entry", False, True), ("reentry", True, False), ("both", False, False)],
)
def test_scope_selects_what_is_blocked(scope: str, expect_new: bool, expect_reentry: bool) -> None:
    """`entry`/`reentry`/`both`가 **서로 다른 거래**를 막는다 — 라벨이 아니라 동작으로."""
    new_entry = DAY1 + 5 * _MINUTE
    rearm = DAY1 + 6 * _MINUTE
    # 🚨 신규 진입은 재무장 **뒤에** 청산된다 — 안 그러면 그 이익이 누계를 되살려
    # 「범위」가 아니라 「그날이 회복됐다」를 재게 된다(범위 축이 조용히 흐려진다).
    cells = _cells(
        ("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]),
        ("ETHUSDT", [_winner(new_entry, DAY1 + 60 * _MINUTE)]),
        ("SOLUSDT", [_winner(rearm, rearm + _MINUTE, is_reentry=True)]),
    )
    entries, _ = _run(cells, daily_loss_limit_r=-1.0, circuit_breaker_scope=scope)
    assert (new_entry in entries) is expect_new
    assert (rearm in entries) is expect_reentry


def test_breaker_is_a_latch_within_the_day() -> None:
    """🚨 **한 번 발동하면 그 KST 하루는 안 열린다** — 늦은 익절로 누계가 회복돼도 그대로.

    래치가 아니면 「누계」가 오르내리며 매매가 다시 열리고, 그러면 *「발동 뒤 진입 0건」*이
    **정의상 성립하지 않는다**(4h 연기 시험에서 실제로 12건이 샜다).
    """
    cells = _cells(
        # 00:00 진입 → 00:01 손절(−1R) = 발동.
        ("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]),
        # 발동 전에 이미 열려 있던 큰 익절이 00:05에 청산돼 누계를 **플러스로 되돌린다**.
        ("ETHUSDT", [_winner(DAY1, DAY1 + 5 * _MINUTE)]),
        # 그 뒤 진입 — 누계는 회복됐지만 래치는 그대로 내려가 있다.
        ("SOLUSDT", [_winner(DAY1 + 6 * _MINUTE, DAY1 + 7 * _MINUTE)]),
    )
    entries, out = _run(cells, daily_loss_limit_r=-1.0)
    assert DAY1 + 6 * _MINUTE not in entries, "누계가 회복되자 래치가 풀렸다"
    assert out.stats.circuit_breaker_days == {"2024-03-05": 1}  # type: ignore[attr-defined]


def test_same_instant_entry_is_not_blocked_and_that_is_causal() -> None:
    """🚨 **손실을 만든 그 거래 자신은 자기 손실로 막힐 수 없다 — 그게 인과다.**

    진입과 청산이 같은 1분인 거래(WAN-336 「같은 분」 부류)는 **자신이 배치된 뒤에야** 정산
    되므로, 그 거래가 평가되는 순간 그 손실은 아직 실현되지 않았다. 막으려면 같은 ms 안에서
    나중에 일어날 일을 미리 알아야 하고 그건 룩어헤드다(WAN-364가 6년치 표를 얼린 그 부류).

    그래서 `entry == 발동 시각`인 거래가 **하나 남고**, 검산 (b)는 그것을 위반으로 세지
    않는다(**엄격히 뒤(`>`)만** 센다). 같은 ms라도 **그 정산 뒤에** 평가되는 후보는 막힌다 —
    이 테스트가 그 경계를 양쪽으로 못 박는다.
    """
    t = DAY1 + 3 * _MINUTE
    cells = _cells(
        # 길이 0 손절(−1R) — 정렬상 먼저다(같은 시각이면 청산이 이른 쪽이 앞).
        ("BTCUSDT", [_loser(t, t)]),
        # 같은 ms · 정렬상 뒤 — 그때는 이미 그 손실이 **정산돼** 막힌다.
        ("ETHUSDT", [_winner(t, t + 5 * _MINUTE)]),
        # 한 스텝 뒤 — 당연히 막힌다.
        ("SOLUSDT", [_winner(t + _MINUTE, t + 2 * _MINUTE)]),
    )
    entries, out = _run(cells, daily_loss_limit_r=-1.0)
    # 🚨 **손실을 만든 그 거래 자신은 자기 손실로 막힐 수 없다** — 배치되는 순간 그 손익은
    # 아직 없다. 그래서 `entry == 발동 시각`인 거래가 남고, 검산 (b)가 그 한 칸을 위반으로
    # 세지 않는 이유가 이것이다.
    assert entries == [t], "자기 손실로 자신을 막았거나(룩어헤드) 뒤 후보를 못 막았다"
    assert out.stats.skipped_circuit_breaker == 2  # type: ignore[attr-defined]
    assert out.stats.circuit_breaker_first_trip == {"2024-03-05": t}  # type: ignore[attr-defined]


def test_stop_count_axis_counts_stops_not_losses() -> None:
    """대조군 B는 **손절 건수**를 센다 — 이긴 거래를 안 세므로 「버는 날」도 끈다."""
    # 크게 이긴 뒤 손절 하나 — 그날 누계는 여전히 **플러스**인데 B는 발동한다.
    cells = _cells(
        ("BTCUSDT", [_winner(DAY1, DAY1 + _MINUTE)]),
        ("ETHUSDT", [_loser(DAY1 + 2 * _MINUTE, DAY1 + 3 * _MINUTE)]),
        ("SOLUSDT", [_winner(DAY1 + 5 * _MINUTE, DAY1 + 6 * _MINUTE)]),
    )
    by_loss, _ = _run(cells, daily_loss_limit_r=-1.0)
    assert DAY1 + 5 * _MINUTE in by_loss, "누계가 플러스인데 A축이 발동했다"
    by_stops, _ = _run(cells, daily_stop_limit=1)
    assert DAY1 + 5 * _MINUTE not in by_stops, "손절 1건인데 B축이 발동 안 했다"


# --------------------------------------------------------------------------- #
# 4. 거부해야 할 조합
# --------------------------------------------------------------------------- #


def test_rejects_positive_loss_limit() -> None:
    """양수 한도는 **거부한다** — 조용히 돌면 「첫 거래부터 늘 발동」이 된다(WAN-112 부류)."""
    cells = _cells(("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]))
    with pytest.raises(ValueError, match="0 이하"):
        _run(cells, daily_loss_limit_r=10.0)


def test_rejects_null_schedule_together_with_threshold() -> None:
    """대조군 스케줄과 손실 문턱을 함께 주면 **거부한다** — 겹치면 대조군이 아니다."""
    cells = _cells(("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]))
    with pytest.raises(ValueError, match="대조군"):
        _run(cells, daily_loss_limit_r=-1.0, blocked_from_by_day={"2024-03-05": DAY1})


def test_rejects_unknown_scope() -> None:
    cells = _cells(("BTCUSDT", [_loser(DAY1, DAY1 + _MINUTE)]))
    with pytest.raises(ValueError, match="circuit_breaker_scope"):
        _run(cells, daily_loss_limit_r=-1.0, circuit_breaker_scope="new_entries")


def test_schedule_arm_blocks_without_reading_pnl() -> None:
    """대조군 팔은 **손익을 안 읽고** 시각만으로 막는다 — 그것이 대조군의 정의다."""
    cells = _cells(
        # 그날 누계가 계속 플러스인데도 스케줄이 막는다.
        ("BTCUSDT", [_winner(DAY1, DAY1 + _MINUTE)]),
        ("ETHUSDT", [_winner(DAY1 + 5 * _MINUTE, DAY1 + 6 * _MINUTE)]),
    )
    entries, out = _run(cells, blocked_from_by_day={"2024-03-05": DAY1 + 2 * _MINUTE})
    assert entries == [DAY1]
    assert out.stats.skipped_circuit_breaker == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# 5. §1-0 — (b) 두 규약의 대수 항등
# --------------------------------------------------------------------------- #


def _fact(entry: int, exit_: int, *, net_r: float = -1.0, symbol: str = "BTCUSDT") -> TradeFact:
    return TradeFact(
        symbol=symbol,
        timeframe="1h",
        entry_time=entry,
        exit_time=exit_,
        is_stop=net_r < 0,
        net_r=net_r,
        is_reentry=False,
    )


def test_open_cells_conventions_differ_exactly_by_zero_duration_trades() -> None:
    """🚨 (b) 두 규약의 차 = `#{진입 == 청산 == t}` — §1-0이 못 박는 그 항등이다.

    같은 ms에 열고 닫는 거래(「같은 분 익절」 부류)는 `wan408` 식에서 **자기 자신을 열린
    칸에서 뺀다**. 그 성질을 값으로 고정한다.
    """
    t = DAY1 + 10 * _MINUTE
    facts = [
        _fact(DAY1, DAY1 + 30 * _MINUTE),  # t에 열려 있다
        _fact(t, t),  # 길이 0 — 바로 그 순간
        _fact(t, t + _MINUTE),  # t에 진입(자기 자신은 안 센다)
    ]
    published = open_cells_before(facts, convention="wan408")
    interval = open_cells_before(facts, convention="interval")
    # 길이 0 거래 자신과, 같은 ms에 진입한 셋째 거래가 각각 1씩 깎여 있었다.
    assert published[1] == 0 and interval[1] == 1
    assert published[2] == 0 and interval[2] == 1
    assert published[0] == interval[0] == 0
    with pytest.raises(ValueError, match="모르는 규약"):
        open_cells_before(facts, convention="whatever")


def test_realized_conventions_differ_at_the_same_instant() -> None:
    """(c) `strict`는 같은 ms 청산을 **안 세고** `settled`는 **센다**."""
    t = DAY1 + 5 * _MINUTE
    facts = [_fact(DAY1, t, net_r=-2.0), _fact(t, t + _MINUTE, net_r=1.0)]
    assert realized_r_before(facts, convention="strict")[1] == pytest.approx(0.0)
    assert realized_r_before(facts, convention="settled")[1] == pytest.approx(-2.0)


def test_definition_rows_bucket_every_trade() -> None:
    """🚨 버킷 합 == 전체 거래 수 — 이슈가 지적한 「1,597건이 샜다」를 막는 열이다."""
    facts = [_fact(DAY1 + i * _MINUTE, DAY1 + (i + 2) * _MINUTE) for i in range(10)]
    rows = definition_rows(facts, segment="oos_warm")
    assert rows
    by_convention: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.indicator, row.convention)
        by_convention[key] = by_convention.get(key, 0) + row.num_trades
    assert set(by_convention.values()) == {len(facts)}
    assert {r.total_trades for r in rows} == {len(facts)}


# --------------------------------------------------------------------------- #
# 6. 널이 항등으로 퇴화하면 죽는다
# --------------------------------------------------------------------------- #


def test_shuffled_schedule_actually_moves_days() -> None:
    """🚨 재배정이 **실제로 날짜를 옮긴다** — 항등이면 이 널은 아무것도 안 한 것이다.

    WAN-408 §0-2가 「종목 안 순열은 통계량을 안 바꾼다」를 몰라 30판을 헛돌린 선례를
    동작으로 막는다.
    """
    days = [f"2024-03-{d:02d}" for d in range(1, 29)]
    schedule = {"2024-03-05": day_start_ms("2024-03-05") + 3 * 3_600_000}
    rng = random.Random(410)
    moved = 0
    for _ in range(20):
        shuffled = shuffled_schedule(schedule, days, rng)
        assert len(shuffled) == len(schedule), "날짜 수가 안 맞으면 대조군이 아니다"
        offsets = {v - day_start_ms(k) for k, v in shuffled.items()}
        assert offsets == {3 * 3_600_000}, "하루 중 시각 분포가 보존되지 않았다"
        moved += 1 if set(shuffled) != set(schedule) else 0
    assert moved >= 15, "재배정이 사실상 항등이다 — 이 널은 대조군이 아니다"


def test_null_pvalue_is_one_sided_rank() -> None:
    """단측 순위 p — 실제가 전부를 이기면 하한 `1/(판+1)`, 지면 1에 가깝다(WAN-142 자)."""
    from backtest.wan410_daily_loss_circuit_breaker import NullRow

    def row(seed: int, value: float) -> NullRow:
        return NullRow(
            segment="oos_warm",
            threshold=-3.0,
            scope="both",
            seed=seed,
            is_actual=seed < 0,
            num_trades=10,
            mean_net_r=value,
            sum_net_r=value * 10,
            win_rate=0.5,
            max_drawdown=0.2,
            blocked_candidates=5,
            trip_days=3,
        )

    best = [row(-1, 0.5)] + [row(i, 0.1) for i in range(19)]
    p, count = null_pvalue(best, threshold=-3.0, scope="both")
    assert count == 19
    assert p == pytest.approx(1 / 20)
    worst = [row(-1, 0.0)] + [row(i, 0.1) for i in range(19)]
    p_worst, _ = null_pvalue(worst, threshold=-3.0, scope="both")
    assert p_worst == pytest.approx(1.0)
    assert null_pvalue([], threshold=-3.0, scope="both")[1] == 0


# --------------------------------------------------------------------------- #
# 7. 표시 규약
# --------------------------------------------------------------------------- #


def test_wallet_defined_matches_wan386_predicate() -> None:
    """지갑 층 열이 뜻을 잃으면 **비율을 내지 않는다**(WAN-386/388과 같은 술어)."""
    assert wallet_defined(-0.5, 0.4)
    assert not wallet_defined(-1.0, 0.4), "자본이 0을 뚫었는데 정의됐다고 본다"
    assert not wallet_defined(-0.5, 1.0), "MDD 100%인데 정의됐다고 본다"


def test_arm_scope_predicate_matches_engine() -> None:
    """팔의 범위 술어가 엔진 판정과 **같은 말**이어야 검산 (b)가 뜻을 갖는다."""
    entry_only = Arm(name="x", label="x", kind="loss", threshold=-1.0, scope="entry")
    assert entry_only.in_scope(is_reentry=False)
    assert not entry_only.in_scope(is_reentry=True)
    both = Arm(name="y", label="y", kind="loss", threshold=-1.0, scope="both")
    assert both.in_scope(is_reentry=True) and both.in_scope(is_reentry=False)
    assert Arm(name="b", label="b", kind="base").kwargs() == {}
