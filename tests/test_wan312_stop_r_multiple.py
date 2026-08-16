"""WAN-312 손실 R 배수 스트레스 모듈 테스트.

고정 대상 넷 — 전부 **라벨이 아니라 동작**이다:

1. **k=1.00이 인자 없는 채택 북과 비트 동일**하다(`book_cli.build_book_rows`와 대조).
   변환이 항등이 아니면 이 표의 기준점이 통째로 깨진다.
2. 배수가 **청산가 하나만** 옮긴다 — 손절가·진입·거래 집합·시각은 불변이다(= 「손절선을
   넓히는 것이 아니다」는 이슈의 전제가 코드에서 참인지).
3. **계획** 동시 리스크는 k에 불변이고 **실효**만 k배로 움직인다(§2 주 산출물).
4. 스트레스 배수가 **청산 검사까지 실제로 닿는다**(충분히 큰 k에서 청산이 난다) — 안 닿으면
   「청산 0건이 유지되는 k의 상한」이 측정이 아니라 상수가 된다.
"""

from __future__ import annotations

import dataclasses

import pytest

from backtest import harness
from backtest.book_cli import build_book_rows
from backtest.leverage_book import BookCell, LeverageBookParams, run_leverage_book
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload, _segment_cells
from backtest.wan312_stop_r_multiple import (
    K_MULTIPLES,
    NEW_THREE,
    VARIANT_OBSERVED,
    VARIANT_PURE,
    apply_stop_multiple,
    build_grid,
    build_leave_one_out,
    restore_pre_backfill_funding,
    scenario_label,
    stop_multiple_candidate,
)
from backtest.zone_limit_backtest import _Candidate
from data.models import FundingRate
from execution.sizing import PositionSizingParams

_HOUR = 3_600_000


def _stop_candidate(entry_time: int, *, extreme: float = 80.0) -> _Candidate:
    """손절 청산 롱 후보 — 진입 100 · 손절 90(= 1R 10) · 그 봉 저가 80(= 2R)."""
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=entry_time + _HOUR,
        exit_price=90.0,  # 현행 = 손절가 그 값
        reason=ExitReason.STOP_LOSS,
        stop_price=90.0,
        exit_extreme=extreme,
        trigger_time=entry_time,
    )


def _payload(symbol: str, timeframe: str = "1h") -> CellPayload:
    cands = (_stop_candidate(0), _stop_candidate(10 * _HOUR))
    return CellPayload(
        symbol=symbol,
        timeframe=timeframe,
        boundary_ms=0,  # 전 후보가 따뜻한 경계 이후 → oos_warm이 전부 포함
        candidates={harness.SEGMENT_FULL: cands, harness.SEGMENT_OOS: cands},
        funding={harness.SEGMENT_FULL: (), harness.SEGMENT_OOS: ()},
        rows=(),
    )


# --------------------------------------------------------------------------- #
# 변환 산식
# --------------------------------------------------------------------------- #


def test_pure_multiple_long_math() -> None:
    cand = _stop_candidate(0)
    # 1R = 10. k=1 → 손절가 그대로(항등) · k=1.5 → 진입가 −15 = 85 · k=2 → 80.
    assert stop_multiple_candidate(cand, 1.0, VARIANT_PURE).exit_price == 90.0
    assert stop_multiple_candidate(cand, 1.5, VARIANT_PURE).exit_price == pytest.approx(85.0)
    assert stop_multiple_candidate(cand, 2.0, VARIANT_PURE).exit_price == pytest.approx(80.0)
    # `무조건` 팔은 봉이 실제로 간 데(80)를 넘어서도 간다 — 가정이지 관측이 아니다.
    assert stop_multiple_candidate(cand, 3.0, VARIANT_PURE).exit_price == pytest.approx(70.0)


def test_pure_multiple_k_one_is_exactly_stop_price() -> None:
    """k=1은 부동소수 오차 없이 **정확히** 손절가여야 한다(비트 동일의 뿌리)."""
    for entry, stop in (
        (100.0, 90.0),
        (0.3141592653589793, 0.27182818284590454),
        (61234.5, 60987.3),
    ):
        cand = dataclasses.replace(
            _stop_candidate(0),
            entry_price=entry,
            stop_price=stop,
            exit_extreme=stop - (entry - stop),  # 봉이 2R까지 갔다(클립이 안 걸리는 값)
        )
        for variant in (VARIANT_PURE, VARIANT_OBSERVED):
            assert stop_multiple_candidate(cand, 1.0, variant).exit_price == stop


