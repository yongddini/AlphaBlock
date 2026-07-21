"""페이퍼 거래 영속 저장 (WAN-33).

WAN-25 페이퍼 러너가 청산한 가상 거래(`live.paper.ClosedTrade`)를 손익·비용과 함께
SQLite `paper_trades` 테이블에 거래 단위로 누적한다. `(symbol, timeframe, entry_time,
exit_time)`을 기본키로 UPSERT하므로 러너 재시작·재평가로 같은 거래가 다시 들어와도
중복되지 않는다.

## 손익·비용 모델 (WAN-20 재사용)

한 거래의 손익은 진입 노셔널 대비 **백분율(%)**로 저장한다.

- ``gross_pct`` = 방향을 반영한 가격 손익률(롱은 상승이 +, 숏은 하락이 +). 슬리피지 미반영.
- ``fee_pct``   = 왕복 수수료(진입+청산) 비용률. 공용 `CostModel`(WAN-37)이 주어지면
  메이커/테이커 구분과 실제 체결 노셔널을 반영하고, 없으면 ``2 × fee_rate × 100`` 근사.
- ``slippage_pct`` = 진입·청산 체결가 슬리피지 비용률(≥0). `CostModel`이 주어졌을 때만
  산정하며(테이커 체결에만 붙음), 없으면 0. 백테스트와 같은 산식을 쓴다.
- ``funding_pct`` = 보유 구간 `[진입, 청산)`에 정산된 펀딩비용률(WAN-16/WAN-20 모델).
  롱은 요율>0이 지불(+), 숏은 반대. 명목가는 구간 내 일정(=진입 노셔널)하다고 본다.
- ``net_pct`` = ``gross_pct − fee_pct − slippage_pct − funding_pct`` (모든 비용 반영 순손익률).

`CostModel`을 주면 이 순손익률은 **같은 진입/청산에 대해 백테스트 엔진의 실현손익률과
정확히 일치**한다(둘 다 `common.costs`의 한 산식을 쓰므로). 그래서 패리티 리포트의
잔여 차이는 비용 모델이 아니라 거래 선택·체결 타이밍에서만 비롯된다.

리스크(손절 거리) 기준 **R 배수**도 함께 저장한다: ``risk_pct = |진입가 − 손절가| /
진입가 × 100``, ``r_multiple = net_pct / risk_pct`` (손절 참조가가 없으면 둘 다 None).

## 열린 포지션 복구 (WAN-34)

실시간 러너가 execution 엔진으로 집행한 페이퍼 주문의 **열린 포지션**을 별도
``open_positions`` 테이블에 저장한다(진입 시 upsert, 청산 시 삭제). 러너 재시작 시
이 테이블을 읽어 엔진 장부를 복구하므로 청산 평가가 끊기지 않는다. 청산이 끝난
라운드트립은 위 ``paper_trades`` 스키마(``PaperTradeRecorder``)로 위임해 성과 집계와
같은 테이블에 누적한다 — 집행 결과가 곧바로 WAN-33 성과·패리티 리포트에 반영된다.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import BaseModel, ConfigDict

from common.costs import CostModel, Liquidity
from data.funding import Direction, FundingRateStore, cumulative_funding_cost
from data.models import FundingRate
from data.sqlite_util import configure_connection
from execution.models import Position
from live.paper import ClosedTrade
from strategy.models import OrderBlockDirection, SignalExitReason

_logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    symbol             TEXT    NOT NULL,
    timeframe          TEXT    NOT NULL,
    direction          TEXT    NOT NULL,
    entry_time         INTEGER NOT NULL,
    entry_price        REAL    NOT NULL,
    exit_time          INTEGER NOT NULL,
    exit_price         REAL    NOT NULL,
    reason             TEXT    NOT NULL,
    gross_pct          REAL    NOT NULL,
    fee_pct            REAL    NOT NULL,
    slippage_pct       REAL    NOT NULL DEFAULT 0,
    funding_pct        REAL    NOT NULL,
    net_pct            REAL    NOT NULL,
    risk_pct           REAL,
    r_multiple         REAL,
    stop_price         REAL,
    take_profit_price  REAL,
    PRIMARY KEY (symbol, timeframe, entry_time, exit_time)
)
"""

