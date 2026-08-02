"""WAN-226 §3 형성-예약 대조 프레젠터 테스트.

CSV → 팔 페어링 → 표 렌더가 두 팔(현행 24봉 · 형성-예약 무기한)을 올바로 가르는지,
그리고 무기한 팔의 빈 `limit_valid_bars`가 CSV 왕복에서 `None`으로 보존되는지를 고정한다
(그게 두 팔을 가르는 핵심이라 회귀로 막는다)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest import wan226_reservation_compare as w226
from backtest.harness import RunRow, rows_to_frame


def _row(
    symbol: str,
    timeframe: str,
    segment: str,
    limit_valid_bars: int | None,
    *,
    total_return: float,
    fill_rate: float,
    num_trades: int,
    num_filled: int,
) -> RunRow:
    return RunRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        window=0,
        entry_mode="zone_limit",
        take_profit_r=1.5,
        offset_bps=2.0,
        max_zone_width_atr=1.28,
        limit_valid_bars=limit_valid_bars,
        fill="baseline",
        seed=0,
        start_time=1_600_128_000_000,
        end_time=1_784_664_000_000,
        num_bars=12816,
        num_trades=num_trades,
        win_rate=0.6,
        total_return=total_return,
        max_drawdown=0.05,
        sharpe=1.0,
        profit_factor=2.0,
        mean_r=0.5,
        fill_rate=fill_rate,
        eligible_setups=86,
        num_filled=num_filled,
        funding_coverage=0.86,
    )


def _pair_rows() -> list[RunRow]:
    rows: list[RunRow] = []
    for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        for segment in ("is", "oos_warm"):
            rows.append(
                _row(
                    symbol,
                    "4h",
                    segment,
                    24,
                    total_return=0.20,
                    fill_rate=0.70,
                    num_trades=40,
                    num_filled=45,
                )
            )
            rows.append(
                _row(
                    symbol,
                    "4h",
                    segment,
                    None,
                    total_return=0.25,
                    fill_rate=0.79,
                    num_trades=44,
                    num_filled=52,
                )
            )
    return rows


def test_pair_arms_groups_both_arms() -> None:
    pairs = w226.pair_arms(_pair_rows())
    # 2 symbols × 2 segments = 4 pairs, 각 팔이 채워졌다.
    assert len(pairs) == 4
    for pair in pairs.values():
        assert pair.complete
        assert pair.current is not None and pair.current.limit_valid_bars == 24
        assert pair.reservation is not None and pair.reservation.limit_valid_bars is None


def test_pair_arms_ignores_other_segments_and_arms() -> None:
    rows = _pair_rows()
    # full 구간과 12봉 팔은 표에서 접힌다.
    rows.append(
        _row(
            "BTC/USDT:USDT",
            "4h",
            "full",
            24,
            total_return=0.3,
            fill_rate=0.7,
            num_trades=50,
            num_filled=55,
        )
    )
    rows.append(
        _row(
            "BTC/USDT:USDT",
            "4h",
            "is",
            12,
            total_return=0.1,
            fill_rate=0.6,
            num_trades=30,
            num_filled=33,
        )
    )
    pairs = w226.pair_arms(rows)
    assert ("BTC/USDT:USDT", "4h", "full") not in pairs
    # is 팔은 여전히 24 vs None만 잡는다(12봉은 무시).
    btc_is = pairs[("BTC/USDT:USDT", "4h", "is")]
    assert btc_is.current is not None and btc_is.current.limit_valid_bars == 24


def test_symbol_mean_return_delta() -> None:
    pairs = list(w226.pair_arms(_pair_rows()).values())
    # 각 페어 델타 = (0.25 − 0.20) × 100 = +5.0%p, 전부 같으므로 평균도 +5.0.
    assert w226.symbol_mean_return_delta(pairs) == pytest.approx(5.0)


def test_rows_from_csv_preserves_none_arm(tmp_path: Path) -> None:
    """무기한 팔의 빈 `limit_valid_bars`가 CSV 왕복에서 None으로 되돌아온다."""
    csv = tmp_path / "compare.csv"
    rows_to_frame(_pair_rows()).to_csv(csv, index=False)
    loaded = w226.rows_from_csv(csv)
    assert len(loaded) == len(_pair_rows())
    arms = {r.limit_valid_bars for r in loaded}
    assert arms == {24, None}  # None이 float NaN으로 뭉개지지 않았다.


def test_build_summary_renders_and_marks_funding_gap(tmp_path: Path) -> None:
    rows = _pair_rows()
    rows.append(
        _row(
            "DOGE/USDT:USDT",
            "4h",
            "oos_warm",
            24,
            total_return=0.1,
            fill_rate=0.6,
            num_trades=20,
            num_filled=25,
        )
    )
    rows.append(
        _row(
            "DOGE/USDT:USDT",
            "4h",
            "oos_warm",
            None,
            total_return=0.05,
            fill_rate=0.7,
            num_trades=24,
            num_filled=30,
        )
    )
    md = w226.build_summary_markdown(rows, cells_csv=Path("x.csv"))
    assert "WAN-226 §3" in md
    assert "4h · is" in md and "4h · oos_warm" in md
    assert "DOGE†" in md  # 신규 종목 펀딩 갭 표시.
    assert "심볼평균 total_return 델타" in md
