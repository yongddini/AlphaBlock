"""WAN-375 Phase 1: 보유 구간 경로 관측 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것:

1. **관측은 순수하다** — `observe_hold_path`를 켜도 후보·체결·청산이 하나도 안 움직인다
   (익절 끈 판 · 켠 판 둘 다). 관측이 대상을 바꾸면 그 순간 이 측정은 무효다.
2. **청산 스텝을 뺀다** — `mfe_r_pre_exit`·목표 도달·밴드 무너짐은 청산 스텝을 안 본다
   (같은 스텝 손절 우선이라 그 스텝의 고가로는 익절이 안 됐다). 손으로 만든 경로로 확인한다.
3. **익절 켠 판 복원이 채택 엔진과 같다**(검산 (b)) — 진입 집합 동일 · 청산 사유·시각 동일.
4. **걸러내기 구간이 판정을 안 바꾼다** — `hold_break_screen`을 없앤 판(매 스텝 정확 판정)과
   같은 결과가 나오고, 걸러내기가 실제로 조회를 줄였다(공허 방지). 닫힌 해 자체도 속성
   테스트로 건다(구간 안이면 밴드가 문턱의 유리한 쪽).
5. **봉 안에서 잘라도 관측이 비트 동일하다** — WAN-377의 절단 자를 그대로 쓴다.
6. **판정이 지어지지 않는다** — 표본 미달 무리는 부호가 없고, 스코프가 하나라도 갈리면 「혼합」이다.
7. **스코프가 이름대로다** — 「첫 탭만」 ≡ `retap_mode="once"`의 셋업 · 「존별 첫 체결」은 존마다
   가장 이른 체결 하나 · 탭 표의 「체결」 수 ≡ 셋업 수 · 탭 관측도 순수하다.
"""

from __future__ import annotations

import dataclasses
import math
import random
from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.substep import (
    HoldPathProbe,
    SubStep,
    ZoneLimitStatus,
    simulate_zone_limit_trade,
)
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan375_conditional_rr import (
    ARM_BAND,
    ARM_ZONE_TOP,
    OUTCOME_FILLED,
    OUTCOME_NEVER_RESTED,
    SCOPE_ALL,
    SCOPE_FIRST,
    SCOPES,
    VERDICT_GO,
    VERDICT_MIN_N,
    VERDICT_MIXED,
    VERDICT_SIMILAR,
    CellResult,
    CostRates,
    GapTest,
    SetupObs,
    arm_params,
    band_exit_effects,
    band_rows,
    breakeven_win_rate,
    cost_r,
    derive_tp_on,
    gap_tests,
    observations_from_candidates,
    pct_rows,
    reach_rows,
    reconstruction_mismatches,
    render_summary,
    setups_frame,
    tap_observations,
    tap_outcome_rows,
    taps_frame,
    uncensored_params,
    verdict_for,
    verdict_for_scope,
)
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    UnrestedTap,
    _IntrabarLiveLimit,
    build_zone_limit_candidates,
)
from data.models import timeframe_to_ms
from strategy.models import ConfluenceParams, OrderBlockDirection
from strategy.realtime_band import RealtimeBand
from strategy.realtime_rsi import RealtimeRsi
from tests.test_wan377_intrabar_cut_invariance import (
    _MIN_MS,
    _SYMBOL,
    _SYNTHETIC_TF,
    _engine_params,
    aggregate_1m,
    cut_world_intrabar,
    intrabar_cuts_for,
)

#: 볼린저(채택 폭 2σ)를 켠 채 손절·밴드 무너짐이 실제로 나는 합성 세계. 기본 WAN-377 픽스처는
#: 너무 매끄러워 볼린저가 후보를 전부 걸러 내거나 전부 익절로 끝나 이 테스트가 공허해진다.
_SEED, _SWING, _NOISE, _HTF_BARS = 17, 40, 0.04, 400