_UPSERT = """
INSERT INTO paper_trades
    (symbol, timeframe, direction, entry_time, entry_price, exit_time, exit_price,
     reason, gross_pct, fee_pct, slippage_pct, funding_pct, net_pct, risk_pct,
     r_multiple, stop_price, take_profit_price)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, timeframe, entry_time, exit_time) DO UPDATE SET
    direction         = excluded.direction,
    entry_price       = excluded.entry_price,
    exit_price        = excluded.exit_price,
    reason            = excluded.reason,
    gross_pct         = excluded.gross_pct,
    fee_pct           = excluded.fee_pct,
    slippage_pct      = excluded.slippage_pct,
    funding_pct       = excluded.funding_pct,
    net_pct           = excluded.net_pct,
    risk_pct          = excluded.risk_pct,
    r_multiple        = excluded.r_multiple,
    stop_price        = excluded.stop_price,
    take_profit_price = excluded.take_profit_price
"""

# `list_records`가 조회·반환하는 컬럼 순서.
_COLUMNS = [
    "symbol",
    "timeframe",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "reason",
    "gross_pct",
    "fee_pct",
    "slippage_pct",
    "funding_pct",
    "net_pct",
    "risk_pct",
    "r_multiple",
    "stop_price",
    "take_profit_price",
]

# 열린 페이퍼 포지션(재시작 복구용, WAN-34). (symbol, timeframe)당 최대 하나.
_OPEN_POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS open_positions (
    symbol            TEXT    NOT NULL,
    timeframe         TEXT    NOT NULL,
    direction         TEXT    NOT NULL,
    quantity          REAL    NOT NULL,
    entry_price       REAL    NOT NULL,
    entry_time        INTEGER NOT NULL,
    stop_price        REAL,
    take_profit_price REAL,
    risk_amount       REAL    NOT NULL DEFAULT 0,
    entry_fee         REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, timeframe)
)
"""

_OPEN_UPSERT = """
INSERT INTO open_positions
    (symbol, timeframe, direction, quantity, entry_price, entry_time,
     stop_price, take_profit_price, risk_amount, entry_fee)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, timeframe) DO UPDATE SET
    direction         = excluded.direction,
    quantity          = excluded.quantity,
    entry_price       = excluded.entry_price,
    entry_time        = excluded.entry_time,
    stop_price        = excluded.stop_price,
    take_profit_price = excluded.take_profit_price,
    risk_amount       = excluded.risk_amount,
    entry_fee         = excluded.entry_fee
"""


class OpenPosition(BaseModel):
    """DB에 영속된 열린 페이퍼 포지션 한 건(엔진 복구용, WAN-34).

    진입 시점에 감수한 리스크 금액·진입 수수료를 함께 담는다(재시작 후에도 청산
    라운드트립을 온전히 복원하기 위함).
    """

    model_config = ConfigDict(frozen=True)

    position: Position
    risk_amount: float
    entry_fee: float


class PaperTradeRecord(BaseModel):
    """청산 완료된 페이퍼 거래 하나(진입→익절/손절). `paper_trades` 한 행."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    reason: SignalExitReason
    gross_pct: float
    """방향을 반영한 가격 손익률(%). 슬리피지·수수료 미반영."""
    fee_pct: float
    """왕복 수수료 비용률(%). 항상 ≥0."""
    slippage_pct: float = 0.0
    """슬리피지 비용률(%). 항상 ≥0. 비용 모델 미지정 시 0."""
    funding_pct: float
    """보유 구간 펀딩비용률(%). 양수=순지불, 음수=순수취."""
    net_pct: float
    """모든 비용을 반영한 순손익률(%). = gross_pct − fee_pct − slippage_pct − funding_pct."""
    risk_pct: float | None = None
    """손절 거리(진입가 대비 %). 손절 참조가가 없으면 None."""
    r_multiple: float | None = None
    """리스크 대비 손익 배수(net_pct / risk_pct). risk_pct가 없으면 None."""
    stop_price: float | None = None
    take_profit_price: float | None = None

    @property
    def is_win(self) -> bool:
        """순손익 기준 승리 여부."""
        return self.net_pct > 0.0

    def as_row(self) -> tuple[object, ...]:
        """SQLite 저장용 튜플(`_COLUMNS` 순서). enum은 값 문자열로 직렬화."""
        return (
            self.symbol,
            self.timeframe,
            self.direction.value,
            self.entry_time,
            self.entry_price,
            self.exit_time,
            self.exit_price,
            self.reason.value,
            self.gross_pct,
            self.fee_pct,
            self.slippage_pct,
            self.funding_pct,
            self.net_pct,
            self.risk_pct,
            self.r_multiple,
            self.stop_price,
            self.take_profit_price,
        )


