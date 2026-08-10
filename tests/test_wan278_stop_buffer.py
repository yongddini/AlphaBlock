"""WAN-278 손절 버퍼 모듈의 계약 테스트 — 라벨이 아니라 동작이다.

CI 안전(실데이터 불필요) 부분:

* **오버라이드가 손절만 밀고 익절은 원래 1R에 고정한다** — WAN-260(비례 확대)과 갈리는
  핵심 동작. ATR 배수·1R 분수 두 단위 모두.
* **救出 분해가 셋업 단위로 3결말을 센다** — 버퍼 0 대비 짝짓기, 순효과 R(원래 1R 단위),
  정렬이 깨지면 `AssertionError`.
* **판정(a/b/c) + 표본 게이트** — 두 OOS로 가르고, 버퍼가 건드리는 손절이 심볼당 20건
  미만이면 판정하지 않는다.
* **집계·편중·렌즈 민감도 헬퍼**.

실데이터가 있을 때만 도는 부분(CI skip):

* **버퍼 0 팔(override=None) ≡ `harness.run_once`** — 따뜻·차가움 양쪽 비트 일치.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import SEGMENT_OOS, SEGMENT_OOS_WARM, load_market_data
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.wan278_stop_buffer import (
    LENS_BASELINE,
    LENS_PEN,
    TP_MULTIPLE,
    UNIT_ATR,
    UNIT_R,
    BufferRow,
    best_buffer,
    decompose,
    leave_one_out,
    lens_note,
    make_buffer_overrides,
    pooled,
    rows_to_frame,
    run_checksum,
    stops_per_symbol,
    verdict,
)
from backtest.zone_limit_backtest import StopLossContext, TakeProfitContext, _Candidate
from strategy.models import OrderBlock, OrderBlockDirection

# --------------------------------------------------------------------------- #
# 픽스처
# --------------------------------------------------------------------------- #


def _ob(*, bottom: float = 90.0, top: float = 100.0, bull: bool = True) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH if bull else OrderBlockDirection.BEARISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )


def _cand(
    *,
    entry_time: int = 1,
    entry_price: float = 100.0,
    exit_price: float = 90.0,
    reason: ExitReason = ExitReason.STOP_LOSS,
    stop_price: float = 90.0,
    side: PositionSide = PositionSide.LONG,
    trigger_time: int = 1,
) -> _Candidate:
    return _Candidate(
        side=side,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=entry_time + 1,
        exit_price=exit_price,
        reason=reason,
        stop_price=stop_price,
        trigger_time=trigger_time,
    )


def _row(
    *,
    symbol: str,
    lens: str = LENS_BASELINE,
    buffer: float,
    total_return: float,
    segment: str = SEGMENT_OOS_WARM,
    num_trades: int = 60,
    would_be_stopped: int = 40,
    rescued_tp: int = 0,
    deeper_sl: int = 0,
    unclosed_eod: int = 0,
    rescue_net_r: float = 0.0,
    unit: str = UNIT_ATR,
) -> BufferRow:
    return BufferRow(
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        lens=lens,
        unit=unit,
        buffer=buffer,
        eligible=100,
        filled=80,
        num_trades=num_trades,
        fill_rate=0.8,
        total_return=total_return,
        max_drawdown=0.1,
        win_rate=0.5,
        sharpe=None,
        mean_gross_r=0.1,
        n_take_profit=20,
        n_stop_loss=30,
        n_end_of_data=0,
        would_be_stopped=would_be_stopped if buffer > 0 else 0,
        rescued_tp=rescued_tp,
        deeper_sl=deeper_sl,
        unclosed_eod=unclosed_eod,
        rescue_net_r=rescue_net_r,
    )


# --------------------------------------------------------------------------- #
# 오버라이드 — 손절만 밀고 익절은 원래 1R 고정
# --------------------------------------------------------------------------- #


def test_atr_buffer_pushes_stop_but_keeps_original_1r_take_profit() -> None:
    """ATR 버퍼: 손절은 원래 경계 − 버퍼×ATR로 밀리고, 익절은 원래 1R × 1.5에 고정된다."""
    atr_map = {5: 4.0}  # 탭 봉(trigger_time=5) 직전 확정봉 ATR = 4.0
    stop_ovr, tp_ovr = make_buffer_overrides(UNIT_ATR, 0.5, atr_map)
    ob = _ob(bottom=90.0, top=100.0)
    # 진입 98, 원래 무효화 경계(default_stop) = 90.
    new_stop = stop_ovr(
        StopLossContext(
            is_long=True, entry_price=98.0, default_stop=90.0, trigger_time=5, order_block=ob
        )
    )
    assert new_stop == pytest.approx(90.0 - 0.5 * 4.0)  # 88.0 — 2 아래로 밀림
    # 익절은 버퍼로 밀린 88이 아니라 **원래 경계 90** 기준 1R(=8)의 1.5배.
    tp = tp_ovr(
        TakeProfitContext(
            is_long=True, entry_price=98.0, stop_price=88.0, trigger_time=5, order_block=ob
        )
    )
    assert tp == pytest.approx(98.0 + TP_MULTIPLE * (98.0 - 90.0))  # 110.0, 88과 무관


def test_r_fraction_buffer_scales_with_original_risk() -> None:
    """1R 분수 버퍼: 손절이 원래 1R의 분수만큼 더 깊어진다(ATR 불필요)."""
    stop_ovr, _ = make_buffer_overrides(UNIT_R, 0.25, {})
    ob = _ob(bottom=90.0, top=100.0)
    new_stop = stop_ovr(
        StopLossContext(
            is_long=True, entry_price=98.0, default_stop=90.0, trigger_time=5, order_block=ob
        )
    )
    # 원래 1R = 98 − 90 = 8, 버퍼 = 0.25×8 = 2 → 손절 88.
    assert new_stop == pytest.approx(88.0)


def test_short_buffer_pushes_stop_upward() -> None:
    """숏은 손절이 위로 밀린다(대칭)."""
    stop_ovr, tp_ovr = make_buffer_overrides(UNIT_R, 0.5, {})
    ob = _ob(bottom=100.0, top=110.0, bull=False)
    # 숏: 진입 102, 원래 무효화 경계(default_stop) = 110(존 top).
    new_stop = stop_ovr(
        StopLossContext(
            is_long=False, entry_price=102.0, default_stop=110.0, trigger_time=1, order_block=ob
        )
    )
    one_r = 110.0 - 102.0  # 8
    assert new_stop == pytest.approx(110.0 + 0.5 * one_r)  # 114 — 위로
    tp = tp_ovr(
        TakeProfitContext(
            is_long=False, entry_price=102.0, stop_price=114.0, trigger_time=1, order_block=ob
        )
    )
    assert tp == pytest.approx(102.0 - TP_MULTIPLE * one_r)  # 90


def test_atr_warmup_setup_gets_no_buffer() -> None:
    """ATR을 못 재는(직전 확정봉 없음) 셋업은 버퍼 0으로 폴백한다(정직)."""
    stop_ovr, _ = make_buffer_overrides(UNIT_ATR, 0.5, {})  # 빈 맵 = 어떤 탭도 ATR 없음
    ob = _ob()
    new_stop = stop_ovr(
        StopLossContext(
            is_long=True, entry_price=98.0, default_stop=90.0, trigger_time=5, order_block=ob
        )
    )
    assert new_stop == pytest.approx(90.0)  # 밀리지 않음


# --------------------------------------------------------------------------- #
# 救出 분해
# --------------------------------------------------------------------------- #


def test_decompose_classifies_three_rescue_outcomes() -> None:
    """버퍼 0에서 손절났을 3셋업이 버퍼 팔에서 익절/더깊은손절/미청산으로 갈린다."""
    # 버퍼 0(base): 셋 다 손절(진입 100 · 원래 손절 90 · 청산 90 = −1R).
    base = [
        _cand(entry_time=1, exit_price=90.0, reason=ExitReason.STOP_LOSS, stop_price=90.0),
        _cand(entry_time=2, exit_price=90.0, reason=ExitReason.STOP_LOSS, stop_price=90.0),
        _cand(entry_time=3, exit_price=90.0, reason=ExitReason.STOP_LOSS, stop_price=90.0),
    ]
    # 버퍼 팔: 손절 88(버퍼)로 밀림. 1) 익절 115(+1.5R) 2) 더 깊은 손절 88(−1.25R) 3) 미청산.
    buffered = [
        _cand(entry_time=1, exit_price=115.0, reason=ExitReason.TAKE_PROFIT, stop_price=88.0),
        _cand(entry_time=2, exit_price=88.0, reason=ExitReason.STOP_LOSS, stop_price=88.0),
        _cand(entry_time=3, exit_price=105.0, reason=ExitReason.END_OF_DATA, stop_price=88.0),
    ]
    d = decompose(base, buffered)
    assert d.would_be_stopped == 3
    assert d.rescued_tp == 1
    assert d.deeper_sl == 1
    assert d.unclosed_eod == 1
    # 순효과(원래 1R=10 기준): 1) +1.5 − (−1.0)=+2.5 · 2) −1.2 − (−1.0)=−0.2 · 3) +0.5 −(−1.0)=+1.5
    assert d.net_r == pytest.approx(2.5 - 0.2 + 1.5)


def test_decompose_ignores_setups_not_stopped_at_baseline() -> None:
    """버퍼 0에서 익절/미청산이던 셋업은 「버퍼가 건드릴 손절」이 아니라 안 센다."""
    base = [
        _cand(entry_time=1, exit_price=115.0, reason=ExitReason.TAKE_PROFIT, stop_price=90.0),
        _cand(entry_time=2, exit_price=90.0, reason=ExitReason.STOP_LOSS, stop_price=90.0),
    ]
    buffered = [
        _cand(entry_time=1, exit_price=115.0, reason=ExitReason.TAKE_PROFIT, stop_price=88.0),
        _cand(entry_time=2, exit_price=115.0, reason=ExitReason.TAKE_PROFIT, stop_price=88.0),
    ]
    d = decompose(base, buffered)
    assert d.would_be_stopped == 1  # 익절이던 셋업은 제외
    assert d.rescued_tp == 1


def test_decompose_raises_on_misalignment() -> None:
    """진입 셋업이 어긋나면(정렬 깨짐) 배선 버그라 멈춘다."""
    base = [_cand(entry_time=1)]
    buffered = [_cand(entry_time=99)]  # 다른 셋업
    with pytest.raises(AssertionError):
        decompose(base, buffered)

    with pytest.raises(AssertionError):
        decompose([_cand(entry_time=1)], [_cand(entry_time=1), _cand(entry_time=2)])


# --------------------------------------------------------------------------- #
# 판정 — 두 OOS + 표본 게이트
# --------------------------------------------------------------------------- #


def _grid(
    warm: dict[float, float],
    cold: dict[float, float],
    *,
    would: int = 40,
    lens: str = LENS_BASELINE,
) -> list[BufferRow]:
    rows: list[BufferRow] = []
    for segment, spec in ((SEGMENT_OOS_WARM, warm), (SEGMENT_OOS, cold)):
        for buffer, value in spec.items():
            for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
                rows.append(
                    _row(
                        symbol=symbol,
                        lens=lens,
                        buffer=buffer,
                        total_return=value,
                        segment=segment,
                        would_be_stopped=would,
                    )
                )
    return rows


def test_verdict_a_when_a_buffer_beats_baseline_in_both_oos() -> None:
    frame = rows_to_frame(_grid(warm={0.0: 0.03, 0.25: 0.06}, cold={0.0: 0.02, 0.25: 0.04}))
    assert verdict(frame, "1h").startswith("(a)")


def test_verdict_b_when_no_buffer_beats_baseline() -> None:
    frame = rows_to_frame(_grid(warm={0.0: 0.05, 0.25: 0.03}, cold={0.0: 0.04, 0.25: 0.02}))
    assert verdict(frame, "1h").startswith("(b)")


def test_verdict_c_when_oos_disagree() -> None:
    frame = rows_to_frame(_grid(warm={0.0: 0.03, 0.25: 0.06}, cold={0.0: 0.04, 0.25: 0.01}))
    assert verdict(frame, "1h").startswith("(c)")


def test_verdict_refuses_thin_stop_sample() -> None:
    """버퍼가 건드리는 손절이 심볼당 20건 미만이면 판정하지 않는다."""
    frame = rows_to_frame(
        _grid(warm={0.0: 0.01, 0.25: 0.06}, cold={0.0: 0.01, 0.25: 0.05}, would=18)
    )
    text = verdict(frame, "1h")
    assert "판정 불가(대조군)" in text
    assert not text.startswith("(a)")


def test_stops_per_symbol_uses_smallest_positive_buffer() -> None:
    frame = rows_to_frame(_grid(warm={0.0: 0.0, 0.1: 0.0, 0.25: 0.0}, cold={0.0: 0.0}, would=30))
    # 가장 작은 양수 버퍼(0.1)의 would_be_stopped 심볼합(30×2) ÷ 심볼(2) = 30.
    assert stops_per_symbol(frame, "1h", LENS_BASELINE) == 30.0


# --------------------------------------------------------------------------- #
# 집계 · 편중 · 렌즈
# --------------------------------------------------------------------------- #


def test_pooled_sums_rescue_and_counts_positive_symbols() -> None:
    rows = [
        _row(
            symbol="BTC/USDT:USDT",
            buffer=0.25,
            total_return=0.10,
            rescued_tp=5,
            would_be_stopped=20,
        ),
        _row(
            symbol="ETH/USDT:USDT",
            buffer=0.25,
            total_return=-0.02,
            rescued_tp=3,
            would_be_stopped=20,
        ),
    ]
    cell = pooled(rows_to_frame(rows), "1h", SEGMENT_OOS_WARM, LENS_BASELINE, 0.25)
    assert cell["n_positive"] == 1.0
    assert cell["would_be_stopped"] == 40.0
    assert cell["rescued_tp"] == 8.0
    assert cell["rescue_rate"] == pytest.approx(8 / 40)


def test_best_buffer_picks_largest_positive_delta() -> None:
    frame = rows_to_frame(_grid(warm={0.0: 0.02, 0.1: 0.04, 0.25: 0.03}, cold={0.0: 0.0}))
    buffer, delta = best_buffer(frame, "1h", SEGMENT_OOS_WARM, LENS_BASELINE)
    assert buffer == 0.1
    assert delta == pytest.approx(0.02)


def test_leave_one_out_names_every_symbol() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", buffer=0.25, total_return=0.40),
        _row(symbol="ETH/USDT:USDT", buffer=0.25, total_return=-0.02),
    ]
    text = leave_one_out(rows_to_frame(rows), "1h", LENS_BASELINE, 0.25)
    assert "−BTC -2.00%" in text and "−ETH +40.00%" in text


def test_lens_note_flags_sign_flip_under_pen5bp() -> None:
    rows = _grid(warm={0.0: 0.02, 0.25: 0.05}, cold={0.0: 0.0, 0.25: 0.0}) + _grid(
        warm={0.0: 0.02, 0.25: 0.01}, cold={0.0: 0.0, 0.25: 0.0}, lens=LENS_PEN
    )
    assert "부호가 뒤집힌다" in lens_note(rows_to_frame(rows), "1h")


def test_verdict_survives_frame_roundtrip() -> None:
    frame = rows_to_frame(_grid(warm={0.0: 0.03, 0.25: 0.06}, cold={0.0: 0.02, 0.25: 0.04}))
    before = verdict(frame, "1h")
    roundtripped = pd.DataFrame(frame.to_dict(orient="records"))
    assert verdict(roundtripped, "1h") == before


# --------------------------------------------------------------------------- #
# 검산 — 버퍼 0 팔 ≡ run_once (실데이터가 있을 때만)
# --------------------------------------------------------------------------- #


def test_zero_buffer_reproduces_run_once_bit_for_bit() -> None:
    """버퍼 0 팔(override=None)이 프로덕션 경로와 비트 일치 — 따뜻·차가움 양쪽.

    실데이터가 없으면 skip(CI 기본). 비용을 감당하려 짧은 창(0.5년)·1셀로 좁힌다.
    """
    start, end = "2025-07-15", "2026-01-15"
    market = load_market_data(
        "BTC/USDT:USDT",
        "1h",
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        need_1m=False,
        funding=False,
    )
    if market.empty:
        pytest.skip("BTC 1h 실데이터가 없어 run_once 검산을 건너뜁니다(CI 기본).")
    max_diff, compared, verdict_txt = run_checksum(
        symbol="BTCUSDT", timeframe="1h", start=start, end=end
    )
    assert compared >= 2, "따뜻·차가움 두 구간 이상을 대조해야 한다"
    assert max_diff < 1e-9, f"버퍼 0 팔이 run_once와 어긋났다: {max_diff:.2e} ({verdict_txt})"


_ = (SEGMENT_OOS,)  # 렌더 상수 import 유지(정적 검사 노이즈 방지)
