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
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from backtest.trade_store import ENGINE_VERSION, UNKNOWN_REVISION, engine_source_revision
from common.timefmt import format_kst_zoned
from data.sqlite_util import configure_connection
from live.trade_timeline import SOURCE_BACKTEST, TimelineRow, backtest_setup_by_cell

__all__ = [
    "TIMELINE_CACHE_VERSION",
    "CachedCell",
    "CachedCellRef",
    "CachedEngine",
    "DayCacheResult",
    "DuplicateTimelineCacheError",
    "PersistReport",
    "PruneCandidate",
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
#: wan335.1: 🚨 **행 스키마가 셋업 행을 다 담지 못하고 있었다** — WAN-297이 담기는 것을 셋업
#: 전부로 넓히면서 payload는 넓혔는데 열은 거래 행 시절 그대로라 `reserve_ms`(탭 봉 시각) ·
#: `limit_price` · `stop_price` · `tap_index`가 왕복에서 통째로 사라졌다. 그중 `tap_index`는
#: **조인 키의 일부**(`live.setup_compare.setup_key`)이고 `stop_price`는 손절폭 그 자체라,
#: 캐시에서 읽은 행으로는 파리티 조인이 성립하지 않았다(`stop-width --with-backtest`가 캐시를
#: 읽게 되면서 드러났다 — WAN-335). 열을 넓히고 버전을 올린다: 옛 적재분은 지문이 갈라져 자동
#: 미스이고 **지워지지 않는다**(배포 뒤 되채우기는 `trades --persist-cache --days N`).
TIMELINE_CACHE_VERSION = "wan335.1"


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
    reserve_ms         INTEGER,
    limit_price        REAL,
    fill_ms            INTEGER,
    fill_price         REAL,
    stop_price         REAL,
    take_profit_price  REAL,
    tap_index          INTEGER,
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


@dataclass(frozen=True)
class CachedCellRef:
    """캐시에 담긴 셀 하나의 좌표 — (심볼, TF)와 그 셀의 `run_id`·적재 시각 (WAN-325).

    행을 싣지 않는 가벼운 참조다. 「어느 엔진이 어느 칸을 갖고 있나」를 먼저 세고, 실제로
    보여 주기로 고른 엔진의 셀만 `load_rows`로 읽기 위한 것이다.
    """

    run_id: str
    symbol: str
    timeframe: str
    created_at: int


@dataclass(frozen=True)
class CachedEngine:
    """한 날짜의 캐시가 담고 있는 **엔진 한 판**과 그 판이 가진 셀 전부 (WAN-325).

    「옛 엔진 결과를 라벨 달아 보여준다」의 단위가 **셀이 아니라 엔진**인 것이 핵심이다 —
    미스인 칸마다 제일 가까운 셀을 주워 오면 한 표에 여러 리비전이 섞이고, 그것이야말로
    이 저장소가 금지하는 「여러 엔진의 숫자를 한 표에서 비교」다(완료 기준 4). 엔진 정체는
    (엔진 소스 지문, 엔진 버전) 쌍으로 가른다 — 리비전만으로 가르면 엔진 버전이 오른 옛
    적재분이 같은 판으로 뭉친다.
    """

    revision: str
    engine_version: str
    engine_name: str
    created_at: int
    cells: tuple[CachedCellRef, ...]

    @property
    def num_cells(self) -> int:
        return len(self.cells)

    def display_label(self) -> str:
        """화면·터미널 배지 — (Ⅰ) 설명형 이름 + (Ⅱ) 엔진 소스 지문.

        `current_engine_label()`과 **같은 꼴**이라 배지만 보고 두 판을 헷갈리지 않는다.
        """
        return f"{self.engine_name} ({self.revision})"

    def created_label(self) -> str:
        """적재 시각(KST). 시각이 안 남아 있으면 지어내지 않고 「적재 시각 미상」이다.

        WAN-325 이전 적재분은 `created_at`이 0으로 저장돼 있어(호출부가 값을 안 넘겼다)
        이 문구가 나온다 — 없는 시각을 그럴듯하게 만들어 내는 것보다 모른다고 밝히는 편이
        낫다(`UNKNOWN_REVISION`과 같은 태도).
        """
        if self.created_at <= 0:
            return "적재 시각 미상"
        return format_kst_zoned(self.created_at)


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
        `is_reentry`를, WAN-335가 셋업 행 다섯 열을 추가했으므로 옛 DB에서 새 INSERT가 죽지
        않게 ALTER를 건다(옛 적재분 행은 NULL로 남지만 캐시 버전이 갈라져 어차피 로드되지
        않는다).
        """
        existing = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(timeline_cache_rows)")
        }
        for column, kind in _ADDED_ROW_COLUMNS:
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE timeline_cache_rows ADD COLUMN {column} {kind}"  # noqa: S608
                )

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

        `created_at`(UTC epoch ms)은 「이 행이 **언제** 계산됐나」다 — 옛 엔진 판을 보여 줄 때
        배너에 찍힌다(WAN-325). 기본 `0`은 「모른다」이고, 실제 적재 경로(`persist_day`)는
        지금 시각을 넣는다(옛 적재분은 0이라 배너가 「적재 시각 미상」으로 밝힌다).
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
                f"INSERT INTO timeline_cache_rows ({', '.join(_ROW_COLUMNS)})"
                " VALUES (" + ", ".join("?" * len(_ROW_COLUMNS)) + ")",
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
            # 셀 조회와 행 조회를 **한 락 안에서** 한다(그 사이 정리가 끼면 "셀은 있는데
            # 행이 0"으로 보인다) — `self._lock`은 재진입이 안 되므로 헬퍼를 나눠 둔다.
            rows = self._rows_locked(run_id)
        return CachedCell(fingerprint=fingerprint, created_at=int(cell[0]), rows=rows)

    def _rows_locked(self, run_id: str) -> tuple[TimelineRow, ...]:
        """`load_rows`의 알맹이 — **락을 이미 쥔 채** 부른다."""
        row_data = self._conn.execute(
            f"SELECT {', '.join(_ROW_VALUE_COLUMNS)} FROM timeline_cache_rows"  # noqa: S608
            " WHERE run_id = ? ORDER BY row_no",
            (run_id,),
        ).fetchall()
        return tuple(_row_from_db(r) for r in row_data)

    def load_rows(self, run_id: str) -> tuple[TimelineRow, ...]:
        """`run_id` 한 셀의 백테 행을 적재 순서대로 복원한다(없으면 빈 튜플).

        지문이 아니라 **`run_id`로** 꺼내는 저수준 경로다 — 옛 엔진 셀은 지금 지문으로 만들
        수 없으므로(그게 미스의 정의다) `day_engines`가 찾아낸 `run_id`로 읽는다(WAN-325).
        ⚠️ 빈 튜플은 「셀이 없다」와 「셀은 있는데 거래가 0건」을 구분하지 않는다 — 그 구분이
        필요한 자리는 `load_cell`(지문 경로)이나 `day_engines`(셀 목록)를 쓸 것.
        """
        with self._lock:
            return self._rows_locked(run_id)

    def day_engines(
        self,
        day_key: str,
        *,
        warmup_days: int,
        fill: str,
        cache_version: str = TIMELINE_CACHE_VERSION,
    ) -> tuple[CachedEngine, ...]:
        """그 날짜의 캐시가 담고 있는 **엔진들**을 커버리지와 함께 돌려준다 (WAN-325).

        지문의 나머지(날짜·워밍업·체결 렌즈·캐시 버전)를 맞춘 뒤 **엔진 축만 열어** 훑는다.
        옛 엔진 결과를 보여 줄 후보를 고르는 자리이고, 삭제는 하지 않는다(읽기 전용).

        🚨 **`cache_version`은 반드시 맞춘다 — 여기가 조용한 실패의 자리다.** 캐시 버전은
        「행의 의미」다: `wan305.1` 셀은 **청산 거래만** 담고 `wan297.1`은 **셋업 전부**
        (청산·미진입·미체결·건너뜀)를 담는다. 옛 버전 셀을 「옛 엔진 결과」라며 3열 대조에
        내주면 미체결·건너뜀 행이 통째로 빠진 표가 「계산됨」으로 떠서, WAN-297이 이름 붙인
        「계산은 됐는데 미체결 행이 없는」 실패가 그대로 재현된다. 그래서 캐시 버전이 다른
        셀은 **후보에서 아예 뺀다**(옛 행은 지우지 않고 그냥 안 쓴다).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, symbol, timeframe, revision, engine_version, engine_name, "
                "created_at FROM timeline_cache_cells WHERE day_key = ? AND warmup_days = ? "
                "AND fill = ? AND cache_version = ? ORDER BY created_at DESC, rowid DESC",
                (day_key, warmup_days, fill, cache_version),
            ).fetchall()

        grouped: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
        for row in rows:
            grouped.setdefault((str(row[3]), str(row[4])), []).append(row)

        engines: list[CachedEngine] = []
        for (revision, engine_version), members in grouped.items():
            seen: dict[tuple[str, str], CachedCellRef] = {}
            for row in members:  # created_at DESC 정렬이라 같은 칸이 겹치면 최신이 이긴다.
                key = (str(row[1]), str(row[2]))
                if key in seen:
                    continue
                seen[key] = CachedCellRef(
                    run_id=str(row[0]),
                    symbol=key[0],
                    timeframe=key[1],
                    created_at=int(row[6]),
                )
            engines.append(
                CachedEngine(
                    revision=revision,
                    engine_version=engine_version,
                    engine_name=str(members[0][5]),
                    created_at=max(int(row[6]) for row in members),
                    cells=tuple(seen.values()),
                )
            )
        engines.sort(key=lambda e: (-e.created_at, e.revision, e.engine_version))
        return tuple(engines)

    # ------------------------------------------------------------------ 정리

    def stale_cells(
        self,
        *,
        keep_revision: str | None = None,
        before_day: str | None = None,
    ) -> tuple[PruneCandidate, ...]:
        """정리 후보 셀을 **읽기만** 해서 돌려준다(삭제하지 않는다, WAN-297 §2-6).

        기준은 두 가지이고 **적어도 하나는 명시해야 한다** — 둘 다 없으면 "전부 지워라"가
        되므로 `ValueError`로 거부한다(무엇을 지우는지 모르는 삭제를 저장소가 스스로 만들지
        않는다, WAN-194 원칙):

        * `keep_revision`: 이 리비전이 **아닌** 셀(= 옛 엔진으로 적재된 셀)이 후보다.
        * `before_day`: 이 KST 날짜보다 **앞선** 날의 셀이 후보다(`YYYY-MM-DD` 문자열 비교 —
          ISO 날짜는 사전순이 곧 시간순이다).

        둘을 함께 주면 **합집합**이다(옛 엔진 셀 + 오래된 날 셀).
        """
        if keep_revision is None and before_day is None:
            raise ValueError(
                "정리 기준을 하나 이상 명시하세요(keep_revision 또는 before_day) — "
                "기준 없는 일괄 삭제는 거부합니다."
            )
        clauses: list[str] = []
        params: list[object] = []
        if keep_revision is not None:
            clauses.append("revision != ?")
            params.append(keep_revision)
        if before_day is not None:
            clauses.append("day_key < ?")
            params.append(before_day)
        where = " OR ".join(clauses)
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, day_key, symbol, timeframe, revision, cache_version, num_rows "
                f"FROM timeline_cache_cells WHERE {where} ORDER BY day_key, symbol, timeframe",
                params,
            ).fetchall()
        return tuple(
            PruneCandidate(
                run_id=str(r[0]),
                day_key=str(r[1]),
                symbol=str(r[2]),
                timeframe=str(r[3]),
                revision=str(r[4]),
                cache_version=str(r[5]),
                num_rows=int(r[6]),
            )
            for r in rows
        )

    def delete_cells(self, run_ids: Sequence[str]) -> int:
        """주어진 셀들을 지우고 지운 셀 수를 돌려준다(행도 함께). 없는 id는 조용히 통과."""
        deleted = 0
        with self._lock, self._conn:
            for run_id in run_ids:
                exists = self._conn.execute(
                    "SELECT 1 FROM timeline_cache_cells WHERE run_id = ?", (run_id,)
                ).fetchone()
                if exists is None:
                    continue
                self._delete_locked(run_id)
                deleted += 1
        return deleted


