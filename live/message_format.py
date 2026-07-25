"""알림 메시지의 저수준 포맷 헬퍼 — 여러 러너가 공유하는 단일 소스 (WAN-189).

가격·금액·수량·방향·시각을 사람이 읽기 좋게 찍는 순수 함수들이다. 옛 시그널 러너
(`live.notifier`, WAN-25)와 채택 지정가 러너(`live.zone_limit_notifier`, WAN-189)가
**같은 함수**를 쓰도록 여기 모아 둔다 — 두 러너가 각자 포맷을 지어내면 같은 값이 폰에서
다르게 보인다(로직 이중화 금지, WAN-45/100 교훈).

시각은 전부 KST(`common.timefmt`, WAN-172)로 위임한다 — 이 모듈은 시간대 규칙을 다시
구현하지 않는다.
"""

from __future__ import annotations

from common.timefmt import format_kst_zoned
from strategy.models import OrderBlockDirection


def fmt_price(value: float) -> str:
    """가격을 천 단위 구분 + 불필요한 소수점 0 제거로 읽기 좋게 포맷."""
    text = f"{value:,.8f}".rstrip("0").rstrip(".")
    return text or "0"


def fmt_qty(value: float) -> str:
    """수량을 불필요한 소수점 0 제거로 포맷."""
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def fmt_money(value: float) -> str:
    """견적 통화 금액(USDT 등)을 천 단위 구분·소수 2자리로 포맷."""
    return f"{value:,.2f}"


def fmt_usd(value: float, *, signed: bool = False) -> str:
    """폰 알림용 정수 달러 표기(`$4,987`·`+$74`). 소수점은 폰에서 노이즈라 반올림한다.

    `signed=True`면 부호를 앞에 붙인다(손익용) — 0은 `+$0`으로 찍는다(손실이 아님).
    """
    rounded = round(value)
    if signed:
        sign = "+" if rounded >= 0 else "−"
        return f"{sign}${abs(rounded):,}"
    return f"${rounded:,}"


def fmt_pct(value: float, *, signed: bool = True) -> str:
    """백분율(이미 % 단위)을 소수 2자리로. 기본은 부호를 붙인다(손익률용)."""
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def fmt_duration(ms: int) -> str:
    """경과 시간(ms)을 한국어로 짧게: `45초`·`3분`·`2시간 7분`.

    예약→체결 대기 시간·포지션 보유 시간 같은 자리에서 쓴다. 음수는 0으로 클램프한다
    (시각 정렬 오차 방어 — 벽시계가 아니라 봉 시각이라 드물지만 0을 밑돌 수 있다).
    """
    total_seconds = max(ms, 0) // 1000
    total_minutes = total_seconds // 60
    if total_minutes < 1:
        return f"{total_seconds}초"
    if total_minutes < 60:
        return f"{total_minutes}분"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"


def fmt_time(open_time_ms: int) -> str:
    """알림 본문의 시각(KST 표기, WAN-172). 내부 비교·저장은 UTC epoch ms 그대로다."""
    return format_kst_zoned(open_time_ms)


def direction_label(direction: OrderBlockDirection) -> str:
    """방향을 한국어 라벨로(`롱`/`숏`)."""
    return "롱" if direction is OrderBlockDirection.BULLISH else "숏"


def short_symbol(symbol: str) -> str:
    """폰 알림용 짧은 종목명(`BTC/USDT:USDT` → `BTC`).

    존/장부/CSV는 전체 심볼을 쓰지만, 폰 한 줄에는 베이스 자산만 보이면 충분하다.
    구분자(`/`)가 없으면 원문 그대로 돌려준다.
    """
    return symbol.split("/", 1)[0]
