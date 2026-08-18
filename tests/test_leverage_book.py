"""레버리지 북 회계 테스트 (WAN-169).

핵심 고정 대상 세 가지: (1) **칸 하나짜리 북 = 채택 단일 포지션 시퀀서**(비트 단위 —
새 회계가 기존 엔진의 상위집합이라는 증명), (2) **칸당 1포지션 + 공유 자본 + 사이징
N배**가 라벨이 아니라 동작으로 존재한다는 것, (3) **straddle 회계 (b)**(워밍업 셋업은
배치조차 하지 않는다)와 **인과성**(미래를 잘라도 그 전에 끝난 거래는 그대로)이 동작으로
고정된다는 것.
"""

from __future__ import annotations

import pytest

from backtest.leverage_book import (
    LEGACY_BOOK_PARAMS,
    BookCell,
    LeverageBookParams,
    apply_book_leverage,
    run_leverage_book,
    scale_sizing_params,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import PartialExit
from backtest.zone_limit_backtest import _Candidate, _to_trade, sequence_with_candidates
from data.models import FundingRate
from execution.sizing import PositionSizingParams
from strategy.models import SignalExitReason


def _cand(
    entry_time: int,
    exit_time: int,
    *,
    entry_price: float = 100.0,
    exit_price: float = 101.5,
    stop_price: float = 99.0,
    reason: ExitReason = ExitReason.TAKE_PROFIT,
    trigger_time: int | None = None,
    adv_usd: float | None = None,
) -> _Candidate:
    """실제 엔진 자료형(`_Candidate`) 그대로 만든 테스트 후보.

    구조 흉내(dataclass 대역)가 아니라 실물을 쓴다 — 북이 `_to_trade`(실제 비용·사이징)
    를 태우므로 대역이면 사이징 검증이 라벨 검증으로 퇴화한다.
    """
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        reason=reason,
        stop_price=stop_price,
        trigger_time=entry_time if trigger_time is None else trigger_time,
        adv_usd=adv_usd,
    )


def _cfg(
    *,
    risk_per_trade: float = 0.01,
    leverage: float = 1.0,
    sizing_mode: str = "risk_pct",
    notional_fraction: float = 1.0,
) -> BacktestConfig:
    return BacktestConfig(
        initial_capital=10_000.0,
        risk_sizing=PositionSizingParams(
            sizing_mode=sizing_mode,
            risk_per_trade=risk_per_trade,
            leverage=leverage,
            notional_fraction=notional_fraction,
            min_stop_distance_fraction=0.0,
        ),
    )


def _cell(symbol: str, timeframe: str, candidates: list[_Candidate]) -> BookCell:
    return BookCell(symbol=symbol, timeframe=timeframe, candidates=candidates)


# --------------------------------------------------------------------------- #
# 기본 경로 불변: 칸 하나짜리 북 = 채택 단일 포지션 시퀀서
# --------------------------------------------------------------------------- #


def test_single_cell_book_matches_adopted_sequencer_bit_for_bit() -> None:
    """칸이 하나면 북은 `sequence_with_candidates`(채택 엔진)와 같은 거래를 낸다.

    칸당 1포지션이 겹침을 다 막아 `open_notional`이 항상 0이므로, 배수 1에서 사이징도
    시퀀싱도 단일 포지션 경로와 완전히 같아야 한다 — 새 회계가 기존 규칙의 확장이라는
    구조적 증명이자, 북 쪽 배선 실수를 비트 비교로 잡는 그물이다.
    """
    cfg = _cfg()
    candidates = [
        _cand(1_000, 2_000),
        _cand(1_500, 2_500, exit_price=98.0, reason=ExitReason.STOP_LOSS),  # 겹침 → 스킵돼야
        _cand(2_000, 3_000, exit_price=98.0, reason=ExitReason.STOP_LOSS),
        _cand(3_500, 4_000),
    ]
    adopted = [trade for _, trade in sequence_with_candidates(candidates, cfg)]
    outcome = run_leverage_book([_cell("BTC/USDT:USDT", "1h", candidates)], cfg, LEGACY_BOOK_PARAMS)
    assert outcome.trades == adopted
    assert outcome.stats.skipped_cell_busy == 1
    assert outcome.stats.peak_concurrency == 1


# --------------------------------------------------------------------------- #
# 칸당 1포지션 + 칸 간 동시 허용 (사용자 정의)
# --------------------------------------------------------------------------- #


