"""backtest.wan104_offset_incremental_report 단위 테스트 (WAN-104).

3심볼×3년 실데이터 분해는 `backtest/reports/wan104_offset_incremental.csv`·
`wan104_offset_gap_distribution.csv`(재현: `python -m
backtest.wan104_offset_incremental_report`)로 별도 확인한다. 여기서는 결정적 입력으로
**분류 규칙·품질 산식·집계 가중·갭 밴드 배정**만 검증한다 — 이 리포트의 결론이 걸린 곳이
엔진이 아니라 그 네 가지이기 때문이다.
"""

from __future__ import annotations

import pytest

from backtest.models import ExitReason, PositionSide, Trade, TradeFill
from backtest.substep import ZoneLimitStatus
from backtest.wan104_offset_incremental_report import (
    CLASS_BASE,
    CLASS_INCREMENTAL,
    CLASS_LOST,
    ClassRow,
    OffsetRun,
    aggregate_classes,
    build_class_rows,
    build_gap_rows,
    classes_to_frame,
    classify_setups,
    measure_quality,
)
from backtest.zone_limit_backtest import SetupDiagnostic, _Candidate

_TP_R = 1.5


def _setup(trigger_time: int, *, filled: bool) -> SetupDiagnostic:
    """진단 레코드 하나. 분류는 `filled`와 키만 보므로 나머지는 고정값으로 둔다."""
    return SetupDiagnostic(
        trigger_time=trigger_time,
        tap_bar_time=trigger_time,
        tap_close=100.0,
        side=PositionSide.LONG,
        limit_price=100.0,
        stop_price=95.0,
        filled=filled,
        dropped=False,
        status=ZoneLimitStatus.FILLED_EXITED if filled else ZoneLimitStatus.NO_TOUCH,
        tap_index=0,
        zone_key=frozenset({trigger_time}),
    )


def _candidate(trigger_time: int, reason: ExitReason) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=trigger_time + 60_000,
        entry_price=100.0,
        exit_time=trigger_time + 120_000,
        exit_price=101.0,
        reason=reason,
        stop_price=95.0,
        zone_key=frozenset({trigger_time}),
    )


def _trade(pnl: float) -> Trade:
    return Trade(
        side=PositionSide.LONG,
        entry_time=1,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=0.0,
        exits=[
            TradeFill(time=2, price=101.0, quantity=1.0, fee=0.0, reason=ExitReason.TAKE_PROFIT)
        ],
        realized_pnl=pnl,
        return_pct=pnl / 100.0,
    )


def _run(
    offset_bps: float,
    *,
    filled: dict[int, bool],
    reasons: dict[int, ExitReason] | None = None,
    pnls: dict[int, float] | None = None,
) -> OffsetRun:
    reasons = reasons or {}
    pnls = pnls or {}
    return OffsetRun(
        offset_bps=offset_bps,
        setups={k: _setup(k, filled=v) for k, v in filled.items()},
        candidates={
            k: _candidate(k, reasons.get(k, ExitReason.TAKE_PROFIT)) for k, v in filled.items() if v
        },
        trades={k: (_candidate(k, ExitReason.TAKE_PROFIT), _trade(v)) for k, v in pnls.items()},
    )


class TestClassifySetups:
    def test_splits_base_incremental_and_lost(self) -> None:
        base = _run(0.0, filled={1: True, 2: False, 3: True, 4: False})
        other = _run(2.0, filled={1: True, 2: True, 3: False, 4: False})

        classes = classify_setups(base, other)

        assert classes[1] == CLASS_BASE
        assert classes[2] == CLASS_INCREMENTAL
        assert classes[3] == CLASS_LOST
        # 양쪽 다 미체결이면 어느 부류도 아니다 — 오프셋이 건드리지 못한 셋업이다.
        assert 4 not in classes

    def test_rejects_mismatched_setup_universe(self) -> None:
        """셋업 집합이 갈라지면 분해의 전제가 깨진 것이라 조용히 넘기지 않는다."""
        base = _run(0.0, filled={1: True})
        other = _run(2.0, filled={1: True, 2: True})

        with pytest.raises(ValueError, match="셋업 집합이 다릅니다"):
            classify_setups(base, other)


class TestMeasureQuality:
    def test_counts_only_resolved_setups(self) -> None:
        candidates = [
            _candidate(1, ExitReason.TAKE_PROFIT),
            _candidate(2, ExitReason.STOP_LOSS),
            _candidate(3, ExitReason.END_OF_DATA),
        ]

        quality = measure_quality(candidates, _TP_R)

        # 미청산(END_OF_DATA)은 R이 확정되지 않아 승패 표본에서 빠진다.
        assert quality.num_resolved == 2
        assert quality.num_wins == 1
        assert quality.win_rate == pytest.approx(0.5)
        assert quality.mean_r == pytest.approx((1.5 - 1.0) / 2)

    def test_empty_is_none_not_zero(self) -> None:
        """표본이 없을 때 0%를 내면 '전패'로 읽힌다 — 없는 것은 없다고 해야 한다."""
        quality = measure_quality([], _TP_R)

        assert quality.num_resolved == 0
        assert quality.win_rate is None
        assert quality.mean_r is None


