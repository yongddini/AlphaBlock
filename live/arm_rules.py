"""스토캐스틱 팔의 진입·청산 규칙 — 페이퍼 러너용 (WAN-451 ③ 단계 · 정본 WAN-449).

백테스트 정본(`backtest/wan424_stoch_ob_arm.py` · `wan432` · `wan436` · `wan439` · `wan452`)이
**체결된 후보**에 체결 분 기준으로 거는 규칙을 그대로 옮긴다. 순서도 백테스트와 같다:

1. **손절폭 하한** — `(진입가 − 오더블록 손절가) / 진입가 ≥ 4%`
   (2배 **전** 폭 · 이상 포함, WAN-432/436).
2. **%K 게이트** — 그 칸 TF의 **직전 확정봉** 스토캐스틱(20,10,10) %K 선 `< 25`(엄격 · 값을 모르면
   탈락, WAN-436). 라이브 저장소에는 확정봉만 있어 `strategy.stoch.k_before_live`로 고른다.
3. **손절 2배** — `손절가 = 진입가 − 2 × (진입가 − 오더블록 손절가)`(WAN-436). 그 손절가가 0 이하
   (= 2배 손절폭 ≥ 100%)면 **진입 거부**(사용자 결정 2026-10-14 · WAN-452 §3 — 손절 주문을
   낼 수 없다).
4. **폭락 크기** — 진입 분 **직전 확정 1분봉** 기준 BTC 24h 수익률 `≤ −5%`면
   거래 크기 × 0.5(WAN-439).
   1분봉이 모자라 계산 못 하면 폭락으로 보지 않는다(정본과 같다).

청산은:

* **익절 없음**.
* **손절**은 1분봉 저가가 2배 손절가에 닿으면(진입 분 포함, 기존 `_check_exits`).
* **시간 청산(ts4)** — 그 칸 TF의 봉 4개(진입 봉 포함)를 채운 뒤, 다음 봉이 시작되기 **직전 1분봉의
  종가**에 나간다(테이커, WAN-436 `hold_end`). 봉 경계는 `floor(t / tf)` — **epoch 정렬**이라 1w는
  목요일 경계다(정본 그대로 · 결정 ③ 가). 손절이 먼저다.

⚠️ **아직 없는 것**(다음 단계): 몰림 규칙(직전 24h 신호 수 · BTC 4h 게이트 · 예산 배율 — 「진입 안 한
신호까지 세는」 추적기가 필요하다) · 매주 종목 교체. 이 모듈만 켜면 **정본보다 덜 거르는** 팔이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from strategy import stoch

MINUTE_MS = 60_000

#: 진입 거부 사유 코드(장부 `entry_reject_code`·일일 요약에 그대로 실린다).
REJECT_CODE_ARM_STOP_FLOOR = "arm_stop_floor"
REJECT_CODE_ARM_STOCH = "arm_stoch"
REJECT_CODE_ARM_STOCH_UNKNOWN = "arm_stoch_unknown"
REJECT_CODE_ARM_STOP_BELOW_ZERO = "arm_stop_below_zero"
ARM_REJECT_CODES: tuple[str, ...] = (
    REJECT_CODE_ARM_STOP_FLOOR,
    REJECT_CODE_ARM_STOCH,
    REJECT_CODE_ARM_STOCH_UNKNOWN,
    REJECT_CODE_ARM_STOP_BELOW_ZERO,
)


@dataclass(frozen=True)
class ArmParams:
    """정본 팔 파라미터(WAN-449) — 페이퍼 결과를 보고 바꾸지 않는다(WAN-451)."""

    min_stop_width: float = 0.04
    k_threshold: float = 25.0
    stop_multiple: float = 2.0
    hold_bars: int = 4
    crash_lookback_minutes: int = 1440
    crash_threshold: float = -0.05
    crash_multiplier: float = 0.5


@dataclass(frozen=True)
class ArmDecision:
    """체결 하나에 대한 팔 판정."""

    accepted: bool
    reason_code: str = ""
    reason: str = ""
    stop_price: float | None = None
    """2배 손절가(받아들였을 때)."""
    size_scale: float = 1.0
    """거래 크기 배율(폭락이면 0.5)."""
    stop_width: float | None = None
    """2배 **전** 손절폭(진입가 대비)."""
    k_value: float | None = None
    btc_24h: float | None = None


def btc_lookback_return(
    minute_open_times: Sequence[int],
    minute_closes: Sequence[float],
    entry_ms: int,
    lookback_minutes: int,
) -> float | None:
    """진입 분 **직전 확정 1분봉** 종가 ÷ 그보다 `lookback_minutes`분 앞(이하 마지막) 종가 − 1.

    백테스트 `wan439.btc_lookback_return`과 같은 선택이다: 진입 분 자체는 안 본다
    (확정봉 = `open_time + 1분 ≤ 진입 시각`). 틱 체결(분 중간)도 그 분은 아직 안 닫혀
    같은 봉을 고른다. 모자라면 None.
    """
    import bisect

    j = bisect.bisect_right(minute_open_times, entry_ms - MINUTE_MS) - 1
    if j < 0:
        return None
    back = minute_open_times[j] - lookback_minutes * MINUTE_MS
    i = bisect.bisect_right(minute_open_times, back) - 1
    if i < 0:
        return None
    base = minute_closes[i]
    if base <= 0:
        return None
    return minute_closes[j] / base - 1.0


def evaluate_arm_fill(
    params: ArmParams,
    *,
    entry_price: float,
    ob_stop: float,
    entry_ms: int,
    tf_ms: int,
    htf_closed_times: Sequence[int],
    htf_closed_k: Sequence[float | None],
    btc_minute_times: Sequence[int],
    btc_minute_closes: Sequence[float],
) -> ArmDecision:
    """롱 체결 하나를 팔 규칙으로 판정한다(순수 함수 · 백테스트와 같은 순서)."""
    width = max((entry_price - ob_stop) / entry_price, 0.0)
    if width < params.min_stop_width:
        return ArmDecision(
            False,
            REJECT_CODE_ARM_STOP_FLOOR,
            f"손절폭 {width * 100:.2f}% < 하한 {params.min_stop_width * 100:.0f}%",
            stop_width=width,
        )
    k = stoch.k_before_live(htf_closed_times, htf_closed_k, entry_ms, tf_ms)
    if k is None:
        return ArmDecision(
            False,
            REJECT_CODE_ARM_STOCH_UNKNOWN,
            "직전 확정봉 %K를 알 수 없음(워밍업 · 방금 닫힌 봉 미저장)",
            stop_width=width,
        )
    if not k < params.k_threshold:
        return ArmDecision(
            False,
            REJECT_CODE_ARM_STOCH,
            f"%K {k:.1f} ≥ {params.k_threshold:.0f}",
            stop_width=width,
            k_value=k,
        )
    stop = entry_price - params.stop_multiple * (entry_price - ob_stop)
    if stop <= 0:
        return ArmDecision(
            False,
            REJECT_CODE_ARM_STOP_BELOW_ZERO,
            f"2배 손절가 {stop:.6g} ≤ 0(손절폭 {params.stop_multiple * width * 100:.0f}%)",
            stop_width=width,
            k_value=k,
        )
    btc = btc_lookback_return(
        btc_minute_times, btc_minute_closes, entry_ms, params.crash_lookback_minutes
    )
    crash = btc is not None and btc <= params.crash_threshold
    return ArmDecision(
        True,
        stop_price=stop,
        size_scale=params.crash_multiplier if crash else 1.0,
        stop_width=width,
        k_value=k,
        btc_24h=btc,
    )


def time_exit_boundary(entry_ms: int, tf_ms: int, hold_bars: int) -> int:
    """시간 청산 경계 = 진입 봉 + `hold_bars`개 봉이 시작하는 시각(epoch 정렬 · 1w는 목요일)."""
    return (entry_ms // tf_ms + hold_bars) * tf_ms


def is_time_exit_minute(minute_open_ms: int, entry_ms: int, tf_ms: int, hold_bars: int) -> bool:
    """이 1분봉에서 시간 청산하는가 — 경계 직전 마지막 1분봉(또는 구멍이면 경계를 넘은 첫 1분봉).

    백테스트는 경계 직전에 **실제로 있는** 마지막 1분봉 종가에 나간다. 라이브는 그 분이 닫힐 때
    판정하므로 정상이면 같은 분이다. 그 분이 비면(수집 구멍) 라이브는 과거로 돌아갈 수 없어 경계를
    넘은 첫 1분봉에서 나간다 — 알고 받는 차이다.
    """
    return minute_open_ms + MINUTE_MS >= time_exit_boundary(entry_ms, tf_ms, hold_bars)