def test_observed_multiple_clips_at_bar_extreme() -> None:
    """`봉극값` 팔은 1분봉이 실제로 간 데서 멈춘다 — k를 키워도 α=1로 수렴한다."""
    cand = _stop_candidate(0, extreme=85.0)  # 봉 저가가 1.5R
    assert stop_multiple_candidate(cand, 1.25, VARIANT_OBSERVED).exit_price == pytest.approx(87.5)
    assert stop_multiple_candidate(cand, 1.5, VARIANT_OBSERVED).exit_price == pytest.approx(85.0)
    # k를 더 키워도 봉 저가를 넘지 않는다(= WAN-277 α=1과 같은 값).
    assert stop_multiple_candidate(cand, 5.0, VARIANT_OBSERVED).exit_price == pytest.approx(85.0)
    # 같은 k에서 `무조건` 팔은 그 너머로 간다 — 그 차이가 「1분봉이 못 보는 몫」이다.
    assert stop_multiple_candidate(cand, 5.0, VARIANT_PURE).exit_price == pytest.approx(50.0)


def test_short_side_is_mirrored() -> None:
    short = dataclasses.replace(
        _stop_candidate(0),
        side=PositionSide.SHORT,
        entry_price=100.0,
        exit_price=110.0,
        stop_price=110.0,
        exit_extreme=115.0,  # 봉 고가 = 1.5R
    )
    assert stop_multiple_candidate(short, 1.5, VARIANT_PURE).exit_price == pytest.approx(115.0)
    assert stop_multiple_candidate(short, 2.0, VARIANT_PURE).exit_price == pytest.approx(120.0)
    assert stop_multiple_candidate(short, 2.0, VARIANT_OBSERVED).exit_price == pytest.approx(115.0)


def test_non_stop_and_degenerate_candidates_untouched() -> None:
    tp = dataclasses.replace(_stop_candidate(0), reason=ExitReason.TAKE_PROFIT, exit_price=110.0)
    assert stop_multiple_candidate(tp, 2.0, VARIANT_PURE) is tp
    zero_r = dataclasses.replace(_stop_candidate(0), stop_price=100.0)
    assert stop_multiple_candidate(zero_r, 2.0, VARIANT_PURE) is zero_r
    no_extreme = dataclasses.replace(_stop_candidate(0), exit_extreme=None)
    assert stop_multiple_candidate(no_extreme, 2.0, VARIANT_OBSERVED) is no_extreme


def test_unknown_variant_is_rejected() -> None:
    with pytest.raises(ValueError, match="변형"):
        apply_stop_multiple([_payload("BTCUSDT")], 1.5, "worst_case")


def test_multiple_moves_only_exit_price() -> None:
    """배수는 **청산가 하나만** 옮긴다 — 손절선·진입·거래 집합은 불변이다."""
    payloads = [_payload("BTCUSDT")]
    out = apply_stop_multiple(payloads, 1.5, VARIANT_PURE)
    before = payloads[0].candidates[harness.SEGMENT_FULL]
    after = out[0].candidates[harness.SEGMENT_FULL]
    assert len(after) == len(before)  # 거래 집합 불변
    for old, new in zip(before, after, strict=True):
        assert new.exit_price != old.exit_price
        assert (new.stop_price, new.entry_price, new.entry_time, new.exit_time) == (
            old.stop_price,
            old.entry_price,
            old.entry_time,
            old.exit_time,
        )
    # 원본 payload는 손대지 않는다(사후 변환은 새 객체를 낸다).
    assert payloads[0].candidates[harness.SEGMENT_FULL][0].exit_price == 90.0


def test_reentry_candidates_get_the_same_multiple() -> None:
    """재진입 손절(WAN-273 채택)도 같은 갭을 겪는다 — 변환에서 빠지면 스트레스가 반쪽이다."""
    base = _payload("BTCUSDT")
    payload = dataclasses.replace(
        base, reentry_candidates={harness.SEGMENT_FULL: (_stop_candidate(5 * _HOUR),)}
    )
    out = apply_stop_multiple([payload], 1.5, VARIANT_PURE)
    assert out[0].reentry_candidates[harness.SEGMENT_FULL][0].exit_price == pytest.approx(85.0)


# --------------------------------------------------------------------------- #
# 검산 (a) — k=1.00 ≡ 인자 없는 채택 북
# --------------------------------------------------------------------------- #


