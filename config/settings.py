"""애플리케이션 설정.

`.env` 파일 또는 환경변수에서 설정을 로드한다. API 키 등 비밀정보는
절대 코드에 하드코딩하지 않으며, `.env`는 `.gitignore`로 커밋을 막는다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_symbols() -> list[str]:
    """수집 대상 기본 심볼 (USDT 무기한 선물)."""
    return ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]


def _default_timeframes() -> list[str]:
    """수집 대상 기본 타임프레임."""
    return ["1m", "5m", "15m", "1h", "4h", "1d"]


def _default_backfill_lookback_days() -> dict[str, int]:
    """타임프레임별 백필 룩백(일). 과도한 과거 요청을 막기 위한 기본값."""
    return {"1m": 3, "5m": 10, "15m": 30, "1h": 120, "4h": 365, "1d": 1825}


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

    # 거래 시장: 현물(spot) 또는 선물(future). 본 프로젝트는 USDⓈ-M 무기한 선물이 기본.
    market_type: Literal["spot", "future"] = Field(default="future")

    # 레거시 단일 심볼 / 타임프레임 (엔트리포인트 요약 등에서 사용)
    symbol: str = Field(default="BTC/USDT:USDT")
    timeframe: str = Field(default="1h")

    # 수집 대상 심볼·타임프레임 목록 (설정으로 추가·변경 가능)
    symbols: list[str] = Field(default_factory=_default_symbols)
    timeframes: list[str] = Field(default_factory=_default_timeframes)

    # 타임프레임별 백필 룩백(일). 저장된 데이터가 없을 때 이만큼 과거부터 수집한다.
    backfill_lookback_days: dict[str, int] = Field(default_factory=_default_backfill_lookback_days)

    # 수집 데이터 SQLite 경로 (OHLCV·펀딩비 공용)
    db_path: str = Field(default="data/ohlcv.db")

    # 펀딩비(funding rate) 수집 설정
    funding_enabled: bool = Field(default=True)  # 펀딩 수집 on/off
    funding_refresh_interval_seconds: int = Field(default=300)  # 현재 펀딩비 갱신 간격(초)
    funding_backfill_lookback_days: int = Field(default=30)  # 저장분 없을 때 백필 룩백(일)

    # 안전장치: 실제 주문 실행 여부. 기본은 반드시 False. (본 이슈에서는 미사용)
    live_trading: bool = Field(default=False)

    @property
    def has_credentials(self) -> bool:
        """API 키와 시크릿이 모두 설정됐는지 여부."""
        return bool(self.binance_api_key) and bool(self.binance_api_secret)

    def lookback_days_for(self, timeframe: str, *, default: int = 30) -> int:
        """타임프레임의 백필 룩백(일)을 반환한다. 없으면 `default`."""
        return self.backfill_lookback_days.get(timeframe, default)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 싱글턴을 반환한다."""
    return Settings()
