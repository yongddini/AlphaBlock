"""backtest.wan403_zone_position_null_today 테스트 (WAN-403).

이 파일이 지키는 것:

1. **좌표가 핀 없이 채택 기본값을 따라간다**(WAN-305) — 심볼·TF·존폭 필터·무효화 취소·익절
   배수를 이 모듈이 자기 리터럴로 다시 적지 않는다. 재-베이스라인이 오면 이 표도 따라간다.
2. **`UNSET`(핀 없음)과 명시적 `None`(끄기)이 갈린다** — 오늘은 값이 같아 보이지만 필터가
   다시 채택되면 갈라진다(WAN-159 센티넬 규약). **라벨이 아니라 동작으로** 건다.
3. **갈래 판정은 코드가 고른다**(완료기준 2) — 선이 모듈 상수로 못 박혀 있고, 표를 보고 선을
   옮기지 못하게 이 테스트가 그 값을 잠근다.
4. **대칭 확인이 실제로 판정한다**(완료기준 5) — 두 팔의 무효화 봉 탭 비율이 벌어지면
   요약이 조용히 통과하지 않고 🚨로 찍는다(돌연변이 확인).
5. **net R은 「실현 손익 ÷ 그 거래의 리스크 금액」이다** — `harness.mean_r`(승률의 대수적
   재탕)이 아니다(WAN-154 PM 정정).
6. **leave-one-out이 종목 전부를 돈다** — 옛 판의 세 종목 목록이 좌표를 안 따라가면 새 종목의
   편중이 안 보인다.
7. **자를 안 바꿨다** — 유의 기준·유효 셀 기준·부트스트랩 상수가 WAN-248의 그 값 그대로다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest import wan248_zone_position_null as wan248_module
from backtest.models import ExitReason, PositionSide, Trade, TradeFill
from backtest.wan248_zone_position_null import (
    ALPHA,
    BASELINE_LENS,
    BOOTSTRAP_ITERATIONS,
    MIN_TRADES_FOR_VERDICT,
    PEN_LENS,
    PositionNullRow,
    invalidation_tap_census,
    mean_net_r,
    resolve_params,
)
from backtest.wan248_zone_position_null import (
    NULL_CSV as WAN248_CSV,
)
from backtest.wan248_zone_position_null import rows_from_csv as wan248_rows_from_csv
from backtest.wan248_zone_position_null import verdict as wan248_verdict
from backtest.wan403_zone_position_null_today import (
    CHANCE_RATIO,
    GROWN_OOS_RATIO,
    IS_SEGMENT,
    OLD_IS_SIGNIFICANT,
    OLD_OOS_SIGNIFICANT,
    OOS_SEGMENT,
    POOL_K,
    SYMBOLS,
    SYMMETRY_TOLERANCE_PP,
    TIMEFRAMES,
    ZONE_WIDTH_PIN,
    branch_verdict,
    build_summary_markdown,
    describe_engine,
    leave_one_out_lines,
    old_comparison_line,
    summary_table,
    symmetry_line,
    symmetry_table,
)
from common.costs import Liquidity
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

# --------------------------------------------------------------------------- #
# 1·2. 좌표는 핀 없이 채택 기본값을 따라간다
# --------------------------------------------------------------------------- #


def test_coordinates_follow_adopted_defaults_without_pins() -> None:
    """리터럴을 다시 적으면 재-베이스라인 때 이 표만 옛 좌표로 남는다(WAN-305)."""
    assert SYMBOLS is harness.DEFAULT_SYMBOLS
    assert TIMEFRAMES is harness.DEFAULT_TIMEFRAMES
    assert len(SYMBOLS) == 12
    assert "2h" in TIMEFRAMES


def test_zone_width_pin_is_unset_not_none() -> None:
    """`UNSET`(핀 없음)과 `None`(끄기)은 다르다 — 라벨이 아니라 **동작**으로 건다."""
    assert ZONE_WIDTH_PIN is harness.UNSET

    adopted = resolve_params(ZONE_WIDTH_PIN)
    pinned_on = resolve_params(harness.LEGACY_ZONE_WIDTH_FILTER_ON)
    pinned_off = resolve_params(None)

    # 오늘 채택 기본값을 그대로 따라간다.
    assert adopted.max_zone_width_atr == ConfluenceParams().max_zone_width_atr
    # 옛 판(WAN-248)은 1.28을 켠 채 돌았다 — 그 팔이 도달 가능해야 옛 CSV가 재현된다.
    assert pinned_on.max_zone_width_atr == harness.LEGACY_ZONE_WIDTH_FILTER_ON
    assert pinned_off.max_zone_width_atr is None
    # 🚨 `UNSET`은 「덮어쓰지 않는다」라, 필터가 다시 채택되면 `None` 팔과 갈라진다.
    sentinel = ConfluenceParams(max_zone_width_atr=1.9)
    assert harness.pin_zone_width(sentinel, None).max_zone_width_atr is None
    assert sentinel.max_zone_width_atr == 1.9


def test_engine_fingerprint_reports_causal_cancel_and_adopted_multiple() -> None:
    """무효화 취소·익절 배수도 핀이 없다 — 지문이 그것을 드러내야 한다(WAN-365 · WAN-81/90)."""
    fingerprint = describe_engine()
    assert "invalidation_cancel=bar_close" in fingerprint
    assert f"take_profit_r={ConfluenceParams().take_profit_r}" in fingerprint
    assert f"max_zone_width_atr={ConfluenceParams().max_zone_width_atr}" in fingerprint


def test_ruler_constants_are_wan248s() -> None:
    """자를 바꾸면 「판정이 바뀐 것」과 「자가 바뀐 것」이 안 갈린다."""
    assert MIN_TRADES_FOR_VERDICT == 20
    assert ALPHA == 0.05
    assert BOOTSTRAP_ITERATIONS == 200


# --------------------------------------------------------------------------- #
# 3. 갈래 판정은 코드가 고른다
# --------------------------------------------------------------------------- #


def _row(**over: object) -> PositionNullRow:
    base: dict[str, object] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "segment": OOS_SEGMENT,
        "lens": BASELINE_LENS,
        "combine_obs": False,
        "real_total_return": 0.1,
        "real_num_trades": 40,
        "real_long": 40,
        "real_short": 0,
        "pool_size": 300,
        "real_zones": 50,
        "fake_zones": 250,
        "pool_k": 4,
        "random_mean_return": 0.02,
        "random_ci_low": -0.05,
        "random_ci_high": 0.09,
        "random_p_value": 0.30,
        "iterations": 200,
        "bucket_fallback_count": 0,
        "buy_hold": 0.5,
    }
    base.update(over)
    return PositionNullRow.model_validate(base)


def _cells(segment: str, *, n: int, significant: int) -> list[PositionNullRow]:
    """유효 셀 `n`개 중 `significant`개만 유의(p≤α & 실제>무작위평균)하게 만든다."""
    rows: list[PositionNullRow] = []
    for i in range(n):
        sig = i < significant
        rows.append(
            _row(
                symbol=f"S{i}/USDT:USDT",
                segment=segment,
                random_p_value=0.01 if sig else 0.60,
                real_total_return=0.5 if sig else -0.1,
            )
        )
    return rows


def test_branch_thresholds_are_pinned() -> None:
    """결과를 보고 선을 옮기지 못하게 값을 잠근다(착수 전에 못 박은 기준)."""
    assert CHANCE_RATIO == 2.0 * ALPHA == 0.10
    assert GROWN_OOS_RATIO == 0.24  # = 옛 뒷구간 3/25(12.0%)의 2배
    assert OLD_IS_SIGNIFICANT == (13, 27)
    assert OLD_OOS_SIGNIFICANT == (3, 25)


def test_branch_a_when_oos_significance_grew() -> None:
    rows = _cells(IS_SEGMENT, n=20, significant=10) + _cells(OOS_SEGMENT, n=20, significant=10)
    text = branch_verdict(rows)
    assert "(가) 씨앗이 커졌다" in text
    assert "WAN-402" in text


def test_branch_c_when_in_sample_seed_is_gone() -> None:
    """앞구간이 우연 수준이면 (다) — 뒷구간이 낮은 것만으로는 (나)와 안 갈린다."""
    rows = _cells(IS_SEGMENT, n=20, significant=1) + _cells(OOS_SEGMENT, n=20, significant=0)
    text = branch_verdict(rows)
    assert "(다) 씨앗이 사라졌다" in text
    assert "접는 결정 이슈" in text


def test_branch_b_when_still_split() -> None:
    rows = _cells(IS_SEGMENT, n=20, significant=10) + _cells(OOS_SEGMENT, n=20, significant=1)
    text = branch_verdict(rows)
    assert "(나) 그대로 (c)" in text


def test_branch_verdict_reports_insufficient_sample() -> None:
    thin = [_row(segment=OOS_SEGMENT, real_num_trades=MIN_TRADES_FOR_VERDICT - 1)]
    assert "판정 불가" in branch_verdict(thin)


def test_branch_verdict_reads_baseline_lens_only() -> None:
    """`pen_5bp` 행이 섞여도 공식 렌즈 판정이 흔들리지 않는다."""
    baseline = _cells(IS_SEGMENT, n=20, significant=10) + _cells(OOS_SEGMENT, n=20, significant=1)
    noise = [r.model_copy(update={"lens": PEN_LENS, "random_p_value": 0.001}) for r in baseline]
    assert branch_verdict(baseline) == branch_verdict(baseline + noise)


def test_old_comparison_reports_ratio_and_direction_only() -> None:
    rows = _cells(IS_SEGMENT, n=20, significant=2) + _cells(OOS_SEGMENT, n=20, significant=8)
    line = old_comparison_line(rows)
    assert "13/27" in line and "3/25" in line
    assert "↓ 줄었다" in line and "↑ 늘었다" in line


# --------------------------------------------------------------------------- #
# 4. 대칭 확인 (완료기준 5)
# --------------------------------------------------------------------------- #


def _symmetry_rows(real_pct: float, fake_pct: float) -> list[PositionNullRow]:
    return [
        _row(
            real_taps=1000,
            real_invalidation_taps=int(1000 * real_pct / 100),
            fake_taps=8000,
            fake_invalidation_taps=int(8000 * fake_pct / 100),
        )
    ]


def test_symmetry_line_passes_when_arms_match() -> None:
    line = symmetry_line(_symmetry_rows(15.6, 14.0))
    assert "대칭 성립" in line
    assert "방향" in line


def test_symmetry_line_flags_when_arms_diverge() -> None:
    """돌연변이 확인 — 벌어지면 조용히 통과하지 않는다."""
    line = symmetry_line(_symmetry_rows(30.0, 5.0))
    assert "🚨" in line
    assert "대칭이 흔들린다" in line
    assert SYMMETRY_TOLERANCE_PP == 5.0


def test_symmetry_table_reports_both_arms() -> None:
    frame = symmetry_table(_symmetry_rows(20.0, 10.0))
    assert list(frame["real_inval_pct"]) == [20.0]
    assert list(frame["fake_inval_pct"]) == [10.0]
    assert list(frame["delta_pp"]) == [10.0]


def test_invalidation_tap_census_counts_only_the_invalidation_bar() -> None:
    """WAN-364의 소급 취소가 지우던 탭이 정확히 이 부분집합이다."""
    zone = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=110.0,
        bottom=100.0,
        start_time=0,
        confirmed_time=1_000,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=True,
        break_time=5_000,
        swept_time=None,
        tapped_times=(2_000, 5_000),
    )
    assert invalidation_tap_census([zone]) == (1, 2)
    alive = zone.model_copy(update={"breaker": False, "break_time": None})
    assert invalidation_tap_census([alive]) == (0, 2)


# --------------------------------------------------------------------------- #
# 5. net R 산식
# --------------------------------------------------------------------------- #


def _trade(entry: float, qty: float, pnl: float) -> Trade:
    return Trade(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=entry,
        quantity=qty,
        entry_fee=0.0,
        entry_liquidity=Liquidity.MAKER,
        exits=[
            TradeFill(time=1, price=entry, quantity=qty, fee=0.0, reason=ExitReason.TAKE_PROFIT)
        ],
        realized_pnl=pnl,
        return_pct=pnl / (entry * qty),
    )


class _StubCandidate:
    def __init__(self, stop_price: float) -> None:
        self.stop_price = stop_price


def test_mean_net_r_divides_by_that_trades_own_risk_amount() -> None:
    """실현 손익 ÷ (수량 × 손절 거리) — `harness.mean_r`(승률 재탕)이 아니다."""
    pairs = [
        (_StubCandidate(90.0), _trade(entry=100.0, qty=2.0, pnl=30.0)),  # risk 20 → +1.5R
        (_StubCandidate(95.0), _trade(entry=100.0, qty=2.0, pnl=-10.0)),  # risk 10 → -1.0R
    ]
    value = mean_net_r(pairs)  # type: ignore[arg-type]
    assert value is not None
    assert value == 0.25


def test_mean_net_r_skips_zero_risk_and_empty() -> None:
    assert mean_net_r([]) is None
    zero = [(_StubCandidate(100.0), _trade(entry=100.0, qty=1.0, pnl=5.0))]
    assert mean_net_r(zero) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 6·7. leave-one-out · 요약 렌더
# --------------------------------------------------------------------------- #


def test_leave_one_out_covers_every_symbol_not_a_frozen_three() -> None:
    """옛 판은 ETH·SOL·DOGE 셋만 뺐다 — 12종목 좌표에서는 전부 돈다."""
    cells = [
        _row(symbol=f"{name}/USDT:USDT", segment=OOS_SEGMENT, real_total_return=value)
        for name, value in (("BTC", 0.4), ("ADA", 0.2), ("BCH", 0.1), ("DOT", 0.05))
    ]
    lines = leave_one_out_lines(cells, lens=BASELINE_LENS)
    assert len(lines) == 1
    assert "4판 전부 **부호 유지**" in lines[0]


def test_leave_one_out_flags_a_sign_flip() -> None:
    cells = [
        _row(symbol=f"{name}/USDT:USDT", segment=OOS_SEGMENT, real_total_return=value)
        for name, value in (("ETH", 1.0), ("BTC", -0.2), ("ADA", -0.2), ("DOT", -0.2))
    ]
    lines = leave_one_out_lines(cells, lens=BASELINE_LENS)
    assert "부호 뒤집힘" in lines[0]
    assert "−ETH" in lines[0]


def test_summary_table_carries_net_r_columns() -> None:
    rows = [_row(real_mean_net_r=0.12, random_mean_net_r=-0.03)]
    frame = summary_table(rows, lens=BASELINE_LENS)
    assert float(frame.loc[0, "real_net_r"]) == 0.12
    assert float(frame.loc[0, "random_net_r"]) == -0.03


def test_summary_markdown_carries_verdict_and_warnings() -> None:
    rows = _cells(IS_SEGMENT, n=20, significant=10) + _cells(OOS_SEGMENT, n=20, significant=1)
    rows = [
        r.model_copy(
            update={
                "real_taps": 100,
                "real_invalidation_taps": 15,
                "fake_taps": 800,
                "fake_invalidation_taps": 112,
                "real_mean_net_r": -0.1,
                "random_mean_net_r": -0.15,
            }
        )
        for r in rows
    ]
    text = build_summary_markdown(rows)
    assert "(나) 그대로 (c)" in text
    assert "셀을 직접 비교하지 말 것" in text
    assert "총수익 %" in text and "복리" in text
    assert "익절 1.5R에서 쟀다" in text
    assert "대칭" in text
    assert "ALPHABLOCK_LIVE_TRADING=false" in text
    assert isinstance(pd.DataFrame(), pd.DataFrame)


def test_pool_k_matches_the_published_wan248_table() -> None:
    """자는 모듈 기본값(8)이 아니라 **WAN-248 공개 CSV의 `pool_k`(4)** 다."""
    assert POOL_K == 4


def test_branch_verdict_can_read_the_pen_lens() -> None:
    """🚨 렌즈를 함수 안에 못 박으면 `pen_5bp` 절이 조용히 「판정 불가」를 찍는다."""
    pen_rows = [
        r.model_copy(update={"lens": PEN_LENS})
        for r in _cells(IS_SEGMENT, n=20, significant=10) + _cells(OOS_SEGMENT, n=20, significant=1)
    ]
    assert "판정 불가" in branch_verdict(pen_rows)  # 공식 렌즈 행이 없다
    assert "(나) 그대로 (c)" in branch_verdict(pen_rows, lens=PEN_LENS)


def test_summary_markdown_renders_a_pen_verdict_not_a_blank() -> None:
    baseline = _cells(IS_SEGMENT, n=20, significant=10) + _cells(OOS_SEGMENT, n=20, significant=1)
    pen = [r.model_copy(update={"lens": PEN_LENS}) for r in baseline]
    text = build_summary_markdown(baseline + pen)
    assert "(pen_5bp 미측정)" not in text
    assert text.count("(나) 그대로 (c)") >= 2


def test_old_wan248_csv_still_parses_and_reproduces_its_published_verdict() -> None:
    """새 관측 열을 더해도 **옛 공개 CSV가 그대로 읽히고 같은 판정을 낸다**.

    이 계열의 핵심 계약은 「옛 표는 그때의 기록으로 보존된다」이고, 열을 더하면서 그 표를
    못 읽게 만드는 것이 가장 흔한 방식의 위반이다. 새 열은 전부 기본값 `None`이라 옛 행이
    그대로 살아 있어야 한다.
    """
    if not WAN248_CSV.exists():  # pragma: no cover - 저장소에 항상 있다
        pytest.skip("wan248 공개 CSV 없음")
    rows = wan248_rows_from_csv(WAN248_CSV)
    assert len(rows) == 108
    assert all(r.real_mean_net_r is None and r.real_taps is None for r in rows)
    baseline = [r for r in rows if r.lens == BASELINE_LENS]
    assert "유효 셀 52개 중 유의 16개" in wan248_verdict(baseline)


def test_the_old_module_still_pins_the_filter_on() -> None:
    """면제의 짝 — **옛 판은 필터를 켠 채(1.28) 고정돼 있어야 한다**.

    WAN-403이 WAN-384 존폭 핀 스캔에서 면제된 근거가 *「핀 없음이 측정 대상 그 자체」*인데,
    그 면제는 **옛 판이 계속 고정돼 있을 때만** 정당하다. 둘이 같이 핀을 잃으면 두 표가 모두
    조용히 오늘 엔진으로 돌아 「그때는 그랬다」가 사라진다.
    """
    assert wan248_module._Task.__dataclass_fields__["zone_width_pin"].default == (
        harness.LEGACY_ZONE_WIDTH_FILTER_ON
    )
    assert resolve_params(harness.LEGACY_ZONE_WIDTH_FILTER_ON).max_zone_width_atr == 1.28
