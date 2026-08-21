"""백테 존 대장의 감사용 축약 — 순환 임포트를 피하는 잎 모듈 (WAN-343 §2).

`live.trade_timeline`이 존 대장을 뽑고 `live.zone_audit`이 그걸 대조하는데, 후자는
`live.unpaired_setups` → `live.trade_timeline`을 이미 거치므로 두 값 객체를 어느 한쪽에 두면
순환이 된다. **값 객체만 여기 두고 양쪽이 함께 읽는다**(런타임 의존은 표준 라이브러리뿐).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CellZoneFacts", "ZoneFact"]


@dataclass(frozen=True, slots=True)
class ZoneFact:
    """백테 존 아카이브의 존 하나 — 감사에 필요한 조각만 (`strategy.models.OrderBlock`의 축약).

    전체 `OrderBlock`을 들고 다니지 않는 것은 **프로세스 경계를 넘기 때문**이다(칸 계산은
    워커에서 돈다). 여기 없는 필드가 필요해지면 늘리되, 감사에 안 쓰는 값을 넣지 않는다.
    """

    is_long: bool
    start_time: int
    confirmed_time: int
    break_time: int | None
    swept_time: int | None
    tapped_times: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CellZoneFacts:
    """한 칸(심볼, TF)의 백테 존 대장 + **탐지 창**.

    창을 함께 실어야 `창 밖`과 `존 미탐지`를 가를 수 있다 — 둘을 섞으면 「워밍업이 짧아서」와
    「탐지가 다르게 돌아서」가 한 부류로 뭉쳐 후속이 갈리지 않는다.
    """

    symbol: str
    timeframe: str
    window_start_ms: int
    """탐지에 실제로 들어간 첫 상위TF 봉의 `open_time`. 로드 요청 하한이 아니라 **실측**이다."""
    window_end_ms: int
    """마지막 상위TF 봉의 `open_time`."""
    zones: tuple[ZoneFact, ...]
