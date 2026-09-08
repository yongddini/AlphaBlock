"""WAN-410 — 후보 payload 재생성(캐시 적재 전용 · 측정 아님).

🚨 **왜 다시 만드나**: 이 PR이 `backtest/leverage_book.py`·`backtest/book_cli.py`를 고쳤고
둘 다 `trade_store.ENGINE_SOURCE_FILES`에 있어 **엔진 소스 지문이 바뀐다** → 후보 payload
캐시(WAN-394 §0)가 **전 칸 미스**가 된다. **그게 맞는 동작이다**(WAN-253/364: 엔진이
바뀌었는데 캐시가 히트하면 「고쳤다고 믿으면서 옛 엔진 결과를 인용」하게 된다).

이 스크립트는 그 미스를 **한 번** 메운다. 산출물은 캐시뿐이고 아무것도 판정하지 않는다.

🚨 **이 패스가 도는 동안 `ENGINE_SOURCE_FILES`의 파일을 한 글자도 고치지 말 것** — 지문이
바뀌는 순간 지금 쌓고 있는 칸이 **다른 리비전의 것이 되어** 뒤이은 격자가 전부 미스가 된다
(개발 중 실제로 한 번 겪었다: `leverage_book.py`의 **주석 한 줄**을 고쳤더니 리비전이
`pay:ddadf4223d8a` → `pay:9975f4edec11`로 갈렸다). 엔진 API가 굳은 **뒤에** 이 패스를 돌고,
그 뒤의 서술·주석 수정은 **엔진 밖 파일**(이 모듈·리포트 모듈·문서)에서 한다.

📌 **TF를 싼 것부터 하나씩 돈다** — 캐시는 「제출 순서대로 나오는 대로」 적재되므로
(`wan169_leverage_book._drain`) 48칸을 한 번에 던지면 가장 비싼 15m 칸이 나머지를 전부
붙잡는다. TF마다 끊으면 비싼 패스가 죽어도 앞의 36칸이 남는다.
"""

from __future__ import annotations

import sys
import time

from backtest import harness
from backtest.payload_cache import PayloadCache
from backtest.wan410_daily_loss_circuit_breaker import build_payloads


def main() -> int:
    """TF를 **싼 것부터 하나씩** 돈다 — 캐시는 칸이 나오는 대로 적재되므로(WAN-394 §0)
    비싼 15m 패스가 죽어도 앞의 36칸이 남는다(한 번에 48칸을 던지면 제출 순서 탓에 가장
    비싼 칸이 전부를 붙잡는다)."""
    jobs = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    t0 = time.time()
    total = 0
    for timeframe in ("4h", "2h", "1h", "15m"):
        started = time.time()
        payloads = build_payloads(
            list(harness.DEFAULT_SYMBOLS),
            [timeframe],
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            jobs=jobs,
            cache=PayloadCache(),
        )
        total += len(payloads)
        print(f"[{timeframe}] 칸 {len(payloads)}개 · {time.time() - started:.0f}초", flush=True)
    print(f"칸 {total}개 · {time.time() - t0:.0f}초", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - 실행 스크립트
    raise SystemExit(main())
