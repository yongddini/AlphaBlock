"""WAN-152 「선별 대 기하」 분리 — 장벽 팔이 라벨이 아니라 실제로 다른 기하인가.

이 파일이 **동작으로** 고정하는 것:

1. **ATR 장벽 팔이 실제로 다른 손절선을 쓴다** — 라벨만 붙고 존 장벽이 그대로 도는 것이
   이 저장소가 WAN-91/95/112/123에서 반복해 겪은 조용한 실패다. 손절 거리로 확인한다.
2. **두 장벽이 같은 셋업을 본다** — 공통 풀이 같은 순서·같은 키여야 「장벽만 바꿨다」가
   성립한다. 한쪽에만 있는 셋업이 섞이면 차이가 장벽이 아니라 표본에서 온다.
3. **매칭 추첨이 장벽에 걸쳐 공유된다** — 시드 `s`가 두 장벽에서 다른 거래를 뽑으면
   두 표의 차이에 추첨 노이즈가 섞인다.
4. **매칭 대조군 크기 = 필터 크기**(구간별), 구간을 넘나들지 않는다(룩어헤드 금지).
5. **엔진이 핀 없이 오늘의 채택 기본값을 탄다**(WAN-142 선례) — 분리 존 · `intrabar_live`.
6. **판정 네 분기가 의도한 입력에서 나오고 결론 문단이 그것과 어긋나지 않는다** —
   문장 부분문자열로 분기하던 시절의 사고를 열거형으로 막는다(WAN-142 교훈).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import LEGACY_OB_PARAMS, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import ExitReason, PositionSide
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan133_geometry_vs_selection import ARM_DEFAULT, ARM_FILTER, MIN_TRADES_FOR_PNL
from backtest.wan142_zone_width_filter_verdict import ARM_MATCHED, MATCH_SEEDS, SEED_AGGREGATE
from backtest.wan152_selection_vs_geometry import (
    ATR_K,
    BARRIER_ATR,
    BARRIER_ZONE,
    ExperimentResult,
    GeoCell,
    MatchedTestRow,
    PnlRow,
    VerdictKind,
    build_cell,
    build_summary_markdown,
    candidate_key,
    geometry_share,
    matched_test_row,
    pnl_rows_for_cell,
    symbol_mean,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.models import ConfluenceParams, OrderBlockParams

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"
_HOUR_MS = 3_600_000


def _market(bars: int = 600, *, need_1m: bool = False, span: int = 400) -> MarketData:
    """합성 시장(WAN-142 테스트와 같은 조합 — 1h에서 후보가 실제로 나오는 시드).

    ⚠️ `span`은 IS/OOS 경계(2/3 지점)를 넘겨야 한다 — 1분봉이 없는 구간은 후보가 아예
    안 생겨서 IS 문턱이 잡히지 않고 필터 팔이 통째로 비어 검정이 공허해진다.
    """
    htf = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=bars, seed=7)
    one_min = pd.DataFrame()
    if need_1m:
        start = int(htf["open_time"].iloc[-span])
        one_min = make_synthetic_ohlcv(
            timeframe="1m", bars=span * 60, seed=11, start_time_ms=start, swing_period=180
        )
    return MarketData(_SYMBOL, _TIMEFRAME, htf, one_min, [])


# --------------------------------------------------------------------------- #
# 1·2. 장벽 팔이 라벨이 아니다 · 두 장벽이 같은 셋업을 본다
# --------------------------------------------------------------------------- #


def _engine_params() -> ConfluenceParams:
    """합성 데이터에서 **실제로 후보가 나오는** 엔진 설정(wan133/137 테스트와 같은 관행).

    ⚠️ 채택 기본값 그대로는 이 합성 시리즈에서 후보가 **0개**라, 「두 장벽이 다르다」·
    「공통 풀이 같다」 검정이 전부 `[] == []`로 **공허하게 참**이 된다(WAN-142 테스트가
    같은 함정을 한 번 밟았다). 이 시드는 숏 셋업만 내고 볼린저는 작은 데이터셋에서 후보를
    모두 걸러내므로 둘을 열어 준다 — 여기서 재는 것은 **장벽 배선**이지 진입 규칙이 아니다.
    탐지(`OrderBlockParams`)는 건드리지 않으므로 핀 검정의 축은 그대로 살아 있다.
    """
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )


def _built_cell() -> GeoCell:
    cell = build_cell(_market(need_1m=True), params=_engine_params())
    if cell.n_pool == 0:
        pytest.skip("합성 데이터에서 공통 풀 후보가 나오지 않았다.")
    return cell


def test_atr_barrier_actually_moves_the_stop() -> None:
    """ATR 장벽 팔의 손절선이 존 장벽과 **다르다** — 팔 이름만 바뀐 게 아니다.

    이 검정이 없으면 `stop_loss_override`가 조용히 무시돼도(또는 봉내 경로에 배선되지
    않아도) 표가 「두 장벽이 같다 = 기하는 무관」이라는 **정반대 결론**을 낸다.
    """
    cell = _built_cell()
    zone = cell.by_barrier[BARRIER_ZONE]
    atr_arm = cell.by_barrier[BARRIER_ATR]
    assert len(zone) == len(atr_arm)
    differing = [(z, a) for z, a in zip(zone, atr_arm, strict=True) if z.stop_price != a.stop_price]
    assert differing, "두 장벽의 손절가가 전부 같다 — 오버라이드가 안 걸렸다."
    # 방향까지 본다: 롱의 ATR 손절은 진입가에서 정확히 k·ATR 아래여야 하므로, 존 장벽과
    # 같은 값이 우연히 나올 수는 있어도 **전부** 같을 수는 없다.
    assert all(a.stop_price < a.entry_price for a in atr_arm if a.side is PositionSide.LONG)
    assert ATR_K > 0


def test_common_pool_is_the_same_setups_in_the_same_order() -> None:
    """공통 풀의 두 장벽 리스트가 같은 키·같은 순서다 — 인덱스로 짝지어도 안전하다."""
    cell = _built_cell()
    zone_keys = [candidate_key(c) for c in cell.by_barrier[BARRIER_ZONE]]
    atr_keys = [candidate_key(c) for c in cell.by_barrier[BARRIER_ATR]]
    assert zone_keys == atr_keys
    assert len(cell.zwa) == len(zone_keys)
    assert all(z is not None for z in cell.zwa), "존폭/ATR이 없는 셋업이 풀에 남아 있다."


def test_build_cell_uses_adopted_defaults_not_legacy_pins() -> None:
    """탐지가 **분리 존**(WAN-149 채택 기본값)이지 병합 존(WAN-133 핀)이 아니다.

    WAN-133/142 코드를 재사용하면서 핀까지 물려받으면 「병합 시절 숫자를 새 이슈 이름으로
    다시 내는」 조용한 실패가 된다 — 라벨이 아니라 **후보 집합**으로 확인한다.
    """
    market = _market(need_1m=True)
    params = _engine_params()
    cell = build_cell(market, params=params)
    cfg = harness.build_config(_TIMEFRAME)
    separated, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=harness.detect_order_blocks(market, OrderBlockParams()),
    )
    merged, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=harness.detect_order_blocks(market, LEGACY_OB_PARAMS),
    )
    got = {candidate_key(c) for c in cell.by_barrier.get(BARRIER_ZONE, [])}
    assert got, "후보가 0개다 — 이 검정이 공허하게 참이 됐다."
    assert got <= {candidate_key(c) for c in separated}  # 공통 풀은 분리 존 후보의 부분집합.
    if separated != merged:
        assert got != {candidate_key(c) for c in merged}


def test_band_bar_is_intrabar_live_not_pinned_tap() -> None:
    """진입가 정본이 `intrabar_live`다(WAN-132) — 이 모듈은 `pin_band_bar`를 쓰지 않는다."""
    params = harness.build_params(fill=harness.BASELINE_FILL)
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"
    assert params.deviation_filter.band_bar != harness.LEGACY_BAND_BAR


# --------------------------------------------------------------------------- #
# 3·4. 추첨 — 장벽 공유 · 크기 · 구간
# --------------------------------------------------------------------------- #


def _cand(entry: int, *, exit_price: float, stop_price: float, win: bool) -> _Candidate:
    """겹치지 않게(진입 ≥ 직전 청산) 놓인 한 후보 — 동시 1포지션 시퀀서가 전부 통과시킨다."""
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry,
        entry_price=100.0,
        exit_time=entry + _HOUR_MS // 2,
        exit_price=exit_price,
        reason=ExitReason.TAKE_PROFIT if win else ExitReason.STOP_LOSS,
        stop_price=stop_price,
        trigger_time=entry,
    )


def _hand_made_cell(n: int = 60) -> tuple[GeoCell, MarketData]:
    """후보를 **직접 만든** 셀(WAN-142 테스트와 같은 이유 — 합성 1h는 후보가 한둘뿐이라
    「매칭 크기 = 필터 크기」가 `0 == 0`으로 공허하게 참이 된다).

    두 장벽은 같은 셋업을 청산만 달리 갖는다: `zone`은 손절 −1%, `atr`은 −2%로 두고
    손익 결과도 다르게 만들어, 「같은 인덱스를 뽑았는가」와 「값이 다른가」를 함께 본다.
    """
    t0 = 1_700_000_000_000
    zone: list[_Candidate] = []
    atr_arm: list[_Candidate] = []
    zwa: list[float | None] = []
    for i in range(n):
        entry = t0 + i * _HOUR_MS
        win = bool(i % 2)
        zone.append(_cand(entry, exit_price=101.5 if win else 99.0, stop_price=99.0, win=win))
        atr_arm.append(_cand(entry, exit_price=103.0 if win else 98.0, stop_price=98.0, win=win))
        zwa.append(float(i % 10))
    cell = GeoCell(symbol=_SYMBOL, timeframe=_TIMEFRAME)
    cell.by_barrier = {BARRIER_ZONE: zone, BARRIER_ATR: atr_arm}
    cell.zwa = zwa
    cell.is_boundary = t0 + (n * 2 // 3) * _HOUR_MS  # IS 2/3 · OOS 1/3.
    return cell, MarketData(_SYMBOL, _TIMEFRAME, pd.DataFrame(), pd.DataFrame(), [])


def test_matched_arm_matches_filter_count_in_both_barriers() -> None:
    """매칭 대조군의 후보 수 = 필터의 후보 수(장벽·구간별로 각각)."""
    cell, market = _hand_made_cell()
    rows = pnl_rows_for_cell(cell, market)
    assert rows
    drawn: list[float] = []
    for barrier in (BARRIER_ZONE, BARRIER_ATR):
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            seg = [r for r in rows if r.barrier == barrier and r.segment == segment]
            filt = next(r for r in seg if r.arm == ARM_FILTER)
            matched = [r for r in seg if r.arm == ARM_MATCHED and r.seed != SEED_AGGREGATE]
            assert len(matched) == len(MATCH_SEEDS)
            assert {r.num_candidates for r in matched} == {filt.num_candidates}
            drawn.append(filt.num_candidates)
    assert max(drawn) > 0  # `0 == 0`으로 공허하게 참이 되는 것을 막는다.


def test_matched_draw_is_shared_across_barriers() -> None:
    """같은 시드가 두 장벽에서 **같은 셋업**을 뽑는다 — 차이가 추첨이 아니라 장벽에서 온다.

    손으로 만든 셀은 두 장벽의 손익이 다르므로(`zone` −1% vs `atr` −2% 손절) 값이 같은지로는
    확인할 수 없다. 대신 **거래 수와 후보 수**가 시드별로 정확히 대응하는지, 그리고 같은
    시드의 두 장벽 행이 서로 다른 수익을 내는지(= 같은 표본에 다른 청산)를 본다.
    """
    cell, market = _hand_made_cell()
    rows = pnl_rows_for_cell(cell, market)
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        for seed in MATCH_SEEDS:
            picked = {
                r.barrier: r
                for r in rows
                if r.arm == ARM_MATCHED and r.seed == seed and r.segment == segment
            }
            assert set(picked) == {BARRIER_ZONE, BARRIER_ATR}
            assert picked[BARRIER_ZONE].num_candidates == picked[BARRIER_ATR].num_candidates
        # 시드가 실제로 다른 표본을 낸다(분포가 있어야 순위 p가 상수가 아니다).
        returns = {
            r.total_return
            for r in rows
            if r.arm == ARM_MATCHED
            and r.seed != SEED_AGGREGATE
            and r.segment == segment
            and r.barrier == BARRIER_ZONE
        }
        assert len(returns) > 1


def test_matched_arm_draws_only_from_its_own_segment() -> None:
    """매칭 추첨은 같은 구간의 후보에서만 한다 — 구간을 넘나들면 IS가 OOS로 샌다."""
    cell, market = _hand_made_cell()
    rows = pnl_rows_for_cell(cell, market)
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        in_seg = sum(
            1
            for c in cell.by_barrier[BARRIER_ZONE]
            if (c.trigger_time < cell.is_boundary) == (segment == SEGMENT_IS)
        )
        for r in rows:
            if r.segment == segment and r.arm == ARM_MATCHED:
                assert r.num_candidates <= in_seg


# --------------------------------------------------------------------------- #
# 5·6. 판정 · 결론
# --------------------------------------------------------------------------- #


def _row(
    symbol: str,
    barrier: str,
    arm: str,
    *,
    ret: float,
    win: float,
    seed: int = SEED_AGGREGATE,
    trades: float = 50.0,
    mdd: float = 0.10,
) -> PnlRow:
    return PnlRow(
        barrier=barrier,
        symbol=symbol,
        timeframe=_TIMEFRAME,
        segment=SEGMENT_OOS,
        arm=arm,
        seed=seed,
        num_candidates=trades,
        num_trades=trades,
        total_return=ret,
        max_drawdown=mdd,
        win_rate=win,
    )


def _synthetic_rows(
    *,
    zone_filter_ret: float,
    atr_filter_ret: float,
    matched_ret: float = 0.02,
    zone_filter_win: float = 0.58,
    atr_filter_win: float = 0.50,
    matched_win: float = 0.47,
    symbols: int = 4,
) -> list[PnlRow]:
    """필터가 매칭 시드 분포를 이기는(또는 못 이기는) 합성 표.

    시드마다 값을 조금씩 흔들어 순위 p가 상수가 되지 않게 한다.
    """
    rows: list[PnlRow] = []
    for i in range(symbols):
        sym = f"S{i}/USDT:USDT"
        rows.append(_row(sym, BARRIER_ZONE, ARM_DEFAULT, ret=0.01, win=0.48))
        rows.append(_row(sym, BARRIER_ATR, ARM_DEFAULT, ret=0.01, win=0.48))
        rows.append(_row(sym, BARRIER_ZONE, ARM_FILTER, ret=zone_filter_ret, win=zone_filter_win))
        rows.append(_row(sym, BARRIER_ATR, ARM_FILTER, ret=atr_filter_ret, win=atr_filter_win))
        for seed in MATCH_SEEDS:
            jitter = (seed - len(MATCH_SEEDS) / 2) * 0.001
            for barrier in (BARRIER_ZONE, BARRIER_ATR):
                rows.append(
                    _row(
                        sym,
                        barrier,
                        ARM_MATCHED,
                        ret=matched_ret + jitter,
                        win=matched_win + jitter,
                        seed=seed,
                    )
                )
    return rows


def _tests_for(rows: list[PnlRow]) -> list[MatchedTestRow]:
    return [
        matched_test_row(rows, barrier=b, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)
        for b in (BARRIER_ZONE, BARRIER_ATR)
    ]


def test_verdict_reads_selection_when_advantage_survives_the_fixed_barrier() -> None:
    """장벽을 고정해도 필터가 이기면 (a) 선별."""
    tests = _tests_for(_synthetic_rows(zone_filter_ret=0.30, atr_filter_ret=0.28))
    got = verdict(tests, timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.SELECTION
    assert "채택이 아니다" in got.text


def test_verdict_reads_geometry_when_advantage_dies_with_the_fixed_barrier() -> None:
    """존 장벽에서는 이기는데 ATR 장벽에서 지면 (b) 기하."""
    tests = _tests_for(_synthetic_rows(zone_filter_ret=0.30, atr_filter_ret=-0.05))
    got = verdict(tests, timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.GEOMETRY
    assert "기하" in got.text


def test_verdict_reads_neither_when_no_barrier_shows_an_edge() -> None:
    """두 장벽 다 지면 (c)."""
    tests = _tests_for(_synthetic_rows(zone_filter_ret=-0.05, atr_filter_ret=-0.06))
    got = verdict(tests, timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.NEITHER


def test_verdict_refuses_on_thin_symbol_coverage() -> None:
    """유효 심볼이 3개 미만이면 (a)/(b)/(c) 대신 「판정 불가」(WAN-142/143 게이트와 같은 취지)."""
    tests = _tests_for(_synthetic_rows(zone_filter_ret=0.30, atr_filter_ret=0.28, symbols=2))
    got = verdict(tests, timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.INDETERMINATE
    assert "판정 불가" in got.text


def test_geometry_share_is_none_when_zone_gap_is_not_positive() -> None:
    """존 장벽에 설명할 우위가 없으면 소멸 비율은 뜻을 잃는다(0으로 나누기·부호 함정).

    WAN-115가 「기준 증분이 음수인 셀의 잔존 비율」로 172%를 찍어 「유지」로 읽힐 뻔한
    함정과 같은 자리다.
    """
    tests = _tests_for(
        _synthetic_rows(
            zone_filter_ret=0.30, atr_filter_ret=0.28, zone_filter_win=0.40, matched_win=0.47
        )
    )
    assert geometry_share(tests, timeframe=_TIMEFRAME) is None


def test_geometry_share_measures_the_vanished_win_rate_gap() -> None:
    """승률 격차가 절반으로 줄면 소멸분이 50% 근처다(크기 지표가 실제로 계산된다)."""
    tests = _tests_for(
        _synthetic_rows(
            zone_filter_ret=0.30,
            atr_filter_ret=0.28,
            zone_filter_win=0.57,
            atr_filter_win=0.52,
            matched_win=0.47,
        )
    )
    share = geometry_share(tests, timeframe=_TIMEFRAME)
    assert share is not None
    assert share == pytest.approx(0.5, abs=0.02)


def test_symbol_mean_gate_excludes_small_cells() -> None:
    rows = _synthetic_rows(zone_filter_ret=0.30, atr_filter_ret=0.28, symbols=3)
    rows.append(_row("S9/USDT:USDT", BARRIER_ZONE, ARM_FILTER, ret=99.0, win=1.0, trades=1.0))
    gated = symbol_mean(
        rows, barrier=BARRIER_ZONE, timeframe=_TIMEFRAME, segment=SEGMENT_OOS, arm=ARM_FILTER
    )
    assert gated["n_symbols"] == 3.0
    assert gated["n_excluded"] == 1.0
    assert gated["total_return"] == pytest.approx(0.30)
    assert MIN_TRADES_FOR_PNL > 1


@pytest.mark.parametrize(
    ("zone_ret", "atr_ret", "expected"),
    [
        (0.30, 0.28, "(a) 선별이 실재한다"),
        (0.30, -0.05, "(b) 존폭 필터의 우위는 기하가 대부분이다"),
        (-0.05, -0.06, "(c) 두 장벽 모두 우위가 없다"),
    ],
)
def test_conclusion_paragraph_agrees_with_the_verdict(
    zone_ret: float, atr_ret: float, expected: str
) -> None:
    """§결론이 §판정과 어긋나지 않는다 — 분기를 열거형으로 하는 이유(WAN-142 사고 재발 방지)."""
    rows = _synthetic_rows(zone_filter_ret=zone_ret, atr_filter_ret=atr_ret)
    result = ExperimentResult(pnl_rows=rows, matched_tests=_tests_for(rows))
    text = build_summary_markdown(result, timeframes=[_TIMEFRAME])
    assert expected in text
    assert "ALPHABLOCK_LIVE_TRADING=false" in text
