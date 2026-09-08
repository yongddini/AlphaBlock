"""WAN-410 — 후보 payload 재생성(캐시 적재 전용 · 측정 아님).

🚨 **왜 다시 만드나**: 이 PR이 `backtest/leverage_book.py`·`backtest/book_cli.py`를 고쳤고
둘 다 `trade_store.ENGINE_SOURCE_FILES`에 있어 **엔진 소스 지문이 바뀐다** → 후보 payload
캐시(WAN-394 §0)가 **전 칸 미스**가 된다. **그게 맞는 동작이다**(WAN-253/364: 엔진이
바뀌었는데 캐시가 히트하면 「고쳤다고 믿으면서 옛 엔진 결과를 인용」하게 된다).

이 스크립트는 그 미스를 **한 번** 메운다. 산출물은 캐시뿐이고 아무것도 판정하지 않는다.
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
