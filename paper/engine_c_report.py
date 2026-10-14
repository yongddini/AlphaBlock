"""엔진 C(추세추종) 표시용 조회·요약 — 다이제스트·대시보드가 같이 쓴다 (WAN-451 ⑩).

엔진 C는 `paper_trades`(왕복 1행)가 아니라 `tsmom_rebalances`(하루 자산당 1행)와
`paper_equity_marks`(하루 1회 시가평가 자본)에 기록된다. 다이제스트·대시보드가 `paper_trades`만
읽으면 엔진 C가 **화면에서 통째로 사라지고**, 팔 거래가 없는 날(② 단계의 보통 날)에는 「거래
없음」만 보인다 — 그래서 이 모듈이 그 두 테이블을 읽어 요약을 낸다.

🚨 **읽기 전용이다** — 테이블이 없으면(엔진 C를 한 번도 안 켠 DB) 빈 결과를 낸다. 조회가 테이블을
만들면 안 된다(`TsmomLedgerStore`는 쓰기용이라 생성 시 테이블을 만든다 — 여기서 쓰지 않는다).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from common.timefmt import format_kst
from paper.tsmom_ledger import REBALANCE_COLUMNS, EquityMark, Holding, RebalanceRow


@dataclass(frozen=True)
class EngineCView:
    """엔진 C 기록 한 묶음(기간 필터는 `rows`·`marks`에만 걸린다)."""

    rows: list[RebalanceRow] = field(default_factory=list)
    """기간 안의 리밸런싱 행(거래 시각 기준) — `as_of` 오름차순."""
    marks: list[EquityMark] = field(default_factory=list)
    """기간 안의 시가평가 자본 — 시각 오름차순."""
    holdings: list[Holding] = field(default_factory=list)
    """**지금** 보유(기간과 무관 · 자산별 마지막 행)."""
    total_net_pnl: float = 0.0
    """**전 기간** 지갑 기여 합(정산 손익 − 펀딩 − 비용) — 지갑 잔고 재구성용."""

    @property
    def empty(self) -> bool:
        return not self.rows and not self.holdings and not self.marks


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def read_engine_c(
    db_path: str | Path, *, since_ms: int | None = None, until_ms: int | None = None
) -> EngineCView:
    """엔진 C 기록을 읽는다(읽기 전용 · 테이블·파일이 없으면 빈 결과)."""
    path = Path(db_path)
    if str(db_path) != ":memory:" and not path.exists():
        return EngineCView()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows: list[RebalanceRow] = []
        holdings: list[Holding] = []
        total = 0.0
        if _has_table(conn, "tsmom_rebalances"):
            sql = f"SELECT {', '.join(REBALANCE_COLUMNS)} FROM tsmom_rebalances"
            clauses: list[str] = []
            params: list[int] = []
            if since_ms is not None:
                clauses.append("executed_ms >= ?")
                params.append(since_ms)
            if until_ms is not None:
                clauses.append("executed_ms < ?")
                params.append(until_ms)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY as_of_ms, asset"
            rows = [
                RebalanceRow(**dict(zip(REBALANCE_COLUMNS, r, strict=True)))
                for r in conn.execute(sql, params).fetchall()
            ]
            for asset, qty, price, as_of in conn.execute(
                "SELECT asset, new_qty, price, as_of_ms FROM tsmom_rebalances t WHERE as_of_ms = "
                "(SELECT MAX(as_of_ms) FROM tsmom_rebalances u WHERE u.asset = t.asset) "
                "ORDER BY asset"
            ).fetchall():
                holdings.append(Holding(str(asset), float(qty), float(price), int(as_of)))
            got = conn.execute(
                "SELECT COALESCE(SUM(settled_pnl - funding - cost), 0.0) FROM tsmom_rebalances"
            ).fetchone()
            total = 0.0 if got is None else float(got[0])
        marks: list[EquityMark] = []
        if _has_table(conn, "paper_equity_marks"):
            sql = "SELECT mark_ms, realized, unrealized, open_positions FROM paper_equity_marks"
            m_clauses: list[str] = []
            m_params: list[int] = []
            if since_ms is not None:
                m_clauses.append("mark_ms >= ?")
                m_params.append(since_ms)
            if until_ms is not None:
                m_clauses.append("mark_ms < ?")
                m_params.append(until_ms)
            if m_clauses:
                sql += " WHERE " + " AND ".join(m_clauses)
            sql += " ORDER BY mark_ms"
            marks = [
                EquityMark(int(m), float(r), float(u), int(n))
                for m, r, u, n in conn.execute(sql, m_params).fetchall()
            ]
        return EngineCView(rows=rows, marks=marks, holdings=holdings, total_net_pnl=total)
    finally:
        conn.close()


@dataclass(frozen=True)
class MarkStats:
    """시가평가 자본 곡선 요약."""

    first: float
    last: float
    change_pct: float
    max_drawdown_pct: float


def mark_stats(marks: Sequence[EquityMark]) -> MarkStats | None:
    """기간 안 시가평가 자본의 변화율·최대 낙폭(하루 1회 평가 기준). 없으면 None."""
    if not marks:
        return None
    equities = [m.equity for m in marks]
    peak = equities[0]
    mdd = 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    first, last = equities[0], equities[-1]
    change = (last / first - 1.0) * 100.0 if first > 0 else 0.0
    return MarkStats(first=first, last=last, change_pct=change, max_drawdown_pct=mdd * 100.0)


def _short(asset: str) -> str:
    return asset.split("/")[0]


def _side(qty: float) -> str:
    return "롱" if qty > 0 else ("숏" if qty < 0 else "없음")


def engine_c_digest_lines(view: EngineCView) -> list[str]:
    """다이제스트에 붙일 엔진 C 줄들(텔레그램 Markdown — 밑줄·별표 없는 값만 넣는다)."""
    if view.empty:
        return []
    lines = ["*엔진 C (추세추종)*"]
    if view.holdings:
        last_weight = {r.asset: r.weight for r in view.rows}
        parts = []
        for h in view.holdings:
            w = last_weight.get(h.asset)
            wtxt = "" if w is None else f" {w:+.2f}배"
            parts.append(f"{_short(h.asset)} {_side(h.qty)}{wtxt}")
        lines.append("보유: " + " · ".join(parts))
    if view.rows:
        settled = sum(r.settled_pnl for r in view.rows)
        funding = sum(r.funding for r in view.rows)
        cost = sum(r.cost for r in view.rows)
        days = len({r.as_of_ms for r in view.rows})
        lines.append(
            f"리밸런싱 {days}일 · 정산 `{settled:+,.2f}` · 펀딩 `{0.0 - funding:+,.2f}`"
            f" · 비용 `{0.0 - cost:,.2f}`"
        )
    else:
        lines.append("기간 안 리밸런싱 없음")
    stats = mark_stats(view.marks)
    if stats is not None:
        lines.append(
            f"지갑(하루 1회 시가평가): `{stats.last:,.2f}` · 기간 `{stats.change_pct:+.2f}%`"
            f" · 낙폭 최대 `{stats.max_drawdown_pct:.2f}%`"
        )
    return lines


def holdings_frame(view: EngineCView) -> pd.DataFrame:
    """대시보드용 — 지금 보유(자산 · 방향 · 수량 · 마지막 정산가 · 명목)."""
    return pd.DataFrame(
        [
            {
                "자산": _short(h.asset),
                "방향": _side(h.qty),
                "수량": h.qty,
                "정산가": h.price,
                "명목($)": h.qty * h.price,
                "기준일(KST)": format_kst(h.as_of_ms + 86_400_000),
            }
            for h in view.holdings
        ]
    )


def rebalance_frame(view: EngineCView) -> pd.DataFrame:
    """대시보드용 — 리밸런싱 원장(최근이 위)."""
    return pd.DataFrame(
        [
            {
                "시각(KST)": format_kst(r.executed_ms),
                "자산": _short(r.asset),
                "배율": r.weight,
                "신호": r.signal,
                "목표 명목($)": r.target_notional,
                "거래 명목($)": r.traded_notional,
                "정산($)": r.settled_pnl,
                "펀딩($)": 0.0 - r.funding,
                "비용($)": 0.0 - r.cost,
                "합계($)": r.net_pnl,
            }
            for r in reversed(view.rows)
        ]
    )
