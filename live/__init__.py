"""실시간(폴링) 시그널 러너 + 텔레그램 알림 (페이퍼 모드, WAN-25).

WAN-23 컨플루언스 전략을 최근 저장분(WAN-6 수집)에 반복 평가해 새 진입/청산
신호가 뜨면 **텔레그램**으로 알림을 보낸다. 실제 주문은 하지 않고(페이퍼)
가상 포지션만 추적한다. 실주문 연결은 WAN-9의 몫이며 여기서는 다루지 않는다.

## 지연(lazy) re-export — WAN-42

이 패키지의 공개 심볼은 **PEP 562 `__getattr__`** 로 최초 접근 시점에 로딩한다.
`live/__init__.py`가 `runner`/`executor` 등 하위 모듈을 **eager 하게** 임포트하면,
`paper.store`가 `live.paper`(ClosedTrade)를 임포트하는 순간 `live/__init__` 전체가
실행되며 아직 초기화 중인 `paper.store`를 다시 끌어와 **순환 임포트**가 난다.
지연 로딩으로 `from live.paper import ClosedTrade` 한 줄이 러너·페이퍼 스토어 전체를
끌어오지 않게 해 이 고리를 끊는다. (레이어 규칙: `docs/architecture-layers.md`)

`from live import SignalRunner` 같은 기존 사용법은 그대로 동작한다.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 정적 타입 검사·IDE용 — 런타임에는 아래 __getattr__ 가 로딩한다.
    from common.telegram import TelegramClient, TelegramResponse, TransportError  # noqa: F401
    from live.notifier import (  # noqa: F401
        Notifier,
        SignalEvent,
        collect_events,
        format_entry,
        format_exit,
    )
    from live.paper import ClosedTrade, PaperBook, PaperPosition  # noqa: F401
    from live.runner import SignalRunner, WatermarkStore  # noqa: F401

#: 공개 심볼 → 실제 정의 모듈. `__getattr__` 가 최초 접근 시 해당 모듈만 임포트한다.
_LAZY_EXPORTS: dict[str, str] = {
    "TelegramClient": "common.telegram",
    "TelegramResponse": "common.telegram",
    "TransportError": "common.telegram",
    "Notifier": "live.notifier",
    "SignalEvent": "live.notifier",
    "collect_events": "live.notifier",
    "format_entry": "live.notifier",
    "format_exit": "live.notifier",
    "ClosedTrade": "live.paper",
    "PaperBook": "live.paper",
    "PaperPosition": "live.paper",
    "SignalRunner": "live.runner",
    "WatermarkStore": "live.runner",
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """공개 심볼을 최초 접근 시점에 지연 로딩한다(PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value  # 이후 접근은 캐시된 값을 바로 반환.
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
