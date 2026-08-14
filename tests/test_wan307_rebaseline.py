"""WAN-307 재-베이스라인(채택 유니버스 9→12종목) 회귀.

라벨이 아니라 **동작**으로 고정한다(WAN-91/95/112 부류의 조용한 실패 방지):

- 새 기본값이 실제로 12종목이고, 합류 3종목(ADA·DOT·BCH)이 **WAN-304 사다리의
  12-유니버스와 정확히 같은 집합**이다(`wan300_universe_size.universe_symbols(12)` —
  ADV 동결 순서 규칙, 자유 파라미터 아님).
- 옛 9종목 좌표 스냅샷(`harness.LEGACY_NINE_SYMBOLS`)이 값째 안정하다 — 결론을 9종목
  수치에 박아 둔 리포트 재현의 근거.
- 그 리포트들이 **실제로** 9종목 좌표로 돈다: 모듈 상수 알리아스(24개)와 함수 기본
  인자(인라인 핀 3개)를 값으로 확인한다. `harness.DEFAULT_SYMBOLS`를 따라가게 두면
  다음 실행에서 조용히 12종목으로 돌아 본문과 어긋난다.
- 채택 성과 리포트(wan95)·범용 CLI는 핀 반대 방향 — 새 좌표를 따라간다
  (`test_wan182_rebaseline.py`가 시그니처·파서 산출물로 고정).

인자 없는 `backtest.run`이 12 × 4TF를 도는 것은 `test_wan182_rebaseline.py`의
`test_bare_cli_actually_runs_adopted_coordinates`가 (WAN-307 갱신판으로) 고정한다.
"""

from __future__ import annotations

import inspect

from backtest import harness

_LEGACY_NINE = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
)


def test_default_universe_is_twelve_symbols_in_freeze_order() -> None:
    """채택 유니버스 = 12종목, 순서는 「기존 9 → ADV 상위 3」 동결 순서."""
    assert harness.DEFAULT_SYMBOLS == _LEGACY_NINE + (
        "ADA/USDT:USDT",
        "DOT/USDT:USDT",
        "BCH/USDT:USDT",
    )


def test_twelve_universe_matches_wan304_ladder() -> None:
    """합류 3종목 = `wan300_universe_size.universe_symbols(12)`의 확장분과 같은 집합.

    WAN-307의 근거는 WAN-304 사다리가 잰 12-유니버스다 — 기본값이 그 사다리와 다른
    집합을 돌면 근거 표가 이 좌표를 가리키지 않게 된다. ADV 동결 순서(`CANDIDATE_SYMBOLS`
    상위 3)까지 함께 고정한다.
    """
    from backtest.wan300_universe_size import universe_symbols

    ladder_twelve = tuple(harness.normalize_symbol(s) for s in universe_symbols(12))
    assert ladder_twelve == harness.DEFAULT_SYMBOLS


def test_legacy_nine_snapshot_is_stable() -> None:
    """`LEGACY_NINE_SYMBOLS` = WAN-182~306 시절 채택 유니버스 스냅샷(값째 안정).

    이 값이 움직이면 9종목 시절 리포트(아래 핀 목록)가 결론에 박아 둔 수치의 재현이
    통째로 깨진다 — 리터럴로 고정한다(`LEGACY_SYMBOLS`(3심볼) 가드와 같은 원칙).
    """
    assert harness.LEGACY_NINE_SYMBOLS == _LEGACY_NINE


def test_nine_symbol_reports_are_pinned_via_module_constants() -> None:
    """9종목 결론 리포트의 심볼 상수가 전부 `LEGACY_NINE_SYMBOLS`다(24개 모듈).

    상수는 각 모듈의 실행 기본값(함수 기본 인자·argparse 기본값)으로 흘러가므로, 이
    값 확인이 곧 「기본 실행이 9종목 좌표를 돈다」의 확인이다. `is` 비교로 알리아스
    자체를 요구한다 — 값만 같은 사본은 다음 좌표 이동 때 또 조용히 갈라진다.
    """
    from backtest import (
        wan150_instant_death_autopsy,
        wan203_narrow_zone_selection,
        wan204_ob_extension_tp,
        wan209_death_autopsy_axes,
        wan210_reverse_rsi_filter,
        wan211_band_slope_filter,
        wan223_limit_order_census,
        wan228_reentry_census,
        wan231_reentry_null,
        wan244_capacity_cap,
        wan248_zone_position_null,
        wan254_formation_census,
        wan255_formation_null,
        wan258_breaker_null,
        wan261_reentry_book,
        wan263_reentry_selection,
        wan264_reentry_book_stress,
        wan267_reentry_decompose,
        wan269_reentry_book_band,
        wan271_reentry_book_band_stress,
        wan280_reentry_short_transition,
        wan282_resistance_short_mirror,
        wan284_resistance_short_profit_null,
        wan288_monthly_long_short,
    )

    pinned_constants = [
        wan150_instant_death_autopsy.DEFAULT_SYMBOLS,
        wan203_narrow_zone_selection.DEFAULT_SYMBOLS,
        wan204_ob_extension_tp.DEFAULT_SYMBOLS,
        wan209_death_autopsy_axes.DEFAULT_SYMBOLS,
        wan210_reverse_rsi_filter.DEFAULT_SYMBOLS,
        wan211_band_slope_filter.DEFAULT_SYMBOLS,
        wan244_capacity_cap.DEFAULT_SYMBOLS,
        wan223_limit_order_census.ALL_SYMBOLS,
        wan228_reentry_census.ALL_SYMBOLS,
        wan231_reentry_null.ALL_SYMBOLS,
        wan254_formation_census.ALL_SYMBOLS,
        wan261_reentry_book.ALL_SYMBOLS,
        wan263_reentry_selection.ALL_SYMBOLS,
        wan264_reentry_book_stress.ALL_SYMBOLS,
        wan267_reentry_decompose.ALL_SYMBOLS,
        wan269_reentry_book_band.ALL_SYMBOLS,
        wan271_reentry_book_band_stress.ALL_SYMBOLS,
        wan280_reentry_short_transition.ALL_SYMBOLS,
        wan282_resistance_short_mirror.ALL_SYMBOLS,
        wan284_resistance_short_profit_null.ALL_SYMBOLS,
        wan288_monthly_long_short.ALL_SYMBOLS,
        wan248_zone_position_null.NINE_SYMBOLS,
        wan255_formation_null.NINE_SYMBOLS,
        wan258_breaker_null.NINE_SYMBOLS,
    ]
    for constant in pinned_constants:
        assert constant is harness.LEGACY_NINE_SYMBOLS


def test_nine_symbol_reports_are_pinned_via_function_defaults() -> None:
    """인라인 핀 3개 모듈 — 실행 함수의 심볼 기본 인자가 `LEGACY_NINE_SYMBOLS`다."""
    from backtest.wan197_guard_with_filter import run_audit
    from backtest.wan206_zone_height_tp_today import run_report as wan206_run
    from backtest.wan278_stop_buffer import run_report as wan278_run

    for fn in (run_audit, wan206_run, wan278_run):
        default = inspect.signature(fn).parameters["symbols"].default
        assert default is harness.LEGACY_NINE_SYMBOLS, fn.__module__
