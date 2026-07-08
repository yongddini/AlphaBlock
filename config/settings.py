"""애플리케이션 설정.

`.env` 파일 또는 환경변수에서 설정을 로드한다. API 키 등 비밀정보는
절대 코드에 하드코딩하지 않으며, `.env`는 `.gitignore`로 커밋을 막는다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수/.env 기반 설정 값."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ALPHABLOCK_",
        extra="ignore",
    )

    # 바이낸스 API 자격 증명 (없으면 공개 데이터만 사용)
    binance_api_key: str = Field(default="")
    binance_api_secret: str = Field(default="")

    # 거래 시장: 현물(spot) 또는 선물(future)
    market_type: Literal["spot", "future"] = Field(default="spot")

    # 기본 심볼 / 타임프레임
    symbol: str = Field(default="BTC/USDT")
    timeframe: str = Field(default="1h")

    # 안전장치: 실제 주문 실행 여부. 기본은 반드시 False.
    live_trading: bool = Field(default=False)

    @property
    def has_credentials(self) -> bool:
        """API 키와 시크릿이 모두 설정됐는지 여부."""
        return bool(self.binance_api_key) and bool(self.binance_api_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 싱글턴을 반환한다."""
    return Settings()
