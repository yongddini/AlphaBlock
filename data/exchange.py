"""ccxt 거래소 인스턴스 생성.

레이트리밋을 켜고, 설정된 시장 유형(현물/선물)에 맞춰 바이낸스 인스턴스를
만든다. 자격 증명은 있을 때만 주입한다(공개 시세만 쓰면 불필요).
"""

from __future__ import annotations

import ccxt

from config.settings import Settings, get_settings


def create_exchange(settings: Settings | None = None) -> ccxt.binance:
    """설정에 맞춘 ccxt 바이낸스 인스턴스를 반환한다.

    `market_type="future"`이면 USDⓈ-M 선물(`defaultType="future"`)로 구성한다.
    `enableRateLimit=True`로 클라이언트 측 레이트리밋을 활성화한다.
    """
    settings = settings or get_settings()

    config: dict[str, object] = {
        "enableRateLimit": True,
        "options": {"defaultType": settings.market_type},
    }
    if settings.has_credentials:
        config["apiKey"] = settings.binance_api_key
        config["secret"] = settings.binance_api_secret

    return ccxt.binance(config)
