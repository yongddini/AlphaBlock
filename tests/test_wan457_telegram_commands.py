"""WAN-457 §2: 텔레그램 「/잔고」 — 허용 대화방만 · 읽기 전용 · 실패해도 안 죽는다."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from config.settings import Settings
from live.telegram_commands import TelegramCommandPoller, parse_command
from live.zone_limit_runner import start_balance_commands
from paper.wallet_summary import build_balance_reply, read_latest_mark, read_wallet_snapshot
from strategy import tsmom
from tests import test_wan457_wallet_summary as ws

ALLOWED = "12345"
NOW_MS = 1_700_000_000_000


def _update(
    update_id: int, *, chat: str | int = ALLOWED, text: str = "/잔고", age_s: int = 1
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat}, "text": text, "date": NOW_MS // 1000 - age_s},
    }


class _Fake:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.payloads.append(dict(payload))
        assert url.endswith("/getUpdates")
        assert timeout > payload["timeout"]  # HTTP 타임아웃이 롱폴링보다 길다
        item = self.responses.pop(0) if self.responses else {"ok": True, "result": []}
        if isinstance(item, Exception):
            raise item
        return item


def _collector(sent: list[str]) -> Callable[[str], bool]:
    def send(text: str) -> bool:
        sent.append(text)
        return True

    return send


def _poller(fetch: _Fake, sent: list[str], reply: str = "답") -> TelegramCommandPoller:
    return TelegramCommandPoller(
        bot_token="tok",
        chat_id=ALLOWED,
        reply_builder=lambda now: f"{reply}@{now}",
        send=_collector(sent),
        fetch=fetch,
        now_ms=lambda: NOW_MS,
    )


def test_parse_command_accepts_both_names_and_bot_suffix() -> None:
    assert parse_command("/잔고") == "/잔고"
    assert parse_command("  /balance@AlphaBlockBot  ") == "/balance"
    assert parse_command("/BALANCE") == "/balance"
    assert parse_command("잔고") is None
    assert parse_command("/buy BTC") is None  # 매매 명령은 없다
    assert parse_command("") is None


def test_allowed_chat_gets_a_reply_other_chats_are_ignored() -> None:
    fetch = _Fake([{"ok": True, "result": [_update(10), _update(11, chat=999), _update(12)]}])
    sent: list[str] = []
    poller = _poller(fetch, sent)
    assert poller.poll_once()
    assert sent == [f"답@{NOW_MS}", f"답@{NOW_MS}"]  # 999는 무시
    # 다음 폴링은 처리한 마지막 다음부터(업데이트를 다시 받지 않는다).
    assert poller.poll_once()
    assert fetch.payloads[1]["offset"] == 13
    assert fetch.payloads[0]["allowed_updates"] == ["message"]


def test_non_command_and_stale_messages_get_no_reply() -> None:
    fetch = _Fake(
        [{"ok": True, "result": [_update(1, text="안녕"), _update(2, age_s=3600), _update(3)]}]
    )
    sent: list[str] = []
    assert _poller(fetch, sent).poll_once()
    assert len(sent) == 1


def test_api_failures_never_raise() -> None:
    sent: list[str] = []
    poller = _poller(
        _Fake(
            [
                OSError("네트워크 끊김"),
                {"ok": False, "error_code": 409, "description": "Conflict"},
                {"ok": True, "result": "이상한 값"},
            ]
        ),
        sent,
    )
    assert poller.poll_once() is False
    assert poller.poll_once() is False
    assert poller.poll_once() is True
    assert sent == []


def test_reply_builder_failure_still_answers_and_send_failure_is_contained() -> None:
    sent: list[str] = []

    def broken(_: int) -> str:
        raise RuntimeError("DB 잠김")

    poller = TelegramCommandPoller(
        bot_token="tok",
        chat_id=ALLOWED,
        reply_builder=broken,
        send=_collector(sent),
        fetch=_Fake([{"ok": True, "result": [_update(1)]}]),
        now_ms=lambda: NOW_MS,
    )
    assert poller.poll_once()
    assert sent and "잔고 조회 실패" in sent[0]

    def send_boom(_: str) -> bool:
        raise RuntimeError("전송 실패")

    poller2 = TelegramCommandPoller(
        bot_token="tok",
        chat_id=ALLOWED,
        reply_builder=lambda now: "x",
        send=send_boom,
        fetch=_Fake([{"ok": True, "result": [_update(1), _update(2)]}]),
        now_ms=lambda: NOW_MS,
    )
    assert poller2.poll_once()  # 업데이트 하나의 실패가 루프를 죽이지 않는다


def test_run_loop_survives_failures_and_stops() -> None:
    calls = threading.Event()

    class _Flaky:
        n = 0

        def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
            self.n += 1
            if self.n >= 2:
                calls.set()
            raise ConnectionError("down")

    poller = TelegramCommandPoller(
        bot_token="tok",
        chat_id=ALLOWED,
        reply_builder=lambda now: "x",
        send=lambda text: True,
        fetch=_Flaky(),
        now_ms=lambda: NOW_MS,
    )
    import live.telegram_commands as tc

    original = tc._BACKOFF_BASE_S
    tc._BACKOFF_BASE_S = 0.01
    try:
        thread = poller.start()
        assert calls.wait(5.0)
        assert thread.is_alive()
        poller.stop()
        thread.join(5.0)
        assert not thread.is_alive()
    finally:
        tc._BACKOFF_BASE_S = original


def test_balance_reply_matches_ledger_and_labels_engine_c_as_reference(tmp_path: Path) -> None:
    rig = ws._Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    mark = rig.ledger.marks()[-1]
    now = mark.mark_ms + 5 * 3_600_000
    for asset in tsmom.ASSETS:
        holding = rig.ledger.holding(asset)
        assert holding is not None
        ws._minute(rig.store, asset, now - 60_000, holding.price * 1.02)
    ws._minute(rig.store, ws.DOGE, now - 60_000, 0.21)

    reply = build_balance_reply(rig.db, initial_equity=ws.INITIAL, now_ms=now)
    snap = read_wallet_snapshot(rig.db, initial_equity=ws.INITIAL, at_ms=now, live_engine_c=True)
    # 「정산 기준 자본」 == 같은 시각 장부 값(실현 = 지갑 자본, 평가손익은 같은 가격).
    assert snap.realized == pytest.approx(rig.wallet.equity, abs=1e-6)
    assert f"정산 기준 자본 ${snap.equity:,.2f}" in reply
    # DOGE 가격이 마지막 평가와 같다 → 정산 기준 자본 == 그 장부 mark(엔진 C 움직임 0으로 보임).
    assert snap.equity == pytest.approx(mark.equity, abs=1e-6)
    # 엔진 C 정산 이후 움직임은 「참고」로 따로.
    unsettled = snap.engine_c_unsettled
    assert unsettled is not None and unsettled != 0.0
    holdings = [rig.ledger.holding(a) for a in tsmom.ASSETS]
    expected = 0.0
    for h in holdings:
        assert h is not None
        expected += h.qty * (h.price * 1.02 - h.price)
    assert unsettled == pytest.approx(expected)
    assert "엔진 C 정산 이후 평가손익(참고)" in reply
    assert f"지금 평가 총액(참고) ${snap.equity + unsettled:,.2f}" in reply
    assert read_latest_mark(rig.db, now) == mark
    assert "마지막 정산(" in reply


def test_runner_does_not_start_the_poller_without_telegram_or_when_disabled() -> None:
    assert start_balance_commands(Settings(telegram_bot_token="", telegram_chat_id="")) is None
    assert (
        start_balance_commands(
            Settings(telegram_bot_token="t", telegram_chat_id="1", telegram_commands_enabled=False)
        )
        is None
    )
