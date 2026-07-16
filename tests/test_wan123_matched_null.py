"""backtest.wan123_matched_null 테스트 (WAN-124 2단).

이 파일이 지키는 것은 두 가지다:

1. **널이 퇴화했다는 사실 자체**(`게이트 제거 → 무력화 축 소멸`) — 이 리포트가 WAN-88을
   그대로 다시 돌리지 않고 무력화 축을 옮긴 **근거**다. 근거가 코드로 고정돼 있지 않으면
   다음 사람이 "왜 WAN-88을 안 썼지?" 하고 되돌린다.
2. **옮긴 축이 실제로 살아 있다는 것** — 풀이 실제와 같아지면 널은 자기 자신을 검정하면서도
   p값을 멀쩡히 뱉는다. 그 조용한 실패를 **동작으로** 막는다(라벨이 아니라).

실데이터 6심볼 격자 산출은 `backtest/reports/wan123_matched_null_summary.md`가 낸다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import build_params, fill_preset
from backtest.substep import SubStep, ZoneLimitStatus, simulate_zone_limit_trade
from backtest.wan70_random_control_b import (
    _RSI_GATE_DISABLED_OVERBOUGHT,
    _RSI_GATE_DISABLED_OVERSOLD,
    run_random_control_b_segment,
)
from backtest.wan88_long_only_validation import _MIN_TRADES_FOR_VERDICT as WAN88_MIN_TRADES
from backtest.wan88_long_only_validation import NULL_FILL_LEVELS as WAN88_NULL_LENSES
from backtest.wan123_fill_conservatism import LENS_NAMES as FILL_REPORT_LENSES
from backtest.wan123_matched_null import (
    LENS_NAMES,
    MIN_TRADES_FOR_VERDICT,
    NEUTRALIZED_POOL_UPDATES,
    OFFICIAL_LENS,
    STRESS_LENS,
    NullRow,
    adopted_params,
    build_conclusion,
    build_summary_markdown,
    build_verdict,
    describe_engine,
    is_significant,
    neutralized_pool_params,
    null_table,
    pool_growth_note,
    rows_from_csv,
    rows_to_frame,
    summarize_lens_dependence,
)
from strategy.models import ConfluenceParams, OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

# 롱 셋업: 존 상단(지정가)=100, 손절=90, 익절=110 — tests/test_substep.py와 같은 좌표.
_LIMIT = 100.0
_STOP = 90.0
_TP = 110.0

#: 강한 상승 시딩 → 실시간 RSI가 과매수로 유지된다. 롱 게이트(`RSI<=30`)는 **막는다**.
_OVERBOUGHT_SEED = [90.0, 95.0, 100.0, 105.0, 110.0]


def _blocked_state() -> RealtimeRsi:
    return RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3)


def _touch_steps() -> list[SubStep]:
    """지정가를 터치하고 익절까지 가는 스텝 — 게이트만 통과하면 반드시 체결된다."""
    return [
        SubStep(time=0, high=101.0, low=99.0, close=99.0, htf_bar_time=0),
        SubStep(time=60_000, high=111.0, low=100.0, close=110.0, htf_bar_time=0),
    ]


def _simulate(*, gate: str, oversold: float) -> ZoneLimitStatus:
    outcome = simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        substeps=_touch_steps(),
        rsi_state=_blocked_state(),
        rsi_oversold=oversold,
        rsi_overbought=_RSI_GATE_DISABLED_OVERBOUGHT,
        take_profit_price=_TP,
        rsi_gate_mode=gate,  # type: ignore[arg-type]
    )
    return outcome.status


# --------------------------------------------------------- 널 퇴화 (이 리포트의 근거)


def test_rsi_neutralization_moves_outcome_under_the_old_gate() -> None:
    """옛 기본값에서는 WAN-70/88의 무력화 오버라이드가 **실제로 무언가를 한다**.

    이것이 성립해야 그 널에 대조군이 존재했다 — 아래 `unconditional` 테스트의 대조축이다.
    """
    gated = _simulate(gate="extreme", oversold=30.0)
    neutralized = _simulate(gate="extreme", oversold=_RSI_GATE_DISABLED_OVERSOLD)

    assert gated is not ZoneLimitStatus.FILLED_EXITED  # RSI 과매수 → 게이트가 막는다.
    assert neutralized is ZoneLimitStatus.FILLED_EXITED  # 무력화 → 통과한다.


def test_rsi_neutralization_is_a_noop_under_the_adopted_gate() -> None:
    """🚨 이 리포트의 존재 이유 — 게이트가 없으면 무력화할 게이트도 없다.

    `rsi_gate_mode="unconditional"`(WAN-123 채택 기본값)에서 시뮬레이터는 RSI를 **읽지도
    않으므로**(단락 평가), WAN-70/88의 무력화 오버라이드가 아무것도 하지 않는다 → 널의
    풀이 실제 후보 집합과 같아진다 → 널이 자기 자신을 검정한다.

    이 테스트가 깨진다면 게이트가 되살아났다는 뜻이고, 그때는 이 모듈이 아니라 WAN-88의
    널을 써야 한다.
    """
    gated = _simulate(gate="unconditional", oversold=30.0)
    neutralized = _simulate(gate="unconditional", oversold=_RSI_GATE_DISABLED_OVERSOLD)

    assert gated is neutralized is ZoneLimitStatus.FILLED_EXITED


def test_adopted_default_is_the_gate_free_engine() -> None:
    """채택 기본값이 `unconditional`인 동안에만 위 퇴화가 성립한다(전제의 명시)."""
    assert ConfluenceParams().rsi_gate_mode == "unconditional"


# --------------------------------------------------------- 옮긴 축이 살아 있는가


def test_pool_params_equal_to_real_params_is_rejected() -> None:
    """풀 = 실제이면 널이 자기 자신을 검정한다 — 라벨이 아니라 **동작**으로 막는다.

    이 가드가 없으면 볼린저가 기본값에서 꺼지는 날(재-베이스라인) 이 리포트의 널이 조용히
    퇴화한 채 p값을 계속 뱉는다 — WAN-91/95/100/112와 같은 부류의 사고다.
    """
    params = build_params()
    with pytest.raises(ValueError, match="무력화 풀이 실제 후보 집합과"):
        run_random_control_b_segment(
            pd.DataFrame(),
            pd.DataFrame(),
            "1h",
            symbol="BTC/USDT:USDT",
            segment="IS",
            gate="baseline",
            confluence_params=params,
            pool_params=params,
        )


def test_neutralized_pool_turns_bollinger_off_and_keeps_everything_else() -> None:
    """풀 = 채택 기본값에서 **볼린저만** 끈 것. 렌즈·시드는 실제와 같아야 한다."""
    preset = fill_preset("pen_5bp")
    real = adopted_params(preset, seed=0)
    pool = neutralized_pool_params(preset, seed=0)

    assert real.deviation_filter is not None
    assert pool.deviation_filter is None
    # 체결 가정이 어긋나면 널이 규칙이 아니라 체결 보수화를 재게 된다.
    assert pool.fill_penetration_bps == real.fill_penetration_bps == preset.penetration_bps
    assert pool.rsi_gate_mode == real.rsi_gate_mode
    assert pool.retap_mode == real.retap_mode
    assert pool.zone_limit_offset_bps == real.zone_limit_offset_bps
    assert pool.take_profit_r == real.take_profit_r


def test_neutralized_updates_target_the_only_remaining_selection_rule() -> None:
    """무력화 축이 볼린저라는 것을 상수로 고정한다(축이 바뀌면 문서도 바뀌어야 한다)."""
    assert NEUTRALIZED_POOL_UPDATES == {"deviation_filter": None}


def test_adopted_params_follow_the_default_and_are_not_pinned() -> None:
    """이 리포트는 「지금 채택된 것」을 잰다 — 기본값을 고정하지 않는다(WAN-88과 같은 선택).

    고정하면 기본값이 움직여도 이 표가 옛 엔진을 계속 돌아 「지금 엣지가 있는가」에 답하지
    못한다. 고정하는 쪽은 결론을 옛 수치에 박아 둔 리포트다(`LEGACY_RSI_GATE_MODE`).
    """
    defaults = ConfluenceParams()
    params = adopted_params(fill_preset(OFFICIAL_LENS), seed=0)

    assert params.rsi_gate_mode == defaults.rsi_gate_mode
    assert params.zone_limit_offset_bps == defaults.zone_limit_offset_bps
    assert params.take_profit_r == defaults.take_profit_r
    assert "rsi_gate_mode=unconditional" in describe_engine()


# --------------------------------------------------------- 자(임계값)


def test_verdict_ruler_matches_wan70_84_88() -> None:
    """같은 자로 재야 「판정이 바뀌었다」와 「자를 바꿨다」를 구분할 수 있다."""
    assert MIN_TRADES_FOR_VERDICT == WAN88_MIN_TRADES == 20


def test_null_lens_axis_matches_wan88_and_excludes_the_stress_lens() -> None:
    """널의 렌즈 축은 WAN-88과 같다 — 스트레스 렌즈는 뺀다.

    이슈(WAN-124)는 널에도 3렌즈를 요구했지만, 탈락 추첨의 시드 잡음 위에 부트스트랩
    난수를 얹으면 p값이 「엔진이 뭘 하는가」가 아니라 「시드를 어떻게 뽑았는가」를 잰다
    (WAN-88이 아예 제외한 근거). 3렌즈 요구는 **3단 표**가 채운다 — 그쪽은 부트스트랩이
    없어 난수가 탈락 추첨 하나뿐이다.
    """
    assert LENS_NAMES == WAN88_NULL_LENSES == ("baseline", "pen_5bp")
    assert OFFICIAL_LENS == "baseline"  # WAN-104 공식 렌즈.
    assert STRESS_LENS not in LENS_NAMES
    # 3렌즈는 3단 표가 낸다 — 이슈 요구가 어디서 충족되는지 코드로 고정한다.
    assert STRESS_LENS in FILL_REPORT_LENSES


# --------------------------------------------------------- 판정 로직


def _row(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
    fill: str = OFFICIAL_LENS,
    real: float = 0.1,
    trades: int = 50,
    mean: float | None = 0.0,
    p: float | None = 0.01,
    pool: int = 120,
) -> NullRow:
    return NullRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        fill=fill,
        seed=0,
        real_total_return=real,
        real_num_trades=trades,
        real_long=trades,
        real_short=0,
        pool_size=pool,
        random_mean_return=mean,
        random_ci_low=-0.1,
        random_ci_high=0.1,
        random_p_value=p,
        iterations=200,
        bucket_fallback_count=0,
    )


def test_significance_requires_direction_not_just_p_value() -> None:
    """실제가 무작위보다 **나쁜데** p가 낮은 것은 하방이라 채택 근거가 아니다(WAN-70)."""
    assert is_significant(_row(real=0.1, mean=0.0, p=0.01))
    assert not is_significant(_row(real=-0.1, mean=0.0, p=0.01))


def test_thin_cells_are_excluded_from_the_verdict() -> None:
    thin = _row(trades=MIN_TRADES_FOR_VERDICT - 1)
    verdict = build_verdict([thin])
    assert "유효 셀 0개" in verdict
    assert "판정 불가" in verdict
    assert "표본부족" in null_table([thin], lens=OFFICIAL_LENS)


def test_verdict_reads_no_edge_when_nothing_is_significant() -> None:
    rows = [_row(p=0.9, real=-0.05), _row(symbol="ETH/USDT:USDT", p=0.7, real=-0.02)]
    assert "엣지 없다" in build_verdict(rows)
    assert "엣지 없다" in build_conclusion(rows)


def test_verdict_names_the_cells_when_only_some_are_significant() -> None:
    rows = [_row(p=0.01), _row(symbol="ETH/USDT:USDT", p=0.9, real=-0.05)]
    verdict = build_verdict(rows)
    assert "특정 TF·심볼에서만 있다" in verdict
    assert "BTC/1h/oos" in verdict


def test_lens_dependence_flags_significance_that_dies_under_penetration() -> None:
    """WAN-88 §판정의 재현 — 유의성이 「스치듯 닿은 체결」에 실려 있으면 경고한다."""
    rows = [
        _row(fill=OFFICIAL_LENS, p=0.01),
        _row(fill="pen_5bp", p=0.6, real=-0.01),
    ]
    note = summarize_lens_dependence(rows)
    assert "유의성을 잃는다" in note
    assert "BTC/1h/oos" in note


def test_lens_dependence_is_quiet_when_baseline_has_no_significance() -> None:
    rows = [_row(p=0.8, real=-0.05), _row(fill="pen_5bp", p=0.8, real=-0.06)]
    assert "체결 가정을 조이기 전부터" in summarize_lens_dependence(rows)


def test_pool_growth_note_reports_the_pool_is_larger_than_the_real_trades() -> None:
    """풀이 실제보다 크다는 것이 CSV 상의 「퇴화하지 않았다」 증거다."""
    note = pool_growth_note([_row(trades=50, pool=100)])
    assert "2.00배" in note
    assert "퇴화" in note


# --------------------------------------------------------- 왕복 · 렌더


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    """요약이 CSV와 갈라질 수 없어야 한다(`--from-csv` 경로의 계약)."""
    rows = [_row(), _row(symbol="ETH/USDT:USDT", fill=STRESS_LENS)]
    path = tmp_path / "null.csv"
    rows_to_frame(rows).to_csv(path, index=False)

    assert rows_from_csv(path) == rows


def test_summary_flags_the_stress_lens_when_it_is_explicitly_requested() -> None:
    rows = [
        _row(fill=OFFICIAL_LENS),
        _row(fill="pen_5bp"),
        _row(fill=STRESS_LENS),
    ]
    summary = build_summary_markdown(rows, csv_path=Path("x.csv"))

    assert "공식 렌즈 `baseline`" in summary
    assert "민감도 렌즈 `pen_5bp`" in summary
    # 이슈가 3렌즈를 요구했으므로 싣되, 판정에서 뺀다는 경고가 함께 있어야 한다.
    # 기본 축은 2렌즈지만 `--lenses`로 명시하면 실릴 수 있다 — 그때도 경고가 따라붙는다.
    assert "스트레스 렌즈 `pen_5bp_drop_50`" in summary
    assert "이 표의 p값으로 판정하지 말 것" in summary
    # WAN-88을 그대로 못 돌린 이유가 산출물에 남아야 한다.
    assert "WAN-88의 널을 그대로 다시 돌리지 않았다" in summary


def test_summary_survives_a_stress_lens_only_run() -> None:
    """`--lenses`로 일부만 돌려도 렌더가 죽지 않는다(격자 축은 CLI가 좁힐 수 있다)."""
    summary = build_summary_markdown([_row(fill=OFFICIAL_LENS)], csv_path=Path("x.csv"))
    assert "# WAN-124 2단" in summary
