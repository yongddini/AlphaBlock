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
    assert s.market_type == "future"
    assert s.timeframe == "1h"
    assert s.has_credentials is False


def test_data_collection_defaults() -> None:
    """데이터 수집 대상 기본값(심볼·타임프레임·룩백·DB 경로)."""
    s = Settings.model_validate({})
    # WAN-111에서 6심볼, WAN-182에서 9종목으로 확장. 수집 대상이지 실거래·실시간 시그널
    # 대상이 아니다.
    assert s.symbols == [
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
        "BNB/USDT:USDT",
        "XRP/USDT:USDT",
        "TRX/USDT:USDT",
        "DOGE/USDT:USDT",
        "LINK/USDT:USDT",
        "LTC/USDT:USDT",
    ]
    assert s.timeframes == ["1m", "5m", "15m", "1h", "4h", "1d"]
    assert s.db_path == "data/ohlcv.db"
    assert s.lookback_days_for("1m") == 3
    # 미지정 타임프레임은 default 로 폴백.
    assert s.lookback_days_for("30m", default=7) == 7


def test_symbols_override_via_dict() -> None:
    s = Settings.model_validate({"symbols": ["XRP/USDT:USDT"], "timeframes": ["15m"]})
    assert s.symbols == ["XRP/USDT:USDT"]
    assert s.timeframes == ["15m"]


def test_has_credentials_true_when_both_set() -> None:
    s = Settings.model_validate({"binance_api_key": "k", "binance_api_secret": "v"})
    assert s.has_credentials is True


def test_has_credentials_false_when_partial() -> None:
    s = Settings.model_validate({"binance_api_key": "k"})
    assert s.has_credentials is False


def test_testnet_disabled_by_default() -> None:
    """테스트넷(WAN-27)은 기본 off, 키도 비어 있어야 한다."""
    s = Settings.model_validate({})
    assert s.use_testnet is False
    assert s.has_testnet_credentials is False


def test_has_testnet_credentials_true_when_both_set() -> None:
    s = Settings.model_validate({"testnet_api_key": "tk", "testnet_api_secret": "ts"})
    assert s.has_testnet_credentials is True


def test_has_testnet_credentials_false_when_partial() -> None:
    s = Settings.model_validate({"testnet_api_key": "tk"})
    assert s.has_testnet_credentials is False


def test_testnet_and_mainnet_keys_are_independent() -> None:
    """테스트넷 키와 실계좌 키는 별도 필드로 서로 섞이지 않는다."""
    s = Settings.model_validate(
        {
            "binance_api_key": "mk",
            "binance_api_secret": "ms",
            "testnet_api_key": "tk",
            "testnet_api_secret": "ts",
        }
    )
    assert s.binance_api_key == "mk"
    assert s.testnet_api_key == "tk"
    assert s.has_credentials is True
    assert s.has_testnet_credentials is True


def test_invalid_market_type_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"market_type": "invalid"})


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_risk_sizing_enabled_by_default() -> None:
    """리스크 기반 사이징(WAN-26)은 기본 켬이며 파라미터를 반환한다."""
    s = Settings.model_validate({})
    assert s.risk_sizing_enabled is True
    assert s.risk_sizing.risk_per_trade == pytest.approx(0.01)
    assert s.effective_risk_sizing is s.risk_sizing


def test_risk_sizing_disabled_returns_none() -> None:
    s = Settings.model_validate({"risk_sizing_enabled": False})
    assert s.effective_risk_sizing is None


def test_risk_sizing_nested_override() -> None:
    s = Settings.model_validate({"risk_sizing": {"risk_per_trade": 0.02, "leverage": 5.0}})
    assert s.risk_sizing.risk_per_trade == pytest.approx(0.02)
    assert s.risk_sizing.leverage == pytest.approx(5.0)


def test_dashboard_defaults() -> None:
    """대시보드 상시 구동·자동 새로고침(WAN-48) 기본값."""
    s = Settings.model_validate({})
    assert s.dashboard_port == 8501
    assert s.dashboard_refresh_seconds == 60


def test_dashboard_refresh_can_be_disabled() -> None:
    """새로고침 주기 0은 자동 갱신 끔을 뜻하며 허용된다."""
    s = Settings.model_validate({"dashboard_refresh_seconds": 0})
    assert s.dashboard_refresh_seconds == 0


def test_dashboard_refresh_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"dashboard_refresh_seconds": -1})


@pytest.mark.parametrize("port", [0, 70000])
def test_dashboard_port_out_of_range_rejected(port: int) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"dashboard_port": port})
