"""엔진 C(시계열 모멘텀) 페이퍼 원장 — 매일 정산 · 목표 명목까지 증감 (WAN-451 ⑨).

사용자 결정(2026-10-14, WAN-451 §3 ⑨): 엔진 C는 「진입 → 청산」 왕복 1행인 `paper_trades`에
안 맞는다(며칠~몇 주를 들고 매일 크기만 바꾼다). 그래서 **리밸런싱 원장**을 따로 두고 자산마다
하루 1행을 남긴다. 그리고 하루 1회 **시가평가 자본**을 `paper_equity_marks`에 남긴다.

## 회계 — 매일 정산(daily settlement)

하루 한 번(00:00 UTC) 자산마다 순서대로:

1. **정산**: 어제 들고 있던 수량 `q`를 지난 가격 → 오늘 가격으로 평가한 손익 `q × (p − p_prev)`를
   실현으로 확정한다(선물 일일 정산과 같다). 그 보유 구간에 정산된 펀딩도 확정한다.
2. **목표**: `strategy.tsmom` 단일 소스로 정한 목표 명목 = `정산 후 총자본 × λ·k·0.5·w`.
3. **증감**: 목표 수량까지 사고판다. 비용 = **실제 거래 명목** × 9bp(`tsmom.COST`).

그래서 엔진 C 몫에는 **미실현 손익이 남지 않는다** — 매일 확정되어 지갑 자본에 바로 들어간다.
백테스트(`wan449.combine`)의 「총자본에 일 단위로 다시 맞춘다」와 같은 회계다.

⚠️ **백테스트와 딱 한 군데 다르다(알고 다르게 간다)** — 백테스트 비용은 `|Δw| × 9bp`(배율 변화)인데
여기는 **실제 거래 명목**에 9bp를 매긴다. 어제 포지션은 가격이 움직인 만큼 명목이 이미 달라져 있어
(drift) 실제로 거래할 양은 `|Δw|`와 조금 다르다. 페이퍼는 실매매를 흉내 내는 쪽이 맞으므로 실제
거래량을 쓴다. 차이는 2차 항(하루 수익 × 배율)이다.

⚠️ 체결가는 **방금 닫힌 일봉 D의 종가**다(= 00:00 UTC 직전 마지막 가격). 신호를 그 종가로 정하고
같은 가격에 거래한다고 보는 것은 백테스트(종가→종가 수익)와 같은 가정이다.

이 모듈은 저장·순수 계산만 한다. 러너 배선(스케줄 · 재시작 따라잡기 · 지갑 합산)은 다음 단계다.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from data.funding import cumulative_funding_cost
from data.models import FundingRate
from data.sqlite_util import configure_connection
from strategy import tsmom

_REBALANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tsmom_rebalances (
    asset           TEXT    NOT NULL,
    as_of_ms        INTEGER NOT NULL,
    executed_ms     INTEGER NOT NULL,
    price           REAL    NOT NULL,
    prev_price      REAL,
    signal          REAL,
    annual_vol      REAL,
    weight          REAL    NOT NULL,
    equity_basis    REAL    NOT NULL,
    target_notional REAL    NOT NULL,
    prev_qty        REAL    NOT NULL,
    new_qty         REAL    NOT NULL,
    traded_notional REAL    NOT NULL,
    settled_pnl     REAL    NOT NULL,
    funding         REAL    NOT NULL,
    cost            REAL    NOT NULL,
    PRIMARY KEY (asset, as_of_ms)
)
"""

_MARKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_equity_marks (
    mark_ms        INTEGER PRIMARY KEY,
    realized       REAL    NOT NULL,
    unrealized     REAL    NOT NULL,
    equity         REAL    NOT NULL,
    open_positions INTEGER NOT NULL
)
"""

_REBALANCE_COLUMNS = (
    "asset",
    "as_of_ms",
    "executed_ms",
    "price",
    "prev_price",
    "signal",
    "annual_vol",
    "weight",
    "equity_basis",
    "target_notional",
    "prev_qty",
    "new_qty",
    "traded_notional",
    "settled_pnl",
    "funding",
    "cost",
)

#: `paper-reset.sh`가 함께 지워야 하는 테이블(규칙이 바뀌는 리셋에서 장부와 같이 간다).
LEDGER_TABLES: tuple[str, ...] = ("tsmom_rebalances", "paper_equity_marks")


@dataclass(frozen=True)
class Holding:
    """한 자산의 현재 엔진 C 보유 — 직전 리밸런싱 행에서 복원한다."""

    asset: str
    qty: float
    """부호 있는 수량(양수 롱 · 음수 숏)."""
    price: float
    """그 수량을 마지막으로 정산·거래한 가격."""
    as_of_ms: int


@dataclass(frozen=True)
class RebalanceRow:
    """하루 한 자산의 정산 + 증감 기록."""

    asset: str
    as_of_ms: int
    """신호를 정한 일봉 D의 `open_time`."""
    executed_ms: int
    """거래 시각 = D+1 00:00 UTC."""
    price: float
    prev_price: float | None
    signal: float
    annual_vol: float
    weight: float
    equity_basis: float
    """목표 명목을 정한 자본 = 이 정산 **후** 총자본."""
    target_notional: float
    """부호 있는 목표 명목(= equity_basis × λ·k·0.5·w)."""
    prev_qty: float
    new_qty: float
    traded_notional: float
    """|new − prev| × price (≥ 0)."""
    settled_pnl: float
    """어제 수량의 가격 손익 `prev_qty × (price − prev_price)`."""
    funding: float
    """보유 구간 펀딩 비용(양수 = 지불)."""
    cost: float
    """거래 비용 = traded_notional × 9bp."""

    @property
    def net_pnl(self) -> float:
        """이 행이 지갑에 더하는 금액 = 정산 손익 − 펀딩 − 비용."""
        return self.settled_pnl - self.funding - self.cost

    def as_row(self) -> tuple[object, ...]:
        return tuple(getattr(self, c) for c in _REBALANCE_COLUMNS)


def plan_rebalance(
    target: tsmom.TsmomTarget,
    *,
    price: float,
    equity_before: float,
    holding: Holding | None,
    funding_rates: Iterable[FundingRate] = (),
) -> RebalanceRow:
    """어제 보유를 정산하고 오늘 목표까지 증감한다 — 순수 계산(저장은 호출부).

    `equity_before`는 이 행을 반영하기 **전** 총자본이다(다른 자산·팔 손익은 이미 들어가 있다).
    목표 명목은 이 행의 정산 손익·펀딩을 더한 **정산 후** 자본으로 정한다(백테스트가 그날 수익을
    반영한 자본에 배율을 곱하는 것과 같다). 비용은 그 뒤에 뺀다.

    `funding_rates`는 보유 구간 `[holding.as_of 다음 날 00:00, 지금)`에 **정산된** 펀딩이어야 한다
    (`data.funding.settled_funding_rates`가 고른 것 — 예측 대체 행 포함).
    """
    if price <= 0:
        raise ValueError(f"{target.asset}: 가격이 비양수다({price})")
    if equity_before <= 0:
        raise ValueError(f"{target.asset}: 자본이 비양수다({equity_before}) — 크기를 정할 수 없다")
    executed_ms = target.effective_from_ms
    if holding is not None:
        if holding.asset != target.asset:
            raise ValueError(f"보유 자산 {holding.asset} ≠ 목표 자산 {target.asset}")
        if holding.as_of_ms >= target.as_of_ms:
            raise ValueError(
                f"{target.asset}: 이미 as_of={holding.as_of_ms}까지 정산했다 — "
                f"as_of={target.as_of_ms}를 다시 정산하면 이중 계상이다"
            )
    prev_qty = holding.qty if holding is not None else 0.0
    prev_price = holding.price if holding is not None else None

    settled = prev_qty * (price - prev_price) if prev_price is not None else 0.0
    funding = 0.0
    if prev_qty != 0.0 and prev_price is not None and holding is not None:
        funding = cumulative_funding_cost(
            funding_rates,
            position_notional=abs(prev_qty) * prev_price,
            direction="long" if prev_qty > 0 else "short",
            start_ms=holding.as_of_ms + tsmom.DAY_MS,
            end_ms=executed_ms,
            include_predicted=True,
        )

    basis = equity_before + settled - funding
    target_notional = basis * target.notional_fraction
    new_qty = target_notional / price
    traded = abs(new_qty - prev_qty) * price
    return RebalanceRow(
        asset=target.asset,
        as_of_ms=target.as_of_ms,
        executed_ms=executed_ms,
        price=price,
        prev_price=prev_price,
        signal=target.signal,
        annual_vol=target.annual_vol,
        weight=target.weight,
        equity_basis=basis,
        target_notional=target_notional,
        prev_qty=prev_qty,
        new_qty=new_qty,
        traded_notional=traded,
        settled_pnl=settled,
        funding=funding,
        cost=traded * tsmom.COST,
    )


@dataclass(frozen=True)
class EquityMark:
    """하루 1회 시가평가 자본(⑨) — 실현 + 열린 팔 포지션의 미실현."""

    mark_ms: int
    realized: float
    """초기 자본 + 팔 실현 손익 합 + 엔진 C 정산 손익 합."""
    unrealized: float
    """열린 팔 포지션을 그 시각 가격으로 평가한 손익(엔진 C는 매일 정산이라 0)."""
    open_positions: int

    @property
    def equity(self) -> float:
        return self.realized + self.unrealized


class TsmomLedgerStore:
    """엔진 C 원장 · 시가평가 자본의 SQLite 래퍼(`PaperTradeStore`와 같은 DB 파일 · 같은 규약)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        configure_connection(self._conn)
        self._conn.execute(_REBALANCE_SCHEMA)
        self._conn.execute(_MARKS_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> TsmomLedgerStore:
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

    def append(self, row: RebalanceRow) -> None:
        """한 행을 넣는다 — 같은 (자산, as_of)가 이미 있으면 **거부한다**(덮으면 이중 계상)."""
        placeholders = ", ".join("?" for _ in _REBALANCE_COLUMNS)
        columns = ", ".join(_REBALANCE_COLUMNS)
        sql = f"INSERT INTO tsmom_rebalances ({columns}) VALUES ({placeholders})"
        try:
            with self._lock, self._conn:
                self._conn.execute(sql, row.as_row())
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"{row.asset}: as_of={row.as_of_ms} 리밸런싱이 이미 기록돼 있다 — 이중 계상 거부"
            ) from exc

    def holding(self, asset: str) -> Holding | None:
        """가장 최근 행의 수량·가격 = 지금 보유(재시작 복원). 기록이 없으면 None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT new_qty, price, as_of_ms FROM tsmom_rebalances WHERE asset = ? "
                "ORDER BY as_of_ms DESC LIMIT 1",
                (asset,),
            ).fetchone()
        if row is None:
            return None
        return Holding(asset=asset, qty=float(row[0]), price=float(row[1]), as_of_ms=int(row[2]))

    def total_net_pnl(self) -> float:
        """전 행의 지갑 기여 합(정산 손익 − 펀딩 − 비용) — 재시작 자본 복원에 더한다."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(settled_pnl - funding - cost), 0.0) FROM tsmom_rebalances"
            ).fetchone()
        return 0.0 if row is None else float(row[0])

    def rows(self, asset: str | None = None) -> list[RebalanceRow]:
        sql = f"SELECT {', '.join(_REBALANCE_COLUMNS)} FROM tsmom_rebalances"
        params: Sequence[object] = ()
        if asset is not None:
            sql += " WHERE asset = ?"
            params = (asset,)
        sql += " ORDER BY as_of_ms, asset"
        with self._lock:
            fetched = self._conn.execute(sql, params).fetchall()
        return [RebalanceRow(**dict(zip(_REBALANCE_COLUMNS, r, strict=True))) for r in fetched]

    def record_mark(self, mark: EquityMark) -> None:
        """시가평가 자본을 남긴다 — 같은 시각은 덮는다(같은 시각의 평가는 하나다)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO paper_equity_marks (mark_ms, realized, unrealized, equity, "
                "open_positions) VALUES (?, ?, ?, ?, ?) ON CONFLICT(mark_ms) DO UPDATE SET "
                "realized = excluded.realized, unrealized = excluded.unrealized, "
                "equity = excluded.equity, open_positions = excluded.open_positions",
                (mark.mark_ms, mark.realized, mark.unrealized, mark.equity, mark.open_positions),
            )

    def marks(self) -> list[EquityMark]:
        with self._lock:
            fetched = self._conn.execute(
                "SELECT mark_ms, realized, unrealized, open_positions FROM paper_equity_marks "
                "ORDER BY mark_ms"
            ).fetchall()
        return [
            EquityMark(
                mark_ms=int(m), realized=float(r), unrealized=float(u), open_positions=int(n)
            )
            for m, r, u, n in fetched
        ]