def test_same_cell_overlap_skipped_but_other_cell_enters() -> None:
    """같은 칸의 겹침은 스킵, 다른 칸(다른 TF 포함)은 동시에 열린다."""
    cfg = _cfg(leverage=10.0)  # 명목 상한이 판정을 가리지 않게 넉넉히.
    a = _cand(1_000, 5_000)
    a_overlap = _cand(2_000, 6_000)  # 같은 칸 — 스킵돼야 한다.
    b = _cand(2_000, 6_000)  # 같은 심볼, 다른 TF — 별개 칸이라 들어가야 한다.
    outcome = run_leverage_book(
        [
            _cell("BTC/USDT:USDT", "15m", [a, a_overlap]),
            _cell("BTC/USDT:USDT", "1h", [b]),
        ],
        cfg,
        LEGACY_BOOK_PARAMS,
    )
    assert outcome.stats.placed == 2
    assert outcome.stats.skipped_cell_busy == 1
    assert outcome.stats.peak_concurrency == 2


def test_cell_frees_at_exit_time_half_open() -> None:
    """청산 시각 == 진입 시각(반개구간)이면 같은 칸의 연속 거래가 허용된다."""
    cfg = _cfg()
    outcome = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", [_cand(1_000, 2_000), _cand(2_000, 3_000)])],
        cfg,
        LEGACY_BOOK_PARAMS,
    )
    assert outcome.stats.placed == 2
    assert outcome.stats.skipped_cell_busy == 0


def test_duplicate_cell_key_rejected() -> None:
    """같은 (종목, TF) 칸이 두 번 들어오면 거부한다 — 칸당 1포지션 전제가 깨진다."""
    cfg = _cfg()
    cells = [
        _cell("BTC/USDT:USDT", "1h", [_cand(1_000, 2_000)]),
        _cell("BTC/USDT:USDT", "1h", [_cand(3_000, 4_000)]),
    ]
    with pytest.raises(ValueError, match="칸이 중복"):
        run_leverage_book(cells, cfg, LEGACY_BOOK_PARAMS)


# --------------------------------------------------------------------------- #
# WAN-244 — 유동성 한도(일거래량 비례 절대 명목 상한)이 북에서 동작으로 존재한다
# --------------------------------------------------------------------------- #


def _adv_cfg(fraction: float | None) -> BacktestConfig:
    """유동성 한도 프랙션만 얹은 채택-형 cfg(cap_only 북과 함께 쓴다)."""
    cfg = _cfg(leverage=1.0)  # cap_only 북이 상한을 5배로 연다.
    assert cfg.risk_sizing is not None
    sizing = cfg.risk_sizing.model_copy(update={"max_notional_adv_fraction": fraction})
    return cfg.model_copy(update={"risk_sizing": sizing})


def test_adv_cap_off_reproduces_book_bit_for_bit() -> None:
    """상한이 꺼져 있으면(fraction=None) 후보에 `adv_usd`가 실려 있어도 거래가 비트 동일하다.

    `adv_usd`는 순수 메타데이터라 상한이 꺼진 한 사이징에 영향을 주지 않는다 —
    이것이 「기본 꺼짐 = wan180 채택 셀 비트 재현」의 회계 단위 보증이다.
    """
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    # 같은 후보를 adv_usd 유무만 다르게.
    plain = [_cand(1_000, 2_000), _cand(3_000, 4_000)]
    with_adv = [_cand(1_000, 2_000, adv_usd=100.0), _cand(3_000, 4_000, adv_usd=100.0)]
    out_plain = run_leverage_book([_cell("BTC/USDT:USDT", "1h", plain)], _cfg(leverage=1.0), book)
    out_adv = run_leverage_book([_cell("BTC/USDT:USDT", "1h", with_adv)], _adv_cfg(None), book)
    assert out_adv.trades == out_plain.trades
    assert out_adv.stats.adv_capped_entries == 0
    assert out_adv.stats.first_adv_cap_equity is None


def test_adv_cap_binds_and_is_recorded() -> None:
    """상한을 켜면 명목이 `k×ADV_usd`로 잘리고 발동이 계측된다.

    ADV=100_000 · k=0.5% → 상한 명목 500. 리스크 사이징 명목(자본 10_000, 손절거리 1 →
    명목 10_000)이 그보다 크므로 상한이 구속한다 — 명목 500, 수량 5.
    """
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    cand = _cand(1_000, 2_000, adv_usd=100_000.0)
    off = run_leverage_book([_cell("BTC/USDT:USDT", "1h", [cand])], _adv_cfg(None), book)
    on = run_leverage_book([_cell("BTC/USDT:USDT", "1h", [cand])], _adv_cfg(0.005), book)
    # 상한 끔: 리스크 사이징 명목 = 10_000(자본×1% / 손절거리 1 = 100주 × 100).
    off_notional = off.trades[0].entry_price * off.trades[0].quantity
    on_notional = on.trades[0].entry_price * on.trades[0].quantity
    assert off_notional == pytest.approx(10_000.0)
    assert on_notional == pytest.approx(500.0)  # k×ADV = 0.005 × 100_000.
    assert on.stats.adv_capped_entries == 1
    assert on.stats.first_adv_cap_time == on.trades[0].entry_time
    assert on.stats.first_adv_cap_equity == pytest.approx(10_000.0)
    # 발동은 clamped_entries의 부분집합이다.
    assert on.stats.clamped_entries >= on.stats.adv_capped_entries


