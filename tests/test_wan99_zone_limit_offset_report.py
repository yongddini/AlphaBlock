"""backtest.wan99_zone_limit_offset_report 단위 테스트 (WAN-99).

3심볼×2TF×3년 실데이터 스윕은 `backtest/reports/wan99_zone_limit_offset*.csv`·
`wan99_offset_plateau.csv`·`wan99_offset_oos.csv`·`wan99_zone_limit_offset_summary.md`
(재현: `python -m backtest.wan99_zone_limit_offset_report`)로 별도 확인한다. 여기서는
결정적 합성 데이터로 격자 배선·구간 분할·고원/OOS 판정 산식·리포트 렌더만 검증한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.metrics import build_metrics
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan99_zone_limit_offset_report import (
    DECISION_ASSUMPTION,
    DEFAULT_OFFSETS_BPS,
    FILL_ASSUMPTIONS,
    IS_FRACTION,
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    FillAssumption,
    _slice_segment,
    build_markdown,
    build_oos_frame,
    build_plateau_frame,
    build_sensitivity_frame,
    mean_r,
    rows_to_frame,
    run_cell,
)
from data.models import FundingRate
from strategy.models import ConfluenceParams

_SYMBOL = "TEST/USDT:USDT"
_TIMEFRAME = "1h"


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=bars, seed=7)
    htf_ms = timeframe_to_ms(_TIMEFRAME)
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _fully_covered_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    """1분봉이 상위TF **전 구간**을 덮는 쌍.

    기본 `_synthetic_pair`는 1분봉이 뒤쪽 일부만 덮어 IS 구간에 1분봉 커버가 없다 —
    그러면 IS 셋업이 통째로 평가에서 빠져(WAN-41 폴백) 구간 배선을 검증할 수 없다.
    """
    return _synthetic_pair(bars=200, span=200)


def _funding_rates(df: pd.DataFrame) -> list[FundingRate]:
    start = int(df["open_time"].iloc[0])
    end = int(df["open_time"].iloc[-1])
    interval = 8 * 60 * 60_000
    return [
        FundingRate(symbol=_SYMBOL, funding_time=t, rate=0.0001)
        for t in range(start, end, interval)
    ]


# ---------------------------------------------------- 격자 정의


def test_zero_offset_baseline_is_the_pre_wan112_engine_unchanged() -> None:
    """오프셋 0 × `baseline`은 WAN-112 이전 채택 기본값 그 자체 — WAN-95/96이 재현돼야 한다.

    체결 가정이 기준선에 스며들면 이 리포트의 기준선이 WAN-95/96과 달라져 비교 자체가
    무의미해진다.

    ⚠️ 이 격자는 **오프셋을 축으로 명시해 돌리므로**(`params(offset_bps=...)`) 채택
    기본값이 2bp로 바뀌어도 발표 수치가 움직이지 않는다 — 기준선 셀이 "기본값"에서
    "명시적 0bp"로 이름만 바뀐 것이다.
    """
    baseline = FILL_ASSUMPTIONS[0]
    assert baseline.name == "baseline"
    assert baseline.params(offset_bps=0.0, seed=0) == ConfluenceParams(zone_limit_offset_bps=0.0)
    assert ConfluenceParams().zone_limit_offset_bps == 2.0, "채택 기본값과 갈라졌음이 의도다"


def test_grid_only_varies_offset_and_fill_assumptions() -> None:
    """격자는 오프셋 + 체결 가정 4필드만 건드린다 — 전략 파라미터는 손대지 않는다.

    이슈의 '과최적화 방어'는 오프셋 하나만 고르는 실험임을 전제한다. 다른 파라미터가
    같이 움직이면 무엇이 성과를 냈는지 귀속할 수 없다.
    """
    tunable = {
        "zone_limit_offset_bps",
        "fill_penetration_bps",
        "fill_dropout_rate",
        "fill_dropout_seed",
    }
    default = ConfluenceParams().model_dump()
    for assumption in FILL_ASSUMPTIONS:
        for offset in DEFAULT_OFFSETS_BPS:
            for seed in assumption.seeds:
                params = assumption.params(offset, seed).model_dump()
                diff = {k for k, v in params.items() if v != default[k]}
                assert diff <= tunable, f"{assumption.name}이 격자 밖의 필드를 바꿨다: {diff}"


def test_offset_grid_includes_zero_and_is_ascending() -> None:
    """0bp(현행 기본값)가 격자에 있어야 오프셋의 순효과를 비교할 수 있다."""
    assert DEFAULT_OFFSETS_BPS[0] == 0.0
    assert list(DEFAULT_OFFSETS_BPS) == sorted(DEFAULT_OFFSETS_BPS)
    assert all(bps >= 0.0 for bps in DEFAULT_OFFSETS_BPS)


def test_decision_assumption_is_the_worst_case_and_runs_multiple_seeds() -> None:
    """판단 기준은 최악 가정이고, 탈락이 있으니 시드를 여러 개 돌려야 한다."""
    decision = next(a for a in FILL_ASSUMPTIONS if a.name == DECISION_ASSUMPTION)
    assert decision.penetration_bps > 0 and decision.dropout_rate > 0
    assert len(decision.seeds) > 1
    for assumption in FILL_ASSUMPTIONS:
        if assumption.dropout_rate == 0:
            assert assumption.seeds == (0,)


# ---------------------------------------------------- 구간 분할


def test_slice_segment_splits_is_and_oos_without_overlap() -> None:
    """IS/OOS는 시간으로 갈리고 겹치지 않으며, 합치면 전 구간이다.

    겹치면 OOS가 IS를 일부 다시 보는 셈이라 '아웃 오브 샘플'이라 부를 수 없다.
    """
    htf, _ = _synthetic_pair()
    full = _slice_segment(htf, SEGMENT_FULL)
    is_part = _slice_segment(htf, SEGMENT_IS)
    oos_part = _slice_segment(htf, SEGMENT_OOS)

    assert len(full) == len(htf)
    assert len(is_part) + len(oos_part) == len(htf)
    assert int(is_part["open_time"].iloc[-1]) < int(oos_part["open_time"].iloc[0])
    # IS가 앞 2/3다(경계는 시각 기준이라 봉 수는 근사).
    assert len(is_part) / len(htf) == pytest.approx(IS_FRACTION, abs=0.01)


def test_slice_segment_full_is_untouched() -> None:
    htf, _ = _synthetic_pair()
    assert _slice_segment(htf, SEGMENT_FULL) is htf


# ---------------------------------------------------- 평균 R 산식


def _trade(reason: ExitReason) -> Trade:
    return Trade(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=0.0,
        exits=[TradeFill(time=1, price=101.0, quantity=1.0, fee=0.0, reason=reason)],
        realized_pnl=1.0,
        return_pct=0.01,
    )


def _result(reasons: list[ExitReason]) -> BacktestResult:
    trades = [_trade(r) for r in reasons]
    return BacktestResult(
        config=BacktestConfig(),
        trades=trades,
        equity_curve=[],
        metrics=build_metrics(initial_capital=1.0, equities=[1.0], trades=trades),
    )


def test_mean_r_scores_stops_and_targets_by_exit_reason() -> None:
    """손절 = −1R, 고정 R 익절 = +take_profit_r — 엔진 구성상 사유만으로 R이 확정된다."""
    result = _result([ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT])
    assert mean_r(result, take_profit_r=1.5) == 0.25  # (−1.0 + 1.5) / 2
    assert mean_r(_result([ExitReason.STOP_LOSS]), take_profit_r=1.5) == -1.0
    assert mean_r(_result([ExitReason.TAKE_PROFIT]), take_profit_r=1.5) == 1.5


def test_mean_r_excludes_unresolved_trades() -> None:
    """데이터 종료까지 미청산인 거래는 R이 확정되지 않아 분모에서 빠진다."""
    result = _result([ExitReason.STOP_LOSS, ExitReason.END_OF_DATA])
    assert mean_r(result, take_profit_r=1.5) == -1.0  # 손절 1건만 센다
    assert mean_r(_result([ExitReason.END_OF_DATA]), take_profit_r=1.5) is None
    assert mean_r(_result([]), take_profit_r=1.5) is None


# ---------------------------------------------------- 고원/OOS 판정 산식


def _sensitivity(returns: dict[float, float], segment: str = SEGMENT_FULL) -> pd.DataFrame:
    """오프셋 → 평균 수익률만 지정한 최소 민감도표."""
    return pd.DataFrame(
        [
            {
                "segment": segment,
                "timeframe": "15m",
                "assumption": DECISION_ASSUMPTION,
                "offset_bps": offset,
                "mean_return": value,
                "worst_symbol_return": value,
                "positive_symbols": 3 if value > 0 else 0,
                "num_symbols": 3,
                "mean_win_rate": 0.5,
                "mean_mdd": 0.1,
                "mean_fill_rate": 0.3,
                "mean_trades": 100.0,
                "mean_r": 0.25,
            }
            for offset, value in returns.items()
        ]
    )


def test_plateau_flags_broad_peak_as_plateau() -> None:
    """이웃이 최적값의 절반 이상을 유지하면 고원 — 그 구간 전체가 같은 이야기를 한다."""
    frame = build_plateau_frame(_sensitivity({0.0: 0.01, 2.0: 0.08, 5.0: 0.10, 10.0: 0.07}))
    row = frame.iloc[0]
    assert row["best_offset_bps"] == 5.0
    assert row["best_return"] == 0.10
    assert row["neighbour_mean_return"] == pytest.approx(0.075)  # (0.08 + 0.07) / 2
    assert bool(row["is_plateau"]) is True


def test_plateau_flags_spike_as_overfit_signal() -> None:
    """이웃이 무너지는 뾰족한 봉우리는 고원이 아니다 — 한 칸 어긋나면 결론이 뒤집힌다."""
    frame = build_plateau_frame(_sensitivity({0.0: -0.05, 2.0: -0.04, 5.0: 0.10, 10.0: -0.06}))
    row = frame.iloc[0]
    assert row["best_offset_bps"] == 5.0
    assert bool(row["is_plateau"]) is False
    assert int(row["positive_offsets"]) == 1


def test_plateau_is_undefined_when_nothing_is_positive() -> None:
    """전부 마이너스면 고를 것 자체가 없다 — 고원/봉우리 판정이 의미를 잃는다."""
    frame = build_plateau_frame(_sensitivity({0.0: -0.05, 2.0: -0.03, 5.0: -0.08}))
    assert frame.iloc[0]["is_plateau"] is None
    assert int(frame.iloc[0]["positive_offsets"]) == 0


def test_oos_marks_survival_only_when_is_choice_beats_zero_offset_out_of_sample() -> None:
    """IS에서 고른 오프셋이 OOS에서 플러스이고 **0bp보다 나아야** 생존이다.

    OOS에서 플러스이기만 하면 되는 게 아니다 — 오프셋 0도 플러스라면 오프셋이 보탠 게
    없으므로 채택할 이유가 없다.
    """
    frame = pd.concat(
        [
            _sensitivity({0.0: 0.01, 5.0: 0.10}, segment=SEGMENT_IS),
            _sensitivity({0.0: 0.02, 5.0: 0.06}, segment=SEGMENT_OOS),
        ]
    )
    row = build_oos_frame(frame).iloc[0]
    assert row["is_best_offset_bps"] == 5.0
    assert row["oos_return_at_is_best"] == 0.06
    assert row["oos_return_at_zero"] == 0.02
    assert bool(row["survives_oos"]) is True


def test_oos_marks_failure_when_is_choice_collapses_out_of_sample() -> None:
    """IS 최적이 OOS에서 마이너스면 생존이 아니다 — 그게 과최적화의 정의다."""
    frame = pd.concat(
        [
            _sensitivity({0.0: 0.01, 5.0: 0.10}, segment=SEGMENT_IS),
            _sensitivity({0.0: 0.02, 5.0: -0.04}, segment=SEGMENT_OOS),
        ]
    )
    row = build_oos_frame(frame).iloc[0]
    assert row["oos_return_at_is_best"] == -0.04
    assert bool(row["survives_oos"]) is False
    # 사후 최적은 대조용으로만 남긴다(선택이 아니다).
    assert row["oos_best_offset_bps"] == 0.0


def test_oos_marks_failure_when_offset_adds_nothing_over_zero() -> None:
    """OOS에서 플러스여도 0bp보다 못하면 오프셋이 보탠 게 없다."""
    frame = pd.concat(
        [
            _sensitivity({0.0: 0.01, 5.0: 0.10}, segment=SEGMENT_IS),
            _sensitivity({0.0: 0.08, 5.0: 0.03}, segment=SEGMENT_OOS),
        ]
    )
    assert bool(build_oos_frame(frame).iloc[0]["survives_oos"]) is False


# ---------------------------------------------------- end-to-end 배선


def test_run_cell_covers_grid_and_segments() -> None:
    """셀 실행이 격자를 빠짐없이 돌고 구간·오프셋·가정을 행에 남긴다."""
    htf, one_min = _fully_covered_pair()
    offsets = (0.0, 20.0)
    assumptions = (
        FILL_ASSUMPTIONS[0],
        FillAssumption(
            name=DECISION_ASSUMPTION, penetration_bps=5.0, dropout_rate=0.5, seeds=(0, 1)
        ),
    )
    rows = run_cell(
        htf,
        one_min,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        funding_rates=_funding_rates(htf),
        offsets_bps=offsets,
        assumptions=assumptions,
    )
    assert rows
    assert {r.segment for r in rows} == {SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS}
    assert {r.offset_bps for r in rows} == set(offsets)
    assert {r.assumption for r in rows} == {a.name for a in assumptions}
    # 전 구간은 두 가정 × 오프셋 × 시드를 모두 돈다.
    full = [r for r in rows if r.segment == SEGMENT_FULL]
    assert len(full) == len(offsets) * (1 + 2)


def test_run_cell_zero_offset_baseline_matches_engine_defaults() -> None:
    """오프셋 0 × baseline 행은 기본값 그대로 돌린 결과다(기준선 오염 방지)."""
    htf, one_min = _synthetic_pair()
    rows = run_cell(
        htf,
        one_min,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        funding_rates=_funding_rates(htf),
        offsets_bps=(0.0,),
        assumptions=(FILL_ASSUMPTIONS[0],),
    )
    full = [r for r in rows if r.segment == SEGMENT_FULL]
    assert len(full) == 1
    assert full[0].penetration_bps == 0.0
    assert full[0].dropout_rate == 0.0
    assert full[0].num_dropped == 0


def test_report_frames_and_markdown_render() -> None:
    """리포트 렌더 경로가 실제 실행 결과로 끝까지 동작한다."""
    htf, one_min = _synthetic_pair()
    rows = run_cell(
        htf,
        one_min,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        funding_rates=_funding_rates(htf),
        offsets_bps=(0.0, 20.0),
        assumptions=(FILL_ASSUMPTIONS[0],),
    )
    frame = rows_to_frame(rows)
    sensitivity = build_sensitivity_frame(frame)
    plateau = build_plateau_frame(sensitivity)
    oos = build_oos_frame(sensitivity)
    markdown = build_markdown(sensitivity, plateau, oos)

    assert not frame.empty and not sensitivity.empty
    assert "# WAN-99" in markdown
    assert "python -m backtest.wan99_zone_limit_offset_report" in markdown
    assert "보수 가정에서 살아남는 오프셋이 있는가" in markdown
    assert "오프셋 채택/미채택 권고" in markdown


def test_markdown_renders_with_no_rows() -> None:
    """실행 결과가 없어도 렌더가 죽지 않는다(부분 실행·데이터 결측 대비)."""
    empty = pd.DataFrame()
    markdown = build_markdown(empty, empty, empty)
    assert "# WAN-99" in markdown