def _minutes() -> pd.DataFrame:
    htf_ms = timeframe_to_ms(_SYNTHETIC_TF)
    return make_synthetic_ohlcv(
        timeframe="1m",
        bars=_HTF_BARS * (htf_ms // _MIN_MS),
        seed=_SEED,
        swing_period=_SWING,
        noise=_NOISE,
    ).assign(symbol=_SYMBOL, timeframe="1m")


def _band_params() -> ConfluenceParams:
    """WAN-377 합성 파라미터에 **채택 볼린저**(봉내 라이브 · 2σ)를 되돌린 것."""
    return _engine_params().model_copy(
        update={"deviation_filter": ConfluenceParams().deviation_filter}
    )


def _build(
    htf: pd.DataFrame, minutes: pd.DataFrame, params: ConfluenceParams, *, observe: bool
) -> list[Any]:
    kwargs: dict[str, Any] = {}
    if observe:
        kwargs = {"observe_hold_path": True, "hold_path_target_r": params.take_profit_r}
    candidates, _stats = build_zone_limit_candidates(
        htf,
        minutes,
        _SYNTHETIC_TF,
        params=params,
        cfg=harness.build_config(_SYNTHETIC_TF),
        **kwargs,
    )
    return candidates


def _strip(candidates: list[Any]) -> list[Any]:
    return [dataclasses.replace(c, hold_path=None) for c in candidates]


@pytest.fixture(scope="module")
def world() -> tuple[pd.DataFrame, pd.DataFrame]:
    minutes = _minutes()
    return aggregate_1m(minutes, _SYNTHETIC_TF, allow_partial=False), minutes


# --------------------------------------------------------------------------- #
# 1 · 관측은 순수하다
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("take_profit_on", [False, True])
def test_observation_moves_nothing(
    world: tuple[pd.DataFrame, pd.DataFrame], take_profit_on: bool
) -> None:
    htf, minutes = world
    params = _band_params()
    if not take_profit_on:
        params = uncensored_params(params)
    plain = _build(htf, minutes, params, observe=False)
    observed = _build(htf, minutes, params, observe=True)
    assert plain, "후보가 없어 이 테스트는 아무것도 안 지켰다."
    assert all(c.hold_path is None for c in plain)
    assert all(c.hold_path is not None for c in observed)
    assert _strip(observed) == plain


# --------------------------------------------------------------------------- #
# 2 · 청산 스텝을 뺀다 (손으로 만든 경로)
# --------------------------------------------------------------------------- #


class _StubProvider:
    """고정 지정가 100 · 손절 99 · 익절 없음, 종가 99.5 미만이면 「밴드 무너짐」인 공급자."""

    def __init__(self) -> None:
        self.probes = 0

    def commit(self, closed_price: float) -> None:
        return None

    def limit_price(self, live_price: float) -> float | None:
        return 100.0

    def probe_limit(self, live_price: float) -> float | None:
        self.probes += 1
        return None if live_price < 99.5 else 100.0

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
        return 99.0, None


def _steps() -> list[SubStep]:
    rows = [
        (100.2, 99.9, 100.1),  # 0: 체결(저가 ≤ 100) · 고가 100.2
        (102.0, 100.0, 101.5),  # 1: 1.5R(101.5)과 2R(102) 도달
        (100.5, 99.3, 99.4),  # 2: 손절 아님 · 종가 99.4 < 99.5 → 밴드 무너짐(−0.6R)
        (103.0, 98.9, 99.0),  # 3: 손절 스텝 — 고가 103은 청산 스텝이라 청산 전 MFE에 안 든다
    ]
    return [
        SubStep(time=i * 60_000, high=h, low=lo, close=c, htf_bar_time=0)
        for i, (h, lo, c) in enumerate(rows)
    ]


def _simulate(provider: Any, **kwargs: Any) -> Any:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        live_limit=provider,
        stop_price=99.0,
        substeps=_steps(),
        rsi_state=RealtimeRsi(length=14),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        rsi_gate_mode="unconditional",
        **kwargs,
    )


