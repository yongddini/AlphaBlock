"""시리즈 「꼬리 신선도」 판정 — 갭 검사가 구조적으로 못 잡는 정지 감지 (WAN-156).

`data.gaps.find_gaps`는 **저장된 봉과 봉 사이**만 본다(그 모듈 독스트링의 경계 처리
참고). 그래서 시리즈가 어느 시점부터 **통째로 멈추면** 「사이」가 없어 갭이 0으로
보고된다::

    정상:         ●●●●●●●●●●●●●● (오늘)
    구멍(잡힘):   ●●●●●●   ●●●●● (오늘)   ← find_gaps가 잡는다
    꼬리 정지:    ●●●●●●●●               ← 「사이」가 없어 갭이 아니다

실제로 WAN-156에서 BNB·XRP·TRX가 5일 멈춘 채 `repair_state.json`에 전 TF
`gaps_found: 0`으로 기록됐다. 신선도를 보는 장치는 `live.health_watch`뿐이었고 그쪽은
텔레그램 출구가 막혀 침묵했다 — **두 장치가 동시에 조용했다.**

이 모듈은 `find_gaps`를 **건드리지 않고**(내부 갭 계산은 정확하다) 복구·검증 층에
신선도 판정을 더한다. 판정 기준은 대시보드 Health(WAN-30)와 같은 자다 —
`lag > stale_multiplier × 주기`면 정지로 본다.

부수효과·네트워크 의존이 없는 순수 함수라 단위 테스트가 쉽다.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from data.gaps import find_gaps
from data.models import timeframe_to_ms

#: `config.settings.Settings.health_stale_multiplier` 기본값과 같은 자.
#: 설정을 읽을 수 있는 호출부(CLI·오케스트레이션)는 설정값을 넘기고, 순수 함수
#: 단독 사용처는 이 기본값을 쓴다.
DEFAULT_STALE_MULTIPLIER = 2.5

#: 펀딩비 시리즈를 `StaleSeries.timeframe`에 표기할 때 쓰는 의사(pseudo) TF 라벨.
#: 실제 TF가 아니므로 `timeframe_to_ms`에 넘기지 않는다.
FUNDING_TIMEFRAME = "funding"

#: 펀딩 정산 주기(8시간). `dashboard.health.FUNDING_INTERVAL_MS`와 같은 값이다.
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000


class StaleSeries(BaseModel):
    """꼬리가 멈춘 시리즈 하나.

    `timeframe`은 OHLCV면 실제 TF(`15m` 등), 펀딩비면 `FUNDING_TIMEFRAME`이다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    last_ms: int
    """마지막으로 저장된 봉(또는 펀딩 정산)의 시각."""
    lag_ms: int
    """`now_ms - last_ms`. 정지로 판정된 이상 항상 양수다."""
    expected_interval_ms: int
    """기대 갱신 주기(TF 주기 또는 펀딩 정산 주기)."""
    threshold_ms: int
    """이 값을 넘으면 정지로 본 문턱(`stale_multiplier × 주기`)."""

    @property
    def lag_intervals(self) -> float:
        """지연이 기대 주기의 몇 배인지(사람이 읽는 요약용)."""
        return self.lag_ms / self.expected_interval_ms


def evaluate_staleness(
    symbol: str,
    timeframe: str,
    last_ms: int | None,
    *,
    now_ms: int,
    interval_ms: int,
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
) -> StaleSeries | None:
    """한 시리즈의 꼬리 신선도를 판정한다. 정상이면 None.

    `last_ms`가 None(= 저장된 봉이 하나도 없음)이면 **정지가 아니다** — 멈춘 게
    아니라 시작한 적이 없는 것이고, 복구할 꼬리가 없다. 대시보드 Health가 그런
    시리즈를 STALE이 아니라 UNKNOWN으로 두는 것과 같은 취급이다.
    """
    if last_ms is None:
        return None
    threshold = int(stale_multiplier * interval_ms)
    lag = now_ms - last_ms
    if lag <= threshold:
        return None
    return StaleSeries(
        symbol=symbol,
        timeframe=timeframe,
        last_ms=last_ms,
        lag_ms=lag,
        expected_interval_ms=interval_ms,
        threshold_ms=threshold,
    )