def test_adv_cap_not_counted_when_liquidity_ample() -> None:
    """ADV가 커서 상한이 리스크 사이징 명목보다 크면 발동으로 세지 않는다(구속 안 함)."""
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    # ADV=10^9 · k=0.5% → 상한 5_000_000 ≫ 리스크 명목 10_000. 안 물린다.
    cand = _cand(1_000, 2_000, adv_usd=1_000_000_000.0)
    on = run_leverage_book([_cell("BTC/USDT:USDT", "1h", [cand])], _adv_cfg(0.005), book)
    assert on.stats.adv_capped_entries == 0
    assert on.stats.first_adv_cap_equity is None
    assert on.trades[0].entry_price * on.trades[0].quantity == pytest.approx(10_000.0)


# --------------------------------------------------------------------------- #
# 공유 자본: 실현 손익이 다음 진입의 사이징 자본이 된다
# --------------------------------------------------------------------------- #


def test_realized_pnl_flows_into_other_cells_sizing() -> None:
    """칸 A의 실현 손익이 칸 B의 사이징 자본에 반영된다 — 「한 지갑」의 동작 증명."""
    cfg = _cfg()
    win = _cand(1_000, 2_000)  # +1.5R 익절 → 현금 증가.
    later = _cand(3_000, 4_000)
    lone = run_leverage_book([_cell("ETH/USDT:USDT", "1h", [later])], cfg, LEGACY_BOOK_PARAMS)
    shared = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", [win]), _cell("ETH/USDT:USDT", "1h", [later])],
        cfg,
        LEGACY_BOOK_PARAMS,
    )
    qty_alone = lone.trades[0].quantity
    qty_after_win = shared.trades[1].quantity
    assert shared.trades[0].realized_pnl > 0
    # 승리 후 자본이 커졌으니 같은 셋업의 수량도 커져야 한다(자본이 공유되지 않으면 같다).
    assert qty_after_win > qty_alone


# --------------------------------------------------------------------------- #
# 명목 상한: 공유 자본 × (기본 leverage × N)
# --------------------------------------------------------------------------- #


def test_notional_cap_shared_across_cells_and_relative_headroom_invariant() -> None:
    """한 칸이 상한을 다 쓰면 다른 칸은 스킵된다 — 그리고 그 판정은 배수와 무관하다.

    손절 1%·리스크 1%면 자연 명목이 자본과 같아 leverage 1배 clamp에 정확히 걸리고
    (WAN-154가 실측한 그 발동), 그 상태에서 두 번째 칸은 여유가 0이다. 배수 N은 매
    거래 크기와 상한을 **함께** N배 하므로(사용자 확정 모델) 상대 여유가 불변이다 —
    "N배로 올리면 겹칠 자리가 는다"가 아니라 "모든 것이 N배로 커진다"는 것이 이 모델의
    핵심 성질이고, 이 테스트가 그것을 동작으로 고정한다(cap-only 모델과의 차이).
    """
    a = _cand(1_000, 5_000)
    b = _cand(2_000, 6_000)
    cells = [_cell("BTC/USDT:USDT", "1h", [a]), _cell("ETH/USDT:USDT", "1h", [b])]

    one_x = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=1.0, leverage_mode="combined")
    )
    assert one_x.stats.placed == 1
    assert one_x.stats.skipped_notional == 1

    three_x = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=3.0, leverage_mode="combined")
    )
    assert three_x.stats.placed == 1  # 배치 집합은 그대로 —
    assert three_x.stats.skipped_notional == 1
    # — 크기만 3배다.
    assert three_x.trades[0].quantity == pytest.approx(one_x.trades[0].quantity * 3.0)


