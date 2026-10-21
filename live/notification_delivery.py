"""러너 알림의 **발송 결과**를 센다 — 「막힌 것」과 「보낼 게 없는 것」을 가른다 (WAN-351).

`alphablock status`의 「마지막 알림」 칸은 러너가 한 번도 채우지 않았다 — 채택 러너가
상태 파일에 `new_events=[]`를 고정으로 넘겨, 화면의 `—`는 「알림이 없었다」가 아니라
「이 칸은 원래 안 채워진다」였다. 그래서 알림이 안 올 때 **경로가 막힌 것인지**(텔레그램
미설정 = 드라이런 · 전송 실패) **보낼 게 없던 것인지**(조용한 날) 가릴 수 없었다.

이 모듈이 러너의 모든 텔레그램 발송(팔 체결·청산·일일 요약 = `ZoneLimitNotifier`, 엔진 C
리밸런싱·「💰 지갑 요약」 = `TsmomRebalancer`)을 **한 곳에서** 보낸다. 그리고:

* **성공한 시각만** 「마지막 알림」으로 센다 — 시도한 시각이 아니다(드라이런·실패는 안 센다).
* **드라이런**(텔레그램 미설정)과 **꺼짐**(`paper_trade_notify_enabled=false`)을 모드로 남긴다.
* **마지막 성공 이후 실패 횟수**를 센다 — 0이 아니면 「보낼 게 있었는데 못 보냈다」다.

⚠️ 「/잔고」 답(`live.telegram_commands`)은 세지 않는다 — 사용자가 물을 때만 나가는 응답이라
「러너가 알아서 보내는 알림 경로」의 지표가 아니고, 별도 데몬 스레드라 공유 상태를 피한다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

_logger = logging.getLogger(__name__)

NotificationMode = Literal["telegram", "dry_run", "disabled"]
"""알림 경로의 모드.

* `telegram` — 텔레그램 설정됨, 실제로 보낸다.
* `dry_run` — 알림은 켜져 있는데 텔레그램이 미설정이라 **로그로만** 간다.
* `disabled` — `paper_trade_notify_enabled=false`로 알림을 일부러 껐다.
"""


class _Sender(Protocol):
    def send_message(self, text: str, *, parse_mode: str | None = ...) -> bool: ...


class DeliveryStatus(BaseModel):
    """발송 추적기의 현재 스냅샷(러너가 상태 파일에 싣는다)."""

    model_config = ConfigDict(frozen=True)

    mode: NotificationMode
    last_success_ms: int | None = None
    """마지막으로 **전송에 성공한** 벽시계 시각. 한 번도 없으면 None."""
    last_failure_ms: int | None = None
    """마지막으로 전송에 실패한 벽시계 시각."""
    failures_since_success: int = 0
    """마지막 성공 이후 실패한 전송 수 — 0이면 경로가 막히지 않았다."""


#: `send_message`에 `parse_mode`를 넘기지 않는 기본값 표식(텔레그램 클라이언트 기본 = Markdown).
_DEFAULT = object()


class NotificationDelivery:
    """러너 알림 발송을 감싸 결과를 센다(단일 스레드 = 러너 루프 전용).

    `telegram`이 None이면 드라이런이다 — 메시지를 로그로만 남기고 False를 돌려준다.
    `enabled=False`면 모드만 `disabled`로 보고한다(호출부가 애초에 알림기를 안 만든다).
    """

    def __init__(
        self,
        telegram: _Sender | None,
        *,
        enabled: bool = True,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._telegram = telegram
        self._enabled = enabled
        self._now_ms = now_ms
        self._last_success_ms: int | None = None
        self._last_failure_ms: int | None = None
        self._failures_since_success = 0

    @property
    def mode(self) -> NotificationMode:
        if not self._enabled:
            return "disabled"
        return "telegram" if self._telegram is not None else "dry_run"

    def restore(self, previous: DeliveryStatus | None) -> None:
        """재시작 직후 이전 세션의 기록을 이어받는다(성공 시각·미해소 실패).

        재시작했다고 「마지막 알림」이 비거나 쌓인 실패가 0으로 지워지면, 막힌 경로가
        재시작 한 번으로 정상처럼 보인다.
        """
        if previous is None:
            return
        self._last_success_ms = previous.last_success_ms
        self._last_failure_ms = previous.last_failure_ms
        self._failures_since_success = previous.failures_since_success

    def send(self, text: str, *, parse_mode: object = _DEFAULT) -> bool:
        """메시지를 보낸다. 실제 전송 성공만 True — 드라이런·실패·예외는 False."""
        if self._telegram is None:
            _logger.info("[드라이런] 텔레그램 미설정 — 알림:\n%s", text)
            return False
        try:
            if parse_mode is _DEFAULT:
                ok = bool(self._telegram.send_message(text))
            else:
                assert parse_mode is None or isinstance(parse_mode, str)
                ok = bool(self._telegram.send_message(text, parse_mode=parse_mode))
        except Exception:  # noqa: BLE001 — 알림 실패가 매매 루프를 멈추지 않는다.
            _logger.exception("알림 전송 중 예외")
            ok = False
        now = self._now_ms()
        if ok:
            self._last_success_ms = now
            self._failures_since_success = 0
        else:
            self._last_failure_ms = now
            self._failures_since_success += 1
        return ok

    def status(self) -> DeliveryStatus:
        return DeliveryStatus(
            mode=self.mode,
            last_success_ms=self._last_success_ms,
            last_failure_ms=self._last_failure_ms,
            failures_since_success=self._failures_since_success,
        )
