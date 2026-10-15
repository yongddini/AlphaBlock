"""팔 페이퍼 장부 ↔ 정본 백테스트 대조 (WAN-451 ③ · 「백테는 페이퍼와 같은 선상」 WAN-305).

켜기 전 관문(`live.arm_gate`)은 저장 데이터로 러너를 **재생**해 정본과 맞댔다. 이 도구는 서버에서
실제로 돈 **페이퍼 장부**를 맞댄다 — 같은 DB(시세 · 장부 · 매주 목록)로 같은 기간의 정본 팔을 돌리고

1. **신호** — 장부 `arm_signals`(센 것) ≡ 정본 팔 후보(그 주 목록 · 손절폭 ≥ 4% · %K < 25)
2. **거래** — 진입 시각 · 진입가 · 2배 손절가 · 크기 배율(`entry_size_scale`) · 청산 시각 · 사유

를 하나씩 맞댄다. 정본은 **실제 주간 목록**(`universe_rotations`)을 쓰고, 페이퍼가 시작한 뒤에 탭된
후보만 쓴다(러너는 그 전의 탭을 못 봤다).

어긋남이 나오면 원인 후보를 붙인다: **러너 공백**(가동 세션 사이 48시간 넘는 틈 — 따라잡기 한도를
넘어 그 사이 주문이 폐기됐다) · 그 밖은 **데이터 또는 로직**(데이터가 나중에 고쳐졌거나(백필
복구) 규칙이 갈라졌다). 재생 대조(`live.arm_gate`)를 같은 기간에 돌리면 둘을 가른다.

⚠️ **서버에서 돌리지 않는다** — 정본 후보 생성이 2년치 탐지라 2코어 서버에서는 몇 시간이 걸리고
러너와 코어를 다툰다. 서버 DB를 내려받아 로컬에서 `--db`로 돌린다(로컬 M1 약 30분). 읽기 전용이다.

재현: `uv run alphablock arm-parity --db ~/Downloads/ohlcv.db`.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from common.timefmt import format_kst
from data.universe import rebalance_at
from data.universe_pool import UniverseStore
from live import arm_gate as g

DAY = 86_400_000
MINUTE = 60_000
#: 러너 따라잡기 한도(시간) 기본값 — 이보다 긴 공백은 대기 주문이 폐기된다(WAN-306).
CATCHUP_HOURS = 48


@dataclass(frozen=True)
class Ledger:
    since: int
    until: int
    signals: set[tuple[str, str, int, float, float]]
    trades: list[g.LiveTrade]
    dispositions: dict[g.Key, str]
    gaps: list[tuple[int, int]]
    """러너가 안 돈 구간(따라잡기 한도를 넘는 것만)."""
    pre_session: int = 0
    """기간 시작 **전에 탭된** 주문이 기간 안에 체결된 것 — 양쪽에서 뺀 개수(아래 `read_ledger`)."""


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def read_ledger(db: Path, *, since: int | None, until: int | None) -> Ledger:
    conn = _connect(db)
    try:
        sessions = conn.execute(
            "SELECT started_ms, last_seen_ms FROM live_runner_sessions ORDER BY started_ms"
        ).fetchall()
        if not sessions and since is None:
            raise ValueError(
                "가동 세션이 없다 — 페이퍼가 아직 안 돌았거나 다른 DB다(--since로 지정)"
            )
        start = since if since is not None else int(sessions[0][0])
        last_minute = conn.execute(
            "SELECT MAX(open_time) FROM ohlcv WHERE symbol = 'BTC/USDT:USDT' AND timeframe = '1m'"
        ).fetchone()[0]
        end = until if until is not None else int(last_minute or start)
        gaps = [
            (int(a[1]), int(b[0]))
            for a, b in zip(sessions, sessions[1:], strict=False)
            if int(b[0]) - int(a[1]) > CATCHUP_HOURS * 3_600_000
        ]
        # 러너는 처음 뜰 때 **지금 형성 중인 상위TF 봉의 시작부터** 1분봉을 소비한다(첫 탭 상태를
        # 알아야 한다 — 1w면 최대 일주일 전부터). 그 사이 탭으로 건 주문은 기간 시작 뒤에 체결될 수
        # 있는데, 정본은 기간 시작 뒤에 탭된 후보만 쓴다(`arm_gate.canon`). 그런 체결은 양쪽에서
        # 뺀다 — 안 빼면 「장부만」 어긋남으로 둔갑한다(2026-10-16 첫 기동: 22:09 탭 → 00:43 체결).
        pre_session = {
            (str(r[0]), str(r[1]), int(r[2]))
            for r in conn.execute(
                "SELECT symbol, timeframe, fill_ms FROM live_limit_orders"
                " WHERE status = 'filled' AND placed_ms < ? AND fill_ms >= ?",
                (start, start),
            )
        }
        signals = {
            (str(r[0]), str(r[1]), int(r[2]), float(r[3]), float(r[4]))
            for r in conn.execute(
                "SELECT symbol, timeframe, fill_ms, price, ob_stop FROM arm_signals"
                " WHERE counted = 1 AND fill_ms >= ? AND fill_ms < ?",
                (start, end),
            )
            if (str(r[0]), str(r[1]), int(r[2])) not in pre_session
        }
        scales: dict[g.Key, float] = {}
        dispositions: dict[g.Key, str] = {}
        for r in conn.execute(
            "SELECT symbol, timeframe, fill_ms, entry_status, COALESCE(entry_reject_code, ''),"
            " entry_size_scale FROM live_limit_orders WHERE status = 'filled'"
        ):
            key = (str(r[0]), str(r[1]), int(r[2]))
            dispositions[key] = "진입" if r[3] == "entered" else f"거부({r[4]})"
            if r[5] is not None:
                scales[key] = float(r[5])
        trades: list[g.LiveTrade] = []
        for r in conn.execute(
            "SELECT symbol, timeframe, entry_time, entry_price, stop_price, exit_time, exit_price,"
            " reason FROM paper_trades WHERE entry_time >= ?",
            (start,),
        ):
            key = (str(r[0]), str(r[1]), int(r[2]))
            trades.append(
                g.LiveTrade(
                    *key,
                    float(r[3]),
                    float(r[4] or 0.0),
                    scales.get(key, float("nan")),
                    int(r[5]),
                    float(r[6]),
                    str(r[7]),
                )
            )
        for r in conn.execute(
            "SELECT symbol, timeframe, entry_time, entry_price, stop_price FROM open_positions"
            " WHERE entry_time >= ?",
            (start,),
        ):
            key = (str(r[0]), str(r[1]), int(r[2]))
            trades.append(
                g.LiveTrade(
                    *key,
                    float(r[3]),
                    float(r[4] or 0.0),
                    scales.get(key, float("nan")),
                    None,
                    None,
                    None,
                )
            )
    finally:
        conn.close()
    trades = [t for t in trades if (t.symbol, t.timeframe, t.entry_time) not in pre_session]
    return Ledger(start, end, signals, trades, dispositions, gaps, len(pre_session))


def weekly_members(db: Path, since: int, until: int) -> dict[int, frozenset[str]]:
    """기간에 걸친 주마다 저장된 목록(없는 주는 빠진다 — 그 주 진입은 러너가 거부했다)."""
    out: dict[int, frozenset[str]] = {}
    with UniverseStore(db) as store:
        t = rebalance_at(since)
        while t < until:
            snap = store.at(t)
            if snap is not None and snap.rebalance_ms == t:
                out[t] = snap.members
            t += 7 * DAY
    return out


@contextlib.contextmanager
def db_workdir(db: Path) -> Iterator[None]:
    """정본 파이프라인은 시세를 상대 경로 `data/ohlcv.db`로 읽는다 — 그 자리에 대상 DB를 건다.

    엔진 코드(경로 인자)를 고치지 않는다 — 고치면 후보 캐시 지문이 통째로 바뀐다(WAN-394). 병렬
    워커는 작업 디렉터리를 물려받는다.
    """
    with tempfile.TemporaryDirectory(prefix="arm-parity-") as d:
        (Path(d) / "data").mkdir()
        (Path(d) / "data" / "ohlcv.db").symlink_to(db.resolve())
        old = Path.cwd()
        os.chdir(d)
        try:
            yield
        finally:
            os.chdir(old)


@dataclass
class Report:
    ledger: Ledger
    members: dict[int, frozenset[str]]
    sig_canon: set[tuple[str, str, int, float, float]] = field(default_factory=set)
    checks: list[g.TradeCheck] = field(default_factory=list)
    decisions: dict[g.Key, str] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def mismatches(self) -> list[g.TradeCheck]:
        return [c for c in self.checks if c.problems]

    @property
    def ok(self) -> bool:
        return not self.mismatches and self.sig_canon == self.ledger.signals


def open_trade_ok(check: g.TradeCheck, until: int) -> g.TradeCheck:
    """장부에서 아직 열려 있고 정본은 데이터 끝에서 끊긴 거래 — 청산 비교를 하지 않는다."""
    if check.live is None or check.canon is None or check.live.exit_time is not None:
        return check
    if check.canon.exit_time < until - MINUTE:
        return check  # 정본은 기간 안에서 청산했는데 장부는 열려 있다 — 진짜 어긋남
    kept = tuple(p for p in check.problems if not p.startswith(("청산 시각", "청산 사유")))
    return g.TradeCheck(check.key, check.canon, check.live, kept)


def run(db: Path, *, since: int | None, until: int | None, jobs: int) -> Report:
    t0 = time.monotonic()
    ledger = read_ledger(db, since=since, until=until)
    members = weekly_members(db, ledger.since, ledger.until)
    report = Report(ledger, members)
    symbols = sorted({s for m in members.values() for s in m})
    if not symbols:
        report.seconds = time.monotonic() - t0
        return report

    def members_at(ms: int) -> frozenset[str]:
        return members.get(rebalance_at(ms), frozenset())

    with db_workdir(db):
        cn = g.canon(symbols, ledger.since, ledger.until, jobs, members_at=members_at)
    report.sig_canon = {s for s in cn.signals if ledger.since <= s[2] < ledger.until}
    report.decisions = cn.decisions
    report.checks = [
        open_trade_ok(c, ledger.until)
        for c in g.compare_trades(cn.trades, ledger.trades, ledger.since, ledger.until)
    ]
    report.seconds = time.monotonic() - t0
    return report


def _cause(key: g.Key, gaps: Sequence[tuple[int, int]]) -> str:
    for a, b in gaps:
        if a <= key[2] < b:
            return f"러너 공백({format_kst(a)} ~ {format_kst(b)})"
    return "데이터 또는 로직 — `live.arm_gate` 재생으로 가른다"


def render(report: Report) -> str:
    led = report.ledger
    lines = [
        "## 팔 페이퍼 장부 ↔ 정본 백테스트",
        "",
        f"- 기간 {format_kst(led.since)} ~ {format_kst(led.until)}"
        f" · 목록 있는 주 {len(report.members)}개"
        f" · 측정 {report.seconds:.0f}초",
    ]
    if led.pre_session:
        lines.append(
            f"- 기간 시작 전에 탭된 주문의 체결 {led.pre_session}건은 양쪽에서 뺐다"
            "(러너 첫 기동이 형성 중인 상위TF 봉부터 소비한다 — 정본은 기간 뒤 탭만 쓴다)"
        )
    if led.gaps:
        lines.append(f"- ⚠️ 따라잡기 한도({CATCHUP_HOURS}시간)를 넘는 러너 공백 {len(led.gaps)}개")
    if not report.members:
        lines += ["", "그 기간에 저장된 주간 목록이 없다 — 팔이 진입할 수 없었다."]
        return "\n".join(lines)
    sl, sc = led.signals, report.sig_canon
    lines += [
        "",
        "### 1. 몰림 신호",
        "",
        f"- 장부 {len(sl)}건 · 정본 {len(sc)}건 · 일치 {len(sl & sc)}건"
        f" · 장부만 {len(sl - sc)} · 정본만 {len(sc - sl)}",
    ]
    for label, diff in (("장부만", sl - sc), ("정본만", sc - sl)):
        for s in sorted(diff, key=lambda x: x[2])[:20]:
            lines.append(
                f"  - {label}: {s[0].split('/')[0]} {s[1]} {format_kst(s[2])} 체결가 {s[3]:.8g}"
            )
    bad = report.mismatches
    lines += [
        "",
        "### 2. 거래",
        "",
        f"- 장부 {sum(1 for c in report.checks if c.live)}건 · 정본"
        f" {sum(1 for c in report.checks if c.canon)}건 · 어긋남 **{len(bad)}건**",
    ]
    if bad:
        lines += [
            "",
            "| 진입(KST) | 칸 | 문제 | 정본 판정 | 장부 판정 | 원인 후보 |",
            "| -- | -- | -- | -- | -- | -- |",
        ]
        for c in bad:
            k = c.key
            lines.append(
                f"| {format_kst(k[2])} | {k[0].split('/')[0]} {k[1]} | {' · '.join(c.problems)} |"
                f" {report.decisions.get(k, '후보 아님')}"
                f" | {led.dispositions.get(k, '체결 없음')} |"
                f" {_cause(k, led.gaps)} |"
            )
    lines += ["", "**일치 ✅**" if report.ok else "**어긋남 있음 ❌**"]
    return "\n".join(lines)
