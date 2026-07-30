"""백테스트 공용 유틸 — 타임프레임 변환·연율화 계수·설정 팩토리 (WAN-19 / WAN-215).

원래 이 모듈은 A안(종가 진입) 성과 평가(`evaluate`)와 파라미터 스윕(`run_sweep`)을
담았으나, **A안 경로가 WAN-208/WAN-215로 제거되면서** 그 기계(evaluate·run_sweep·
Sweep* 리포트 타입·CLOSE_ENTRY_DEFAULTS)도 함께 사라졌다. 남은 것은 모든 백테스트
진입점이 공유하는 순수 유틸뿐이다:

- `timeframe_to_ms()` / `bars_per_year()` — 타임프레임 문자열 ↔ 봉 간격(ms)·연율화 계수.
- `default_backtest_config()` — 모든 진입점이 공유하는 단일 `BacktestConfig` 팩토리.

harness·live·qc·다수 리포트가 이 세 함수를 쓴다. 파라미터 스윕은 이제 범용 CLI
(`backtest.run`)가 대체한다.
"""

from __future__ import annotations

from backtest.models import BacktestConfig
from config.settings import Settings, get_settings

# --------------------------------------------------------------------------- #
# 타임프레임 → 밀리초 / 연율화 계수
# --------------------------------------------------------------------------- #

_MINUTE_MS = 60_000
_YEAR_MS = 365 * 24 * 60 * 60 * 1000

# 지원 타임프레임(분 단위)의 밀리초 길이.
_TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "3d": 4320,
    "1w": 10080,
}


def timeframe_to_ms(timeframe: str) -> int:
    """타임프레임 문자열(예: ``"1h"``)을 봉 간격(ms)으로 변환한다."""
    try:
        return _TIMEFRAME_MINUTES[timeframe] * _MINUTE_MS
    except KeyError as exc:
        supported = ", ".join(_TIMEFRAME_MINUTES)
        raise ValueError(f"지원하지 않는 타임프레임: {timeframe!r} (지원: {supported})") from exc


def bars_per_year(timeframe: str) -> float:
    """타임프레임의 연간 봉 수(샤프 연율화 계수)를 반환한다."""
    return _YEAR_MS / timeframe_to_ms(timeframe)


def default_backtest_config(
    timeframe: str | None = None, *, seed: int = 0, settings: Settings | None = None
) -> BacktestConfig:
    """모든 백테스트 진입점이 공유하는 **단일 설정 소스**(공용 팩토리, WAN-59/WAN-65).

    타임프레임을 주면 그로부터 유도한 연율화 계수(`bars_per_year`)로 샤프를
    연율화한다(`timeframe=None`이면 봉 단위 샤프를 그대로 둔다 — 타임프레임을 아직
    모르는 호출부, 예: A/B 비용 오버라이드용). `settings.effective_risk_sizing`
    (WAN-26)을 `BacktestConfig.risk_sizing`에 실어, 손절 거리에 반비례하는 리스크
    기반 사이징이 기본으로 켜지게 한다 — 이 배선이 빠지면 모든 진입이 조용히 자본
    100%를 쓰는 `position_fraction` 경로로 되돌아간다(WAN-65가 고친 조용한 실패).
    CLI(`backtest.run`)·harness·대시보드(`dashboard.app`)가 모두 이 함수(또는 이
    함수의 결과를 `model_copy`로 덮어쓴 설정)를 거쳐 `BacktestConfig`를 만든다 —
    `BacktestConfig()`를 진입점에서 직접 생성하지 않는다.

    `settings.backtest_funding_enabled`(WAN-91)를 `BacktestConfig.funding_enabled`에
    같은 패턴으로 싣는다. 이 플래그만으로는 손익이 바뀌지 않는다 — 호출부가
    `data.FundingRateStore.get_rates(symbol, ...)`로 조회한 `funding_rates`를
    백테스트 진입점에 별도로 넘겨야 실제 펀딩비가 반영된다(심볼별 데이터라 이 함수는
    심볼을 모른다). 넘기지 않으면 `funding_missing_policy`에 따라 커버리지 0%가 결과에
    명시적으로 드러난다 — 예전처럼 `funding_enabled=False`로 비용을 조용히 0 취급하는
    대신, "펀딩을 안 썼다"는 사실 자체가 보이게 한다(WAN-63).
    """
    settings = settings or get_settings()
    annualization_factor = bars_per_year(timeframe) if timeframe is not None else None
    return BacktestConfig(
        annualization_factor=annualization_factor,
        seed=seed,
        risk_sizing=settings.effective_risk_sizing,
        funding_enabled=settings.backtest_funding_enabled,
        funding_missing_policy=settings.backtest_funding_missing_policy,
    )
