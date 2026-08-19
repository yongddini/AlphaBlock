"""WAN-330 — 채택 4TF 지갑 위의 반익절 래더 (스코프 · 렌즈 · 검산).

두 층으로 고정한다:

* **인자 없는 데이터** — 스코프 분해, 렌즈 인자 규약(`baseline`은 `None`), 검산 (c) 비교기,
  판정 문장. 그리고 이 모듈이 `run_cells`에 넘기는 **채택 좌표 인자**가 `book_cli.run_book`
  (= 인자 없는 채택 북)과 같은지를 **실제 호출 인자 캡처**로 본다 — 모듈 상수를 서로 비교하면
  둘이 같이 틀려도 통과하므로 의미가 없다.
* **실데이터(있을 때만)** — 이 이슈의 핵심 주장인 「4TF 실행에서 15m 칸만 빼면 3TF 실행과
  같은 지갑이다」를 작은 창에서 실제로 확인한다(WAN-316 `--check-legacy-grid`의 단위 판).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from backtest import book_cli, harness
from backtest.leverage_book import LeverageBookParams
from backtest.wan323_partial_tp_ladder import ARMS_BY_NAME
from backtest.wan330_partial_tp_ladder_4tf import (
    ADOPTED_CELL_KWARGS,
    BASELINE_LENS,
    BOTH_NO_15M_SCOPE,
    BOTH_SCOPE,
    STRESS_LENS,
    _lens_arg,
    build_summary,
    compare_legacy_book,
    resolve_scopes,
    run_arm,
    scope_payloads,
)

# --------------------------------------------------------------------------- #
# 채택 좌표 인자 — 라벨이 아니라 실제 호출로
# --------------------------------------------------------------------------- #


def test_adopted_cell_kwargs_match_the_bare_adopted_book(monkeypatch: pytest.MonkeyPatch) -> None:
    """`A0` 팔이 `run_cells`에 넘기는 좌표 인자가 **인자 없는 채택 북**과 같다(검산 (a)의 고리).

    검산 (a)는 「같은 payload에서 나온 행이 같은가」까지만 보므로, payload를 **만드는** 인자가
    채택 경로와 갈라지면 못 잡는다. 여기서 두 경로의 `run_cells` 호출을 실제로 가로채 유동성
    한도·재진입·재무장 규칙이 같은지 본다 — 이 셋 중 하나만 어긋나도 「A0 = 현행」이라는 표의
    기준점이 조용히 깨진다(WAN-91/95/112/123/159 부류).
    """
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan330_partial_tp_ladder_4tf.run_cells", _fake)
    monkeypatch.setattr(book_cli, "run_cells", _fake)
    monkeypatch.setattr(book_cli, "apply_funding_proxy", lambda p: (list(p), ""))

    run_arm(
        ["BTCUSDT"],
        ["1h"],
        ARMS_BY_NAME["A0"],
        lens=BASELINE_LENS,
        scopes=[BOTH_SCOPE],
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        jobs=1,
        log=False,
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
    # 기준선 팔은 래더를 켜지 않는다 — 켜면 「A0 = 현행」이 아니게 된다.
    assert mine["partial_take_profit_r"] is None
    assert mine["breakeven_after_partial"] is False
    assert mine["fill"] is None  # `baseline` = 채택 기본값 = 옛 CSV 비트 재현


def test_ladder_arms_do_not_run_the_engine_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """래더 팔은 `engine_check`를 끈다 — 그 검산은 래더 없는 per-cell과의 비트 일치라
    래더를 켠 팔에서는 **당연히** 어긋난다(끄지 않으면 정상 실행이 실패로 보인다)."""
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan330_partial_tp_ladder_4tf.run_cells", _fake)
    for name, expected in (("A0", True), ("A1_be_on", False)):
        captured.clear()
        run_arm(
            ["BTCUSDT"],
            ["1h"],
            ARMS_BY_NAME[name],
            lens=BASELINE_LENS,
            scopes=[BOTH_SCOPE],
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            jobs=1,
            log=False,
        )
        assert captured[0]["engine_check"] is expected


def test_stress_lens_is_passed_through_but_baseline_is_none() -> None:
    """`baseline`은 `None`으로 넘어가야 옛 CSV가 비트 재현된다 — 렌즈 객체를 넘기면 안 된다."""
    assert _lens_arg(BASELINE_LENS) is None
    stress = _lens_arg(STRESS_LENS)
    assert stress is not None and stress.name == STRESS_LENS


def test_stress_lens_arm_disables_engine_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pen_5bp`에서는 기준선 팔도 `engine_check`를 끈다 — 그 검산은 채택 렌즈 기준이다."""
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan330_partial_tp_ladder_4tf.run_cells", _fake)
    run_arm(
        ["BTCUSDT"],
        ["1h"],
        ARMS_BY_NAME["A0"],
        lens=STRESS_LENS,
        scopes=[BOTH_SCOPE],
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        jobs=1,
        log=False,
    )
    assert captured[0]["engine_check"] is False


