"""당일 거래별 타임라인의 백테스트 대조를 미리 계산해 담는 캐시 (WAN-239).

## 왜 이 모듈이 있나

WAN-234의 `alphablock trades`(당일 거래별 타임라인)는 라이브를 주인공으로, 백테스트 채택
엔진을 대조로 병기한다. 문제는 그 **백테 대조가 조회 순간에 계산**된다는 것 — 기본 좌표가
27칸(9종목 × 15m·1h·4h) × 120일 워밍업 × 직렬이라 첫 조회가 무겁다(특히 15m은 봉내 밴드
`value()` 고유 비용으로 셀당 시간이 길다). 사용자 결정(2026-08-03): **"차라리 야간에 미리
다 돌려두는 게 낫다. 굳이 화면에서 조회 버튼 눌러서 그때 계산시키지는 말자."**

이 모듈은 그 산출물(`live.trade_timeline`의 백테 `TimelineRow`)을 하루(KST) · (심볼, TF)
셀 단위로 **저장**하고, 화면·터미널이 **조회**만 하게 만든다. 계산 규칙은 WAN-234 그대로다
— 캐시는 그 산출물을 담을 뿐 무엇을 계산하는지 바꾸지 않는다.

## 실행 지문 (`TimelineCacheFingerprint`) — WAN-106의 교훈 재사용

거래 행만 저장하면 "이게 어느 엔진의 대조인지" 알 수 없다. 이 캐시는 **실행 지문 없이는
적재도 조회도 되지 않는다**. 지문은 하루(KST)·심볼·TF·워밍업과 채택 파라미터
(`ConfluenceParams`/`OrderBlockParams`/`BacktestConfig`)의 직렬화, 그리고 **엔진 버전과
엔진 소스 지문**(`eng:…`, `backtest.trade_store.engine_source_revision`)을 담고, 그 전부의
해시가 `run_id`가 된다.

**엔진 소스 지문을 넣는 것이 특히 중요하다.** 파라미터만으로 키를 만들면 엔진 버그를
고쳐도 키가 같아 옛 결과를 꺼내 준다 — WAN-91/95/112가 반복해 당한 "바꿨다고 믿으면서 안
바뀐" 사고의 재현이다. 규칙(완료 기준 4): (a) 엔진이 바뀌면(= 소스 지문이 달라지면) 옛
행을 **덮어쓰지 않고** 새 행을 따로 쓴다(엔진 간 대조·이력 보존), (b) 조회는 **지금 지문과
일치하는 셀만** 꺼내고 없으면 캐시 미스로 취급, (c) 화면·터미널에 **(Ⅰ) 설명형 엔진 이름 +
(Ⅱ) 엔진 소스 지문 보조**를 표시한다(옛 엔진 숫자를 오늘 것인 양 읽는 사고 방지).

⚠️ **리비전 축을 「레포 HEAD 해시」에서 「엔진 소스 지문」으로 좁혔다(WAN-253).** 옛
`engine_revision()`(레포 전체 git 해시)은 대시보드 UI·리포트·PM·문서 커밋에도 값이 달라져
배포 때마다 야간 캐시가 통째로 무효화됐다. `engine_source_revision()`은 **백테 결과를 바꿀
수 있는 소스 파일**(`ENGINE_SOURCE_FILES`)의 내용만 해시하므로, 비-엔진 배포에는 캐시가
살아 있고 **엔진을 실제로 바꿀 때만** 무효화된다(WAN-106 방어는 유지, 자만 정밀화).

두 겹 태그:
* **(Ⅰ) 설명형 이름** = 실제 파라미터에서 자동 조합한 요약(예:
  `오프셋2bp · 라이브밴드 · 게이트없음 · 필터1.28 · 1.5R · 단일포지션`). 손으로 짓는 이름이
  아니라 파라미터에서 뽑으므로 엔진이 바뀌면 이름도 저절로 바뀐다.
* **(Ⅱ) 정확한 키** = 엔진 소스 지문 `eng:…`(+ 파라미터 직렬화 전체의 `run_id`). 설명형
  이름은 노브가 안 바뀌고 결과만 바뀌는 변경(버그 수정)을 못 가르므로, 지문이 기계 판별을 맡는다.

## 캐시 미스 = "아직 계산 안 됨" (조용한 폴백 금지)

사용자가 원한 것은 "클릭 시 재계산 금지"다. 그래서 조회 경로는 캐시 미스일 때 **조용히 무거운
계산으로 폴백하지 않는다** — "아직 계산 안 됨(야간 크론 대기 또는 `--persist-cache` 수동
실행)"을 명시한다(회귀 테스트가 고정). 단 명시적 `--recompute`는 남겨 수동 재계산은 언제든
가능하다(CLI 소관).

## 성격

순수 도구/캐시다. 엔진·전략·기본값·토대 불변, `ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from backtest.trade_store import ENGINE_VERSION, UNKNOWN_REVISION, engine_source_revision
from data.sqlite_util import configure_connection
from live.trade_timeline import SOURCE_BACKTEST, TimelineRow, backtest_setup_by_cell

__all__ = [
    "TIMELINE_CACHE_VERSION",
    "CachedCell",
    "DayCacheResult",
    "DuplicateTimelineCacheError",
    "PersistReport",
    "TimelineCacheFingerprint",
    "TimelineCacheStore",
    "adopted_universe",
    "cell_fingerprint",
    "compute_and_persist_day",
    "current_engine_label",
    "describe_engine",
    "load_cached_day",
    "load_full_universe_day",
    "persist_day",
]

#: 저장 포맷·복원 규칙의 버전. **캐시 행의 의미가 바뀌면 손으로 올린다** — 지문에 들어가므로
#: 값을 올리면 옛 적재분과 키가 갈라져 새로 적재된다(옛 행은 남는다). `ENGINE_VERSION`은
#: 거래 의미(엔진)를, 이 값은 캐시 표현(행 스키마·조합 규칙)을 각각 판별한다.
#: wan305.1: 백테 타임라인이 채택 재진입(band)을 포함하고 행에 `is_reentry`가 실린다 —
#: 옛 적재분(재진입 없는 판)은 지문이 갈라져 자동 미스가 된다(WAN-305).
#: wan297.1: 셀에 담기는 행이 **청산 거래만**에서 **셋업 전부**(청산·미진입·미체결·건너뜀,
#: WAN-295)로 넓어졌다 — 화면 「채택 좌표 전부」 모드가 읽는 것이 셋업 행이라, 한 캐시가 두
#: 모드를 다 먹이려면 담기는 것이 넓은 쪽이어야 한다(좁은 쪽을 넓은 소비자에게 내주면
#: 「계산했는데 미체결 행이 없는」 조용한 실패가 된다). 옛 적재분은 버전이 갈라져 자동 미스다.
TIMELINE_CACHE_VERSION = "wan297.1"


class DuplicateTimelineCacheError(RuntimeError):
    """같은 지문의 셀이 이미 적재돼 있다(덮어쓰려면 `replace=True`)."""


# --------------------------------------------------------------------------- #
# (Ⅰ) 설명형 엔진 이름 — 실제 파라미터에서 자동 조합
# --------------------------------------------------------------------------- #

_BAND_LABELS = {
    "intrabar_live": "라이브밴드",
    "tap": "탭밴드",
    "prev_closed": "직전봉밴드",
    "intrabar_causal": "인과밴드",
}
_GATE_LABELS = {
    "unconditional": "게이트없음",
    "first_tap_free": "첫탭면제",
    "neutral": "중립게이트",
    "extreme": "극단게이트",
    "none": "워밍업게이트",
}


def _fmt_num(value: float) -> str:
    """`2.0` → `"2"`, `1.28` → `"1.28"` — 배지에 군더더기 0을 남기지 않는다."""
    return f"{value:g}"


def _engine_name_from_confluence(data: dict[str, object]) -> str:
    """`ConfluenceParams` 직렬화(dict)에서 채택 핵심 노브를 뽑아 한 줄 요약으로.

    손으로 짓는 이름이 아니라 **실제 파라미터에서 뽑는다** — 엔진이 바뀌면 이름도 저절로
    바뀌어 "이름표만 갈고 안 바뀐" 사고가 불가능하다(완료 기준 4-Ⅰ). 백테 타임라인은 언제나
    per-cell 단일 포지션(`run_once`, WAN-234 규약)이라 마지막 토큰은 `단일포지션` 고정이다.
    """
    offset = data.get("zone_limit_offset_bps")
    band = data.get("deviation_filter")
    band_bar = band.get("band_bar") if isinstance(band, dict) else None
    gate = data.get("rsi_gate_mode")
    zone_filter = data.get("max_zone_width_atr")
    take_profit_r = data.get("take_profit_r")
    short_enabled = data.get("short_enabled")

    tokens: list[str] = []
    if isinstance(offset, (int, float)):
        tokens.append(f"오프셋{_fmt_num(float(offset))}bp")
    tokens.append(_BAND_LABELS.get(str(band_bar), f"밴드={band_bar}"))
    tokens.append(_GATE_LABELS.get(str(gate), f"게이트={gate}"))
    if zone_filter is None:
        tokens.append("필터없음")
    elif isinstance(zone_filter, (int, float)):
        tokens.append(f"필터{_fmt_num(float(zone_filter))}")
    if isinstance(take_profit_r, (int, float)):
        tokens.append(f"{_fmt_num(float(take_profit_r))}R")
    tokens.append("롱숏" if short_enabled else "롱온리")
    tokens.append("단일포지션")
    return " · ".join(tokens)


def describe_engine(confluence_json: str) -> str:
    """채택 설정(`ConfluenceParams` 직렬화 JSON)에서 (Ⅰ) 설명형 엔진 이름을 낸다."""
    return _engine_name_from_confluence(json.loads(confluence_json))


class TimelineCacheFingerprint(BaseModel):
    """하루(KST)·(심볼, TF) 백테 타임라인 캐시 한 셀의 실행 지문 (WAN-239).

    "어떤 설정으로 나온 대조인지"를 행이 아니라 **셀 단위로** 남긴다. 파라미터 직렬화 전부와
    코드 리비전이 `run_id` 해시에 들어가므로, 엔진이 달라지면 키가 갈라져 옛 셀을 안 꺼낸다.
    """

    model_config = ConfigDict(frozen=True)

    day_key: str
    """조회 기준 KST 날짜 `YYYY-MM-DD`(WAN-172 — 날짜 경계는 KST, 창 계산은 UTC epoch)."""
    symbol: str
    timeframe: str
    warmup_days: int
    fill: str
    confluence_json: str
    order_block_json: str
    config_json: str
    engine_version: str = ENGINE_VERSION
    cache_version: str = TIMELINE_CACHE_VERSION
    revision: str = UNKNOWN_REVISION

    @field_validator(
        "day_key",
        "symbol",
        "timeframe",
        "fill",
        "engine_version",
        "cache_version",
        "revision",
    )
    @classmethod
    def _no_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("실행 지문의 필수 항목이 비어 있습니다.")
        return value

    @field_validator("confluence_json", "order_block_json", "config_json")
    @classmethod
    def _must_be_json_object(cls, value: str) -> str:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"실행 지문의 파라미터가 JSON이 아닙니다: {value[:40]!r}") from exc
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("실행 지문의 파라미터가 비어 있습니다.")
        return value

    @property
    def run_id(self) -> str:
        """지문 전체의 SHA-256 앞 16바이트(hex 32자) — 캐시의 기계 판별 키(완료 기준 4-Ⅱ)."""
        payload = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def engine_name(self) -> str:
        """(Ⅰ) 설명형 엔진 이름 — 실제 파라미터에서 자동 조합(예: `오프셋2bp · 라이브밴드 …`)."""
        return _engine_name_from_confluence(json.loads(self.confluence_json))

    def display_label(self) -> str:
        """화면·터미널 배지: (Ⅰ) 설명형 이름 + (Ⅱ) 엔진 소스 지문 보조 — 옛 엔진 오독 방지."""
        return f"{self.engine_name()} ({self.revision})"


def cell_fingerprint(
    symbol: str,
    timeframe: str,
    day_key: str,
    *,
    warmup_days: int,
    revision: str,
) -> TimelineCacheFingerprint:
    """한 (심볼, TF) 셀의 지문을 채택 엔진 파라미터로 만든다.

    파라미터는 `live.trade_timeline.cell_timeline_trades`가 실제로 쓰는 것과 **같아야** 한다
    (`build_params(fill=BASELINE_FILL)` · `build_config(tf)` · `OrderBlockParams()`) — 지문과
    실제 계산이 갈라지면 화면이 안 돌린 엔진의 배지를 단다(WAN-95 부류). 회귀 테스트가 이
    일치를 고정한다.
    """
    from backtest.harness import BASELINE_FILL, build_config, build_params
    from strategy.models import OrderBlockParams

    params = build_params(fill=BASELINE_FILL)
    cfg = build_config(timeframe)
    return TimelineCacheFingerprint(
        day_key=day_key,
        symbol=symbol,
        timeframe=timeframe,
        warmup_days=warmup_days,
        fill=BASELINE_FILL.name,
        confluence_json=params.model_dump_json(),
        order_block_json=OrderBlockParams().model_dump_json(),
        config_json=cfg.model_dump_json(),
        revision=revision,
    )


def current_engine_label(*, revision: str | None = None) -> str:
    """지금 코드가 도는 채택 엔진의 배지((Ⅰ) 설명형 이름 + (Ⅱ) 엔진 소스 지문).

    캐시가 비어 있어도 "무엇을 계산하려는지"를 화면에 보여 줄 수 있게, 셀과 무관한 채택
    파라미터에서 뽑는다(모든 셀이 같은 파라미터·리비전을 공유하므로 라벨은 하나다).
    """
    from backtest.harness import BASELINE_FILL, build_params

    rev = revision if revision is not None else engine_source_revision()
    name = describe_engine(build_params(fill=BASELINE_FILL).model_dump_json())
    return f"{name} ({rev})"


# --------------------------------------------------------------------------- #
# 저장소
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS timeline_cache_cells (
    run_id           TEXT    PRIMARY KEY,
    created_at       INTEGER NOT NULL,
    day_key          TEXT    NOT NULL,
    symbol           TEXT    NOT NULL,
    timeframe        TEXT    NOT NULL,
    warmup_days      INTEGER NOT NULL,
    fill             TEXT    NOT NULL,
    confluence_json  TEXT    NOT NULL,
    order_block_json TEXT    NOT NULL,
    config_json      TEXT    NOT NULL,
    engine_version   TEXT    NOT NULL,
    cache_version    TEXT    NOT NULL,
    revision         TEXT    NOT NULL,
    engine_name      TEXT    NOT NULL,
    num_rows         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS timeline_cache_rows (
    run_id             TEXT    NOT NULL,
    row_no             INTEGER NOT NULL,
    symbol             TEXT    NOT NULL,
    timeframe          TEXT    NOT NULL,
    is_long            INTEGER NOT NULL,
    status             TEXT    NOT NULL,
    fill_ms            INTEGER,
    fill_price         REAL,
    exit_ms            INTEGER,
    exit_price         REAL,
    exit_reason        TEXT,
    pnl_pct            REAL,
    pnl_amount         REAL,
    zone_start_time    INTEGER,
    zone_confirmed_time INTEGER,
    is_reentry         INTEGER,
    PRIMARY KEY (run_id, row_no)
);

CREATE INDEX IF NOT EXISTS idx_timeline_cache_day
    ON timeline_cache_cells (day_key, revision);
"""