def test_partial_headroom_clamps_entry() -> None:
    """여유가 남되 원하는 명목보다 작으면 축소 진입으로 세어진다."""
    # 첫 후보는 손절이 멀어(2%) 자연 명목 = 자본의 절반 → 남은 절반이 둘째의 천장.
    a = _cand(1_000, 5_000, stop_price=98.0)
    b = _cand(2_000, 6_000)  # 자연 명목 = 자본×1 > 남은 절반 → clamp.
    outcome = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", [a]), _cell("ETH/USDT:USDT", "1h", [b])],
        _cfg(),
        LEGACY_BOOK_PARAMS,
    )
    assert outcome.stats.placed == 2
    assert outcome.stats.clamped_entries == 1


# --------------------------------------------------------------------------- #
# 레버리지 = 매 거래 사이징 N배 (사용자 확정 모델)
# --------------------------------------------------------------------------- #


def test_multiple_scales_every_trade_size() -> None:
    """배수 N은 상한만 여는 게 아니라 **매 거래의 수량을 N배** 키운다."""
    cells = [_cell("BTC/USDT:USDT", "1h", [_cand(1_000, 2_000)])]
    base = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=1.0, leverage_mode="combined")
    )
    tripled = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=3.0, leverage_mode="combined")
    )
    assert tripled.trades[0].quantity == pytest.approx(base.trades[0].quantity * 3.0)
    # 리스크 비율도 N배로 계측된다(1% → 3%).
    assert tripled.stats.max_concurrent_risk_ratio == pytest.approx(
        base.stats.max_concurrent_risk_ratio * 3.0
    )


def test_scale_sizing_params_scales_all_three_knobs() -> None:
    sizing = PositionSizingParams(
        risk_per_trade=0.01, leverage=1.0, notional_fraction=0.5, min_stop_distance_fraction=0.0
    )
    scaled = scale_sizing_params(sizing, 5.0)
    assert scaled.risk_per_trade == pytest.approx(0.05)
    assert scaled.leverage == pytest.approx(5.0)
    assert scaled.notional_fraction == pytest.approx(2.5)


def test_fixed_notional_mode_scales_with_multiple() -> None:
    """`fixed_notional`(시드 분할) 모드에서도 배수가 명목을 키운다."""
    cells = [_cell("BTC/USDT:USDT", "1h", [_cand(1_000, 2_000)])]
    cfg = _cfg(sizing_mode="fixed_notional", notional_fraction=0.25, leverage=1.0)
    base = run_leverage_book(
        cells, cfg, LeverageBookParams(leverage_multiple=1.0, leverage_mode="combined")
    )
    doubled = run_leverage_book(
        cells, cfg, LeverageBookParams(leverage_multiple=2.0, leverage_mode="combined")
    )
    assert doubled.trades[0].quantity == pytest.approx(base.trades[0].quantity * 2.0)


def test_apply_book_leverage_rejects_missing_risk_sizing() -> None:
    """전액 진입 모드(risk_sizing=None)는 배수를 정의할 수 없어 거부한다."""
    cfg = BacktestConfig(initial_capital=10_000.0, risk_sizing=None)
    with pytest.raises(ValueError, match="리스크 사이징"):
        apply_book_leverage(
            cfg, LeverageBookParams(leverage_multiple=2.0, leverage_mode="combined")
        )


# --------------------------------------------------------------------------- #
# 최악 가정 청산 검사 (WAN-103 결정 4를 공유 자본 위에서)
# --------------------------------------------------------------------------- #


def test_liquidation_event_recorded_at_high_multiple() -> None:
    """전 포지션 동시 손절 가정이 유지증거금을 뚫으면 청산 이벤트로 계측된다."""
    cells = [_cell("BTC/USDT:USDT", "1h", [_cand(1_000, 2_000)])]
    calm = run_leverage_book(
        cells,
        _cfg(),
        LeverageBookParams(
            leverage_multiple=1.0, maintenance_margin_rate=0.25, leverage_mode="combined"
        ),
    )
    assert calm.stats.liquidations == []
    # 배수 5: 명목 ≈ 자본×5 → 유지증거금 1.25×자본 > 최악 자본(0.95×자본) → 트리거.
    risky = run_leverage_book(
        cells,
        _cfg(),
        LeverageBookParams(
            leverage_multiple=5.0, maintenance_margin_rate=0.25, leverage_mode="combined"
        ),
    )
    assert len(risky.stats.liquidations) == 1
    assert risky.stats.liquidated


# --------------------------------------------------------------------------- #
# 따뜻한 OOS × straddle 회계 (b): 워밍업 셋업은 배치조차 하지 않는다
# --------------------------------------------------------------------------- #


