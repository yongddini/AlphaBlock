"""WAN-372: 진입 순간 MACD 색 관측 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 여섯:

1. **관측 필드는 순수하다** — `observe_macd`를 켜도 후보·체결·손익이 하나도 안 움직인다
   (실데이터). 관측이 대상을 바꾸면 그 순간 이 측정은 무효다(WAN-328/376 선례).
2. **봉 안에서 잘라도 색이 비트 동일하다**(완료기준 4) — WAN-377이 만든 절단 자를 그대로
   쓴다. 룩어헤드(탭 봉 종가 MACD)가 섞이면 이 테스트가 죽는다.
3. **재진입 거래도 색을 받는다** — 채택 북은 재진입 ON이라(WAN-273) 한쪽만 배선하면 색
   표가 거래의 상당 부분을 조용히 놓친다. 🚨 **인자를 넘기는 줄이 아니라 재진입 후보에
   실제로 색이 붙었는지**로 건다(WAN-345의 교훈 — 넘기는 줄만 보는 테스트는 같은 실패를
   또 통과시킨다).
4. **색 귀속은 전수다** — 색별 거래 수의 합이 그 구간 거래 수와 같고, 워밍업은 어느 색으로도
   흡수되지 않는다.
5. **판정이 지어지지 않는다** — 표본 미달 색은 판정에서 빠지고, 앞구간 승자가 뒷구간에서
   뒤집히면 게이트가 「근거 없음」을 낸다(뒷구간은 고르는 축이 아니다).
6. **거래별 표의 색 열은 관측을 켠 실행에만 붙는다** — 안 켠 실행에 빈 열을 달면 옛 CSV의
   열 모양이 바뀌고 「값이 없다」와 「관측을 안 켰다」가 같은 모양이 된다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest.book_cli import (
    BOOK_MACD_COLUMNS,
    COL_MACD_COLOR,
    book_trades_to_display_frame,
    iter_book_segments,
    macd_color_label,
)
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan372_macd_color import (
    AXIS_OVERALL,
    BUCKET_ORDER,
    MIN_TRADES,
    NOISE_R,
    ColorRow,
    coverage_gap,
    gate_decision,
    placement_bucket,
    placement_color,
    render_summary,
    rows_for_segment,
    unlabeled_reentries,
    verdict,
)
from backtest.zone_limit_backtest import build_zone_limit_candidates
from data.models import timeframe_to_ms
from strategy.realtime_macd import WARMUP_LABEL, MacdColor
from tests.test_wan377_intrabar_cut_invariance import (
    _SYNTHETIC_FIXTURES,
    _SYNTHETIC_TF,
    _engine_params,
    _synthetic_1m,
    aggregate_1m,
    cut_world_intrabar,
    intrabar_cuts_for,
)

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-10-01"


def _shared_kwargs() -> dict[str, Any]:
    return {
        "start": _REAL_START,
        "end": _REAL_END,
        "jobs": 1,
        "cold_segments": False,
        "engine_check": False,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        **ADOPTED_CELL_KWARGS,
    }


def _skip_without_real_data() -> None:
    """🚨 게이트는 `run_cells` **호출 전에** 판정한다 — 안 그러면 CI의 빈 DB가 skip이 아니라
    실패로 끝난다(이 저장소가 이미 겪은 실패)."""
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")


def _bare(candidates: Any) -> list[tuple[Any, ...]]:
    """관측 필드를 뺀 후보 지문 — 「관측이 대상을 안 바꿨나」를 재는 자."""
    return [
        (c.entry_time, c.entry_price, c.exit_time, c.exit_price, c.reason, c.stop_price, c.mfe_r)
        for c in candidates
    ]


# --------------------------------------------------------------------------- #
# 1 · 관측 필드는 순수하다 (실데이터)
# --------------------------------------------------------------------------- #


def test_observation_field_moves_nothing() -> None:
    """켜도 후보·체결·청산이 **비트 단위로 같다** — 관측이 대상을 바꾸면 이 측정은 무효다."""
    _skip_without_real_data()
    off = run_cells([_REAL_SYMBOL], [_REAL_TF], **_shared_kwargs())
    on = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_macd=True, **_shared_kwargs())
    a = off[0].candidates[harness.SEGMENT_FULL]
    b = on[0].candidates[harness.SEGMENT_FULL]
    assert a, "후보가 없어 검사가 성립하지 않습니다."
    assert _bare(a) == _bare(b)
    assert all(c.macd_hist is None for c in a), "안 켰는데 값이 실렸습니다."
    assert any(c.macd_hist is not None for c in b), "켰는데 값이 안 실렸습니다."
    # 격리 성과 행까지 같아야 「손익이 안 움직였다」가 된다.
    assert [r.model_dump() for r in off[0].rows] == [r.model_dump() for r in on[0].rows]


def test_book_placement_is_unchanged_by_the_observation() -> None:
    """북 배치(거래 수·수익·MDD)도 비트 단위로 같다 — 검산 (a)와 같은 자다."""
    _skip_without_real_data()
    start_ms, end_ms = parse_date_ms(_REAL_START), parse_date_ms(_REAL_END)

    def book_row(observe: bool) -> tuple[int, float, float]:
        payloads = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_macd=observe, **_shared_kwargs())
        proxied, _note = apply_funding_proxy(payloads)
        segment = iter_book_segments(
            proxied,
            book=LeverageBookParams(),
            segments=[harness.SEGMENT_FULL],
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
            take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        )[0]
        return (segment.row.num_trades, segment.row.total_return, segment.row.max_drawdown)

    assert book_row(observe=False) == book_row(observe=True)


# --------------------------------------------------------------------------- #
# 2 · 봉 안에서 잘라도 색이 비트 동일하다 (완료기준 4)
# --------------------------------------------------------------------------- #


def _color_keys(candidates: Any, cut_ms: int) -> list[tuple[Any, ...]]:
    """절단 이전에 **청산까지 끝난** 셋업의 (진입 시각, 진입가, 히스토그램 한 쌍, 색).

    WAN-377의 `_setup_keys`와 같은 규약(미확정 청산은 뺀다)에 **색 축을 얹은** 것이다.
    """
    from backtest.models import ExitReason

    return sorted(
        (
            candidate.entry_time,
            candidate.entry_price,
            candidate.macd_hist,
            candidate.macd_hist_prev,
            placement_bucket(
                PlacedSetup(
                    cell=("x", "1h"),
                    equity=0.0,
                    risk_amount=1.0,
                    realized_pnl=0.0,
                    macd_hist=candidate.macd_hist,
                    macd_hist_prev=candidate.macd_hist_prev,
                )
            ),
        )
        for candidate in candidates
        if candidate.exit_time < cut_ms and candidate.reason is not ExitReason.END_OF_DATA
    )


@pytest.mark.parametrize(("seed", "swing_period"), _SYNTHETIC_FIXTURES)
def test_macd_color_survives_an_intrabar_cut(seed: int, swing_period: int) -> None:
    """「그 시점에 알 수 있던 것만」으로 다시 돌려도 이미 끝난 거래의 색이 **비트 동일**하다.

    🚨 이 자가 무는 것은 **탭 봉 종가 MACD**(룩어헤드)다 — 그 값은 봉이 어떻게 끝나느냐에
    달렸으므로, 봉을 반쪽만 준 세상에서는 다른 색이 나온다. WAN-377이 만든 절단 도구를
    그대로 쓴다(새로 짜지 않는다 — 그 결정문이 이 이슈를 이름까지 적어 뒀다).
    """
    minutes = _synthetic_1m(seed, swing_period)
    params = _engine_params()
    cfg = harness.build_config(_SYNTHETIC_TF)
    htf_ms = timeframe_to_ms(_SYNTHETIC_TF)

    def build(htf: pd.DataFrame, mins: pd.DataFrame) -> Any:
        candidates, _stats = build_zone_limit_candidates(
            htf, mins, _SYNTHETIC_TF, params=params, cfg=cfg, observe_macd=True
        )
        return candidates

    full = build(aggregate_1m(minutes, _SYNTHETIC_TF, allow_partial=False), minutes)
    assert any(c.macd_hist is not None for c in full), "색이 하나도 안 붙어 검사가 공허하다."

    compared = in_forming_bar = 0
    for cut in intrabar_cuts_for(full, _SYNTHETIC_TF):
        cut_htf, cut_1m = cut_world_intrabar(minutes, _SYNTHETIC_TF, cut)
        expected = _color_keys(full, cut)
        assert expected == _color_keys(build(cut_htf, cut_1m), cut), f"T={cut}에서 색이 갈렸다."
        compared += len(expected)
        bar_open = (cut // htf_ms) * htf_ms
        if cut != bar_open:
            in_forming_bar += sum(1 for key in expected if key[0] >= bar_open)
    assert compared > 0, "비교한 셋업이 없어 이 테스트는 아무것도 안 지켰다."
    assert in_forming_bar > 0, "재구성한 **반쪽 봉 안에서** 체결된 셋업이 없어 자가 안 물었다."


def test_a_lookahead_color_would_fail_the_cut(caplog: pytest.LogCaptureFixture) -> None:
    """돌연변이 확인 — **탭 봉 종가**로 색을 매기면 절단판에서 값이 달라진다.

    엔진에 그런 모드를 만들지 않고(만들면 그 자체가 룩어헤드 경로다) 같은 논증을 값으로
    확인한다: 반쪽 봉의 종가는 전체 봉의 종가와 다르다. 즉 「탭 봉 종가 MACD」는 봉이
    어떻게 끝나느냐에 달린 값이고, 그래서 위 테스트가 실제로 무는 자가 된다.
    """
    seed, swing_period = _SYNTHETIC_FIXTURES[0]
    minutes = _synthetic_1m(seed, swing_period)
    full_htf = aggregate_1m(minutes, _SYNTHETIC_TF, allow_partial=False)
    htf_ms = timeframe_to_ms(_SYNTHETIC_TF)
    bar_open = int(full_htf.iloc[40]["open_time"])
    cut = bar_open + htf_ms // 2
    cut_htf, _cut_1m = cut_world_intrabar(minutes, _SYNTHETIC_TF, cut)
    forming = cut_htf[cut_htf["open_time"] == bar_open]
    settled = full_htf[full_htf["open_time"] == bar_open]
    assert not forming.empty and not settled.empty
    assert float(forming.iloc[0]["close"]) != float(settled.iloc[0]["close"])


# --------------------------------------------------------------------------- #
# 3 · 재진입 거래도 색을 받는다 (실데이터 · WAN-345 부류의 동작 가드)
# --------------------------------------------------------------------------- #


def test_reentry_candidates_actually_get_a_color() -> None:
    """🚨 인자를 넘기는 줄이 아니라 **재진입 후보에 색이 붙었는지**로 건다.

    채택 북은 재진입 ON이라(WAN-273) 여기가 빠지면 색 표가 거래의 상당 부분을 조용히
    「워밍업」으로 흘린다 — WAN-345가 래더 축에서 겪은 실패의 관측 판이다.
    """
    _skip_without_real_data()
    payloads = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_macd=True, **_shared_kwargs())
    reentry = payloads[0].reentry_candidates[harness.SEGMENT_FULL]
    assert reentry, "재진입 후보가 없어 이 검사가 성립하지 않습니다(창을 넓히세요)."
    assert all(c.macd_hist is not None for c in reentry), "재진입 거래에 색이 안 붙었습니다."


def test_book_segment_reports_no_unlabeled_reentry() -> None:
    """검산 (c)를 실데이터에서 그대로 돌린다 — 색 없는 재진입 거래가 0건이어야 한다."""
    _skip_without_real_data()
    payloads = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_macd=True, **_shared_kwargs())
    proxied, _note = apply_funding_proxy(payloads)
    book = iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=[harness.SEGMENT_FULL],
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        include_reentry=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )
    assert any(p.is_reentry for _t, p in book[0].trades_with_placements())
    assert unlabeled_reentries(book) == 0


# --------------------------------------------------------------------------- #
# 4 · 색 귀속은 전수다 (실데이터)
# --------------------------------------------------------------------------- #


def test_color_attribution_is_exhaustive() -> None:
    """색별 거래 수의 합 == 그 구간 거래 수. 어느 거래도 표에서 사라지지 않는다."""
    _skip_without_real_data()
    payloads = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_macd=True, **_shared_kwargs())
    proxied, _note = apply_funding_proxy(payloads)
    book = iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=[harness.SEGMENT_FULL],
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        include_reentry=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )
    frame = pd.DataFrame([r.model_dump() for r in rows_for_segment(book[0])])
    assert not frame.empty
    assert coverage_gap(book, frame) == 0.0
    # TF 축도 전수다 — 이 실행은 한 TF뿐이라 전체 축과 같은 수가 나와야 한다.
    overall = frame[frame["axis"] == AXIS_OVERALL]["num_trades"].sum()
    by_tf = frame[frame["axis"] != AXIS_OVERALL]["num_trades"].sum()
    assert int(overall) == int(by_tf) == len(book[0].outcome.trades)


def test_warmup_is_not_absorbed_into_a_color() -> None:
    """색을 판정 못 한 거래는 「워밍업」으로 **보인다** — 어느 색으로 흡수하면 분포가 거짓말이다."""
    warm = PlacedSetup(cell=("x", "1h"), equity=0.0, risk_amount=1.0, realized_pnl=0.0)
    assert placement_color(warm) is None
    assert placement_bucket(warm) == WARMUP_LABEL
    assert macd_color_label(warm) == WARMUP_LABEL
    assert BUCKET_ORDER[-1] == WARMUP_LABEL
    assert WARMUP_LABEL not in {c.label for c in MacdColor}

    red = PlacedSetup(
        cell=("x", "1h"),
        equity=0.0,
        risk_amount=1.0,
        realized_pnl=0.0,
        macd_hist=-2.0,
        macd_hist_prev=-1.0,
    )
    assert placement_color(red) is MacdColor.STRONG_RED
    assert macd_color_label(red) == MacdColor.STRONG_RED.label


# --------------------------------------------------------------------------- #
# 5 · 판정이 지어지지 않는다
# --------------------------------------------------------------------------- #


def _row(segment: str, color: str, *, trades: int, mean_net_r: float) -> dict[str, Any]:
    return ColorRow(
        segment=segment,
        axis=AXIS_OVERALL,
        bucket="전체",
        color=color,
        num_trades=trades,
        share=0.25,
        win_rate=0.5,
        mean_net_r=mean_net_r,
        sum_net_r=mean_net_r * trades,
        sample_ok=trades >= MIN_TRADES,
    ).model_dump()


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_a_thin_color_never_becomes_the_best() -> None:
    """표본 미달 색의 극단값이 「최선 색」으로 올라오면 판정이 통째로 흔들린다."""
    rows = [
        _row("is", "진한 빨강", trades=200, mean_net_r=-0.10),
        _row("is", "연한 초록", trades=3, mean_net_r=+9.00),  # 얇은 조각의 극단값
        _row("oos_warm", "진한 빨강", trades=200, mean_net_r=-0.12),
        _row("oos_warm", "연한 초록", trades=3, mean_net_r=+9.00),
    ]
    first, second = verdict(_frame(rows))
    assert "진한 빨강" in first
    assert "연한 초록" not in first and "연한 초록" not in second
    assert "근거 없음" in gate_decision(_frame(rows))


def test_a_flipped_winner_closes_the_gate() -> None:
    """앞구간 승자가 뒷구간에서 뒤집히면 **색이 아니라 구간 우연**이다(WAN-161 부류)."""
    rows = [
        _row("is", "진한 초록", trades=100, mean_net_r=+0.20),
        _row("is", "진한 빨강", trades=100, mean_net_r=-0.20),
        _row("oos_warm", "진한 초록", trades=100, mean_net_r=-0.20),
        _row("oos_warm", "진한 빨강", trades=100, mean_net_r=+0.20),
    ]
    _first, second = verdict(_frame(rows))
    assert "뒤집혔다" in second
    decision = gate_decision(_frame(rows))
    assert "근거 없음" in decision and "다름" in decision


def test_a_gap_inside_the_noise_band_closes_the_gate() -> None:
    """격차가 ±0.005R 안이면 0과 구분되지 않는다 — 승자가 유지돼도 게이트는 닫힌다."""
    rows = [
        _row("is", "진한 초록", trades=100, mean_net_r=-0.1300),
        _row("is", "진한 빨강", trades=100, mean_net_r=-0.1320),
        _row("oos_warm", "진한 초록", trades=100, mean_net_r=-0.1300),
        _row("oos_warm", "진한 빨강", trades=100, mean_net_r=-0.1320),
    ]
    first, _second = verdict(_frame(rows))
    assert "아니오" in first
    decision = gate_decision(_frame(rows))
    assert "근거 없음" in decision and f"±{NOISE_R}R" in decision


def test_both_conditions_met_opens_a_follow_up_but_demands_the_book() -> None:
    """둘 다 만족해도 「필터를 켜라」가 아니라 **북에서 다시 재라 · 사용자 결정**이다."""
    rows = [
        _row("is", "진한 초록", trades=100, mean_net_r=+0.05),
        _row("is", "진한 빨강", trades=100, mean_net_r=-0.20),
        _row("oos_warm", "진한 초록", trades=100, mean_net_r=+0.04),
        _row("oos_warm", "진한 빨강", trades=100, mean_net_r=-0.18),
    ]
    decision = gate_decision(_frame(rows))
    assert "두 조건을 다 만족" in decision
    assert "북에서 다시" in decision and "사용자 결정" in decision


def test_a_single_surviving_color_is_reported_as_no_selectivity() -> None:
    """분포가 한 색에 쏠리면 「색으로 걸러 봐야 아무것도 안 걸린다」가 답이다."""
    rows = [
        _row("is", "진한 빨강", trades=300, mean_net_r=-0.13),
        _row("oos_warm", "진한 빨강", trades=300, mean_net_r=-0.13),
    ]
    first, _second = verdict(_frame(rows))
    assert "하나뿐" in first
    assert "근거 없음" in gate_decision(_frame(rows))


def test_summary_renders_without_checks() -> None:
    """`--from-csv`(검산값 없음)에서도 요약이 지어내지 않고 그 사실을 밝힌다."""
    rows = [
        _row("is", "진한 빨강", trades=100, mean_net_r=-0.13),
        _row("oos_warm", "진한 빨강", trades=100, mean_net_r=-0.13),
    ]
    text = render_summary(_frame(rows))
    assert "검산값은 실행 시점 로그" in text
    assert "네 색 중 하나는 반드시 좋아 보인다" in text


# --------------------------------------------------------------------------- #
# 6 · 거래별 표의 색 열은 관측을 켠 실행에만 붙는다
# --------------------------------------------------------------------------- #


def test_trade_frame_gains_macd_columns_only_when_observed() -> None:
    _skip_without_real_data()
    start_ms, end_ms = parse_date_ms(_REAL_START), parse_date_ms(_REAL_END)

    def frame_for(observe: bool) -> pd.DataFrame:
        payloads = run_cells([_REAL_SYMBOL], [_REAL_TF], observe_macd=observe, **_shared_kwargs())
        proxied, _note = apply_funding_proxy(payloads)
        segment = iter_book_segments(
            proxied,
            book=LeverageBookParams(),
            segments=[harness.SEGMENT_FULL],
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
            take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        )[0]
        return book_trades_to_display_frame(segment)

    plain, observed = frame_for(observe=False), frame_for(observe=True)
    assert not any(col in plain.columns for col in BOOK_MACD_COLUMNS)
    assert all(col in observed.columns for col in BOOK_MACD_COLUMNS)
    # 색 열은 판정기가 내는 이름 그대로다(사본 규칙을 만들지 않는다).
    allowed = {c.label for c in MacdColor} | {WARMUP_LABEL}
    assert set(observed[COL_MACD_COLOR]) <= allowed
    # 관측 열을 뺀 나머지는 두 실행이 같다 — 열이 늘었을 뿐 값이 안 움직였다.
    pd.testing.assert_frame_equal(observed[plain.columns], plain)


# --------------------------------------------------------------------------- #
# 7 · CLI — 관측 플래그는 북 전용이고, per-cell에서 조용히 무시되지 않는다
# --------------------------------------------------------------------------- #


def test_observe_macd_flag_is_book_only(capsys: pytest.CaptureFixture[str]) -> None:
    """per-cell 경로엔 이 배선이 없다 — 조용히 무시하면 「색을 켰다」는 라벨만 남는다(WAN-95)."""
    from backtest.run import main

    assert main(["--positions", "single", "--observe-macd", "--quiet"]) == 2
    assert "북 모드 전용" in capsys.readouterr().err


def test_observe_macd_flag_reaches_the_book(monkeypatch: pytest.MonkeyPatch) -> None:
    """플래그가 실제로 북 경로까지 간다 — 파서에만 있고 배선이 없으면 조용히 꺼진 채 돈다."""
    from backtest import book_cli
    from backtest.run import main

    seen: dict[str, Any] = {}

    def fake(*_args: Any, **kwargs: Any) -> list[Any]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(book_cli, "run_book_segments", fake)
    assert main(["--positions", "book", "--observe-macd", "--quiet"]) == 0
    assert seen["observe_macd"] is True
    seen.clear()
    assert main(["--positions", "book", "--quiet"]) == 0
    assert seen["observe_macd"] is False


# --------------------------------------------------------------------------- #
# 8 · 관측은 어디서도 기본으로 켜지지 않는다 (완료기준 6)
# --------------------------------------------------------------------------- #


def test_observation_defaults_are_off_everywhere() -> None:
    """기본값이 하나라도 켜지면 「안 켜면 비트 재현」이라는 이 이슈의 계약이 깨진다.

    라벨이 아니라 **시그니처 기본값**으로 건다 — 배선이 여럿이라(후보 빌더·재진입 루프·
    칸 러너·북 진입점·CLI) 한 곳만 뒤집혀도 옛 CSV가 조용히 새 열을 얻는다.
    """
    import inspect

    from backtest import book_cli, wan169_leverage_book, wan228_reentry_census
    from backtest import run as run_module
    from backtest.zone_limit_backtest import build_zone_limit_candidates

    targets: list[Callable[..., Any]] = [
        build_zone_limit_candidates,
        wan228_reentry_census.reentry_candidates,
        wan228_reentry_census._iter_reentries,
        wan169_leverage_book.run_cells,
        wan169_leverage_book.reentry_candidates_for_window,
        book_cli.run_book,
        book_cli.run_book_segments,
    ]
    for fn in targets:
        default = inspect.signature(fn).parameters["observe_macd"].default
        assert default is False, f"{fn.__qualname__}의 observe_macd 기본값이 켜져 있습니다."
    assert wan169_leverage_book._Task.observe_macd is False
    # CLI도 마찬가지 — 플래그를 안 주면 꺼진 채로 돈다.
    assert run_module.build_parser().parse_args([]).observe_macd is False


def test_adopted_params_are_untouched() -> None:
    """완료기준 6 — 이 이슈는 전략 기본값·토대를 하나도 안 건드린다."""
    from execution.leverage import LeverageBookParams as _BookParams
    from strategy.models import ConfluenceParams as _Params

    params = _Params()
    assert params.max_zone_width_atr == 1.28
    assert params.take_profit_r == 1.5
    assert params.short_enabled is False
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"
    # MACD는 전략 파라미터가 **아니다** — 관측 모듈에만 산다(켜고 끄는 축이 아니라 자다).
    assert not hasattr(params, "macd_params")
    assert _BookParams() == LeverageBookParams()
