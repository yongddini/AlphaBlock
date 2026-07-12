"""텔레그램 Bot API(sendMessage) 클라이언트 (WAN-25).

새 의존성 없이 표준 라이브러리(`urllib`)만으로 전송한다. 실제 HTTP 전송은
`Transport`(호출 가능 객체)로 추상화해, 테스트에서는 네트워크 없이 목(mock)
전송으로 대체한다. 네트워크 오류와 HTTP 429(rate limit)는 지수 백오프로
재시도하고, 429의 `retry_after`가 오면 그만큼 대기한다.

토큰·chat_id는 절대 코드에 두지 않고 `config.Settings`(=`ALPHABLOCK_TELEGRAM_*`
환경변수)로만 주입한다.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from config.settings import Settings

_logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10.0


class TransportError(Exception):
    """전송 계층의 (재시도 가능한) 네트워크 오류."""


@dataclass(frozen=True)
class TelegramResponse:
    """텔레그램 API 한 번의 HTTP 응답 요약."""

    ok: bool
    status_code: int
    description: str | None = None
    retry_after: float | None = None
    """429 응답이 지시한 대기 시간(초). 없으면 None."""


#: 실제 전송 함수의 시그니처. `(url, payload) -> TelegramResponse`.
#: 네트워크 오류는 `TransportError`로 올려 클라이언트가 재시도하게 한다.
Transport = Callable[[str, dict[str, Any]], TelegramResponse]


def _parse_response(status_code: int, body: bytes) -> TelegramResponse:
    """텔레그램 JSON 응답 본문을 `TelegramResponse`로 변환한다."""
    description: str | None = None
    retry_after: float | None = None
    ok = 200 <= status_code < 300
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if isinstance(payload, dict):
        ok = bool(payload.get("ok", ok))
        desc = payload.get("description")
        if isinstance(desc, str):
            description = desc
        params = payload.get("parameters")
        if isinstance(params, dict):
            raw_retry = params.get("retry_after")
            if isinstance(raw_retry, int | float):
                retry_after = float(raw_retry)
    return TelegramResponse(
        ok=ok, status_code=status_code, description=description, retry_after=retry_after
    )


def urllib_transport(url: str, payload: dict[str, Any]) -> TelegramResponse:
    """표준 라이브러리 기반 기본 전송. 네트워크 오류는 `TransportError`로 올린다."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
            return _parse_response(int(resp.status), resp.read())
    except urllib.error.HTTPError as exc:
        # 텔레그램은 4xx/429에도 JSON 본문을 준다 → 파싱해 상태로 넘긴다(재시도 판단은 클라이언트).
        body = exc.read() if hasattr(exc, "read") else b""
        return _parse_response(int(exc.code), body)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:  # 네트워크 계층 오류 → 재시도
        raise TransportError(str(exc)) from exc


class TelegramClient:
    """텔레그램 봇으로 메시지를 보내는 클라이언트.

    `transport`를 주입하면 네트워크 없이 테스트할 수 있다. `send_message`는
    성공 시 True, (재시도 소진 등) 실패 시 False를 반환하며 예외를 던지지 않아
    러너 루프가 알림 실패로 멈추지 않는다.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        transport: Transport | None = None,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token or not chat_id:
            raise ValueError("bot_token과 chat_id가 모두 필요합니다.")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._transport = transport or urllib_transport
        self._max_retries = max(0, max_retries)
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleep = sleep

    @property
    def _send_url(self) -> str:
        return f"{_API_BASE}/bot{self._bot_token}/sendMessage"

    def send_message(self, text: str, *, parse_mode: str | None = "Markdown") -> bool:
        """메시지를 전송한다. 성공 True, 최종 실패 False.

        네트워크 오류·HTTP 429·5xx는 지수 백오프로 최대 `max_retries`회 재시도한다.
        429가 `retry_after`를 주면 백오프 대신 그 시간을 존중한다. 그 외 4xx는
        재시도해도 소용없으므로 즉시 실패로 처리한다.
        """
        payload: dict[str, Any] = {"chat_id": self._chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        for attempt in range(self._max_retries + 1):
            try:
                response = self._transport(self._send_url, payload)
            except TransportError as exc:
                if attempt >= self._max_retries:
                    _logger.warning("텔레그램 전송 네트워크 오류(재시도 소진): %s", exc)
                    return False
                self._wait(self._backoff(attempt), reason=f"네트워크 오류: {exc}")
                continue

            if response.ok:
                return True

            if self._is_retryable(response) and attempt < self._max_retries:
                delay = response.retry_after
                self._wait(
                    delay if delay is not None else self._backoff(attempt),
                    reason=f"status={response.status_code} {response.description or ''}".strip(),
                )
                continue

            _logger.warning(
                "텔레그램 전송 실패: status=%s description=%s",
                response.status_code,
                response.description,
            )
            return False
        return False

    @staticmethod
    def _is_retryable(response: TelegramResponse) -> bool:
        return response.status_code == 429 or response.status_code >= 500

    def _backoff(self, attempt: int) -> float:
        return min(self._base_backoff * (2.0**attempt), self._max_backoff)

    def _wait(self, seconds: float, *, reason: str) -> None:
        _logger.info("텔레그램 전송 재시도 대기 %.1fs (%s)", seconds, reason)
        if seconds > 0:
            self._sleep(seconds)


def build_telegram_client(settings: Settings) -> TelegramClient | None:
    """설정이 갖춰졌으면 텔레그램 클라이언트를, 아니면 None(드라이런)을 반환한다.

    토큰·chat_id 는 `config.Settings`(=`ALPHABLOCK_TELEGRAM_*`)로만 주입한다.
    `data`·`live`·`scripts`·`dashboard` 등 여러 레이어에서 공용으로 쓰므로
    `common` 에 둔다(레이어 독립).
    """
    if not settings.telegram_configured:
        return None
    return TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