def test_straddle_position_does_not_occupy_capital_or_cell() -> None:
    """워밍업에 탭이 나 경계를 넘어 사는 셋업이 평가 초입의 칸·자본을 점유하지 않는다.

    (b)가 아니라면 straddle 포지션이 칸을 잠가 평가 첫 진입이 스킵되거나, 명목 상한을
    먹어 축소됐을 것이다 — 둘 다 일어나지 않아야 한다(WAN-169 사용자 결정).
    """
    boundary = 5_000
    straddle = _cand(1_000, 9_000, trigger_time=1_000)  # 경계(5_000)를 넘어 산다.
    fresh = _cand(6_000, 8_000, trigger_time=6_000)  # 평가 창 셋업 — straddle과 겹친다.
    cells = [_cell("BTC/USDT:USDT", "1h", [straddle, fresh])]
    outcome = run_leverage_book(cells, _cfg(), LEGACY_BOOK_PARAMS, eval_from_ms=boundary)

    assert outcome.stats.placed == 1  # straddle은 배치조차 되지 않았다.
    assert outcome.stats.skipped_cell_busy == 0  # 칸을 잠그지도 않았다.
    only = outcome.trades[0]
    assert only.entry_time == 6_000
    # 신선한 초기자본 그대로 사이징됐다(워밍업 손익·점유가 스며들지 않았다).
    lone = run_leverage_book([_cell("BTC/USDT:USDT", "1h", [fresh])], _cfg(), LEGACY_BOOK_PARAMS)
    assert only.quantity == pytest.approx(lone.trades[0].quantity)


def test_eval_filter_uses_trigger_time_not_entry_time() -> None:
    """평가 경계 판정은 진입 시각이 아니라 **탭 시각**이다(WAN-166 규약)."""
    boundary = 5_000
    # 탭은 경계 전(4_000), 체결은 경계 후(6_000) — 워밍업 셋업이므로 배치되지 않아야 한다.
    warm_tap = _cand(6_000, 8_000, trigger_time=4_000)
    outcome = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", [warm_tap])],
        _cfg(),
        LEGACY_BOOK_PARAMS,
        eval_from_ms=boundary,
    )
    assert outcome.stats.placed == 0


# --------------------------------------------------------------------------- #
# 인과성: 미래를 잘라도 그 전에 끝난 거래는 비트 단위로 같다
# --------------------------------------------------------------------------- #


def test_book_causality_truncating_future_keeps_past_trades() -> None:
    """시각 T 이후를 잘라낸 실행과 전체 실행에서, T 이전에 청산까지 끝난 거래가 같다.

    자름의 의미는 실데이터 절단과 같다: T 이후 진입 후보는 사라지고, T를 넘겨 살던
    후보는 T에서 강제 청산(`END_OF_DATA`)된다. 북 회계에 미래 참조가 하나라도 있으면
    (뒤 후보가 앞 배치를 바꾸면) 이 비교가 깨진다.
    """
    cut = 5_000
    full_cells = [
        _cell(
            "BTC/USDT:USDT",
            "1h",
            [_cand(1_000, 2_000), _cand(3_000, 9_000, exit_price=97.0), _cand(9_500, 9_900)],
        ),
        _cell("ETH/USDT:USDT", "1h", [_cand(1_500, 4_500), _cand(6_000, 7_000)]),
    ]

    def truncate(cand: _Candidate) -> _Candidate | None:
        if cand.entry_time > cut:
            return None
        if cand.exit_time > cut:
            return _cand(
                cand.entry_time,
                cut,
                entry_price=cand.entry_price,
                exit_price=cand.entry_price,  # 절단 강제 청산가는 손익 0으로 단순화.
                stop_price=cand.stop_price,
                reason=ExitReason.END_OF_DATA,
                trigger_time=cand.trigger_time,
            )
        return cand

    truncated_cells = [
        BookCell(
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            candidates=[c for c in (truncate(cand) for cand in cell.candidates) if c is not None],
        )
        for cell in full_cells
    ]
    cfg = _cfg(leverage=10.0)
    full = run_leverage_book(full_cells, cfg, LEGACY_BOOK_PARAMS)
    part = run_leverage_book(truncated_cells, cfg, LEGACY_BOOK_PARAMS)

    # 절단 시각 자체에 강제 청산된 인공 거래(END_OF_DATA)는 비교 대상이 아니다 —
    # "그 전에 끝난" 거래만 비교한다(엄격 미만).
    full_done = [t for t in full.trades if t.exit_time < cut]
    part_done = [t for t in part.trades if t.exit_time < cut]
    assert full_done == part_done
    assert len(full_done) >= 2  # 빈 비교로 통과하는 것을 막는다.


# --------------------------------------------------------------------------- #
# cap-only 레버리지 (WAN-180 팔 B): 상한만 N배, 거래 크기는 1배 그대로
# --------------------------------------------------------------------------- #


