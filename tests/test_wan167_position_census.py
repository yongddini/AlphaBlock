"""WAN-167 census의 자(尺)를 동작으로 고정한다.

백테스트를 돌리지 않는다 — 스위프라인·스코프·판정·CSV 왕복은 전부 순수 함수라 합성 구간으로
검증한다. 여기서 고정하는 함정들:

1. **반개구간** — 청산 시각 == 다음 진입 시각(같은 칸 연속 거래·칸 간 맞닿음)은 겹침이 아니다.
2. **같은 칸 겹침 거부** — 동시 1포지션 전제(사용자 정의)가 깨지면 조용히 세지 않고 거부한다.
3. **분포 완결** — 수준 0을 포함해 share 합이 정확히 1.0이라야 "겹침 없음"과 "안 셌음"이
   구분된다.
4. **`min_count=2` 스코프** — "자기 두 칸을 동시에 든 종목 수"가 칸 수 겹침과 뒤섞이지 않는다.
5. **판정 세 갈래** — 문턱이 문장이 아니라 코드에 있고, (a)/(b)/(c)가 실제로 갈린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest.wan167_position_census import (
    NEGLIGIBLE_OVERLAP_SHARE,
    SIGNIFICANT_OVERLAP_SHARE,
    CensusRow,
    IntervalRow,
    census_window,
    concurrency_durations,
    intervals_from_csv,
    intervals_to_frame,
    max_level,
    mean_level,
    run_census,
    share_at_least,
    validate_single_position,
    verdict,
)

WINDOW = (0, 1_000)

BTC = "BTC/USDT:USDT"
ETH = "ETH/USDT:USDT"


def _iv(symbol: str, timeframe: str, entry: int, exit_: int) -> IntervalRow:
    return IntervalRow(
        symbol=symbol,
        timeframe=timeframe,
        side="long",
        entry_time=entry,
        exit_time=exit_,
        window_start=WINDOW[0],
        window_end=WINDOW[1],
    )


def _cell_unit(row: IntervalRow) -> tuple[str, str]:
    return (row.symbol, row.timeframe)


# --------------------------------------------------------------------------- #
# 스위프라인
# --------------------------------------------------------------------------- #


class TestConcurrencyDurations:
    def test_disjoint_intervals_never_overlap(self) -> None:
        rows = [_iv(BTC, "15m", 100, 200), _iv(BTC, "1h", 300, 400)]
        durations = concurrency_durations(
            rows, window_start=0, window_end=1_000, unit_of=_cell_unit
        )
        assert durations == {0: 800, 1: 200}

    def test_overlap_counts_both_cells(self) -> None:
        rows = [_iv(BTC, "15m", 100, 300), _iv(BTC, "1h", 200, 400)]
        durations = concurrency_durations(
            rows, window_start=0, window_end=1_000, unit_of=_cell_unit
        )
        # [100,200) 1칸 · [200,300) 2칸 · [300,400) 1칸
        assert durations == {0: 700, 1: 200, 2: 100}

    def test_half_open_touching_intervals_do_not_overlap(self) -> None:
        """청산 시각 == 다음 진입 시각이면 그 순간을 동시에 세지 않는다(반개구간)."""
        rows = [_iv(BTC, "15m", 100, 200), _iv(ETH, "15m", 200, 300)]
        durations = concurrency_durations(
            rows, window_start=0, window_end=1_000, unit_of=_cell_unit
        )
        assert durations == {0: 800, 1: 200}
        assert 2 not in durations

    def test_intervals_clipped_to_window(self) -> None:
        rows = [_iv(BTC, "15m", -50, 100), _iv(ETH, "15m", 900, 2_000)]
        durations = concurrency_durations(
            rows, window_start=0, window_end=1_000, unit_of=_cell_unit
        )
        assert durations == {0: 800, 1: 200}

    def test_zero_length_interval_contributes_nothing(self) -> None:
        rows = [_iv(BTC, "15m", 100, 100)]
        durations = concurrency_durations(
            rows, window_start=0, window_end=1_000, unit_of=_cell_unit
        )
        assert durations == {0: 1_000}

    def test_symbol_unit_min_count_2_requires_both_cells_of_same_symbol(self) -> None:
        """`min_count=2` = 같은 종목의 두 칸이 **동시에** 열려 있어야 그 종목이 켜진다."""
        rows = [
            _iv(BTC, "15m", 100, 300),
            _iv(BTC, "1h", 200, 400),
            _iv(ETH, "15m", 200, 250),  # ETH는 한 칸뿐 — min_count=2에서 절대 안 켜짐
        ]
        durations = concurrency_durations(
            rows,
            window_start=0,
            window_end=1_000,
            unit_of=lambda r: r.symbol,
            min_count=2,
        )
        # BTC 두 칸이 겹치는 [200,300)에서만 수준 1.
        assert durations == {0: 900, 1: 100}

    def test_empty_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="창이 비었습니다"):
            concurrency_durations([], window_start=10, window_end=10, unit_of=_cell_unit)


# --------------------------------------------------------------------------- #
# 같은 칸 겹침 거부 (동시 1포지션 전제)
# --------------------------------------------------------------------------- #


class TestValidateSinglePosition:
    def test_same_cell_overlap_rejected(self) -> None:
        rows = [_iv(BTC, "15m", 100, 300), _iv(BTC, "15m", 200, 400)]
        with pytest.raises(ValueError, match="같은 칸 안에서 포지션이 겹친다"):
            validate_single_position(rows)

    def test_back_to_back_same_cell_allowed(self) -> None:
        """같은 1분봉에서 청산·재진입(exit == next entry)은 겹침이 아니다."""
        validate_single_position([_iv(BTC, "15m", 100, 200), _iv(BTC, "15m", 200, 300)])

    def test_cross_cell_overlap_allowed(self) -> None:
        validate_single_position([_iv(BTC, "15m", 100, 300), _iv(BTC, "1h", 200, 400)])

    def test_exit_before_entry_rejected(self) -> None:
        with pytest.raises(ValueError, match="청산이 진입보다 이르다"):
            validate_single_position([_iv(BTC, "15m", 300, 100)])


# --------------------------------------------------------------------------- #
# census (스코프 · 분포 완결)
# --------------------------------------------------------------------------- #


class TestRunCensus:
    def _census(self) -> list[CensusRow]:
        rows = [
            _iv(BTC, "15m", 100, 300),
            _iv(BTC, "1h", 200, 400),
            _iv(ETH, "1h", 250, 350),
        ]
        return run_census(rows, window_start=0, window_end=1_000, symbols=[BTC, ETH])

    def test_shares_sum_to_one_per_scope(self) -> None:
        census = self._census()
        scopes = {(r.scope_kind, r.scope_key) for r in census}
        for kind, key in scopes:
            total = sum(r.share for r in census if r.scope_kind == kind and r.scope_key == key)
            assert total == pytest.approx(1.0), (kind, key)

    def test_level_zero_row_present(self) -> None:
        """ "겹침 없음"과 "안 셌음"이 구분되도록 수준 0이 행으로 존재한다."""
        census = self._census()
        main = [r for r in census if r.scope_kind == "cells" and r.scope_key == "main"]
        assert any(r.level == 0 for r in main)

    def test_main_cells_distribution(self) -> None:
        census = self._census()
        # 열림 수: [100,200) 1 · [200,250) 2 · [250,300) 3 · [300,350) 2 · [350,400) 1
        assert share_at_least(census, "cells", "main", 1) == pytest.approx(0.3)
        assert share_at_least(census, "cells", "main", 2) == pytest.approx(0.15)
        assert share_at_least(census, "cells", "main", 3) == pytest.approx(0.05)
        assert max_level(census, "cells", "main") == 3
        assert mean_level(census, "cells", "main") == pytest.approx(
            (100 * 1 + 50 * 2 + 50 * 3 + 50 * 2 + 50 * 1) / 1_000
        )

    def test_symbols_scope_counts_symbols_not_cells(self) -> None:
        census = self._census()
        # BTC 두 칸이 겹쳐도 종목 수는 1 — 종목 수 2는 ETH가 함께 열린 [250,350)뿐.
        assert share_at_least(census, "symbols", "main", 2) == pytest.approx(0.1)
        assert max_level(census, "symbols", "main") == 2

    def test_symbols_multi_tf_scope(self) -> None:
        census = self._census()
        # 자기 두 칸을 동시에 든 종목은 BTC뿐이고 그 구간은 [200,300).
        assert share_at_least(census, "symbols_multi_tf", "main", 1) == pytest.approx(0.1)

    def test_intra_symbol_scope(self) -> None:
        census = self._census()
        assert share_at_least(census, "intra_symbol", "BTC", 2) == pytest.approx(0.1)
        assert share_at_least(census, "intra_symbol", "ETH", 2) == 0.0

    def test_wan108_scopes_limited_to_3sym(self) -> None:
        census = self._census()
        # 1h_3sym: BTC 1h [200,400) · ETH 1h [250,350) → 2칸 동시 = [250,350).
        assert share_at_least(census, "wan108", "1h_3sym", 2) == pytest.approx(0.1)

    def test_same_cell_overlap_propagates(self) -> None:
        rows = [_iv(BTC, "15m", 100, 300), _iv(BTC, "15m", 200, 400)]
        with pytest.raises(ValueError, match="같은 칸"):
            run_census(rows, window_start=0, window_end=1_000, symbols=[BTC])


# --------------------------------------------------------------------------- #
# 판정 (문턱은 코드에 있다)
# --------------------------------------------------------------------------- #


def _census_with_ge2_share(share2: float) -> list[CensusRow]:
    """본 census 스코프의 2칸 겹침 비율을 정확히 `share2`로 맞춘 합성 행."""
    span = 1_000_000
    dur2 = int(span * share2)
    return [
        CensusRow(
            scope_kind="cells",
            scope_key="main",
            level=0,
            duration_ms=span - dur2,
            share=(span - dur2) / span,
            window_start=0,
            window_end=span,
        ),
        CensusRow(
            scope_kind="cells",
            scope_key="main",
            level=2,
            duration_ms=dur2,
            share=dur2 / span,
            window_start=0,
            window_end=span,
        ),
    ]


class TestVerdict:
    def test_significant(self) -> None:
        text = verdict(_census_with_ge2_share(SIGNIFICANT_OVERLAP_SHARE + 0.01))
        assert "(a)" in text

    def test_negligible(self) -> None:
        text = verdict(_census_with_ge2_share(NEGLIGIBLE_OVERLAP_SHARE / 2))
        assert "(b)" in text

    def test_borderline(self) -> None:
        text = verdict(
            _census_with_ge2_share((SIGNIFICANT_OVERLAP_SHARE + NEGLIGIBLE_OVERLAP_SHARE) / 2)
        )
        assert "(c)" in text


# --------------------------------------------------------------------------- #
# CSV 왕복 · 공통 창
# --------------------------------------------------------------------------- #


class TestCsvRoundTrip:
    def test_intervals_round_trip(self, tmp_path: Path) -> None:
        rows = [_iv(BTC, "15m", 100, 300), _iv(ETH, "1h", 200, 400)]
        path = tmp_path / "intervals.csv"
        intervals_to_frame(rows).to_csv(path, index=False)
        assert intervals_from_csv(path) == rows

    def test_census_window_uniform(self) -> None:
        assert census_window([_iv(BTC, "15m", 0, 1), _iv(ETH, "1h", 2, 3)]) == WINDOW

    def test_census_window_mixed_rejected(self) -> None:
        other = IntervalRow(
            symbol=BTC,
            timeframe="15m",
            side="long",
            entry_time=0,
            exit_time=1,
            window_start=0,
            window_end=999,
        )
        with pytest.raises(ValueError, match="창이 서로 다릅니다"):
            census_window([_iv(BTC, "15m", 0, 1), other])
