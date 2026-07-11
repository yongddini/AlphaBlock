"""OHLCV 데이터 갭(누락 봉) 탐지 — 순수 함수 (WAN-35).

저장된 시리즈(심볼·TF)의 봉 시각열에서, 기대 봉 간격 대비 **비어 있는 내부
구간**만 찾아낸다. 부수효과·네트워크 의존이 없어 단위 테스트가 쉽다. 실제
재수집(백필)은 `data.repair`가 이 결과를 받아 수행한다.

경계 처리
--------
* **신규 상장 이전 구간**: 저장된 첫 봉 *이전*은 아예 보지 않는다. 상장 전에는
  봉이 존재하지 않으므로 갭이 아니다(오탐 방지).
* **현재 진행 중인 봉**: 저장된 마지막 봉 *이후*(현재 형성 중이거나 아직 수집
  안 된 최신 구간)도 갭으로 잡지 않는다. 그 구간은 재시작 백필(`backfill_all`)의
  몫이며, 갭 복구는 오직 **이미 저장된 데이터 사이의 구멍**만 메운다.
* 따라서 `find_gaps`는 연속한 두 저장 봉 사이 간격이 TF 주기를 초과하는 경우만
  갭으로 보고한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from data.models import timeframe_to_ms


@dataclass(frozen=True, slots=True)
class Gap:
    """저장된 봉 사이의 누락 구간 하나.

    `start_ms`~`end_ms`는 **누락된 봉의 open_time 범위(양끝 포함)**이며, 저장돼
    있는 두 봉 사이의 빈 자리다. `missing`은 그 안에 들어가야 할 봉 개수다.
    """

    start_ms: int
    """첫 번째 누락 봉의 open_time(포함)."""
    end_ms: int
    """마지막 누락 봉의 open_time(포함)."""
    missing: int
    """이 구간에 누락된 봉 개수(>= 1)."""


def find_gaps(timestamps: Sequence[int], timeframe: str) -> list[Gap]:
    """봉 시각열에서 내부 누락 구간을 계산한다(오름차순 가정 불필요).

    `timestamps`는 한 시리즈의 봉 `open_time`(ms) 목록이다. 중복·역순이 섞여
    있어도 방어적으로 정렬·중복 제거한 뒤 처리한다. 봉이 1개 이하면 사이 구간이
    없으므로 빈 리스트를 반환한다.

    지원하지 않는 타임프레임이면 `timeframe_to_ms`가 `ValueError`를 던진다.
    """
    tf_ms = timeframe_to_ms(timeframe)
    ordered = sorted({int(t) for t in timestamps})
    if len(ordered) < 2:
        return []

    gaps: list[Gap] = []
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        delta = cur - prev
        if delta <= tf_ms:
            # 인접(정상) 또는 동일 봉 — 누락 없음.
            continue
        # prev 다음에 있어야 할 봉이 몇 개 비었는지. TF 정렬 데이터에서 정확하고,
        # 비정렬이어도 prev 기준으로 안전하게 계산한다.
        missing = delta // tf_ms - 1
        if missing < 1:
            continue
        start = prev + tf_ms
        end = start + (missing - 1) * tf_ms
        gaps.append(Gap(start_ms=start, end_ms=end, missing=missing))
    return gaps


def total_missing(gaps: Sequence[Gap]) -> int:
    """갭 목록의 총 누락 봉 수."""
    return sum(g.missing for g in gaps)
