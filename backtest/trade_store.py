"""백테스트 거래 저장소 — 채택 엔진의 거래를 DB에 적재하고 조회한다 (WAN-106).

## 왜 이 모듈이 있나

지금까지 백테스트는 캔들 1,200만 개를 읽어 오더블록·진입·손절을 다시 계산하고, 요약
1행(`total_return`·`num_trades`…)만 뽑은 뒤 **거래 단위 결과를 버렸다**. 그래서
"어디서 손절났는지 보고싶다"에 답하려면 매번 전체 재계산이었고(BTC 15m 한 조합에
7분 이상), 커밋 259개·리포트 41개를 쌓는 동안 **채택 엔진이 낸 거래를 한 건도 눈으로
본 적이 없었다.** 이 모듈은 그 결과를 한 번 계산해 **저장**하고, 화면이 **조회**만
하게 만든다(사용자 결정 2026-07-20).

## 실행 지문 (`RunFingerprint`) — 이 모듈의 핵심 계약

거래 행만 저장하면 "이게 어느 엔진의 거래인지" 알 수 없다. 이 저장소는 **실행 지문
없이는 적재도 조회도 되지 않는다**:

* `save_run`은 `RunFingerprint`를 **반드시** 받는다. 지문은 심볼·TF·구간·창과
  `ConfluenceParams`/`OrderBlockParams`/`BacktestConfig`의 직렬화, 그리고 **엔진 버전과
  코드 리비전**을 담고, 그 전부의 해시가 `run_id`가 된다.
* `load_result`/`load_setups`는 `backtest_runs`에 지문 행이 없는 `run_id`를 거부한다
  (`UnknownRunError`) — 거래 행만 남은 고아 데이터를 조용히 돌려주지 않는다.

**코드 리비전을 지문에 넣는 것이 특히 중요하다.** 파라미터만으로 키를 만들면 엔진
버그를 고쳐도 키가 같아 옛 결과를 꺼내 준다 — 이 저장소가 WAN-91(펀딩 배선했는데 안
넘김) · WAN-95(라벨만 `zone_limit`) · WAN-112(2bp를 `0.0002`로 넣어 사실상 0bp)에서
반복해 당한 **"바꿨다고 믿으면서 안 바뀐"** 사고를 그대로 재현하게 된다.

같은 지문으로 다시 적재하면 **기본은 거부**(`DuplicateRunError`)이고, 덮어쓰려면
`replace=True`를 명시해야 한다 — 조용한 중복 적재도, 조용한 덮어쓰기도 만들지 않는다.

## 무엇을 저장하나

| 테이블 | 내용 |
| -- | -- |
| `backtest_runs` | 실행 지문 + 요약 지표(`total_return`·`num_trades`·체결률…) |
| `backtest_trades` | 거래 한 건 = 한 행(시드 변화 `equity_before`→`equity_after` 포함) |
| `backtest_trade_exits` | 부분 청산까지 포함한 청산 체결 원본 |
| `backtest_setups` | **미체결 셋업** — 지정가를 걸었는데 가격이 안 와서 못 산 자리 |
| `backtest_equity` | 시드곡선 |

`backtest_trade_exits`를 따로 두는 이유는 `load_result`가 `BacktestResult`를 **원본
그대로 복원**하기 위해서다. 복원된 결과는 화면(`trades_to_display_frame`)·CSV·요약표가
같은 함수를 타므로 세 출력이 같은 숫자를 낸다(회귀 테스트가 고정한다).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest.models import (
    BacktestConfig,
    BacktestResult,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)
from backtest.zone_limit_backtest import SetupDiagnostic, ZoneLimitStats, build_result_from_trades

#: 저장 포맷·복원 규칙의 버전. **엔진의 거래 의미가 바뀌면 손으로 올린다** — 지문에
#: 들어가므로 값을 올리면 옛 적재분과 키가 갈라져 새로 적재된다(옛 행은 남는다).
ENGINE_VERSION = "wan106.1"

#: 코드 리비전을 알 수 없을 때의 표기. `"unknown"`이 지문에 그대로 들어가므로 **git 밖에서
#: 돌린 적재분끼리는 서로 구분되지 않는다** — 조용히 감추는 대신 라벨로 드러낸다.
UNKNOWN_REVISION = "unknown"


def engine_revision(*, cwd: str | Path | None = None) -> str:
    """현재 코드 리비전(git 짧은 해시, 워킹트리가 더러우면 `-dirty` 접미).

    git이 없거나 저장소가 아니면 `UNKNOWN_REVISION`을 낸다 — 적재 자체를 막지는 않되,
    지문에 "모른다"가 남아 나중에 그 행을 신뢰할지 판단할 수 있게 한다.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - git 없는 환경
        return UNKNOWN_REVISION
    if not head:  # pragma: no cover - 방어
        return UNKNOWN_REVISION
    return f"{head}-dirty" if dirty else head


