"""WAN-142 존폭 필터 채택 판정 — 매칭 대조군이 실제로 「같은 수, 규칙만 없음」인가.

이 파일이 **동작으로** 고정하는 것:

1. **매칭 대조군의 크기가 필터와 정확히 같다** — 이 이슈 전체가 「MDD 개선이 선별인가 표본
   축소인가」를 가르는 것이고, 그 분모가 되는 대조군이 크기를 안 맞추면 표가 아무것도 재지
   못한다. 구간(IS/OOS)을 넘나들며 뽑지 않는 것도 함께 본다(IS 후보가 OOS 성과에 섞이면
   룩어헤드다).
2. **시드가 실제로 다른 표본을 낸다** — 시드 20개가 전부 같은 표본이면 순위 p값이 상수가
   된다(`MATCH_SEEDS` 라벨만 있고 분포가 없는 상태). 같은 시드는 재현된다.
3. **검정이 두 팔에 같은 심볼 집합을 쓴다** — 표본 게이트를 한쪽에만 걸면 p값이 「선별의
   몫」이 아니라 심볼 구성 차이를 재게 된다.
4. **엔진이 핀 없이 오늘의 채택 기본값을 탄다** — WAN-133이 `LEGACY_OB_PARAMS`·`pin_band_bar`
   로 옛 엔진을 고정한 것과 **정반대**여야 한다. 그대로 물려받으면 「병합 시절 숫자를 새
   이슈 이름으로 다시 내는」 조용한 실패가 된다(WAN-91/95/112/123 부류).
5. **존폭 분포가 존 단위다** — 탭이 많은 존이 분포를 여러 번 차지하면 「존폭 분포」가 아니라
   「탭 분포」다.
6. **판정 문장이 표본 부족을 삼키지 않는다**.
7. **결론 문단이 판정과 어긋나지 않는다** — (a)/(b)/(c)/판정불가 네 분기가 각각 의도한
   입력에서 나온다. 문장 부분문자열로 분기하던 시절 §판정은 (a)인데 §결론만 「판정 불가」를
   찍는 사고가 실제로 났다(PM 지적) — 그래서 분기를 `VerdictKind` 열거형으로 옮기고 이
   절이 그 동작을 고정한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import LEGACY_OB_PARAMS, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import ExitReason, PositionSide
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan133_geometry_vs_selection import (
    ARM_DEFAULT,
    ARM_FILTER,
    MIN_TRADES_FOR_PNL,
    CellResult,
)
from backtest.wan142_zone_width_filter_verdict import (
    ALPHA,
    ARM_MATCHED,
    LENS_PRIMARY,
    MATCH_SEEDS,
    SEED_AGGREGATE,
    ExperimentResult,
    PnlRow,
    VerdictKind,
    build_cell,
    build_summary_markdown,
    matched_test_row,
    pnl_rows_for_cell,
    symbol_mean,
    verdict,
    zone_width_samples,
)
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.models import ConfluenceParams, OrderBlockParams

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"


def _market(bars: int = 600, *, need_1m: bool = False, span: int = 400) -> MarketData:
    """합성 시장. 1분봉 조합은 wan133 테스트의 `_synthetic_pair` 계열이다(후보가 나오는 조합).

    ⚠️ `span`은 **IS/OOS 경계(2/3 지점)를 반드시 넘겨야** 한다 — 1분봉이 없는 구간은 후보가
    아예 안 생겨서, 마지막 120봉만 덮으면 IS 후보가 0이 되고 IS 문턱이 잡히지 않아 필터 팔이
    통째로 비어 버린다. 그러면 「매칭 대조군 크기 = 필터 크기」 검정이 `0 == 0`으로 공허하게
    참이 된다(실제로 한 번 그렇게 통과했다).
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
# 1·2. 매칭 대조군의 크기·구간·시드
# --------------------------------------------------------------------------- #


_HOUR_MS = 3_600_000