def test_cap_only_scales_cap_not_trade_size() -> None:
    """cap-only는 스킵을 실제로 줄이고 동시 열림을 늘리되, 거래 크기는 1배 그대로다.

    손절 1%·리스크 1%·leverage 1이면 자연 명목 = 자본이라 한 거래가 1배 상한을 다
    쓴다. combined는 배수 N이 크기·상한을 함께 키워 겹침 자리가 늘지 않지만(상대 여유
    불변 — WAN-169 성질), cap-only N=3은 상한만 3배라 같은 크기 포지션이 세 자리
    생긴다 — 라벨이 아니라 동작(수량·스킵·동시 열림)으로 고정한다(완료기준).
    """
    a = _cand(1_000, 5_000)
    b = _cand(2_000, 6_000)
    cells = [_cell("BTC/USDT:USDT", "1h", [a]), _cell("ETH/USDT:USDT", "1h", [b])]

    base = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=1.0, leverage_mode="combined")
    )
    assert base.stats.placed == 1
    assert base.stats.skipped_notional == 1

    combined = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=3.0, leverage_mode="combined")
    )
    assert combined.stats.placed == 1  # 상대 여유 불변 — 겹침 자리가 늘지 않는다.
    assert combined.stats.skipped_notional == 1

    cap_only = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=3.0, leverage_mode="cap_only")
    )
    assert cap_only.stats.placed == 2  # 스킵이 실제로 줄었다 —
    assert cap_only.stats.skipped_notional == 0
    assert cap_only.stats.peak_concurrency == 2  # — 동시 열림이 늘었다 —
    for trade in cap_only.trades:  # — 그리고 거래 크기는 1배 그대로다.
        assert trade.quantity == pytest.approx(base.trades[0].quantity)


def test_cap_only_per_trade_ceiling_stays_base() -> None:
    """cap-only에서 거래당 명목 천장도 1배로 남는다 — 상한을 키운 만큼 개별 거래가
    커지면 그건 cap-only가 아니라 결합의 반쪽이다(모듈 독스트링)."""
    close_stop = _cand(1_000, 2_000, stop_price=99.9)  # 자연 명목 = 자본×10.
    cells = [_cell("BTC/USDT:USDT", "1h", [close_stop])]

    cap_only = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    )
    combined = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=5.0, leverage_mode="combined")
    )

    cap_qty = cap_only.trades[0].quantity
    # 1배 천장(자본×1 = 10,000 명목) ÷ 진입가 100 = 수량 100 — 북 상한(5배)이 아니다.
    assert cap_qty * cap_only.trades[0].entry_price == pytest.approx(10_000.0)
    assert cap_only.stats.clamped_entries == 1
    # combined의 천장은 5배라 같은 후보가 5배 명목까지 커진다 — 두 모드가 실제로 다르다.
    assert combined.trades[0].quantity == pytest.approx(cap_qty * 5.0)


def test_cap_only_multiple_one_equals_combined() -> None:
    """배수 1에서는 두 모드가 같은 거래를 낸다 — cap-only는 1배의 다른 이름이 아니라
    배수를 싣는 자리만 다른 같은 북이다."""
    cells = [
        _cell("BTC/USDT:USDT", "1h", [_cand(1_000, 5_000), _cand(6_000, 8_000)]),
        _cell("ETH/USDT:USDT", "1h", [_cand(2_000, 6_000)]),
    ]
    cfg = _cfg(leverage=10.0)  # 상한이 판정을 가리지 않게.
    combined = run_leverage_book(
        cells, cfg, LeverageBookParams(leverage_multiple=1.0, leverage_mode="combined")
    )
    cap_only = run_leverage_book(
        cells, cfg, LeverageBookParams(leverage_multiple=1.0, leverage_mode="cap_only")
    )
    assert cap_only.trades == combined.trades
    assert cap_only.stats.placed == combined.stats.placed


def test_scale_sizing_params_cap_only_scales_only_leverage() -> None:
    sizing = PositionSizingParams(
        risk_per_trade=0.01, leverage=2.0, notional_fraction=0.5, min_stop_distance_fraction=0.0
    )
    scaled = scale_sizing_params(sizing, 5.0, mode="cap_only")
    assert scaled.leverage == pytest.approx(10.0)
    assert scaled.risk_per_trade == pytest.approx(0.01)  # 거래 크기 노브는 불변 —
    assert scaled.notional_fraction == pytest.approx(0.5)  # — 둘 다.


# --------------------------------------------------------------------------- #
# 스킵·배치 기록 (WAN-180 밀림 기회비용의 원자료)
# --------------------------------------------------------------------------- #