def test_k_one_is_bit_identical_to_adopted_book() -> None:
    """k=1.00 행이 채택 북 경로(`book_cli.build_book_rows` = 인자 없는 `backtest.run`)와 같다.

    라벨 대조가 아니라 **같은 payload를 두 경로에 태운** 비트 대조다 — 재진입 포함 여부·북
    파라미터·후보 변환 중 하나라도 어긋나면 갈라진다.
    """
    payloads = [_payload("BTCUSDT"), _payload("ETHUSDT")]
    mine = {
        (r.segment, r.variant): r
        for r in build_grid(payloads, ["1h"])
        if r.k == 1.0 and not r.exclude
    }
    theirs = {
        r.segment: r
        for r in build_book_rows(
            payloads,
            book=LeverageBookParams(),
            segments=(harness.SEGMENT_FULL, harness.SEGMENT_OOS_WARM, harness.SEGMENT_OOS),
            start_ms=0,
            end_ms=100 * _HOUR,
        )
    }
    assert theirs  # 대조군이 비어 있으면 검산이 아니다.
    for (segment, _variant), row in mine.items():
        ref = theirs[segment]
        assert row.total_return == ref.total_return
        assert row.max_drawdown == ref.max_drawdown
        assert row.num_trades == ref.num_trades
        assert row.max_concurrent_risk == ref.max_concurrent_risk
        assert row.liquidation_events == ref.liquidation_events


def test_k_one_variants_are_identical_rows() -> None:
    """k=1.00에서 두 변형은 정의상 같은 행 — 격자에 박힌 자기 검산."""
    rows = [r for r in build_grid([_payload("BTCUSDT")], ["1h"]) if r.k == 1.0]
    by_variant: dict[str, dict[str, float]] = {}
    for r in rows:
        by_variant.setdefault(r.variant, {})[r.segment] = r.total_return
    assert by_variant[VARIANT_PURE] == by_variant[VARIANT_OBSERVED]


# --------------------------------------------------------------------------- #
# §2 계획 대 실효 동시 리스크
# --------------------------------------------------------------------------- #


def test_effective_risk_scales_planned_stays() -> None:
    """계획 동시 리스크는 k에 **불변**, 실효만 k배 — 이 이슈의 주 산출물."""
    rows = [
        r
        for r in build_grid([_payload("BTCUSDT")], ["1h"])
        if r.segment == harness.SEGMENT_FULL and r.variant == VARIANT_PURE
    ]
    by_k = {r.k: r for r in rows}
    planned = {r.max_concurrent_risk for r in rows}
    assert len(planned) == 1  # 손절 체결 보수화에 설계상 불변
    base = by_k[1.0]
    for k in K_MULTIPLES:
        assert by_k[k].max_effective_concurrent_risk == pytest.approx(base.max_concurrent_risk * k)


def test_grid_scenarios_and_monotone_damage() -> None:
    """격자가 k × 변형을 전 구간에 내고, k가 커질수록 손실이 깊어진다."""
    rows = build_grid([_payload("BTCUSDT"), _payload("ETHUSDT")], ["1h"])
    assert {r.scenario for r in rows} == {
        scenario_label(k, v) for k in K_MULTIPLES for v in (VARIANT_PURE, VARIANT_OBSERVED)
    }
    full = {(r.k, r.variant): r for r in rows if r.segment == harness.SEGMENT_FULL}
    for variant in (VARIANT_PURE, VARIANT_OBSERVED):
        for lo, hi in zip(K_MULTIPLES, K_MULTIPLES[1:], strict=False):
            assert full[(hi, variant)].total_return <= full[(lo, variant)].total_return
            assert full[(hi, variant)].max_drawdown >= full[(lo, variant)].max_drawdown
    # `봉극값` 팔은 봉이 간 데(2R)에서 멈추므로 k=2를 넘어가면 `무조건`보다 덜 아프다.
    assert full[(2.0, VARIANT_OBSERVED)].total_return >= full[(2.0, VARIANT_PURE)].total_return


# --------------------------------------------------------------------------- #
# leave-one-out
# --------------------------------------------------------------------------- #


def test_leave_one_out_drops_each_symbol_and_the_new_three() -> None:
    payloads = [_payload(s) for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "DOTUSDT", "BCHUSDT")]
    rows = build_leave_one_out(payloads, ["1h"])
    labels = {r.exclude for r in rows}
    assert labels == {"-BTC", "-ETH", "-ADA", "-DOT", "-BCH", "-new3"}
    per_symbol = {r.num_symbols for r in rows if r.exclude != "-new3"}
    assert per_symbol == {4}  # 하나씩 뺐다
    new3 = {r.num_symbols for r in rows if r.exclude == "-new3"}
    assert new3 == {2}  # ADA·DOT·BCH 셋을 한꺼번에 뺐다
    assert set(NEW_THREE) == {"ADA", "DOT", "BCH"}


