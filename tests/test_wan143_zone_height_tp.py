"""WAN-143 §1 격자의 계약 테스트 — 존높이 익절·가드 축·판정 문장이 라벨이 아니라 동작이다.

고정하는 것:

* **존높이 익절 오버라이드의 산식** — 목표 = 진입가 ± R × (top − bottom)이고 **손절은 안
  건드린다**(가설의 전제: 볼린저가 준 좋은 가격이 위험 축소로만 쓰인다).
* **가드 축이 사이징 필드 하나만 바꾼다** — `min_stop_distance_fraction` 외의 사이징 값이
  같이 움직이면 "가드의 몫"이라는 §가드 분리 문장이 거짓이 된다.
* **판정·가드 분리 문장이 표의 숫자에서 실제로 계산된다** — (a)/(b)/(c) 분기와, 개선분을
  가드 몫과 익절 몫으로 가르는 뺄셈.
* **팔 라벨에 `|`가 없다** — 요약이 마크다운 표라 셀 안의 파이프가 열을 쪼갠다.
"""

from __future__ import annotations

import pandas as pd

from backtest.harness import SEGMENT_OOS, build_config
from backtest.wan143_zone_height_tp import (
    GUARD_OFF,
    GUARD_ON,
    TP_ENTRY,
    TP_ZONE,
    TpRow,
    apply_guard,
    arm_label,
    best_r,
    guard_isolation,
    leave_one_out,
    make_zone_height_override,
    pooled,
    rows_to_frame,
    trades_per_symbol,
    verdict,
)
from backtest.zone_limit_backtest import TakeProfitContext
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection


def _ob(top: float = 110.0, bottom: float = 100.0) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )


def _ctx(entry_price: float, stop_price: float, *, is_long: bool = True) -> TakeProfitContext:
    return TakeProfitContext(
        is_long=is_long,
        entry_price=entry_price,
        stop_price=stop_price,
        trigger_time=0,
        order_block=_ob(),
    )


# --------------------------------------------------------------------------- #
# 존높이 익절
# --------------------------------------------------------------------------- #


def test_zone_height_target_ignores_the_shrunken_1r() -> None:
    """목표는 존 높이(10.0)의 1.5배 — 진입가·손절 거리가 아무리 좁아도 그대로다.

    이것이 이 이슈의 전부다: 현행 규칙이라면 손절 거리 0.2에서 목표가 0.3인데(왕복비용이
    절반을 먹는다), 존높이 기준이면 15.0 떨어진 곳이다.
    """
    override = make_zone_height_override(ConfluenceParams(), 1.5)
    assert override(_ctx(entry_price=101.0, stop_price=100.8)) == 101.0 + 15.0


def test_zone_height_target_is_mirrored_for_shorts() -> None:
    override = make_zone_height_override(ConfluenceParams(), 1.5)
    assert override(_ctx(entry_price=109.0, stop_price=110.0, is_long=False)) == 109.0 - 15.0


def test_zone_height_r_multiple_scales_the_target() -> None:
    """R 배수 스윕 축이 실제로 목표를 옮긴다(스윕이 같은 값을 네 번 재지 않는다)."""
    targets = [
        make_zone_height_override(ConfluenceParams(), r)(_ctx(101.0, 100.8))
        for r in (1.0, 2.0, 3.0)
    ]
    assert targets == [111.0, 121.0, 131.0]


def test_zone_height_falls_back_to_the_current_rule_when_height_is_degenerate() -> None:
    """높이를 못 재면 지어내지 않고 현행 고정 R로 돌아간다(그 셋업만 대조 팔과 같은 목표)."""
    params = ConfluenceParams(take_profit_mode="fixed_r", take_profit_r=1.5)
    override = make_zone_height_override(params, 1.5)
    ctx = TakeProfitContext(
        is_long=True,
        entry_price=101.0,
        stop_price=100.0,
        trigger_time=0,
        order_block=_ob(top=100.0, bottom=100.0),  # 높이 0
    )
    assert override(ctx) == 101.0 + 1.5 * 1.0