def _cell_with_candidates(n: int = 60) -> tuple[CellResult, MarketData]:
    """후보를 **직접 만든** 셀.

    ⚠️ 여기서 합성 OHLCV를 엔진에 태우지 않는 이유: 이 저장소의 합성 시리즈는 1h에서 후보를
    한두 개밖에 못 내서, 「매칭 대조군 크기 = 필터 크기」 검정이 `0 == 0`으로 **공허하게
    참**이 된다(실제로 한 번 그렇게 통과했다). 이 절이 재는 것은 **추첨·구간 분리 로직**이지
    탐지기가 아니므로, 후보를 손으로 만들어 개수를 통제하는 편이 정확하다. 엔진이 오늘의
    채택 기본값을 타는지는 `test_build_cell_uses_adopted_defaults_not_legacy_pins`가 따로 본다.

    후보는 서로 겹치지 않게(진입 ≥ 직전 청산) 시간순으로 놓아 동시 1포지션 시퀀서가 전부
    통과시키게 하고, `zone_width_atr`은 0..9를 순환시켜 하위 1/3 문턱이 실제로 표본을 가르게
    한다.
    """
    t0 = 1_700_000_000_000
    cands: list[_Candidate] = []
    zwa: list[float | None] = []
    for i in range(n):
        entry = t0 + i * _HOUR_MS
        cands.append(
            _Candidate(
                side=PositionSide.LONG,
                entry_time=entry,
                entry_price=100.0,
                exit_time=entry + _HOUR_MS // 2,
                exit_price=101.5 if i % 2 else 99.0,
                reason=ExitReason.TAKE_PROFIT if i % 2 else ExitReason.STOP_LOSS,
                stop_price=99.0,
                trigger_time=entry,
            )
        )
        zwa.append(float(i % 10))
    cell = CellResult(symbol=_SYMBOL, timeframe=_TIMEFRAME)
    cell.default_candidates = cands
    cell.default_zwa = zwa
    cell.is_boundary = t0 + (n * 2 // 3) * _HOUR_MS  # IS 2/3 · OOS 1/3.
    return cell, MarketData(_SYMBOL, _TIMEFRAME, pd.DataFrame(), pd.DataFrame(), [])


def test_matched_arm_matches_filter_candidate_count_per_segment() -> None:
    """매칭 대조군의 후보 수 = 필터의 후보 수(구간별로 각각).

    이 등식이 깨지면 「같은 수의 거래를 무작위로 뽑았다」는 이 표의 전제가 거짓이 되고,
    p값은 선별이 아니라 표본 크기 차이를 재게 된다.
    """
    cell, market = _cell_with_candidates()
    rows = pnl_rows_for_cell(cell, market, lens=LENS_PRIMARY)
    assert rows
    drawn: list[float] = []
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        seg = [r for r in rows if r.segment == segment]
        filt = next(r for r in seg if r.arm == ARM_FILTER)
        matched = [r for r in seg if r.arm == ARM_MATCHED and r.seed != SEED_AGGREGATE]
        assert matched, "시드 행이 없다 — 매칭 팔이 통째로 빠졌다."
        assert {r.num_candidates for r in matched} == {filt.num_candidates}
        drawn.append(filt.num_candidates)
    # 등식이 `0 == 0`으로 공허하게 참이 되는 것을 막는다 — 한 구간은 실제로 뽑아야 한다.
    assert max(drawn) > 0


def test_matched_arm_draws_only_from_its_own_segment() -> None:
    """매칭 추첨은 같은 구간의 후보에서만 한다 — 구간을 넘나들면 IS가 OOS에 샌다."""
    cell, market = _cell_with_candidates()
    cands: list[_Candidate] = cell.default_candidates
    boundary: int = cell.is_boundary
    rows = pnl_rows_for_cell(cell, market, lens=LENS_PRIMARY)
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        in_seg = [c for c in cands if (c.trigger_time < boundary) == (segment == SEGMENT_IS)]
        matched = [
            r
            for r in rows
            if r.segment == segment and r.arm == ARM_MATCHED and r.seed != SEED_AGGREGATE
        ]
        # 그 구간에 있는 후보보다 많이 뽑을 수는 없다(비복원 추출).
        assert all(r.num_candidates <= len(in_seg) for r in matched)


def test_match_seeds_are_reproducible_and_varied() -> None:
    """같은 시드는 같은 표본, 다른 시드는 (적어도 하나는) 다른 표본을 낸다.

    시드가 결과를 안 바꾸면 순위 p값이 상수가 되어 판정이 불가능해진다 — 시드 목록이
    라벨만 있고 분포가 없는 상태를 막는다.
    """
    cell, market = _cell_with_candidates()
    rows_a = pnl_rows_for_cell(cell, market, lens=LENS_PRIMARY)
    rows_b = pnl_rows_for_cell(cell, market, lens=LENS_PRIMARY)
    assert rows_a

    def _by_seed(rows: list[PnlRow], segment: str) -> dict[int, float]:
        return {
            r.seed: r.total_return
            for r in rows
            if r.arm == ARM_MATCHED and r.segment == segment and r.seed != SEED_AGGREGATE
        }

    a, b = _by_seed(rows_a, SEGMENT_IS), _by_seed(rows_b, SEGMENT_IS)
    assert a == b, "같은 시드가 다른 표본을 냈다 — 재현되지 않는다."
    assert len(a) == len(MATCH_SEEDS)
    # 시드가 결과를 안 바꾸면 순위 p값이 상수가 되어 「분포 위에 놓는다」가 거짓이 된다.
    assert len(set(a.values())) > 1, "모든 시드가 같은 성과를 냈다 — 분포가 없다."


# --------------------------------------------------------------------------- #
# 3. 검정이 두 팔에 같은 심볼 집합을 쓴다
# --------------------------------------------------------------------------- #


def _row(
    symbol: str,
    arm: str,
    *,
    seed: int = SEED_AGGREGATE,
    trades: float = 100.0,
    ret: float = 0.0,
    mdd: float = 0.10,
    timeframe: str = _TIMEFRAME,
) -> PnlRow:
    return PnlRow(
        lens=LENS_PRIMARY,
        symbol=symbol,
        timeframe=timeframe,
        segment=SEGMENT_OOS,
        arm=arm,
        seed=seed,
        num_candidates=trades,
        num_trades=trades,
        total_return=ret,
        max_drawdown=mdd,
        win_rate=0.5,
    )


def _synthetic_pnl(
    *,
    filter_mdd: float,
    matched_mdd: float,
    symbols: int = 6,
    timeframe: str = _TIMEFRAME,
) -> list[PnlRow]:
    rows: list[PnlRow] = []
    for i in range(symbols):
        sym = f"S{i}/USDT:USDT"
        rows.append(_row(sym, ARM_DEFAULT, mdd=0.20, timeframe=timeframe))
        rows.append(_row(sym, ARM_FILTER, mdd=filter_mdd, ret=0.05, timeframe=timeframe))
        for seed in MATCH_SEEDS:
            rows.append(
                _row(sym, ARM_MATCHED, seed=seed, mdd=matched_mdd, ret=0.05, timeframe=timeframe)
            )
    return rows


def test_matched_test_uses_filter_valid_symbols_for_both_arms() -> None:
    """표본 게이트는 **필터 셀이 유효한 심볼**로 두 팔에 똑같이 적용된다.

    한쪽만 게이트를 걸면 두 팔의 심볼 구성이 갈려 p값이 심볼 차이를 재게 된다.
    """
    rows = _synthetic_pnl(filter_mdd=0.05, matched_mdd=0.15)
    # 한 심볼의 필터 셀만 표본 미달로 만든다 → 검정 심볼이 5개로 줄어야 한다.
    rows = [r for r in rows if not (r.symbol == "S0/USDT:USDT" and r.arm == ARM_FILTER)]
    rows.append(_row("S0/USDT:USDT", ARM_FILTER, trades=MIN_TRADES_FOR_PNL - 1, mdd=0.01))
    test = matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)
    assert test.n_symbols == 5


