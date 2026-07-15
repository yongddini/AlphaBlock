"""backtest.wan96_fill_conservatism_report 단위 테스트 (WAN-96).

3심볼×2TF×3년 실데이터 민감도 산출은 `backtest/reports/wan96_fill_conservatism*.csv`·
`wan96_fill_bias.csv`·`wan96_fill_conservatism_summary.md`(재현:
`python -m backtest.wan96_fill_conservatism_report`)로 별도 확인한다. 여기서는 결정적
합성 데이터로 보수화 레벨 배선·편향 진단 산식·리포트 렌더만 검증한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import PositionSide
from backtest.substep import SubStep, ZoneLimitStatus
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan96_fill_conservatism_report import (
    BASE_PARAMS,
    CONSERVATISM_LEVELS,
    ConservatismLevel,
    _virtual_r,
    build_bias_row,
    build_markdown,
    build_seed_spread_frame,
    build_sensitivity_frame,
    rows_to_frame,
    run_cell,
)
from backtest.zone_limit_backtest import SetupDiagnostic
from data.models import FundingRate
from strategy.models import ConfluenceParams
from strategy.order_blocks import OrderBlockDetector

_SYMBOL = "TEST/USDT:USDT"
_TIMEFRAME = "1h"


def _synthetic_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=600, seed=7)
    htf_ms = timeframe_to_ms(_TIMEFRAME)
    span = 120
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _funding_rates(df: pd.DataFrame) -> list[FundingRate]:
    start = int(df["open_time"].iloc[0])
    end = int(df["open_time"].iloc[-1])
    interval = 8 * 60 * 60_000
    return [
        FundingRate(symbol=_SYMBOL, funding_time=t, rate=0.0001)
        for t in range(start, end, interval)
    ]


def _run_cell(levels: tuple[ConservatismLevel, ...]) -> tuple[list[object], object]:
    htf, one_min = _synthetic_pair()
    rows, bias = run_cell(
        htf,
        one_min,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        funding_rates=_funding_rates(htf),
        order_block_result=OrderBlockDetector().run(htf),
        levels=levels,
    )
    return list(rows), bias


# ---------------------------------------------------- 레벨 정의


def test_baseline_level_is_the_pinned_pre_wan112_engine() -> None:
    """`baseline`은 이 리포트가 발표한 엔진 그 자체 — WAN-95 당시 결과가 재현돼야 한다.

    보수화 필드가 기준선에 스며들면 이 리포트의 기준선이 WAN-95와 달라져 비교 자체가
    무의미해진다.

    ⚠️ WAN-112로 채택 기본 오프셋이 0bp→2bp가 되면서 이 기준선은 더 이상
    `ConfluenceParams()`가 아니다. 이 리포트의 발표 수치는 **0bp에서 나왔고**, 그 엔진을
    `BASE_PARAMS`가 명시 고정한다 — 즉 이 리포트는 이제 **당시 엔진의 기록**이다.
    """
    assert ConfluenceParams(zone_limit_offset_bps=0.0) == BASE_PARAMS
    assert BASE_PARAMS.zone_limit_offset_bps == 0.0
    assert ConfluenceParams().zone_limit_offset_bps == 2.0, "채택 기본값과 갈라졌음이 의도다"
    baseline = CONSERVATISM_LEVELS[0]
    assert baseline.name == "baseline"
    assert baseline.params(seed=0) == BASE_PARAMS
    assert BASE_PARAMS.fill_penetration_bps == 0.0
    assert BASE_PARAMS.fill_dropout_rate == 0.0


def test_levels_only_vary_fill_assumptions() -> None:
    """모든 레벨은 체결 가정 3필드만 다르다 — 전략 파라미터는 건드리지 않는다.

    이슈 비고의 '파라미터 최적화 금지'를 코드로 못 박는다.
    """
    fill_fields = {"fill_penetration_bps", "fill_dropout_rate", "fill_dropout_seed"}
    default = BASE_PARAMS.model_dump()
    for level in CONSERVATISM_LEVELS:
        for seed in level.seeds:
            diff = {k for k, v in level.params(seed).model_dump().items() if v != default[k]}
            assert diff <= fill_fields, f"{level.name}이 체결 가정 밖의 필드를 바꿨다: {diff}"


def test_dropout_levels_run_multiple_seeds() -> None:
    """탈락이 있는 레벨은 여러 시드를 돈다 — 단일 시드의 운이 아님을 보여야 하므로."""
    for level in CONSERVATISM_LEVELS:
        if level.dropout_rate > 0:
            assert len(level.seeds) > 1, f"{level.name}이 시드 하나로만 돈다"
        else:
            assert level.seeds == (0,)


# ---------------------------------------------------- 가상 R (편향 진단 산식)


def _setup(*, filled: bool, tap_close: float, stop: float) -> SetupDiagnostic:
    return SetupDiagnostic(
        trigger_time=0,
        tap_bar_time=0,
        tap_close=tap_close,
        side=PositionSide.LONG,
        limit_price=tap_close,
        stop_price=stop,
        filled=filled,
        dropped=False,
        status=ZoneLimitStatus.FILLED_EXITED if filled else ZoneLimitStatus.NO_TOUCH,
    )


def _steps(*bars: tuple[float, float]) -> tuple[list[SubStep], list[int]]:
    """탭 봉(0) 다음 상위TF 봉부터의 서브스텝. `bars`는 (high, low)."""
    htf_ms = timeframe_to_ms(_TIMEFRAME)
    steps = [
        SubStep(time=htf_ms + i * 60_000, high=h, low=lo, close=lo, htf_bar_time=htf_ms)
        for i, (h, lo) in enumerate(bars)
    ]
    return steps, [s.time for s in steps]


def test_virtual_r_returns_take_profit_multiple_on_target_hit() -> None:
    # 진입 100 · 손절 90 → 1R=10, 1:1.5R 목표=115.
    steps, times = _steps((116.0, 101.0))
    value = _virtual_r(
        _setup(filled=False, tap_close=100.0, stop=90.0),
        steps,
        times,
        htf_ms=timeframe_to_ms(_TIMEFRAME),
        take_profit_r=1.5,
    )
    assert value == pytest.approx(1.5)


def test_virtual_r_returns_minus_one_on_stop_hit() -> None:
    steps, times = _steps((101.0, 89.0))
    value = _virtual_r(
        _setup(filled=False, tap_close=100.0, stop=90.0),
        steps,
        times,
        htf_ms=timeframe_to_ms(_TIMEFRAME),
        take_profit_r=1.5,
    )
    assert value == pytest.approx(-1.0)


def test_virtual_r_prefers_stop_when_both_hit_in_same_step() -> None:
    """손절·익절 동시 충족은 손절 우선 — 엔진과 같은 보수적 규칙이어야 비교가 성립한다."""
    steps, times = _steps((116.0, 89.0))
    value = _virtual_r(
        _setup(filled=False, tap_close=100.0, stop=90.0),
        steps,
        times,
        htf_ms=timeframe_to_ms(_TIMEFRAME),
        take_profit_r=1.5,
    )
    assert value == pytest.approx(-1.0)


def test_virtual_r_is_none_when_unresolved_or_invalid() -> None:
    """미해결(데이터 종료까지 미도달)·역전(종가가 손절선 너머)은 진단에서 제외한다."""
    steps, times = _steps((101.0, 99.0))  # 손절·익절 어느 쪽에도 닿지 않는 경로

    def _r(setup: SetupDiagnostic) -> float | None:
        return _virtual_r(
            setup, steps, times, htf_ms=timeframe_to_ms(_TIMEFRAME), take_profit_r=1.5
        )

    assert _r(_setup(filled=False, tap_close=100.0, stop=90.0)) is None
    # 롱인데 종가가 이미 손절선 아래 → 1R이 음수라 가상 진입이 성립하지 않는다.
    assert _r(_setup(filled=False, tap_close=89.0, stop=90.0)) is None


def test_virtual_r_ignores_the_tap_bar_itself() -> None:
    """탭 봉 종가로 진입하므로 그 봉 **안**의 움직임은 평가하지 않는다(사후예지 방지)."""
    htf_ms = timeframe_to_ms(_TIMEFRAME)
    # 탭 봉(htf_bar_time=0) 안에서 손절선을 깨지만, 진입은 그 봉이 끝난 뒤다.
    tap_bar = [SubStep(time=0, high=101.0, low=80.0, close=80.0, htf_bar_time=0)]
    after, _ = _steps((116.0, 101.0))
    steps = tap_bar + after
    value = _virtual_r(
        _setup(filled=False, tap_close=100.0, stop=90.0),
        steps,
        [s.time for s in steps],
        htf_ms=htf_ms,
        take_profit_r=1.5,
    )
    assert value == pytest.approx(1.5)  # 탭 봉 내부의 손절 관통은 무시된다


def test_build_bias_row_splits_filled_and_unfilled() -> None:
    """편향 진단은 체결군·미체결군을 같은 가상 잣대로 나눠 평균 R 차이를 낸다."""
    steps, _ = _steps((116.0, 101.0))  # 모두 익절(+1.5R)에 도달하는 경로
    setups = [
        _setup(filled=True, tap_close=100.0, stop=90.0),
        _setup(filled=False, tap_close=100.0, stop=90.0),
    ]
    row = build_bias_row(setups, steps, symbol=_SYMBOL, timeframe=_TIMEFRAME)
    assert row.filled_setups == 1 and row.unfilled_setups == 1
    assert row.filled_mean_r == pytest.approx(1.5)
    assert row.unfilled_mean_r == pytest.approx(1.5)
    assert row.mean_r_gap == pytest.approx(0.0)  # 같은 경로 → 편향 없음
    assert row.filled_resolved == 1 and row.unfilled_resolved == 1


# ---------------------------------------------------- 셀 실행·집계·렌더


def test_run_cell_emits_row_per_level_seed_and_baseline_bias() -> None:
    """레벨×시드마다 한 행이 나오고, 편향 진단은 기준선에서 1건만 나온다."""
    levels = (
        CONSERVATISM_LEVELS[0],
        ConservatismLevel(name="drop_50", dropout_rate=0.5, seeds=(0, 1)),
    )
    rows, bias = _run_cell(levels)
    assert [(r.level, r.seed) for r in rows] == [  # type: ignore[attr-defined]
        ("baseline", 0),
        ("drop_50", 0),
        ("drop_50", 1),
    ]
    assert bias is not None
    assert bias.symbol == _SYMBOL  # type: ignore[attr-defined]


def test_dropout_never_increases_fills_in_a_real_cell() -> None:
    """실제 셀에서도 탈락은 체결을 줄이기만 한다(배선 확인)."""
    levels = (
        CONSERVATISM_LEVELS[0],
        ConservatismLevel(name="drop_all", dropout_rate=1.0, seeds=(0,)),
    )
    rows, _ = _run_cell(levels)
    baseline, dropped = rows
    assert dropped.num_filled == 0  # type: ignore[attr-defined]
    assert dropped.num_dropped == baseline.num_filled  # type: ignore[attr-defined]
    assert baseline.num_dropped == 0  # type: ignore[attr-defined]


def test_sensitivity_frame_averages_seeds_before_counting_positive_symbols() -> None:
    """시드 평균을 낸 **뒤** 플러스 심볼을 센다 — 시드 하나의 운으로 넘어가면 안 된다.

    심볼 A는 시드 두 개가 +30%/−10%(평균 +10% → 플러스), 심볼 B는 +10%/−30%
    (평균 −10% → 마이너스)다. 시드별로 세면 2/2가 되어 결론이 뒤집힌다.
    """
    frame = pd.DataFrame(
        [
            {
                "symbol": sym,
                "timeframe": "15m",
                "level": "drop_50",
                "seed": seed,
                "dropout_rate": 0.5,
                "total_return": ret,
                "win_rate": 0.5,
                "max_drawdown": 0.1,
                "fill_rate": 0.2,
                "num_trades": 10,
            }
            for sym, seed, ret in [
                ("A", 0, 0.30),
                ("A", 1, -0.10),
                ("B", 0, 0.10),
                ("B", 1, -0.30),
            ]
        ]
    )
    sens = build_sensitivity_frame(frame).iloc[0]
    assert sens["positive_symbols"] == 1
    assert sens["num_symbols"] == 2
    assert sens["mean_return"] == pytest.approx(0.0)
    assert sens["worst_symbol_return"] == pytest.approx(-0.10)

    spread = build_seed_spread_frame(frame)
    row_a = spread[spread["symbol"] == "A"].iloc[0]
    assert row_a["min_return"] == pytest.approx(-0.10)
    assert row_a["max_return"] == pytest.approx(0.30)
    assert row_a["num_seeds"] == 2


def test_markdown_renders_verdict_and_limits() -> None:
    """리포트에 15m 권고·편향 진단·한계가 함께 남는다."""
    levels = (CONSERVATISM_LEVELS[0], ConservatismLevel(name="pen_5bp", penetration_bps=5.0))
    rows, bias = _run_cell(levels)
    frame = rows_to_frame(rows)  # type: ignore[arg-type]
    sensitivity = build_sensitivity_frame(frame)
    spread = build_seed_spread_frame(frame)
    bias_frame = pd.DataFrame([bias.model_dump()])  # type: ignore[attr-defined]

    md = build_markdown(frame, sensitivity, spread, bias_frame)
    assert "WAN-96" in md
    assert "python -m backtest.wan96_fill_conservatism_report" in md
    assert "체결 편향 진단" in md
    assert "15m 채택/제외 최종 권고" in md
    assert "한계" in md


def test_mechanism_section_reports_trade_drop_vs_return_drop() -> None:
    """'거래는 그대로인데 수익만 사라진다'가 표에서 계산돼 리포트에 남는다.

    이 비대칭이 제외 권고의 핵심 근거라, 숫자가 표와 어긋나면 근거가 무너진다.
    """
    frame = pd.DataFrame(
        [
            {
                "symbol": "A",
                "timeframe": "15m",
                "level": level,
                "seed": 0,
                "dropout_rate": 0.0,
                "total_return": ret,
                "win_rate": 0.5,
                "max_drawdown": 0.2,
                "fill_rate": 0.28,
                "num_trades": trades,
            }
            # 거래는 10%만 줄었는데(100→90) 수익은 16%→1%로 사라진다.
            for level, ret, trades in [("baseline", 0.16, 100), ("pen_5bp", 0.01, 90)]
        ]
    )
    md = build_markdown(
        frame, build_sensitivity_frame(frame), build_seed_spread_frame(frame), pd.DataFrame()
    )
    assert "왜 무너지는가" in md
    assert "거래 수를 10.0%만 줄이는데(100 → 90)" in md
    assert "16.00% → 1.00%" in md


def test_mechanism_section_is_omitted_without_penetration_level() -> None:
    """`pen_5bp`를 안 돌린 실행에서는 근거 문단을 지어내지 않는다."""
    frame = pd.DataFrame(
        [
            {
                "symbol": "A",
                "timeframe": "15m",
                "level": "baseline",
                "seed": 0,
                "dropout_rate": 0.0,
                "total_return": 0.16,
                "win_rate": 0.5,
                "max_drawdown": 0.2,
                "fill_rate": 0.28,
                "num_trades": 100,
            }
        ]
    )
    md = build_markdown(
        frame, build_sensitivity_frame(frame), build_seed_spread_frame(frame), pd.DataFrame()
    )
    assert "왜 무너지는가" not in md


def test_verdict_recommends_exclusion_when_conservatism_breaks_15m() -> None:
    """보수화에서 15m이 무너지면 권고가 '제외'로 뒤집힌다(결론이 표에서 계산됨).

    권고 문장을 리포트에 손으로 박아두면 재실행해도 갱신되지 않아 낡은 결론이 남는다.
    """
    frame = pd.DataFrame(
        [
            {
                "symbol": "A",
                "timeframe": "15m",
                "level": level,
                "seed": 0,
                "dropout_rate": 0.0,
                "total_return": ret,
                "win_rate": 0.5,
                "max_drawdown": 0.2,
                "fill_rate": 0.28,
                "num_trades": 50,
            }
            for level, ret in [("baseline", 0.16), ("pen_5bp_drop_50", -0.09)]
        ]
    )
    md = build_markdown(
        frame, build_sensitivity_frame(frame), build_seed_spread_frame(frame), pd.DataFrame()
    )
    assert "제외 권고" in md
    assert "채택 권고" not in md
