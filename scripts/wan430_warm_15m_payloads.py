"""WAN-430 — 15m base 후보만 만들어 wan424 payload 캐시에 적재한다(격자는 그 위에서 돈다).

🚨 이 스크립트는 **캐시를 데우는 것뿐**이고 어떤 표도 내지 않는다. `run_cells`가 칸이 나오는
대로 적재하므로(WAN-394 §0) 중간에 죽어도 앞쪽이 남는다 — 31칸이 8시간대라 그 성질이 요점이다.

🚨 **`if __name__ == "__main__":` 가드가 없으면 죽는다** — `run_cells`가 spawn 프로세스 풀을 쓰고
워커가 `__main__`을 **다시 import** 하므로, 모듈 레벨에서 빌드를 시작하면 워커마다 풀을 또 열어
`BrokenProcessPool`로 끝난다(실제로 한 번 그렇게 죽었다).

    uv run python scripts/wan430_warm_15m_payloads.py 4
"""

from __future__ import annotations

import sys
import time

from backtest.wan424_stoch_ob_arm import DEFAULT_PAYLOAD_DIR, SYMBOLS, build_base_payloads


def main() -> int:
    jobs = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    t0 = time.monotonic()
    payloads = build_base_payloads(
        jobs=jobs, payload_dir=DEFAULT_PAYLOAD_DIR, symbols=SYMBOLS, timeframes=("15m",)
    )
    print(f"[wan430] 15m base 후보 {len(payloads)}칸: {time.monotonic() - t0:.0f}s", flush=True)
    for payload in payloads:
        total = sum(len(v) for v in payload.candidates.values())
        print(f"  {payload.symbol} {payload.timeframe}: 후보 {total:,}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