def test_pre_exit_path_excludes_the_exit_step() -> None:
    outcome = _simulate(_StubProvider(), observe_hold_path=True, hold_path_target_r=1.5)
    assert outcome.status is ZoneLimitStatus.FILLED_EXITED
    assert outcome.exit_time == 3 * 60_000
    assert outcome.mfe_r == pytest.approx(3.0)  # 기존 필드는 청산 스텝 고가까지 본다.
    probe = outcome.hold_path
    assert isinstance(probe, HoldPathProbe)
    assert probe.mfe_r_pre_exit == pytest.approx(2.0)
    assert probe.target_reach_time == 1 * 60_000
    assert probe.band_break_time == 2 * 60_000
    assert probe.band_break_r == pytest.approx(-0.6)
    assert probe.band_break_mfe_r == pytest.approx(2.0)


def test_same_step_as_stop_is_not_a_reach() -> None:
    """목표가 **손절 스텝에만** 닿았으면 도달이 아니다(엔진은 손절을 이기게 한다)."""
    outcome = _simulate(_StubProvider(), observe_hold_path=True, hold_path_target_r=2.5)
    assert outcome.hold_path.target_reach_time is None


def test_break_is_not_checked_on_the_entry_step() -> None:
    """진입 스텝 종가는 방금 체결한 지정가를 낸 표본이라 규칙 3을 다시 묻지 않는다."""
    provider = _StubProvider()
    _simulate(provider, observe_hold_path=True)
    # 스텝 1·2만 조회된다(0 = 진입 · 3 = 청산). 무너짐 뒤로는 더 묻지 않는다.
    assert provider.probes == 2


def test_screen_skips_probes_without_changing_the_answer() -> None:
    exact = _simulate(_StubProvider(), observe_hold_path=True, hold_path_target_r=1.5)
    screened_provider = _StubProvider()

    def screen() -> tuple[float, float]:
        return (99.5, math.inf)

    screened_provider.hold_break_screen = screen  # type: ignore[attr-defined]
    screened = _simulate(screened_provider, observe_hold_path=True, hold_path_target_r=1.5)
    assert screened == exact
    assert screened_provider.probes == 1  # 스텝 1(101.5)은 구간 안이라 조회를 건너뛰었다.


def test_observation_is_rejected_without_a_probe_provider() -> None:
    with pytest.raises(ValueError, match="observe_hold_path"):
        simulate_zone_limit_trade(
            direction=OrderBlockDirection.BULLISH,
            limit_price=100.0,
            stop_price=99.0,
            substeps=_steps(),
            rsi_state=RealtimeRsi(length=14),
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            observe_hold_path=True,
        )
    with pytest.raises(ValueError, match="hold_path_target_r"):
        _simulate(_StubProvider(), hold_path_target_r=1.5)