# --------------------------------------------------------------------------- #
# 스코프 — 한 payload 집합에서 두 지갑
# --------------------------------------------------------------------------- #


class _StubPayload:
    def __init__(self, timeframe: str) -> None:
        self.timeframe = timeframe


def test_scope_payloads_splits_the_wallet() -> None:
    payloads = [_StubPayload(tf) for tf in ("15m", "1h", "2h", "4h")]
    assert len(scope_payloads(payloads, BOTH_SCOPE)) == 4  # type: ignore[arg-type]
    no15 = scope_payloads(payloads, BOTH_NO_15M_SCOPE)  # type: ignore[arg-type]
    assert [p.timeframe for p in no15] == ["1h", "2h", "4h"]
    assert [p.timeframe for p in scope_payloads(payloads, "1h")] == ["1h"]  # type: ignore[arg-type]


def test_resolve_scopes_only_adds_the_legacy_wallet_when_15m_is_present() -> None:
    assert resolve_scopes(("15m", "1h", "2h", "4h")) == [BOTH_SCOPE, BOTH_NO_15M_SCOPE]
    assert resolve_scopes(("1h", "2h", "4h")) == [BOTH_SCOPE]
    # 15m을 빼면 칸이 하나뿐인 실행은 「3TF 판」이 아니다 — 라벨만 붙는 행을 만들지 않는다.
    assert resolve_scopes(("15m", "1h")) == [BOTH_SCOPE]