#: 행 테이블의 열 순서 — INSERT·SELECT가 **한 곳에서** 읽는다. 두 벌로 적으면 열을 늘릴 때
#: 한쪽만 고쳐 값이 옆 칸에 들어간다(WAN-335가 고친 결함의 이웃한 실패 모드다).
_ROW_VALUE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "is_long",
    "status",
    "reserve_ms",
    "limit_price",
    "fill_ms",
    "fill_price",
    "stop_price",
    "take_profit_price",
    "exit_ms",
    "exit_price",
    "exit_reason",
    "pnl_pct",
    "pnl_amount",
    "zone_start_time",
    "zone_confirmed_time",
    "tap_index",
    "is_reentry",
)
_ROW_COLUMNS: tuple[str, ...] = ("run_id", "row_no", *_ROW_VALUE_COLUMNS)

#: 뒤늦게 생긴 열(옛 DB에 ALTER로 덧붙인다) — 이름과 타입만.
_ADDED_ROW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("is_reentry", "INTEGER"),
    ("reserve_ms", "INTEGER"),
    ("limit_price", "REAL"),
    ("stop_price", "REAL"),
    ("take_profit_price", "REAL"),
    ("tap_index", "INTEGER"),
)


def _row_values(run_id: str, row_no: int, row: TimelineRow) -> tuple[object, ...]:
    """백테 `TimelineRow` → DB 행 (열 순서는 `_ROW_COLUMNS`).

    🚨 **셋업 행의 모든 칸을 담는다(WAN-335)** — 옛 판은 「백테 행은 예약·목표가·손절 칸이
    없다」는 전제로 다섯 열을 버렸는데, 그건 거래 행(`cell_timeline_trades`) 시절 이야기였고
    WAN-297이 담는 것을 셋업 행(`cell_setup_timeline`)으로 넓힌 뒤로는 거짓이다. 특히
    `tap_index`는 **조인 키의 일부**라 버리면 캐시에서 읽은 행이 라이브와 절대 안 짝지어진다.
    """
    return (
        run_id,
        row_no,
        row.symbol,
        row.timeframe,
        int(row.is_long),
        row.status,
        row.reserve_ms,
        row.limit_price,
        row.fill_ms,
        row.fill_price,
        row.stop_price,
        row.take_profit_price,
        row.exit_ms,
        row.exit_price,
        row.exit_reason,
        row.pnl_pct,
        row.pnl_amount,
        row.zone_start_time,
        row.zone_confirmed_time,
        row.tap_index,
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
        reserve_ms=None if r[4] is None else int(r[4]),
        limit_price=None if r[5] is None else float(r[5]),
        fill_ms=None if r[6] is None else int(r[6]),
        fill_price=None if r[7] is None else float(r[7]),
        stop_price=None if r[8] is None else float(r[8]),
        take_profit_price=None if r[9] is None else float(r[9]),
        exit_ms=None if r[10] is None else int(r[10]),
        exit_price=None if r[11] is None else float(r[11]),
        exit_reason=None if r[12] is None else str(r[12]),
        pnl_pct=None if r[13] is None else float(r[13]),
        pnl_amount=None if r[14] is None else float(r[14]),
        zone_start_time=None if r[15] is None else int(r[15]),
        zone_confirmed_time=None if r[16] is None else int(r[16]),
        tap_index=None if r[17] is None else int(r[17]),
        is_reentry=None if r[18] is None else bool(r[18]),
    )


