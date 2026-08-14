"""WAN-191 실시간 페이퍼 러너 감시 대상 확대 회귀 (사용자 결정 2026-07-25).

라벨이 아니라 **동작**으로 고정한다(WAN-91/95/112 부류의 조용한 실패 방지):

- 기본 설정에서 두 러너(`live.runner`·`live.zone_limit_runner`)의 `build_series`가
  12종목 × 15m·1h·2h·4h = **48 조합**을 낸다(각 조합이 독립 시리즈 = 독립 신호 ·
  WAN-246이 2h를 추가(WAN-252 흡수), WAN-307이 유니버스를 12종목으로 확장).
- 감시 심볼 기본값이 수집 유니버스와 **정확히 일치**한다(갈라지면 감시 대상이 수집되지
  않아 조용히 낡은 데이터를 본다) — WAN-307에서 수집 유니버스가 12종목이 되면서 감시
  대상도 상속으로 함께 12종목이 됐다(여전히 페이퍼).
- 확대는 **페이퍼 전용** — 실거래 플래그(`live_trading_enabled`)는 여전히 꺼짐이다.
"""

from __future__ import annotations

from itertools import product

from config.settings import (
    Settings,
    _default_live_signal_symbols,
    _default_live_signal_timeframes,
    _default_symbols,
)
from live.runner import build_series as build_series_signal
from live.zone_limit_runner import build_series as build_series_zone_limit

_TWELVE_SYMBOLS = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
    "ADA/USDT:USDT",
    "DOT/USDT:USDT",
    "BCH/USDT:USDT",
)
_WORKING_TFS = ("15m", "1h", "2h", "4h")


def test_default_watch_symbols_match_collection_universe() -> None:
    """감시 심볼 기본값 = 수집 유니버스 12종목(항상 일치, 드리프트 금지 — WAN-307)."""
    assert _default_live_signal_symbols() == _default_symbols()
    assert tuple(_default_live_signal_symbols()) == _TWELVE_SYMBOLS


def test_default_watch_timeframes_are_working_tfs() -> None:
    """감시 TF 기본값 = 작업 TF 4개(15m·1h·2h·4h, WAN-182 + WAN-246/252 2h 승격)."""
    assert tuple(_default_live_signal_timeframes()) == _WORKING_TFS


def test_signal_runner_build_series_is_forty_eight_combos() -> None:
    """기본 설정에서 시그널 러너가 48개 독립 시리즈를 낸다(WAN-307 12종목 × 4TF)."""
    series = build_series_signal(Settings())
    assert len(series) == len(_TWELVE_SYMBOLS) * len(_WORKING_TFS) == 48
    assert set(series) == set(product(_TWELVE_SYMBOLS, _WORKING_TFS))
    # 각 조합이 정확히 한 번씩(중복 없이) — 독립 신호의 전제.
    assert len(set(series)) == len(series)


def test_zone_limit_runner_build_series_is_forty_eight_combos() -> None:
    """기본 설정에서 존-지정가 페이퍼 러너도 같은 48개 시리즈를 낸다(WAN-307)."""
    series = build_series_zone_limit(Settings())
    assert len(series) == 48
    assert set(series) == set(product(_TWELVE_SYMBOLS, _WORKING_TFS))


def test_expansion_is_paper_only_live_trading_stays_off() -> None:
    """감시 확대는 페이퍼 전용 — 실거래 플래그는 기본 꺼짐 불변."""
    assert Settings().live_trading is False