def test_skip_and_placed_records_match_counters() -> None:
    """기록 리스트는 카운터의 원자료다 — 사유별 합이 카운터와 항상 같다."""
    a = _cand(1_000, 5_000)
    a_overlap = _cand(2_000, 4_000)  # 같은 칸 → cell_busy.
    b = _cand(2_500, 6_000)  # 다른 칸이되 상한 소진 → notional.
    outcome = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", [a, a_overlap]), _cell("ETH/USDT:USDT", "1h", [b])],
        _cfg(),
        LEGACY_BOOK_PARAMS,
    )
    stats = outcome.stats
    reasons = [r.reason for r in stats.skip_records]
    assert reasons.count("cell_busy") == stats.skipped_cell_busy == 1
    assert reasons.count("notional") == stats.skipped_notional == 1
    assert reasons.count("sizing") == stats.skipped_sizing == 0
    # 스킵 순간의 공유 자본이 실렸다(아직 실현 손익이 없으니 초기자본 그대로).
    assert all(r.equity == pytest.approx(10_000.0) for r in stats.skip_records)
    assert len(stats.placed_records) == stats.placed == 1
    placed = stats.placed_records[0]
    assert placed.realized_pnl == pytest.approx(outcome.trades[0].realized_pnl)
    assert placed.risk_amount > 0.0


# --------------------------------------------------------------------------- #
# 펀딩 구간 자르기 (성능 전용): 전체 리스트 경로와 비트 단위로 같아야 한다
# --------------------------------------------------------------------------- #


def test_funding_window_slicing_bit_identical_to_full_list() -> None:
    """북이 자른 펀딩 구간의 손익이 전체 리스트를 넘긴 `_to_trade`와 비트로 같다.

    자르기는 같은 부분집합을 같은 순서로 누적하게 만드는 성능 장치일 뿐이다 — 구간 밖
    정산과 예측값(`is_predicted`)이 걸러지는 기존 필터 동작이 그대로임을 고정한다.
    """
    cand = _cand(10_000, 30_000)
    rates = [
        FundingRate(symbol="BTC/USDT:USDT", funding_time=5_000, rate=0.01),  # 진입 전 — 제외.
        FundingRate(symbol="BTC/USDT:USDT", funding_time=12_000, rate=0.0001),
        FundingRate(symbol="BTC/USDT:USDT", funding_time=20_000, rate=-0.0002),
        FundingRate(symbol="BTC/USDT:USDT", funding_time=25_000, rate=0.0003, is_predicted=True),
        FundingRate(symbol="BTC/USDT:USDT", funding_time=30_000, rate=0.05),  # 청산 시각 — 제외.
    ]
    cfg = _cfg().model_copy(update={"funding_enabled": True})
    outcome = run_leverage_book(
        [BookCell(symbol="BTC/USDT:USDT", timeframe="1h", candidates=[cand], funding_rates=rates)],
        cfg,
        LEGACY_BOOK_PARAMS,
    )
    manual = _to_trade(cand, cfg.initial_capital, cfg, rates, 0.0)
    assert manual is not None
    assert outcome.trades == [manual]
    assert outcome.trades[0].funding_cost != 0.0  # 구간 안 정산이 실제로 반영됐다.


# --------------------------------------------------------------------------- #
# 반익절 래더의 북 회계 (WAN-323) — 부분 청산이 명목·리스크를 실제로 덜어내는가
# --------------------------------------------------------------------------- #


def _ladder_cand(
    entry_time: int, exit_time: int, *, partial_time: int, partial_price: float = 101.0
) -> _Candidate:
    """진입 100 · 손절 99(1R=1) · 최종 청산 101.5인 후보 + 중간 절반 청산."""
    from dataclasses import replace

    return replace(
        _cand(entry_time, exit_time),
        partial_exits=(
            PartialExit(
                time=partial_time,
                price=partial_price,
                fraction=0.5,
                reason=SignalExitReason.TAKE_PROFIT,
            ),
        ),
    )


def test_book_without_partials_is_bit_identical() -> None:
    """래더를 안 켜면 축소 이벤트가 없어 북이 예전과 글자 그대로 같이 돈다."""
    cells = [_cell("BTC", "1h", [_cand(0, 100), _cand(200, 300)])]
    out = run_leverage_book(cells, cfg=_cfg(), book=LEGACY_BOOK_PARAMS)
    paired = sequence_with_candidates(list(cells[0].candidates), _cfg(), None)
    assert [t.realized_pnl for t in out.trades] == [t.realized_pnl for _, t in paired]