# --------------------------------------------------------------------------- #
# 고수준: 적재(야간 크론) · 조회(캐시만)
# --------------------------------------------------------------------------- #


def _now_ms() -> int:
    """지금(UTC epoch ms) — 적재 시각의 기본값.

    ⚠️ 저장·비교는 UTC epoch 그대로이고 KST는 **표시 계층에서만** 붙인다(WAN-172).
    이 값이 「옛 엔진 결과입니다 · 언제 계산됨」 배너의 시각이 된다(WAN-325).
    """
    return int(datetime.now(tz=UTC).timestamp() * 1000)


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
    created_at: int | None = None,
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
    stamp = created_at if created_at is not None else _now_ms()

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
            store.save_cell(fingerprint, rows, replace=replace, created_at=stamp)
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
    """`load_cached_day`의 결과 — 캐시에 있던 행 + 어느 셀이 있고 없었나.

    `stale`이 `None`이 아니면 **행이 지금 엔진의 것이 아니다**(옛 엔진 판을 대신 읽었다,
    WAN-325). 그때 `label`도 그 옛 엔진의 배지로 바뀐다 — 배지가 지금 엔진을 가리키면서
    행은 옛 엔진인 상태가 바로 이 저장소가 금지하는 「조용히 내주기」다.
    """

    rows: tuple[TimelineRow, ...]
    hits: tuple[tuple[str, str], ...]
    misses: tuple[tuple[str, str], ...]
    label: str
    stale: CachedEngine | None = None

    @property
    def all_hit(self) -> bool:
        return not self.misses

    @property
    def is_stale(self) -> bool:
        """이 행들이 **지금 엔진이 아닌** 판에서 왔나(호출부가 경고를 붙일지 판단)."""
        return self.stale is not None