def find_stale_series(
    rows: Sequence[tuple[str, str, int | None]],
    *,
    now_ms: int,
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
) -> list[StaleSeries]:
    """`(symbol, timeframe, last_open_time)` 행들에서 정지 시리즈만 골라낸다.

    지원하지 않는 TF(주기를 모르는 시리즈)는 판정 대상에서 제외한다 — 문턱을
    지어내면 오탐이 되기 때문이다.
    """
    out: list[StaleSeries] = []
    for symbol, timeframe, last_ms in rows:
        try:
            interval = timeframe_to_ms(timeframe)
        except ValueError:
            continue
        stale = evaluate_staleness(
            symbol,
            timeframe,
            last_ms,
            now_ms=now_ms,
            interval_ms=interval,
            stale_multiplier=stale_multiplier,
        )
        if stale is not None:
            out.append(stale)
    return out


def find_stale_funding(
    rows: Sequence[tuple[str, int | None]],
    *,
    now_ms: int,
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
    interval_ms: int = FUNDING_INTERVAL_MS,
) -> list[StaleSeries]:
    """`(symbol, last_funding_time)` 행들에서 정지한 펀딩비 시리즈만 골라낸다.

    펀딩비는 OHLCV 백필이 채우지 않는 **별도 경로**(`data.funding`)라 같이 밀릴 수
    있고(WAN-156 §5), 비어 있으면 롱 온리 엔진의 수익률이 실제보다 좋게 나온다.
    """
    return [
        stale
        for symbol, last_ms in rows
        if (
            stale := evaluate_staleness(
                symbol,
                FUNDING_TIMEFRAME,
                last_ms,
                now_ms=now_ms,
                interval_ms=interval_ms,
                stale_multiplier=stale_multiplier,
            )
        )
        is not None
    ]


def format_stale(stale: StaleSeries) -> str:
    """정지 시리즈 한 건을 사람이 읽는 한 줄로."""
    lag_hours = stale.lag_ms / 3_600_000
    lag_text = f"{lag_hours:.1f}시간" if lag_hours < 48 else f"{lag_hours / 24:.1f}일"
    return (
        f"{stale.symbol} {stale.timeframe}: 마지막 갱신 {lag_text} 전"
        f" (기대 주기의 {stale.lag_intervals:.1f}배)"
    )


# -- 창(window) 연속성 --------------------------------------------------------


def window_gap_summary(timestamps: Sequence[int], timeframe: str) -> str | None:
    """평가 창에 내부 구멍이 있으면 사람이 읽는 요약을, 없으면 None을 반환한다.

    `live.runner`가 `df.tail(N)`으로 가져오는 창은 **「마지막 N개 행」이지 「최근 N개
    봉 기간」이 아니다.** 구멍이 있으면 볼린저 SMA20·RSI가 멀리 떨어진 봉을 **바로
    옆 봉처럼** 계산하고, 에러도 경고도 없이 그럴듯한 숫자가 나온다(WAN-156 §3).
    하필 이 저장소에서 볼린저는 필터가 아니라 **지정가를 얼마에 걸지 정하는 값**
    (WAN-132 `intrabar_live`)이라, 조용히 틀린 가격에 주문을 걸게 된다.

    그래서 호출부는 이 함수가 요약을 돌려주면 **평가를 거부한다** — 틀린 값을
    조용히 내는 것보다 안 내는 것이 낫다.
    """
    gaps = find_gaps(timestamps, timeframe)
    if not gaps:
        return None
    missing = sum(g.missing for g in gaps)
    first = gaps[0]
    return (
        f"평가 창에 구멍 {len(gaps)}개({missing}봉)"
        f" — 첫 구멍 open_time {first.start_ms}~{first.end_ms}"
    )