_CELL_COLUMNS: tuple[str, ...] = (
    "run_id",
    "created_at",
    "day_key",
    "symbol",
    "timeframe",
    "warmup_days",
    "fill",
    "confluence_json",
    "order_block_json",
    "config_json",
    "engine_version",
    "cache_version",
    "revision",
    "engine_name",
    "num_rows",
)


@dataclass(frozen=True)
class CachedCell:
    """캐시에서 꺼낸 한 셀 — 지문 + 그 셀의 백테 타임라인 행들."""

    fingerprint: TimelineCacheFingerprint
    created_at: int
    rows: tuple[TimelineRow, ...]


class TimelineCacheStore:
    """당일 백테 타임라인 캐시를 담는 SQLite 저장소 (WAN-239).

    장부 DB(`settings.db_path`)와 같은 파일을 써도 되고(테이블 이름이 겹치지 않는다) 따로 둬도
    된다. `OhlcvStore`/`BacktestRunStore`와 같은 방식(`check_same_thread=False` + 락)을 쓴다.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        configure_connection(self._conn)
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """옛 캐시 DB에 나중에 생긴 열을 덧붙인다(`order_journal._migrate`와 같은 패턴).

        `CREATE TABLE IF NOT EXISTS`는 기존 테이블에 열을 늘려 주지 않는다 — WAN-305가
        `is_reentry`를 추가했으므로 옛 DB에서 새 INSERT가 죽지 않게 ALTER를 건다(옛 적재분
        행은 NULL로 남지만 캐시 버전이 갈라져 어차피 로드되지 않는다).
        """
        existing = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(timeline_cache_rows)")
        }
        if "is_reentry" not in existing:
            self._conn.execute("ALTER TABLE timeline_cache_rows ADD COLUMN is_reentry INTEGER")

    def __enter__(self) -> TimelineCacheStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ 적재

    def save_cell(
        self,
        fingerprint: TimelineCacheFingerprint,
        rows: Sequence[TimelineRow],
        *,
        replace: bool = False,
        created_at: int = 0,
    ) -> str:
        """한 셀의 백테 타임라인 행을 적재하고 `run_id`를 반환한다.

        거래가 0건인 셀도 **셀 레코드는 남긴다**(`num_rows=0`) — 그래야 조회가 "계산했고
        거래 없음"과 "아직 계산 안 됨"을 구분한다. 같은 지문이 이미 있으면
        `DuplicateTimelineCacheError`이고, 덮어쓰려면 `replace=True`(엔진이 그대로인데 다시
        돌린 경우). 엔진이 바뀌면 `run_id`가 달라 **다른 셀**이 되므로 옛 셀을 안 건드린다.
        """
        run_id = fingerprint.run_id
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM timeline_cache_cells WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists is not None:
                if not replace:
                    raise DuplicateTimelineCacheError(
                        f"같은 지문의 셀이 이미 적재돼 있습니다(run_id={run_id}). "
                        "덮어쓰려면 replace=True(CLI: --persist-replace)를 명시하세요."
                    )
                self._delete_locked(run_id)
            self._conn.execute(
                f"INSERT INTO timeline_cache_cells ({', '.join(_CELL_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_CELL_COLUMNS))})",
                (
                    run_id,
                    created_at,
                    fingerprint.day_key,
                    fingerprint.symbol,
                    fingerprint.timeframe,
                    fingerprint.warmup_days,
                    fingerprint.fill,
                    fingerprint.confluence_json,
                    fingerprint.order_block_json,
                    fingerprint.config_json,
                    fingerprint.engine_version,
                    fingerprint.cache_version,
                    fingerprint.revision,
                    fingerprint.engine_name(),
                    len(rows),
                ),
            )
            self._conn.executemany(
                "INSERT INTO timeline_cache_rows (run_id, row_no, symbol, timeframe, is_long,"
                " status, fill_ms, fill_price, exit_ms, exit_price, exit_reason, pnl_pct,"
                " pnl_amount, zone_start_time, zone_confirmed_time, is_reentry)"
                " VALUES (" + ", ".join("?" * 16) + ")",
                [_row_values(run_id, no, row) for no, row in enumerate(rows)],
            )
        return run_id

    def _delete_locked(self, run_id: str) -> None:
        for table in ("timeline_cache_rows", "timeline_cache_cells"):
            self._conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))

    def delete_cell(self, run_id: str) -> None:
        """한 셀의 모든 행을 지운다(없으면 조용히 통과)."""
        with self._lock, self._conn:
            self._delete_locked(run_id)

    # ------------------------------------------------------------------ 조회

    def load_cell(self, fingerprint: TimelineCacheFingerprint) -> CachedCell | None:
        """지문과 일치하는 셀을 꺼낸다. 없으면 `None`(= 캐시 미스, 폴백하지 않는다).

        `run_id`로만 찾으므로 리비전·파라미터가 다르면 자동으로 미스다(완료 기준 4-b).
        """
        run_id = fingerprint.run_id
        with self._lock:
            cell = self._conn.execute(
                "SELECT created_at FROM timeline_cache_cells WHERE run_id = ?", (run_id,)
            ).fetchone()
            if cell is None:
                return None
            row_data = self._conn.execute(
                "SELECT symbol, timeframe, is_long, status, fill_ms, fill_price, exit_ms, "
                "exit_price, exit_reason, pnl_pct, pnl_amount, zone_start_time, "
                "zone_confirmed_time, is_reentry FROM timeline_cache_rows WHERE run_id = ?"
                " ORDER BY row_no",
                (run_id,),
            ).fetchall()
        rows = tuple(_row_from_db(r) for r in row_data)
        return CachedCell(fingerprint=fingerprint, created_at=int(cell[0]), rows=rows)


def _row_values(run_id: str, row_no: int, row: TimelineRow) -> tuple[object, ...]:
    """백테 `TimelineRow` → DB 행. 백테 행은 예약·목표가·손절 칸이 없어 저장하지 않는다."""
    return (
        run_id,
        row_no,
        row.symbol,
        row.timeframe,
        int(row.is_long),
        row.status,
        row.fill_ms,
        row.fill_price,
        row.exit_ms,
        row.exit_price,
        row.exit_reason,
        row.pnl_pct,
        row.pnl_amount,
        row.zone_start_time,
        row.zone_confirmed_time,
        None if row.is_reentry is None else int(row.is_reentry),
    )


def _row_from_db(r: tuple[Any, ...]) -> TimelineRow:
    """DB 행 → 백테 `TimelineRow`(원본 그대로 복원). `source`는 언제나 백테스트다.

    sqlite `fetchall` 행은 열 타입이 정적으로 `Any`라(값은 스키마가 보증) 변환 함수에서
    좁힌다 — `backtest.trade_store._load_trades`와 같은 방식이다.
    """
    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=str(r[0]),
        timeframe=str(r[1]),
        is_long=bool(r[2]),
        status=str(r[3]),
        reserve_ms=None,
        limit_price=None,
        fill_ms=None if r[4] is None else int(r[4]),
        fill_price=None if r[5] is None else float(r[5]),
        stop_price=None,
        take_profit_price=None,
        exit_ms=None if r[6] is None else int(r[6]),
        exit_price=None if r[7] is None else float(r[7]),
        exit_reason=None if r[8] is None else str(r[8]),
        pnl_pct=None if r[9] is None else float(r[9]),
        pnl_amount=None if r[10] is None else float(r[10]),
        zone_start_time=None if r[11] is None else int(r[11]),
        zone_confirmed_time=None if r[12] is None else int(r[12]),
        is_reentry=None if r[13] is None else bool(r[13]),
    )


# --------------------------------------------------------------------------- #
# 고수준: 적재(야간 크론) · 조회(캐시만)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PersistReport:
    """`persist_day`의 결과 요약 — 몇 셀을 적재/건너뛰고 몇 거래를 담았나."""

    day_key: str
    label: str
    persisted: tuple[tuple[str, str], ...]
    skipped: tuple[tuple[str, str], ...]
    total_rows: int


def persist_day(
    store: TimelineCacheStore,
    *,
    day_start_ms: int,
    day_end_ms: int,
    day_key: str,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    jobs: int = 1,
    replace: bool = False,
    revision: str | None = None,
    created_at: int = 0,
) -> PersistReport:
    """하루치 백테 타임라인을 셀 단위로 계산해 캐시에 적재한다(야간 크론, WAN-239 §2).

    계산은 `backtest_setup_by_cell`(WAN-234 규약: 워밍업 연속 · 그날만 평가 · per-cell
    단일 · 미래 봉 없음)이 하고, 이 함수는 각 셀에 지문을 붙여 저장할 뿐이다. 같은 지문이
    이미 있으면 `replace=False`에서 조용히 건너뛴다(`skipped`) — 크론을 두 번 돌려도 무해하다.
    거래 0건 셀도 적재해 조회가 미스와 구분한다.

    📌 **담기는 것은 셋업 전부다(WAN-297)** — 청산 거래만이 아니라 미진입·미체결·건너뜀까지
    (`cell_setup_timeline`, WAN-295). 화면의 「채택 좌표 전부」 모드가 읽는 것이 셋업 행이라
    한 캐시가 두 모드를 다 먹이려면 넓은 쪽을 담아야 한다. 「청산」 행만 추리면
    `backtest_timeline_by_cell`과 비트 동일하므로(실데이터 회귀 테스트가 고정) 거래만 보는
    소비자(`alphablock trades` 표)는 걸러 읽는다 — 좁은 판을 담고 넓게 읽으면 「계산은 됐는데
    미체결 행이 없는」 조용한 실패가 된다.
    """
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS

    warm = warmup_days if warmup_days is not None else DEFAULT_WARMUP_DAYS
    rev = revision if revision is not None else engine_source_revision()

    by_cell = backtest_setup_by_cell(
        day_start_ms=day_start_ms,
        day_end_ms=day_end_ms,
        symbols=symbols,
        timeframes=timeframes,
        warmup_days=warm,
        jobs=jobs,
    )
    persisted: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    total_rows = 0
    label = current_engine_label(revision=rev)
    for (symbol, timeframe), rows in by_cell.items():
        fingerprint = cell_fingerprint(symbol, timeframe, day_key, warmup_days=warm, revision=rev)
        try:
            store.save_cell(fingerprint, rows, replace=replace, created_at=created_at)
        except DuplicateTimelineCacheError:
            skipped.append((symbol, timeframe))
            continue
        persisted.append((symbol, timeframe))
        total_rows += len(rows)
    return PersistReport(
        day_key=day_key,
        label=label,
        persisted=tuple(persisted),
        skipped=tuple(skipped),
        total_rows=total_rows,
    )


@dataclass(frozen=True)
class DayCacheResult:
    """`load_cached_day`의 결과 — 캐시에 있던 행 + 어느 셀이 있고 없었나."""

    rows: tuple[TimelineRow, ...]
    hits: tuple[tuple[str, str], ...]
    misses: tuple[tuple[str, str], ...]
    label: str

    @property
    def all_hit(self) -> bool:
        return not self.misses


def load_cached_day(
    store: TimelineCacheStore,
    *,
    day_key: str,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    warmup_days: int | None = None,
    revision: str | None = None,
) -> DayCacheResult:
    """요청한 셀들의 백테 타임라인을 **캐시에서만** 읽는다(조회 경로, WAN-239 §3).

    미스인 셀은 무거운 계산으로 폴백하지 않고 `misses`에 담아 돌려준다 — 호출부(CLI·대시보드)가
    "아직 계산 안 됨"을 명시한다(완료 기준 3). `symbols`/`timeframes`는 반드시 명시한다(기본
    좌표 확정은 호출부 책임 — 캐시는 무엇을 읽을지 스스로 넓히지 않는다).
    """
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS

    warm = warmup_days if warmup_days is not None else DEFAULT_WARMUP_DAYS
    rev = revision if revision is not None else engine_source_revision()

    rows: list[TimelineRow] = []
    hits: list[tuple[str, str]] = []
    misses: list[tuple[str, str]] = []
    for symbol in symbols:
        for timeframe in timeframes:
            fingerprint = cell_fingerprint(
                symbol, timeframe, day_key, warmup_days=warm, revision=rev
            )
            cell = store.load_cell(fingerprint)
            if cell is None:
                misses.append((symbol, timeframe))
                continue
            hits.append((symbol, timeframe))
            rows.extend(cell.rows)
    return DayCacheResult(
        rows=tuple(rows),
        hits=tuple(hits),
        misses=tuple(misses),
        label=current_engine_label(revision=rev),
    )


# --------------------------------------------------------------------------- #
# 채택 좌표 전부(full-universe) — 화면 버튼과 야간 크론이 **같은 함수**를 탄다 (WAN-297 §1)
# --------------------------------------------------------------------------- #


def adopted_universe() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """채택 좌표(종목, TF)를 코드 기본값에서 읽는다 — 호출부가 하드코딩하지 않게.

    유니버스가 9→12종목이 된 뒤(WAN-307) 화면 라벨만 안 따라가 「9종목 × 4TF = 48셀」이
    떴던 사고(WAN-318 §6)의 캐시 축 대응이다. `backtest.run`의 인자 없는 좌표와 같은 상수를
    읽으므로 좌표를 옮기면 캐시가 읽는 셀도 함께 옮겨진다.
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES

    return tuple(DEFAULT_SYMBOLS), tuple(DEFAULT_TIMEFRAMES)


