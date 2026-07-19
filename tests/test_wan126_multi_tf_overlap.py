"""WAN-126 리포트 집계 테스트.

격자 실행(수십 분·DB 필요)이 아니라 **집계·분해·판정 로직**을 손으로 만든 행으로 고정한다:
`B−A`/`C−B` 분해, 거래 수 오염 판정, 심볼 편중 leave-one-out, 표본 게이트, (a)/(b)/(c)
판정 문장. 격자 자체의 정확성은 `test_multi_tf_overlap.py`(엔진·룩어헤드)가 맡는다.
"""

from __future__ import annotations

import pandas as pd

from backtest.wan126_multi_tf_overlap import (
    OverlapRow,
    _cache_path,
    decomposition,
    detect_ltf_archives,
    sample_gate,
    symbol_bias,
    trade_contamination,
    verdict,
)
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockResult


def _row(
    *,
    symbol: str,
    segment: str,
    arm: str,
    definition: str,
    total_return: float,
    num_trades: int = 50,
) -> OverlapRow:
    return OverlapRow(
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        arm=arm,
        definition=definition,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.1,
        fill_rate=0.8,
        eligible_setups=100,
        mean_r=0.1,
        sharpe=1.0,
        n_from_15m=num_trades if arm != "A" else 0,
        n_from_5m=0,
        n_from_1m=0,
    )


def _frame(rows: list[OverlapRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _grid(
    a: float, b: float, c: float, *, symbols: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT")
) -> pd.DataFrame:
    """두 심볼 모두 같은 (A,B,C) total_return을 갖는 OOS 프레임(정본 정의)."""
    rows: list[OverlapRow] = []
    for sym in symbols:
        rows.append(_row(symbol=sym, segment="oos", arm="A", definition="none", total_return=a))
        rows.append(
            _row(symbol=sym, segment="oos", arm="B", definition="contained", total_return=b)
        )
        rows.append(
            _row(symbol=sym, segment="oos", arm="C", definition="contained", total_return=c)
        )
    return _frame(rows)


def test_decomposition_signs() -> None:
    frame = _grid(0.10, 0.13, 0.16)  # 선별 +3%p, 가격 +3%p
    dec = decomposition(frame)
    oos = dec[dec["segment"] == "oos"].iloc[0]
    assert oos["selection_B_minus_A"] == pytest_approx(0.03)
    assert oos["price_C_minus_B"] == pytest_approx(0.03)


def test_verdict_a_both_positive() -> None:
    lines = verdict(_grid(0.10, 0.13, 0.16))
    assert any("(a)" in ln for ln in lines)


def test_verdict_b_price_only() -> None:
    # 선별 0(B==A), 가격만 플러스(C>B) → 볼린저 부류.
    lines = verdict(_grid(0.10, 0.10, 0.14))
    assert any("(b)" in ln for ln in lines)


def test_verdict_c_both_nonpositive() -> None:
    lines = verdict(_grid(0.10, 0.08, 0.07))  # 선별 음수, 가격 음수
    assert any("(c)" in ln for ln in lines)


def test_trade_contamination_flags_over_5pct() -> None:
    rows = [
        _row(
            symbol="BTC/USDT:USDT",
            segment="oos",
            arm="B",
            definition="contained",
            total_return=0.1,
            num_trades=100,
        ),
        _row(
            symbol="BTC/USDT:USDT",
            segment="oos",
            arm="C",
            definition="contained",
            total_return=0.1,
            num_trades=90,
        ),
    ]
    contam = trade_contamination(_frame(rows))
    oos = contam[contam["segment"] == "oos"].iloc[0]
    assert oos["diff_pct"] == pytest_approx(0.10)
    assert bool(oos["contaminated"]) is True


def test_trade_contamination_clean_under_5pct() -> None:
    rows = [
        _row(
            symbol="BTC/USDT:USDT",
            segment="oos",
            arm="B",
            definition="contained",
            total_return=0.1,
            num_trades=100,
        ),
        _row(
            symbol="BTC/USDT:USDT",
            segment="oos",
            arm="C",
            definition="contained",
            total_return=0.1,
            num_trades=98,
        ),
    ]
    contam = trade_contamination(_frame(rows))
    assert bool(contam[contam["segment"] == "oos"].iloc[0]["contaminated"]) is False


def test_symbol_bias_detects_single_symbol_carry() -> None:
    """ETH가 선별 효과를 혼자 만들면 ETH 제외 시 `B−A`가 무너진다."""
    rows = [
        _row(symbol="BTC/USDT:USDT", segment="oos", arm="A", definition="none", total_return=0.10),
        _row(
            symbol="BTC/USDT:USDT",
            segment="oos",
            arm="B",
            definition="contained",
            total_return=0.10,
        ),
        _row(symbol="ETH/USDT:USDT", segment="oos", arm="A", definition="none", total_return=0.10),
        _row(
            symbol="ETH/USDT:USDT",
            segment="oos",
            arm="B",
            definition="contained",
            total_return=0.30,
        ),
    ]
    bias = symbol_bias(_frame(rows))
    none_row = bias[bias["dropped"] == "(none)"].iloc[0]
    drop_eth = bias[bias["dropped"] == "ETH"].iloc[0]
    # 전체 B−A는 플러스인데(평균 B 0.20 vs A 0.10) ETH를 빼면 0으로 무너진다.
    assert none_row["selection_B_minus_A"] == pytest_approx(0.10)
    assert drop_eth["selection_B_minus_A"] == pytest_approx(0.0)


def test_sample_gate_flags_thin_cells() -> None:
    rows = [
        _row(
            symbol="BTC/USDT:USDT",
            segment="oos",
            arm="B",
            definition="contained",
            total_return=0.1,
            num_trades=10,
        ),
        _row(
            symbol="ETH/USDT:USDT",
            segment="oos",
            arm="B",
            definition="contained",
            total_return=0.1,
            num_trades=30,
        ),
    ]
    gate = sample_gate(_frame(rows))
    row = gate[(gate["arm"] == "B")].iloc[0]
    assert row["min_trades"] == 10
    assert bool(row["ok"]) is False  # 최소 10 < 20.


def test_ltf_archive_cache_roundtrip(tmp_path: object) -> None:
    """캐시가 있으면 탐지를 건너뛰고 디스크에서 읽는다(1분봉 8분+/심볼을 피하는 핵심).

    캐시 파일을 미리 심어 두고 `detect_ltf_archives`가 DB 탐지 없이 그 값을 그대로 돌려주는지
    확인한다 — 캐시 히트 경로는 DB·거래소를 안 탄다.
    """
    import pathlib

    cache_dir = pathlib.Path(str(tmp_path))
    sym = "BTC/USDT:USDT"
    start_ms, end_ms = 1_000, 2_000
    zone = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=110.0,
        bottom=100.0,
        start_time=500,
        confirmed_time=500,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    result = OrderBlockResult(order_blocks=[zone], signals=[], retap_signals=[])
    path = _cache_path(str(cache_dir), sym, "5m", start_ms, end_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(), encoding="utf-8")

    got = detect_ltf_archives(
        sym, ("5m",), start_ms=start_ms, end_ms=end_ms, cache_dir=str(cache_dir)
    )
    assert "5m" in got
    assert len(got["5m"].order_blocks) == 1
    assert got["5m"].order_blocks[0].top == 110.0


def pytest_approx(value: float) -> object:
    import pytest

    return pytest.approx(value, abs=1e-9)
