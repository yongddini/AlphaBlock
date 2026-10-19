"""지갑 요약 — 「지금 지갑이 얼마이고 왜 바뀌었나」를 텔레그램 한 통으로 (WAN-457).

엔진 C(WAN-451)가 붙은 뒤로 지갑은 **두 엔진이 같이 쓴다**: 팔은 `paper_trades`(왕복 1행)에,
엔진 C는 `tsmom_rebalances`(하루 자산당 1행)에 손익을 남긴다. 팔 청산 알림의 「잔고」는 그사이
엔진 C 정산이 섞여 팔 거래만으로는 설명되지 않게 움직이고, 엔진 C 알림의 「지갑 자본」은 어제
대비·내역을 안 보여 준다. 이 모듈이 그 둘을 한 장으로 묶는다.

- §1 하루 1회 「💰 지갑 요약」 — 엔진 C 리밸런싱 직후(`live.tsmom_runner`가 부른다).
- §2 「/잔고」 언제든 조회 — `live.telegram_commands`가 같은 포맷 함수를 그대로 쓴다.

## 숫자의 정의 (새 계산 규칙을 만들지 않는다)

* **실현 잔고** = 초기 자본 + 팔 실현손익 합 + 엔진 C 정산 합. 대시보드 「지갑 잔고(팔 + 엔진 C)」
  (`dashboard.app._render_engine_c`)와 **같은 공식**이다. 엔진 C는 매일 정산이라 미실현이 없다.
* **평가 포함 총자산** = 실현 잔고 + 열린 팔 포지션 평가손익. `paper_equity_marks.equity`
  (`paper.tsmom_ledger.EquityMark`)와 **같은 정의**다 — 하루 요약은 그 장부 값을 그대로 찍는다.
* **엔진 C 정산 이후 평가손익(참고)** — §2 전용. 보유 수량 × (최신 1분봉 종가 − 정산가). 엔진 C는
  하루 1번만 정산하므로 낮에 조회하면 그사이 BTC·ETH 움직임이 장부에 0으로 보인다 — 그래서 따로
  계산해 **참고** 라벨을 붙인다. 장부에는 쓰지 않는다.

## 내역 = 변화 (숫자 일치)

두 평가 시점 A → B 사이의 총자산 변화를 팔 실현 · 팔 평가손익 변화 · 엔진 C(가격 / 펀딩 / 비용)로
쪼갠다(WAN-459: 화면에는 **확정**(실현 잔고 변화)과 **평가 포함**(확정 + 팔 평가손익 변화 + 추세추종
미정산 평가손익)을 나란히 낸다 — 추세추종은 하루 1번만 정산하므로 낮에는 확정이 거의 늘 0이다).
실현 쪽 변화 중 장부 행으로 설명되지 않는 몫은 **잔차**로 따로 낸다(지우지 않는다). 정상이면
0이고, 0이 아니면 그 자체가 경보다 — 시각 경계에 걸친 청산(평가 시각 뒤 같은 폴링에서 처리된 청산)
이거나 지갑과 장부가 어긋났다는 뜻이다(WAN-367: 칼 같은 0과 마찬가지로 설명 안 되는 몫을 숨기지
않는다).

🚨 **읽기 전용이다** — 모든 조회는 `mode=ro` 연결이다. 테이블이 없으면(한 번도 안 켠 DB) 빈 값을
낸다. `PaperTradeStore`·`TsmomLedgerStore`는 생성 시 테이블을 만들므로 여기서 쓰지 않는다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from common.timefmt import format_kst
from paper.tsmom_ledger import EquityMark
from strategy.models import OrderBlockDirection

#: 평가가를 찾을 1분봉 창 — `live.tsmom_runner.TsmomRebalancer._last_price`와 같은 10분.
DEFAULT_PRICE_WINDOW_MS = 10 * 60_000
#: 이 이상이면 잔차 줄을 찍는다(달러). 부동소수 끝자리는 숨기고 센트 단위부터 보인다.
RESIDUAL_DISPLAY_EPS = 0.005

PriceAt = Callable[[str, int], float | None]
"""`(심볼, 시각 ms) -> 그 시각 직전 1분봉 종가 | None`."""


# -- 조회 ---------------------------------------------------------------------


def _connect_ro(db_path: str | Path) -> sqlite3.Connection | None:
    path = Path(db_path)
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def last_close(
    db_path: str | Path, symbol: str, at_ms: int, *, window_ms: int = DEFAULT_PRICE_WINDOW_MS
) -> float | None:
    """`[at − window, at)`에서 마지막 1분봉 종가(읽기 전용).

    `TsmomRebalancer._last_price`(`OhlcvStore.load(start, end)` = `open_time ∈ [start, end)`)와
    **같은 창 규칙**이다 — 하루 요약의 포지션별 평가손익 합이 장부 `paper_equity_marks.unrealized`
    와 같은 가격으로 계산되게 하려는 것이다.
    """
    conn = _connect_ro(db_path)
    if conn is None:
        return None
    try:
        if not _has_table(conn, "ohlcv"):
            return None
        row = conn.execute(
            "SELECT close FROM ohlcv WHERE symbol = ? AND timeframe = '1m' "
            "AND open_time >= ? AND open_time < ? ORDER BY open_time DESC LIMIT 1",
            (symbol, at_ms - window_ms, at_ms),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else float(row[0])


@dataclass(frozen=True)
class ArmPositionLine:
    """열린 팔 포지션 한 줄."""

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    quantity: float
    entry_price: float
    mark_price: float | None
    """평가가(그 시각 직전 1분봉 종가). 없으면 None — 평가손익을 지어내지 않는다."""

    @property
    def unrealized(self) -> float | None:
        if self.mark_price is None:
            return None
        sign = 1.0 if self.direction is OrderBlockDirection.BULLISH else -1.0
        return sign * (self.mark_price - self.entry_price) * self.quantity


@dataclass(frozen=True)
class EngineCLine:
    """엔진 C 보유 한 줄(자산별 마지막 리밸런싱 행)."""

    asset: str
    qty: float
    settle_price: float
    """마지막으로 정산·거래한 가격."""
    live_price: float | None = None
    """§2 참고 평가가(최신 1분봉 종가). 하루 요약에서는 None."""

    @property
    def notional(self) -> float:
        return self.qty * self.settle_price

    @property
    def unsettled(self) -> float | None:
        """정산 이후 평가손익(참고) — 장부에 없는 값."""
        if self.live_price is None:
            return None
        return self.qty * (self.live_price - self.settle_price)


@dataclass(frozen=True)
class WalletSnapshot:
    """한 시각의 지갑 — 장부만으로 재구성한다."""

    at_ms: int
    initial_equity: float
    arm_realized_total: float
    """팔 실현손익 합(`paper_trades.realized_pnl` 전체)."""
    engine_c_total: float
    """엔진 C 정산 합(`settled_pnl − funding − cost` 전체)."""
    arm_positions: list[ArmPositionLine] = field(default_factory=list)
    engine_c: list[EngineCLine] = field(default_factory=list)
    legacy_rows: int = 0
    """`realized_pnl`이 NULL인 옛 %-only 행 수(있으면 실현 잔고가 그만큼 모자란다)."""

    @property
    def realized(self) -> float:
        """실현 잔고 = 초기 자본 + 팔 실현 합 + 엔진 C 정산 합(대시보드와 같은 공식)."""
        return self.initial_equity + self.arm_realized_total + self.engine_c_total

    @property
    def arm_unrealized(self) -> float:
        """평가가가 있는 팔 포지션의 평가손익 합."""
        return sum(p.unrealized for p in self.arm_positions if p.unrealized is not None)

    @property
    def unpriced_positions(self) -> int:
        return sum(1 for p in self.arm_positions if p.mark_price is None)

    @property
    def equity(self) -> float:
        """평가 포함 총자산 = 실현 잔고 + 팔 평가손익(`paper_equity_marks.equity`와 같은 정의)."""
        return self.realized + self.arm_unrealized

    @property
    def engine_c_unsettled(self) -> float | None:
        """엔진 C 정산 이후 평가손익 합(참고). 보유가 없으면 0, 가격이 하나라도 없으면 None."""
        total = 0.0
        for line in self.engine_c:
            if line.qty == 0.0:
                continue
            value = line.unsettled
            if value is None:
                return None
            total += value
        return total

    def as_mark(self) -> EquityMark:
        """이 스냅샷을 `EquityMark` 모양으로 — 「직전 평가 대비 변화」 계산에 쓴다."""
        return EquityMark(
            mark_ms=self.at_ms,
            realized=self.realized,
            unrealized=self.arm_unrealized,
            open_positions=len(self.arm_positions),
        )


def read_wallet_snapshot(
    db_path: str | Path,
    *,
    initial_equity: float,
    at_ms: int,
    price_at: PriceAt | None = None,
    live_engine_c: bool = False,
) -> WalletSnapshot:
    """장부에서 지갑 스냅샷을 읽는다(읽기 전용 · 테이블이 없으면 빈 값).

    `price_at`이 없으면 `last_close(db_path, …)`를 쓴다. `live_engine_c=True`면 엔진 C 보유에도
    `at_ms` 직전 가격을 붙여 정산 이후 평가손익(참고)을 낸다(§2).
    """
    prices: PriceAt = price_at or (lambda symbol, t: last_close(db_path, symbol, t))
    conn = _connect_ro(db_path)
    if conn is None:
        return WalletSnapshot(at_ms, initial_equity, 0.0, 0.0)
    try:
        arm_total = 0.0
        legacy = 0
        if _has_table(conn, "paper_trades"):
            n, n_pnl, s = conn.execute(
                "SELECT COUNT(*), COUNT(realized_pnl), COALESCE(SUM(realized_pnl), 0.0) "
                "FROM paper_trades"
            ).fetchone()
            arm_total = float(s)
            legacy = int(n) - int(n_pnl)
        positions: list[tuple[str, str, str, float, float]] = []
        if _has_table(conn, "open_positions"):
            positions = [
                (str(a), str(b), str(c), float(d), float(e))
                for a, b, c, d, e in conn.execute(
                    "SELECT symbol, timeframe, direction, quantity, entry_price "
                    "FROM open_positions ORDER BY symbol, timeframe"
                ).fetchall()
            ]
        ec_total = 0.0
        holdings: list[tuple[str, float, float]] = []
        if _has_table(conn, "tsmom_rebalances"):
            got = conn.execute(
                "SELECT COALESCE(SUM(settled_pnl - funding - cost), 0.0) FROM tsmom_rebalances"
            ).fetchone()
            ec_total = float(got[0])
            holdings = [
                (str(a), float(q), float(p))
                for a, q, p in conn.execute(
                    "SELECT asset, new_qty, price FROM tsmom_rebalances t WHERE as_of_ms = "
                    "(SELECT MAX(as_of_ms) FROM tsmom_rebalances u WHERE u.asset = t.asset) "
                    "ORDER BY asset"
                ).fetchall()
            ]
    finally:
        conn.close()
    arm_lines = [
        ArmPositionLine(
            symbol=sym,
            timeframe=tf,
            direction=OrderBlockDirection(direction),
            quantity=qty,
            entry_price=entry,
            mark_price=prices(sym, at_ms),
        )
        for sym, tf, direction, qty, entry in positions
    ]
    ec_lines = [
        EngineCLine(
            asset=asset,
            qty=qty,
            settle_price=price,
            live_price=prices(asset, at_ms) if live_engine_c and qty != 0.0 else None,
        )
        for asset, qty, price in holdings
    ]
    return WalletSnapshot(
        at_ms=at_ms,
        initial_equity=initial_equity,
        arm_realized_total=arm_total,
        engine_c_total=ec_total,
        arm_positions=arm_lines,
        engine_c=ec_lines,
        legacy_rows=legacy,
    )


def read_marks_before(db_path: str | Path, before_ms: int) -> EquityMark | None:
    """`before_ms` **이전**(엄격) 마지막 시가평가 자본. 없으면 None."""
    conn = _connect_ro(db_path)
    if conn is None:
        return None
    try:
        if not _has_table(conn, "paper_equity_marks"):
            return None
        row = conn.execute(
            "SELECT mark_ms, realized, unrealized, open_positions FROM paper_equity_marks "
            "WHERE mark_ms < ? ORDER BY mark_ms DESC LIMIT 1",
            (before_ms,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return EquityMark(int(row[0]), float(row[1]), float(row[2]), int(row[3]))


def read_latest_mark(db_path: str | Path, at_ms: int) -> EquityMark | None:
    """`at_ms` 이하 마지막 시가평가 자본."""
    return read_marks_before(db_path, at_ms + 1)


# -- 변화 내역 ------------------------------------------------------------------


@dataclass(frozen=True)
class WalletChange:
    """두 평가 시점 A → B 사이 총자산 변화와 그 내역."""

    since_ms: int | None
    """A의 시각. None이면 「시작 대비」(초기 자본 · 평가손익 0)."""
    before_equity: float
    after_equity: float
    arm_realized: float
    """`exit_time ∈ (A, B]` 팔 청산의 실현손익 합."""
    arm_unrealized_change: float
    engine_c_settled: float
    """`executed_ms ∈ (A, B]` 엔진 C 행의 가격 손익 합."""
    engine_c_funding: float
    """같은 행들의 펀딩 지갑 기여(지불이면 음수)."""
    engine_c_cost: float
    """같은 행들의 거래 비용 지갑 기여(언제나 0 이하)."""
    residual: float
    """실현 변화 중 위 장부 행으로 설명되지 않는 몫(정상 0)."""
    engine_c_unsettled: float | None = 0.0
    """B 시각 추세추종 정산 이후 평가손익(참고 · 장부 밖). 가격이 없으면 None (WAN-459).

    A는 언제나 정산 직후 장부 값(`paper_equity_marks`는 리밸런싱 직후에만 남는다)이라 A의 미정산
    평가손익은 0이다 — 그래서 B의 값 하나가 곧 「정산 이후 움직임」이다. 하루 요약(B = 방금
    정산)은 0.
    """

    @property
    def total(self) -> float:
        """장부 총자산 변화(실현 + 팔 평가손익 변화). `confirmed + arm_unrealized_change`."""
        return self.after_equity - self.before_equity

    @property
    def confirmed(self) -> float:
        """확정 변화 — 실현 잔고 변화(팔 실현 + 추세추종 정산 + 잔차). 평가손익 제외(WAN-459)."""
        return self.arm_realized + self.engine_c_net + self.residual

    @property
    def with_marks(self) -> float | None:
        """평가 포함 변화 = 확정 + 팔 평가손익 변화 + 추세추종 미정산 평가손익(WAN-459).

        낮에는 추세추종이 정산되지 않아 `confirmed`가 거의 늘 0이다 — 실제 움직임은 이 값에 있다.
        추세추종 가격이 없으면 None(지어내지 않는다).
        """
        if self.engine_c_unsettled is None:
            return None
        return self.confirmed + self.arm_unrealized_change + self.engine_c_unsettled

    @property
    def engine_c_net(self) -> float:
        return self.engine_c_settled + self.engine_c_funding + self.engine_c_cost

    @property
    def explained(self) -> float:
        """내역의 합(잔차 포함) — `total`과 같아야 한다(테스트가 고정)."""
        return self.arm_realized + self.arm_unrealized_change + self.engine_c_net + self.residual


def wallet_change(
    db_path: str | Path,
    *,
    current: EquityMark,
    previous: EquityMark | None,
    initial_equity: float,
    engine_c_unsettled: float | None = 0.0,
) -> WalletChange:
    """`previous → current` 총자산 변화를 장부 행으로 쪼갠다(읽기 전용).

    `engine_c_unsettled`는 `current` 시각의 추세추종 정산 이후 평가손익(참고)이다 — 「평가 포함」
    변화에만 더한다(WAN-459). 하루 요약은 방금 정산했으니 0이다.
    """
    lo = None if previous is None else previous.mark_ms
    hi = current.mark_ms
    arm = 0.0
    settled = funding = cost = 0.0
    conn = _connect_ro(db_path)
    if conn is not None:
        try:
            if _has_table(conn, "paper_trades"):
                sql = (
                    "SELECT COALESCE(SUM(realized_pnl), 0.0) FROM paper_trades "
                    "WHERE realized_pnl IS NOT NULL AND exit_time <= ?"
                )
                params: list[int] = [hi]
                if lo is not None:
                    sql += " AND exit_time > ?"
                    params.append(lo)
                arm = float(conn.execute(sql, params).fetchone()[0])
            if _has_table(conn, "tsmom_rebalances"):
                sql = (
                    "SELECT COALESCE(SUM(settled_pnl), 0.0), COALESCE(SUM(funding), 0.0), "
                    "COALESCE(SUM(cost), 0.0) FROM tsmom_rebalances WHERE executed_ms <= ?"
                )
                params = [hi]
                if lo is not None:
                    sql += " AND executed_ms > ?"
                    params.append(lo)
                s, f, c = conn.execute(sql, params).fetchone()
                settled, funding, cost = float(s), float(f), float(c)
        finally:
            conn.close()
    before_realized = initial_equity if previous is None else previous.realized
    before_unrealized = 0.0 if previous is None else previous.unrealized
    ec_net = settled - funding - cost
    residual = (current.realized - before_realized) - (arm + ec_net)
    return WalletChange(
        since_ms=lo,
        before_equity=before_realized + before_unrealized,
        after_equity=current.equity,
        arm_realized=arm,
        arm_unrealized_change=current.unrealized - before_unrealized,
        engine_c_settled=settled,
        engine_c_funding=0.0 - funding,
        engine_c_cost=0.0 - cost,
        residual=residual,
        engine_c_unsettled=engine_c_unsettled,
    )


# -- 포맷(평문 — 텔레그램 parse_mode=None) ---------------------------------------


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _signed(value: float) -> str:
    rounded = round(value, 2)
    if rounded == 0:
        rounded = 0.0  # −0.00을 찍지 않는다.
    return f"{'+' if rounded >= 0 else '−'}${abs(rounded):,.2f}"


def _pct(value: float, base: float) -> str:
    if base <= 0:
        return "-"
    return f"{(value / base) * 100.0:+.2f}%"


def _short(symbol: str) -> str:
    return symbol.split("/", 1)[0]


def _side(qty: float) -> str:
    return "롱" if qty > 0 else ("숏" if qty < 0 else "없음")


def _price(value: float) -> str:
    text = f"{value:,.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _change_lines(change: WalletChange, *, label: str) -> list[str]:
    """변화 한 줄(확정 · 평가 포함 나란히) + 내역 — 하루 요약과 「/잔고」가 같은 모양(WAN-459)."""
    base = change.before_equity
    marked = change.with_marks
    marked_text = "가격 없음" if marked is None else f"{_signed(marked)} ({_pct(marked, base)})"
    lines = [
        f"{label}: 확정 {_signed(change.confirmed)} ({_pct(change.confirmed, base)})"
        f" · 평가 포함 {marked_text}"
    ]
    lines.append(f"  · 팔 실현 {_signed(change.arm_realized)}")
    lines.append(
        f"  · 추세추종 정산 {_signed(change.engine_c_net)} (가격 {_signed(change.engine_c_settled)}"
        f" · 펀딩 {_signed(change.engine_c_funding)} · 비용 {_signed(change.engine_c_cost)})"
    )
    if abs(change.residual) >= RESIDUAL_DISPLAY_EPS:
        lines.append(
            f"  · ⚠️ 장부로 설명 안 되는 몫 {_signed(change.residual)}"
            " (평가 시각 경계의 청산 또는 지갑·장부 불일치)"
        )
    lines.append(f"  · 팔 평가손익 변화 {_signed(change.arm_unrealized_change)} (평가 포함에만)")
    unsettled = change.engine_c_unsettled
    unsettled_text = "가격 없음" if unsettled is None else _signed(unsettled)
    lines.append(f"  · 추세추종 미정산 평가손익 {unsettled_text} (평가 포함에만)")
    return lines


def _position_lines(snapshot: WalletSnapshot, *, live_engine_c: bool) -> list[str]:
    lines: list[str] = []
    if snapshot.arm_positions:
        lines.append(f"팔 열린 포지션 {len(snapshot.arm_positions)}건")
        for p in snapshot.arm_positions:
            side = "롱" if p.direction is OrderBlockDirection.BULLISH else "숏"
            value = p.unrealized
            tail = "평가가 없음" if value is None else f"평가 {_signed(value)}"
            lines.append(
                f"  · {_short(p.symbol)} {p.timeframe} {side} @ {_price(p.entry_price)} · {tail}"
            )
    else:
        lines.append("팔 열린 포지션 없음")
    if any(line.qty != 0.0 for line in snapshot.engine_c):
        parts = []
        for line in snapshot.engine_c:
            if line.qty == 0.0:
                parts.append(f"{_short(line.asset)} 없음")
                continue
            text = f"{_short(line.asset)} {_side(line.qty)} 명목 {_signed(line.notional)}"
            if live_engine_c:
                unsettled = line.unsettled
                tail = "가격 없음" if unsettled is None else _signed(unsettled)
                text += f" · 정산 후 {tail}"
            parts.append(text)
        lines.append("추세추종 보유: " + " / ".join(parts))
    elif snapshot.engine_c:
        lines.append("추세추종 보유 없음(현금)")
    return lines


def format_daily_wallet_summary(
    snapshot: WalletSnapshot, *, mark: EquityMark, change: WalletChange
) -> str:
    """§1 하루 1회 「💰 지갑 요약」 — 엔진 C 리밸런싱 직후. 평문.

    총자산은 방금 남긴 장부 값(`mark.equity`)을 그대로 찍는다. 실현 잔고는 장부 행 합(대시보드와
    같은 공식)이고, 둘이 지갑과 어긋나면 그 사실을 따로 찍는다.
    """
    initial = snapshot.initial_equity
    lines = [f"💰 지갑 요약 · {format_kst(mark.mark_ms)} KST"]
    lines.append(
        f"평가 포함 총자산 {_usd(mark.equity)} (시작 대비 {_pct(mark.equity - initial, initial)})"
    )
    lines.append(
        f"실현 잔고 {_usd(snapshot.realized)} (대시보드와 같음) · 팔 평가손익"
        f" {_signed(mark.unrealized)}"
    )
    gap = mark.realized - snapshot.realized
    if abs(gap) >= RESIDUAL_DISPLAY_EPS:
        lines.append(f"⚠️ 지갑 자본과 장부 합이 {_signed(gap)} 어긋남")
    if snapshot.legacy_rows:
        lines.append(f"⚠️ 달러 손익이 없는 옛 장부 행 {snapshot.legacy_rows}건 — 실현 잔고에서 빠짐")
    label = "시작 대비" if change.since_ms is None else f"{format_kst(change.since_ms)} 대비"
    lines += _change_lines(change, label=label)
    lines += _position_lines(snapshot, live_engine_c=False)
    return "\n".join(lines)


def format_balance_reply(
    snapshot: WalletSnapshot, *, change: WalletChange | None, last_mark: EquityMark | None
) -> str:
    """§2 「/잔고」 답 — 그 시각 기준. 평문.

    「정산 기준 자본」은 장부 값(실현 잔고 + 팔 평가손익)이다. 엔진 C의 정산 이후 움직임은 장부에
    없으므로 「참고」 라벨로 따로 찍고, 더한 「지금 평가 총액」도 참고값이라고 밝힌다.
    """
    initial = snapshot.initial_equity
    lines = [f"💰 지갑 현황 · {format_kst(snapshot.at_ms)} KST"]
    lines.append(
        f"정산 기준 자본 {_usd(snapshot.equity)} (시작 대비"
        f" {_pct(snapshot.equity - initial, initial)})"
    )
    lines.append(
        f"  = 실현 잔고 {_usd(snapshot.realized)} (대시보드와 같음) + 팔 평가손익"
        f" {_signed(snapshot.arm_unrealized)}"
    )
    if snapshot.unpriced_positions:
        lines.append(
            f"  ⚠️ 평가가가 없는 팔 포지션 {snapshot.unpriced_positions}건 — 평가손익에서 빠짐"
        )
    unsettled = snapshot.engine_c_unsettled
    if unsettled is None:
        lines.append("추세추종 정산 이후 평가손익(참고): 가격 없음")
    else:
        lines.append(f"추세추종 정산 이후 평가손익(참고) {_signed(unsettled)}")
        total = snapshot.equity + unsettled
        lines.append(
            f"지금 평가 총액(참고) {_usd(total)} (시작 대비 {_pct(total - initial, initial)})"
        )
    if snapshot.legacy_rows:
        lines.append(f"⚠️ 달러 손익이 없는 옛 장부 행 {snapshot.legacy_rows}건 — 실현 잔고에서 빠짐")
    if change is not None and last_mark is not None:
        lines += _change_lines(change, label=f"마지막 정산({format_kst(last_mark.mark_ms)}) 이후")
    lines += _position_lines(snapshot, live_engine_c=True)
    return "\n".join(lines)


# -- 진입점 --------------------------------------------------------------------


def build_daily_wallet_summary(
    db_path: str | Path,
    *,
    mark: EquityMark,
    initial_equity: float,
    price_at: PriceAt | None = None,
) -> str:
    """§1 — 방금 남긴 `mark` 기준 하루 요약 문자열(읽기 전용)."""
    snapshot = read_wallet_snapshot(
        db_path, initial_equity=initial_equity, at_ms=mark.mark_ms, price_at=price_at
    )
    previous = read_marks_before(db_path, mark.mark_ms)
    change = wallet_change(db_path, current=mark, previous=previous, initial_equity=initial_equity)
    return format_daily_wallet_summary(snapshot, mark=mark, change=change)


def build_balance_reply(
    db_path: str | Path,
    *,
    initial_equity: float,
    now_ms: int,
    price_at: PriceAt | None = None,
) -> str:
    """§2 — 지금 기준 「/잔고」 답(읽기 전용)."""
    snapshot = read_wallet_snapshot(
        db_path,
        initial_equity=initial_equity,
        at_ms=now_ms,
        price_at=price_at,
        live_engine_c=True,
    )
    last_mark = read_latest_mark(db_path, now_ms)
    change = None
    if last_mark is not None:
        change = wallet_change(
            db_path,
            current=snapshot.as_mark(),
            previous=last_mark,
            initial_equity=initial_equity,
            engine_c_unsettled=snapshot.engine_c_unsettled,
        )
    return format_balance_reply(snapshot, change=change, last_mark=last_mark)