def load_cached_day(
    store: TimelineCacheStore,
    *,
    day_key: str,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    warmup_days: int | None = None,
    revision: str | None = None,
    allow_stale: bool = False,
) -> DayCacheResult:
    """요청한 셀들의 백테 타임라인을 **캐시에서만** 읽는다(조회 경로, WAN-239 §3).

    미스인 셀은 무거운 계산으로 폴백하지 않고 `misses`에 담아 돌려준다 — 호출부(CLI·대시보드)가
    "아직 계산 안 됨"을 명시한다(완료 기준 3). `symbols`/`timeframes`는 반드시 명시한다(기본
    좌표 확정은 호출부 책임 — 캐시는 무엇을 읽을지 스스로 넓히지 않는다).

    📌 **`allow_stale=True`면 미스일 때 옛 엔진 판을 대신 읽는다(WAN-325).** 배포로 엔진
    소스가 바뀌면 과거 날짜가 통째로 미스가 되는데(설계대로 — WAN-106/253/318) 그 행은
    **지워지지 않고 DB에 그대로 있다**(삭제는 `--prune-cache --prune-apply`뿐). 하루치
    재계산이 서버 6분 23초(WAN-322 실측)라 옛 날짜를 훑어보는 것만으로 그 비용을 치르는
    것이 사용자 요청의 계기였다.

    ⚠️ **리비전 키를 느슨하게 하는 게 아니다** — 지금 엔진 셀이 있으면 **언제나 그쪽이
    이기고**(아래 순위), 옛 판을 읽었을 때는 `stale`과 `label`이 그 사실을 밝힌다. 금지된
    것은 옛 결과를 **조용히** 새 결과인 척 내주는 것이지 라벨을 달아 내주는 것이 아니다.

    고르는 규칙(엔진 **단위**로 고른다 — 셀을 주워 섞지 않는다):

    1. 지금 엔진이 요청한 칸을 **전부** 갖고 있으면 그대로 쓴다(옛 것이 새 것을 못 가린다).
    2. 아니면 후보는 「그 날짜의 다른 엔진 판」이고, **지금 엔진보다 더 많은 칸을 가진**
       판만 남긴다(부분만 남은 오늘 판을 옛 판이 이유 없이 밀어내지 않게).
    3. 그중 커버리지 → 적재 시각 순으로 **하나**를 고른다(§5: 옛 리비전이 여럿이면 가장
       최근 것 하나만). 고른 판에 없는 칸은 그대로 `misses`다 — 다른 판에서 메우지 않는다.
    """
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS

    warm = warmup_days if warmup_days is not None else DEFAULT_WARMUP_DAYS
    rev = revision if revision is not None else engine_source_revision()
    wanted = [(symbol, timeframe) for symbol in symbols for timeframe in timeframes]

    rows: list[TimelineRow] = []
    hits: list[tuple[str, str]] = []
    misses: list[tuple[str, str]] = []
    for symbol, timeframe in wanted:
        fingerprint = cell_fingerprint(symbol, timeframe, day_key, warmup_days=warm, revision=rev)
        cell = store.load_cell(fingerprint)
        if cell is None:
            misses.append((symbol, timeframe))
            continue
        hits.append((symbol, timeframe))
        rows.extend(cell.rows)

    if allow_stale and misses:
        stale = _pick_stale_engine(
            store,
            day_key=day_key,
            wanted=wanted,
            warmup_days=warm,
            revision=rev,
            fresh_hits=len(hits),
        )
        if stale is not None:
            return _stale_day_result(store, stale=stale, wanted=wanted)

    return DayCacheResult(
        rows=tuple(rows),
        hits=tuple(hits),
        misses=tuple(misses),
        label=current_engine_label(revision=rev),
    )


