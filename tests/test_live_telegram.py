"""common.telegram 테스트 — 전송은 목 트랜스포트로 대체(네트워크 없음)."""

from __future__ import annotations

import pytest

from common.telegram import TelegramClient, TelegramResponse, TransportError, urllib_transport


class _FakeTransport:
    """큐에 담긴 응답/예외를 차례로 돌려주는 목 트랜스포트."""

    def __init__(self, items: list[TelegramResponse | Exception]) -> None:
        self._items = list(items)
        self.calls = 0
        self.payloads: list[dict[str, object]] = []

    def __call__(self, url: str, payload: dict[str, object]) -> TelegramResponse:
        self.calls += 1
        self.payloads.append(payload)
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(
    transport: _FakeTransport, sleeps: list[float], *, max_retries: int = 3
) -> TelegramClient:
    return TelegramClient(
        "token",
        "chat",
        transport=transport,
        max_retries=max_retries,
        base_backoff_seconds=1.0,
        sleep=sleeps.append,
    )


def test_requires_token_and_chat_id() -> None:
    with pytest.raises(ValueError):
        TelegramClient("", "chat")
    with pytest.raises(ValueError):
        TelegramClient("token", "")


def test_send_success_first_try() -> None:
    transport = _FakeTransport([TelegramResponse(ok=True, status_code=200)])
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("hello") is True
    assert transport.calls == 1
    assert sleeps == []  # 재시도 없음
    assert transport.payloads[0] == {"chat_id": "chat", "text": "hello", "parse_mode": "Markdown"}


def test_retry_on_429_respects_retry_after() -> None:
    transport = _FakeTransport(
        [
            TelegramResponse(
                ok=False, status_code=429, description="Too Many Requests", retry_after=2.0
            ),
            TelegramResponse(ok=True, status_code=200),
        ]
    )
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("hi") is True
    assert transport.calls == 2
    assert sleeps == [2.0]  # 백오프가 아니라 retry_after를 존중


def test_retry_on_network_error_then_success() -> None:
    transport = _FakeTransport([TransportError("boom"), TelegramResponse(ok=True, status_code=200)])
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("hi") is True
    assert transport.calls == 2
    assert sleeps == [1.0]  # base_backoff * 2**0


def test_non_retryable_4xx_fails_immediately() -> None:
    transport = _FakeTransport(
        [TelegramResponse(ok=False, status_code=400, description="Bad Request")]
    )
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("hi") is False
    assert transport.calls == 1
    assert sleeps == []


def test_retries_exhausted_returns_false() -> None:
    transport = _FakeTransport([TelegramResponse(ok=False, status_code=429) for _ in range(3)])
    sleeps: list[float] = []
    client = _client(transport, sleeps, max_retries=2)

    assert client.send_message("hi") is False
    assert transport.calls == 3  # 최초 1 + 재시도 2


def test_no_parse_mode_omits_field() -> None:
    transport = _FakeTransport([TelegramResponse(ok=True, status_code=200)])
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    client.send_message("plain", parse_mode=None)
    assert "parse_mode" not in transport.payloads[0]


# --- 서식 파싱 실패 → 평문 1회 재전송 (WAN-321 §2) ---------------------------

#: 서버 실측(2026-08-17)에서 doctor 경고를 막은 바로 그 응답.
_PARSE_ERROR = TelegramResponse(
    ok=False,
    status_code=400,
    description="Bad Request: can't parse entities: Can't find end of the entity"
    " starting at byte offset 80",
)


def test_parse_error_falls_back_to_plain_text() -> None:
    """서식이 깨져 400이 나면 **평문으로 한 번 더** 보낸다 — 경고가 실제로 도착한다."""
    transport = _FakeTransport([_PARSE_ERROR, TelegramResponse(ok=True, status_code=200)])
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("빈 장부(open_positions)") is True
    assert transport.calls == 2
    assert transport.payloads[0]["parse_mode"] == "Markdown"
    assert "parse_mode" not in transport.payloads[1]  # 두 번째는 평문이다.
    assert transport.payloads[1]["text"] == "빈 장부(open_positions)"  # 본문은 안 깎는다.
    assert sleeps == []  # 파싱 실패는 백오프 대상이 아니다(기다려도 안 낫는다).


def test_plain_text_fallback_happens_at_most_once() -> None:
    """평문 재전송이 또 실패해도 **다시 시도하지 않는다** — 루프가 되면 안 된다."""
    transport = _FakeTransport([_PARSE_ERROR, _PARSE_ERROR])
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("hi") is False
    assert transport.calls == 2


def test_plain_text_send_does_not_retry_on_parse_error() -> None:
    """호출부가 이미 평문을 줬으면 되돌릴 서식이 없다 — 재전송하지 않는다."""
    transport = _FakeTransport([_PARSE_ERROR])
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("hi", parse_mode=None) is False
    assert transport.calls == 1


def test_other_400_does_not_trigger_fallback() -> None:
    """서식과 무관한 400(예: chat_id 오류)은 평문으로 다시 보내도 소용없다."""
    transport = _FakeTransport(
        [TelegramResponse(ok=False, status_code=400, description="Bad Request: chat not found")]
    )
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    assert client.send_message("hi") is False
    assert transport.calls == 1


def test_network_exhaustion_does_not_trigger_fallback() -> None:
    """API가 답한 적이 없으면 서식 문제인지 알 수 없다 — 평문 재전송을 시도하지 않는다."""
    transport = _FakeTransport([TransportError("boom") for _ in range(2)])
    sleeps: list[float] = []
    client = _client(transport, sleeps, max_retries=1)

    assert client.send_message("hi") is False
    assert transport.calls == 2  # 최초 1 + 재시도 1, 그리고 끝.


def test_send_failure_is_logged_at_error(caplog: pytest.LogCaptureFixture) -> None:
    """전송 실패는 ERROR로 남는다 — 「경보를 못 보냈다」가 조용히 묻히면 안 된다."""
    transport = _FakeTransport(
        [TelegramResponse(ok=False, status_code=400, description="Bad Request: chat not found")]
    )
    sleeps: list[float] = []
    client = _client(transport, sleeps)

    with caplog.at_level("ERROR"):
        assert client.send_message("hi") is False

    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_urllib_transport_is_default() -> None:
    """트랜스포트 미지정 시 표준 라이브러리 전송을 기본값으로 쓴다."""
    client = TelegramClient("token", "chat")
    assert client._transport is urllib_transport