class DuplicateRunError(RuntimeError):
    """같은 지문의 실행이 이미 적재돼 있다(덮어쓰려면 `replace=True`)."""


class UnknownRunError(LookupError):
    """지문이 없는 `run_id`를 조회했다 — 고아 거래 행은 돌려주지 않는다."""


class RunFingerprint(BaseModel):
    """한 번의 백테스트 실행을 식별하는 지문 (WAN-106).

    "어떤 설정으로 나온 거래인지"를 **행이 아니라 실행 단위로** 남긴다. 같은 심볼·TF를
    다른 설정으로 두 번 돌린 결과가 섞이면 안 되므로, 파라미터 직렬화 전부가 `run_id`
    해시에 들어간다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str = "full"
    window: int = 0
    start_time: int | None = None
    end_time: int | None = None
    entry_mode: str
    fill: str
    seed: int = 0
    position_mode: str = "single"
    portfolio_leverage: float | None = None
    confluence_json: str
    order_block_json: str
    config_json: str
    engine_version: str = ENGINE_VERSION
    revision: str = UNKNOWN_REVISION

    @field_validator("symbol", "timeframe", "entry_mode", "fill", "engine_version", "revision")
    @classmethod
    def _no_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("실행 지문의 필수 항목이 비어 있습니다.")
        return value

    @field_validator("confluence_json", "order_block_json", "config_json")
    @classmethod
    def _must_be_json_object(cls, value: str) -> str:
        """빈 문자열·비-JSON을 거부한다 — 지문이 껍데기면 없느니만 못하다."""
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"실행 지문의 파라미터가 JSON이 아닙니다: {value[:40]!r}") from exc
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("실행 지문의 파라미터가 비어 있습니다.")
        return value

    @property
    def run_id(self) -> str:
        """지문 전체의 SHA-256 앞 16바이트(hex 32자)."""
        payload = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def label(self) -> str:
        """화면 배지용 한 줄 요약 — 지금 보고 있는 게 어느 엔진의 거래인지."""
        entry = "B안(존-지정가)" if self.entry_mode == "zone_limit" else "A안(봉 마감 종가)"
        segment = self.segment if self.window == 0 else f"{self.segment}#{self.window}"
        return (
            f"{self.symbol} · {self.timeframe} · {segment} · {entry} · fill={self.fill} · "
            f"pos={self.position_mode} · {self.engine_version}@{self.revision}"
        )


@dataclass(frozen=True)
class RunSummary:
    """`list_runs`가 내는 한 줄 — 지문 + 요약 지표."""

    run_id: str
    fingerprint: RunFingerprint
    created_at: int
    num_trades: int
    total_return: float
    max_drawdown: float
    win_rate: float
    final_equity: float
    fill_rate: float | None
    eligible_setups: int | None
    num_filled: int | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id             TEXT    PRIMARY KEY,
    created_at         INTEGER NOT NULL,
    engine_version     TEXT    NOT NULL,
    revision           TEXT    NOT NULL,
    symbol             TEXT    NOT NULL,
    timeframe          TEXT    NOT NULL,
    segment            TEXT    NOT NULL,
    window             INTEGER NOT NULL,
    start_time         INTEGER,
    end_time           INTEGER,
    entry_mode         TEXT    NOT NULL,
    fill               TEXT    NOT NULL,
    seed               INTEGER NOT NULL,
    position_mode      TEXT    NOT NULL,
    portfolio_leverage REAL,
    confluence_json    TEXT    NOT NULL,
    order_block_json   TEXT    NOT NULL,
    config_json        TEXT    NOT NULL,
    initial_capital    REAL    NOT NULL,
    final_equity       REAL    NOT NULL,
    total_return       REAL    NOT NULL,
    max_drawdown       REAL    NOT NULL,
    win_rate           REAL    NOT NULL,
    num_trades         INTEGER NOT NULL,
    fill_rate          REAL,
    eligible_setups    INTEGER,
    num_filled         INTEGER
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    run_id         TEXT    NOT NULL,
    trade_no       INTEGER NOT NULL,
    side           TEXT    NOT NULL,
    entry_time     INTEGER NOT NULL,
    exit_time      INTEGER NOT NULL,
    entry_price    REAL    NOT NULL,
    quantity       REAL    NOT NULL,
    entry_notional REAL    NOT NULL,
    entry_fee      REAL    NOT NULL,
    exit_fees      REAL    NOT NULL,
    num_exits      INTEGER NOT NULL,
    exit_reason    TEXT    NOT NULL,
    funding_cost   REAL    NOT NULL,
    realized_pnl   REAL    NOT NULL,
    return_pct     REAL    NOT NULL,
    equity_before  REAL    NOT NULL,
    equity_after   REAL    NOT NULL,
    mfe_r          REAL,
    mae_r          REAL,
    PRIMARY KEY (run_id, trade_no)
);

CREATE TABLE IF NOT EXISTS backtest_trade_exits (
    run_id   TEXT    NOT NULL,
    trade_no INTEGER NOT NULL,
    exit_no  INTEGER NOT NULL,
    time     INTEGER NOT NULL,
    price    REAL    NOT NULL,
    quantity REAL    NOT NULL,
    fee      REAL    NOT NULL,
    reason   TEXT    NOT NULL,
    PRIMARY KEY (run_id, trade_no, exit_no)
);

CREATE TABLE IF NOT EXISTS backtest_setups (
    run_id       TEXT    NOT NULL,
    setup_no     INTEGER NOT NULL,
    trigger_time INTEGER NOT NULL,
    tap_bar_time INTEGER NOT NULL,
    side         TEXT    NOT NULL,
    tap_close    REAL    NOT NULL,
    limit_price  REAL,
    stop_price   REAL    NOT NULL,
    filled       INTEGER NOT NULL,
    dropped      INTEGER NOT NULL,
    status       TEXT    NOT NULL,
    tap_index    INTEGER NOT NULL,
    PRIMARY KEY (run_id, setup_no)
);

CREATE TABLE IF NOT EXISTS backtest_equity (
    run_id   TEXT    NOT NULL,
    point_no INTEGER NOT NULL,
    time     INTEGER NOT NULL,
    equity   REAL    NOT NULL,
    PRIMARY KEY (run_id, point_no)
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_reason
    ON backtest_trades (run_id, exit_reason);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_entry
    ON backtest_trades (run_id, entry_time);
CREATE INDEX IF NOT EXISTS idx_backtest_setups_filled
    ON backtest_setups (run_id, filled);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_symbol
    ON backtest_runs (symbol, timeframe);
"""

