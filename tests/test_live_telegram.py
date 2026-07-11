"""live.telegram 테스트 — 전송은 목 트랜스포트로 대체(네트워크 없음)."""

from __future__ import annotations

import pytest

from live.telegram import TelegramClient, TelegramResponse, TransportError, urllib_transport


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


def test_urllib_transport_is_default() -> None:
    """트랜스포트 미지정 시 표준 라이브러리 전송을 기본값으로 쓴다."""
    client = TelegramClient("token", "chat")
    assert client._transport is urllib_transport