class TestBuildClassRows:
    def test_quality_from_setups_and_pnl_from_trades(self) -> None:
        """품질은 시퀀싱 전 셋업에서, 손익은 시퀀싱 후 거래에서 — 표본이 서로 다르다."""
        base = _run(0.0, filled={1: True, 2: False}, reasons={1: ExitReason.STOP_LOSS})
        other = _run(
            2.0,
            filled={1: True, 2: True},
            reasons={1: ExitReason.STOP_LOSS, 2: ExitReason.TAKE_PROFIT},
            # 셋업 2는 체결됐지만 시퀀싱에서 밀려 거래가 되지 못했다.
            pnls={1: -50.0},
        )

        rows = build_class_rows(
            {0.0: base, 2.0: other},
            symbol="TEST/USDT:USDT",
            timeframe="1h",
            segment="full",
            take_profit_r=_TP_R,
            offsets=(2.0,),
        )

        by_class = {r.trade_class: r for r in rows}
        inc = by_class[CLASS_INCREMENTAL]
        # 거래가 0건이어도 셋업 품질은 잡힌다 — 이게 셋업 수준으로 재는 이유다.
        assert inc.num_setups == 1
        assert inc.num_trades == 0
        assert inc.win_rate == pytest.approx(1.0)
        assert inc.mean_r == pytest.approx(_TP_R)
        assert inc.pnl == pytest.approx(0.0)

        base_row = by_class[CLASS_BASE]
        assert base_row.win_rate == pytest.approx(0.0)
        assert base_row.pnl == pytest.approx(-50.0)

    def test_lost_class_has_no_pnl_share(self) -> None:
        """`lost`의 손익은 X 실행에 없는 값이라 X의 총손익으로 비중을 매기면 뜻이 없다."""
        base = _run(0.0, filled={1: True}, reasons={1: ExitReason.TAKE_PROFIT}, pnls={1: 80.0})
        other = _run(2.0, filled={1: False})

        rows = build_class_rows(
            {0.0: base, 2.0: other},
            symbol="TEST/USDT:USDT",
            timeframe="1h",
            segment="full",
            take_profit_r=_TP_R,
            offsets=(2.0,),
        )

        lost = next(r for r in rows if r.trade_class == CLASS_LOST)
        assert lost.num_setups == 1
        assert lost.pnl == pytest.approx(80.0)
        assert lost.pnl_share is None


class TestAggregateClasses:
    def test_pools_wins_instead_of_averaging_rates(self) -> None:
        """심볼별 승률을 단순 평균하면 표본 1건이 100건과 같은 무게를 갖는다."""
        rows = [
            ClassRow(
                symbol="A",
                timeframe="1h",
                segment="full",
                offset_bps=2.0,
                trade_class=CLASS_INCREMENTAL,
                num_setups=1,
                num_resolved=1,
                num_wins=1,
                win_rate=1.0,
                mean_r=1.5,
                num_trades=1,
                pnl=10.0,
                pnl_share=None,
            ),
            ClassRow(
                symbol="B",
                timeframe="1h",
                segment="full",
                offset_bps=2.0,
                trade_class=CLASS_INCREMENTAL,
                num_setups=9,
                num_resolved=9,
                num_wins=0,
                win_rate=0.0,
                mean_r=-1.0,
                num_trades=9,
                pnl=-90.0,
                pnl_share=None,
            ),
        ]

        agg = aggregate_classes(classes_to_frame(rows))

        row = agg.iloc[0]
        # 단순 평균이면 50%, 승/패를 합치면 1/10 = 10%.
        assert row["win_rate"] == pytest.approx(0.1)
        assert row["mean_r"] == pytest.approx((1.5 * 1 + -1.0 * 9) / 10)
        assert int(row["num_setups"]) == 10
        assert row["pnl"] == pytest.approx(-80.0)


class TestBuildGapRows:
    def test_assigns_setup_to_first_filling_offset(self) -> None:
        """셋업이 처음 체결된 오프셋이 그 셋업의 반등 갭 밴드다."""
        runs = {
            0.0: _run(0.0, filled={1: False, 2: False, 3: False}),
            2.0: _run(
                2.0, filled={1: True, 2: False, 3: False}, reasons={1: ExitReason.TAKE_PROFIT}
            ),
            5.0: _run(
                5.0,
                filled={1: True, 2: True, 3: False},
                reasons={1: ExitReason.TAKE_PROFIT, 2: ExitReason.STOP_LOSS},
            ),
        }

        rows = build_gap_rows(
            runs, symbol="TEST/USDT:USDT", timeframe="1h", segment="full", take_profit_r=_TP_R
        )

        by_band = {r.band: r for r in rows}
        # 셋업 1은 2bp에서 이미 체결됐으므로 5bp 밴드로 중복 계상되면 안 된다.
        assert by_band["(0, 2]"].num_setups == 1
        assert by_band["(2, 5]"].num_setups == 1
        # 격자 끝까지 안 붙은 셋업 3은 오프셋으로 살 수 있는 대상이 아니다.
        assert by_band["never"].num_setups == 1

    def test_ignores_setups_already_filled_at_zero(self) -> None:
        """0에서 체결된 셋업은 오프셋이 산 것이 아니므로 갭 분포에 들어오면 안 된다."""
        runs = {
            0.0: _run(0.0, filled={1: True}),
            2.0: _run(2.0, filled={1: True}, reasons={1: ExitReason.TAKE_PROFIT}),
        }

        rows = build_gap_rows(
            runs, symbol="TEST/USDT:USDT", timeframe="1h", segment="full", take_profit_r=_TP_R
        )

        assert all(r.num_setups == 0 for r in rows)