def load_full_universe_day(
    store: TimelineCacheStore,
    *,
    day_key: str,
    warmup_days: int | None = None,
    revision: str | None = None,
) -> DayCacheResult:
    """채택 좌표 **전 셀**의 하루치 백테 셋업 행을 캐시에서만 읽는다(WAN-297 §1-2).

    화면 「채택 좌표 전부」 모드의 조회 경로다. `load_cached_day`에 채택 좌표를 먹이는 얇은
    래퍼일 뿐이고, 미스는 여전히 폴백하지 않는다(WAN-239 §3) — 호출부가 "아직 계산 안 됨"을
    명시한다.
    """
    symbols, timeframes = adopted_universe()
    return load_cached_day(
        store,
        day_key=day_key,
        symbols=symbols,
        timeframes=timeframes,
        warmup_days=warmup_days,
        revision=revision,
    )


def compute_and_persist_day(
    store: TimelineCacheStore,
    *,
    day_start_ms: int,
    day_end_ms: int,
    day_key: str,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    jobs: int = 1,
    revision: str | None = None,
    created_at: int = 0,
) -> tuple[PersistReport, DayCacheResult]:
    """하루치를 **계산해 적재한 뒤 캐시에서 다시 읽어** 돌려준다(화면 버튼 경로, WAN-297 §1-1).

    화면 버튼이 이 함수를 타므로 **화면이 그리는 행은 언제나 디스크에 담긴 그 행**이다 —
    "화면에는 떴는데 캐시에는 없다"가 구조적으로 불가능하고, 세션이 끊겨도 다음 조회가
    `load_full_universe_day`로 그대로 뜬다(완료 기준 1).

    적재는 야간 크론과 **같은 `persist_day`**를 탄다(완료 기준 4) — 두 경로가 각자 계산하면
    산출물이 갈라진다(WAN-146의 교훈). `replace=True`인 이유는 버튼이 「지금 다시 계산해
    달라」는 명시적 요청이기 때문이다(같은 지문이 이미 있으면 크론 판을 이 판으로 덮는다 —
    같은 엔진·같은 좌표라 값은 같다).
    """
    report = persist_day(
        store,
        day_start_ms=day_start_ms,
        day_end_ms=day_end_ms,
        day_key=day_key,
        symbols=symbols,
        timeframes=timeframes,
        warmup_days=warmup_days,
        jobs=jobs,
        replace=True,
        revision=revision,
        created_at=created_at,
    )
    syms = list(symbols) if symbols is not None else list(adopted_universe()[0])
    tfs = list(timeframes) if timeframes is not None else list(adopted_universe()[1])
    cached = load_cached_day(
        store,
        day_key=day_key,
        symbols=syms,
        timeframes=tfs,
        warmup_days=warmup_days,
        revision=revision,
    )
    return report, cached
