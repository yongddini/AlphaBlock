"""애플리케이션 설정.

`.env` 파일 또는 환경변수에서 설정을 로드한다. API 키 등 비밀정보는
절대 코드에 하드코딩하지 않으며, `.env`는 `.gitignore`로 커밋을 막는다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.costs import CostModel
from execution.risk import RiskParams
from execution.sizing import PositionSizingParams
from strategy.models import ConfluenceParams


def _default_symbols() -> list[str]:
    """수집 대상 기본 심볼 (USDT 무기한 선물).

    WAN-111에서 BNB·XRP·TRX를 더해 6심볼, **WAN-182(= WAN-179 결정)에서 DOGE·LINK·LTC를
    더해 9종목**이 됐다. **수집 대상**일 뿐 실거래·실시간 시그널 대상이 아니다(그쪽은
    `live_signal_symbols`, 기본 BTC 단독 — 유니버스 확장은 측정·수집 대상이지 실거래
    승인이 아니다).

    신규 심볼을 여기에 올리지 않으면 수집기가 기존 심볼만 갱신해 **신규 심볼만 낡는다** —
    그러면 다음 격자에서 9종목이 서로 다른 창을 보게 되고, 심볼 편중을 가르려던 표에
    기간 차이가 섞인다.

    ⚠️ 신규 3종목(DOGE·LINK·LTC)은 **펀딩 데이터가 아직 0행**이다(WAN-178 백필 전) —
    수집기가 앞으로의 펀딩은 쌓지만 과거분은 백필해야 한다. 그전까지 백테스트는 대리
    (BTC 시계열, WAN-180 규칙) 또는 펀딩 미반영으로 돈다.
    """
    return [
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
    # 저장분이 없을 때 이 날짜(ISO, 예 "2023-07-01")부터 펀딩을 백필한다. 설정하면
    # 룩백일수 대신 우선한다 — 백테스트 구간 전체를 커버하려면 이 값을 쓴다(WAN-63).
    # None이면 기존 룩백(30일) 동작을 유지한다.
    funding_backfill_start: str | None = Field(default=None)

    # 백테스트 손익에 펀딩비를 반영할지(WAN-91). 위 `funding_enabled`(수집 on/off)와
    # 별개 개념이다 — 이 값은 `BacktestConfig.funding_enabled`에 실린다
    # (`risk_sizing_enabled`와 동일 패턴, `default_backtest_config`가 배선).
    # 기본 True: 무기한선물은 보통 펀딩비가 손익에 실제로 걸리므로, 반영을 기본으로
    # 켜서 호출부가 깜빡 빠뜨려도 조용히 비용이 누락되지 않게 한다(WAN-65의 risk_sizing
    # 조용한 실패와 같은 유형). 단, 이 플래그만으로는 아무 것도 바뀌지 않는다 — 실제
    # 펀딩비 반영에는 호출부가 `data.FundingRateStore.get_rates(symbol, ...)`로 조회한
    # `funding_rates`를 `evaluate`/`run_backtest`에 별도로 전달해야 한다(심볼별 데이터라
    # `default_backtest_config`가 알 수 없다). 전달하지 않으면 `funding_missing_policy`에
    # 따라 커버리지 0%로 명시적으로 드러난다(비용을 0으로 채우고 "반영했다"고 하지 않음).
    backtest_funding_enabled: bool = Field(default=True)
    # 펀딩비 데이터가 없는 구간의 정책. "zero"=0으로 채우고 진행(커버리지는 낮게
    # 보고됨), "error"=중단. 기본은 기존 리포트들을 깨지 않도록 "zero".
    backtest_funding_missing_policy: Literal["zero", "error"] = Field(default="zero")

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

    # OHLCV 갭 자동 탐지·복구 백필(WAN-35). 저장된 시리즈의 내부 누락 봉을 찾아 그
    # 구간만 재수집한다. 수집 데몬(`alphablock collect`) 시작 시 1회 자동 점검을 켜는
    # 기본값(repair_on_start=True)과, 마지막 복구 요약을 남겨 Health 뷰가 읽는 파일 경로.
    repair_on_start: bool = Field(default=True)
    repair_state_path: str = Field(default="data/repair_state.json")

    # 운영 상태(Health) 워치(WAN-32). WAN-30 판정을 주기적으로 점검해 이상 시 텔레그램
    # 경고를 보낸다. stale 판정 배수는 위 health_stale_multiplier를 공유한다.
    # 점검 주기(초): 이 간격마다 상태를 점검한다.
    health_watch_interval_seconds: int = Field(default=600, ge=1)
    # 쿨다운(초): 동일 이상은 이 시간 내 1회만 경고(플래핑 방지).
    health_watch_cooldown_seconds: int = Field(default=3600, ge=0)
    # 발효 중인 경고 상태를 남겨 재시작 시 중복/누락을 막는 상태 파일 경로.
    health_watch_state_path: str = Field(default="data/health_watch_state.json")

    # 대시보드 상시 구동·자동 새로고침(WAN-48). launchd 데몬으로 상주시켜 터미널
    # 없이 북마크(http://localhost:<port>)로 확인한다. port는 설치 스크립트가
    # 읽어 plist에 싣고, refresh_seconds는 대시보드가 읽어 운영 상태 탭을 주기적으로
    # 자동 갱신한다(0이면 자동 갱신 끔). 로컬(127.0.0.1) 바인딩만 하며 외부 노출은 안 한다.
    dashboard_port: int = Field(default=8501, ge=1, le=65535)
    dashboard_refresh_seconds: int = Field(default=60, ge=0)

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

    # 체결 비용 모델(WAN-37). 메이커/테이커 수수료 + 슬리피지의 **단일 소스**로,
    # 백테스트와 페이퍼가 같은 값을 공유해 패리티 비교를 유의미하게 한다(paper.parity·
    # live.runner 가 이 값을 읽어 손익 비용을 산정한다). 펀딩비는 funding_enabled
    # 수집분(WAN-16/20)을 별도로 가감한다. 개별 필드는 ALPHABLOCK_COSTS__<필드명>로
    # 덮어쓴다. 예: ALPHABLOCK_COSTS__TAKER_FEE_RATE=0.0005
    costs: CostModel = Field(default_factory=CostModel)

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
