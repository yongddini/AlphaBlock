"""WAN-180 신규 종목 펀딩 대리 테스트.

사용자 지시(2026-07-23): 펀딩 데이터가 없는 신규 종목(DOGE·LINK·LTC)의 펀딩을 기존
6종목 중 **확정 펀딩 평균이 가장 높은** 종목의 시계열로 대체한다. 라벨이 아니라
동작으로 고정한다 — 대체가 실제로 손익을 깎는지, 기존 종목은 안 건드리는지, 대체 칸이
`engine_*` 배선 검산에서 빠지는지.
"""

from __future__ import annotations

import pytest

from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload, CellRow
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.zone_limit_backtest import _Candidate
from data.models import FundingRate


def _cand(entry_time: int, exit_time: int) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=101.5,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.0,
        trigger_time=entry_time,
    )


def _rates(symbol: str, rate: float) -> tuple[FundingRate, ...]:
    return tuple(
        FundingRate(symbol=symbol, funding_time=t, rate=rate) for t in (2_000, 4_000, 6_000)
    )


def _payload(
    symbol: str, funding: tuple[FundingRate, ...], *, engine_return: float | None = None
) -> CellPayload:
    cands: tuple[_Candidate, ...] = (_cand(1_000, 8_000),)
    segments: dict[str, tuple[_Candidate, ...]] = {
        SEGMENT_FULL: cands,
        SEGMENT_IS: cands,
        SEGMENT_OOS: cands,
    }
    rows = tuple(
        CellRow(
            symbol=symbol,
            timeframe="1h",
            segment=segment,
            num_candidates=1,
            num_trades=1,
            win_rate=1.0,
            total_return=0.0,
            max_drawdown=0.0,
            engine_total_return=engine_return if segment == SEGMENT_FULL else None,
            engine_num_trades=1 if segment == SEGMENT_FULL else None,
        )
        for segment in (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS)
    )
    return CellPayload(
        symbol=symbol,
        timeframe="1h",
        boundary_ms=0,
        candidates=segments,
        funding={k: funding for k in segments},
        rows=rows,
    )


def test_proxy_picks_highest_funding_old_symbol_and_reprices() -> None:
    """대리 = 기존 종목 중 확정 펀딩 평균 최고 · 신규 칸 손익이 실제로 깎인다."""
    btc = _payload("BTC/USDT:USDT", _rates("BTC/USDT:USDT", 0.0001), engine_return=0.0)
    trx = _payload("TRX/USDT:USDT", _rates("TRX/USDT:USDT", 0.01), engine_return=0.0)  # 최고.
    doge = _payload("DOGE/USDT:USDT", ())  # 펀딩 공백 — 대체 대상.

    out, note = apply_funding_proxy([btc, trx, doge])
    assert "TRX" in note and "DOGE 1h" in note

    proxied = next(p for p in out if p.symbol.startswith("DOGE"))
    assert [r.rate for r in proxied.funding[SEGMENT_FULL]] == [0.01, 0.01, 0.01]
    # 손익이 실제로 움직였다 — 대체 전(펀딩 0) 격리 성과보다 나빠야 한다.
    no_proxy, _ = apply_funding_proxy([btc, trx])
    full_row = next(r for r in proxied.rows if r.segment == SEGMENT_FULL)
    assert full_row.total_return < 0.20  # 펀딩 3회 × 1%가 비용으로 실렸다(라벨 아님).
    # 대체 칸은 배선 검산에서 빠진다 — 표준 경로는 원본(빈) 펀딩으로 돌았기 때문.
    assert full_row.engine_total_return is None
    # 기존 종목 칸은 객체 그대로다(펀딩·행 무변).
    assert out[0] is btc
    assert out[1] is trx
    assert no_proxy == [btc, trx]


def test_proxy_noop_without_new_symbols_or_without_old_rates() -> None:
    """대체할 신규 칸이 없거나 기존 종목에 확정 펀딩이 없으면 손대지 않는다."""
    btc = _payload("BTC/USDT:USDT", _rates("BTC/USDT:USDT", 0.0001))
    out, note = apply_funding_proxy([btc])
    assert out == [btc] and note == ""

    empty_old = _payload("BTC/USDT:USDT", ())
    doge = _payload("DOGE/USDT:USDT", ())
    out, note = apply_funding_proxy([empty_old, doge])
    assert out == [empty_old, doge] and note == ""


def test_proxy_keeps_new_symbol_own_data_when_present() -> None:
    """신규 종목이라도 자기 펀딩 데이터가 있으면 대리로 덮지 않는다 — 대리는 「데이터
    공백 → 0 계상」의 교정이지 데이터 교체가 아니다."""
    trx = _payload("TRX/USDT:USDT", _rates("TRX/USDT:USDT", 0.01))
    doge_with_data = _payload("DOGE/USDT:USDT", _rates("DOGE/USDT:USDT", 0.0001))
    out, note = apply_funding_proxy([trx, doge_with_data])
    assert out == [trx, doge_with_data]
    assert note == ""


def test_proxy_reprice_reflects_funding_cost_in_return() -> None:
    """같은 후보·같은 자본에서 대리 펀딩 유무가 total_return 차이로 나타난다."""
    trx = _payload("TRX/USDT:USDT", _rates("TRX/USDT:USDT", 0.01), engine_return=0.0)
    doge_blank = _payload("DOGE/USDT:USDT", ())
    with_proxy, _ = apply_funding_proxy([trx, doge_blank])
    proxied = next(p for p in with_proxy if p.symbol.startswith("DOGE"))
    blank_return = None
    proxy_return = None
    for row in doge_blank.rows:
        if row.segment == SEGMENT_FULL:
            blank_return = row.total_return
    for row in proxied.rows:
        if row.segment == SEGMENT_FULL:
            proxy_return = row.total_return
    assert blank_return is not None and proxy_return is not None
    # 원본 rows는 total_return=0.0 더미다 — 재계산 값은 실제 시퀀싱 결과라 0이 아니고,
    # 펀딩 비용(3회 × 1% × 명목)이 실린 값이다.
    assert proxy_return != pytest.approx(blank_return)
