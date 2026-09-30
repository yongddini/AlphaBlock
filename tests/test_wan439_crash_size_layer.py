"""WAN-439 — 폭락 국면 크기 층: 인과 검산 · 북 배율 훅 비트 동일 · 층이 북 배치를 실제로 바꾸는지.

라벨이 아니라 동작으로 건다: (1) 폭락 국면 판정은 진입 분 **이후**를 바꿔도 불변이고 직전 분을
바꾸면 변한다, (2) `risk_scale`을 안 주거나 1을 주면 북 산출이 비트 동일하다, (3) 0.5를 주면
그 진입만 수량이 절반이고 `net R`은 그대로다, (4) 층을 북에 넣으면 명목 자리가 비어 **다른 칸의
배치가 실제로 바뀐다**(복리 층에서만 바꾼 판은 못 보는 채널 — WAN-341).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan438_spot_stress as m438
from backtest import wan439_crash_size_layer as m
from backtest.book_cli import net_r
from backtest.leverage_book import LEGACY_BOOK_PARAMS, BookCell, run_leverage_book
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload
from backtest.zone_limit_backtest import _Candidate
from execution.sizing import PositionSizingParams

MIN = 60_000


# --- 1. 착수 전 고정 상수 ------------------------------------------------------------------


def test_constants_are_the_issue_spec() -> None:
    assert m.CRASH_THRESHOLD == -0.05
    assert m.CRASH_MULT == 0.5
    assert m.CAPIT_VOL_RATIO == 8.0
    assert m.CRASH_LOOKBACK_MIN == 24 * 60
    assert m.MDD_TARGET == 0.35
    assert m.FIT_FILL == "bar_low"
    # 토대는 WAN-438 페이퍼 좌표를 **그대로 읽는다**(두 벌 금지).
    assert m.RULES is m438.GATED
    assert m.RULES.btc_gate == -0.025 and m.RULES.stop_multiple == 2.0
    assert m.RULES.skip_early and m.RULES.budget == 10.0


# --- 2. 폭락 국면 판정 — 인과 ---------------------------------------------------------------


def _series(n: int = 3000) -> tuple[np.ndarray, np.ndarray]:
    t = np.arange(n, dtype=np.int64) * MIN
    c = 100.0 * np.exp(np.cumsum(np.random.default_rng(439).normal(0, 0.002, n)))
    return t, c


def test_btc_24h_ignores_the_entry_minute_and_after() -> None:
    t, c = _series()
    entry = int(t[2000])
    base = m.btc_lookback_return(t, c, entry)
    assert base == pytest.approx(c[1999] / c[1999 - 1440] - 1.0)
    future = c.copy()
    future[2000:] *= 0.5  # 진입 분부터 뒤는 전부 폭락 — 진입 순간엔 모른다
    assert m.btc_lookback_return(t, future, entry) == base
    past = c.copy()
    past[1999] *= 0.9  # 직전 확정 분은 안다
    assert m.btc_lookback_return(t, past, entry) != base


def test_btc_24h_is_nan_without_history() -> None:
    t, c = _series(100)
    assert math.isnan(m.btc_lookback_return(t, c, int(t[0])))
    assert math.isnan(m.btc_lookback_return(t, c, int(t[50])))  # 24h 전 값이 없다


def test_volume_ratio_ignores_the_entry_minute_and_after() -> None:
    n = 12_000
    t = np.arange(n, dtype=np.int64) * MIN
    q = np.ones(n)
    q[10_940:11_000] = 20.0  # 직전 1h 투매
    cum = np.concatenate([[0.0], np.cumsum(q)])
    entry = int(t[11_000])
    base = m.btc_volume_ratio(t, cum, entry)
    assert base > m.CAPIT_VOL_RATIO
    q2 = q.copy()
    q2[11_000:] = 1e9
    cum2 = np.concatenate([[0.0], np.cumsum(q2)])
    assert m.btc_volume_ratio(t, cum2, entry) == base


def test_layer_multiplier() -> None:
    assert m.layer_multiplier(m.ARM_A, -0.5, 100.0) == 1.0
    assert m.layer_multiplier(m.ARM_B, -0.05, math.nan) == 0.5  # 경계 포함
    assert m.layer_multiplier(m.ARM_B, -0.0499, math.nan) == 1.0
    assert m.layer_multiplier(m.ARM_B, math.nan, math.nan) == 1.0  # 판정 불가 ≠ 폭락
    assert m.layer_multiplier(m.ARM_B, -0.2, 50.0) == 0.5  # B는 투매를 안 본다
    assert m.layer_multiplier(m.ARM_C, -0.2, 8.0) == 1.0  # C만 예외
    assert m.layer_multiplier(m.ARM_C, -0.2, 7.9) == 0.5
    assert m.layer_multiplier(m.ARM_C, -0.2, math.nan) == 0.5
    with pytest.raises(ValueError):
        m.layer_multiplier("Z", -0.2, 1.0)


# --- 3. 북 배율 훅 ---------------------------------------------------------------------------


def _cand(t0: int, stop: float = 99.0) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=t0,
        entry_price=100.0,
        exit_time=t0 + 5 * MIN,
        exit_price=101.5,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=stop,
        trigger_time=t0,
    )


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=10_000.0,
        risk_sizing=PositionSizingParams(
            risk_per_trade=0.01, leverage=1.0, min_stop_distance_fraction=0.0
        ),
    )


def _cells() -> list[BookCell]:
    return [
        BookCell(symbol="BTC/USDT:USDT", timeframe="1h", candidates=[_cand(0), _cand(10 * MIN)]),
        BookCell(symbol="ETH/USDT:USDT", timeframe="1h", candidates=[_cand(MIN)]),
    ]


def test_risk_scale_none_or_one_is_bit_identical() -> None:
    plain = run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS)
    ones = run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS, risk_scale=lambda c, x: 1.0)
    assert ones.trades == plain.trades
    assert ones.stats.placed_records == plain.stats.placed_records


def test_risk_scale_halves_that_entry_and_keeps_net_r() -> None:
    plain = run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS)

    def scale(cell: tuple[str, str], cand: _Candidate) -> float:
        return 0.5 if cell[0] == "BTC/USDT:USDT" and cand.entry_time == 10 * MIN else 1.0

    half = run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS, risk_scale=scale)
    pairs = list(zip(plain.trades, half.trades, strict=True))
    changed = [(a, b) for a, b in pairs if a.quantity != b.quantity]
    assert len(changed) == 1
    a, b = changed[0]
    assert b.quantity == pytest.approx(a.quantity * 0.5)
    ra = [p for p in plain.stats.placed_records if p.realized_pnl == a.realized_pnl][0]
    rb = [p for p in half.stats.placed_records if p.realized_pnl == b.realized_pnl][0]
    assert net_r(b, rb) == pytest.approx(net_r(a, ra))


def test_risk_scale_rejects_non_positive() -> None:
    for bad in (0.0, -1.0, math.inf):

        def scale(cell: tuple[str, str], cand: _Candidate, bad: float = bad) -> float:
            return bad

        with pytest.raises(ValueError, match="risk_scale"):
            run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS, risk_scale=scale)


# --- 4. 층을 북에 넣으면 다른 칸의 자리가 바뀐다 -------------------------------------------


def _entry(symbol: str, t0: int) -> w.ArmEntry:
    cand = _Candidate(
        side=PositionSide.LONG,
        trigger_time=t0,
        entry_time=t0,
        entry_price=100.0,
        stop_price=99.0,  # 손절 1% · 리스크 1% → 자연 명목 = 자본 × 1(거래당 천장)
        exit_time=t0,
        exit_price=100.0,
        reason=ExitReason.END_OF_DATA,
    )
    times = t0 + np.arange(4, dtype=np.int64) * MIN
    return w.ArmEntry(symbol, "1h", cand, 0.0, times, np.full(4, 99.9), np.full(4, 100.0))


def _payload(symbol: str) -> CellPayload:
    empty: dict[str, tuple[()]] = {s: () for s in (harness.SEGMENT_FULL, "is", "oos")}
    return CellPayload(symbol, "1h", 1 << 60, dict(empty), dict(empty), ())


def test_layer_in_book_frees_notional_for_another_cell() -> None:
    """채택 북은 cap_only 5배 — 명목 1배짜리 여섯 칸이 같은 분에 오면 여섯째가 밀린다.

    첫 칸에 층 0.5를 **북에도** 넣으면 명목이 반만 차 여섯째가 들어온다. 복리 층에서만 넣으면
    배치는 층이 없는 판과 같다(탐색이 못 본 채널).
    """
    symbols = [f"S{i}/USDT:USDT" for i in range(6)]
    entries = [_entry(s, i) for i, s in enumerate(symbols)]  # 1ms씩 어긋나 순서 고정
    payloads = [_payload(s) for s in symbols]
    layer = [0.5] + [1.0] * 5
    counts = [0] * 6
    none = w.place(payloads, entries, w.OFF, counts=counts)
    sim_only = w.place(
        payloads, entries, w.OFF, counts=counts, size_layer=layer, layer_in_book=False
    )
    in_book = w.place(payloads, entries, w.OFF, counts=counts, size_layer=layer)
    assert len(none) == len(sim_only) == 5
    assert len(in_book) == 6
    assert [t.size for t in sim_only][0] == 0.5  # 복리 층의 크기는 둘 다 반영된다
    assert [t.size for t in in_book][0] == 0.5
    assert [t.net_r for t in none] == [t.net_r for t in sim_only]


def test_size_layer_default_is_bit_identical() -> None:
    symbols = [f"S{i}/USDT:USDT" for i in range(3)]
    entries = [_entry(s, i) for i, s in enumerate(symbols)]
    payloads = [_payload(s) for s in symbols]
    a = w.place(payloads, entries, w.OFF, counts=[0] * 3)
    b = w.place(payloads, entries, w.OFF, counts=[0] * 3, size_layer=[1.0] * 3)
    assert [(t.entry_time, t.net_r, t.size) for t in a] == [
        (t.entry_time, t.net_r, t.size) for t in b
    ]
    with pytest.raises(ValueError, match="size_layer"):
        w.place(payloads, entries, w.OFF, counts=[0] * 3, size_layer=[1.0])


# --- 5. 8.6년 이어 붙이기 · 크기 맞춤 · 판정 -------------------------------------------------


def _trade(e: int, x: int, r: float, lows: list[float]) -> w.PlacedTrade:
    times = np.arange(e, e + len(lows) * MIN, MIN, dtype=np.int64)
    arr = np.array(lows)
    return w.PlacedTrade("X", "1h", e, x, r, r < 0, False, 1.0, 100.0, 90.0, times, arr, arr)


def test_chain_scales_the_second_leg_by_the_first() -> None:
    spot = [_trade(0, 4 * MIN, -1.0, [100.0] * 4)]
    fut = [_trade(0, 4 * MIN, 2.0, [100.0] * 4)]
    total, dd = m.chain(spot, fut, 0.1)
    assert total == pytest.approx(0.9 * 1.2 - 1.0)
    assert dd == pytest.approx(0.1)


def test_fit_risk_finds_the_largest_size_within_target() -> None:
    r = m.fit_risk(lambda k: 10 * k, target=0.35)
    assert r <= 0.035 and r > 0.034


def _row(arm: str, basis: str, seg: str, ret: float, mdd: float, cg: float = 0.1) -> m.Row:
    return m.Row(arm, True, m.FIT_FILL, basis, 0.02, seg, 10, 0, ret, cg, mdd)


def test_verdict_is_cagr_plus_three_segments_at_a_size_only() -> None:
    """판정 = (1) 8.6년 연환산 + (2) **A 맞춤 크기**에서 세 구간 수익/MDD 악화 없음.

    B 크기에서의 비교는 판정 밖 참고다 — A를 B 크기로 돌리면 한도를 넘는 설정이라(첫 실행 실측:
    창 밖 낙폭 57%) 그 비율로 B를 떨어뜨리면 안 된다(사용자 지적 2026-09-29).
    """
    b_size = m.size_label(m.ARM_B, True)
    rows = [_row(m.ARM_A, m.SIZE_OWN, m.SEG_CHAIN, 1.0, 0.35, 0.10)]
    rows.append(_row(m.ARM_B, m.SIZE_OWN, m.SEG_CHAIN, 1.5, 0.35, 0.12))
    for basis in (m.SIZE_A, b_size):
        for seg in m.VERDICT_SEGMENTS:
            rows.append(_row(m.ARM_A, basis, seg, 0.5, 0.2))
            rows.append(_row(m.ARM_B, basis, seg, 0.5, 0.2))  # 동률 = 악화 없음
    ok, lines = m.verdict(rows, m.ARM_B)
    assert ok and len(lines) == 1 + 3

    def swap(basis: str, seg: str, ret: float) -> list[m.Row]:
        return [
            _row(m.ARM_B, basis, seg, ret, 0.2)
            if (r.arm, r.size_basis, r.segment) == (m.ARM_B, basis, seg)
            else r
            for r in rows
        ]

    assert m.verdict(swap(b_size, m.SEG_BACK, 0.4), m.ARM_B)[0]  # B 크기 비교는 판정 밖
    assert len(m.reference_lines(swap(b_size, m.SEG_BACK, 0.4), m.ARM_B)) == 3
    assert not m.verdict(swap(m.SIZE_A, m.SEG_BACK, 0.4), m.ARM_B)[0]
    slower = [
        _row(m.ARM_B, m.SIZE_OWN, m.SEG_CHAIN, 0.9, 0.35, 0.09)
        if (r.arm, r.size_basis) == (m.ARM_B, m.SIZE_OWN)
        else r
        for r in rows
    ]
    assert not m.verdict(slower, m.ARM_B)[0]


def test_chain_passes_legacy_zero_duration_order_through() -> None:
    """WAN-443: `chain(legacy_zero_duration_order=True)`는 옛 정렬(보유 0분 손익이 뒤 크기에서
    안 빠짐)을 쓴다 — WAN-440 `checksum_31`이 옛 공개 CSV와 대조할 때 이 경로를 탄다."""
    zero = _trade(0, 0, -1.0, [])  # 진입 분 = 청산 분 · 손실
    later = _trade(MIN, 5 * MIN, 1.0, [100.0] * 4)
    spot = [_trade(0, 4 * MIN, 0.0, [100.0] * 4)]
    new_total, _ = m.chain(spot, [zero, later], 0.1)
    old_total, _ = m.chain(spot, [zero, later], 0.1, legacy_zero_duration_order=True)
    assert new_total != old_total
    legacy = w.mtm_path([zero, later], risk=0.1, legacy_zero_duration_order=True)
    assert old_total == pytest.approx(float(legacy.mtm_close[-1]) - 1.0)