def test_matched_test_p_value_bounds_and_direction() -> None:
    """필터가 모든 시드를 이기면 p는 하한(1/(n+1))이고, 지면 1.0이다."""
    win = matched_test_row(
        _synthetic_pnl(filter_mdd=0.05, matched_mdd=0.15),
        lens=LENS_PRIMARY,
        timeframe=_TIMEFRAME,
        segment=SEGMENT_OOS,
    )
    assert win.p_mdd == pytest.approx(1 / (len(MATCH_SEEDS) + 1))
    assert win.p_mdd is not None and win.p_mdd <= ALPHA

    lose = matched_test_row(
        _synthetic_pnl(filter_mdd=0.15, matched_mdd=0.05),
        lens=LENS_PRIMARY,
        timeframe=_TIMEFRAME,
        segment=SEGMENT_OOS,
    )
    assert lose.p_mdd == pytest.approx(1.0)


def test_sample_share_is_none_when_there_is_no_improvement() -> None:
    """`default → filter` 개선이 없으면 「표본축소 몫」 비율은 뜻을 잃으므로 None이다.

    WAN-115가 겪은 부호 함정(기준 증분이 음수인데 비율을 계산해 「유지」로 읽힌 것)의
    같은 부류를 막는다.
    """
    rows = _synthetic_pnl(filter_mdd=0.30, matched_mdd=0.25)  # 기본 MDD 0.20보다 나쁨.
    test = matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)
    assert test.sample_share is None