# --------------------------------------------------------------------------- #
# 가드 축
# --------------------------------------------------------------------------- #


def test_apply_guard_changes_only_the_minimum_stop_distance() -> None:
    cfg = build_config("1h")
    assert cfg.risk_sizing is not None
    off = apply_guard(cfg, GUARD_OFF)
    assert off.risk_sizing is not None
    assert off.risk_sizing.min_stop_distance_fraction == GUARD_OFF
    # 나머지 사이징 필드는 그대로여야 "가드의 몫"이라는 문장이 참이다.
    assert off.risk_sizing.model_dump(exclude={"min_stop_distance_fraction"}) == (
        cfg.risk_sizing.model_dump(exclude={"min_stop_distance_fraction"})
    )
    assert apply_guard(cfg, GUARD_ON).risk_sizing is not None


def test_apply_guard_leaves_a_config_without_risk_sizing_alone() -> None:
    """사이징이 없는 설정에 가드를 끼워 넣지 않는다 — 없던 축이 생기면 두 축이 섞인다."""
    cfg = build_config("1h").model_copy(update={"risk_sizing": None})
    assert apply_guard(cfg, GUARD_OFF) is cfg


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _row(
    *,
    symbol: str,
    tp_rule: str,
    guard: float,
    total_return: float,
    segment: str = SEGMENT_OOS,
    r_multiple: float = 1.5,
    num_trades: int = 50,
) -> TpRow:
    return TpRow(
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        tp_rule=tp_rule,
        guard=guard,
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


def _grid(returns: dict[tuple[str, float], float]) -> pd.DataFrame:
    rows = [
        _row(symbol=symbol, tp_rule=rule, guard=guard, total_return=value)
        for (rule, guard), value in returns.items()
        for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT")
    ]
    return rows_to_frame(rows)


def test_verdict_reads_both_guard_columns() -> None:
    """가드마다 부호가 갈리면 (c) — 하나의 기본값으로 둘 다 좋게 할 수 없다는 선례(WAN-108)."""
    split = _grid(
        {
            (TP_ENTRY, GUARD_ON): 0.05,
            (TP_ZONE, GUARD_ON): 0.03,  # 가드 켬에서는 진다
            (TP_ENTRY, GUARD_OFF): 0.01,
            (TP_ZONE, GUARD_OFF): 0.08,  # 가드 끔에서는 이긴다
        }
    )
    assert verdict(split, "1h").startswith("(c) 가드에 갈린다")

    wins = _grid(
        {
            (TP_ENTRY, GUARD_ON): 0.01,
            (TP_ZONE, GUARD_ON): 0.02,
            (TP_ENTRY, GUARD_OFF): 0.01,
            (TP_ZONE, GUARD_OFF): 0.05,
        }
    )
    assert wins.pipe(verdict, "1h").startswith("(a)")

    loses = _grid(
        {
            (TP_ENTRY, GUARD_ON): 0.05,
            (TP_ZONE, GUARD_ON): 0.01,
            (TP_ENTRY, GUARD_OFF): 0.05,
            (TP_ZONE, GUARD_OFF): 0.02,
        }
    )
    assert loses.pipe(verdict, "1h").startswith("(b)")


def test_verdict_refuses_to_judge_a_thin_sample() -> None:
    """표본이 WAN-84 기준(심볼당 20거래) 미달이면 (a)/(b)/(c)를 붙이지 않는다.

    4h가 정확히 그 자리다(WAN-107 15.7거래 · WAN-130 18.3거래 → 「보류」). 게이트가 없으면
    대조군 열이 판정문을 갖게 되고 그 문장이 다음 이슈에서 근거로 재인용된다.
    """
    thin = rows_to_frame(
        [
            _row(
                symbol=symbol,
                tp_rule=rule,
                guard=guard,
                total_return=value,
                num_trades=18,  # 심볼당 18거래 — 기준 미달
            )
            for (rule, guard), value in {
                (TP_ENTRY, GUARD_ON): 0.01,
                (TP_ZONE, GUARD_ON): 0.05,
                (TP_ENTRY, GUARD_OFF): 0.01,
                (TP_ZONE, GUARD_OFF): 0.06,
            }.items()
            for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT")
        ]
    )
    text = verdict(thin, "1h")
    assert "판정 불가(대조군)" in text
    assert "18.0거래" in text
    assert not text.startswith("(a)")
    # 숫자 자체는 여전히 나온다 — 방향은 볼 수 있어야 한다.
    assert "+5.00%" in text


def test_trades_per_symbol_uses_the_baseline_arm() -> None:
    rows = [
        _row(symbol=symbol, tp_rule=TP_ENTRY, guard=GUARD_ON, total_return=0.0, num_trades=40)
        for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT")
    ]
    assert trades_per_symbol(rows_to_frame(rows), "1h") == 40.0


def test_guard_isolation_splits_the_improvement_into_two_deltas() -> None:
    """②가 없으면 ④의 개선이 익절 덕인지 가드 덕인지 모른다 — 그 뺄셈을 문장으로 고정한다."""
    frame = _grid(
        {
            (TP_ENTRY, GUARD_ON): 0.01,
            (TP_ZONE, GUARD_ON): 0.02,
            (TP_ENTRY, GUARD_OFF): 0.03,  # 가드만 풀어 +2%p
            (TP_ZONE, GUARD_OFF): 0.09,  # 익절까지 바꿔 +6%p
        }
    )
    sentence = guard_isolation(frame, "1h")
    assert "+2.00%p" in sentence and "+6.00%p" in sentence


def test_pooled_counts_positive_symbols_and_stop_rate() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", tp_rule=TP_ZONE, guard=GUARD_OFF, total_return=0.10),
        _row(symbol="ETH/USDT:USDT", tp_rule=TP_ZONE, guard=GUARD_OFF, total_return=-0.02),
    ]
    cell = pooled(rows_to_frame(rows), "1h", SEGMENT_OOS, TP_ZONE, GUARD_OFF)
    assert cell["n_symbols"] == 2.0
    assert cell["n_positive"] == 1.0
    assert cell["stop_rate"] == 30 / 50  # SL / (TP+SL+EOD)


