"""WAN-191 실시간 페이퍼 러너 감시 대상 확대 회귀 (사용자 결정 2026-07-25).

라벨이 아니라 **동작**으로 고정한다(WAN-91/95/112 부류의 조용한 실패 방지):

- 감시 좌표 기본값이 12종목 × 15m·1h·2h·4h다(WAN-246이 2h를 추가(WAN-252 흡수), WAN-307이
  유니버스를 12종목으로 확장). 📌 러너가 이 좌표로 시리즈를 만들던 `build_series`는 WAN-451 옛
  규칙 정리로 없어졌다(팔은 매주 종목 목록 × 팔 TF) — 대시보드·드리프트 점검이 아직 읽는다.
- 감시 심볼 기본값이 수집 유니버스와 **정확히 일치**한다(갈라지면 감시 대상이 수집되지
  않아 조용히 낡은 데이터를 본다) — WAN-307에서 수집 유니버스가 12종목이 되면서 감시
  대상도 상속으로 함께 12종목이 됐다(여전히 페이퍼).
- 확대는 **페이퍼 전용** — 실거래 플래그(`live_trading_enabled`)는 여전히 꺼짐이다.
"""

from __future__ import annotations

from config.settings import (
    Settings,
    _default_live_signal_symbols,
    _default_live_signal_timeframes,
    _default_symbols,
)

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


def test_expansion_is_paper_only_live_trading_stays_off() -> None:
    """감시 확대는 페이퍼 전용 — 실거래 플래그는 기본 꺼짐 불변."""
    assert Settings().live_trading is False
