"""config.settings 에 대한 테스트.

`model_validate` 로 검증하여 환경변수/.env 로딩과 무관하게 결정론적으로 테스트한다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import Settings, get_settings


def test_defaults_are_safe() -> None:
    """기본값은 안전(실거래 비활성, 자격증명 없음)해야 한다."""
    s = Settings.model_validate({})
    assert s.live_trading is False
    assert s.market_type == "spot"
    assert s.symbol == "BTC/USDT"
    assert s.timeframe == "1h"
    assert s.has_credentials is False


def test_has_credentials_true_when_both_set() -> None:
    s = Settings.model_validate({"binance_api_key": "k", "binance_api_secret": "v"})
    assert s.has_credentials is True


def test_has_credentials_false_when_partial() -> None:
    s = Settings.model_validate({"binance_api_key": "k"})
    assert s.has_credentials is False


def test_invalid_market_type_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"market_type": "invalid"})


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