def test_partial_exit_releases_notional_for_another_cell() -> None:
    """부분 청산이 공유 명목 상한을 실제로 풀어 준다 — 안 풀면 래더가 손해를 본다.

    자본을 좁혀(명목 상한 = 자본 × 1배) 한 칸이 상한을 거의 다 쓰게 만든 뒤, 절반을
    덜어낸 **다음에** 다른 칸이 들어올 수 있는지로 본다. 축소 이벤트가 없으면 그 칸은
    `skipped_notional`로 밀린다.
    """
    cfg = _cfg(risk_per_trade=1.0, leverage=1.0)  # 1R=1%라 명목이 자본을 꽉 채운다
    late = _cand(150, 400, entry_price=100.0)
    with_partial = [
        _cell("BTC", "1h", [_ladder_cand(0, 300, partial_time=100)]),
        _cell("ETH", "1h", [late]),
    ]
    without = [
        _cell("BTC", "1h", [_cand(0, 300)]),
        _cell("ETH", "1h", [late]),
    ]
    on = run_leverage_book(with_partial, cfg=cfg, book=LEGACY_BOOK_PARAMS)
    off = run_leverage_book(without, cfg=cfg, book=LEGACY_BOOK_PARAMS)
    assert off.stats.skipped_notional == 1  # 절반을 안 덜면 늦은 칸이 밀린다
    assert on.stats.skipped_notional == 0  # 덜어내면 자리가 난다
    assert on.stats.placed == 2


def test_partial_exit_lowers_max_concurrent_risk() -> None:
    """부분 청산 뒤에는 동시 리스크가 그만큼 줄어 있어야 한다(래더의 존재 이유).

    ⚠️ 리스크는 **배치 시점에만** 계측되므로(`_observe`) 부분 청산 **뒤에 들어오는 칸**이
    있어야 차이가 드러난다. 명목 상한이 그 칸을 밀어내면 두 팔이 비교 불가가 되므로
    배수 2로 여유를 준다(상한이 구속하는 경우는 위 `..._releases_notional_...`이 잰다).
    """
    cfg = _cfg(leverage=2.0)
    ladder = [
        _cell("BTC", "1h", [_ladder_cand(0, 300, partial_time=100)]),
        _cell("ETH", "1h", [_cand(150, 400)]),
    ]
    plain = [
        _cell("BTC", "1h", [_cand(0, 300)]),
        _cell("ETH", "1h", [_cand(150, 400)]),
    ]
    on = run_leverage_book(ladder, cfg=cfg, book=LEGACY_BOOK_PARAMS)
    off = run_leverage_book(plain, cfg=cfg, book=LEGACY_BOOK_PARAMS)
    assert on.stats.max_concurrent_risk_ratio < off.stats.max_concurrent_risk_ratio


def test_partial_exit_total_cash_matches_to_trade_exactly() -> None:
    """시점은 나눠도 **총액은 정의상 정확하다** — 최종 청산이 나머지를 낸다."""
    cfg = _cfg()
    cand = _ladder_cand(0, 300, partial_time=100)
    out = run_leverage_book([_cell("BTC", "1h", [cand])], cfg=cfg, book=LEGACY_BOOK_PARAMS)
    trade = _to_trade(cand, cfg.initial_capital, cfg, None)
    assert trade is not None
    assert out.trades[0].realized_pnl == pytest.approx(trade.realized_pnl, rel=1e-12)


def test_partial_cash_is_credited_at_the_partial_time_not_at_close() -> None:
    """덜어낸 몫의 현금이 **그 시점에** 들어와 다른 칸의 사이징 자본이 된다.

    부분 청산이 이익이면 그 뒤에 들어오는 칸의 수량이 커진다 — 최종 청산까지 미루면
    그 효과가 사라진다(WAN-169 `realized_pnl_flows_into_other_cells_sizing`의 래더 판).
    """
    cfg = _cfg(leverage=2.0)
    late = _cand(150, 400)
    early_credit = run_leverage_book(
        [
            _cell("BTC", "1h", [_ladder_cand(0, 300, partial_time=100, partial_price=140.0)]),
            _cell("ETH", "1h", [late]),
        ],
        cfg=cfg,
        book=LEGACY_BOOK_PARAMS,
    )
    no_credit = run_leverage_book(
        [
            _cell("BTC", "1h", [_cand(0, 300)]),
            _cell("ETH", "1h", [late]),
        ],
        cfg=cfg,
        book=LEGACY_BOOK_PARAMS,
    )
    late_on = [t for t in early_credit.trades if t.entry_time == 150][0]
    late_off = [t for t in no_credit.trades if t.entry_time == 150][0]
    assert late_on.quantity > late_off.quantity
