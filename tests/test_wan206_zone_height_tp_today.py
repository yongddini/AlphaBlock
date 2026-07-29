"""WAN-206 격자의 계약 테스트 — 존높이 재측정이 라벨이 아니라 동작이다.

CI 안전(실데이터 불필요) 부분:

* **판정(a/b/c)이 표의 숫자에서 계산된다** — 따뜻(주)·차가움(스트레스) 두 OOS로 가르고,
  표본 미달(4h·TRX류)이면 판정을 붙이지 않는다.
* **렌즈 민감도 문장이 부호 뒤집힘을 실제로 잡는다** — `pen_5bp`에서 존높이 우위가
  사라지는지.
* **집계·편중·스윕 헬퍼** — leave-one-out·pooled·best_r·trades_per_symbol.
* **`_gross_r`이 WAN-143과 같은 산식** — 재구현이 조용히 갈라지지 않았다.

실데이터가 있을 때만 도는 부분(CI skip):

* **`entry_r` 팔(override=None) ≡ `harness.run_once`** — 따뜻·차가움 양쪽에서 비트 일치.
  존높이 팔의 델타가 배선 인공물이 아님을 보장하는 검산이다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import (
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    load_market_data,
)
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import TP_ENTRY, TP_ZONE
from backtest.wan143_zone_height_tp import _gross_r as wan143_gross_r
from backtest.wan206_zone_height_tp_today import (
    LENS_BASELINE,
    LENS_PEN,
    TpRow,
    arm_label,
    best_r,
    leave_one_out,
    lens_note,
    pooled,
    rows_to_frame,
    run_checksum,
    trades_per_symbol,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate

_SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT")


# --------------------------------------------------------------------------- #
# 행 팩토리
# --------------------------------------------------------------------------- #


def _row(
    *,
    symbol: str,
    lens: str,
    tp_rule: str,
    total_return: float,
    segment: str = SEGMENT_OOS_WARM,
    r_multiple: float = 1.5,
    num_trades: int = 50,
) -> TpRow:
    return TpRow(
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        lens=lens,
        tp_rule=tp_rule,
        r_multiple=r_multiple,
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
    )


def _oos_grid(
    warm: dict[str, float], cold: dict[str, float], *, lens: str = LENS_BASELINE
) -> list[TpRow]:
    """두 OOS 구간의 (tp_rule → 심볼평균 수익) 사양으로 격자 행을 만든다."""
    rows: list[TpRow] = []
    for segment, spec in ((SEGMENT_OOS_WARM, warm), (SEGMENT_OOS, cold)):
        for tp_rule, value in spec.items():
            for symbol in _SYMBOLS:
                rows.append(
                    _row(
                        symbol=symbol,
                        lens=lens,
                        tp_rule=tp_rule,
                        total_return=value,
                        segment=segment,
                    )
                )
    return rows


# --------------------------------------------------------------------------- #
# _gross_r — WAN-143과 같은 산식
# --------------------------------------------------------------------------- #


def _cand(entry: float, stop: float, exit_price: float) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=1,
        entry_price=entry,
        exit_time=2,
        exit_price=exit_price,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=stop,
    )


def test_gross_r_matches_wan143() -> None:
    """재구현한 `_gross_r`이 WAN-143 원본과 같은 값을 낸다(조용히 갈라지지 않았다)."""
    from backtest.wan206_zone_height_tp_today import _gross_r

    cand = _cand(entry=100.0, stop=98.0, exit_price=103.0)
    assert _gross_r(cand) == wan143_gross_r(cand) == pytest.approx(1.5)


def test_gross_r_returns_none_for_degenerate_risk() -> None:
    from backtest.wan206_zone_height_tp_today import _gross_r

    assert _gross_r(_cand(entry=100.0, stop=100.0, exit_price=101.0)) is None


# --------------------------------------------------------------------------- #
# 판정 — 따뜻(주) + 차가움(스트레스)
# --------------------------------------------------------------------------- #


def test_verdict_a_when_zone_height_wins_both_oos() -> None:
    frame = rows_to_frame(
        _oos_grid(
            warm={TP_ENTRY: 0.03, TP_ZONE: 0.05},
            cold={TP_ENTRY: 0.01, TP_ZONE: 0.02},
        )
    )
    assert verdict(frame, "1h").startswith("(a)")


def test_verdict_b_when_current_wins_both_oos() -> None:
    frame = rows_to_frame(
        _oos_grid(
            warm={TP_ENTRY: 0.05, TP_ZONE: 0.03},
            cold={TP_ENTRY: 0.04, TP_ZONE: 0.01},
        )
    )
    assert verdict(frame, "1h").startswith("(b)")


def test_verdict_c_when_warm_and_cold_disagree() -> None:
    """WAN-166의 두 OOS가 반대를 가리키면 (c) — 강건성 신호가 갈린다."""
    frame = rows_to_frame(
        _oos_grid(
            warm={TP_ENTRY: 0.03, TP_ZONE: 0.06},  # 따뜻: 존높이 승
            cold={TP_ENTRY: 0.04, TP_ZONE: 0.01},  # 차가움: 현행 승
        )
    )
    assert verdict(frame, "1h").startswith("(c)")


def test_verdict_refuses_to_judge_a_thin_sample() -> None:
    """표본이 WAN-84 기준(심볼당 20거래) 미달이면 (a)/(b)/(c)를 붙이지 않는다(4h 자리)."""
    rows = [
        _row(
            symbol=symbol,
            lens=LENS_BASELINE,
            tp_rule=tp_rule,
            total_return=value,
            segment=segment,
            num_trades=18,  # 심볼당 18거래 — 기준 미달
        )
        for segment, spec in (
            (SEGMENT_OOS_WARM, {TP_ENTRY: 0.01, TP_ZONE: 0.06}),
            (SEGMENT_OOS, {TP_ENTRY: 0.01, TP_ZONE: 0.05}),
        )
        for tp_rule, value in spec.items()
        for symbol in _SYMBOLS
    ]
    text = verdict(rows_to_frame(rows), "1h")
    assert "판정 불가(대조군)" in text
    assert "18.0거래" in text
    assert not text.startswith("(a)")
    assert "+6.00%" in text  # 숫자 자체는 방향을 위해 그대로 나온다


def test_trades_per_symbol_uses_entry_arm_on_warm_oos() -> None:
    rows = [
        _row(symbol=symbol, lens=LENS_BASELINE, tp_rule=TP_ENTRY, total_return=0.0, num_trades=40)
        for symbol in _SYMBOLS
    ]
    assert trades_per_symbol(rows_to_frame(rows), "1h") == 40.0


# --------------------------------------------------------------------------- #
# 렌즈 민감도
# --------------------------------------------------------------------------- #


def test_lens_note_flags_a_sign_flip_under_pen5bp() -> None:
    """baseline에서 존높이가 이기는데 pen_5bp에서 지면 「부호가 뒤집힌다」."""
    rows = _oos_grid(
        warm={TP_ENTRY: 0.02, TP_ZONE: 0.05}, cold={TP_ENTRY: 0.0, TP_ZONE: 0.0}
    ) + _oos_grid(
        warm={TP_ENTRY: 0.02, TP_ZONE: 0.01},  # pen_5bp: 존높이가 진다
        cold={TP_ENTRY: 0.0, TP_ZONE: 0.0},
        lens=LENS_PEN,
    )
    text = lens_note(rows_to_frame(rows), "1h")
    assert "부호가 뒤집힌다" in text


def test_lens_note_reports_kept_sign() -> None:
    rows = _oos_grid(
        warm={TP_ENTRY: 0.02, TP_ZONE: 0.05}, cold={TP_ENTRY: 0.0, TP_ZONE: 0.0}
    ) + _oos_grid(
        warm={TP_ENTRY: 0.02, TP_ZONE: 0.04},  # pen_5bp에서도 존높이 승(부호 유지)
        cold={TP_ENTRY: 0.0, TP_ZONE: 0.0},
        lens=LENS_PEN,
    )
    assert "부호는 유지된다" in lens_note(rows_to_frame(rows), "1h")


# --------------------------------------------------------------------------- #
# 집계 · 편중 · 스윕
# --------------------------------------------------------------------------- #


def test_pooled_counts_positive_symbols_and_stop_rate() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", lens=LENS_BASELINE, tp_rule=TP_ZONE, total_return=0.10),
        _row(symbol="ETH/USDT:USDT", lens=LENS_BASELINE, tp_rule=TP_ZONE, total_return=-0.02),
    ]
    cell = pooled(rows_to_frame(rows), "1h", SEGMENT_OOS_WARM, LENS_BASELINE, TP_ZONE)
    assert cell["n_symbols"] == 2.0
    assert cell["n_positive"] == 1.0
    assert cell["stop_rate"] == 30 / 50  # SL / (TP+SL+EOD)


def test_leave_one_out_names_every_symbol() -> None:
    """편중 확인(ETH·SOL)은 이슈의 필수 축이다 — 심볼 하나가 다 만들었는지 보이게 한다."""
    rows = [
        _row(symbol="BTC/USDT:USDT", lens=LENS_BASELINE, tp_rule=TP_ZONE, total_return=0.40),
        _row(symbol="ETH/USDT:USDT", lens=LENS_BASELINE, tp_rule=TP_ZONE, total_return=-0.02),
    ]
    text = leave_one_out(rows_to_frame(rows), "1h", LENS_BASELINE, TP_ZONE)
    assert "−BTC -2.00%" in text and "−ETH +40.00%" in text


def test_best_r_picks_the_top_multiple() -> None:
    rows = [
        _row(
            symbol="BTC/USDT:USDT",
            lens=LENS_BASELINE,
            tp_rule=TP_ZONE,
            total_return=value,
            segment=SEGMENT_IS,
            r_multiple=r,
        )
        for r, value in ((1.0, 0.02), (1.5, 0.05), (2.0, 0.03))
    ]
    text = best_r(rows_to_frame(rows), "1h", SEGMENT_IS, LENS_BASELINE, TP_ZONE)
    assert text.startswith("최적 1.5R (+5.00%)")


def test_arm_label_is_the_bare_rule() -> None:
    assert arm_label(TP_ZONE) == "zone_height"
    assert "|" not in arm_label(TP_ZONE)


def test_sweep_arms_full_grid_at_main_multiple() -> None:
    """메인 배수(1.5)에서는 2렌즈 × 2팔 전체를 돈다 — 렌즈 병기·현행 대조가 여기서 성립."""
    from backtest.wan206_zone_height_tp_today import LENSES, sweep_arms

    arms = sweep_arms(1.5, LENSES)
    assert set(arms) == {
        (LENS_BASELINE, TP_ENTRY),
        (LENS_BASELINE, TP_ZONE),
        (LENS_PEN, TP_ENTRY),
        (LENS_PEN, TP_ZONE),
    }


def test_sweep_arms_only_baseline_zone_height_off_the_main_multiple() -> None:
    """스윕 배수(1.0/2.0/3.0)는 요약이 실제로 읽는 baseline×zone_height 한 팔만 돈다."""
    from backtest.wan206_zone_height_tp_today import LENSES, sweep_arms

    for r in (1.0, 2.0, 3.0):
        assert sweep_arms(r, LENSES) == [(LENS_BASELINE, TP_ZONE)]


# --------------------------------------------------------------------------- #
# 검산 — entry_r 팔 ≡ run_once (실데이터가 있을 때만)
# --------------------------------------------------------------------------- #


def test_entry_arm_reproduces_run_once_bit_for_bit() -> None:
    """entry_r 팔(override=None)이 프로덕션 경로와 비트 일치 — 따뜻·차가움 양쪽.

    실데이터가 없으면 skip한다(CI 기본). 비용을 감당하려 짧은 창(0.5년)·1셀로 좁힌다.
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
    assert max_diff < 1e-9, f"entry_r 팔이 run_once와 어긋났다: {max_diff:.2e} ({verdict_txt})"


def test_verdict_survives_frame_roundtrip() -> None:
    """dict 왕복(= `--from-csv` 로드) 후에도 판정이 같다 — 요약 재생성이 신뢰된다."""
    frame = rows_to_frame(
        _oos_grid(
            warm={TP_ENTRY: 0.03, TP_ZONE: 0.05},
            cold={TP_ENTRY: 0.01, TP_ZONE: 0.02},
        )
    )
    before = verdict(frame, "1h")
    roundtripped = pd.DataFrame(frame.to_dict(orient="records"))
    assert verdict(roundtripped, "1h") == before
