"""data.exchange 의 ccxt 인스턴스 생성 테스트 (WAN-6 · WAN-27).

네트워크 없이 `ccxt.binance`를 가짜로 대체해, 테스트넷(sandbox) 배선과 **키 격리**를
결정론적으로 검증한다:

* use_testnet=True → set_sandbox_mode(True) 호출 + 테스트넷 키만 주입.
* 실계좌(mainnet) 키가 테스트넷 경로로 절대 새지 않음.
* use_testnet=False(기본) → sandbox 미호출 + 실계좌 키 경로.
"""

from __future__ import annotations

from typing import Any

import ccxt
import pytest

import data.exchange as exchange_mod
from config.settings import Settings


class _FakeExchange:
    """ccxt.binance 대역. 생성 config와 sandbox 호출 여부를 기록한다."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.sandbox: bool | None = None

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox = enabled


@pytest.fixture
def fake_binance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ccxt, "binance", _FakeExchange)


def test_testnet_enables_sandbox_and_uses_testnet_keys(fake_binance: None) -> None:
    settings = Settings.model_validate(
        {
            "use_testnet": True,
            "testnet_api_key": "TESTNET_KEY",
            "testnet_api_secret": "TESTNET_SECRET",
            # 실계좌 키도 채워두지만 테스트넷 경로로 새면 안 된다.
            "binance_api_key": "MAINNET_KEY",
            "binance_api_secret": "MAINNET_SECRET",
        }
    )
    ex = exchange_mod.create_exchange(settings)
    assert isinstance(ex, _FakeExchange)
    assert ex.sandbox is True
    assert ex.config["apiKey"] == "TESTNET_KEY"
    assert ex.config["secret"] == "TESTNET_SECRET"
    # 실계좌 키·시크릿이 테스트넷 config로 절대 새지 않음.
    assert "MAINNET_KEY" not in ex.config.values()
    assert "MAINNET_SECRET" not in ex.config.values()


def test_testnet_without_keys_stays_public_and_no_mainnet_leak(fake_binance: None) -> None:
    settings = Settings.model_validate(
        {
            "use_testnet": True,
            "binance_api_key": "MAINNET_KEY",
            "binance_api_secret": "MAINNET_SECRET",
        }
    )
    ex = exchange_mod.create_exchange(settings)
    assert isinstance(ex, _FakeExchange)
    assert ex.sandbox is True
    # 테스트넷 키가 없으면 공개(무자격) 인스턴스 — 실계좌 키를 끌어오지 않는다.
    assert "apiKey" not in ex.config
    assert "secret" not in ex.config


def test_mainnet_default_no_sandbox_uses_mainnet_keys(fake_binance: None) -> None:
    settings = Settings.model_validate(
        {
            "binance_api_key": "MAINNET_KEY",
            "binance_api_secret": "MAINNET_SECRET",
            "testnet_api_key": "TESTNET_KEY",
            "testnet_api_secret": "TESTNET_SECRET",
        }
    )
    ex = exchange_mod.create_exchange(settings)
    assert isinstance(ex, _FakeExchange)
    assert ex.sandbox is None  # sandbox 미호출
    assert ex.config["apiKey"] == "MAINNET_KEY"
    assert ex.config["secret"] == "MAINNET_SECRET"
    # 테스트넷 키가 메인넷 경로로 새지 않음.
    assert "TESTNET_KEY" not in ex.config.values()


def test_future_market_type_sets_default_type(fake_binance: None) -> None:
    settings = Settings.model_validate({"market_type": "future"})
    ex = exchange_mod.create_exchange(settings)
    assert isinstance(ex, _FakeExchange)
    assert ex.config["options"] == {"defaultType": "future"}
    assert ex.config["enableRateLimit"] is True
