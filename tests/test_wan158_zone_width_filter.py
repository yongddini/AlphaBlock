"""WAN-158: 존폭 필터 옵트인 배선 — 라벨이 아니라 **동작**으로 고정한다.

사용자 결정은 **B(옵트인만)** 다: 파라미터로 켜고 끌 수 있게만 하고 기본은 꺼짐이다
(`docs/decisions/wan158.md`). 그래서 이 파일이 지키는 것은 다섯이다.

1. **기본은 꺼짐이고, 꺼진 실행은 예전과 같은 후보 집합을 낸다** — 필터를 안 주면
   엔진이 ATR을 계산조차 하지 않으므로 기존 CSV가 비트 단위로 재현돼야 한다.
2. **켜면 후보가 실제로 줄어든다** — 이 저장소는 「켰다고 믿으면서 안 켜진」 사고를
   네 번 겪었다(WAN-91 펀딩 플래그 · WAN-95 `entry_mode` 라벨 · WAN-112 단위 함정 ·
   WAN-123 `none` vs `unconditional`). 그래서 **후보 집합 자체**를 본다.
3. **자는 탭 봉이 아니라 직전 확정봉의 ATR이다** — 탭 봉 ATR은 그 봉 종가를 알아야
   나오므로 룩어헤드다. 두 봉의 ATR이 **다르도록** 심은 셋업에서, 문턱을 그 사이에
   놓아 어느 봉을 읽는지 가른다.
4. **단위는 ATR 배수지 퍼센트가 아니다**(WAN-112 단위 함정의 반대 방향).
5. **종가 진입(A안)과 함께 쓰면 거부한다** — A안은 이 필드를 읽지 않으므로, 조용히
   무시하면 "필터를 켰다"고 믿는 표에 라벨만 붙는다(WAN-95의 교훈).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import (
    BASELINE_FILL,
    UNSET,
    MarketData,
    RunOutcome,
    build_params,
    build_row,
    segments_for,
)
from backtest.models import BacktestConfig
from backtest.run import Grid, build_parser, grid_from_args, iter_combos
from backtest.sweep import default_backtest_config, evaluate, timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from strategy.indicators import atr
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

_TF = "1h"
_HTF_MS = timeframe_to_ms(_TF)
_MINUTE = 60_000


# --------------------------------------------------------------------------- #
# 심어 둔 셋업 — 존폭도 ATR도 **아는 값**이라 판정 규칙을 산수로 검산할 수 있다
# --------------------------------------------------------------------------- #


def _staged_frames(*, tap_bar_range: float) -> tuple[pd.DataFrame, pd.DataFrame, OrderBlock]:
    """존 근단 100 · 무효화 90인 롱 오더블록 하나를 심는다(존폭 = 10).

    앞선 봉들은 폭이 4로 일정하고 **탭 봉만** `tap_bar_range`로 벌어진다 — 그래야
    ATR(탭 봉) ≠ ATR(직전 확정봉)이라 엔진이 어느 봉을 읽는지 가를 수 있다.
    구조는 `tests/test_zone_limit_backtest.py::_staged_long_setup`과 같다.
    """
    bars = 40
    highs = [107.0] * (bars - 1) + [105.0 + tap_bar_range / 2.0]
    lows = [103.0] * (bars - 1) + [105.0 - tap_bar_range / 2.0]
    htf = pd.DataFrame(
        {
            "open_time": [i * _HTF_MS for i in range(bars)],
            "open": [105.0 for _ in range(bars)],
            # RSI 워밍업을 벗어나도록 종가를 흔든다(NaN이면 서브스텝이 체결하지 않는다).
            "close": [105.0 + (1.0 if i % 2 else -1.0) for i in range(bars)],
            "high": highs,
            "low": lows,
            "volume": [1_000.0 for _ in range(bars)],
        }
    )
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
    tap_time = int(htf["open_time"].iloc[-1])
    # 탭 봉 안에서 99.5까지 내려가 지정가(100)를 채운 뒤 손절선 90을 건드리지 않고 오른다.
    path = [99.5] + [100.0 + i * 0.35 for i in range(60)]
    one_min = pd.DataFrame(
        {
            "open_time": [tap_time + i * _MINUTE for i in range(len(path))],
            "open": [105.0] + [p for p in path[:-1]],
            "high": [105.0] + [p + 1.0 for p in path[1:]],
            "low": path,
            "close": [100.0] + [p + 0.5 for p in path[1:]],
            "volume": [10.0 for _ in path],
        }
    )
    return htf, one_min, ob


def _staged_candidates(threshold: float | None, *, tap_bar_range: float = 4.0) -> list[_Candidate]:
    htf, one_min, ob = _staged_frames(tap_bar_range=tap_bar_range)
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=int(htf["open_time"].iloc[-1]),
        price=100.0,
        order_block=ob,
    )
    params = ConfluenceParams(
        deviation_filter=None,  # 검증 대상은 존폭 필터이지 볼린저가 아니다.
        rsi_gate_mode="none",
        max_zone_width_atr=threshold,
    )
    candidates, _ = build_zone_limit_candidates(
        htf,
        one_min,
        _TF,
        params=params,
        cfg=BacktestConfig(),
        order_block_result=OrderBlockResult(
            order_blocks=[ob], signals=[signal], retap_signals=[signal]
        ),
    )
    return candidates


def _atr_at(htf: pd.DataFrame, pos: int, length: int = 14) -> float:
    frame = htf.sort_values("open_time").reset_index(drop=True)
    return float(atr(frame, length=length).tolist()[pos])


# --------------------------------------------------------------------------- #
# 1. 기본은 꺼짐 (재현 보존)
# --------------------------------------------------------------------------- #


def test_adopted_default_now_filters_at_1_28() -> None:
    """WAN-159가 기본값을 옵트인(꺼짐) → 채택(1.28, 좁은 존만)으로 승격했다."""
    assert ConfluenceParams().max_zone_width_atr == 1.28


def test_off_reproduces_the_old_candidate_set() -> None:
    """필터를 **끄면**(`None`) 후보가 WAN-159 이전 엔진과 완전히 같다 — 합성 시장에서 본다.

    WAN-159가 기본값을 1.28로 올린 뒤로 "인자를 안 준 것"은 더 이상 꺼짐이 아니므로, 끄기는
    `max_zone_width_atr=None`을 **명시**해야 한다. 그 명시적 끄기가 ATR을 계산조차 하지 않는
    옛 경로와 비트 단위로 같은지 본다(끄기 vs 끄기 = 같아야 한다). 그리고 채택 기본값(1.28)은
    같은 시장에서 후보를 **줄인다**(라벨만 붙는 배선이면 안 준다).
    """
    htf = make_synthetic_ohlcv(timeframe=_TF, bars=600, seed=7)
    start = int(htf["open_time"].iloc[-120])
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=120 * 60, seed=11, start_time_ms=start, swing_period=180
    )
    off = ConfluenceParams(short_enabled=True, deviation_filter=None, max_zone_width_atr=None)
    cfg = default_backtest_config(_TF)

    before, stats_before = build_zone_limit_candidates(htf, one_min, _TF, params=off, cfg=cfg)
    after, stats_after = build_zone_limit_candidates(
        htf,
        one_min,
        _TF,
        params=off.model_copy(update={"max_zone_width_atr": None}),
        cfg=cfg,
    )
    assert before, "합성 데이터에서 후보가 하나도 안 나오면 이 검정이 공허하다."
    assert [(c.entry_time, c.entry_price, c.exit_price) for c in before] == [
        (c.entry_time, c.entry_price, c.exit_price) for c in after
    ]
    assert stats_before == stats_after

    # 채택 기본값(1.28)은 같은 시장에서 후보를 줄인다.
    filtered, stats_filtered = build_zone_limit_candidates(
        htf, one_min, _TF, params=off.model_copy(update={"max_zone_width_atr": 1.28}), cfg=cfg
    )
    assert stats_filtered.eligible <= stats_before.eligible


def test_build_params_unset_inherits_base_but_none_turns_off() -> None:
    """WAN-159: 미지정(`UNSET`)은 `base`를 물려받고, 명시적 `None`은 **끈다**.

    존폭 필터는 끄기가 `None`이라 `offset_bps`의 "None = 손대지 않는다" 규약을 못 쓴다 —
    센티넬로 미지정을 따로 표현한다. 이래야 「필터 끔」 라벨을 단 채 1.28로 도는 이중
    필터를 피한다.
    """
    base = ConfluenceParams(max_zone_width_atr=1.24)
    assert build_params(base=base).max_zone_width_atr == 1.24  # UNSET = 물려받음
    assert build_params(max_zone_width_atr=0.9, base=base).max_zone_width_atr == 0.9
    assert build_params(max_zone_width_atr=None, base=base).max_zone_width_atr is None  # 끄기


# --------------------------------------------------------------------------- #
# 2·3. 켜면 걸러진다 · 자는 직전 확정봉 ATR
# --------------------------------------------------------------------------- #


def test_on_actually_drops_the_setup_and_off_keeps_it() -> None:
    """같은 셋업이 문턱 하나로 살고 죽는다 — 라벨만 붙는 배선이면 실패한다."""
    htf, _, ob = _staged_frames(tap_bar_range=4.0)
    ratio = (ob.top - ob.bottom) / _atr_at(htf, len(htf) - 2)

    assert len(_staged_candidates(None)) == 1
    assert len(_staged_candidates(ratio * 1.01)) == 1  # 문턱이 존폭보다 넓다 → 통과
    assert _staged_candidates(ratio * 0.99) == []  # 조이면 이 셋업은 주문 자체가 안 걸린다


def test_boundary_is_inclusive() -> None:
    """규칙은 `존폭 ÷ ATR <= 문턱`이다(같으면 통과)."""
    htf, _, ob = _staged_frames(tap_bar_range=4.0)
    ratio = (ob.top - ob.bottom) / _atr_at(htf, len(htf) - 2)
    assert len(_staged_candidates(ratio)) == 1


def test_atr_comes_from_the_previous_closed_bar_not_the_tap_bar() -> None:
    """탭 봉 ATR을 읽으면 룩어헤드다 — 문턱을 두 값 사이에 놓아 가른다.

    탭 봉만 크게 벌려 두면 ATR(탭 봉) > ATR(직전 봉)이라 존폭 비율은 탭 봉 쪽이 **더
    작다**. 그 사이의 문턱에서 올바른 엔진(직전 봉)은 **기각**하고, 탭 봉을 읽는
    엔진은 통과시킨다.
    """
    htf, _, ob = _staged_frames(tap_bar_range=40.0)
    width = ob.top - ob.bottom
    ratio_prev = width / _atr_at(htf, len(htf) - 2)
    ratio_tap = width / _atr_at(htf, len(htf) - 1)
    assert ratio_tap < ratio_prev, "두 봉의 ATR이 같으면 이 검정이 아무것도 가르지 못한다."

    between = (ratio_tap + ratio_prev) / 2.0
    assert _staged_candidates(between, tap_bar_range=40.0) == []
    assert len(_staged_candidates(ratio_prev * 1.01, tap_bar_range=40.0)) == 1


def test_unit_is_an_atr_multiple_not_a_percent() -> None:
    """0.01은 "1%"가 아니라 "ATR의 1/100" — 퍼센트로 착각하면 전부 걸러진다.

    WAN-112가 `2.0`을 `0.0002`로 넣어 무효과를 겪은 그 함정의 반대 방향이라, 단위를
    문서가 아니라 동작으로 남긴다.
    """
    assert _staged_candidates(0.01) == []
    assert len(_staged_candidates(1000.0)) == 1


# --------------------------------------------------------------------------- #
# 판정 규칙 자체 (워밍업은 기각)
# --------------------------------------------------------------------------- #


def _zone(top: float, bottom: float) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1_000.0,
        ob_low_volume=400.0,
        ob_high_volume=600.0,
    )


def test_pass_rule_is_less_than_or_equal() -> None:
    params = ConfluenceParams(max_zone_width_atr=2.0)
    assert params.zone_width_filter_passes(_zone(102.0, 100.0), 1.0) is True  # 2.0 == 문턱
    assert params.zone_width_filter_passes(_zone(102.01, 100.0), 1.0) is False


def test_unusable_atr_is_rejected_not_waved_through() -> None:
    """워밍업(NaN)·비양수 ATR은 **기각**이다 — 통과로 두면 그 구간만 필터가 조용히 꺼진다."""
    params = ConfluenceParams(max_zone_width_atr=2.0)
    assert params.zone_width_filter_passes(_zone(101.0, 100.0), None) is False
    assert params.zone_width_filter_passes(_zone(101.0, 100.0), float("nan")) is False
    assert params.zone_width_filter_passes(_zone(101.0, 100.0), 0.0) is False


def test_off_never_rejects() -> None:
    off = ConfluenceParams(max_zone_width_atr=None)
    assert off.zone_width_filter_passes(_zone(1e9, 0.0), None) is True


def test_threshold_must_be_positive() -> None:
    """0 이하는 "필터 없음"이 아니라 무의미한 값이다 — `None`으로만 끈다."""
    with pytest.raises(ValueError):
        ConfluenceParams(max_zone_width_atr=0.0)


# --------------------------------------------------------------------------- #
# 5. 종가 진입(A안)과 함께 쓰면 거부
# --------------------------------------------------------------------------- #


def test_close_entry_engine_rejects_the_filter() -> None:
    htf = make_synthetic_ohlcv(timeframe=_TF, bars=300, seed=7)
    params = ConfluenceParams(
        entry_mode="close",
        rsi_mode="closed_bar",
        zone_limit_offset_bps=0.0,
        max_zone_width_atr=1.2,
    )
    with pytest.raises(ValueError, match="존폭 필터"):
        evaluate(htf, confluence_params=params)


def test_build_params_rejects_filter_on_close_entry() -> None:
    with pytest.raises(ValueError, match="존폭 필터"):
        build_params(entry_mode="close", max_zone_width_atr=1.24)


def test_grid_rejects_filter_on_close_entry() -> None:
    with pytest.raises(ValueError, match="존폭"):
        Grid(
            symbols=("BTC/USDT:USDT",),
            timeframes=(_TF,),
            entry_modes=("close",),
            take_profit_rs=(1.5,),
            offsets_bps=(0.0,),
            fills=(BASELINE_FILL,),
            max_zone_widths_atr=(1.24,),
        )


# --------------------------------------------------------------------------- #
# CLI 축 · 행 라벨
# --------------------------------------------------------------------------- #


def test_cli_axis_defaults_to_the_adopted_engine() -> None:
    """인자를 안 주면 축이 `(UNSET,)` — 채택 기본값(1.28)으로 도는 행이 나온다(WAN-159)."""
    args = build_parser().parse_args(["--symbol", "BTCUSDT"])
    grid = grid_from_args(args)
    assert grid.max_zone_widths_atr == (UNSET,)
    combos = iter_combos(grid)
    assert [c.max_zone_width_atr for c in combos] == [UNSET]
    # 센티넬은 build_params에서 채택 기본값으로 풀린다.
    assert build_params(max_zone_width_atr=combos[0].max_zone_width_atr).max_zone_width_atr == 1.28


def test_cli_axis_parses_none_and_numbers() -> None:
    args = build_parser().parse_args(
        ["--symbol", "BTCUSDT", "--max-zone-width-atr", "none,1.24,1.32"]
    )
    grid = grid_from_args(args)
    assert grid.max_zone_widths_atr == (None, 1.24, 1.32)
    assert [c.max_zone_width_atr for c in iter_combos(grid)] == [None, 1.24, 1.32]


@pytest.mark.parametrize("token", ["1.24,오타", "0", "-1"])
def test_cli_axis_rejects_bad_tokens(token: str) -> None:
    args = build_parser().parse_args(["--symbol", "BTCUSDT", "--max-zone-width-atr", token])
    with pytest.raises(ValueError, match="max-zone-width-atr"):
        grid_from_args(args)


def test_row_carries_the_value_that_actually_ran() -> None:
    """행에 찍히는 값은 **엔진에 넘어간 파라미터**에서 읽는다(요청 라벨이 아니다)."""
    htf = make_synthetic_ohlcv(timeframe=_TF, bars=120, seed=3)
    market = MarketData("BTC/USDT:USDT", _TF, htf, pd.DataFrame(), [])
    empty = build_result_from_trades([], BacktestConfig(), _TF)
    outcome = RunOutcome(result=empty, stats=None)
    segment = segments_for(oos=False, walkforward=0)[0]

    on = build_row(
        outcome,
        market,
        segment=segment,
        params=build_params(max_zone_width_atr=1.24),
        fill_name=BASELINE_FILL.name,
    )
    off = build_row(
        outcome,
        market,
        segment=segment,
        params=build_params(max_zone_width_atr=None),
        fill_name=BASELINE_FILL.name,
    )
    assert on.max_zone_width_atr == 1.24
    assert off.max_zone_width_atr is None
