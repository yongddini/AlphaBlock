"""다중TF 오더블록 겹침 캐스케이드 — 옵트인 진입 정밀화 (WAN-126).

사용자 제안: **"상위TF에서 오더블록 진입이 확인되면, 그 아래 TF에 오더블록이 겹치는지
내려가며 확인하고, 겹치는 자리에서만 진입한다(겹침이 없으면 진입하지 않는다)."** SMC/ICT
계열의 "상위TF 관심영역(POI) + 하위TF 정밀화(refinement)" 방식이다.

이 모듈은 그 규칙을 **측정용 옵트인**으로만 구현한다 — `ConfluenceParams()` 기본값도
`build_zone_limit_candidates`의 기본 동작도 바꾸지 않는다. 겹침 파라미터를 명시적으로
넘기는 경로에서만 돈다.

## 확정 사양 (WAN-126 사용자 확정, 2026-07-19)

1. **방향 일치** — 상위TF 수요(BULLISH) 존에는 하위TF **수요 존**만 겹침으로 센다
   (반대 방향은 겹침 아님). "같은 자리에서 같은 신호"라는 규칙의 논리를 지킨다.
2. **캐스케이드 사다리** — 상위TF 아래로 내려가며(예: 1h → 15m → 5m → 1m) **첫 겹침에서
   멈춘다**(더 아래는 보지 않는다). 사다리는 `MultiTfOverlapParams.ladder`가 정한다.
   비어 있거나 데이터가 없는 TF는 조용히 건너뛴다(그 사실은 리포트가 별도로 센다).
3. **겹침 정의 3종** — 정본은 `contained`(완전 포함), 나머지 둘(`proximal_in`·`touch`)은
   민감도다. 세 정의는 중첩 관계다: `contained` ⊂ `proximal_in` ⊂ `touch`.

## 3팔 (선별 대 가격 분리 — 이 이슈의 본론)

| 팔 | 겹침 요구 | 씨앗 존(진입가·손절·1R 기준) |
| -- | -- | -- |
| `A` (대조)     | 없음   | 상위TF 존 (= 채택 기본값) |
| `B` (선별만)   | 있음   | **상위TF 존** (1R 불변 — 겹침 필터만의 기여) |
| `C` (선별+가격) | 있음   | **하위TF 겹침 존** (좁아진 1R까지 포함) |

`B − A` = 순수 선별 효과, `C − B` = 순수 가격 효과. 볼린저·오프셋 사슬은 세 팔이 **글자
그대로 동일**하고(WAN-95 "볼린저가 이긴다" 규칙 유지), `C`는 `zone_limit_price`가 읽는
**씨앗 존만** 하위TF로 갈아끼운다. 그 뒤 `deviation_entry_price`(볼린저)·오프셋은 그대로
얹힌다 — 그래서 `C − B`가 두 군데(존 출처 + 볼린저 on/off)가 아니라 **한 군데**(존
출처)만 달라 순수 가격 효과가 된다.

⚠️ **알려진 보수적 편향**(WAN-126 확정 사양 §2): 볼린저가 최종 가격을 덮어쓰므로 하위 존
근단이 만든 가격 이점 중 일부는 볼린저에 흡수돼 `C − B`에 안 잡힌다. `C − B`는 가격
효과의 **하한**으로 읽는다. 리포트가 이를 명시한다.

## 🚨 룩어헤드 가드 (엔진 정확성 — 협상 불가)

상위TF 진입(탭) 시점에 하위TF 오더블록을 **그 순간까지의 데이터로만** 판정해야 한다.
미래 봉으로 만들어진 하위TF 존이 새어 들어오면 결과가 통째로 무의미해진다(볼린저에서
반복해서 데인 자리 — WAN-95/115/119/120).

이 모듈은 겹침을 `ZoneProvider`(시각 → 그 시각에 활성인 하위TF 존)에만 의존해 판정하고,
기본 구현(`order_block_zone_provider`)은 `strategy.models.select_active`로 아카이브를 탭
시각까지 **클리핑**한다(`_clip_to_time`) — 그 시각 이후 확정된 존·아직 없던 무효화/탭은
뷰에서 제외된다. 합성 데이터 회귀 테스트(`tests/test_multi_tf_overlap.py`)가 "미래 하위TF
존이 겹침으로 새어 들어오지 않음"을 **동작으로** 고정한다.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    select_active,
)

#: 겹침 판정 방식. 정본은 `contained`, 나머지는 민감도(WAN-126 확정 사양 §3).
OverlapDefinition = Literal["contained", "proximal_in", "touch"]

#: 3팔 식별자(WAN-126). `A`=대조(겹침 무시), `B`=선별만, `C`=선별+가격.
OverlapArm = Literal["A", "B", "C"]

OVERLAP_DEFINITIONS: tuple[OverlapDefinition, ...] = ("contained", "proximal_in", "touch")
OVERLAP_ARMS: tuple[OverlapArm, ...] = ("A", "B", "C")

#: `시각 → 그 시각에 활성인 하위TF 존들`을 내는 콜러블(룩어헤드 가드의 유일한 접점).
#: 인자: (timeframe, time_ms, direction). 반환: 그 방향·그 시각의 활성 존 목록(클리핑 뷰).
ZoneProvider = Callable[[str, int, OrderBlockDirection], list[OrderBlock]]


class MultiTfOverlapParams(BaseModel):
    """다중TF 겹침 캐스케이드 설정 (WAN-126, 옵트인).

    `ConfluenceParams`에 넣지 않고 별도 객체로 두는 이유: 채택 기본값(`ConfluenceParams()`)을
    한 글자도 건드리지 않기 위해서다. 이 객체를 `build_zone_limit_candidates`에 명시적으로
    넘기는 경로에서만 겹침 로직이 돈다 — 안 넘기면 엔진은 비트 단위로 예전과 같다.
    """

    model_config = ConfigDict(frozen=True)

    arm: OverlapArm
    """3팔 중 하나. `A`는 겹침을 무시하는 대조(= 채택 기본값과 동일 동작)."""
    definition: OverlapDefinition = "contained"
    """겹침 판정 방식. 정본 `contained`. `arm="A"`면 쓰이지 않는다."""
    ladder: tuple[str, ...] = ()
    """상위TF **아래로** 내려갈 TF 순서(예: 1h 진입이면 `("15m", "5m", "1m")`). 위에서부터
    첫 겹침에서 멈춘다. 빈 사다리면 어떤 하위TF도 보지 않으므로 `B`/`C`는 항상 진입 없음."""
    combine: bool = True
    """하위TF 존을 병합(`combine_obs`)한 뒤 겹침을 볼지. 기본 `True`.

    ⚠️ **WAN-149 이후로는 탐지 기본값과 어긋난다** — 그쪽은 `False`(분리)로 옮겼는데 여기는
    `True`로 남긴다. WAN-126이 발표 수치를 낸 설정이 병합이라, 기본값을 따라가면 그 표가
    조용히 다른 사다리로 다시 돈다(`harness.LEGACY_COMBINE_OBS`와 같은 이유의 고정).
    """


@dataclass(frozen=True)
class Refinement:
    """캐스케이드가 찾은 하위TF 정밀화 결과 하나."""

    timeframe: str
    """겹침을 찾은 하위TF(사다리에서 멈춘 칸)."""
    zone: OrderBlock
    """그 TF에서 고른 겹침 존(`C` 팔의 씨앗 존)."""


def _proximal(zone: OrderBlock, direction: OrderBlockDirection) -> float:
    """존의 근단(먼저 닿는 경계). 롱=상단, 숏=하단."""
    return zone.top if direction is OrderBlockDirection.BULLISH else zone.bottom


def zones_overlap(htf: OrderBlock, ltf: OrderBlock, definition: OverlapDefinition) -> bool:
    """상위TF 존 `htf`와 하위TF 존 `ltf`가 `definition` 기준으로 겹치는지.

    호출부가 **같은 방향**만 넘긴다는 계약이다(방향 일치는 `_active_zones_for`에서 이미
    필터한다). 세 정의는 중첩 관계다(`contained` ⊂ `proximal_in` ⊂ `touch`).

    * `touch`: 가격 구간이 조금이라도 겹침(경계 접촉 포함).
    * `contained`: 하위 존이 상위 존 **안에 완전히** 들어감.
    * `proximal_in`: 하위 존의 **근단**(진입가 쪽 끝)이 상위 존 범위 안.
    """
    if definition == "touch":
        return min(htf.top, ltf.top) >= max(htf.bottom, ltf.bottom)
    if definition == "contained":
        return ltf.bottom >= htf.bottom and ltf.top <= htf.top
    # proximal_in
    prox = _proximal(ltf, ltf.direction)
    return htf.bottom <= prox <= htf.top


def choose_refinement_zone(
    htf: OrderBlock, candidates: list[OrderBlock], direction: OrderBlockDirection
) -> OrderBlock:
    """겹치는 하위TF 존이 여러 개일 때 **먼저 닿는(first-touched)** 존을 고른다.

    롱은 가격이 위에서 내려오며 근단(상단)이 **가장 높은** 존에 먼저 닿고, 숏은 아래에서
    올라오며 근단(하단)이 **가장 낮은** 존에 먼저 닿는다. 동률이면 거래량↓·확정 시각↓
    순으로 결정론적 타이브레이크(재현성). `candidates`는 비어 있지 않아야 한다.
    """
    is_long = direction is OrderBlockDirection.BULLISH

    def key(z: OrderBlock) -> tuple[float, float, int]:
        prox = _proximal(z, direction)
        prox_rank = prox if is_long else -prox  # 클수록 먼저 닿음
        return (prox_rank, z.ob_volume, z.confirmed_time)

    return max(candidates, key=key)


def find_refinement(
    htf: OrderBlock,
    setup_time: int,
    params: MultiTfOverlapParams,
    zone_provider: ZoneProvider,
) -> Refinement | None:
    """캐스케이드를 돌려 첫 겹침 정밀화를 찾는다(없으면 None = 진입하지 않음).

    `setup_time`(상위TF 탭 봉 시각) 시점의 하위TF 존만 `zone_provider`로 본다 — 룩어헤드
    가드는 그 provider가 책임진다. 사다리를 위에서부터 훑어 **첫 겹침에서 멈춘다**.
    """
    for timeframe in params.ladder:
        zones = zone_provider(timeframe, setup_time, htf.direction)
        overlapping = [z for z in zones if zones_overlap(htf, z, params.definition)]
        if overlapping:
            zone = choose_refinement_zone(htf, overlapping, htf.direction)
            return Refinement(timeframe=timeframe, zone=zone)
    return None


def order_block_zone_provider(
    results_by_tf: Mapping[str, OrderBlockResult], *, combine: bool
) -> ZoneProvider:
    """탐지 아카이브(TF별 `OrderBlockResult`)를 룩어헤드-안전 `ZoneProvider`로 감싼다.

    각 질의는 `select_active(archive, time_ms, combine=...)`로 아카이브를 **탭 시각까지
    클리핑**한다(`_clip_to_time`) — 그 시각 이후 확정된 존은 애초에 뷰에 없고, 아직
    일어나지 않은 무효화/탭도 반영되지 않는다. 이것이 이 이슈의 룩어헤드 가드다.

    ⚠️ `select_active`는 매 질의마다 아카이브 전체를 훑고(방향별 정렬 + 선택적 병합) 돌므로,
    큰 1분봉 아카이브에 수천 셋업을 질의하면 비싸다 — 리포트 계층이 필요하면 시간 인덱스로
    감싸 최적화하되, **반환 존 집합은 이 구현과 동일**해야 한다(클리핑 규칙을 복제하지 말 것).
    """

    def provider(timeframe: str, time_ms: int, direction: OrderBlockDirection) -> list[OrderBlock]:
        result = results_by_tf.get(timeframe)
        if result is None:
            return []  # 데이터 없는 TF는 조용히 건너뛴다(리포트가 별도로 센다).
        zones = select_active(result.order_blocks, time_ms, combine=combine)
        return [z for z in zones if z.direction is direction]

    return provider


def indexed_zone_provider(
    results_by_tf: Mapping[str, OrderBlockResult], *, combine: bool
) -> ZoneProvider:
    """`order_block_zone_provider`와 **같은 존 집합**을 내되 격자용으로 빠르게 (WAN-126).

    두 가지로 가속한다: (1) 아카이브를 `confirmed_time`으로 미리 정렬해 질의 시각 `T`의
    `confirmed <= T` 접두사만 `select_active`에 넘긴다(그 뒤는 어차피 alive_at이 거른다),
    (2) `(tf, T)` 결과를 **메모이즈**한다 — 클리핑·병합은 방향·팔·정의·구간과 무관하게
    시각에만 달렸으므로, 같은 탭 시각을 7팔이 다시 질의해도 한 번만 계산된다.

    클리핑·병합 규칙은 **복제하지 않고** `select_active`를 그대로 재사용하므로
    `order_block_zone_provider`와 반환이 동일하다(`tests/test_multi_tf_overlap.py`가 고정).
    """
    sorted_obs: dict[str, list[OrderBlock]] = {}
    confirmed_times: dict[str, list[int]] = {}
    for timeframe, result in results_by_tf.items():
        obs = sorted(result.order_blocks, key=lambda o: o.confirmed_time)
        sorted_obs[timeframe] = obs
        confirmed_times[timeframe] = [o.confirmed_time for o in obs]
    cache: dict[tuple[str, int], list[OrderBlock]] = {}

    def provider(timeframe: str, time_ms: int, direction: OrderBlockDirection) -> list[OrderBlock]:
        obs = sorted_obs.get(timeframe)
        if obs is None:
            return []
        key = (timeframe, time_ms)
        active = cache.get(key)
        if active is None:
            hi = bisect.bisect_right(confirmed_times[timeframe], time_ms)
            active = select_active(obs[:hi], time_ms, combine=combine)
            cache[key] = active
        return [z for z in active if z.direction is direction]

    return provider
