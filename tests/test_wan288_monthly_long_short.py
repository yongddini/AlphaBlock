"""WAN-288 월별 롱/숏 수익률의 자(尺)를 동작으로 고정한다.

지키는 것:

1. **월 접기·방향 분리** — 거래가 청산 달로 접히고, 방향(롱·숏)이 갈린다.
2. **롱-온리 필터** — 롱+숏 셀에서 롱만 남기고 재진입을 비운다(= WAN-180 경로 재구성).
3. **경계 필터** — oos_warm 방향 후보가 따뜻한 경계 이후만 뽑힌다.
4. **분석 함수** — 숏 델타·장세 분해·상충 건수가 행에서 올바로 계산된다.
5. **CSV 왕복·append 병합** — 값이 보존되고 키로 덮어쓴다.
6. **요약 렌더** — 두-팔 표·세 줄·상충이 나온다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backtest import harness
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload
from backtest.wan288_monthly_long_short import (
    ARM_LONG_ONLY,
    ARM_LONG_SHORT,
    BookMonthlyRow,
    DirMonthlyRow,
    _dir_monthly_rows,
    _direction_candidates,
    _filter_long_only,
    _month_of,
    book_from_csv,
    book_to_frame,
    build_summary_markdown,
    conflict_months,
    dir_from_csv,
    dir_to_frame,
    merge_book,
    merge_dir,
    regime_split,
    short_delta_by_month,
    short_profit_by_month,
)
from backtest.zone_limit_backtest import _Candidate


def _ms(year: int, month: int, day: int = 10) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _cand(
    side: PositionSide,
    *,
    entry_time: int,
    exit_time: int,
    entry_price: float,
    exit_price: float,
    stop_price: float,
    reason: ExitReason = ExitReason.TAKE_PROFIT,
    trigger_time: int | None = None,
) -> _Candidate:
    return _Candidate(
        side=side,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        reason=reason,
        stop_price=stop_price,
        trigger_time=entry_time if trigger_time is None else trigger_time,
    )


def _cell(base: list[_Candidate], *, boundary: int = 0, timeframe: str = "1h") -> CellPayload:
    return CellPayload(
        symbol="BTC/USDT",
        timeframe=timeframe,
        boundary_ms=boundary,
        candidates={"full": tuple(base), "is": (), "oos": ()},
        funding={"full": (), "is": (), "oos": ()},
        rows=(),
        reentry_candidates={"full": tuple(base), "is": (), "oos": ()},
    )


# --------------------------------------------------------------------------- #
# 1. 월 접기 · 방향 분리
# --------------------------------------------------------------------------- #


def test_month_of_formats_utc_calendar_month() -> None:
    assert _month_of(_ms(2024, 8, 1)) == "2024-08"
    assert _month_of(_ms(2026, 12, 31)) == "2026-12"


def test_dir_monthly_folds_by_exit_month_and_side() -> None:
    aug, sep = _ms(2024, 8), _ms(2024, 9)
    longs = [
        _cand(
            PositionSide.LONG,
            entry_time=aug - 3_600_000,
            exit_time=aug,
            entry_price=100.0,
            exit_price=110.0,
            stop_price=95.0,
        ),
        _cand(
            PositionSide.LONG,
            entry_time=sep - 3_600_000,
            exit_time=sep,
            entry_price=100.0,
            exit_price=90.0,
            stop_price=95.0,
            reason=ExitReason.STOP_LOSS,
        ),
    ]
    cell = _cell(longs)
    rows = _dir_monthly_rows(cell, PositionSide.LONG, {"2024-08": 0.2, "2024-09": -0.1})
    by_month = {r.month: r for r in rows}
    assert set(by_month) == {"2024-08", "2024-09"}
    assert all(r.direction == "long" for r in rows)
    assert by_month["2024-08"].num_trades == 1
    assert by_month["2024-08"].net_pp_sum > 0  # 익절
    assert by_month["2024-09"].net_pp_sum < 0  # 손절
    assert by_month["2024-08"].symbol_buy_hold == 0.2  # 장세 라벨 조인


def test_dir_monthly_short_direction_labeled_and_gap_flag() -> None:
    aug = _ms(2024, 8)
    short = _cand(
        PositionSide.SHORT,
        entry_time=aug - 3_600_000,
        exit_time=aug,
        entry_price=100.0,
        exit_price=90.0,  # 숏 익절(가격 하락)
        stop_price=105.0,
    )
    doge = CellPayload(
        symbol=harness.normalize_symbol("DOGEUSDT"),
        timeframe="1h",
        boundary_ms=0,
        candidates={"full": (short,), "is": (), "oos": ()},
        funding={"full": (), "is": (), "oos": ()},
        rows=(),
        reentry_candidates={},
    )
    rows = _dir_monthly_rows(doge, PositionSide.SHORT, {})
    assert rows[0].direction == "short"
    assert rows[0].funding_gap is True  # DOGE = 펀딩 공백
    assert rows[0].net_pp_sum > 0


# --------------------------------------------------------------------------- #
# 2. 롱-온리 필터 · 3. 경계 필터
# --------------------------------------------------------------------------- #


def test_filter_long_only_keeps_longs_and_empties_reentry() -> None:
    base = [
        _cand(
            PositionSide.LONG,
            entry_time=10,
            exit_time=20,
            entry_price=100.0,
            exit_price=110.0,
            stop_price=95.0,
        ),
        _cand(
            PositionSide.SHORT,
            entry_time=30,
            exit_time=40,
            entry_price=100.0,
            exit_price=90.0,
            stop_price=105.0,
        ),
    ]
    cell = _cell(base)
    lo = _filter_long_only(cell)
    assert all(c.side is PositionSide.LONG for c in lo.candidates["full"])
    assert len(lo.candidates["full"]) == 1
    assert lo.reentry_candidates == {}  # 재진입 비움(WAN-180 북 구성)
    assert lo.funding == cell.funding
    assert lo.boundary_ms == cell.boundary_ms


def test_direction_candidates_oos_warm_boundary_and_side() -> None:
    base = [
        _cand(
            PositionSide.LONG,
            entry_time=5,
            exit_time=6,
            entry_price=100.0,
            exit_price=101.0,
            stop_price=95.0,
            trigger_time=5,
        ),
        _cand(
            PositionSide.LONG,
            entry_time=50,
            exit_time=51,
            entry_price=100.0,
            exit_price=101.0,
            stop_price=95.0,
            trigger_time=50,
        ),
        _cand(
            PositionSide.SHORT,
            entry_time=60,
            exit_time=61,
            entry_price=100.0,
            exit_price=99.0,
            stop_price=105.0,
            trigger_time=60,
        ),
    ]
    cell = _cell(base, boundary=25)
    longs = _direction_candidates(cell, PositionSide.LONG)
    assert [c.trigger_time for c in longs] == [50]  # 경계 이전(5)·숏 제외
    shorts = _direction_candidates(cell, PositionSide.SHORT)
    assert [c.trigger_time for c in shorts] == [60]


# --------------------------------------------------------------------------- #
# 4. 분석 함수
# --------------------------------------------------------------------------- #


def _book(scope: str, arm: str, month: str, ret: float, btc: float) -> BookMonthlyRow:
    return BookMonthlyRow(
        scope=scope,
        arm=arm,
        month=month,
        monthly_return=ret,
        equity_end=1.0,
        num_exits=1,
        btc_buy_hold=btc,
    )


def test_short_delta_and_regime_split() -> None:
    rows = [
        _book("all", ARM_LONG_ONLY, "2024-08", 0.05, 0.10),
        _book("all", ARM_LONG_SHORT, "2024-08", 0.03, 0.10),  # 상승 달: 숏이 −0.02 까먹음
        _book("all", ARM_LONG_ONLY, "2024-09", -0.04, -0.08),
        _book("all", ARM_LONG_SHORT, "2024-09", 0.01, -0.08),  # 하락 달: 숏이 +0.05 벌어줌
    ]
    delta = short_delta_by_month(rows, "all")
    assert delta["2024-08"] == 0.03 - 0.05
    assert delta["2024-09"] == 0.01 - (-0.04)
    down, up, net = regime_split(rows, "all")
    assert abs(down - 0.05) < 1e-12  # 하락 달 숏 기여
    assert abs(up - (-0.02)) < 1e-12  # 상승 달 숏 기여(까먹음)
    assert abs(net - 0.03) < 1e-12


def _dir(symbol: str, tf: str, direction: str, month: str, pp: float, trades: int) -> DirMonthlyRow:
    return DirMonthlyRow(
        symbol=harness.normalize_symbol(symbol),
        timeframe=tf,
        direction=direction,
        month=month,
        net_pp_sum=pp,
        num_trades=trades,
        symbol_buy_hold=0.0,
        funding_gap=False,
    )


def test_short_profit_by_month_sums_only_shorts() -> None:
    rows = [
        _dir("BTCUSDT", "1h", "short", "2024-09", 3.0, 2),
        _dir("ETHUSDT", "4h", "short", "2024-09", 1.5, 1),
        _dir("BTCUSDT", "1h", "long", "2024-09", 9.9, 5),  # 롱은 무시
    ]
    prof = short_profit_by_month(rows)
    assert prof == {"2024-09": 4.5}


def test_conflict_months_counts_same_symbol_both_directions() -> None:
    rows = [
        _dir("BTCUSDT", "1h", "long", "2024-09", 2.0, 3),
        _dir("BTCUSDT", "4h", "short", "2024-09", 1.0, 2),  # 다른 TF, 같은 종목·달 → 상충
        _dir("ETHUSDT", "1h", "long", "2024-09", 2.0, 1),  # 롱만 → 상충 아님
        _dir("SOLUSDT", "1h", "short", "2024-08", 1.0, 0),  # 거래 0 → 제외
        _dir("SOLUSDT", "1h", "long", "2024-08", 1.0, 1),
    ]
    conflicts = conflict_months(rows)
    assert conflicts == [("BTC", "2024-09")]


# --------------------------------------------------------------------------- #
# 5. CSV 왕복 · append 병합
# --------------------------------------------------------------------------- #


def test_dir_roundtrip(tmp_path: Path) -> None:
    rows = [_dir("BTCUSDT", "1h", "short", "2024-09", 3.0, 2)]
    path = tmp_path / "dir.csv"
    dir_to_frame(rows).to_csv(path, index=False)
    back = dir_from_csv(path)
    assert back == rows


def test_book_roundtrip(tmp_path: Path) -> None:
    rows = [_book("all", ARM_LONG_SHORT, "2024-09", 0.01, -0.08)]
    path = tmp_path / "book.csv"
    book_to_frame(rows).to_csv(path, index=False)
    back = book_from_csv(path)
    assert back == rows


def test_merge_dir_overwrites_by_key() -> None:
    existing = [_dir("BTCUSDT", "1h", "short", "2024-09", 3.0, 2)]
    new = [
        _dir("BTCUSDT", "1h", "short", "2024-09", 5.0, 4),  # 덮어씀
        _dir("BTCUSDT", "15m", "long", "2024-09", 1.0, 1),  # 새 TF
    ]
    merged = merge_dir(existing, new)
    by_key = {(r.symbol, r.timeframe, r.direction): r for r in merged}
    assert len(merged) == 2
    assert by_key[(harness.normalize_symbol("BTCUSDT"), "1h", "short")].net_pp_sum == 5.0


def test_merge_book_overwrites_by_key() -> None:
    existing = [_book("all", ARM_LONG_ONLY, "2024-09", -0.04, -0.08)]
    new = [_book("all", ARM_LONG_ONLY, "2024-09", 0.02, -0.08)]
    merged = merge_book(existing, new)
    assert len(merged) == 1
    assert merged[0].monthly_return == 0.02


# --------------------------------------------------------------------------- #
# 6. 요약 렌더
# --------------------------------------------------------------------------- #


def test_committed_long_only_1h_matches_wan180_except_new_symbol_funding() -> None:
    """완료기준 2 — 커밋된 롱-온리 1h 북이 `wan180_monthly_returns.csv`와 **거래는 비트 일치**하되
    신규 3종목 실 펀딩만큼 월수익률이 미세하게 다르다(WAN-291 대리 해제의 증거).

    두 CSV를 읽어 대조한다(엔진 재실행 없음). 롱-온리 팔은 `short_enabled=False`(= WAN-180
    경로)와 같은 거래 집합을 봐야 하므로 (scope=1h, cap_only, 5.0, oos_warm) 셀이 겹친다.
    WAN-180은 신규 3종목(DOGE·LINK·LTC)에 **BTC 대리 펀딩**을 씌웠고, WAN-292 백필 후
    WAN-291은 **실 펀딩**을 쓴다 — 그래서 `num_exits`(진입·청산 구조)는 비트 일치(펀딩은
    거래를 더하거나 빼지 않는다)하지만, `monthly_return`·`equity_end`는 신규 3종목 롱의 실
    펀딩 비용만큼(작게) 갈린다. 다른 북 구성으로 재생성하면(예: band 재진입) 거래 수부터
    깨지므로 조용한 회귀를 여전히 잡는다.
    """
    import pandas as pd

    reports = Path("backtest/reports")
    w180_path = reports / "wan180_monthly_returns.csv"
    w288_path = reports / "wan288_monthly_book.csv"
    if not (w180_path.exists() and w288_path.exists()):
        import pytest

        pytest.skip("리포트 CSV가 없음(측정 미실행 환경)")
    w180 = pd.read_csv(w180_path)
    w288 = pd.read_csv(w288_path)
    ref = (
        w180[
            (w180.scope == "1h")
            & (w180.leverage_mode == "cap_only")
            & (w180.multiple == 5.0)
            & (w180.segment == "oos_warm")
        ][["month", "monthly_return", "equity_end", "num_exits"]]
        .sort_values("month")
        .reset_index(drop=True)
    )
    mine = (
        w288[(w288.scope == "1h") & (w288.arm == ARM_LONG_ONLY)][
            ["month", "monthly_return", "equity_end", "num_exits"]
        ]
        .sort_values("month")
        .reset_index(drop=True)
    )
    assert len(ref) == len(mine) > 0
    merged = ref.merge(mine, on="month", suffixes=("_180", "_288"))
    assert len(merged) == len(ref)
    # 거래 구조는 비트 일치 — 펀딩은 진입·청산을 더하거나 빼지 않는다.
    assert (merged.num_exits_180 - merged.num_exits_288).abs().max() == 0
    # 월수익률은 신규 3종목 실 펀딩만큼 달라지되(대리 해제의 증거) 그 크기는 펀딩 규모로 작다.
    return_diff = (merged.monthly_return_180 - merged.monthly_return_288).abs()
    equity_rel_diff = (
        merged.equity_end_180 - merged.equity_end_288
    ).abs() / merged.equity_end_180.abs()
    assert (return_diff > 1e-9).any(), "실 펀딩 재산출인데 wan180과 완전히 같다 — 대리가 안 꺼졌다"
    assert return_diff.max() < 1e-2, "차이가 펀딩 규모를 넘는다 — 거래/사이징이 바뀌었을 수 있다"
    assert equity_rel_diff.max() < 1e-2, "equity 상대차가 펀딩 규모를 넘는다"


def test_summary_renders_key_sections() -> None:
    book_rows = [
        _book("all", ARM_LONG_ONLY, "2024-09", -0.04, -0.08),
        _book("all", ARM_LONG_SHORT, "2024-09", 0.01, -0.08),
    ]
    dir_rows = [
        _dir("BTCUSDT", "1h", "long", "2024-09", 2.0, 3),
        _dir("BTCUSDT", "4h", "short", "2024-09", 1.0, 2),
    ]
    md = build_summary_markdown(dir_rows, book_rows, funding_note="대리: BTC")
    assert "롱-온리 북 vs 롱+숏 북" in md
    assert "핵심 세 줄" in md
    assert "상충" in md
    assert "복리 착시" in md
    assert "2024-09" in md
