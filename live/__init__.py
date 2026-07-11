"""실시간(폴링) 시그널 러너 + 텔레그램 알림 (페이퍼 모드, WAN-25).

WAN-23 컨플루언스 전략을 최근 저장분(WAN-6 수집)에 반복 평가해 새 진입/청산
신호가 뜨면 **텔레그램**으로 알림을 보낸다. 실제 주문은 하지 않고(페이퍼)
가상 포지션만 추적한다. 실주문 연결은 WAN-9의 몫이며 여기서는 다루지 않는다.
"""

from __future__ import annotations

from live.notifier import Notifier, SignalEvent, collect_events, format_entry, format_exit
from live.paper import ClosedTrade, PaperBook, PaperPosition
from live.runner import SignalRunner, WatermarkStore
from live.telegram import TelegramClient, TelegramResponse, TransportError

__all__ = [
    "ClosedTrade",
    "Notifier",
    "PaperBook",
    "PaperPosition",
    "SignalEvent",
    "SignalRunner",
    "TelegramClient",
    "TelegramResponse",
    "TransportError",
    "WatermarkStore",
    "collect_events",
    "format_entry",
    "format_exit",
]
