"""backtest.wan88_long_only_validation 단위/스모크 테스트 (WAN-88).

3심볼×4TF 전체 실데이터 산출은 `backtest/reports/wan88_*.csv`·`wan88_long_only_summary.md`
(재현: `uv run python -m backtest.wan88_long_only_validation`)로 별도 확인한다. 여기서는
결정적 합성 데이터로 핵심 계약만 검증한다:

- **검증 전용 계약** — 이 리포트가 전략 파라미터를 하나도 바꾸지 않는다(이슈 지침).
- **롱 온리** — 실제·널 양쪽에 숏이 한 건도 없다.
- **판정 임계값** — WAN-70/84와 같은 자(거래 20건, p≤0.05, 방향 조건)를 쓴다.
- **펀딩 배선** — 넘기면 반영되고, 안 넘기면 `None`으로 "미반영"이 드러난다.
- **WAN-70 하위호환** — `funding_rates` 인자 추가가 기존 결과를 바꾸지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from backtest.harness import build_config, fill_preset, pin_band_bar
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan70_random_control_b import Segment, run_random_control_b_segment
from backtest.wan71_edge_decomposition import COST_MULTIPLIERS
from backtest.wan88_long_only_validation import (
    NULL_FILL_LEVELS,
    OFFICIAL_FILL,
    PINNED_BAND_BAR,
    PINNED_OFFSET_BPS,
    PINNED_RSI_GATE_MODE,
    ContrastRow,
    CostRow,
    NullCellRow,
    _describe_move,
    adopted_params,
    build_contrast,
    build_summary_markdown,
    build_verdict,
    describe_engine,
    is_significant,
    run_cell,
    run_experiment,
    run_symbol_timeframe,
    summarize_contrast,
    summarize_fill_dependence,
)
from data.models import Candle, FundingRate
from data.storage import OhlcvStore
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

_HTF_MS = timeframe_to_ms("1h")
_MINUTE = 60_000


def _synthetic_pair(bars: int = 1200, span: int = 500) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


# --------------------------------------------------------------------------- #
# 롱 진입이 **실제로 체결되는** 결정적 픽스처
# --------------------------------------------------------------------------- #
#
# `_synthetic_pair`로는 이 리포트의 행동을 검증할 수 없다. 그 합성 시드는 숏 셋업만
# 내는 데다(`tests/test_zone_limit_backtest.py`도 같은 이유로 `short_enabled=True`를
# 명시한다), 1분봉이 상위TF와 **독립된 난수 경로**라 가격대가 어긋나 존에 닿지도
# 않는다(eligible>0인데 체결 0). 롱 온리 기본값에서는 거래가 0건이 되어 "숏이 없다",
# "펀딩이 붙는다" 같은 단언이 **공허하게 통과**한다.
#
# 그래서 강세 오더블록과 1분봉 경로를 직접 심어 **가격을 아는 상태에서** 검증한다
# (`tests/test_zone_limit_backtest.py::_staged_long_setup`와 같은 수법). 존 근단 100 ·
# 무효화 90이고, 볼린저 밴드는 존보다 위에 있어 채택 기본값(`deviation_filter` 켜짐)
# 에서도 존 경계(100)에 지정가가 걸린다 — 즉 **채택 엔진 그대로** 도는 픽스처다.


def _staged_long_inputs(
    tap_low: float = 99.0,
) -> tuple[pd.DataFrame, pd.DataFrame, OrderBlockResult]:
    """존 근단 100에 닿는 롱 셋업 하나의 상위TF·1분봉·오더블록.

    `tap_low`가 탭 봉의 저가다 — 100.0이면 **스치듯 닿고**(관통 0bp), 99.0이면 100bp
    관통한다. 이 한 손잡이로 `pen_5bp`가 걸러내는 체결을 정확히 만들어낼 수 있다.
    """
    bars = 40
    htf = pd.DataFrame(
        {
            "open_time": [i * _HTF_MS for i in range(bars)],
            "open": [105.0] * bars,
            # RSI가 워밍업을 벗어나도록 종가를 흔든다(서브스텝은 RSI가 NaN이면 체결 안 함).
            "close": [105.0 + (1.0 if i % 2 else -1.0) for i in range(bars)],
            "high": [107.0] * bars,
            "low": [103.0] * bars,
            "volume": [1_000.0] * bars,
            "closed": [True] * bars,
        }
    )
    tap_time = int(htf["open_time"].iloc[-1])
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=_HTF_MS,
        ob_volume=1_000.0,
        ob_low_volume=400.0,
        ob_high_volume=600.0,
    )
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=tap_time,
        price=100.0,
        order_block=ob,
    )
    # 탭 봉에서 존에 닿은 뒤 손절선(90)은 건드리지 않고 익절까지 상승하는 경로.
    lows = [tap_low] + [100.0 + i * 0.35 for i in range(120)]
    one_min = pd.DataFrame(
        {
            "open_time": [tap_time + i * _MINUTE for i in range(len(lows))],
            "open": [105.0] + list(lows[:-1]),
            "high": [105.0] + [lo + 1.0 for lo in lows[1:]],
            "low": lows,
            "close": [100.0] + [lo + 0.5 for lo in lows[1:]],
            "volume": [10.0] * len(lows),
            "closed": [True] * len(lows),
        }
    )
    obr = OrderBlockResult(order_blocks=[ob], signals=[signal], retap_signals=[signal])
    return htf, one_min, obr


def _staged_long_cell(
    *,
    tap_low: float = 99.0,
    funding_rates: list[FundingRate] | None = None,
    iterations: int = 5,
) -> tuple[list[NullCellRow], list[CostRow]]:
    """`_staged_long_inputs`를 채택 기본값으로 돌린 셀 산출물."""
    htf, one_min, obr = _staged_long_inputs(tap_low)
    null_rows, cost_rows, _bh = run_cell(
        htf,
        one_min,
        htf,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        order_block_result=obr,
        backtest_config=build_config("1h"),
        funding_rates=funding_rates,
        iterations=iterations,
    )
    return null_rows, cost_rows


def _staged_funding(rate: float = 0.01) -> list[FundingRate]:
    """포지션 보유 중(탭 30분 뒤)에 한 번 정산되는 펀딩비."""
    tap_time = 39 * _HTF_MS
    return [FundingRate(symbol="BTC/USDT:USDT", funding_time=tap_time + 30 * _MINUTE, rate=rate)]


def _null_row(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: Segment = "OOS",
    fill: str = OFFICIAL_FILL,
    real_total_return: float = 0.2,
    real_num_trades: int = 50,
    random_mean_return: float | None = 0.05,
    random_p_value: float | None = 0.01,
) -> NullCellRow:
    """판정 로직 테스트용 최소 행."""
    return NullCellRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        fill=fill,
        real_total_return=real_total_return,
        real_num_trades=real_num_trades,
        real_long=real_num_trades,
        real_short=0,
        pool_size=200,
        random_mean_return=random_mean_return,
        random_ci_low=-0.1,
        random_ci_high=0.1,
        random_p_value=random_p_value,
        iterations=200,
        bucket_fallback_count=0,
        fill_rate=0.5,
        eligible_setups=100,
        funding_coverage=1.0,
    )


# ------------------------------------------------------- 검증 전용 계약: 기본값을 안 바꾼다


def test_adopted_params_changes_nothing_but_fill_knobs() -> None:
    """이 리포트는 **검증 전용**이다 — 체결 가정 노브 말고는 고정 엔진 그대로여야 한다.

    이 테스트가 이슈 지침("전략 규칙·기본값은 바꾸지 않는다, 파라미터 탐색 금지")을
    코드로 고정한다. 여기가 깨지면 리포트가 "그 설정의 엣지"가 아니라 다른 무언가를
    재고 있다는 뜻이다.

    기준은 `ConfluenceParams()`(지금의 채택 기본값)가 아니라 **이 검정이 돌린 엔진**
    (오프셋 0bp + RSI 게이트 `first_tap_free` + 밴드 `tap`)이다 — WAN-112가 기본 오프셋을
    2bp로 올렸고 WAN-123이 게이트를, WAN-132가 밴드를 옮겼지만, 이 리포트의 결론은
    **그 셋 이전 엔진**에서 나왔다.
    """
    defaults = pin_band_bar(
        ConfluenceParams(
            zone_limit_offset_bps=PINNED_OFFSET_BPS,
            rsi_gate_mode=PINNED_RSI_GATE_MODE,
            max_zone_width_atr=None,
        ),
        PINNED_BAND_BAR,
    )
    params = adopted_params(fill_preset(OFFICIAL_FILL))

    fill_knobs = {"fill_penetration_bps", "fill_dropout_rate", "fill_dropout_seed"}
    changed = {
        name
        for name in ConfluenceParams.model_fields
        if getattr(params, name) != getattr(defaults, name)
    }
    assert changed <= fill_knobs

    assert params.fill_penetration_bps == 5.0
    assert params.short_enabled is False
    assert params.entry_mode == "zone_limit"
    assert params.rsi_mode == "realtime"
    assert params.take_profit_r == defaults.take_profit_r == 1.5
    assert params.zone_limit_offset_bps == 0.0


def test_baseline_fill_is_bit_identical_to_the_pinned_engine() -> None:
    """`baseline`은 이 검정이 실제로 돌린 엔진 그 자체 — 널의 대조축이 성립하려면 한 톨도
    달라선 안 된다.

    ⚠️ WAN-112(오프셋 2bp)·WAN-123(게이트 제거)·WAN-132(밴드 전환)로 그 엔진은 더 이상
    `ConfluenceParams()`가 아니다. WAN-88의 「유의 셀 0개」 판정은 **0bp + 게이트 on + 탭 봉
    밴드에서 나온 결론**이므로 셋 다 고정한다 — 특히 게이트는 거래 집합 자체를 넓히고
    밴드는 진입가를 봉내로 옮기므로, 따라가면 같은 검정이 아니게 된다.
    """
    pinned = pin_band_bar(
        ConfluenceParams(
            zone_limit_offset_bps=PINNED_OFFSET_BPS,
            rsi_gate_mode=PINNED_RSI_GATE_MODE,
            max_zone_width_atr=None,
        ),
        PINNED_BAND_BAR,
    )
    assert adopted_params(fill_preset("baseline")) == pinned
    assert PINNED_OFFSET_BPS == 0.0
    assert PINNED_RSI_GATE_MODE == "first_tap_free"
    assert PINNED_BAND_BAR == "tap"
    default_band = ConfluenceParams().deviation_filter
    assert ConfluenceParams().zone_limit_offset_bps == 2.0, "채택 기본값과 갈라졌음이 의도다"
    assert ConfluenceParams().rsi_gate_mode == "unconditional", "게이트도 갈라졌음이 의도다"
    assert default_band is not None
    assert default_band.band_bar == "intrabar_live", "밴드도 갈라졌음이 의도다"


def test_official_fill_matches_wan97_decision() -> None:
    """공식 체결 기준선은 `pen_5bp`(WAN-97 결정 1)이고, 최악 가정은 널에서 뺀다."""
    assert OFFICIAL_FILL == "pen_5bp"
    assert NULL_FILL_LEVELS == ("baseline", "pen_5bp")
    assert "pen_5bp_drop_50" not in NULL_FILL_LEVELS
    assert fill_preset(OFFICIAL_FILL).penetration_bps == 5.0
    assert fill_preset(OFFICIAL_FILL).dropout_rate == 0.0


def test_describe_engine_reports_live_defaults() -> None:
    text = describe_engine()
    assert "short_enabled=False" in text
    assert "entry_mode=zone_limit" in text
    # 지문은 **실행한 엔진**을 찍는다 — 고정한 밴드가 지문에도 드러나야 한다(WAN-132).
    assert f"band_bar={PINNED_BAND_BAR}" in text


# ------------------------------------------------------- 심볼×TF 전체 흐름


def test_run_symbol_timeframe_produces_all_outputs_per_fill() -> None:
    htf, one_min = _synthetic_pair()
    null_rows, cost_rows, buy_hold_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", iterations=10, seed=5
    )
    assert null_rows
    assert {r.fill for r in null_rows} == set(NULL_FILL_LEVELS)
    assert {r.segment for r in null_rows} <= {"IS", "OOS"}
    assert buy_hold_rows
    if cost_rows:
        assert {r.cost_multiplier for r in cost_rows} == set(COST_MULTIPLIERS)
        assert {r.fill for r in cost_rows} == set(NULL_FILL_LEVELS)


def test_null_rows_are_long_only() -> None:
    """롱 온리 기본값이므로 실제 거래에 숏이 한 건도 없어야 한다.

    널도 같은 파라미터로 만든 풀에서 뽑으므로 자동으로 롱 온리다(이슈: "롱 온리이므로
    널도 롱 온리"). **거래가 실제로 있는 픽스처로 검증한다** — 거래 0건이면 "숏이
    없다"가 공허하게 참이 되어 아무것도 지키지 못한다.
    """
    null_rows, _cost_rows = _staged_long_cell()
    assert null_rows
    for row in null_rows:
        assert row.real_num_trades >= 1
        assert row.real_short == 0
        assert row.real_long == row.real_num_trades


def test_run_symbol_timeframe_too_few_bars_returns_empty() -> None:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=10, seed=1)
    one_min = make_synthetic_ohlcv(timeframe="1m", bars=50, seed=2)
    null_rows, cost_rows, buy_hold_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h"
    )
    assert null_rows == []
    assert cost_rows == []
    assert buy_hold_rows == []


def test_cost_sensitivity_return_is_monotonic_non_increasing() -> None:
    """비용을 올리면 같은 후보의 수익은 나빠지기만 한다."""
    _null_rows, cost_rows = _staged_long_cell()
    grouped: dict[str, list[tuple[float, float]]] = {}
    for row in cost_rows:
        grouped.setdefault(row.fill, []).append((row.cost_multiplier, row.total_return))
    checked = 0
    for fill, pairs in grouped.items():
        ordered = [ret for _mult, ret in sorted(pairs)]
        if all(ret == 0.0 for ret in ordered):
            continue  # 체결이 없는 가정(pen_5bp에서 스친 체결이 빠진 경우)은 비교 대상이 아니다.
        assert ordered == sorted(ordered, reverse=True), fill
        checked += 1
    assert checked > 0


def test_pen_5bp_excludes_a_grazing_fill_but_keeps_a_penetrating_one() -> None:
    """공식 기준선이 걸러내겠다고 한 바로 그 체결을 실제로 걸러내는지 확인한다.

    WAN-97 결정 1의 근거는 "지정가를 **스치듯 닿고 되돌아선** 체결은 실거래에서 큐
    때문에 가장 안 될 체결"이라는 것이다. 존 근단이 100일 때:

    - 탭 저가 100.0 = 관통 0bp(스침) → `baseline`은 체결, **`pen_5bp`는 미체결**.
    - 탭 저가 99.0 = 관통 100bp → 두 가정 모두 체결.

    이 축이 조용히 무력화되면(예: 관통 파라미터 미배선) 공식 기준선이 `baseline`과
    같은 숫자를 내면서 "보수적으로 쟀다"고 주장하게 된다 — 그게 이 리포트의 판정을
    통째로 무의미하게 만드는 실패 방식이다.
    """
    grazing = {r.fill: r.real_num_trades for r in _staged_long_cell(tap_low=100.0)[0]}
    assert grazing["baseline"] == 1
    assert grazing["pen_5bp"] == 0

    penetrating = {r.fill: r.real_num_trades for r in _staged_long_cell(tap_low=99.0)[0]}
    assert penetrating["baseline"] == 1
    assert penetrating["pen_5bp"] == 1


# ------------------------------------------------------- 펀딩 배선


def test_funding_rates_passed_are_reflected_and_absence_is_visible() -> None:
    """펀딩을 넘기면 `funding_coverage`가 값으로 드러나고, 안 넘기면 None이다.

    이것이 이 모듈이 WAN-84와 다른 지점이다 — WAN-84는 안 넘겨서 펀딩 0으로 조용히
    지나갔고 리포트에는 그 사실이 남지 않았다(WAN-91의 조용한 실패 재발).
    """
    with_funding, _c1 = _staged_long_cell(funding_rates=_staged_funding())
    without, _c2 = _staged_long_cell()

    assert with_funding and without
    assert all(r.funding_coverage is None for r in without)
    paid = {r.fill: r for r in with_funding}
    assert paid["baseline"].funding_coverage == 1.0


def test_long_only_funding_makes_returns_worse() -> None:
    """롱은 펀딩을 **지불하는 쪽**이므로 양의 펀딩비를 붙이면 수익이 나빠진다.

    부호가 반대로 배선되면(롱이 펀딩을 받는 것으로) 롱 온리 수익률이 부풀려지는데,
    이 리포트의 결론이 바로 그 수익률에 걸려 있다.
    """
    paid = {r.fill: r for r in _staged_long_cell(funding_rates=_staged_funding())[0]}
    free = {r.fill: r for r in _staged_long_cell()[0]}

    assert free["baseline"].real_num_trades == 1
    assert paid["baseline"].real_num_trades == 1
    assert paid["baseline"].real_total_return < free["baseline"].real_total_return


def test_wan70_segment_unchanged_when_funding_not_passed() -> None:
    """WAN-88이 추가한 `funding_rates` 인자는 **기본값에서 기존 결과를 바꾸지 않는다**.

    WAN-70 CSV가 그대로 재현돼야 두 리포트를 나란히 놓고 읽을 수 있다. 거래가 실제로
    있는 픽스처로 확인한다 — 거래 0건끼리 비교하면 아무것도 지키지 못한다.
    """
    htf, one_min, obr = _staged_long_inputs()
    cfg = build_config("1h")

    implicit = run_random_control_b_segment(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        gate="off",
        confluence_params=ConfluenceParams(
            max_zone_width_atr=None
        ),  # 필터는 검증 대상 아님(WAN-159)
        order_block_result=obr,
        backtest_config=cfg,
        iterations=10,
        seed=70,
    )
    explicit_none = run_random_control_b_segment(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        gate="off",
        confluence_params=ConfluenceParams(
            max_zone_width_atr=None
        ),  # 필터는 검증 대상 아님(WAN-159)
        order_block_result=obr,
        backtest_config=cfg,
        iterations=10,
        seed=70,
        funding_rates=None,
    )
    with_funding = run_random_control_b_segment(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        gate="off",
        confluence_params=ConfluenceParams(
            max_zone_width_atr=None
        ),  # 필터는 검증 대상 아님(WAN-159)
        order_block_result=obr,
        backtest_config=cfg,
        iterations=10,
        seed=70,
        funding_rates=_staged_funding(),
    )

    assert implicit.real_num_trades == 1
    assert implicit == explicit_none
    # 인자를 실제로 쓰는지 반대편도 확인한다 — 안 그러면 위 동일성은 "인자가 조용히
    # 무시된다"와 구분되지 않는다.
    assert with_funding.real_total_return < implicit.real_total_return


# ------------------------------------------------------- 판정 임계값 (WAN-70/84와 같은 자)


def test_is_significant_requires_direction_not_just_p_value() -> None:
    """실제가 무작위 평균보다 **나쁜데** p가 낮은 것은 하방 엣지 — 채택 근거가 아니다."""
    good = _null_row(real_total_return=0.2, random_mean_return=0.05, random_p_value=0.01)
    downside = _null_row(real_total_return=-0.2, random_mean_return=0.05, random_p_value=0.01)
    weak = _null_row(real_total_return=0.2, random_mean_return=0.05, random_p_value=0.5)
    assert is_significant(good)
    assert not is_significant(downside)
    assert not is_significant(weak)


def test_verdict_excludes_thin_cells_and_says_how_many() -> None:
    """거래 20건 미만 셀은 판정에서 빼고 제외 수를 밝힌다(WAN-70/84와 동일 임계값)."""
    rows = [
        _null_row(real_num_trades=50, random_p_value=0.5),
        _null_row(timeframe="1d", real_num_trades=3, random_p_value=0.01),
    ]
    verdict = build_verdict(rows)
    assert "유효 셀 1개" in verdict
    assert "1개 제외" in verdict
    assert "**엣지 없다**" in verdict


def test_verdict_all_significant_says_edge_exists() -> None:
    rows = [_null_row(random_p_value=0.01), _null_row(timeframe="4h", random_p_value=0.02)]
    assert "**엣지 있다**" in build_verdict(rows)


def test_verdict_partial_lists_the_cells() -> None:
    rows = [_null_row(random_p_value=0.01), _null_row(timeframe="4h", random_p_value=0.9)]
    verdict = build_verdict(rows)
    assert "**특정 TF·심볼에서만 있다**" in verdict
    assert "BTC/1h/OOS" in verdict


def test_verdict_is_scoped_to_one_fill() -> None:
    """판정은 체결 가정별로 따로 낸다 — 섞으면 공식 기준선 판정이 오염된다."""
    rows = [
        _null_row(fill="baseline", random_p_value=0.01),
        _null_row(fill="pen_5bp", random_p_value=0.9),
    ]
    assert "**엣지 있다**" in build_verdict(rows, fill="baseline")
    assert "**엣지 없다**" in build_verdict(rows, fill="pen_5bp")


def test_verdict_no_eligible_cells_is_undecidable() -> None:
    rows = [_null_row(real_num_trades=2)]
    assert "**판정 불가**" in build_verdict(rows)


# ------------------------------------------------------- 체결 가정 의존성(핵심 분석)


def test_fill_dependence_flags_significance_that_only_survives_at_baseline() -> None:
    """`baseline`에서만 유의한 셀은 "스치듯 닿은 체결에 기댄 유의성"으로 지목돼야 한다.

    이 문단이 이 리포트의 핵심 발견이다 — 이게 빠지면 대조표의 "유의 셀 0→4"만 읽고
    "롱 온리가 엣지를 만들었다"는 정반대 결론으로 인용된다.
    """
    rows = [
        # 거래는 거의 안 줄었는데(100→94) 유의성이 사라지는 셀.
        _null_row(timeframe="15m", fill="baseline", real_num_trades=100, random_p_value=0.01),
        _null_row(timeframe="15m", fill="pen_5bp", real_num_trades=94, random_p_value=0.30),
    ]
    text = summarize_fill_dependence(rows)
    assert "유의성이 전부 체결 가정에 기대고 있다" in text
    assert "BTC/15m/OOS" in text
    assert "전부 **15m**" in text
    assert "6.0%" in text  # (100-94)/100


def test_fill_dependence_says_nothing_alarming_when_significance_survives() -> None:
    rows = [
        _null_row(fill="baseline", random_p_value=0.01),
        _null_row(fill="pen_5bp", random_p_value=0.01),
    ]
    text = summarize_fill_dependence(rows)
    assert "체결 가정에 기대고 있지 않다" in text


def test_fill_dependence_handles_no_baseline_significance() -> None:
    rows = [
        _null_row(fill="baseline", random_p_value=0.9),
        _null_row(fill="pen_5bp", random_p_value=0.9),
    ]
    assert "유의한 셀이 없다" in summarize_fill_dependence(rows)


def test_fill_dependence_ignores_thin_cells() -> None:
    """표본 부족 셀은 판정에서 빠지므로 이 분석에도 들어오면 안 된다."""
    rows = [
        _null_row(real_num_trades=3, fill="baseline", random_p_value=0.01),
        _null_row(real_num_trades=3, fill="pen_5bp", random_p_value=0.9),
    ]
    assert "유의한 셀이 없다" in summarize_fill_dependence(rows)


# ------------------------------------------------------- WAN-84 대조표


def _wan84_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "BTC/USDT:USDT",
                "timeframe": "1h",
                "segment": "OOS",
                "gate": "new_engine",
                "real_total_return": 0.05,
                "real_num_trades": 92,
                "random_mean_return": 0.10,
                "random_p_value": 0.595,
            },
            {
                "symbol": "ETH/USDT:USDT",
                "timeframe": "1h",
                "segment": "OOS",
                "gate": "new_engine",
                "real_total_return": 0.28,
                "real_num_trades": 116,
                "random_mean_return": 0.05,
                "random_p_value": 0.01,
            },
        ]
    )


def test_build_contrast_joins_on_cell_and_marks_moves() -> None:
    rows = [
        _null_row(symbol="BTC/USDT:USDT", fill="baseline", random_p_value=0.01),
        _null_row(symbol="ETH/USDT:USDT", fill="baseline", random_p_value=0.9),
    ]
    contrast = build_contrast(rows, _wan84_frame())
    by_symbol = {r.symbol: r for r in contrast}

    btc = by_symbol["BTC/USDT:USDT"]
    assert btc.wan84_significant is False and btc.wan88_significant is True
    assert _describe_move(btc.wan84_significant, btc.wan88_significant) == "**무의미 → 유의**"

    eth = by_symbol["ETH/USDT:USDT"]
    assert eth.wan84_significant is True and eth.wan88_significant is False
    assert _describe_move(eth.wan84_significant, eth.wan88_significant) == "유의 → 무의미"


def test_build_contrast_uses_baseline_fill_not_official() -> None:
    """대조는 WAN-84와 **같은 체결 가정**(baseline)으로 맞춘다 — 아니면 "숏을 뺐더니"와
    "체결을 조였더니"가 섞인다."""
    rows = [
        _null_row(fill="baseline", real_total_return=0.5),
        _null_row(fill="pen_5bp", real_total_return=0.1),
    ]
    contrast = build_contrast(rows, _wan84_frame())
    btc = next(r for r in contrast if r.symbol == "BTC/USDT:USDT")
    assert btc.wan88_total_return == 0.5


def test_build_contrast_thin_wan84_cell_is_not_significant() -> None:
    """WAN-84 쪽도 같은 자(20건)로 잰다 — p가 낮아도 표본이 얕으면 유의가 아니다."""
    frame = _wan84_frame()
    frame.loc[0, "real_num_trades"] = 3
    frame.loc[0, "random_p_value"] = 0.01
    contrast = build_contrast([_null_row(fill="baseline")], frame)
    btc = next(r for r in contrast if r.symbol == "BTC/USDT:USDT")
    assert btc.wan84_significant is False


def test_build_contrast_without_wan84_csv_is_graceful() -> None:
    contrast = build_contrast([_null_row(fill="baseline")], pd.DataFrame())
    assert len(contrast) == 1
    assert contrast[0].wan84_significant is None
    assert "대조 가능한 셀이 없다" in summarize_contrast(contrast)


def test_summarize_contrast_counts_both_sides() -> None:
    rows = [
        _null_row(symbol="BTC/USDT:USDT", fill="baseline", random_p_value=0.01),
        _null_row(symbol="ETH/USDT:USDT", fill="baseline", random_p_value=0.9),
    ]
    text = summarize_contrast(build_contrast(rows, _wan84_frame()))
    assert "WAN-84(숏 포함) **1개**" in text
    assert "WAN-88(롱 온리) **1개**" in text
    assert "새로 유의해진 셀: BTC/1h/OOS" in text
    assert "유의성을 잃은 셀: ETH/1h/OOS" in text


def test_describe_move_handles_unknown() -> None:
    assert _describe_move(None, True) == "—"
    assert _describe_move(True, None) == "—"
    assert _describe_move(True, True) == "유의 유지"
    assert _describe_move(False, False) == "무의미 유지"


# ------------------------------------------------------- 리포트 렌더


def test_summary_markdown_contains_required_sections() -> None:
    """완료 기준: 셀별 p값·유효 셀 수·대조표·결론이 리포트에 들어간다."""
    rows = [
        _null_row(fill="baseline", random_p_value=0.9),
        _null_row(fill="pen_5bp", random_p_value=0.9),
    ]
    contrast = build_contrast(rows, _wan84_frame())
    md = build_summary_markdown(
        rows,
        [],
        [],
        contrast,
        null_report_path=Path("a.csv"),
        cost_report_path=Path("b.csv"),
        buy_hold_report_path=Path("c.csv"),
        contrast_report_path=Path("d.csv"),
    )
    assert "매칭 널 — 셀별 결과" in md
    assert "WAN-84(숏 포함) 대조표" in md
    assert "## 결론" in md
    assert "pen_5bp" in md
    assert "uv run python -m backtest.wan88_long_only_validation" in md
    # 결론은 세 판정 중 하나를 명시해야 한다.
    assert any(v in md for v in ("**엣지 없다**", "**엣지 있다**", "**판정 불가", "**일부 셀"))


def test_summary_marks_thin_cells_in_table() -> None:
    md = build_summary_markdown(
        [_null_row(real_num_trades=3, random_p_value=0.01)],
        [],
        [],
        [],
        null_report_path=Path("a.csv"),
        cost_report_path=Path("b.csv"),
        buy_hold_report_path=Path("c.csv"),
        contrast_report_path=Path("d.csv"),
    )
    assert "표본부족" in md


# ------------------------------------------------------- run_experiment 병렬 재현성


def _populate_synthetic_store(db_path: Path, symbols: tuple[str, ...]) -> None:
    with OhlcvStore(db_path) as store:
        for i, symbol in enumerate(symbols):
            htf = make_synthetic_ohlcv(timeframe="1h", bars=1200, seed=7 + i)
            htf_ms = timeframe_to_ms("1h")
            start = int(htf["open_time"].iloc[-500])
            minutes = 500 * (htf_ms // 60_000)
            one_min = make_synthetic_ohlcv(
                timeframe="1m", bars=minutes, seed=11 + i, start_time_ms=start, swing_period=180
            )
            candles = [
                Candle(
                    symbol,
                    "1h",
                    int(row.open_time),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    bool(row.closed),
                )
                for row in htf.itertuples(index=False)
            ]
            candles += [
                Candle(
                    symbol,
                    "1m",
                    int(row.open_time),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    bool(row.closed),
                )
                for row in one_min.itertuples(index=False)
            ]
            store.upsert_candles(candles)


def test_run_experiment_parallel_jobs_matches_sequential(tmp_path: Path) -> None:
    db_path = tmp_path / "ohlcv.db"
    symbols = ("BTC/USDT:USDT", "ETH/USDT:USDT")
    _populate_synthetic_store(db_path, symbols)

    kwargs = dict(
        db_path=db_path,
        symbols=symbols,
        timeframes=("1h",),
        years=1.0,
        iterations=15,
        cache_dir=None,
    )
    sequential = run_experiment(jobs=1, **kwargs)  # type: ignore[arg-type]
    parallel = run_experiment(jobs=2, **kwargs)  # type: ignore[arg-type]

    assert sequential.null_rows == parallel.null_rows
    assert sequential.cost_rows == parallel.cost_rows
    assert sequential.buy_hold_rows == parallel.buy_hold_rows


def test_run_experiment_missing_db_returns_empty(tmp_path: Path) -> None:
    result = run_experiment(db_path=tmp_path / "nope.db", symbols=("BTC/USDT:USDT",))
    assert result.null_rows == []
    assert result.cost_rows == []
    assert result.buy_hold_rows == []


def test_contrast_row_is_frozen() -> None:
    row = ContrastRow(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="OOS",
        wan84_total_return=None,
        wan84_num_trades=None,
        wan84_p_value=None,
        wan84_significant=None,
        wan88_total_return=None,
        wan88_num_trades=None,
        wan88_p_value=None,
        wan88_significant=None,
    )
    with pytest.raises(ValidationError):
        row.symbol = "ETH/USDT:USDT"  # type: ignore[misc]
