"""백테 병렬 워커 수가 **실제로** 설정값대로 뜨는지 세는 프로브 (WAN-354 완료 기준 2).

이 저장소가 반복해 겪은 실패는 「설정했다고 믿으면서 기본값으로 도는」 것이다
(WAN-91/95/112/123/159). 그래서 이 프로브는 **설정 파일에 줄이 있는지**를 보지 않고
`ProcessPoolExecutor`를 실제로 띄워 **자식 프로세스가 몇 개 생기는지**를 센다 —
백테 fan-out(`backtest.run._iter_outcomes`)이 쓰는 것과 같은 실행기다.

계산은 하지 않는다(각 작업이 자기 PID만 돌려준다). 서버에서 러너·수집기와 자원을
다투면 안 되므로 **가볍게** 끝난다.

사용:

    uv run python -m scripts.wan354_jobs_probe          # 채택 좌표 48셀 기준
    uv run python -m scripts.wan354_jobs_probe --cells 12

⚠️ `--jobs`는 결과를 안 바꾸는 순수 성능 노브다(WAN-121: 직렬 = 병렬 비트 동일) —
이 프로브가 확인하는 것은 성능 설정의 배선이지 측정값이 아니다.
"""

from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor

#: 채택 좌표 = 12종목 × 4TF(WAN-307/252). 워커 수가 셀 수로 캡되는지까지 같은 자로 본다.
DEFAULT_CELLS = 48


#: 각 작업이 붙잡고 있는 시간(초)의 후보들. 0이면 **첫 워커가 큐를 다 비워** 나머지가 일감을
#: 못 잡고, 서로 다른 PID가 하나만 관측된다(실측: 4개를 띄웠는데 1개로 보였다). 일을 붙잡고
#: 있어야 「N개가 동시에 돈다」가 관측되므로 이 지연은 측정의 일부다.
#:
#: 🚨 여러 값을 두는 이유: 바쁜 서버(러너·수집기가 도는 2코어 박스)에서 짧은 지연이면 워커
#: 하나가 큐를 다 비울 수 있고, 그러면 **배선은 멀쩡한데 「❌ 설정과 실제가 다르다」는 거짓
#: 경보**가 뜬다. 부족하면 더 길게 잡고 다시 센다 — 확인 도구가 틀린 경보를 내면 그 도구를
#: 아무도 안 믿는다.
_HOLD_CANDIDATES: tuple[float, ...] = (0.1, 0.3, 1.0)


def worker_pid(hold_seconds: float) -> int:
    """`hold_seconds`만큼 일감을 붙잡고 있다가 자기 프로세스 PID를 돌려준다.

    **모듈 최상위 함수여야 한다** — macOS/Windows의 spawn 방식은 워커가 이 모듈을 다시
    임포트하므로, 인라인 스크립트(`python -`)나 지역 함수로 두면 워커가 뜨자마자 죽는다
    (`BrokenProcessPool`). 실제로 그렇게 짰다가 걸렸다.
    """
    time.sleep(hold_seconds)
    return os.getpid()


def count_workers(cells: int = DEFAULT_CELLS) -> tuple[int, int, int]:
    """`(설정값, 그 셀 수에 실제로 쓰는 워커 수, 실제로 뜬 워커 프로세스 수)`.

    앞 둘은 코드가 **말하는** 값이고 마지막이 **일어난** 일이다. 셋이 어긋나면 배선이
    깨진 것이다. 작업을 워커 수보다 넉넉히 뿌리고(×4) 각 작업이 잠깐 일감을 붙잡게 해
    모든 워커가 최소 한 번은 잡히게 한다(`_HOLD_CANDIDATES` 주석 참고 — 부족하면 더 길게
    잡고 다시 센다).
    """
    from backtest.harness import default_jobs
    from backtest.run import resolve_jobs

    requested = default_jobs()
    workers = resolve_jobs(requested, cells)
    if workers <= 1:
        # 직렬 경로는 풀을 만들지 않는다(`_iter_outcomes`와 같은 규약) — 부모가 곧 일꾼이다.
        return requested, workers, 1

    best = 0
    for hold in _HOLD_CANDIDATES:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pids = set(pool.map(worker_pid, [hold] * (workers * 4)))
        best = max(best, len(pids))
        if best >= workers:
            break
    return requested, workers, best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="백테 병렬 워커 수 배선 확인(WAN-354)")
    parser.add_argument(
        "--cells",
        type=int,
        default=DEFAULT_CELLS,
        help=f"격자 셀 수(기본 {DEFAULT_CELLS} = 채택 좌표 12종목 × 4TF)",
    )
    args = parser.parse_args(argv)

    requested, workers, spawned = count_workers(args.cells)
    print(f"설정값(default_jobs)         : {requested}")
    print(f"{args.cells}셀에 실제로 쓰는 워커 수 : {workers}")
    print(f"실제로 뜬 워커 프로세스      : {spawned}개")
    ok = spawned == workers
    print("판정: " + ("✅ 설정이 먹었다" if ok else "❌ 설정과 실제가 다르다"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
