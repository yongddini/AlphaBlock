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
    BookCell,
    LeverageBookParams,
    apply_book_leverage,
    run_leverage_book,
    scale_sizing_params,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate, _to_trade, sequence_with_candidates
from data.models import FundingRate
from execution.sizing import PositionSizingParams


def _cand(
    entry_time: int,
    exit_time: int,
    *,
    entry_price: float = 100.0,
    exit_price: float = 101.5,
    stop_price: float = 99.0,
    reason: ExitReason = ExitReason.TAKE_PROFIT,
    trigger_time: int | None = None,
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
    outcome = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", candidates)], cfg, LeverageBookParams()
    )
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
        LeverageBookParams(),
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
        LeverageBookParams(),
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
        run_leverage_book(cells, cfg, LeverageBookParams())


# --------------------------------------------------------------------------- #
# 공유 자본: 실현 손익이 다음 진입의 사이징 자본이 된다
# --------------------------------------------------------------------------- #


def test_realized_pnl_flows_into_other_cells_sizing() -> None:
    """칸 A의 실현 손익이 칸 B의 사이징 자본에 반영된다 — 「한 지갑」의 동작 증명."""
    cfg = _cfg()
    win = _cand(1_000, 2_000)  # +1.5R 익절 → 현금 증가.
    later = _cand(3_000, 4_000)
    lone = run_leverage_book([_cell("ETH/USDT:USDT", "1h", [later])], cfg, LeverageBookParams())
    shared = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", [win]), _cell("ETH/USDT:USDT", "1h", [later])],
        cfg,
        LeverageBookParams(),
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

    one_x = run_leverage_book(cells, _cfg(), LeverageBookParams(leverage_multiple=1.0))
    assert one_x.stats.placed == 1
    assert one_x.stats.skipped_notional == 1

    three_x = run_leverage_book(cells, _cfg(), LeverageBookParams(leverage_multiple=3.0))
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
        LeverageBookParams(),
    )
    assert outcome.stats.placed == 2
    assert outcome.stats.clamped_entries == 1


# --------------------------------------------------------------------------- #
# 레버리지 = 매 거래 사이징 N배 (사용자 확정 모델)
# --------------------------------------------------------------------------- #


def test_multiple_scales_every_trade_size() -> None:
    """배수 N은 상한만 여는 게 아니라 **매 거래의 수량을 N배** 키운다."""
    cells = [_cell("BTC/USDT:USDT", "1h", [_cand(1_000, 2_000)])]
    base = run_leverage_book(cells, _cfg(), LeverageBookParams(leverage_multiple=1.0))
    tripled = run_leverage_book(cells, _cfg(), LeverageBookParams(leverage_multiple=3.0))
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
    base = run_leverage_book(cells, cfg, LeverageBookParams(leverage_multiple=1.0))
    doubled = run_leverage_book(cells, cfg, LeverageBookParams(leverage_multiple=2.0))
    assert doubled.trades[0].quantity == pytest.approx(base.trades[0].quantity * 2.0)


def test_apply_book_leverage_rejects_missing_risk_sizing() -> None:
    """전액 진입 모드(risk_sizing=None)는 배수를 정의할 수 없어 거부한다."""
    cfg = BacktestConfig(initial_capital=10_000.0, risk_sizing=None)
    with pytest.raises(ValueError, match="리스크 사이징"):
        apply_book_leverage(cfg, LeverageBookParams(leverage_multiple=2.0))


# --------------------------------------------------------------------------- #
# 최악 가정 청산 검사 (WAN-103 결정 4를 공유 자본 위에서)
# --------------------------------------------------------------------------- #


def test_liquidation_event_recorded_at_high_multiple() -> None:
    """전 포지션 동시 손절 가정이 유지증거금을 뚫으면 청산 이벤트로 계측된다."""
    cells = [_cell("BTC/USDT:USDT", "1h", [_cand(1_000, 2_000)])]
    calm = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=1.0, maintenance_margin_rate=0.25)
    )
    assert calm.stats.liquidations == []
    # 배수 5: 명목 ≈ 자본×5 → 유지증거금 1.25×자본 > 최악 자본(0.95×자본) → 트리거.
    risky = run_leverage_book(
        cells, _cfg(), LeverageBookParams(leverage_multiple=5.0, maintenance_margin_rate=0.25)
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
    outcome = run_leverage_book(cells, _cfg(), LeverageBookParams(), eval_from_ms=boundary)

    assert outcome.stats.placed == 1  # straddle은 배치조차 되지 않았다.
    assert outcome.stats.skipped_cell_busy == 0  # 칸을 잠그지도 않았다.
    only = outcome.trades[0]
    assert only.entry_time == 6_000
    # 신선한 초기자본 그대로 사이징됐다(워밍업 손익·점유가 스며들지 않았다).
    lone = run_leverage_book([_cell("BTC/USDT:USDT", "1h", [fresh])], _cfg(), LeverageBookParams())
    assert only.quantity == pytest.approx(lone.trades[0].quantity)


def test_eval_filter_uses_trigger_time_not_entry_time() -> None:
    """평가 경계 판정은 진입 시각이 아니라 **탭 시각**이다(WAN-166 규약)."""
    boundary = 5_000
    # 탭은 경계 전(4_000), 체결은 경계 후(6_000) — 워밍업 셋업이므로 배치되지 않아야 한다.
    warm_tap = _cand(6_000, 8_000, trigger_time=4_000)
    outcome = run_leverage_book(
        [_cell("BTC/USDT:USDT", "1h", [warm_tap])],
        _cfg(),
        LeverageBookParams(),
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
    full = run_leverage_book(full_cells, cfg, LeverageBookParams())
    part = run_leverage_book(truncated_cells, cfg, LeverageBookParams())

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

    base = run_leverage_book(cells, _cfg(), LeverageBookParams(leverage_multiple=1.0))
    assert base.stats.placed == 1
    assert base.stats.skipped_notional == 1

    combined = run_leverage_book(cells, _cfg(), LeverageBookParams(leverage_multiple=3.0))
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
    combined = run_leverage_book(cells, _cfg(), LeverageBookParams(leverage_multiple=5.0))

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
    combined = run_leverage_book(cells, cfg, LeverageBookParams(leverage_multiple=1.0))
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
        LeverageBookParams(),
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
        LeverageBookParams(),
    )
    manual = _to_trade(cand, cfg.initial_capital, cfg, rates, 0.0)
    assert manual is not None
    assert outcome.trades == [manual]
    assert outcome.trades[0].funding_cost != 0.0  # 구간 안 정산이 실제로 반영됐다.
