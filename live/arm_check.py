"""팔을 켜기 전에 서버가 준비됐나 — 읽기 전용 점검(WAN-451 ③ 전환 런북).

`alphablock arm-check`가 부른다. 넷을 본다:

1. **설정** — 팔을 켜면 함께 맞아야 하는 값(`arm_settings_problems`와 같은 목록). 팔이 아직 꺼져
   있어도 「켰다면 무엇이 어긋나나」를 보여 준다.
2. **이번 주 종목 목록** — 수집기가 남긴 그 주 목록이 있나(없으면 러너가 모든 진입을 거부한다).
3. **그 주 종목의 데이터** — 원천 TF(1m · 15m · 1h · 4h · 6h · 8h · 12h · 1d · 1w)와 생성 TF(3h)가
   **최신**인가(마지막 확정봉이 TF 하나 + 여유 안), 상위TF는 **이력이 2년**(오래된 존 기억)인가 —
   상장이 더 늦은 종목은 상장부터면 된다(1d 첫 봉 기준).
4. **장부** — 옛 규칙의 포지션·대기 주문·엔진 스냅샷이 남아 있나. 남아 있으면 켜는 순간이 리셋
   이음매다(`scripts/paper-reset.sh`) — 옛 스냅샷이 새 규칙의 러너에 복원돼 섞이면 안 된다.

하나라도 막히면 종료 코드 1(감시에 물릴 수 있게). DB에 아무것도 쓰지 않는다(WAN-194).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from common.timefmt import format_kst
from config.settings import Settings
from data.models import timeframe_to_ms
from data.storage import OhlcvStore
from data.universe import ARM_STREAM_TIMEFRAMES, BUILT_TIMEFRAMES, rebalance_at
from data.universe_pool import UniverseStore

DAY_MS = 86_400_000
#: 마지막 확정봉이 늦어도 되는 여유(수집 지연 · 폴링 간격).
FRESH_SLACK_MS = 10 * 60_000
#: 점검하는 TF — 원천 kline + 수집기가 만드는 3h(2h는 저장소가 1h에서 즉석 파생이라 뺀다).
CHECK_TIMEFRAMES: tuple[str, ...] = tuple(
    sorted({*ARM_STREAM_TIMEFRAMES, *BUILT_TIMEFRAMES}, key=timeframe_to_ms)
)


@dataclass(frozen=True)
class PairIssue:
    symbol: str
    timeframe: str
    problem: str


@dataclass
class ArmCheck:
    settings_problems: list[str] = field(default_factory=list)
    rebalance_ms: int = 0
    members: tuple[str, ...] = ()
    list_missing: bool = False
    pair_issues: list[PairIssue] = field(default_factory=list)
    ledger: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def leftovers(self) -> dict[str, int]:
        """옛 규칙에서 남은 것(지우지 않고 켜면 섞인다)."""
        keys = ("open_positions", "pending_orders", "live_engine_state")
        return {k: v for k, v in self.ledger.items() if k in keys and v > 0}

    @property
    def ready(self) -> bool:
        return not (self.settings_problems or self.list_missing or self.pair_issues)


def settings_problems(settings: Settings) -> list[str]:
    """팔을 켰다면 어긋나는 설정 — 러너가 기동을 거부하는 목록과 같다."""
    from live.zone_limit_runner import arm_settings_problems

    out = list(arm_settings_problems(settings))
    if not settings.live_arm_enabled:
        out.append(
            "팔 스위치: ALPHABLOCK_LIVE_ARM_ENABLED=true (지금 false — 리셋 이음매에서 켠다)"
        )
    return out


def pair_issues(
    store: OhlcvStore, symbols: Sequence[str], *, now_ms: int, history_days: int
) -> list[PairIssue]:
    """종목 × 점검 TF마다 최신성과 이력 깊이."""
    issues: list[PairIssue] = []
    want = now_ms - history_days * DAY_MS
    for sym in symbols:
        listed = store.first_open_time(sym, "1d")
        for tf in CHECK_TIMEFRAMES:
            tf_ms = timeframe_to_ms(tf)
            last = store.last_open_time(sym, tf)
            if last is None:
                issues.append(PairIssue(sym, tf, "저장된 봉 없음"))
                continue
            lag = now_ms - (last + tf_ms)
            if lag > tf_ms + FRESH_SLACK_MS:
                issues.append(
                    PairIssue(
                        sym, tf, f"마지막 봉 {format_kst(last)} — {lag / 3_600_000:.1f}시간 늦음"
                    )
                )
            if tf == "1m" or history_days <= 0:
                continue
            first = store.first_open_time(sym, tf)
            assert first is not None
            enough = first <= want + tf_ms or (listed is not None and first <= listed + DAY_MS)
            if not enough:
                days = (now_ms - first) / DAY_MS
                issues.append(
                    PairIssue(sym, tf, f"이력 {days:.0f}일(목표 {history_days}일 · 상장 이후 아님)")
                )
    return issues


def ledger_counts(db_path: str | Path) -> dict[str, int]:
    """장부 테이블 행 수(없는 테이블은 0) — 읽기 전용."""
    path = str(db_path)
    if not Path(path).exists():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    queries = {
        "open_positions": "SELECT COUNT(*) FROM open_positions",
        "pending_orders": "SELECT COUNT(*) FROM live_limit_orders WHERE status = 'pending'",
        "live_engine_state": "SELECT COUNT(*) FROM live_engine_state",
        "paper_trades": "SELECT COUNT(*) FROM paper_trades",
        "arm_signals": "SELECT COUNT(*) FROM arm_signals",
        "tsmom_rebalances": "SELECT COUNT(*) FROM tsmom_rebalances",
    }
    out: dict[str, int] = {}
    try:
        for name, sql in queries.items():
            try:
                out[name] = int(conn.execute(sql).fetchone()[0])
            except sqlite3.OperationalError:
                out[name] = 0  # 테이블 없음
    finally:
        conn.close()
    return out


def run_check(settings: Settings, db_path: str | Path, *, now_ms: int) -> ArmCheck:
    check = ArmCheck(settings_problems=settings_problems(settings))
    check.rebalance_ms = rebalance_at(now_ms)
    with UniverseStore(db_path) as universe:
        snap = universe.at(now_ms)
    if snap is None or snap.rebalance_ms != check.rebalance_ms:
        check.list_missing = True
    else:
        check.members = tuple(sorted(snap.members))
        if snap.stale_size:
            check.notes.append(f"그 주 계산 때 일봉이 늦은 풀 종목 {snap.stale_size}개")
    if check.members:
        with OhlcvStore(db_path) as store:
            check.pair_issues = pair_issues(
                store, check.members, now_ms=now_ms, history_days=settings.live_zone_memory_days
            )
    check.ledger = ledger_counts(db_path)
    if not settings.live_tsmom_enabled:
        check.notes.append(
            "엔진 C가 꺼져 있다(ALPHABLOCK_LIVE_TSMOM_ENABLED=false) — 정본은 팔 + 엔진 C 합성이다"
        )
    if settings.live_tick_feed_enabled:
        check.notes.append(
            "틱 피드가 켜져 있다 — 팔은 1분봉으로만 판정해 틱을 안 쓴다(꺼도 결과가 같다 ·"
            " 31종목 웹소켓 하나를 아낀다: ALPHABLOCK_LIVE_TICK_FEED_ENABLED=false)"
        )
    return check


def render(check: ArmCheck) -> str:
    lines = ["## 팔 켜기 전 점검", ""]
    lines.append("### 1. 설정")
    if check.settings_problems:
        lines += [f"- ❌ {p}" for p in check.settings_problems]
    else:
        lines.append("- ✅ 팔 설정이 다 맞다")
    lines += ["", f"### 2. 이번 주 종목 목록({format_kst(check.rebalance_ms)} 주)"]
    if check.list_missing:
        lines.append(
            "- ❌ 그 주 목록이 없다 — 수집기(ALPHABLOCK_UNIVERSE_ENABLED=true)가"
            " 월요일 00:00 UTC 뒤 만든다(`alphablock universe`)"
        )
    else:
        names = ", ".join(s.split("/")[0] for s in check.members)
        lines.append(f"- ✅ {len(check.members)}종목: {names}")
    lines += ["", "### 3. 그 주 종목 데이터(최신 · 2년 이력)"]
    if check.list_missing:
        lines.append("- (목록이 없어 건너뜀)")
    elif check.pair_issues:
        lines.append(f"- ❌ {len(check.pair_issues)}쌍이 막혔다:")
        for i in check.pair_issues[:40]:
            lines.append(f"  - {i.symbol.split('/')[0]} {i.timeframe}: {i.problem}")
        if len(check.pair_issues) > 40:
            lines.append(f"  - … 외 {len(check.pair_issues) - 40}쌍")
    else:
        lines.append(
            f"- ✅ {len(check.members)}종목 × {len(CHECK_TIMEFRAMES)}TF 전부 최신 · 이력 충분"
        )
    lines += ["", "### 4. 장부(지금 남아 있는 것)"]
    for name, n in check.ledger.items():
        lines.append(f"- {name}: {n}")
    if check.leftovers:
        lines.append(
            "- ⚠️ 옛 규칙의 포지션·대기 주문·엔진 스냅샷이 남아 있다 — 켜는 순간이 리셋 이음매다"
            "(`scripts/paper-reset.sh`, 런북 docs/ops/wan451-arm-paper.md)"
        )
    if check.notes:
        lines += ["", "### 참고"]
        lines += [f"- {n}" for n in check.notes]
    lines += ["", "**준비됨 ✅**" if check.ready else "**아직 안 됨 ❌** — 위 ❌를 먼저 해결한다"]
    return "\n".join(lines)