def test_leave_one_out_names_every_symbol() -> None:
    """편중 확인은 이슈의 필수 축이다 — 심볼 하나가 결과를 다 만들었는지 보이게 한다."""
    rows = [
        _row(symbol="BTC/USDT:USDT", tp_rule=TP_ZONE, guard=GUARD_OFF, total_return=0.40),
        _row(symbol="ETH/USDT:USDT", tp_rule=TP_ZONE, guard=GUARD_OFF, total_return=-0.02),
    ]
    text = leave_one_out(rows_to_frame(rows), "1h", TP_ZONE, GUARD_OFF)
    assert "−BTC -2.00%" in text and "−ETH +40.00%" in text


def test_best_r_picks_the_top_multiple() -> None:
    rows = [
        _row(
            symbol="BTC/USDT:USDT",
            tp_rule=TP_ZONE,
            guard=GUARD_OFF,
            total_return=value,
            r_multiple=r,
        )
        for r, value in ((1.0, 0.02), (1.5, 0.05), (2.0, 0.03))
    ]
    text = best_r(rows_to_frame(rows), "1h", SEGMENT_OOS, TP_ZONE, GUARD_OFF)
    assert text.startswith("최적 1.5R (+5.00%)")


def test_arm_label_has_no_pipe() -> None:
    """마크다운 표 셀 안의 `|`는 열을 쪼갠다 — 라벨 구분자를 고정한다."""
    label = arm_label(TP_ZONE, GUARD_OFF)
    assert "|" not in label
    assert label == "zone_height + guard_off"