def test_trade_residual_is_reported_and_warned_in_verdict() -> None:
    """후보를 맞춰도 시퀀서가 남기는 **거래 수 잔차**가 판정 문장에 드러난다.

    잔차가 양수면 매칭이 더 많이 거래한다 = 노출이 커 MDD가 부풀려진다 = 필터에 유리하다.
    이걸 안 적으면 「MDD가 유의하게 낮다」가 잔차의 산물일 때 독자가 알 수 없다.
    """
    rows: list[PnlRow] = []
    for i in range(6):
        sym = f"S{i}/USDT:USDT"
        rows.append(_row(sym, ARM_DEFAULT, mdd=0.20, trades=200.0))
        rows.append(_row(sym, ARM_FILTER, mdd=0.05, ret=0.05, trades=100.0))
        for seed in MATCH_SEEDS:
            # 매칭이 30% 더 거래한다 — 필터에 유리한 잔차.
            rows.append(_row(sym, ARM_MATCHED, seed=seed, mdd=0.15, ret=0.05, trades=130.0))
    test = matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)
    assert test.trade_gap_pct == pytest.approx(30.0)
    got = verdict([test], timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.SELECTIVE
    text = got.text
    assert "거래 수가 +30.0% 어긋난다" in text
    assert "할인해 읽어야 한다" in text


def test_verdict_always_flags_the_geometry_alternative() -> None:
    """(a) 판정은 「선별 알파」로 읽히면 안 된다 — 기하 경로가 열려 있음을 항상 적는다.

    이 모듈의 대조군이 배제하는 것은 **표본 축소** 하나뿐이다. 좁은 존은 1R이 작아 고정
    1.5R 익절이 가까워지므로, 같은 결과가 기하로도 설명된다(WAN-133 계열의 반복 교훈).
    """
    rows = _synthetic_pnl(filter_mdd=0.05, matched_mdd=0.15)
    tests = [matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)]
    text = verdict(tests, timeframe=_TIMEFRAME).text
    assert "기하" in text
    assert "채택이 아니다" in text


def test_symbol_mean_gate_excludes_small_cells() -> None:
    rows = _synthetic_pnl(filter_mdd=0.05, matched_mdd=0.15, symbols=3)
    rows.append(_row("S9/USDT:USDT", ARM_FILTER, trades=1.0, ret=99.0, mdd=0.0))
    gated = symbol_mean(
        rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS, arm=ARM_FILTER
    )
    assert gated["n_symbols"] == 3.0
    assert gated["n_excluded"] == 1.0
    assert gated["total_return"] == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# 4. 엔진이 핀 없이 오늘의 채택 기본값을 탄다
# --------------------------------------------------------------------------- #