# --------------------------------------------------------------------------- #
# 검산 (c) 비교기 · 판정 문장
# --------------------------------------------------------------------------- #


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    base = {
        "lens": BASELINE_LENS,
        "scope": BOTH_SCOPE,
        "arm": "A0",
        "family": "A",
        "take_profit_r": 1.5,
        "partial_r": None,
        "breakeven": False,
        "segment": harness.SEGMENT_OOS_WARM,
        "stress_k": 1.0,
        "num_cells": 48,
        "num_trades": 100,
        "win_rate": 0.5,
        "total_return": 1.0,
        "max_drawdown": 0.2,
        "return_over_mdd": 5.0,
        "peak_concurrency": 10,
        "max_concurrent_risk": 0.1,
        "max_effective_concurrent_risk": 0.1,
        "liquidation_events": 0,
        "skipped_notional": 0,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def test_compare_legacy_book_reports_a_mismatch(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검산 (c)는 「보존한다」가 라벨이 아니라 **같은 숫자**임을 본다 — 틀리면 차이가 나온다."""
    legacy = tmp_path / "legacy.csv"
    pd.DataFrame(
        [
            {
                "arm": "A0",
                "segment": harness.SEGMENT_OOS_WARM,
                "total_return": 1.0,
                "max_drawdown": 0.2,
                "max_concurrent_risk": 0.1,
                "num_trades": 100,
            }
        ]
    ).to_csv(legacy, index=False)
    monkeypatch.setattr("backtest.wan330_partial_tp_ladder_4tf.LEGACY_BOOK_CSV", legacy)

    same = _frame([{"scope": BOTH_NO_15M_SCOPE}])
    assert compare_legacy_book(same) == (1, 0.0)

    moved = _frame([{"scope": BOTH_NO_15M_SCOPE, "max_drawdown": 0.25}])
    result = compare_legacy_book(moved)
    assert result is not None and result[1] == pytest.approx(0.05)

    # 4TF 지갑은 옛 판에 대응물이 없다 — 대조에 섞이면 안 된다.
    assert compare_legacy_book(_frame([{"scope": BOTH_SCOPE}])) is None


def test_compare_legacy_book_returns_none_without_the_old_table(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backtest.wan330_partial_tp_ladder_4tf.LEGACY_BOOK_CSV", tmp_path / "nope.csv"
    )
    assert compare_legacy_book(_frame([{"scope": BOTH_NO_15M_SCOPE}])) is None


def test_summary_states_the_four_tf_verdict() -> None:
    """완료기준 3 — 「15m을 넣으면 −4.84%p가 얼마가 되는가」가 표가 아니라 **문장**으로 남는다."""
    frame = _frame(
        [
            {"scope": BOTH_SCOPE, "arm": "A0", "max_drawdown": 0.2290, "num_trades": 6336},
            {
                "scope": BOTH_SCOPE,
                "arm": "A1_be_on",
                "partial_r": 1.0,
                "breakeven": True,
                "max_drawdown": 0.2000,
                "num_trades": 7000,
            },
            {"scope": BOTH_NO_15M_SCOPE, "arm": "A0", "max_drawdown": 0.1958},
            {
                "scope": BOTH_NO_15M_SCOPE,
                "arm": "A1_be_on",
                "partial_r": 1.0,
                "breakeven": True,
                "max_drawdown": 0.1473,
            },
        ]
    )
    text = build_summary(frame)
    assert "4TF 채택 지갑 ΔMDD = -2.90%p" in text
    assert "-4.85%p" in text  # 같은 실행의 3TF 지갑 — 옛 판과 자릿수가 맞는다.
    assert "축소" in text
    assert f"`{BOTH_NO_15M_SCOPE}`" in text


def test_summary_marks_a_reversal_as_such() -> None:
    """래더가 낙폭을 **키우면** 요약이 그것을 「소멸(역전)」로 말한다 — 좋게 보이게 하지 않는다."""
    frame = _frame(
        [
            {"scope": BOTH_SCOPE, "arm": "A0", "max_drawdown": 0.20},
            {
                "scope": BOTH_SCOPE,
                "arm": "A1_be_on",
                "partial_r": 1.0,
                "breakeven": True,
                "max_drawdown": 0.24,
            },
            {"scope": BOTH_NO_15M_SCOPE, "arm": "A0", "max_drawdown": 0.1958},
            {
                "scope": BOTH_NO_15M_SCOPE,
                "arm": "A1_be_on",
                "partial_r": 1.0,
                "breakeven": True,
                "max_drawdown": 0.1473,
            },
        ]
    )
    assert "소멸(역전)" in build_summary(frame)


# --------------------------------------------------------------------------- #
# 실데이터 — 15m을 빼면 3TF 실행과 같은 지갑인가
# --------------------------------------------------------------------------- #

_START = "2024-01-01"
_END = "2024-04-01"
_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def _require_real_data() -> None:
    market = harness.load_market_data(
        harness.normalize_symbol("BTCUSDT"), "1h", start_ms=None, end_ms=None, need_1m=True
    )
    if market.empty:
        pytest.skip("BTCUSDT 1h 실데이터가 없어 스코프 검산을 건너뜁니다(CI 기본).")


def test_no15m_scope_equals_a_separate_three_tf_run() -> None:
    """이 이슈의 핵심 주장 — 「4TF 실행에서 15m 칸만 빼면 3TF 실행과 같은 지갑」.

    북은 이어붙일 수 없지만(WAN-316) **떼어낼 수는 있다**: 지갑은 한 프로세스의 칸 집합이라,
    같은 payload에서 15m 칸을 뺀 배치가 애초에 15m을 안 돌린 실행과 같은 값이어야 한다.
    이것이 성립해야 옛 3TF 판이 「라벨」이 아니라 **같은 숫자**로 보존된다.
    """
    _require_real_data()
    arm = ARMS_BY_NAME["A0"]
    four, _ = run_arm(
        _SYMBOLS,
        ["15m", "1h", "4h"],
        arm,
        lens=BASELINE_LENS,
        scopes=[BOTH_SCOPE, BOTH_NO_15M_SCOPE],
        start=_START,
        end=_END,
        jobs=1,
        log=False,
    )
    three, _ = run_arm(
        _SYMBOLS,
        ["1h", "4h"],
        arm,
        lens=BASELINE_LENS,
        scopes=[BOTH_SCOPE],
        start=_START,
        end=_END,
        jobs=1,
        log=False,
    )
    dropped = {r.segment: r for r in four if r.scope == BOTH_NO_15M_SCOPE}
    standalone = {r.segment: r for r in three}
    assert dropped and set(dropped) == set(standalone)
    for segment, row in dropped.items():
        other = standalone[segment]
        assert row.num_trades == other.num_trades
        assert row.total_return == other.total_return
        assert row.max_drawdown == other.max_drawdown
        assert row.max_concurrent_risk == other.max_concurrent_risk

    # 그리고 15m을 붙인 지갑은 **다른 지갑**이다(뺀 것이 실제로 있었다는 증거).
    full = {r.segment: r for r in four if r.scope == BOTH_SCOPE}
    assert any(full[s].num_trades != dropped[s].num_trades for s in full)


def test_stress_multiple_only_moves_the_effective_risk_column() -> None:
    """WAN-312 열 — `stress_risk_multiple`은 거래를 안 바꾸고 실효 리스크만 키운다.

    채택 회계(k=1)에서는 계획값과 **정의상 같다** — 이 표가 그 등식을 싣는 근거다.
    """
    _require_real_data()
    from backtest.wan169_leverage_book import run_cells

    payloads = run_cells(_SYMBOLS, ["1h"], start=_START, end=_END, jobs=1)
    kwargs: dict[str, Any] = {
        "book": LeverageBookParams(),
        "segments": [harness.SEGMENT_FULL],
        "start_ms": 0,
        "end_ms": 1,
    }
    plain = book_cli.build_book_rows(payloads, **kwargs)[0]
    stressed = book_cli.build_book_rows(payloads, stress_risk_multiple=1.5, **kwargs)[0]
    assert plain.max_effective_concurrent_risk == plain.max_concurrent_risk
    assert stressed.num_trades == plain.num_trades
    assert stressed.total_return == plain.total_return
    assert stressed.max_effective_concurrent_risk == pytest.approx(plain.max_concurrent_risk * 1.5)


def test_residual_ratio_refuses_meaningless_denominators() -> None:
    """🚨 WAN-115가 문서화한 함정 — 기준 증분이 0 언저리면 잔존율이 폭발해 「유지」로 읽힌다.

    실제로 이 격자의 `full` 셀에서 기준 ΔMDD가 +0.00%p였고 `pen_5bp`가 +0.01%p라 순진한
    비율이 **391.9%**를 찍었다. 그 셀은 크기가 아니라 **부호**로만 읽어야 한다.
    """
    from backtest.wan330_partial_tp_ladder_4tf import _residual_ratio

    assert _residual_ratio(-0.0381, -0.0260) == pytest.approx(0.6824, abs=1e-3)
    assert _residual_ratio(0.00003, 0.00013) is None  # 분모가 0 언저리 — 391.9% 함정
    assert _residual_ratio(-0.0031, -0.0361) is None  # 〃 (0.31%p < 하한)
    assert _residual_ratio(-0.0381, +0.0100) is None  # 부호가 갈리면 「잔존」이 성립 안 한다
    assert _residual_ratio(None, -0.02) is None