def test_candidate_builder_rejects_non_live_band(world: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    htf, minutes = world
    with pytest.raises(ValueError, match="intrabar_live"):
        _build(htf, minutes, _engine_params(), observe=True)  # 볼린저 없음 = 봉내 밴드 없음.
    with pytest.raises(ValueError, match="observe_hold_path"):
        build_zone_limit_candidates(
            htf,
            minutes,
            _SYNTHETIC_TF,
            params=_band_params(),
            cfg=harness.build_config(_SYNTHETIC_TF),
            hold_path_target_r=1.5,
        )


# --------------------------------------------------------------------------- #
# 3 · 익절 켠 판 복원 ≡ 채택 엔진 (합성)
# --------------------------------------------------------------------------- #


def _rows(candidates: list[Any]) -> list[SetupObs]:
    return observations_from_candidates(
        candidates, symbol="X", timeframe=_SYNTHETIC_TF, boundary_ms=0
    )


def test_tp_on_reconstruction_matches_the_engine(world: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    htf, minutes = world
    params = _band_params()
    adopted = _build(htf, minutes, params, observe=False)
    rows = _rows(_build(htf, minutes, uncensored_params(params), observe=True))
    reasons = {c.reason for c in adopted}
    assert ExitReason.TAKE_PROFIT in reasons and ExitReason.STOP_LOSS in reasons, "공허한 대조"
    assert reconstruction_mismatches(adopted, rows) == (0, 0)
    assert any(r.band_break_time is not None for r in rows), "밴드 무너짐이 없어 공허하다."


def test_reconstruction_catches_a_wrong_exit(world: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """돌연변이 — 복원을 일부러 틀리게 하면 대조가 실제로 문다."""
    htf, minutes = world
    params = _band_params()
    adopted = _build(htf, minutes, params, observe=False)
    rows = _rows(_build(htf, minutes, uncensored_params(params), observe=True))
    broken = [dataclasses.replace(r, target_reach_time=None) for r in rows]
    _entries, exits = reconstruction_mismatches(adopted, broken)
    assert exits > 0


# --------------------------------------------------------------------------- #
# 4 · 걸러내기 구간 — 엔진 안에서 판정 불변 · 닫힌 해 속성
# --------------------------------------------------------------------------- #


def test_screen_inside_the_engine_changes_nothing(
    world: tuple[pd.DataFrame, pd.DataFrame], monkeypatch: pytest.MonkeyPatch
) -> None:
    htf, minutes = world
    params = uncensored_params(_band_params())
    calls = {"probe": 0}
    original_probe = _IntrabarLiveLimit.probe_limit

    def counting_probe(self: _IntrabarLiveLimit, live_price: float) -> float | None:
        calls["probe"] += 1
        return original_probe(self, live_price)

    monkeypatch.setattr(_IntrabarLiveLimit, "probe_limit", counting_probe)
    screened = _build(htf, minutes, params, observe=True)
    screened_calls = calls["probe"]
    monkeypatch.setattr(_IntrabarLiveLimit, "hold_break_screen", lambda self: None)
    calls["probe"] = 0
    exact = _build(htf, minutes, params, observe=True)
    assert screened == exact
    assert any(c.hold_path.band_break_time is not None for c in exact)
    assert screened_calls < calls["probe"], "걸러내기가 조회를 하나도 안 줄였다(공허)."


@pytest.mark.parametrize("scale", [0.05, 1.0, 60_000.0])
@pytest.mark.parametrize("direction_sign", [1, -1])
def test_safe_interval_guarantees_the_favorable_side(scale: float, direction_sign: int) -> None:
    rng = random.Random(375)
    params = ConfluenceParams().deviation_filter
    assert params is not None
    covered = 0
    for _ in range(300):
        closes = [scale * (1.0 + rng.gauss(0.0, 0.02)) for _ in range(40)]
        band = RealtimeBand.seed_from_closed(closes, params)
        mid = band.value(closes[-1], direction_sign)
        assert mid is not None
        threshold = mid - direction_sign * abs(rng.gauss(0.0, 0.03 * scale))
        interval = band.safe_price_interval(threshold, direction_sign)
        if interval is None:
            continue
        covered += 1
        lo, hi = interval
        for frac in (0.0, 1e-9, 0.25, 0.5, 0.75, 1 - 1e-9, 1.0):
            price = lo + (hi - lo) * frac
            value = band.value(price, direction_sign)
            assert value is not None
            assert direction_sign * value >= direction_sign * threshold
    assert covered > 100, "구간이 거의 안 나와 속성 테스트가 공허하다."


def test_safe_interval_refuses_non_stdev_bands() -> None:
    params = ConfluenceParams().deviation_filter
    assert params is not None
    band = RealtimeBand.seed_from_closed([1.0 + 0.01 * i for i in range(40)], params)
    assert band.safe_price_interval(0.5, 1) is not None
    pct = RealtimeBand.seed_from_closed(
        [1.0] * 40, params.model_copy(update={"width_kind": "pct", "width_value": 0.01})
    )
    assert pct.safe_price_interval(0.5, 1) is None


# --------------------------------------------------------------------------- #
# 5 · 봉 안 절단 불변 (WAN-377 자)
# --------------------------------------------------------------------------- #


def _closed_keys(candidates: list[Any], cut_ms: int) -> list[tuple[Any, ...]]:
    return sorted(
        (
            c.entry_time,
            c.entry_price,
            c.exit_time,
            c.hold_path.mfe_r_pre_exit,
            c.hold_path.target_reach_time,
            c.hold_path.band_break_time,
            c.hold_path.band_break_r,
        )
        for c in candidates
        if c.exit_time < cut_ms and c.reason is not ExitReason.END_OF_DATA
    )


def test_hold_path_survives_an_intrabar_cut(world: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    htf, minutes = world
    params = uncensored_params(_band_params())
    full = _build(htf, minutes, params, observe=True)
    all_cuts = intrabar_cuts_for(full, _SYNTHETIC_TF)
    cuts = all_cuts[:: max(1, len(all_cuts) // 12)]
    compared = 0
    for cut in cuts:
        cut_htf, cut_1m = cut_world_intrabar(minutes, _SYNTHETIC_TF, cut)
        expected = _closed_keys(full, cut)
        assert expected == _closed_keys(_build(cut_htf, cut_1m, params, observe=True), cut)
        compared += len(expected)
    assert compared > 0


# --------------------------------------------------------------------------- #
# 6 · 집계·판정 (순수 함수)
# --------------------------------------------------------------------------- #


def _obs(**overrides: Any) -> SetupObs:
    base: dict[str, Any] = {
        "symbol": "X",
        "timeframe": "1h",
        "arm": "band",
        "segment": "is",
        "is_long": True,
        "tap_index": 0,
        "zone": "1",
        "trigger_time": 0,
        "entry_time": 0,
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_time": 10,
        "exit_reason": ExitReason.STOP_LOSS.value,
        "mfe_r": 1.0,
        "mfe_r_pre_exit": 1.0,
        "target_reach_time": None,
        "band_break_time": None,
        "band_break_r": None,
        "band_break_mfe_r": None,
    }
    base.update(overrides)
    return SetupObs(**base)


def test_derive_tp_on() -> None:
    assert derive_tp_on(_obs(target_reach_time=5)) == (ExitReason.TAKE_PROFIT.value, 5)
    assert derive_tp_on(_obs()) == (ExitReason.STOP_LOSS.value, 10)


def test_breakeven_is_the_zero_expectancy_point() -> None:
    rates = CostRates.from_config(harness.build_config("1h"))
    for width in (0.003, 0.01, 0.05):
        c_win, c_loss = cost_r(width, 1.5, rates)
        p = breakeven_win_rate(1.5, c_win, c_loss)
        assert p * (1.5 - c_win) - (1 - p) * (1.0 + c_loss) == pytest.approx(0.0, abs=1e-12)
    narrow = cost_r(0.003, 1.5, rates)[1]
    wide = cost_r(0.03, 1.5, rates)[1]
    assert narrow > wide  # 좁을수록 같은 bp가 R로는 크다.


def test_cost_rates_follow_the_adopted_accounting() -> None:
    rates = CostRates.from_config(harness.build_config("1h"))
    assert rates.entry_fee == pytest.approx(0.0002)
    assert rates.take_profit_fee == pytest.approx(0.0002)  # 익절 메이커(WAN-370)
    assert rates.stop_fee == pytest.approx(0.0004)
    assert rates.stop_slippage == pytest.approx(0.0005)


def test_observations_refuse_unobserved_candidates(
    world: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    htf, minutes = world
    plain = _build(htf, minutes, uncensored_params(_band_params()), observe=False)
    with pytest.raises(AssertionError, match="hold_path"):
        _rows(plain)
    with_tp = _build(htf, minutes, _band_params(), observe=True)
    with pytest.raises(AssertionError, match="익절"):
        _rows(with_tp)


def _gap(scope: str, segment: str, diff: float, n: int = 1000) -> GapTest:
    return GapTest(
        segment=segment,
        scope=scope,
        target_r=2.0,
        narrow_n=n,
        narrow_reach=0.5 + diff,
        wide_n=n,
        wide_reach=0.5,
    )


def test_verdict_needs_every_cell_and_every_scope() -> None:
    go = [_gap(scope, seg, 0.2) for scope in SCOPES for seg in ("is", "oos_warm")]
    assert verdict_for(go) == VERDICT_GO
    one_flat = [*go[:-1], _gap(SCOPE_ALL, "oos_warm", 0.0)]
    assert verdict_for_scope(one_flat, SCOPE_ALL) == VERDICT_SIMILAR
    assert verdict_for(one_flat) == VERDICT_MIXED


def test_thin_groups_never_carry_a_sign() -> None:
    thin = _gap(SCOPE_FIRST, "is", 0.4, n=VERDICT_MIN_N - 1)
    assert thin.sign == 0


def _frame_from(rows: list[SetupObs]) -> pd.DataFrame:
    return setups_frame(
        [
            CellResult(
                symbol="X",
                timeframe="1h",
                rows=tuple(rows),
                taps=(),
                checks=(),
                seconds=0.0,
            )
        ]
    )


def test_summary_renders_end_to_end() -> None:
    rng = random.Random(1)
    rows = []
    for _ in range(400):
        width = rng.choice((0.0035, 0.0045, 0.012, 0.02))
        mfe = rng.uniform(-0.5, 4.0)
        reach = 5 if mfe >= 1.5 else None
        rows.append(
            _obs(
                segment=rng.choice(("is", "oos_warm")),
                tap_index=rng.choice((0, 1)),
                zone=str(rng.randrange(60)),
                entry_time=rng.randrange(10_000),
                stop_price=100.0 * (1 - width),
                mfe_r_pre_exit=mfe,
                target_reach_time=reach,
                band_break_time=rng.choice((None, 3)),
                band_break_r=-0.5,
                band_break_mfe_r=0.5,
            )
        )
    frame = _frame_from(rows)
    rates = CostRates.from_config(harness.build_config("1h"))
    reach = reach_rows(frame, rates)
    pct = pct_rows(frame, rates)
    band = band_rows(frame, ("1970-01-01",), rates)
    tests = gap_tests(frame)
    text = render_summary(reach, pct, band, tests, None, ("1970-01-01",))
    assert "## §2 판정" in text and "밴드 무너짐" in text
    assert set(reach["scope"]) == set(SCOPES)


# --------------------------------------------------------------------------- #
# 실데이터 (게이트는 호출 전에)
# --------------------------------------------------------------------------- #

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2025-01-01"


def test_real_data_reconstruction_and_screen() -> None:
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        funding=False,
    )
    if market.empty or market.df_1m.empty:
        pytest.skip("실데이터가 없어 건너뜁니다(CI 기본).")
    from strategy.models import OrderBlockParams

    ob = harness.detect_order_blocks(market, OrderBlockParams())
    params = harness.build_params()
    cfg = harness.build_config(_REAL_TF)

    def build(p: ConfluenceParams, observe: bool) -> list[Any]:
        kwargs: dict[str, Any] = (
            {"observe_hold_path": True, "hold_path_target_r": params.take_profit_r}
            if observe
            else {}
        )
        cands, _ = build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            _REAL_TF,
            params=p,
            cfg=cfg,
            order_block_result=ob,
            **kwargs,
        )
        return cands

    adopted = build(params, observe=False)
    unc = build(uncensored_params(params), observe=True)
    rows = observations_from_candidates(unc, symbol=_REAL_SYMBOL, timeframe=_REAL_TF, boundary_ms=0)
    assert adopted and reconstruction_mismatches(adopted, rows) == (0, 0)
    assert _strip(unc) == build(uncensored_params(params), observe=False)

    diagnostics: list[SetupDiagnostic] = []
    unrested: list[UnrestedTap] = []
    sunk, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _REAL_TF,
        params=uncensored_params(params),
        cfg=cfg,
        order_block_result=ob,
        setup_sink=diagnostics,
        unrested_sink=unrested,
    )
    assert sunk == _strip(unc)
    assert unrested, "실데이터에서도 볼린저가 막은 탭이 없다 — 이 관측이 공허하다."
    taps = tap_observations(
        diagnostics, unrested, symbol=_REAL_SYMBOL, timeframe=_REAL_TF, boundary_ms=0
    )
    assert sum(1 for t in taps if t.outcome == OUTCOME_FILLED) == len(rows)


# --------------------------------------------------------------------------- #
# 7 · 스코프가 이름대로다
# --------------------------------------------------------------------------- #


def test_first_tap_scope_equals_retap_once(world: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """「첫 탭만」(재탭 켬 실행에서 `tap_index == 0`) ≡ `retap_mode="once"` 실행의 셋업.

    셋업 층에는 시퀀싱이 없어 존의 첫 탭 결과가 재탭 정책과 무관해야 한다 — 그 성질이 깨지면
    「첫 탭만」 줄을 재탭 끈 판으로 읽을 수 없다.
    """
    htf, minutes = world
    every = _build(htf, minutes, uncensored_params(_band_params()), observe=True)
    once_params = uncensored_params(_band_params()).model_copy(update={"retap_mode": "once"})
    once = _build(htf, minutes, once_params, observe=True)

    def key(c: Any) -> tuple[Any, ...]:
        return (c.trigger_time, c.entry_time, c.entry_price, c.exit_time, c.hold_path)

    first = sorted(key(c) for c in every if c.tap_index == 0)
    assert first and first == sorted(key(c) for c in once)
    assert any(c.tap_index > 0 for c in every), "재탭 셋업이 없어 대조가 공허하다."


def test_first_fill_scope_takes_the_earliest_fill_per_zone() -> None:
    rows = [
        _obs(zone="7", tap_index=0, trigger_time=10, entry_time=20),
        _obs(zone="7", tap_index=1, trigger_time=30, entry_time=40),
        _obs(zone="8", tap_index=2, trigger_time=50, entry_time=60),  # 첫 탭은 미체결이었던 존
        _obs(zone="8", tap_index=3, trigger_time=70, entry_time=80),
    ]
    frame = _frame_from(rows)
    picked = frame[frame["is_first_fill"]]
    assert sorted(zip(picked["zone"], picked["tap_index"], strict=True)) == [("7", 0), ("8", 2)]


def test_tap_census_is_pure_and_consistent(world: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    htf, minutes = world
    params = uncensored_params(_band_params())
    cfg = harness.build_config(_SYNTHETIC_TF)
    plain, plain_stats = build_zone_limit_candidates(
        htf, minutes, _SYNTHETIC_TF, params=params, cfg=cfg
    )
    diagnostics: list[SetupDiagnostic] = []
    unrested: list[UnrestedTap] = []
    sunk, sunk_stats = build_zone_limit_candidates(
        htf,
        minutes,
        _SYNTHETIC_TF,
        params=params,
        cfg=cfg,
        setup_sink=diagnostics,
        unrested_sink=unrested,
    )
    assert sunk == plain and sunk_stats == plain_stats
    # ⚠️ 이 합성 세계에는 「볼린저가 끝까지 막은」 탭이 없다 — 그 갈래의 비공허 확인은 아래
    # 실데이터 테스트가 한다.
    taps = tap_observations(
        diagnostics, unrested, symbol="X", timeframe=_SYNTHETIC_TF, boundary_ms=0
    )
    assert sum(1 for t in taps if t.outcome == OUTCOME_FILLED) == len(plain)
    assert sum(1 for t in taps if t.outcome == OUTCOME_NEVER_RESTED) == len(unrested)
    # 체결률 분모(eligible)는 규약 그대로 — 안 걸린 탭은 거기 안 들어간다(WAN-119).
    assert sunk_stats.eligible == len(diagnostics)


def test_tap_outcome_table_shares_sum_to_one(world: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    htf, minutes = world
    params = uncensored_params(_band_params())
    diagnostics: list[SetupDiagnostic] = []
    unrested: list[UnrestedTap] = []
    cands, _ = build_zone_limit_candidates(
        htf,
        minutes,
        _SYNTHETIC_TF,
        params=params,
        cfg=harness.build_config(_SYNTHETIC_TF),
        setup_sink=diagnostics,
        observe_hold_path=True,
        hold_path_target_r=params.take_profit_r,
        unrested_sink=unrested,
    )
    rows = _rows(cands)
    taps = tap_observations(
        diagnostics, unrested, symbol="X", timeframe=_SYNTHETIC_TF, boundary_ms=0
    )
    result = CellResult(
        symbol="X",
        timeframe=_SYNTHETIC_TF,
        rows=tuple(rows),
        taps=tuple(taps),
        checks=(),
        seconds=0.0,
    )
    table = tap_outcome_rows(taps_frame([result]), setups_frame([result]))
    share_cols = [c for c in table.columns if c.startswith("share_")]
    for _, row in table.dropna(subset=share_cols).iterrows():
        assert sum(float(row[c]) for c in share_cols) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 8 · 진입가 팔 `zone_top` (볼린저는 진입 여부만 · 진입가는 존 상단)
# --------------------------------------------------------------------------- #


def test_zone_top_arm_enters_at_zone_top_and_reconstructs(
    world: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    htf, minutes = world
    base = _band_params()
    top = arm_params(base, ARM_ZONE_TOP)
    assert arm_params(base, ARM_BAND) is base
    assert top.deviation_filter is not None and top.deviation_filter.select_only
    cands = _build(htf, minutes, uncensored_params(top), observe=True)
    assert cands, "존 상단 팔 후보가 없어 공허하다."
    for cand in cands:
        assert cand.order_block is not None
        expected = top.apply_zone_limit_offset(
            top.zone_limit_price(cand.order_block), is_long=cand.side.value == "long"
        )
        assert cand.entry_price == pytest.approx(expected)
    adopted_top = _build(htf, minutes, top, observe=False)
    rows = observations_from_candidates(
        cands, symbol="X", timeframe=_SYNTHETIC_TF, boundary_ms=0, arm=ARM_ZONE_TOP
    )
    assert reconstruction_mismatches(adopted_top, rows) == (0, 0)
    # 볼린저 진입 여부 판정은 그대로라 존 상단 팔이 **더 적게** 진입할 수는 없다(더 높게 건다).
    band_cands = _build(htf, minutes, uncensored_params(base), observe=True)
    assert len(cands) >= len(band_cands)


def test_arm_params_refuses_unknown_or_bandless() -> None:
    with pytest.raises(ValueError, match="모르는"):
        arm_params(_band_params(), "mid")
    with pytest.raises(ValueError, match="볼린저"):
        arm_params(_engine_params(), ARM_ZONE_TOP)


def test_band_exit_effects_split_and_charge_the_tp_side_more() -> None:
    """손절 쪽·익절 쪽을 따로 내고, 비용은 **익절을 끊을 때** 더 문다(지정가 → 시장가)."""
    rates = CostRates.from_config(harness.build_config("1h"))
    rows = [
        # 손절 전에 −0.4R에서 무너짐 → 0.6R 덜 잃음
        _obs(stop_price=99.0, band_break_time=5, band_break_r=-0.4),
        # 익절(1.5R) 전에 +0.5R에서 무너짐 → 1.0R 포기
        _obs(stop_price=99.0, target_reach_time=8, band_break_time=5, band_break_r=0.5),
        # 무너짐이 익절 뒤 → 안 끊김
        _obs(stop_price=99.0, target_reach_time=3, band_break_time=5, band_break_r=0.5),
    ]
    frame = _frame_from(rows)
    effects = band_exit_effects(frame, rates)

    def num(key: str) -> float:
        value = effects[key]
        assert value is not None, key
        return float(value)

    assert effects["stop_cut"] == 1 and effects["tp_cut"] == 1 and effects["closed"] == 3
    assert num("stop_saved_gross_mean") == pytest.approx(0.6)
    assert num("tp_lost_gross_mean") == pytest.approx(1.0)
    assert num("delta_gross_r") == pytest.approx((0.6 - 1.0) / 3)
    assert num("tp_lost_net_mean") > num("tp_lost_gross_mean")  # 시장가 청산 비용
    stop_gap = abs(num("stop_saved_net_mean") - num("stop_saved_gross_mean"))
    tp_gap = num("tp_lost_net_mean") - num("tp_lost_gross_mean")
    assert stop_gap < tp_gap  # 손절은 원래도 시장가라 차이가 작다.
