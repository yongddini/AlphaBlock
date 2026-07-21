"""같은 DB 파일을 여는 모든 저장소가 공유하는 SQLite 연결 설정 (WAN-156 §4).

`data/ohlcv.db` 하나를 **네 개의 래퍼**가 연다 — `data.storage.OhlcvStore`,
`data.funding.FundingRateStore`, `paper.store.PaperTradeStore`,
`backtest.trade_store.TradeStore`. 게다가 수집기·러너·대시보드·백테스트가 **동시에**
프로세스를 띄운다.

그런데 연결 설정이 아예 없어 SQLite 기본값으로 돌고 있었다::

    journal_mode = delete   ← 쓰는 동안 다른 프로세스가 읽지도 못한다
    busy_timeout = 5000 ms  ← 5초 기다리고 포기

1.4GB DB에 1분봉을 대량 삽입하면 쓰기가 길어지고, 옆 프로세스가 5초 만에
`sqlite3.OperationalError: database is locked`로 죽는다. 실제로 백필 중에 터졌고,
**상시 DB를 읽는 페이퍼 러너(WAN-45)에서도 똑같이 터진다** — 우연히 백필에서 먼저
보였을 뿐이다.

두 가지를 바꾼다:

* **WAL 저널**: 읽기와 쓰기가 서로를 막지 않는다. DB **파일 속성**이라 한 번만
  켜면 이후 모든 연결에 적용되지만, 새 DB·다른 환경을 위해 연결마다 멱등하게 건다.
* **넉넉한 `busy_timeout`**: 락 경합 시 즉시 포기하지 않고 기다린다. **연결마다**
  설정해야 한다(파일 속성이 아니다).

⚠️ WAL을 켜면 `ohlcv.db-wal`·`ohlcv.db-shm` 파일이 옆에 생긴다 — 정상이며
`.gitignore`가 잡는다. `:memory:`는 WAL을 지원하지 않아 조용히 건너뛴다.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

#: 락 경합 시 대기 시간. 대량 1분봉 삽입 트랜잭션이 끝날 때까지 버티는 것이 목적이라
#: SQLite 기본값(5초)보다 넉넉하게 잡는다.
DEFAULT_BUSY_TIMEOUT_MS = 30_000


def configure_connection(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> None:
    """연결에 WAL 저널과 `busy_timeout`을 적용한다(멱등, 실패해도 치명적이지 않음).

    메모리 DB 등 WAL을 지원하지 않는 대상에서는 저널 모드 전환이 실패하거나 다른
    모드를 돌려주는데, 그건 정상이라 로그만 남기고 진행한다 — 연결 설정 때문에
    저장소 생성이 실패하면 훨씬 나쁘다.
    """
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    try:
        (mode,) = conn.execute("PRAGMA journal_mode = WAL").fetchone()
    except sqlite3.DatabaseError as exc:  # pragma: no cover - 드문 환경 방어
        logger.warning("WAL 저널 전환 실패(기본 저널로 계속): %s", exc)
        return
    if str(mode).lower() != "wal":
        # `:memory:`는 "memory"를 돌려준다 — 동시 접근 자체가 없으므로 문제없다.
        logger.debug("저널 모드가 WAL이 아님(%s) — 이 DB에서는 정상일 수 있습니다.", mode)