_RUN_COLUMNS: tuple[str, ...] = (
    "run_id",
    "created_at",
    "engine_version",
    "revision",
    "symbol",
    "timeframe",
    "segment",
    "window",
    "start_time",
    "end_time",
    "entry_mode",
    "fill",
    "seed",
    "position_mode",
    "portfolio_leverage",
    "confluence_json",
    "order_block_json",
    "config_json",
    "initial_capital",
    "final_equity",
    "total_return",
    "max_drawdown",
    "win_rate",
    "num_trades",
    "fill_rate",
    "eligible_setups",
    "num_filled",
)

#: 미체결 셋업 표(`setups_frame`)의 컬럼. 화면·CSV가 같은 이름을 쓴다.
SETUP_COLUMNS: tuple[str, ...] = (
    "setup_no",
    "trigger_time",
    "tap_bar_time",
    "side",
    "tap_close",
    "limit_price",
    "stop_price",
    "filled",
    "dropped",
    "status",
    "tap_index",
)


class BacktestRunStore:
    """백테스트 실행·거래·미체결 셋업·시드곡선을 담는 SQLite 저장소.

    `OhlcvStore`와 같은 DB 파일을 써도 되고(테이블 이름이 겹치지 않는다) 따로 둬도 된다.
    스레드 안전을 위해 `OhlcvStore`와 같은 방식(`check_same_thread=False` + 락)을 쓴다.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def __enter__(self) -> BacktestRunStore:
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

    def save_run(
        self,
        fingerprint: RunFingerprint,
        result: BacktestResult,
        *,
        stats: ZoneLimitStats | None = None,
        setups: Sequence[SetupDiagnostic] = (),
        replace: bool = False,
        created_at: int = 0,
    ) -> str:
        """한 실행의 거래·미체결 셋업·시드곡선을 적재하고 `run_id`를 반환한다.

        같은 지문이 이미 있으면 `DuplicateRunError`다 — 조용히 중복으로 쌓지도, 조용히
        덮어쓰지도 않는다(`replace=True`가 명시적 덮어쓰기). 같은 (심볼·TF·창)이라도
        설정이 다르면 지문이 달라 **다른 행**이 되므로 두 실행이 섞이지 않는다.
        """
        run_id = fingerprint.run_id
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists is not None:
                if not replace:
                    raise DuplicateRunError(
                        f"같은 실행 지문이 이미 적재돼 있습니다(run_id={run_id}). "
                        "덮어쓰려면 replace=True(CLI: --persist-replace)를 명시하세요."
                    )
                self._delete_locked(run_id)
            self._conn.execute(
                f"INSERT INTO backtest_runs ({', '.join(_RUN_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_RUN_COLUMNS))})",
                self._run_row(fingerprint, result, stats=stats, created_at=created_at),
            )
            self._conn.executemany(
                "INSERT INTO backtest_trades VALUES (" + ", ".join("?" * 19) + ")",
                list(_trade_rows(run_id, result)),
            )
            self._conn.executemany(
                "INSERT INTO backtest_trade_exits VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                list(_exit_rows(run_id, result)),
            )
            self._conn.executemany(
                "INSERT INTO backtest_setups VALUES (" + ", ".join("?" * 12) + ")",
                list(_setup_rows(run_id, setups)),
            )
            self._conn.executemany(
                "INSERT INTO backtest_equity VALUES (?, ?, ?, ?)",
                [
                    (run_id, no, point.time, point.equity)
                    for no, point in enumerate(result.equity_curve)
                ],
            )
        return run_id

    def _run_row(
        self,
        fp: RunFingerprint,
        result: BacktestResult,
        *,
        stats: ZoneLimitStats | None,
        created_at: int,
    ) -> tuple[object, ...]:
        m = result.metrics
        return (
            fp.run_id,
            created_at,
            fp.engine_version,
            fp.revision,
            fp.symbol,
            fp.timeframe,
            fp.segment,
            fp.window,
            fp.start_time,
            fp.end_time,
            fp.entry_mode,
            fp.fill,
            fp.seed,
            fp.position_mode,
            fp.portfolio_leverage,
            fp.confluence_json,
            fp.order_block_json,
            fp.config_json,
            m.initial_capital,
            m.final_equity,
            m.total_return,
            m.max_drawdown,
            m.win_rate,
            m.num_trades,
            None if stats is None else stats.fill_rate,
            None if stats is None else stats.eligible,
            None if stats is None else stats.filled,
        )

    def _delete_locked(self, run_id: str) -> None:
        for table in (
            "backtest_equity",
            "backtest_setups",
            "backtest_trade_exits",
            "backtest_trades",
            "backtest_runs",
        ):
            self._conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))

    def delete_run(self, run_id: str) -> None:
        """한 실행의 모든 행을 지운다(없으면 조용히 통과)."""
        with self._lock, self._conn:
            self._delete_locked(run_id)

    # ------------------------------------------------------------------ 조회

    def list_runs(
        self, *, symbol: str | None = None, timeframe: str | None = None
    ) -> list[RunSummary]:
        """적재된 실행 목록(최근 적재 순). 심볼·TF로 좁힐 수 있다."""
        clauses: list[str] = []
        params: list[object] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if timeframe is not None:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM backtest_runs{where} "
            "ORDER BY created_at DESC, symbol ASC, timeframe ASC, segment ASC"
        )
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_summary_from_row(dict(zip(_RUN_COLUMNS, row, strict=True))) for row in rows]

    def fingerprint(self, run_id: str) -> RunFingerprint:
        """`run_id`의 실행 지문. 없으면 `UnknownRunError`."""
        return self.summary(run_id).fingerprint

    def summary(self, run_id: str) -> RunSummary:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(_RUN_COLUMNS)} FROM backtest_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise UnknownRunError(
                f"실행 지문이 없는 run_id입니다: {run_id!r}. "
                "지문 없이 적재된 거래는 어느 엔진의 것인지 알 수 없어 조회하지 않습니다."
            )
        return _summary_from_row(dict(zip(_RUN_COLUMNS, row, strict=True)))

    def load_result(self, run_id: str) -> BacktestResult:
        """적재된 거래를 `BacktestResult`로 **복원**한다(계산 없음).

        거래·청산 체결은 **원본 그대로** 돌아오고(부분 청산 포함), 시드곡선·지표는
        `build_result_from_trades`가 그 거래에서 다시 만든다.

        ⚠️ **지표의 정본은 이 결과가 아니라 적재된 요약(`summary`)이다.** 지정가(B안)는
        엔진 자체가 `build_result_from_trades`로 결과를 만들므로 둘이 같지만, 종가(A안)
        엔진의 자본곡선은 **봉 단위**라 거래 단위로 다시 만든 곡선과 **MDD·Sharpe가
        다르다**(최종 시드·거래 수·수익률은 같다). 화면은 요약 지표를 쓰고, 이 결과는
        거래 표·차트 마커에 쓴다 — 원본 봉 단위 곡선이 필요하면 `equity_frame`이 그대로
        갖고 있다.
        """
        summary = self.summary(run_id)
        cfg = BacktestConfig.model_validate_json(summary.fingerprint.config_json)
        trades = self._load_trades(run_id)
        return build_result_from_trades(
            trades,
            cfg,
            summary.fingerprint.timeframe,
            funding_coverage_value=None,
        )

    def _load_trades(self, run_id: str) -> list[Trade]:
        with self._lock:
            trade_rows = self._conn.execute(
                "SELECT trade_no, side, entry_time, entry_price, quantity, entry_fee, "
                "funding_cost, realized_pnl, return_pct, mfe_r, mae_r "
                "FROM backtest_trades WHERE run_id = ? ORDER BY trade_no",
                (run_id,),
            ).fetchall()
            exit_rows = self._conn.execute(
                "SELECT trade_no, time, price, quantity, fee, reason "
                "FROM backtest_trade_exits WHERE run_id = ? ORDER BY trade_no, exit_no",
                (run_id,),
            ).fetchall()
        exits: dict[int, list[TradeFill]] = {}
        for trade_no, time, price, quantity, fee, reason in exit_rows:
            exits.setdefault(int(trade_no), []).append(
                TradeFill(
                    time=int(time),
                    price=float(price),
                    quantity=float(quantity),
                    fee=float(fee),
                    reason=ExitReason(reason),
                )
            )
        trades: list[Trade] = []
        for row in trade_rows:
            no = int(row[0])
            trades.append(
                Trade(
                    side=PositionSide(row[1]),
                    entry_time=int(row[2]),
                    entry_price=float(row[3]),
                    quantity=float(row[4]),
                    entry_fee=float(row[5]),
                    exits=exits.get(no, []),
                    funding_cost=float(row[6]),
                    realized_pnl=float(row[7]),
                    return_pct=float(row[8]),
                    mfe_r=None if row[9] is None else float(row[9]),
                    mae_r=None if row[10] is None else float(row[10]),
                )
            )
        return trades

    def setups_frame(self, run_id: str, *, only_unfilled: bool = False) -> pd.DataFrame:
        """미체결 셋업을 포함한 셋업 표 (`filled`로 체결·미체결을 가른다).

        "살 뻔했는데 못 산 자리"는 규칙을 판단하는 데 체결된 거래만큼 중요하다 —
        채택 엔진은 셋업의 약 20%가 여기로 빠진다(WAN-123 이후 체결률 ~81%).
        """
        self.summary(run_id)  # 지문 없는 run_id는 여기서 거부된다.
        query = f"SELECT {', '.join(SETUP_COLUMNS)} FROM backtest_setups WHERE run_id = ?"
        params: list[object] = [run_id]
        if only_unfilled:
            query += " AND filled = 0"
        query += " ORDER BY setup_no"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        frame = pd.DataFrame(rows, columns=list(SETUP_COLUMNS))
        if frame.empty:
            return frame
        frame["filled"] = frame["filled"].astype(bool)
        frame["dropped"] = frame["dropped"].astype(bool)
        return frame

    def equity_frame(self, run_id: str) -> pd.DataFrame:
        """적재된 시드곡선 (`time`, `equity`)."""
        self.summary(run_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT time, equity FROM backtest_equity WHERE run_id = ? ORDER BY point_no",
                (run_id,),
            ).fetchall()
        return pd.DataFrame(rows, columns=["time", "equity"])


# --------------------------------------------------------------------------- #
# 행 만들기 (모듈 함수 — 테스트가 저장소 없이도 검증할 수 있게)
# --------------------------------------------------------------------------- #


def _summary_from_row(row: dict[str, object]) -> RunSummary:
    fingerprint = RunFingerprint(
        symbol=str(row["symbol"]),
        timeframe=str(row["timeframe"]),
        segment=str(row["segment"]),
        window=int(str(row["window"])),
        start_time=None if row["start_time"] is None else int(str(row["start_time"])),
        end_time=None if row["end_time"] is None else int(str(row["end_time"])),
        entry_mode=str(row["entry_mode"]),
        fill=str(row["fill"]),
        seed=int(str(row["seed"])),
        position_mode=str(row["position_mode"]),
        portfolio_leverage=(
            None if row["portfolio_leverage"] is None else float(str(row["portfolio_leverage"]))
        ),
        confluence_json=str(row["confluence_json"]),
        order_block_json=str(row["order_block_json"]),
        config_json=str(row["config_json"]),
        engine_version=str(row["engine_version"]),
        revision=str(row["revision"]),
    )
    return RunSummary(
        run_id=str(row["run_id"]),
        fingerprint=fingerprint,
        created_at=int(str(row["created_at"])),
        num_trades=int(str(row["num_trades"])),
        total_return=float(str(row["total_return"])),
        max_drawdown=float(str(row["max_drawdown"])),
        win_rate=float(str(row["win_rate"])),
        final_equity=float(str(row["final_equity"])),
        fill_rate=None if row["fill_rate"] is None else float(str(row["fill_rate"])),
        eligible_setups=(
            None if row["eligible_setups"] is None else int(str(row["eligible_setups"]))
        ),
        num_filled=None if row["num_filled"] is None else int(str(row["num_filled"])),
    )


def _trade_rows(run_id: str, result: BacktestResult) -> Iterable[tuple[object, ...]]:
    """거래 행 — 시드 변화(`equity_before`→`equity_after`)를 함께 싣는다.

    시드 누적은 `trades_to_display_frame`(화면·CSV 공용)과 **같은 규칙**이다: 초기자본에서
    시작해 거래별 실현손익을 순서대로 더한다. 두 곳이 갈라지면 화면과 DB가 다른 숫자를
    내므로 회귀 테스트가 마지막 행의 일치를 고정한다.
    """
    equity = result.metrics.initial_capital
    for no, tr in enumerate(result.trades, start=1):
        before = equity
        after = before + tr.realized_pnl
        equity = after
        yield (
            run_id,
            no,
            tr.side.value,
            tr.entry_time,
            tr.exit_time,
            tr.entry_price,
            tr.quantity,
            tr.entry_price * tr.quantity,
            tr.entry_fee,
            sum(f.fee for f in tr.exits),
            len(tr.exits),
            tr.exits[-1].reason.value,
            tr.funding_cost,
            tr.realized_pnl,
            tr.return_pct,
            before,
            after,
            tr.mfe_r,
            tr.mae_r,
        )


def _exit_rows(run_id: str, result: BacktestResult) -> Iterable[tuple[object, ...]]:
    for no, tr in enumerate(result.trades, start=1):
        for exit_no, fill in enumerate(tr.exits):
            yield (
                run_id,
                no,
                exit_no,
                fill.time,
                fill.price,
                fill.quantity,
                fill.fee,
                fill.reason.value,
            )


def _setup_rows(run_id: str, setups: Sequence[SetupDiagnostic]) -> Iterable[tuple[object, ...]]:
    for no, setup in enumerate(setups, start=1):
        yield (
            run_id,
            no,
            setup.trigger_time,
            setup.tap_bar_time,
            setup.side.value,
            setup.tap_close,
            setup.limit_price,
            setup.stop_price,
            int(setup.filled),
            int(setup.dropped),
            setup.status.value,
            setup.tap_index,
        )