def _pick_stale_engine(
    store: TimelineCacheStore,
    *,
    day_key: str,
    wanted: Sequence[tuple[str, str]],
    warmup_days: int,
    revision: str,
    fresh_hits: int,
) -> CachedEngine | None:
    """지금 엔진 대신 보여 줄 **옛 엔진 판 하나**를 고른다(없으면 `None`, WAN-325).

    엔진 정체는 (엔진 소스 지문, 엔진 버전)이라 지금 코드가 쓴 판은 후보에서 빠진다. 남은
    후보 중 요청한 칸을 지금 엔진보다 **더 많이** 덮는 판만 겨루고, 커버리지가 같으면 더
    최근에 적재된 판이 이긴다(§5 — 옛 리비전이 여럿이어도 표에 오르는 것은 하나다).
    """
    from backtest.harness import BASELINE_FILL

    # 체결 렌즈는 `cell_fingerprint`가 쓰는 것과 **같아야** 한다(다르면 후보를 못 찾는다).
    engines = store.day_engines(day_key, warmup_days=warmup_days, fill=BASELINE_FILL.name)
    wanted_set = set(wanted)
    best: tuple[int, int, CachedEngine] | None = None
    for engine in engines:
        if (engine.revision, engine.engine_version) == (revision, ENGINE_VERSION):
            continue  # 지금 엔진 판 — 위에서 이미 지문으로 읽었다.
        coverage = sum(1 for cell in engine.cells if (cell.symbol, cell.timeframe) in wanted_set)
        if coverage <= fresh_hits:
            continue  # 오늘 판보다 나을 게 없으면 굳이 옛 판으로 갈아타지 않는다.
        rank = (coverage, engine.created_at)
        if best is None or rank > (best[0], best[1]):
            best = (coverage, engine.created_at, engine)
    return None if best is None else best[2]


