"""스토캐스틱 %K — 백테스트(WAN-424 팔)와 페이퍼 러너가 같이 쓰는 **단일 소스** (WAN-451).

`backtest/wan424_stoch_ob_arm.py`에 있던 `stoch_k_line`·`k_before`를 여기로 옮겼다(그 모듈은 이제
import한다). 정의는 트레이딩뷰 스토캐스틱(20,10,10)의 **%K 선 = SMA(raw %K, 10)** 이고 D선은 쓰지
않는다. 진입 게이트는 「진입 시각이 속한 봉의 **직전 확정봉** %K < 25」(WAN-436 정본).

🚨 **라이브에서 `k_before`를 그대로 부르면 한 봉 더 오래된 값이 나온다** — 백테스트 이력에는
진입이 일어난 봉(그 시점엔 형성 중이던 봉)이 이미 저장돼 있지만, 라이브 저장소에는 **확정봉만**
있다. `k_before`의 두 갈래(진입 봉이 있으면 그 앞 · 없으면 그 이하 마지막 봉의 한 칸 앞)가
라이브에서 엉뚱한 갈래를 탄다. `k_before_live`가 형성 중 봉 자리를 하나 붙여
**백테스트가 본 이력 모양**을 흉내 낸 뒤 같은 `k_before`를 부른다(1w의 epoch 주(목요일)
대 저장 주(월요일) 어긋남까지 정본대로 재현한다 — 사용자 결정 ③ 가).

이 모듈은 오더블록 후보·체결·손익에 안 실린다 — `backtest.trade_store`의 비-엔진 목록에 있다.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence

#: 트레이딩뷰 스토캐스틱(20,10,10) — %K 선 = SMA(raw %K, 10).
K_LENGTH = 20
K_SMOOTH = 10


def stoch_k_line(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    *,
    length: int = K_LENGTH,
    smooth: int = K_SMOOTH,
) -> list[float | None]:
    """봉 i의 %K 선(= SMA(raw %K, smooth)) — 봉 i가 **닫혀야** 아는 값이다.

    raw %K = 100·(종가 − 최근 `length`봉 최저) / (최고 − 최저). 폭이 0이면 50(스크래치 규약).
    워밍업(raw가 `length`봉, 평활이 `smooth`개 모자란 자리)은 `None`이다.
    """
    n = len(close)
    if not (len(high) == len(low) == n):
        raise ValueError("high/low/close 길이가 다릅니다.")
    raw: list[float | None] = []
    for i in range(n):
        if i + 1 < length:
            raw.append(None)
            continue
        hi = max(high[i + 1 - length : i + 1])
        lo = min(low[i + 1 - length : i + 1])
        raw.append(50.0 if hi <= lo else 100.0 * (close[i] - lo) / (hi - lo))
    out: list[float | None] = []
    for i in range(n):
        if i + 1 < smooth:
            out.append(None)
            continue
        window = raw[i + 1 - smooth : i + 1]
        vals = [v for v in window if v is not None]
        out.append(sum(vals) / smooth if len(vals) == smooth else None)
    return out


def k_before(
    times: Sequence[int], k: Sequence[float | None], entry_ms: int, tf_ms: int
) -> float | None:
    """진입 시각이 속한 봉의 **직전 확정봉** %K — 인과적으로 알 수 있는 가장 최신 값.

    스크래치(`hold_ls.py` `k_at`) 규약을 그대로 옮긴다: 진입 봉 `b = floor(t/tf)·tf`가
    저장돼 있으면 그 앞 봉의 값, 없으면(데이터 구멍) `b` 이하 마지막 봉의 **한 칸 앞** 값.
    두 경우 모두 진입 시점 이전에 닫힌 봉이다(뒤쪽은 한 봉 더 보수적이다).
    """
    if not times:
        return None
    bar = (entry_ms // tf_ms) * tf_ms
    i = bisect.bisect_left(times, bar)
    if i >= len(times) or times[i] != bar:
        i = bisect.bisect_right(times, bar) - 1
        if i < 0:
            return None
    return k[i - 1] if i - 1 >= 0 else None


def k_before_live(
    closed_times: Sequence[int],
    closed_k: Sequence[float | None],
    entry_ms: int,
    tf_ms: int,
) -> float | None:
    """라이브용 `k_before` — **확정봉만** 있는 저장소에서 백테스트와 같은 값을 낸다.

    백테스트 이력에는 진입 순간 형성 중이던 봉도 들어 있다(나중에 닫혀 저장됐으므로). 그 봉의
    자리(저장 정렬 기준 = 마지막 확정봉 + 한 주기)가 진입 시각 이하이면 목록 끝에 붙여(값은 쓰이지
    않는 `None`) 같은 `k_before`를 부른다. 저장 정렬과 epoch 정렬이 다른 1w도 이렇게 해야
    백테스트가 탄 갈래를 그대로 탄다.
    """
    times = list(closed_times)
    k = list(closed_k)
    if times:
        forming = times[-1] + tf_ms
        if entry_ms >= forming + tf_ms:
            # 진입 시각이 「마지막 확정봉 다음 봉」보다도 뒤다 = 방금 닫힌 봉이 아직 저장되지
            # 않았다(수집 지연·구멍). 그 상태로 고르면 한 봉 이상 낡은 값을 조용히 쓰게 된다 —
            # 모른다(None)로 돌려 호출부가 거부 사유로 남기게 한다(지어내지 않는다).
            return None
        if forming <= entry_ms:
            times.append(forming)
            k.append(None)
    return k_before(times, k, entry_ms, tf_ms)