def _round_trip_fee_pct(fee_rate: float) -> float:
    """왕복(진입+청산) 수수료 비용률(%). 진입 노셔널 대비 근사."""
    return 2.0 * fee_rate * 100.0


def build_record(
    trade: ClosedTrade,
    *,
    fee_rate: float = 0.0,
    cost_model: CostModel | None = None,
    entry_liquidity: Liquidity = Liquidity.TAKER,
    exit_liquidity: Liquidity = Liquidity.TAKER,
    funding_rates: list[FundingRate] | None = None,
    include_predicted: bool = False,
) -> PaperTradeRecord:
    """`ClosedTrade`를 손익·비용을 산출해 `PaperTradeRecord`로 변환한다.

    비용 모델은 두 가지로 줄 수 있다:

    * ``cost_model``(WAN-37, 권장): 공용 `CostModel`로 메이커/테이커 수수료와
      슬리피지를 백테스트와 **같은 산식**으로 반영한다. 진입·청산 유동성 구분
      (``entry_liquidity``/``exit_liquidity``)을 함께 지정한다(시장가 진입=taker,
      지정가 진입=maker). 이 경로의 순손익률은 백테스트 실현손익률과 정확히 일치한다.
    * ``fee_rate``(레거시): 한 방향 수수료율을 왕복(``2 × fee_rate``)으로만 근사하고
      슬리피지는 반영하지 않는다(``cost_model``이 None일 때만 사용).

    `funding_rates`가 주어지면 보유 구간 `[진입, 청산)`의 누적 펀딩비용률을 반영한다
    (WAN-20 모델). 없으면 펀딩비용 0으로 둔다.
    """
    position = trade.position

    if cost_model is not None:
        breakdown = cost_model.trade_costs(
            position.entry_price,
            trade.exit_price,
            is_long=position.is_long,
            entry_liquidity=entry_liquidity,
            exit_liquidity=exit_liquidity,
        )
        gross_pct = breakdown.gross_frac * 100.0
        fee_pct = breakdown.fee_frac * 100.0
        slippage_pct = breakdown.slippage_frac * 100.0
    else:
        gross_pct = trade.realized_pct
        fee_pct = _round_trip_fee_pct(fee_rate)
        slippage_pct = 0.0

    funding_pct = 0.0
    if funding_rates:
        direction: Direction = "long" if position.is_long else "short"
        # 명목가 1.0에 대한 누적 펀딩비용 = 노셔널 대비 분수 → %로 변환.
        funding_frac = cumulative_funding_cost(
            funding_rates,
            position_notional=1.0,
            direction=direction,
            start_ms=position.entry_time,
            end_ms=trade.exit_time,
            include_predicted=include_predicted,
        )
        funding_pct = funding_frac * 100.0

    net_pct = gross_pct - fee_pct - slippage_pct - funding_pct

    risk_pct: float | None = None
    r_multiple: float | None = None
    stop = position.stop_price
    if stop is not None and position.entry_price > 0.0:
        risk_pct = abs(position.entry_price - stop) / position.entry_price * 100.0
        if risk_pct > 0.0:
            r_multiple = net_pct / risk_pct
        else:
            risk_pct = None

    return PaperTradeRecord(
        symbol=position.symbol,
        timeframe=position.timeframe,
        direction=position.direction,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=trade.exit_time,
        exit_price=trade.exit_price,
        reason=trade.reason,
        gross_pct=gross_pct,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        funding_pct=funding_pct,
        net_pct=net_pct,
        risk_pct=risk_pct,
        r_multiple=r_multiple,
        stop_price=stop,
        take_profit_price=position.take_profit_price,
    )