def test_build_cell_uses_adopted_defaults_not_legacy_pins() -> None:
    """탐지가 **분리 존**(WAN-149 채택 기본값)이지 병합 존(WAN-133 핀)이 아니다.

    WAN-133 코드를 재사용하면서 핀까지 물려받으면 「병합 시절 숫자를 새 이슈 이름으로
    다시 내는」 조용한 실패가 된다 — 라벨이 아니라 **후보 집합**으로 확인한다.
    """
    market = _market(need_1m=True)
    cell = build_cell(market, params=ConfluenceParams())
    cfg = harness.build_config(_TIMEFRAME)
    separated, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=ConfluenceParams(),
        cfg=cfg,
        order_block_result=harness.detect_order_blocks(market, OrderBlockParams()),
    )
    merged, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=ConfluenceParams(),
        cfg=cfg,
        order_block_result=harness.detect_order_blocks(market, LEGACY_OB_PARAMS),
    )
    got = [(c.entry_time, c.entry_price) for c in cell.default_candidates]
    assert got == [(c.entry_time, c.entry_price) for c in separated]
    if separated != merged:
        assert got != [(c.entry_time, c.entry_price) for c in merged]


def test_band_bar_is_intrabar_live_not_pinned_tap() -> None:
    """진입가 정본이 `intrabar_live`다(WAN-132) — 이 모듈은 `pin_band_bar`를 쓰지 않는다."""
    params = harness.build_params(fill=harness.BASELINE_FILL)
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"
    assert params.deviation_filter.band_bar != harness.LEGACY_BAND_BAR


# --------------------------------------------------------------------------- #
# 5. 존폭 분포는 존 단위다
# --------------------------------------------------------------------------- #


def test_zone_width_samples_are_one_per_zone() -> None:
    """존 하나가 여러 번 탭돼도 표본은 하나다(「탭 분포」가 되면 안 된다)."""
    market = _market()
    obr = harness.detect_order_blocks(market, OrderBlockParams())
    taps = len(obr.retap_signals)
    samples = zone_width_samples(market, combine_obs=False)
    if not samples:
        pytest.skip("합성 데이터에서 탭이 나오지 않았다.")
    unique_zones = len({s.zone_key for s in obr.retap_signals})
    assert len(samples) <= unique_zones
    assert taps >= len(samples)
    assert all(v > 0 for v in samples)


def test_zone_width_distribution_shifts_with_merge() -> None:
    """병합 ON/OFF가 실제로 다른 분포를 낸다 — §0이 대조하는 축이 죽어 있지 않다."""
    market = _market()
    off = zone_width_samples(market, combine_obs=False)
    on = zone_width_samples(market, combine_obs=True)
    if not off or not on:
        pytest.skip("합성 데이터에서 탭이 나오지 않았다.")
    assert off != on


# --------------------------------------------------------------------------- #
# 6. 판정 문장
# --------------------------------------------------------------------------- #


def test_verdict_refuses_on_thin_symbol_coverage() -> None:
    """유효 심볼이 3개 미만이면 (a)/(b) 대신 「판정 불가」를 낸다(WAN-143 게이트와 같은 취지)."""
    rows = _synthetic_pnl(filter_mdd=0.01, matched_mdd=0.20, symbols=2)
    tests = [matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)]
    got = verdict(tests, timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.INDETERMINATE
    assert "판정 불가" in got.text


def test_verdict_reads_selection_when_filter_beats_matched() -> None:
    rows = _synthetic_pnl(filter_mdd=0.05, matched_mdd=0.15)
    tests = [matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)]
    got = verdict(tests, timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.SELECTIVE
    text = got.text
    assert "(a) 표본 축소로는 환원되지 않는다" in text
    assert "채택이 아니다" in text  # 판정이 곧 채택으로 읽히지 않도록.


def test_verdict_reads_sample_reduction_when_matched_reproduces() -> None:
    rows = _synthetic_pnl(filter_mdd=0.10, matched_mdd=0.09)
    tests = [matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)]
    got = verdict(tests, timeframe=_TIMEFRAME)
    assert got.kind is VerdictKind.SAMPLE_REDUCTION
    assert "(b) 표본 축소" in got.text


