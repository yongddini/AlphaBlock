"""WAN-244 용량 상한 측정 모듈의 CI-안전 단위 테스트.

무거운 후보 생성(실데이터)은 여기서 돌리지 않는다 — 판정 로직·행 모델·base_cfg 스위치·
CSV 왕복만 고정한다. 상한이 실제로 명목을 자르는 **동작**은 `tests/test_leverage_book.py`
가, 후보에 룩어헤드-안전 `adv_usd`가 실리는 것은 `tests/test_run_regression_real_data.py`
(실데이터 게이트)가, 끔=채택 셀 비트 재현은 `tests/test_book_cli.py`가 담당한다.
"""

from __future__ import annotations

import pytest

from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.wan244_capacity_cap import (
    ADV_FRACTION,
    CapRow,
    _base_cfg,
    grid_from_csv,
    grid_to_frame,
    verdict,
)


def _row(
    *,
    cap_on: bool,
    segment: str,
    total_return: float,
    scope: str = "both",
    exclude: str = "",
    num_trades: int = 100,
    max_drawdown: float = 0.2,
    adv_capped_entries: int = 0,
    first_adv_cap_equity: float | None = None,
) -> CapRow:
    return CapRow(
        cap_on=cap_on,
        scope=scope,
        segment=segment,
        exclude_symbol=exclude,
        num_cells=36,
        num_symbols=9,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=max_drawdown,
        peak_concurrency=5,
        max_concurrent_risk=0.07,
        max_open_notional_ratio=5.0,
        liquidation_events=0,
        clamped_entries=adv_capped_entries,
        adv_capped_entries=adv_capped_entries,
        first_adv_cap_equity=first_adv_cap_equity,
    )


def test_base_cfg_toggles_adv_fraction() -> None:
    """`cap_on`이면 프랙션을 얹고, 아니면 채택 base_cfg 그대로다."""
    off = _base_cfg(False)
    on = _base_cfg(True)
    assert off.risk_sizing is not None and on.risk_sizing is not None
    assert off.risk_sizing.max_notional_adv_fraction is None  # 끔 = 채택 셀 재현.
    assert on.risk_sizing.max_notional_adv_fraction == ADV_FRACTION


def test_caprow_derived_metrics() -> None:
    row = _row(cap_on=True, segment=SEGMENT_OOS_WARM, total_return=0.5, num_trades=200)
    assert row.return_over_mdd == pytest.approx(2.5)  # 0.5 / 0.2.
    row2 = _row(
        cap_on=True,
        segment=SEGMENT_OOS_WARM,
        total_return=0.5,
        num_trades=200,
        adv_capped_entries=40,
    )
    assert row2.adv_capped_rate == pytest.approx(0.2)  # 40 / 200.
    assert row2.sample_ok


def _four_segment_rows(
    *,
    off_full: float,
    on_full: float,
    off_is: float,
    on_is: float,
    on_full_capped: int,
    on_is_capped: int,
    first_full: float | None,
) -> list[CapRow]:
    """판정에 필요한 4구간(full·is·oos_warm·oos) × 끔/켬 행 — 주 수치 구간은 거의 불변."""
    return [
        _row(cap_on=False, segment=SEGMENT_FULL, total_return=off_full),
        _row(cap_on=False, segment=SEGMENT_IS, total_return=off_is),
        _row(cap_on=False, segment=SEGMENT_OOS_WARM, total_return=0.43),
        _row(cap_on=False, segment=SEGMENT_OOS, total_return=0.30),
        _row(
            cap_on=True,
            segment=SEGMENT_FULL,
            total_return=on_full,
            adv_capped_entries=on_full_capped,
            first_adv_cap_equity=first_full,
        ),
        _row(
            cap_on=True,
            segment=SEGMENT_IS,
            total_return=on_is,
            adv_capped_entries=on_is_capped,
            first_adv_cap_equity=first_full,
        ),
        _row(
            cap_on=True, segment=SEGMENT_OOS_WARM, total_return=0.43, first_adv_cap_equity=350_000.0
        ),
        _row(cap_on=True, segment=SEGMENT_OOS, total_return=0.30),
    ]


def test_verdict_detects_collapse_on_full_is() -> None:
    """착시가 사는 full·is에서 ≥10배 축소 + 발동이면 (a) 「걷어낸다」로 판정한다."""
    rows = _four_segment_rows(
        off_full=1_000_000.0,
        on_full=5.0,  # 20만배 축소.
        off_is=500_000.0,
        on_is=3.0,
        on_full_capped=9000,
        on_is_capped=5000,
        first_full=9_976.0,
    )
    out = verdict(rows)
    assert out.startswith("**(a) 용량 상한이 복리 착시를 걷어낸다")
    assert "$9,976" in out  # full 첫 발동 자본.
    assert "$350,000" in out  # oos_warm 첫 발동 자본.


def test_verdict_no_collapse_when_cap_never_binds() -> None:
    """full·is에서도 상한이 발동 안 하거나 축소가 작으면 정직하게 「안 걷힘」으로 판정한다."""
    rows = _four_segment_rows(
        off_full=1.4,
        on_full=1.4,
        off_is=1.2,
        on_is=1.2,
        on_full_capped=0,  # 발동 없음.
        on_is_capped=0,
        first_full=None,
    )
    out = verdict(rows)
    assert out.startswith("**(a) full·is에서 착시가 예상만큼 걷히지 않았다")


def test_caprow_csv_roundtrip(tmp_path: object) -> None:
    """CSV 왕복에서 빈 칸(첫 발동 자본 None)이 NaN이 아니라 None으로 복원된다(WAN-130 함정)."""
    rows = [
        _row(cap_on=False, segment=SEGMENT_IS, total_return=0.6, first_adv_cap_equity=None),
        _row(
            cap_on=True,
            segment=SEGMENT_OOS,
            total_return=0.3,
            adv_capped_entries=12,
            first_adv_cap_equity=1_000_000.0,
        ),
    ]
    path = tmp_path / "grid.csv"  # type: ignore[operator]
    grid_to_frame(rows).to_csv(path, index=False)
    restored = grid_from_csv(path)
    assert restored[0].first_adv_cap_equity is None
    assert restored[1].first_adv_cap_equity == pytest.approx(1_000_000.0)
    assert restored[1].adv_capped_entries == 12
