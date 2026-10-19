"""텔레그램 「/잔고」 언제든 조회 — `getUpdates` 롱폴링 · 읽기 전용 (WAN-457 §2).

사용자 결정(2026-10-19): *「텔레그램을 통해서 잔고 현황을 볼 수 있게 해줘」*. `/잔고`(또는
`/balance`)를 보내면 하루 요약(§1)과 같은 포맷으로 **그 시각 기준** 지갑 현황을 답한다
(`paper.wallet_summary.build_balance_reply`).

## 어디서 도나 — 페이퍼 러너 안의 데몬 스레드

* 별도 systemd 유닛을 두지 않는다. 서버는 다른 앱과 메모리를 나눠 쓰는 작은 박스라(파이썬
  프로세스 하나가 pandas만 올려도 수십 MB) 프로세스를 늘리는 것보다 이미 떠 있는 러너에 스레드
  하나를 얹는 쪽이 싸다. 러너는 단일 인스턴스라 **같은 봇 토큰으로 `getUpdates`를 읽는 곳이 하나**로
  유지된다(둘이면 업데이트를 서로 뺏는다 — 텔레그램은 409 Conflict를 준다).
* 스레드는 매매 루프와 **아무것도 공유하지 않는다** — 지갑 현황은 DB를 `mode=ro`로 새로 읽어
  만든다(러너의 메모리 객체를 다른 스레드에서 만지지 않는다). 그래서 폴링·조회·전송이 실패해도
  매매 루프는 그 사실을 모른다. 스레드는 어떤 예외도 밖으로 내보내지 않고 백오프 후 계속한다.
* 웹훅은 쓰지 않는다(서버에 열린 포트가 필요하다).

## 보안

설정된 `ALPHABLOCK_TELEGRAM_CHAT_ID`에서 온 메시지에만 답한다. 다른 대화방의 명령은 **답하지 않고
로그만** 남긴다. 명령은 읽기 전용 하나뿐이다 — 매매·설정을 바꾸는 명령은 만들지 않는다(금지).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

_logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"

#: 답하는 명령 — 전부 읽기 전용.
BALANCE_COMMANDS: frozenset[str] = frozenset({"/잔고", "/balance"})
#: 롱폴링 대기(초). HTTP 타임아웃은 이것보다 길어야 한다.
DEFAULT_POLL_TIMEOUT_S = 25
#: 이보다 오래된 명령은 답하지 않는다(러너가 죽어 있던 동안 쌓인 옛 「/잔고」에 뒤늦게 답하지 않게).
DEFAULT_STALE_AFTER_S = 300
#: 폴링 실패 뒤 기다리는 시간(초) — 지수 증가 · 상한.
_BACKOFF_BASE_S = 5.0
_BACKOFF_MAX_S = 300.0

Fetch = Callable[[str, dict[str, Any], float], dict[str, Any]]
"""`(url, payload, timeout초) -> 응답 JSON`. 네트워크 오류는 예외로 올린다."""


def urllib_fetch(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """표준 라이브러리 POST. HTTP 오류(4xx/5xx)도 텔레그램 JSON 본문을 돌려준다."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        return {"ok": False, "error_code": int(exc.code), "description": str(exc)}
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("텔레그램 응답이 JSON 객체가 아니다")
    return parsed


def parse_command(text: str) -> str | None:
    """메시지 첫 낱말을 명령으로 읽는다(`/balance@봇이름` 꼬리 허용). 답할 명령이 아니면 None."""
    words = text.strip().split()
    if not words:
        return None
    head = words[0].split("@", 1)[0].lower()
    return head if head in BALANCE_COMMANDS else None


