"""애플리케이션 설정.

`.env` 파일 또는 환경변수에서 설정을 로드한다. API 키 등 비밀정보는
절대 코드에 하드코딩하지 않으며, `.env`는 `.gitignore`로 커밋을 막는다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from execution.risk import RiskParams
from execution.sizing import PositionSizingParams
from strategy.models import ConfluenceParams


def _default_symbols() -> list[str]:
    """수집 대상 기본 심볼 (USDT 무기한 선물)."""
    return ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]


def _default_timeframes() -> list[str]:
    """수집 대상 기본 타임프레임."""
    return ["1m", "5m", "15m", "1h", "4h", "1d"]


def _default_backfill_lookback_days() -> dict[str, int]:
    """타임프레임별 백필 룩백(일). 과도한 과거 요청을 막기 위한 기본값."""
    return {"1m": 3, "5m": 10, "15m": 30, "1h": 120, "4h": 365, "1d": 1825}


def _default_live_signal_symbols() -> list[str]:
    """실시간 시그널 러너(WAN-25)가 감시할 기본 심볼."""
    return ["BTC/USDT:USDT"]


def _default_live_signal_timeframes() -> list[str]:
    """실시간 시그널 러너(WAN-25)가 감시할 기본 타임프레임."""
    return ["1h"]


class Settings(BaseSettings):
    """환경변수/.env 기반 설정 값."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ALPHABLOCK_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # 바이낸스 API 자격 증명 (없으면 공개 데이터만 사용)
    binance_api_key: str = Field(default="")
    binance_api_secret: str = Field(default="")

    # 바이낸스 선물 테스트넷(WAN-27). 실계좌 실거래 전 안전 검증용.
    # use_testnet=True면 create_exchange가 set_sandbox_mode로 테스트넷 엔드포인트를 쓰고
    # 아래 testnet 키만 주입한다. **실계좌 키는 테스트넷 경로로 절대 새지 않는다**(별도 필드).
    use_testnet: bool = Field(default=False)
    testnet_api_key: str = Field(default="")
    testnet_api_secret: str = Field(default="")

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

    # 전략 규칙(WAN-23): 진입=오더블록+RSI, 익절=EMA/VWMA 선 도달, 손절=오더블록 무효화.
    # 기본값은 트레이딩뷰 설정과 일치. 개별 필드는 ALPHABLOCK_CONFLUENCE__<필드명>로 덮어쓴다.
    # 예: ALPHABLOCK_CONFLUENCE__RSI_OVERSOLD=25
    confluence: ConfluenceParams = Field(default_factory=ConfluenceParams)

    # 실시간 시그널 러너 + 텔레그램 알림 (WAN-25, 페이퍼 모드).
    # 텔레그램 봇 토큰/대화 ID는 비밀이므로 코드에 두지 않고 env로만 주입한다.
    # 예: ALPHABLOCK_TELEGRAM_BOT_TOKEN=123:abc, ALPHABLOCK_TELEGRAM_CHAT_ID=123456
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    # 러너가 감시할 심볼·타임프레임(수집 대상의 부분집합). 기본은 BTC 1h 하나.
    live_signal_symbols: list[str] = Field(default_factory=_default_live_signal_symbols)
    live_signal_timeframes: list[str] = Field(default_factory=_default_live_signal_timeframes)
    # 폴링 간격(초). 새 확정봉이 저장됐는지 이 주기로 확인한다.
    live_poll_interval_seconds: int = Field(default=60, ge=1)
    # 전략 재평가 시 각 시리즈에서 사용할 최근 봉 수(최장 EMA 365봉 워밍업 여유 포함).
    live_signal_lookback_bars: int = Field(default=1500, ge=1)
    # 이미 보낸 신호를 기록해 재시작 시 중복 발송을 막는 상태 파일 경로.
    live_signal_state_path: str = Field(default="data/live_signals_state.json")
    # 러너 운영 상태(하트비트·페이퍼 포지션·최근 신호)를 남겨 Health 대시보드(WAN-30)가
    # 읽는 파일 경로. 러너가 매 폴링마다 갱신한다.
    live_runtime_state_path: str = Field(default="data/live_runtime_state.json")
    # 데이터 신선도(수집 멈춤)를 stale로 볼 지연 배수. TF 주기 대비 이 배수를 넘으면 경고.
    health_stale_multiplier: float = Field(default=2.5, gt=0)

    # 상시 구동 수집기 하트비트(WAN-31). 수집기가 살아있음을 남기는 파일과, Health가
    # 생존 판정에 쓰는 기대 하트비트 간격(초). min_interval은 파일 쓰기 스로틀.
    collector_heartbeat_path: str = Field(default="data/collector_heartbeat.json")
    collector_heartbeat_interval_seconds: int = Field(default=60, ge=1)
    collector_heartbeat_min_interval_seconds: int = Field(default=5, ge=0)

    # 운영 상태(Health) 워치(WAN-32). WAN-30 판정을 주기적으로 점검해 이상 시 텔레그램
    # 경고를 보낸다. stale 판정 배수는 위 health_stale_multiplier를 공유한다.
    # 점검 주기(초): 이 간격마다 상태를 점검한다.
    health_watch_interval_seconds: int = Field(default=600, ge=1)
    # 쿨다운(초): 동일 이상은 이 시간 내 1회만 경고(플래핑 방지).
    health_watch_cooldown_seconds: int = Field(default=3600, ge=0)
    # 발효 중인 경고 상태를 남겨 재시작 시 중복/누락을 막는 상태 파일 경로.
    health_watch_state_path: str = Field(default="data/health_watch_state.json")

    # 리스크 기반 포지션 사이징(WAN-26). 손절 거리에 반비례해 수량을 역산한다.
    # 백테스트·실행이 공용으로 쓰며, 기본은 켬(risk_sizing_enabled=True).
    # 개별 필드는 ALPHABLOCK_RISK_SIZING__<필드명>로 덮어쓴다.
    # 예: ALPHABLOCK_RISK_SIZING__RISK_PER_TRADE=0.02
    risk_sizing_enabled: bool = Field(default=True)
    risk_sizing: PositionSizingParams = Field(default_factory=PositionSizingParams)

    # 실행 리스크 한도(WAN-9). 사이징(WAN-26)과 별개로 신규 진입을 차단하는 상한:
    # 최대 명목/레버리지, 동시 포지션 수, 일일 손실 서킷브레이커.
    # 개별 필드는 ALPHABLOCK_RISK_LIMITS__<필드명>로 덮어쓴다.
    # 예: ALPHABLOCK_RISK_LIMITS__DAILY_LOSS_LIMIT_FRACTION=0.03
    risk_limits: RiskParams = Field(default_factory=RiskParams)
    # 실행 엔진(페이퍼) 시작 자본. 리스크·사이징·손익 정산의 기준.
    paper_equity: float = Field(default=10_000.0, gt=0)

    # 페이퍼 성과 추적(WAN-33). 러너가 청산한 가상 거래를 paper_trades 테이블에 누적하고,
    # 손익률 계산 시 왕복 수수료를 반영한다. 백테스트 대비 패리티도 이 수수료율을 공유해
    # 둘의 손익 비용 모델을 일치시킨다. 펀딩비는 funding_enabled 수집분(WAN-16/20)을 쓴다.
    # BacktestConfig 기본값과 동일한 0.04%가 기본.
    paper_fee_rate: float = Field(default=0.0004, ge=0)

    # 페이퍼 성과 다이제스트(WAN-36). paper_trades(WAN-33)에서 한 기간의 성과 요약을
    # 만들어 텔레그램(WAN-32 경로)으로 주기적으로 보낸다(`scripts/paper_digest.py`).
    # 실제 발송은 이 값이 True이고 텔레그램이 설정된 경우에만 한다(기본 끔).
    paper_digest_enabled: bool = Field(default=False)
    # --since 미지정 시 기본 집계 창(일). 주 1회 실행이면 7이 자연스럽다.
    paper_digest_days: int = Field(default=7, ge=1)
    # 발송 요일(0=월 … 6=일)·시각(UTC 시). 스케줄러(cron/launchd)가 참고하는 값으로,
    # 스크립트 자체는 스케줄링하지 않는다. README "페이퍼 다이제스트" 절 참고.
    paper_digest_weekday: int = Field(default=0, ge=0, le=6)
    paper_digest_hour_utc: int = Field(default=0, ge=0, le=23)

    # 안전장치: 실제 주문 실행 여부. 기본은 반드시 False. live_trading=True여도
    # 실행 엔진은 실거래 브로커를 자동 생성하지 않고 명시적 주입을 요구한다(WAN-27).
    live_trading: bool = Field(default=False)

    @property
    def has_credentials(self) -> bool:
        """API 키와 시크릿이 모두 설정됐는지 여부."""
        return bool(self.binance_api_key) and bool(self.binance_api_secret)

    @property
    def has_testnet_credentials(self) -> bool:
        """테스트넷 API 키와 시크릿이 모두 설정됐는지 여부(WAN-27)."""
        return bool(self.testnet_api_key) and bool(self.testnet_api_secret)

    @property
    def telegram_configured(self) -> bool:
        """텔레그램 봇 토큰과 chat_id가 모두 설정됐는지 여부."""
        return bool(self.telegram_bot_token) and bool(self.telegram_chat_id)

    @property
    def effective_risk_sizing(self) -> PositionSizingParams | None:
        """활성화됐을 때만 사이징 파라미터를, 아니면 None을 반환한다.

        `BacktestConfig(risk_sizing=...)`에 그대로 넘길 수 있다. None이면 백테스트는
        `position_fraction` 고정 사이징으로 되돌아간다.
        """
        return self.risk_sizing if self.risk_sizing_enabled else None

    def lookback_days_for(self, timeframe: str, *, default: int = 30) -> int:
        """타임프레임의 백필 룩백(일)을 반환한다. 없으면 `default`."""
        return self.backfill_lookback_days.get(timeframe, default)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 싱글턴을 반환한다."""
    return Settings()
