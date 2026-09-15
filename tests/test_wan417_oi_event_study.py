"""WAN-417 §G-0 OI 사건 정렬 관측 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것:

1. **착수 전 상수** — 창 −24h~+4h · 5분 · 판정 점 7개는 전부 「0 왼쪽」 · Bonferroni 28은 2σ보다
   엄격하다 · **판정 정렬은 진입**이다.
2. 🚨 **판정 정렬은 진입 뒤 데이터를 안 읽는다** — 진입 뒤 OI를 바꿔도 판정 점의 값이 비트 동일하고,
   청산 정렬은 바뀐다(= 청산 정렬이 결과를 싣는다는 것을 동작으로 보인다 · WAN-377 미래 절단 자).
3. **기준점 = 0점 이하 마지막 스냅샷**이고 5분보다 오래됐으면 `no_anchor` · 창에 구멍이 있으면 뺀다
   (보간 없음) · **두 정렬 중 하나라도** 구멍이면 뺀다 · 0점 변화율은 정확히 0.
4. **판정은 코드가 낸다** — 관문 넷이 각각 걸리고, 청산 정렬은 관문을 다 넘어도 **무효**로 찍힌다.
5. **관측이 셋업을 안 바꾼다** — 통제 열을 붙여도 진입·청산·손익 열은 값으로 같다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtest import wan417_oi_event_study as wan417
from backtest.wan417_oi_event_study import (
    ALIGN_ENTRY,
    ALIGN_EXIT,
    ALIGNS,
    ANCHOR_COLUMN,
    CONTROL_NO_CRASH,
    CONTROL_NONE,
    CONTROL_POOLED,
    EXCLUDE_HOLE_BTC,
    EXCLUDE_HOLE_SELF,
    EXCLUDE_NO_ANCHOR,
    INTERVAL_MS,
    LEAD_OFFSETS_MS,
    MIN_GROUP_N,
    OFFSETS_MS,
    POINT_LEAD,
    POINT_NONE,
    POINT_PROXY,
    POINT_UNDECIDED,
    SCOPE_BTC,
    SCOPE_SELF,
    SIGMA_MULTIPLE,
    SIGNAL_MAX_OFFSET_MS,
    TESTS,
    UNITS,
    VERDICT_ALIGN,
    VERDICT_INVALID,
    VERDICT_LEAD,
    VERDICT_NO,
    VERDICT_PROXY,
    WINDOW_AFTER_MS,
    WINDOW_BEFORE_MS,
    PathRow,
    attach_controls,
    build_paths,
    decision_z,
    lead_points,
    offset_index,
    offset_label,
    panel_verdicts,
)
from data.oi_metrics_archive import MetricsSeries

_H = 3_600_000
_T0 = 1_700_000_000_000 - (1_700_000_000_000 % INTERVAL_MS)  # 5분 격자 위의 기준 시각
_START = _T0 - 40 * _H
_COUNT = 90 * 12  # 90시간치


def test_pinned_constants() -> None:
    assert WINDOW_BEFORE_MS == 24 * _H and WINDOW_AFTER_MS == 4 * _H and INTERVAL_MS == 300_000
    assert len(OFFSETS_MS) == 337 and OFFSETS_MS[0] == -24 * _H and OFFSETS_MS[-1] == 4 * _H
    assert SIGNAL_MAX_OFFSET_MS == -INTERVAL_MS
    assert all(o <= SIGNAL_MAX_OFFSET_MS for o in LEAD_OFFSETS_MS)
    assert len(LEAD_OFFSETS_MS) == 7 and len(UNITS) == 2 and TESTS == 28
    assert SIGMA_MULTIPLE == 2.0 and decision_z() > SIGMA_MULTIPLE
    # 양측 α = 2·(1−Φ(2)) ≈ 4.55% → /28 → z ≈ 3.15.
    assert decision_z() == pytest.approx(3.151, abs=0.005)
    assert offset_index(-24 * _H) == 0 and offset_index(0) == 288 and offset_index(4 * _H) == 336
    assert offset_label(-6 * _H) == "−6h" and offset_label(-30 * 60_000) == "−30m"
    assert VERDICT_ALIGN == ALIGN_ENTRY and set(ALIGNS) == {ALIGN_ENTRY, ALIGN_EXIT}
    assert ANCHOR_COLUMN == {ALIGN_ENTRY: "entry_time", ALIGN_EXIT: "tp_on_exit_time"}


def _series(
    symbol: str, *, base: float, drop: frozenset[int] = frozenset(), bump_after: int | None = None
) -> MetricsSeries:
    idx = [i for i in range(_COUNT) if i not in drop]
    t = np.array([_START + i * INTERVAL_MS for i in idx], dtype=np.int64)
    coin = base + (t - _START) / INTERVAL_MS  # 점마다 1씩 오른다
    if bump_after is not None:
        coin = np.where(t > bump_after, coin * 2.0, coin)  # 그 시각 **뒤**만 바꾼다
    return MetricsSeries(symbol, t, coin.astype(np.float64), (coin * 10.0).astype(np.float64))


def _population(rows: list[tuple[str, int, int, str]]) -> pd.DataFrame:
    """(종목, 진입 시각, 청산 시각, 사유)."""
    out = []
    for i, (symbol, entry_ms, exit_ms, reason) in enumerate(rows):
        out.append(
            {
                "symbol": f"{symbol}/USDT:USDT",
                "timeframe": "1h",
                "segment": "oos_warm",
                "trigger_time": entry_ms - _H,
                "entry_time": entry_ms,
                "entry_price": 100.0 + i,
                "stop_price": 99.0,
                "tp_on_exit_time": exit_ms,
                "tp_on_reason": reason,
                "stop_width": 0.01,
                "net_r": -1.05 if reason == "stop_loss" else 1.45,
                "is_stop": reason == "stop_loss",
            }
        )
    return pd.DataFrame(out)


def test_paths_anchor_on_last_snapshot_for_both_alignments() -> None:
    btc = _series("BTCUSDT", base=1000.0)
    population = _population(
        [
            ("BTC", _T0 + 90_000, _T0 + 2 * _H + 30_000, "stop_loss"),
            ("BTC", _T0 + INTERVAL_MS, _T0 + 3 * _H, "take_profit"),
        ]
    )
    matrices, exclusions = build_paths(population, {"BTCUSDT": btc})
    labels = matrices.labels
    assert len(labels) == 2 and int(exclusions["n"].sum()) == 0
    assert labels["anchor_time_entry"].tolist() == [_T0, _T0 + INTERVAL_MS]
    assert labels["anchor_lag_entry_ms"].tolist() == [90_000, 0]
    assert labels["anchor_time_exit"].tolist() == [_T0 + 2 * _H, _T0 + 3 * _H]
    assert labels["hold_ms"].tolist() == [2 * _H - 60_000, 3 * _H - INTERVAL_MS]
    for align in ALIGNS:
        coin = matrices.paths[(align, SCOPE_SELF, "coin")]
        assert coin.shape == (2, len(OFFSETS_MS))
        assert (coin[:, offset_index(0)] == 0.0).all()
        # BTC 셋업의 자기 범위 == BTC 범위(같은 격자 · 같은 값).
        assert np.array_equal(coin, matrices.paths[(align, SCOPE_BTC, "coin")])
        # 명목 열은 코인 열의 10배라 변화율은 같다.
        assert np.allclose(coin, matrices.paths[(align, SCOPE_SELF, "usdt")], atol=1e-6)
    anchor_value = 1000.0 + (_T0 - _START) / INTERVAL_MS
    entry = matrices.paths[(ALIGN_ENTRY, SCOPE_SELF, "coin")]
    assert entry[0, offset_index(-INTERVAL_MS)] == pytest.approx(-1.0 / anchor_value, rel=1e-5)


def test_verdict_alignment_never_reads_after_entry_but_exit_alignment_does() -> None:
    """🚨 미래 절단 — 진입 **뒤** OI만 바꿔도 진입 정렬 판정 점은 비트 동일, 청산 정렬은 바뀐다."""
    entry, exit_ = _T0, _T0 + 3 * _H
    population = _population([("ETH", entry, exit_, "stop_loss")])
    market = _series("BTCUSDT", base=1000.0)
    plain, _ = build_paths(
        population, {"BTCUSDT": market, "ETHUSDT": _series("ETHUSDT", base=500.0)}
    )
    bumped, _ = build_paths(
        population,
        {"BTCUSDT": market, "ETHUSDT": _series("ETHUSDT", base=500.0, bump_after=entry)},
    )
    lead_cols = [offset_index(o) for o in LEAD_OFFSETS_MS] + [offset_index(0)]
    for unit, _col in UNITS:
        a = plain.paths[(ALIGN_ENTRY, SCOPE_SELF, unit)][:, lead_cols]
        b = bumped.paths[(ALIGN_ENTRY, SCOPE_SELF, unit)][:, lead_cols]
        assert np.array_equal(a, b)
        exit_a = plain.paths[(ALIGN_EXIT, SCOPE_SELF, unit)][:, lead_cols]
        exit_b = bumped.paths[(ALIGN_EXIT, SCOPE_SELF, unit)][:, lead_cols]
        assert not np.array_equal(exit_a, exit_b)


def test_setup_is_excluded_when_either_alignment_has_a_hole() -> None:
    btc = _series("BTCUSDT", base=1000.0)
    exit_ms = _T0 + 10 * _H
    # 청산 뒤 +2h 자리에만 구멍 — 진입 창은 온전하지만 청산 창이 깨진다.
    hole = offset_index(0) + (exit_ms - _START) // INTERVAL_MS - offset_index(0) + 24
    eth = _series("ETHUSDT", base=500.0, drop=frozenset({hole}))
    matrices, exclusions = build_paths(
        _population([("ETH", _T0, exit_ms, "stop_loss")]), {"BTCUSDT": btc, "ETHUSDT": eth}
    )
    assert len(matrices.labels) == 0
    by = exclusions.groupby(["align", "reason"])["n"].sum()
    assert by[(ALIGN_EXIT, EXCLUDE_HOLE_SELF)] == 1 and by[(ALIGN_ENTRY, EXCLUDE_HOLE_SELF)] == 0


def test_hole_in_market_series_and_stale_anchor_are_excluded() -> None:
    btc_hole = _series("BTCUSDT", base=1000.0, drop=frozenset({(_T0 - _START) // INTERVAL_MS - 3}))
    eth = _series("ETHUSDT", base=500.0)
    _, exclusions = build_paths(
        _population([("ETH", _T0, _T0 + _H, "stop_loss")]), {"BTCUSDT": btc_hole, "ETHUSDT": eth}
    )
    by = exclusions.groupby(["align", "reason"])["n"].sum()
    assert by[(ALIGN_ENTRY, EXCLUDE_HOLE_BTC)] == 1
    # 진입 직전 스냅샷이 빠져 마지막 스냅샷이 6분 전 → 0점이 없다.
    missing = (_T0 - _START) // INTERVAL_MS
    eth_stale = _series("ETHUSDT", base=500.0, drop=frozenset({missing}))
    _, exclusions = build_paths(
        _population([("ETH", _T0 + 60_000, _T0 + 5 * _H, "stop_loss")]),
        {"BTCUSDT": _series("BTCUSDT", base=1000.0), "ETHUSDT": eth_stale},
    )
    by = exclusions.groupby(["align", "reason"])["n"].sum()
    assert by[(ALIGN_ENTRY, EXCLUDE_NO_ANCHOR)] == 1


def test_attach_controls_keeps_setup_columns_and_marks_crash_by_entry_or_exit_day() -> None:
    from common.timefmt import kst_day_key

    entry = 1_700_000_000_000
    population = _population(
        [
            ("BTC", entry, entry + _H, "stop_loss"),
            ("ETH", entry + 39 * _H, entry + 40 * _H, "take_profit"),
        ]
    )
    out = attach_controls(population, bad_days=[kst_day_key(entry)])
    setup_cols = ["entry_time", "tp_on_exit_time", "net_r", "is_stop", "stop_price", "entry_price"]
    pd.testing.assert_frame_equal(out[setup_cols], population[setup_cols])
    assert out["is_crash_day"].tolist() == [True, False]
    out2 = attach_controls(population, bad_days=[kst_day_key(entry + 40 * _H)])
    assert out2["is_crash_day"].tolist() == [False, True]
    assert "realized_today_before" in out and "realized_bucket" in out


def _row(
    *,
    align: str,
    segment: str,
    control: str,
    offset: int,
    delta: float,
    sigma: float,
    n: int = 500,
) -> PathRow:
    return PathRow(
        align=align,
        segment=segment,
        control=control,
        stratum="—",
        scope=SCOPE_SELF,
        unit="coin",
        offset_ms=offset,
        n_stop=n,
        n_tp=n,
        mean_stop=delta,
        mean_tp=0.0,
        median_stop=delta,
        median_tp=0.0,
        q25_stop=0.0,
        q75_stop=0.0,
        q25_tp=0.0,
        q75_tp=0.0,
        delta=delta,
        sigma=sigma,
        z=(abs(delta) / sigma if sigma > 0 else math.inf),
    )


def _rows_for(
    offset: int,
    *,
    oos: float,
    is_: float,
    crash: float,
    pooled: float,
    align: str = ALIGN_ENTRY,
    sigma: float = 0.001,
    n: int = 500,
) -> list[PathRow]:
    spec = [
        ("oos_warm", CONTROL_NONE, oos),
        ("is", CONTROL_NONE, is_),
        ("oos_warm", CONTROL_NO_CRASH, crash),
        ("oos_warm", CONTROL_POOLED, pooled),
    ]
    return [
        _row(align=align, segment=s, control=c, offset=offset, delta=d, sigma=sigma, n=n)
        for s, c, d in spec
    ]


def _status_at(rows: list[PathRow], offset: int) -> str:
    return next(
        p.status
        for p in lead_points(rows)
        if p.offset_ms == offset and p.scope == SCOPE_SELF and p.unit == "coin"
    )


def test_lead_point_requires_all_four_gates() -> None:
    k = LEAD_OFFSETS_MS[2]

    def status(**kw: float) -> str:
        return _status_at(_rows_for(k, **kw), k)  # type: ignore[arg-type]

    assert status(oos=-0.02, is_=-0.01, crash=-0.02, pooled=-0.02) == POINT_LEAD
    # 뒷구간 z가 Bonferroni를 못 넘으면 「무」 — 2σ는 넘어도.
    assert status(oos=-0.0025, is_=-0.01, crash=-0.02, pooled=-0.02) == POINT_NONE
    # 통제 하나가 무너지면 「대리변수」.
    assert status(oos=-0.02, is_=+0.01, crash=-0.02, pooled=-0.02) == POINT_PROXY
    assert status(oos=-0.02, is_=-0.01, crash=+0.02, pooled=-0.02) == POINT_PROXY
    assert status(oos=-0.02, is_=-0.01, crash=-0.02, pooled=-0.0005) == POINT_PROXY
    thin = _rows_for(k, oos=-0.02, is_=-0.01, crash=-0.02, pooled=-0.02, n=MIN_GROUP_N - 1)
    assert _status_at(thin, k) == POINT_UNDECIDED


def test_lead_points_read_only_the_requested_alignment() -> None:
    k = LEAD_OFFSETS_MS[0]
    exit_rows = _rows_for(k, oos=-0.02, is_=-0.01, crash=-0.02, pooled=-0.02, align=ALIGN_EXIT)
    entry_points = lead_points(exit_rows, align=ALIGN_ENTRY)
    assert len(entry_points) == TESTS and all(p.status == POINT_UNDECIDED for p in entry_points)
    assert all(p.offset_ms <= SIGNAL_MAX_OFFSET_MS for p in entry_points)
    exit_points = lead_points(exit_rows, align=ALIGN_EXIT)
    assert any(p.status == POINT_LEAD for p in exit_points)


def test_exit_alignment_is_invalid_even_when_every_gate_passes() -> None:
    k1, k2 = LEAD_OFFSETS_MS[0], LEAD_OFFSETS_MS[1]
    rows = _rows_for(k1, oos=-0.02, is_=-0.01, crash=-0.02, pooled=-0.02)
    rows += _rows_for(k2, oos=-0.02, is_=+0.01, crash=-0.02, pooled=-0.02)
    rows += _rows_for(k1, oos=-0.02, is_=-0.01, crash=-0.02, pooled=-0.02, align=ALIGN_EXIT)
    points = [*lead_points(rows, align=ALIGN_ENTRY), *lead_points(rows, align=ALIGN_EXIT)]
    verdicts = {(v.align, v.scope, v.unit): v for v in panel_verdicts(points)}
    assert verdicts[(ALIGN_ENTRY, SCOPE_SELF, "coin")].verdict == VERDICT_LEAD
    assert "−24h" in verdicts[(ALIGN_ENTRY, SCOPE_SELF, "coin")].reason
    exit_verdict = verdicts[(ALIGN_EXIT, SCOPE_SELF, "coin")]
    assert exit_verdict.verdict == VERDICT_INVALID and exit_verdict.lead_points == 1
    proxy_only = _rows_for(k2, oos=-0.02, is_=+0.01, crash=-0.02, pooled=-0.02)
    verdicts = {(v.align, v.scope, v.unit): v for v in panel_verdicts(lead_points(proxy_only))}
    assert verdicts[(ALIGN_ENTRY, SCOPE_SELF, "coin")].verdict == VERDICT_PROXY
    none_only = _rows_for(k2, oos=-0.001, is_=+0.01, crash=-0.02, pooled=-0.02)
    verdicts = {(v.align, v.scope, v.unit): v for v in panel_verdicts(lead_points(none_only))}
    assert verdicts[(ALIGN_ENTRY, SCOPE_SELF, "coin")].verdict == VERDICT_NO


def test_path_rows_delta_is_stop_minus_tp() -> None:
    matrix = np.zeros((6, len(OFFSETS_MS)), dtype=np.float32)
    matrix[:3, :] = 0.02  # 손절 세 건
    matrix[3:, :] = -0.01  # 익절 세 건
    is_stop = np.array([True, True, True, False, False, False])
    rows = wan417.path_rows(
        matrix,
        is_stop,
        align=ALIGN_ENTRY,
        segment="oos_warm",
        control=CONTROL_NONE,
        stratum="—",
        scope=SCOPE_SELF,
        unit="coin",
    )
    assert len(rows) == len(OFFSETS_MS)
    first = rows[0]
    assert first.n_stop == 3 and first.n_tp == 3
    assert first.delta == pytest.approx(0.03, abs=1e-6)
    assert first.median_tp == pytest.approx(-0.01, abs=1e-6)
    assert first.sigma == 0.0 and math.isinf(first.z)


def test_implied_price_splits_notional_into_coins_and_price() -> None:
    coin = np.array([0.10, 0.0, -0.05])
    price = np.array([0.0, 0.02, 0.10])
    usdt = (1 + coin) * (1 + price) - 1  # 명목 = 수량 × 가격
    assert np.allclose(wan417.implied_price_change(coin, usdt), price, atol=1e-12)
