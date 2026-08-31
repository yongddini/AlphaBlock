"""WAN-381: 출구 눈금 격자 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 아홉:

1. **격자 좌표가 사용자 결정 그대로다** — 가드 5점 · 배수 4점이고 채택값이 둘 다 들어 있으며
   **위쪽 배수(2.0·2.5·3.0R)는 없다**(WAN-386이 이미 냈다). 개발자가 점을 더하거나 빼면
   깨진다.
2. **겹침이 WAN-386 격자를 실제로 덮는다** — 검산 (d)가 성립할 6조합이 그쪽 축 안에 있다.
3. **꺾임 판정이 억지로 최고점을 고르지 않는다** — 끝점이 최선이면 「안 꺾였다」로 쓰고,
   내부에서 꺾이면 그 점을 찍는다(완료기준 2·9).
4. **두 눈금의 상호작용 줄이 실제 데이터를 본다** — 최적 배수가 가드마다 다르면 그렇게 찍고
   같으면 그렇게 찍는다(완료기준 10).
5. **뒤집힘을 세는 자가 IS와 OOS를 실제로 비교한다**(완료기준 4·11).
6. **표본 게이트가 깨지면 표에 찍힌다** — 억지로 살리지 않는다(완료기준 3).
7. **지갑 층 열은 뜻을 잃으면 숫자를 안 낸다**(WAN-386 `wallet_defined` 재사용).
8. 🚨 **좌표가 다르면 WAN-386 대조를 하지 않는다** — 좁혀 돈 판을 그 격자와 비교하면
   「다른 좌표의 두 표」가 배선 오류처럼 보인다(파일럿에서 실제로 그랬다).
9. 🚨 **기준 팔 후보 ≡ 엔진 base+재진입**(실데이터) — 이 표의 모든 칸이 그 위에 선다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from backtest import harness
from backtest.confirmation_arm import ARM_BASE
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import _Task, arm_key, run_cell
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan381_exit_scales import (
    ADOPTED_MULTIPLE,
    CHECK_GUARDS,
    CHECK_MULTIPLES,
    GUARD_POINTS,
    MULTIPLES,
    NOISE_R,
    bend_verdict,
    build_summary_markdown,
    cross_check_wan386,
    curve,
    flip_rows,
    gate_line,
    interaction_line,
    judgment_points,
    on_adopted_coordinates,
    run_checksum,
)
from backtest.wan386_confirmation_pnl import GUARD_POINTS as WAN386_GUARDS
from backtest.wan386_confirmation_pnl import MULTIPLES as WAN386_MULTIPLES
from backtest.wan386_confirmation_pnl import GridRow

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-10-01"


def _skip_without_real_data() -> None:
    """🚨 게이트는 무거운 호출 **전에** 판정한다 — 안 그러면 CI의 빈 DB가 실패로 끝난다."""
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")


def _grid_row(**overrides: Any) -> GridRow:
    base: dict[str, Any] = {
        "arm": ARM_BASE,
        "guard": ADOPTED_STOP_GUARD,
        "multiple": ADOPTED_MULTIPLE,
        "segment": "oos_warm",
        "adopted_point": True,
        "num_cells": 48,
        "num_symbols": 12,
        "num_trades": 100,
        "win_rate": 0.4,
        "mean_net_r": -0.1,
        "mean_gross_r": -0.04,
        "total_return_flat": -0.3,
        "max_drawdown": 0.4,
        "return_over_mdd": -0.75,
        "peak_concurrency": 8,
        "max_concurrent_risk": 0.05,
        "max_effective_concurrent_risk": 0.05,
        "clamp_rate": 0.5,
        "mean_effective_risk": 0.01,
        "liquidation_events": 0,
        "guard_cut": 10,
        "guard_kept": 90,
        "symbols_below_gate": 0,
        "min_symbol_trades": 30,
    }
    base.update(overrides)
    return GridRow(**base)


def _grid(values: dict[tuple[float, float], float], *, segment: str = "oos_warm") -> list[GridRow]:
    """(가드, 배수) → 거래당 net R 로 격자를 짓는다."""
    return [
        _grid_row(
            guard=guard,
            multiple=multiple,
            segment=segment,
            mean_net_r=net,
            adopted_point=(guard == ADOPTED_STOP_GUARD and multiple == ADOPTED_MULTIPLE),
        )
        for (guard, multiple), net in values.items()
    ]


def _flat_grid(fn: Any, *, segment: str = "oos_warm") -> list[GridRow]:
    return _grid({(g, m): fn(g, m) for g in GUARD_POINTS for m in MULTIPLES}, segment=segment)


# --------------------------------------------------------------------------- #
# 1·2. 격자 좌표 — 사용자 결정 그대로 (개발자가 점을 더하거나 빼지 않는다)
# --------------------------------------------------------------------------- #


def test_grid_axes_match_the_user_decision() -> None:
    assert GUARD_POINTS == (0.0030, 0.0040, 0.0050, 0.0060, 0.0080)
    assert MULTIPLES == (0.6, 0.8, 1.0, 1.5)
    assert ADOPTED_STOP_GUARD in GUARD_POINTS
    assert ADOPTED_MULTIPLE in MULTIPLES


def test_upper_multiples_are_not_re_run() -> None:
    """⚠️ 위쪽(2.0·2.5·3.0R)은 WAN-386이 이미 냈고 **단조로 나빠진다** — 다시 돌지 않는다."""
    assert not {2.0, 2.5, 3.0} & set(MULTIPLES)


def test_overlap_with_wan386_is_actually_covered_by_both_grids() -> None:
    """검산 (d)가 성립하려면 겹침이 **양쪽 축 안**에 있어야 한다."""
    assert set(CHECK_GUARDS) <= set(GUARD_POINTS) & set(WAN386_GUARDS)
    assert set(CHECK_MULTIPLES) <= set(MULTIPLES) & set(WAN386_MULTIPLES)
    assert len(CHECK_GUARDS) * len(CHECK_MULTIPLES) == 6


# --------------------------------------------------------------------------- #
# 3. 꺾임 판정 — 억지로 최고점을 고르지 않는다
# --------------------------------------------------------------------------- #


def test_monotone_curve_is_reported_as_not_bending() -> None:
    """끝점이 최선이면 「안 꺾였다」다 — 끝점을 「최적값」으로 부르지 않는다."""
    rows = _flat_grid(lambda g, m: -0.20 + GUARD_POINTS.index(g) * 0.02)
    verdict = bend_verdict(rows, axis="guard", fixed=ADOPTED_MULTIPLE, segment="oos_warm")
    assert "안 꺾였다" in verdict
    assert "최적값" in verdict  # 끝점을 최적으로 읽지 말라는 경고가 함께 나온다


def test_interior_peak_is_reported_with_its_location() -> None:
    peaks = {0.0030: -0.20, 0.0040: -0.10, 0.0050: -0.05, 0.0060: -0.12, 0.0080: -0.30}
    rows = _flat_grid(lambda g, m: peaks[g])
    verdict = bend_verdict(rows, axis="guard", fixed=ADOPTED_MULTIPLE, segment="oos_warm")
    assert "0.50%에서 꺾인다" in verdict
    assert "argmax" in verdict  # 채택 권고로 쓰지 말라는 경고가 붙는다


def test_curve_falling_from_the_first_point_is_reported_as_no_room() -> None:
    rows = _flat_grid(lambda g, m: -0.10 - GUARD_POINTS.index(g) * 0.02)
    verdict = bend_verdict(rows, axis="guard", fixed=ADOPTED_MULTIPLE, segment="oos_warm")
    assert "시작점부터 내려간다" in verdict


def test_curve_reads_the_requested_axis() -> None:
    rows = _flat_grid(lambda g, m: MULTIPLES.index(m) * 0.01)
    assert [
        x for x, _row in curve(rows, axis="multiple", fixed=ADOPTED_STOP_GUARD, segment="oos_warm")
    ] == list(MULTIPLES)
    assert [
        x for x, _row in curve(rows, axis="guard", fixed=ADOPTED_MULTIPLE, segment="oos_warm")
    ] == list(GUARD_POINTS)


# --------------------------------------------------------------------------- #
# 4. 두 눈금의 상호작용 — 이 이슈가 합쳐진 이유
# --------------------------------------------------------------------------- #


def test_interaction_line_says_so_when_the_best_multiple_moves_with_the_guard() -> None:
    """가드마다 최적 배수가 다르면 **그렇게 찍는다** — 따로 돌려서는 못 얻는 답이다."""
    best = {0.0030: 1.5, 0.0040: 1.5, 0.0050: 1.0, 0.0060: 0.8, 0.0080: 0.8}
    rows = _flat_grid(lambda g, m: 0.01 if m == best[g] else -0.10)
    line = interaction_line(rows, segment="oos_warm")
    assert "실제로 움직인다" in line
    assert "0.50%→1R" in line


def test_interaction_line_says_so_when_it_does_not_move() -> None:
    rows = _flat_grid(lambda g, m: 0.01 if m == 0.8 else -0.10)
    line = interaction_line(rows, segment="oos_warm")
    assert "안 움직인다" in line
    assert "0.8R" in line


# --------------------------------------------------------------------------- #
# 5. IS→OOS 뒤집힘 — 앞구간에서 고른 값이 뒷구간에서도 최선인가
# --------------------------------------------------------------------------- #


def test_flip_rows_compare_is_against_oos_warm() -> None:
    rows = [
        *_flat_grid(lambda g, m: 0.01 if m == 1.5 else -0.10, segment="is"),
        *_flat_grid(lambda g, m: 0.01 if m == 0.8 else -0.10, segment="oos_warm"),
    ]
    flips = flip_rows(rows)
    guard_lines = [row for row in flips if "최적 배수" in row[0]]
    assert len(guard_lines) == len(GUARD_POINTS)
    assert all(
        is_best == "1.5R" and oos_best == "0.8R" and flipped
        for _label, is_best, oos_best, flipped in guard_lines
    )


def test_flip_rows_report_no_flip_when_the_two_segments_agree() -> None:
    rows = [
        *_flat_grid(lambda g, m: 0.01 if m == 1.5 else -0.10, segment="is"),
        *_flat_grid(lambda g, m: 0.01 if m == 1.5 else -0.10, segment="oos_warm"),
    ]
    assert not any(flipped for _label, _i, _o, flipped in flip_rows(rows))


# --------------------------------------------------------------------------- #
# 6. 표본 게이트 — 깨지면 찍는다 (억지로 살리지 않는다)
# --------------------------------------------------------------------------- #


def test_sample_gate_breakage_is_reported_not_hidden() -> None:
    rows = _flat_grid(lambda g, m: -0.10)
    rows.append(
        _grid_row(
            guard=0.0080,
            multiple=0.6,
            symbols_below_gate=3,
            min_symbol_trades=4,
            adopted_point=False,
        )
    )
    line = gate_line(rows, segment="oos_warm")
    assert "표본이 깨지는 점이 있다" in line
    assert "0.80%" in line
    assert "억지로 살리지 않는다" in line


def test_sample_gate_reports_the_thinnest_cell_when_nothing_breaks() -> None:
    rows = _flat_grid(lambda g, m: -0.10)
    line = gate_line(rows, segment="oos_warm")
    assert "안 깨진다" in line
    assert "30거래" in line


# --------------------------------------------------------------------------- #
# 7·8. 지갑 층 · 좌표 가드
# --------------------------------------------------------------------------- #


def test_summary_suppresses_wallet_ratios_once_the_wallet_goes_through_zero() -> None:
    rows = _flat_grid(lambda g, m: -0.10)
    rows = [r.model_copy(update={"total_return_flat": -11.06, "max_drawdown": 9.96}) for r in rows]
    text = build_summary_markdown(rows, [], [], [])
    assert "정의 상실" in text
    assert "거래당 net R은 이 함정에 안 걸린다" in text


def test_cross_check_is_skipped_when_the_run_is_narrowed() -> None:
    """🚨 좁혀 돈 판을 WAN-386 격자와 대조하면 좌표 차이가 배선 오류처럼 보인다."""
    assert on_adopted_coordinates(harness.DEFAULT_SYMBOLS, harness.DEFAULT_TIMEFRAMES) is True
    assert on_adopted_coordinates([harness.DEFAULT_SYMBOLS[0]], ["4h"]) is False
    assert on_adopted_coordinates(harness.DEFAULT_SYMBOLS, ["4h"]) is False


def test_cross_check_reports_a_missing_csv_instead_of_passing_silently() -> None:
    from pathlib import Path

    rows = cross_check_wan386(_flat_grid(lambda g, m: -0.10), path=Path("/nonexistent.csv"))
    assert len(rows) == 1
    assert rows[0].abs_diff == 1.0  # 조용히 통과하지 않는다


def test_judgment_points_include_the_adopted_point_and_the_grid_best() -> None:
    rows = _flat_grid(lambda g, m: 0.01 if (g, m) == (0.0060, 0.8) else -0.10)
    points = judgment_points(rows)
    assert (ADOPTED_STOP_GUARD, ADOPTED_MULTIPLE) in points
    assert (0.0060, 0.8) in points


def test_summary_does_not_claim_all_negative_when_a_cell_is_positive() -> None:
    """🚨 「전부 음수」는 계산해서 쓴다 — 라벨로 박아 두면 데이터와 어긋난다."""
    cells = len(GUARD_POINTS) * len(MULTIPLES)
    positive = _flat_grid(lambda g, m: 0.01 if (g, m) == (0.0060, 0.8) else -0.10)
    text = build_summary_markdown(positive, [], [], [])
    assert f"{cells}조합 전부 음수다" not in text  # 이 격자에 대한 주장은 계산해서 쓴다
    assert f"{cells}조합 중 1조합이 양수다" in text

    negative = _flat_grid(lambda g, m: -0.10)
    assert f"{cells}조합 전부 음수다" in build_summary_markdown(negative, [], [], [])


def test_noise_line_matches_the_repository_convention() -> None:
    assert NOISE_R == 0.005


# --------------------------------------------------------------------------- #
# 9. 실데이터 — 기준 팔 후보 ≡ 엔진 base+재진입
# --------------------------------------------------------------------------- #


def _task(**overrides: Any) -> _Task:
    base: dict[str, Any] = {
        "symbol": harness.normalize_symbol(_REAL_SYMBOL),
        "timeframe": _REAL_TF,
        "start_ms": parse_date_ms(_REAL_START),
        "end_ms": parse_date_ms(_REAL_END),
        "reentry": True,
        "confirmation_arms": (ARM_BASE,),
        "confirmation_multiples": MULTIPLES,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    }
    base.update(overrides)
    return _Task(**base)


def _entry_exit_keys(candidates: Sequence[Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (c.entry_time, c.entry_price, c.exit_time, c.exit_price, c.reason.value) for c in candidates
    )


def test_base_arm_candidates_equal_the_engine_on_real_data() -> None:
    """🚨 이 등식이 깨지면 격자의 **모든 칸**이 다른 눈금 위에 선다."""
    _skip_without_real_data()
    payload = run_cell(_task())
    engine = [*payload.candidates["full"], *payload.reentry_candidates.get("full", ())]
    derived = payload.arm_candidates[arm_key(ARM_BASE, ADOPTED_MULTIPLE)]["full"]
    assert engine, "실데이터에서 후보가 하나도 안 나왔습니다 — 게이트가 잘못됐습니다."
    assert _entry_exit_keys(engine) == _entry_exit_keys(derived)


def test_multiples_share_one_entry_set_but_differ_in_exits_on_real_data() -> None:
    """익절은 **청산만** 바꾼다(WAN-137/143 훅) — 진입 집합이 갈라지면 축이 둘이 된다."""
    _skip_without_real_data()
    payload = run_cell(_task())
    entries = {
        m: sorted(
            (c.entry_time, c.entry_price)
            for c in payload.arm_candidates[arm_key(ARM_BASE, m)]["full"]
        )
        for m in MULTIPLES
    }
    assert len({tuple(v) for v in entries.values()}) == 1
    exits = {
        m: sorted(c.exit_time for c in payload.arm_candidates[arm_key(ARM_BASE, m)]["full"])
        for m in MULTIPLES
    }
    assert exits[0.6] != exits[ADOPTED_MULTIPLE], "배수를 바꿨는데 청산이 안 움직였습니다."


def test_checksum_cross_check_is_skipped_without_the_adopted_coordinates() -> None:
    """좁혀 돈 실행에서는 (d)가 「건너뜀」으로 남아야 한다 — 라벨만 남기지 않는다."""
    _skip_without_real_data()
    payload = run_cell(_task())
    checks = run_checksum(
        [payload],
        _flat_grid(lambda g, m: -0.10),
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        cross_check=False,
        log=False,
    )
    skipped = [c for c in checks if c.metric == "skipped"]
    assert len(skipped) == 1
    assert "건너뜀" in skipped[0].check
    assert max(c.abs_diff for c in checks) == 0.0


# --------------------------------------------------------------------------- #
# 10. 후보 생성이 채택 북 경로와 같은 인자로 돈다 (완료기준 6의 나머지 반쪽)
# --------------------------------------------------------------------------- #


def test_candidate_generation_uses_the_adopted_book_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🚨 검산 (a-2)는 **같은 payload를 두 방식으로 배치**한 것이라 후보 **생성**은 못 본다.

    후보를 처음부터 다시 만드는 독립 경로는 이 모듈 비용을 한 판 더 쓰므로(~4시간), 대신
    두 경로가 `run_cells`에 **실제로 넘기는 인자**를 가로채 대조한다 — 라벨이 아니라 호출로
    「인자 없는 채택 북과 같은 후보를 본다」를 고정한다(WAN-330이 세운 스파이 패턴).

    ⚠️ 딱 하나 일부러 다르다: 이 모듈은 `confirmation_arms=(기준,)`을 더한다(익절 배수를
    갈아끼우는 훅). 그것이 **후보 집합을 안 바꾼다**는 것은 검산 (a-1)이 실데이터로 낸다.
    """
    from backtest import book_cli
    from backtest.leverage_book import LeverageBookParams
    from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
    from backtest.wan386_confirmation_pnl import build_payloads

    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan386_confirmation_pnl.run_cells", _fake)
    monkeypatch.setattr(book_cli, "run_cells", _fake)
    monkeypatch.setattr(book_cli, "apply_funding_proxy", lambda p: (list(p), ""))

    build_payloads(
        ["BTCUSDT"],
        ["1h"],
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        jobs=1,
        arms=(ARM_BASE,),
        multiples=MULTIPLES,
    )
    book_cli.run_book(
        ["BTCUSDT"],
        ["1h"],
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        book=LeverageBookParams(),
        segments=[harness.SEGMENT_FULL],
        jobs=1,
        log=False,
    )

    assert len(captured) == 2
    mine, adopted = captured
    for key in ADOPTED_CELL_KWARGS:
        assert mine[key] == adopted[key], f"{key}가 채택 북 경로와 다르다"
    # 익절 청산 유동성을 잊으면 조용히 옛 회계로 돈다(WAN-370/373).
    assert mine["take_profit_liquidity"] == harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert mine["take_profit_liquidity"] == adopted.get(
        "take_profit_liquidity", harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    )
    # 이 모듈이 더하는 것은 팔 훅 하나뿐이고, 그 팔은 **기준 팔** 하나다.
    assert mine["confirmation_arms"] == (ARM_BASE,)
    assert tuple(mine["confirmation_multiples"]) == MULTIPLES
