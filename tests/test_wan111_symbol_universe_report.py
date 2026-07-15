"""WAN-111 리포트의 조립 로직 테스트 (실데이터 없이 합성 행으로).

리포트가 **라벨과 실제 실행이 갈라질** 자리들을 고정한다: 격자가 채택 기본값(오프셋
2bp, WAN-112)을 따라가는지(하드코딩하면 기본값이 움직일 때 이 리포트만 옛 엔진을 돈다),
심볼 평균 전에 시드를 먼저 접는지(순서를 바꾸면 leave-one-out에서 뺄 심볼이 사라진다),
판정 문장이 표의 숫자에서 실제로 계산되는지(사람이 손으로 적으면 재실행 때 문장과
숫자가 갈라진다 — WAN-95가 겪은 사고).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import FILL_PRESETS_BY_NAME, RunRow, build_params
from backtest.wan111_symbol_universe_report import (
    ALL_SYMBOLS,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_TIMEFRAMES,
    LEGACY_SYMBOLS,
    LENS_NAMES,
    NEW_SYMBOLS,
    build_grid,
    leave_one_out,
    per_symbol,
    universe_compare,
    verdict,
)


def _row(
    *,
    symbol: str,
    total_return: float,
    timeframe: str = "15m",
    segment: str = "oos",
    fill: str = "baseline",
    seed: int = 0,
    max_drawdown: float = 0.1,
) -> RunRow:
    return RunRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        window=0,
        entry_mode="zone_limit",
        take_profit_r=1.5,
        offset_bps=2.0,
        fill=fill,
        seed=seed,
        start_time=0,
        end_time=1000,
        num_bars=100,
        num_trades=10,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=max_drawdown,
        sharpe=1.0,
        profit_factor=1.2,
        mean_r=0.2,
        fill_rate=0.5,
        eligible_setups=20,
        num_filled=10,
        funding_coverage=1.0,
    )


# --------------------------------------------------------------------------- #
# 축 정의 — 다른 리포트와 같은 뜻인가
# --------------------------------------------------------------------------- #


def test_universes_are_disjoint_and_ordered() -> None:
    """전체 = 기존 + 신규이고 겹치지 않는다. 겹치면 심볼평균이 한 심볼을 두 번 센다."""
    assert set(LEGACY_SYMBOLS) & set(NEW_SYMBOLS) == set()
    assert ALL_SYMBOLS == LEGACY_SYMBOLS + NEW_SYMBOLS


def test_lens_names_exist_in_harness() -> None:
    """렌즈 이름이 harness 프리셋과 일대일이어야 이 표를 WAN-96/107/110 표와 나란히 놓는다."""
    for name in LENS_NAMES:
        assert name in FILL_PRESETS_BY_NAME
    assert LENS_NAMES[0] == "baseline", "공식 렌즈가 첫 자리여야 판정이 그것을 읽는다(토대 2)"


def test_timeframes_are_both_working_tfs() -> None:
    """WAN-107 공동 작업 TF 병기 — 한쪽만 내면 그 결정의 요구사항을 어긴다."""
    assert set(DEFAULT_TIMEFRAMES) == {"15m", "1h"}


# --------------------------------------------------------------------------- #
# 격자 — 채택 기본값을 따라가는가
# --------------------------------------------------------------------------- #


def test_grid_follows_adoption_defaults() -> None:
    """오프셋·익절 R을 하드코딩하지 않고 `ConfluenceParams()`에서 가져온다.

    2bp를 상수로 박으면 기본값이 다시 움직일 때(WAN-112 같은 재-베이스라인) 이 리포트만
    혼자 옛 엔진을 도는 조용한 갈라짐이 생긴다.
    """
    defaults = build_params()
    grid = build_grid(ALL_SYMBOLS)
    assert grid.offsets_bps == (defaults.zone_limit_offset_bps,)
    assert grid.take_profit_rs == (defaults.take_profit_r,)
    assert grid.entry_modes == ("zone_limit",), "채택 진입 방식은 존 지정가다(토대 1)"


def test_grid_normalizes_symbols() -> None:
    """축약형을 줘도 저장소 표기로 정규화된다(빈 결과로 조용히 넘어가지 않는다)."""
    grid = build_grid(("BTCUSDT", "BNBUSDT"))
    assert grid.symbols == ("BTC/USDT:USDT", "BNB/USDT:USDT")


def test_window_is_pinned_not_years() -> None:
    """창을 못 박는다 — `--years`는 심볼마다 마지막 봉이 달라 창이 어긋난다."""
    assert DEFAULT_START < DEFAULT_END
    assert pd.Timestamp(DEFAULT_START) < pd.Timestamp(DEFAULT_END)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def test_per_symbol_folds_seeds_within_symbol() -> None:
    """시드를 심볼 **안에서** 먼저 접는다 — 심볼이 살아 있어야 leave-one-out이 성립한다."""
    rows = [
        _row(symbol="BTC/USDT:USDT", total_return=0.10, fill="pen_5bp_drop_50", seed=0),
        _row(symbol="BTC/USDT:USDT", total_return=0.20, fill="pen_5bp_drop_50", seed=1),
        _row(symbol="ETH/USDT:USDT", total_return=0.40, fill="pen_5bp_drop_50", seed=0),
        _row(symbol="ETH/USDT:USDT", total_return=0.60, fill="pen_5bp_drop_50", seed=1),
    ]
    frame = per_symbol(rows).set_index("symbol")
    assert frame.loc["BTC/USDT:USDT", "total_return"] == pytest.approx(0.15)
    assert frame.loc["ETH/USDT:USDT", "total_return"] == pytest.approx(0.50)
    assert frame.loc["BTC/USDT:USDT", "seeds"] == 2
    # 시드 분산은 평균 옆에 남아야 한다(평균만 보면 개별 시드의 마이너스가 안 보인다).
    assert frame.loc["BTC/USDT:USDT", "return_min"] == 0.10
    assert frame.loc["BTC/USDT:USDT", "return_max"] == 0.20


def test_universe_compare_splits_legacy_new_all() -> None:
    """3심볼·신규 3심볼·6심볼이 같은 창에서 각각 나온다."""
    rows = [_row(symbol=s, total_return=0.1 * (i + 1)) for i, s in enumerate(ALL_SYMBOLS)]
    compare = universe_compare(per_symbol(rows)).set_index("universe")
    assert compare.loc["legacy_3", "symbols"] == 3
    assert compare.loc["new_3", "symbols"] == 3
    assert compare.loc["all_6", "symbols"] == 6
    assert compare.loc["all_6", "total_return"] == pytest.approx(0.35)
    assert compare.loc["legacy_3", "total_return"] == pytest.approx(0.2)
    assert compare.loc["all_6", "positive"] == 6


def test_universe_compare_counts_negative_symbols() -> None:
    """플러스 심볼 수가 실제 부호를 센다 — 평균이 플러스여도 심볼은 갈릴 수 있다."""
    rows = [_row(symbol=s, total_return=-0.1) for s in LEGACY_SYMBOLS]
    rows += [_row(symbol=s, total_return=0.5) for s in NEW_SYMBOLS]
    compare = universe_compare(per_symbol(rows)).set_index("universe")
    assert compare.loc["all_6", "positive"] == 3
    assert compare.loc["legacy_3", "positive"] == 0


def test_leave_one_out_delta_is_negative_for_the_carrier() -> None:
    """평균을 끌어올리던 심볼을 빼면 `delta`가 음수다(= 그 심볼이 캐리하고 있었다)."""
    rows = [_row(symbol=s, total_return=0.02) for s in ALL_SYMBOLS[:5]]
    rows.append(_row(symbol=ALL_SYMBOLS[5], total_return=1.0))
    loo = leave_one_out(per_symbol(rows)).set_index("dropped")
    carrier = ALL_SYMBOLS[5].split("/")[0]
    assert loo.loc[carrier, "delta"] < 0
    other = ALL_SYMBOLS[0].split("/")[0]
    assert loo.loc[other, "delta"] > 0
    # 5심볼 평균이므로 남은 심볼 수는 항상 5다.
    assert set(loo["symbols"]) == {5}


def test_leave_one_out_covers_every_symbol() -> None:
    rows = [_row(symbol=s, total_return=0.1) for s in ALL_SYMBOLS]
    loo = leave_one_out(per_symbol(rows))
    assert set(loo["dropped"]) == {s.split("/")[0] for s in ALL_SYMBOLS}


# --------------------------------------------------------------------------- #
# 판정 — 숫자에서 계산되는가
# --------------------------------------------------------------------------- #


def test_verdict_reads_official_lens_only() -> None:
    """판정은 `baseline`(공식)만 읽는다 — 민감도 렌즈가 판정을 흔들면 토대 2 위반이다."""
    rows = [_row(symbol=s, total_return=0.1, fill="baseline") for s in ALL_SYMBOLS]
    rows += [_row(symbol=s, total_return=-0.9, fill="pen_5bp") for s in ALL_SYMBOLS]
    frame = per_symbol(rows)
    lines = verdict(universe_compare(frame), leave_one_out(frame), segment="oos")
    text = "\n".join(lines)
    assert "+10.00%" in text
    assert "-90.00%" not in text


def test_verdict_reports_dilution_when_new_symbols_are_worse() -> None:
    """신규 심볼이 나쁘면 6심볼 평균이 3심볼보다 낮게 **계산**된다(문장이 숫자를 따라간다)."""
    rows = [_row(symbol=s, total_return=1.0) for s in LEGACY_SYMBOLS]
    rows += [_row(symbol=s, total_return=0.0) for s in NEW_SYMBOLS]
    frame = per_symbol(rows)
    lines = verdict(universe_compare(frame), leave_one_out(frame), segment="oos")
    text = "\n".join(lines)
    assert "3심볼 +100.00% → 6심볼 +50.00%" in text
    assert "-50.00%p" in text


def test_verdict_flags_leave_one_out_going_negative() -> None:
    """한 심볼을 빼면 마이너스가 되는 경우를 판정이 **잡아낸다**(숨기지 않는다)."""
    rows = [_row(symbol=s, total_return=-0.1) for s in ALL_SYMBOLS[:5]]
    rows.append(_row(symbol=ALL_SYMBOLS[5], total_return=1.0))
    frame = per_symbol(rows)
    lines = verdict(universe_compare(frame), leave_one_out(frame), segment="oos")
    text = "\n".join(lines)
    assert "마이너스로 내려가는 경우가 있다" in text
    assert ALL_SYMBOLS[5].split("/")[0] in text


def test_verdict_covers_both_working_tfs() -> None:
    rows = [
        _row(symbol=s, total_return=0.1, timeframe=tf)
        for s in ALL_SYMBOLS
        for tf in DEFAULT_TIMEFRAMES
    ]
    frame = per_symbol(rows)
    lines = verdict(universe_compare(frame), leave_one_out(frame), segment="oos")
    text = "\n".join(lines)
    assert "15m oos" in text
    assert "1h oos" in text