def test_summary_labels_the_symbols_it_actually_ran() -> None:
    """헤더가 실제로 돌린 심볼 수를 적는다 — 부분 실행에 「6심볼」 라벨이 붙으면 안 된다."""
    rows = _synthetic_pnl(filter_mdd=0.05, matched_mdd=0.15, symbols=2)
    result = ExperimentResult(
        pnl_rows=rows,
        matched_tests=[
            matched_test_row(rows, lens=LENS_PRIMARY, timeframe=_TIMEFRAME, segment=SEGMENT_OOS)
        ],
    )
    text = build_summary_markdown(result, timeframes=(_TIMEFRAME,))
    assert "2심볼(S0/S1)" in text
    assert "6심볼(BTC/ETH/SOL/BNB/XRP/TRX)" not in text


# --------------------------------------------------------------------------- #
# 7. 결론 문단이 판정과 어긋나지 않는다 (네 분기 전부)
# --------------------------------------------------------------------------- #


def _summary_for(cells: dict[str, tuple[float, float]], *, symbols: int = 6) -> str:
    """TF별 (필터 MDD, 매칭 MDD)로 요약 마크다운을 만든다."""
    rows: list[PnlRow] = []
    for timeframe, (filter_mdd, matched_mdd) in cells.items():
        rows += _synthetic_pnl(
            filter_mdd=filter_mdd,
            matched_mdd=matched_mdd,
            symbols=symbols,
            timeframe=timeframe,
        )
    tests = [
        matched_test_row(rows, lens=LENS_PRIMARY, timeframe=tf, segment=SEGMENT_OOS) for tf in cells
    ]
    result = ExperimentResult(pnl_rows=rows, matched_tests=tests)
    return build_summary_markdown(result, timeframes=tuple(cells))


_FALLBACK = "어느 작업 TF에서도 매칭 검정이 유효 표본을 얻지 못했다"


def test_conclusion_is_selective_when_every_tf_reads_a() -> None:
    """(a)가 나온 입력에서 결론 문단이 **폴백이 아니다**.

    🚨 이 테스트가 존재하는 이유: `_conclusion()`이 판정 문장의 부분문자열(`"(a) 선별"`)로
    분기하는 동안, `verdict()`의 실제 출력은 `"(a) 표본 축소로는 환원되지 않는다"`였다.
    그래서 **§판정은 (a)를 찍는데 §결론만 「판정 불가」를 찍었다** — 네 셀 전부 p=0.048이
    나온 격자에서 결론이 "유효 표본을 얻지 못했다"고 말했다. 이 저장소에서 나중에 인용되는
    것은 결론 문단이므로, 다음 이슈가 "WAN-142는 판정 못 냈다"로 인용하게 됐을 것이다.
    라벨이 아니라 **동작**으로 고정한다.
    """
    text = _summary_for({"15m": (0.05, 0.15), "1h": (0.05, 0.15)})
    assert "**(a) 두 작업 TF(15m, 1h) 모두" in text
    assert _FALLBACK not in text


def test_conclusion_is_sample_reduction_when_every_tf_reads_b() -> None:
    """(b)가 나온 입력에서는 「표본 축소의 산물」 결론이 나온다(같은 취약성을 함께 덮는다)."""
    text = _summary_for({"15m": (0.10, 0.09), "1h": (0.10, 0.09)})
    assert "**(b) 존폭 필터의 MDD 개선은 표본 축소의 산물이다" in text
    assert _FALLBACK not in text


def test_conclusion_is_split_when_tfs_disagree() -> None:
    """TF마다 판정이 갈리면 (c) — 「TF마다 다른 필터」는 새 자유 파라미터다."""
    text = _summary_for({"15m": (0.05, 0.15), "1h": (0.10, 0.09)})
    assert "**(c) TF에 갈린다" in text
    assert _FALLBACK not in text


def test_conclusion_falls_back_only_when_no_tf_is_decidable() -> None:
    """폴백은 **판정이 실제로 불가능할 때만** 나온다 — 유효 심볼이 3개 미만인 셀."""
    text = _summary_for({"15m": (0.05, 0.15), "1h": (0.05, 0.15)}, symbols=2)
    assert "⚠️ 판정 불가 — 채택 권고 없음." in text
    assert _FALLBACK in text