# --------------------------------------------------------------------------- #
# 엔진 노브 — 스트레스 배수가 청산 검사까지 닿는가
# --------------------------------------------------------------------------- #


def _liq_cfg() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=10_000.0,
        risk_sizing=PositionSizingParams(
            sizing_mode="risk_pct",
            risk_per_trade=0.01,
            leverage=1.0,
            min_stop_distance_fraction=0.0,
        ),
    )


def _liq_cells() -> list[BookCell]:
    """겹치는 두 칸 — 각 거래의 계획 리스크가 자본의 1%다."""
    return [
        BookCell(symbol="BTCUSDT", timeframe="1h", candidates=[_stop_candidate(0)]),
        BookCell(symbol="ETHUSDT", timeframe="1h", candidates=[_stop_candidate(0)]),
    ]


def test_stress_multiple_reaches_liquidation_check() -> None:
    """k가 최악 가정 청산 검사에 **실제로** 실린다 — 안 실리면 「k 상한」이 상수가 된다."""
    cells, cfg, book = _liq_cells(), _liq_cfg(), LeverageBookParams()
    assert not run_leverage_book(cells, cfg, book).stats.liquidations
    stressed = run_leverage_book(cells, cfg, book, stress_risk_multiple=90.0)
    assert stressed.stats.liquidations  # 계획 2% × 90 → 자본을 넘어 마진콜 사거리


def test_stress_multiple_one_is_bit_identical() -> None:
    """k=1.0은 예전 호출과 비트 동일하고, 실효 열이 계획 열과 정확히 같다."""
    cells, cfg, book = _liq_cells(), _liq_cfg(), LeverageBookParams()
    plain = run_leverage_book(cells, cfg, book).stats
    explicit = run_leverage_book(cells, cfg, book, stress_risk_multiple=1.0).stats
    assert plain.max_concurrent_risk_ratio == explicit.max_concurrent_risk_ratio
    assert plain.max_effective_concurrent_risk_ratio == plain.max_concurrent_risk_ratio
    assert plain.max_open_notional_ratio == explicit.max_open_notional_ratio


def test_stress_multiple_below_one_is_rejected() -> None:
    """계획보다 **유리한** 손절 체결은 이 축이 아니다 — 조용히 받지 않는다."""
    with pytest.raises(ValueError, match="stress_risk_multiple"):
        run_leverage_book(_liq_cells(), _liq_cfg(), LeverageBookParams(), stress_risk_multiple=0.5)


def test_restore_pre_backfill_funding_only_clears_the_new_three() -> None:
    """검산 (b) 전용 복원기 — WAN-292 백필 **이전** 데이터 상태를 되돌린다.

    좌표(유니버스·대리·유동성 한도)만으로는 wan277이 재현되지 않는다 — 그 세 종목의 펀딩이
    그 사이 생겼기 때문이다. 기존 종목의 펀딩까지 지우면 재현이 **다른 이유로** 깨지므로
    대상이 정확히 셋인지 동작으로 고정한다.
    """
    rates = (FundingRate(symbol="X", funding_time=0, rate=0.0001),)
    payloads = [
        dataclasses.replace(_payload(s), funding=dict.fromkeys((harness.SEGMENT_FULL,), rates))
        for s in ("BTCUSDT", "DOGEUSDT", "LINKUSDT", "LTCUSDT", "ADAUSDT")
    ]
    out = {p.symbol: p for p in restore_pre_backfill_funding(payloads)}
    assert out["BTCUSDT"].funding[harness.SEGMENT_FULL] == rates  # 기존 종목은 그대로
    assert out["ADAUSDT"].funding[harness.SEGMENT_FULL] == rates  # WAN-307 합류분도 그대로
    for symbol in ("DOGEUSDT", "LINKUSDT", "LTCUSDT"):
        assert out[symbol].funding[harness.SEGMENT_FULL] == ()  # WAN-292 백필분만 비운다


def test_segment_cells_untouched_by_transform() -> None:
    """변환 후에도 칸 구성(칸 수·심볼)은 그대로다 — 거래 집합이 아니라 청산가만 바뀐다."""
    payloads = [_payload("BTCUSDT"), _payload("ETHUSDT")]
    before = _segment_cells(payloads, harness.SEGMENT_FULL, "")
    after = _segment_cells(
        apply_stop_multiple(payloads, 2.0, VARIANT_PURE), harness.SEGMENT_FULL, ""
    )
    assert [(c.symbol, len(c.candidates)) for c in before] == [
        (c.symbol, len(c.candidates)) for c in after
    ]