class PaperTradeStore:
    """페이퍼 거래를 저장·조회하는 SQLite 래퍼.

    `(symbol, timeframe, entry_time, exit_time)`을 기본키로 UPSERT하므로 재기록이
    무해하다. OHLCV 저장소(WAN-6)와 같은 DB 파일(`ALPHABLOCK_DB_PATH`)에 저장할 수
    있다. 러너(쓰기)와 대시보드/리포트(읽기)가 각각 별도 프로세스에서 접근할 수
    있으므로 커넥션을 `check_same_thread=False`로 열고 접근을 락으로 직렬화한다.

    컨텍스트 매니저로 사용할 수 있다::

        with PaperTradeStore("data/ohlcv.db") as store:
            store.upsert_record(record)
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        configure_connection(self._conn)  # WAL + busy_timeout (WAN-156 §4)
        self._conn.execute(_SCHEMA)
        self._conn.execute(_OPEN_POSITIONS_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """이전 스키마의 DB에 신규 컬럼을 추가한다(WAN-37, idempotent).

        `slippage_pct`는 나중에 도입됐으므로, 기존 `paper_trades` 테이블에 없으면
        기본값 0으로 채운다. `CREATE TABLE IF NOT EXISTS`는 기존 테이블에 컬럼을 더하지
        않으므로 명시적 `ALTER`가 필요하다.
        """
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(paper_trades)")}
        if "slippage_pct" not in cols:
            self._conn.execute(
                "ALTER TABLE paper_trades ADD COLUMN slippage_pct REAL NOT NULL DEFAULT 0"
            )

    def __enter__(self) -> PaperTradeStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def upsert_record(self, record: PaperTradeRecord) -> None:
        """페이퍼 거래 한 건을 UPSERT한다."""
        with self._lock, self._conn:
            self._conn.execute(_UPSERT, record.as_row())

    # -- 열린 포지션 (재시작 복구, WAN-34) ----------------------------------

    def record_open(self, position: Position, *, risk_amount: float, entry_fee: float) -> None:
        """진입 체결을 열린 포지션으로 저장한다(같은 시리즈면 덮어쓴다)."""
        with self._lock, self._conn:
            self._conn.execute(
                _OPEN_UPSERT,
                (
                    position.symbol,
                    position.timeframe,
                    position.direction.value,
                    position.quantity,
                    position.entry_price,
                    position.entry_time,
                    position.stop_price,
                    position.take_profit_price,
                    risk_amount,
                    entry_fee,
                ),
            )

    def load_open_positions(self) -> list[OpenPosition]:
        """저장된 열린 포지션을 모두 읽어 반환한다(재시작 복구용)."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT symbol, timeframe, direction, quantity, entry_price, entry_time,
                       stop_price, take_profit_price, risk_amount, entry_fee
                FROM open_positions
                ORDER BY symbol, timeframe
                """
            )
            rows = cur.fetchall()
        return [
            OpenPosition(
                position=Position(
                    symbol=str(row[0]),
                    timeframe=str(row[1]),
                    direction=OrderBlockDirection(str(row[2])),
                    quantity=float(row[3]),
                    entry_price=float(row[4]),
                    entry_time=int(row[5]),
                    stop_price=None if row[6] is None else float(row[6]),
                    take_profit_price=None if row[7] is None else float(row[7]),
                ),
                risk_amount=float(row[8]),
                entry_fee=float(row[9]),
            )
            for row in rows
        ]

    def remove_open_position(self, symbol: str, timeframe: str) -> None:
        """청산·취소된 시리즈의 열린 포지션을 삭제한다(없으면 무해)."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM open_positions WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            )

    def count(self, symbol: str | None = None, timeframe: str | None = None) -> int:
        """저장된 페이퍼 거래 수. 심볼·타임프레임으로 선택 필터링."""
        clauses: list[str] = []
        params: list[object] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if timeframe is not None:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            cur = self._conn.execute(f"SELECT COUNT(*) FROM paper_trades{where}", params)
            (value,) = cur.fetchone()
        return int(value)

    def list_series(self) -> list[tuple[str, str]]:
        """거래가 저장된 (symbol, timeframe) 조합을 정렬해 반환한다."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT symbol, timeframe FROM paper_trades ORDER BY symbol, timeframe"
            )
            rows = cur.fetchall()
        return [(row[0], row[1]) for row in rows]

    def list_records(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[PaperTradeRecord]:
        """저장된 거래를 `entry_time` 오름차순으로 조회한다.

        `start_ms`/`end_ms`(배타적)는 **진입 시각** 기준으로 필터링한다.
        """
        query = "SELECT " + ", ".join(_COLUMNS) + " FROM paper_trades"
        clauses: list[str] = []
        params: list[object] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if timeframe is not None:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        if start_ms is not None:
            clauses.append("entry_time >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("entry_time < ?")
            params.append(end_ms)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY entry_time ASC, exit_time ASC"

        with self._lock:
            cur = self._conn.execute(query, params)
            rows = cur.fetchall()
        return [_row_to_record(row) for row in rows]

    def time_span(self) -> tuple[int, int] | None:
        """저장된 거래의 (최소 진입시각, 최대 청산시각). 거래가 없으면 None."""
        with self._lock:
            cur = self._conn.execute("SELECT MIN(entry_time), MAX(exit_time) FROM paper_trades")
            lo, hi = cur.fetchone()
        if lo is None or hi is None:
            return None
        return int(lo), int(hi)

    def close(self) -> None:
        """연결을 닫는다."""
        with self._lock:
            self._conn.close()


def _row_to_record(row: Sequence[Any]) -> PaperTradeRecord:
    """`_COLUMNS` 순서의 SQLite 행을 `PaperTradeRecord`로 변환한다."""
    (
        symbol,
        timeframe,
        direction,
        entry_time,
        entry_price,
        exit_time,
        exit_price,
        reason,
        gross_pct,
        fee_pct,
        slippage_pct,
        funding_pct,
        net_pct,
        risk_pct,
        r_multiple,
        stop_price,
        take_profit_price,
    ) = row
    return PaperTradeRecord(
        symbol=str(symbol),
        timeframe=str(timeframe),
        direction=OrderBlockDirection(str(direction)),
        entry_time=int(entry_time),
        entry_price=float(entry_price),
        exit_time=int(exit_time),
        exit_price=float(exit_price),
        reason=SignalExitReason(str(reason)),
        gross_pct=float(gross_pct),
        fee_pct=float(fee_pct),
        slippage_pct=float(slippage_pct),
        funding_pct=float(funding_pct),
        net_pct=float(net_pct),
        risk_pct=None if risk_pct is None else float(risk_pct),
        r_multiple=None if r_multiple is None else float(r_multiple),
        stop_price=None if stop_price is None else float(stop_price),
        take_profit_price=(None if take_profit_price is None else float(take_profit_price)),
    )


class PaperTradeRecorder:
    """`ClosedTrade`를 손익·비용을 산출해 `PaperTradeStore`에 영속화하는 싱크.

    러너(`live.runner`)가 청산을 낼 때마다 `Notifier`가 이 싱크의 `record`를 호출한다.
    저장 실패가 알림·폴링을 막지 않도록 예외를 삼키고 로그만 남긴다(러너 견고성).

    펀딩비용은 `funding_store`가 주어졌을 때만 반영한다(WAN-16 수집분). 없으면 0으로
    둔다. 비용 모델은 `cost_model`(공용 `CostModel`, WAN-37)을 주면 메이커/테이커
    수수료·슬리피지를 백테스트와 같은 산식으로 반영하고, 없으면 `fee_rate`(레거시)만
    왕복으로 근사한다. 실시간 러너의 페이퍼 진입/청산은 시장가라 기본 유동성은 taker다.
    """

    def __init__(
        self,
        store: PaperTradeStore,
        *,
        fee_rate: float = 0.0,
        cost_model: CostModel | None = None,
        entry_liquidity: Liquidity = Liquidity.TAKER,
        exit_liquidity: Liquidity = Liquidity.TAKER,
        funding_store: FundingRateStore | None = None,
        include_predicted: bool = False,
    ) -> None:
        self._store = store
        self._fee_rate = fee_rate
        self._cost_model = cost_model
        self._entry_liquidity = entry_liquidity
        self._exit_liquidity = exit_liquidity
        self._funding_store = funding_store
        self._include_predicted = include_predicted

    def record(self, trade: ClosedTrade) -> PaperTradeRecord | None:
        """청산 거래를 기록한다. 성공 시 저장된 레코드, 실패 시 None."""
        try:
            funding_rates: list[FundingRate] | None = None
            if self._funding_store is not None:
                funding_rates = self._funding_store.get_rates(
                    trade.position.symbol,
                    start_ms=trade.position.entry_time,
                    end_ms=trade.exit_time,
                    include_predicted=self._include_predicted,
                )
            record = build_record(
                trade,
                fee_rate=self._fee_rate,
                cost_model=self._cost_model,
                entry_liquidity=self._entry_liquidity,
                exit_liquidity=self._exit_liquidity,
                funding_rates=funding_rates,
                include_predicted=self._include_predicted,
            )
            self._store.upsert_record(record)
            return record
        except Exception:  # noqa: BLE001 — 기록 실패가 러너 루프를 멈추지 않도록.
            _logger.exception(
                "페이퍼 거래 기록 실패: %s %s",
                trade.position.symbol,
                trade.position.timeframe,
            )
            return None