def _stale_day_result(
    store: TimelineCacheStore,
    *,
    stale: CachedEngine,
    wanted: Sequence[tuple[str, str]],
) -> DayCacheResult:
    """고른 옛 엔진 판 **하나만으로** 하루치 결과를 짠다 — 한 표에 두 리비전이 섞이지 않는다."""
    by_cell = {(cell.symbol, cell.timeframe): cell for cell in stale.cells}
    rows: list[TimelineRow] = []
    hits: list[tuple[str, str]] = []
    misses: list[tuple[str, str]] = []
    for key in wanted:
        ref = by_cell.get(key)
        if ref is None:
            misses.append(key)
            continue
        hits.append(key)
        rows.extend(store.load_rows(ref.run_id))
    return DayCacheResult(
        rows=tuple(rows),
        hits=tuple(hits),
        misses=tuple(misses),
        label=stale.display_label(),
        stale=stale,
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
    allow_stale: bool = False,
) -> DayCacheResult:
    """채택 좌표 **전 셀**의 하루치 백테 셋업 행을 캐시에서만 읽는다(WAN-297 §1-2).

    화면 「채택 좌표 전부」 모드의 조회 경로다. `load_cached_day`에 채택 좌표를 먹이는 얇은
    래퍼일 뿐이고, 미스는 여전히 폴백하지 않는다(WAN-239 §3) — 호출부가 "아직 계산 안 됨"을
    명시한다. `allow_stale`은 그대로 넘어간다(옛 엔진 판을 라벨 달아 보여줄지, WAN-325).
    """
    symbols, timeframes = adopted_universe()
    return load_cached_day(
        store,
        day_key=day_key,
        symbols=symbols,
        timeframes=timeframes,
        warmup_days=warmup_days,
        revision=revision,
        allow_stale=allow_stale,
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
    created_at: int | None = None,
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


# --------------------------------------------------------------------------- #
# 정리(pruning) — 명시적 옵트인만, 자동 삭제 없음 (WAN-297 §2-6 · WAN-194 원칙)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PruneCandidate:
    """정리 후보 한 셀 — 무엇을 지우려는지 사람이 읽고 판단할 수 있게 전부 드러낸다."""

    run_id: str
    day_key: str
    symbol: str
    timeframe: str
    revision: str
    cache_version: str
    num_rows: int
