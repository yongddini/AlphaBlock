"""ccxt 거래소 인스턴스 생성.

레이트리밋을 켜고, 설정된 시장 유형(현물/선물)에 맞춰 바이낸스 인스턴스를
만든다. 자격 증명은 있을 때만 주입한다(공개 시세만 쓰면 불필요).

`use_testnet=True`(WAN-27)이면 `set_sandbox_mode(True)`로 바이낸스 선물
**테스트넷** 엔드포인트를 쓰고, **테스트넷 키만** 주입한다. 실계좌(mainnet)
키는 테스트넷 경로로 절대 새지 않는다(키 필드를 완전히 분리).
"""

from __future__ import annotations

import logging

import ccxt

from config.settings import Settings, get_settings

_logger = logging.getLogger(__name__)


def create_exchange(settings: Settings | None = None) -> ccxt.binance:
    """설정에 맞춘 ccxt 바이낸스 인스턴스를 반환한다.

    `market_type="future"`이면 USDⓈ-M 선물(`defaultType="future"`)로 구성한다.
    `enableRateLimit=True`로 클라이언트 측 레이트리밋을 활성화한다.
    `use_testnet=True`이면 테스트넷(sandbox) 모드로 전환하고 테스트넷 키만 쓴다.
    """
    settings = settings or get_settings()

    config: dict[str, object] = {
        "enableRateLimit": True,
        "options": {"defaultType": settings.market_type},
    }

    if settings.use_testnet:
        # 테스트넷 경로: 오직 테스트넷 키만 주입한다(실계좌 키 혼용 금지).
        if settings.has_testnet_credentials:
            config["apiKey"] = settings.testnet_api_key
            config["secret"] = settings.testnet_api_secret
        exchange = ccxt.binance(config)
        exchange.set_sandbox_mode(True)
        _logger.info("ccxt 바이낸스 테스트넷(sandbox) 모드로 생성됨 — 실계좌 자금 위험 없음.")
        return exchange

    # 메인넷 경로: 실계좌 키만 주입한다.
    if settings.has_credentials:
        config["apiKey"] = settings.binance_api_key
        config["secret"] = settings.binance_api_secret

    return ccxt.binance(config)