class TelegramCommandPoller:
    """`getUpdates`로 「/잔고」를 받아 답한다(읽기 전용 · 허용된 대화방만)."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        reply_builder: Callable[[int], str],
        send: Callable[[str], bool],
        fetch: Fetch | None = None,
        now_ms: Callable[[], int] | None = None,
        poll_timeout_s: int = DEFAULT_POLL_TIMEOUT_S,
        stale_after_s: int = DEFAULT_STALE_AFTER_S,
    ) -> None:
        if not bot_token or not chat_id:
            raise ValueError("bot_token과 chat_id가 모두 필요합니다.")
        self._url = f"{_API_BASE}/bot{bot_token}/getUpdates"
        self._chat_id = str(chat_id).strip()
        self._reply_builder = reply_builder
        self._send = send
        self._fetch = fetch or urllib_fetch
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._poll_timeout_s = poll_timeout_s
        self._stale_after_s = stale_after_s
        self._offset: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0

    # -- 한 번 ----------------------------------------------------------------

    def poll_once(self) -> bool:
        """`getUpdates` 한 번. 성공 True · 실패 False(예외를 밖으로 내지 않는다)."""
        payload: dict[str, Any] = {
            "timeout": self._poll_timeout_s,
            "allowed_updates": ["message"],
        }
        if self._offset is not None:
            payload["offset"] = self._offset
        try:
            response = self._fetch(self._url, payload, float(self._poll_timeout_s + 10))
        except Exception as exc:  # noqa: BLE001 — 폴링 실패가 러너를 죽이지 않는다.
            _logger.warning("텔레그램 getUpdates 실패(WAN-457): %s", exc)
            return False
        if not response.get("ok"):
            code = response.get("error_code")
            hint = (
                " — 같은 봇 토큰으로 getUpdates를 읽는 프로세스가 또 있거나 웹훅이 걸려 있다"
                if code == 409
                else ""
            )
            _logger.error(
                "텔레그램 getUpdates 거부(WAN-457): code=%s %s%s",
                code,
                response.get("description"),
                hint,
            )
            return False
        result = response.get("result")
        if not isinstance(result, list):
            return True
        for update in result:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = update_id + 1
            try:
                self.handle_update(update)
            except Exception:  # noqa: BLE001 — 한 업데이트 실패가 다음을 막지 않는다.
                _logger.exception("텔레그램 명령 처리 실패(WAN-457)")
        return True

    def handle_update(self, update: dict[str, Any]) -> str | None:
        """업데이트 하나를 처리한다. 답했으면 그 답, 아니면 None."""
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        command = parse_command(str(message.get("text") or ""))
        if command is None:
            return None
        chat = message.get("chat")
        chat_id = str(chat.get("id")) if isinstance(chat, dict) else ""
        if chat_id != self._chat_id:
            # 보안: 허용 대화방이 아니면 답하지 않는다(로그만).
            _logger.warning(
                "허용되지 않은 대화방의 명령을 무시(WAN-457): chat_id=%s command=%s",
                chat_id,
                command,
            )
            return None
        now = self._now_ms()
        sent_s = message.get("date")
        if isinstance(sent_s, int) and now - sent_s * 1000 > self._stale_after_s * 1000:
            age_s = now // 1000 - sent_s
            _logger.info("오래된 명령이라 답하지 않음(WAN-457): %s (%d초 전)", command, age_s)
            return None
        try:
            reply = self._reply_builder(now)
        except Exception as exc:  # noqa: BLE001 — 조회 실패도 답은 한다(조용히 삼키지 않는다).
            _logger.exception("잔고 조회 실패(WAN-457)")
            reply = f"⚠️ 잔고 조회 실패: {exc}"
        self._send(reply)
        return reply

    # -- 상시 -----------------------------------------------------------------

    def run(self) -> None:
        """멈추라는 신호가 올 때까지 폴링한다. 어떤 예외도 밖으로 내보내지 않는다."""
        while not self._stop.is_set():
            try:
                ok = self.poll_once()
            except Exception:  # noqa: BLE001 — 최후의 그물.
                _logger.exception("텔레그램 명령 폴링 루프 오류(WAN-457)")
                ok = False
            if ok:
                self._failures = 0
                continue
            self._failures += 1
            delay = min(_BACKOFF_BASE_S * (2.0 ** (self._failures - 1)), _BACKOFF_MAX_S)
            self._stop.wait(delay)

    def start(self) -> threading.Thread:
        """데몬 스레드로 띄운다(러너가 끝나면 함께 끝난다)."""
        thread = threading.Thread(target=self.run, name="telegram-commands", daemon=True)
        thread.start()
        self._thread = thread
        return thread

    def stop(self) -> None:
        self._stop.set()
