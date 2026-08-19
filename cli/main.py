"""AlphaBlock 실행 CLI 구현 (WAN-31).

기존 진입점(`data.collector.run_collector`, `live.runner.run_signal_runner`,
`dashboard.health_data.build_health_view`)을 얇게 감싸 한 줄 명령으로 노출한다.
비즈니스 로직은 각 모듈에 있고, 여기서는 인자 파싱과 배선만 담당한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import socket
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from common import timefmt
from common.timefmt import KST
from config import get_settings
from config.settings import Settings
from dashboard.health import HealthLevel, runner_cycle_budget_ms
from dashboard.health_data import HealthView, build_health_view

if TYPE_CHECKING:
    from data.integrity import IntegrityReport
    from data.partial_bars import BarDiscrepancy, SeriesScan
    from data.verify import VerifyReport

logger = logging.getLogger(__name__)

_LEVEL_TEXT = {
    HealthLevel.OK: "[OK]",
    HealthLevel.STALE: "[STALE]",
    HealthLevel.UNKNOWN: "[--]",
}


def _configure_logging() -> None:
    # 로그 시각도 KST다(WAN-172) — 서버(UTC)와 노트북에서 같은 사건이 다른 시각으로
    # 찍히면 경고와 로그를 나란히 못 읽는다.
    timefmt.use_kst_logging()
    logging.basicConfig(level=logging.INFO, format=timefmt.kst_log_format())


def _fmt_time(ms: int | None) -> str:
    """운영 출력 시각(KST, WAN-172). 판정·저장은 UTC epoch ms 그대로다."""
    return timefmt.format_kst_zoned(ms)


def _fmt_lag(lag_ms: int | None) -> str:
    if lag_ms is None:
        return "—"
    if lag_ms < 0:
        return "실시간"
    minutes = lag_ms / 60_000
    if minutes < 60:
        return f"{minutes:.0f}분"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}시간"
    return f"{hours / 24:.1f}일"


def format_status(view: HealthView, *, configured_symbols: Sequence[str] | None = None) -> str:
    """Health 뷰를 사람이 읽는 여러 줄 텍스트로 요약한다(순수 함수, 테스트용).

    `configured_symbols`(설정의 수집 대상)를 주면 **설정과 실제를 나란히 찍는다**
    (WAN-156 §6). 이번 사고의 본질은 「설정은 6종목인데 실제로 도는 건 3종목」이
    아무 화면에도 안 보였던 것이다 — `.env`가 코드 기본값을 덮어써도, 수집기를
    재시작하지 않아 옛 설정으로 돌고 있어도, 여기서 어긋남이 보인다.
    """
    lines: list[str] = []
    lines.append(f"AlphaBlock 운영 상태  ·  기준 {_fmt_time(view.now_ms)}")
    lines.append(f"종합: {_LEVEL_TEXT[view.overall.level]} {view.overall.label}")
    lines.append("")

    if configured_symbols is not None:
        lines.append(f"수집 대상 심볼(설정): {len(configured_symbols)}종목")
        for symbol in configured_symbols:
            lines.append(f"  · {symbol}")
        stored = {f.symbol for f in view.freshness}
        missing = [s for s in configured_symbols if s not in stored]
        extra = sorted(stored - set(configured_symbols))
        if missing:
            lines.append(f"  ⚠️ 설정에 있으나 저장된 봉이 없음: {', '.join(missing)}")
        if extra:
            lines.append(f"  ⚠️ 저장돼 있으나 수집 대상이 아님(낡습니다): {', '.join(extra)}")
        lines.append("  ℹ️ `.env`를 고쳐도 이미 돌던 수집기는 옛 목록으로 돕니다 — 재시작하세요.")
        lines.append("")

    lines.append("수집기:")
    if not view.collector.ran:
        lines.append("  미실행 — `alphablock collect` 로 시작하세요.")
    else:
        c_lag = _fmt_lag(view.collector.lag_ms)
        lines.append(f"  {_LEVEL_TEXT[view.collector.level]} 마지막 하트비트 {c_lag} 전")

    lines.append("러너:")
    if not view.runner.ran:
        lines.append("  미실행 — `alphablock live` 로 시작하세요.")
    else:
        lines.append(
            f"  {_LEVEL_TEXT[view.runner.level]} 마지막 폴링 {_fmt_lag(view.runner.lag_ms)} 전"
            f"  ·  마지막 알림 {_fmt_time(view.runner.last_notification_ms)}"
        )

    lines.append("데이터 신선도:")
    if view.freshness:
        for f in view.freshness:
            # 봉 수는 기본으로 안 센다(`--bar-count`, WAN-186) — 안 셌으면 아예 안 적는다.
            detail = f"지연 {_fmt_lag(f.lag_ms)}"
            if f.bar_count is not None:
                detail += f", {f.bar_count}봉"
            lines.append(
                f"  {_LEVEL_TEXT[f.level]} {f.symbol} {f.timeframe}"
                f"  최신 {_fmt_time(f.last_open_time)} ({detail})"
            )
    else:
        lines.append("  저장된 OHLCV 없음 — 먼저 수집을 실행하세요.")

    if view.positions:
        lines.append(f"오픈 페이퍼 포지션: {len(view.positions)}건")
    else:
        lines.append("오픈 페이퍼 포지션: 없음")

    if view.last_repair is not None:
        rep = view.last_repair
        detail = (
            f"{len(rep.repaired_series)} 시리즈에서 {rep.total_filled}봉 채움"
            if rep.repaired_series
            else "갭 없음"
        )
        if rep.total_remaining:
            detail += f", {rep.total_remaining}봉 잔여"
        if rep.has_error:
            detail += " ⚠️ 복구 오류"
        if rep.lookback_ms:
            # 「갭 없음」을 「전 구간 이상 없음」으로 읽지 않도록 창을 함께 밝힌다(WAN-187).
            detail += f" (최근 {rep.lookback_ms // 86_400_000}일 창)"
        lines.append(f"마지막 갭 복구: {_fmt_time(rep.ran_at_ms)} — {detail}")
        if rep.untracked_series:
            # 판정에서 뺐다고 화면에서까지 지우면 WAN-156과 같은 침묵이 된다(WAN-157).
            names = ", ".join(f"{u.symbol} {u.timeframe}" for u in rep.untracked_series)
            lines.append(f"  ⚠️ 저장돼 있으나 수집 대상이 아님(낡습니다): {names}")

    return "\n".join(lines)


def _build_health_view(settings: Settings, *, include_bar_count: bool = False) -> HealthView:
    return build_health_view(
        settings.db_path,
        include_bar_count=include_bar_count,
        runtime_state_path=settings.live_runtime_state_path,
        poll_interval_seconds=settings.live_poll_interval_seconds,
        stale_multiplier=settings.health_stale_multiplier,
        collector_heartbeat_path=settings.collector_heartbeat_path,
        collector_heartbeat_interval_seconds=settings.collector_heartbeat_interval_seconds,
        repair_state_path=settings.repair_state_path,
        cycle_budget_ms=runner_cycle_budget_ms(settings.live_signal_timeframes),
    )


def cmd_collect(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock collect` — 백필 후 실시간 스트림(또는 `--once`로 백필만)."""
    from data.collector import run_collector

    asyncio.run(
        run_collector(
            settings,
            run_stream=not args.once,
            repair_on_start=args.repair_on_start,
        )
    )
    return 0


def cmd_backfill(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock backfill --repair` — 내부 갭 복구 + 꼬리 신선도 판정(WAN-35/156).

    갭이 없어도 시리즈가 통째로 멈춰 있으면 종료 코드 1로 알린다.

    ⚠️ 수집 대상(`ALPHABLOCK_TIMEFRAMES`)이 아닌 TF는 **종료 코드를 흔들지 않는다**
    (WAN-157) — 고장이 아니라 설정이라 매번 빨간불로 찍으면 진짜 이상까지 무시하게
    된다. 대신 「저장돼 있으나 수집 대상이 아님(낡습니다)」로 계속 찍는다.
    """
    from data.freshness import format_stale
    from data.repair import run_repair

    summary = run_repair(settings, dry_run=args.dry_run)
    print(
        f"갭 복구: {len(summary.repaired_series)} 시리즈에서 {summary.total_filled}봉 채움"
        + (f", {summary.total_remaining}봉 잔여" if summary.total_remaining else "")
    )
    for s in summary.repaired_series:
        suffix = f" (오류: {s.error})" if s.error else ""
        print(
            f"  {s.symbol} {s.timeframe}: 갭 {s.gaps_found}개 → {s.bars_filled}봉 채움,"
            f" {s.bars_remaining}봉 잔여{suffix}"
        )
    if summary.stale_series:
        # 갭 복구로는 못 메우는 결함이라 「채운 봉 0」과 나란히 찍혀야 한다(WAN-156).
        print(f"🚨 수집 정지 {len(summary.stale_series)}건 — 갭 복구로는 메울 수 없습니다:")
        for stale in summary.stale_series:
            print(f"  {format_stale(stale)}")
        print("  → `alphablock history --days N` 으로 밀린 구간을 먼저 채우세요.")
    if summary.untracked_series:
        # 결함이 아니라 설정과 실제의 어긋남 — 보이되 종료 코드는 흔들지 않는다(WAN-157).
        print(
            f"ℹ️ 저장돼 있으나 수집 대상이 아님(낡습니다) {len(summary.untracked_series)}건"
            f" — 판정에서 제외했습니다:"
        )
        for untracked in summary.untracked_series:
            print(
                f"  {untracked.symbol} {untracked.timeframe}:"
                f" 최신 {_fmt_time(untracked.last_ms)} (지연 {_fmt_lag(untracked.lag_ms)})"
            )
        print(
            "  → 계속 쓸 TF면 `ALPHABLOCK_TIMEFRAMES`에 넣고 수집기를 재시작하세요"
            " (안 쓸 TF면 그대로 두어도 무해합니다)."
        )
    return 1 if summary.has_defect else 0


def cmd_history(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock history --days N` — 지정 구간을 심볼×TF별로 대량 백필(WAN-44)."""
    from data.history import run_history_backfill_with_settings

    symbols = args.symbols or settings.symbols
    timeframes = args.timeframes or ["1m"]
    results = run_history_backfill_with_settings(
        symbols,
        timeframes,
        days=args.days,
        settings=settings,
    )
    print(f"과거 백필 완료: {len(results)} 시리즈 ({args.days}일 창)")
    for r in results:
        # 완료 확인: 창 시작 도달 여부(WAN-51 재발 방지).
        reached = "OK" if r.reached_requested_start() else "미완(창 시작 미도달)"
        print(
            f"  {r.symbol} {r.timeframe}: 처리 {r.bars_written}봉,"
            f" 저장 총 {r.stored_after}봉, {r.elapsed_s:.1f}s [{reached}]"
        )
    return 0


def format_verify_report(report: VerifyReport) -> str:
    """검증 리포트를 사람이 읽는 여러 줄 텍스트로 요약한다(순수 함수, 테스트용)."""
    from data.freshness import format_stale

    lines: list[str] = ["OHLCV 무결성 검증", ""]
    lines.append("시리즈 (봉수 · 갭 · 중복):")
    for s in report.series:
        span = f"{_fmt_time(s.first_ms)} ~ {_fmt_time(s.last_ms)}"
        flags = []
        if s.has_gaps:
            flags.append(f"갭 {len(s.gaps)}개({s.missing}봉)")
        if s.duplicates:
            flags.append(f"중복 {s.duplicates}")
        if not s.monotonic:
            flags.append("역순!")
        status = ", ".join(flags) if flags else "OK"
        lines.append(f"  {s.symbol} {s.timeframe}: {s.bar_count}봉  [{span}]  {status}")

    lines.append("")
    # 손상(부분 봉·가격 불일치)과 거래량 노이즈를 갈라 찍는다 — 한 수로 뭉치면 감시가
    # 상시 빨간불이 되어 진짜 부분 봉이 묻힌다(WAN-327 §3).
    lines.append("1m→상위TF 리샘플 정합성 (손상 · 거래량 노이즈):")
    if report.parity:
        for p in report.parity:
            if p.ok and not p.noise:
                status = "OK"
            else:
                parts = []
                if p.damaged:
                    parts.append(f"🚨 손상 {len(p.damaged)}봉")
                if p.noise:
                    parts.append(f"거래량 노이즈 {len(p.noise)}봉(무해)")
                status = " · ".join(parts)
            lines.append(
                f"  {p.symbol} {p.source_timeframe}→{p.target_timeframe}:"
                f" {p.compared}버킷 비교  {status}"
            )
            for d in p.damaged[:3]:
                fields = ",".join(d.price_fields) if d.price_fields else "가격 일치"
                lines.append(
                    f"      {_fmt_time(d.open_time)} [{d.kind}]"
                    f" 거래량 {d.volume_ratio * 100:.1f}% · {fields}"
                    f" (최대 {d.max_price_bp:.1f}bp)"
                )
    else:
        lines.append("  비교 대상 없음(1m 또는 상위TF 미보유)")

    lines.append("")
    lines.append("꼬리 신선도(수집 정지):")
    if report.stale:
        # 갭·중복·정합성이 전부 깨끗해도 여기가 비지 않을 수 있다 — 그게 WAN-156이다.
        for stale in report.stale:
            lines.append(f"  🚨 {format_stale(stale)}")
    else:
        lines.append("  OK — 정지한 시리즈 없음")

    lines.append("")
    verdict = "통과" if report.sound else "실패"
    lines.append(
        f"판정: {verdict} (하드 실패 없음={report.ok}, 정지 {len(report.stale)}건,"
        f" 갭 총 {report.total_gaps}개, 손상 {report.total_damaged}봉,"
        f" 거래량 노이즈 {report.total_noise}봉)"
    )
    return "\n".join(lines)


def format_partial_bar_scan(scans: Sequence[SeriesScan], *, top: int = 15) -> str:
    """스캔 결과를 사람이 읽는 표로 요약한다(순수 함수, 테스트용).

    시리즈별 합계 + **손상 봉의 일자별(KST) 분포**를 낸다. 「몇 개·언제」가 이 스캔의
    질문이라(WAN-327 §1) 개별 봉이 아니라 날짜로 뭉쳐 보여 준다.
    """
    lines: list[str] = ["부분 봉 스캔 (저장 상위TF 봉 vs 같은 구간 1분봉 합)", ""]
    lines.append("시리즈 (비교 버킷 · 손상 · 거래량 노이즈):")
    for sc in scans:
        span = ""
        if sc.damaged_span is not None:
            lo, hi = sc.damaged_span
            span = f"  손상 구간 {_fmt_time(lo)} ~ {_fmt_time(hi)}"
        status = "OK" if sc.ok else f"🚨 손상 {len(sc.damaged)}봉"
        lines.append(
            f"  {sc.symbol} {sc.source_timeframe}→{sc.timeframe}: {sc.compared}버킷 비교"
            f"  {status} · 노이즈 {len(sc.noise)}봉{span}"
        )

    damaged = [d for sc in scans for d in sc.damaged]
    lines.append("")
    if not damaged:
        lines.append("손상 봉 없음 — 저장 상위TF 봉이 1분봉 합과 일치합니다.")
        return "\n".join(lines)

    by_day: dict[str, list[BarDiscrepancy]] = {}
    for d in damaged:
        by_day.setdefault(timefmt.format_kst(d.open_time)[:10], []).append(d)
    lines.append(f"손상 봉 일자별(KST) — 총 {len(damaged)}봉, {len(by_day)}일:")
    lines.append("  날짜        봉수  partial  price_only  최소 거래량%  최대 가격오차bp  종목")
    for day in sorted(by_day):
        items = by_day[day]
        syms = sorted({d.symbol.split("/")[0] for d in items})
        shown = ",".join(syms[:6]) + ("…" if len(syms) > 6 else "")
        partial = sum(1 for d in items if d.kind == "partial")
        lines.append(
            f"  {day}  {len(items):>4}  {partial:>7}  {len(items) - partial:>10}"
            f"  {min(d.volume_ratio for d in items) * 100:>11.1f}"
            f"  {max(d.max_price_bp for d in items):>15.1f}  {shown}"
        )
    if len(by_day) > top:
        lines.append(f"  (일자 {len(by_day)}개 전부 표시)")
    return "\n".join(lines)


def cmd_partial_bars(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock partial-bars` — 저장 상위TF 봉의 전 이력 부분 봉 스캔(WAN-327 §1).

    `verify`가 최근 표본만 보는 것과 달리 전 구간을 훑어 「몇 개·언제」를 센다. 읽기
    전용이고, 손상이 하나라도 있으면 종료 코드 1이라 감시에 물릴 수 있다.
    ⚠️ **고치지 않는다** — 수정은 사람이 하는 백필이다(WAN-194 원칙 · 자동 쓰기 금지).
    """
    from data.partial_bars import scan_all
    from data.storage import OhlcvStore

    symbols = args.symbols or settings.symbols
    timeframes = args.timeframes or ["4h", "1d"]
    start_ms = _parse_utc_day_ms(args.start)
    end_ms = _parse_utc_day_ms(args.end)
    store = OhlcvStore(settings.db_path)
    try:
        scans = scan_all(
            store,
            symbols,
            timeframes,
            start_ms=start_ms,
            end_ms=end_ms,
            chunk_days=args.chunk_days,
        )
    finally:
        store.close()
    print(format_partial_bar_scan(scans))
    if args.csv:
        path = _write_partial_bar_csv(scans, args.csv)
        print(f"\nCSV: {path}")
    return 0 if all(sc.ok for sc in scans) else 1


def _parse_utc_day_ms(text: str | None) -> int | None:
    """`YYYY-MM-DD`(UTC)를 epoch ms로. 데이터 창 인자라 표시용 KST가 아니라 UTC다."""
    if not text:
        return None
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _write_partial_bar_csv(scans: Sequence[SeriesScan], path: str) -> Path:
    """스캔 결과를 봉 단위 CSV로 적는다(손상·노이즈 전부 — 사후 분석용)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "symbol",
                "timeframe",
                "open_time",
                "open_time_kst",
                "kind",
                "volume_ratio",
                "resampled_volume",
                "stored_volume",
                "price_fields",
                "max_price_bp",
            ]
        )
        for sc in scans:
            for d in sc.discrepancies:
                writer.writerow(
                    [
                        d.symbol,
                        d.timeframe,
                        d.open_time,
                        timefmt.format_kst(d.open_time),
                        d.kind,
                        f"{d.volume_ratio:.6f}",
                        f"{d.resampled_volume:.6f}",
                        f"{d.stored_volume:.6f}",
                        "|".join(d.price_fields),
                        f"{d.max_price_bp:.3f}",
                    ]
                )
    return out


def cmd_verify(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock verify` — 저장된 OHLCV의 갭·중복·상위TF 정합성 검증(WAN-44)."""
    from data.storage import OhlcvStore
    from data.verify import verify_all

    symbols = args.symbols or settings.symbols
    timeframes = args.timeframes or ["1m", "15m", "1h", "4h", "1d"]
    store = OhlcvStore(settings.db_path)
    try:
        report = verify_all(
            store,
            symbols,
            timeframes,
            sample_buckets=args.sample_buckets,
            stale_multiplier=settings.health_stale_multiplier,
        )
    finally:
        store.close()
    print(format_verify_report(report))
    # 정지는 `sound`에 포함된다 — 「갭 0이라 통과」로 끝나 5일을 날린 것이 WAN-156이다.
    ok = report.strict_ok if args.strict else report.sound
    return 0 if ok else 1


def cmd_live(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock live` — 실시간 시그널 러너(페이퍼)."""
    from live.runner import run_signal_runner

    run_signal_runner(
        settings,
        once=args.once,
        dry_run=args.dry_run,
        test_message=args.test_message,
    )
    return 0


def cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock status` — 운영 상태 요약을 출력.

    환경변수가 채택 좌표를 덮어쓰고 있으면(WAN-309) 요약 뒤에 경고를 붙인다 —
    일치하면 아무것도 붙지 않는다.
    """
    from config.drift import check_coordinate_drift, render_drift_lines

    view = _build_health_view(settings, include_bar_count=args.bar_count)
    print(format_status(view, configured_symbols=settings.symbols))
    drift_lines = render_drift_lines(check_coordinate_drift(settings))
    if drift_lines:
        print()
        print("\n".join(drift_lines))
    return 0


def cmd_watch(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock watch` — 운영 상태 워치(이상 시 텔레그램 경고, WAN-32)."""
    from live.health_watch import run_health_watch

    run_health_watch(
        settings,
        once=args.once,
        dry_run=args.dry_run,
        test_message=args.test_message,
    )
    return 0


def cmd_fills(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock fills [--day YYYY-MM-DD]` — 당일(KST) 주문별 체결 여부 조회(WAN-232).

    `진입이 너무 안 됨`을 숫자로 가르는 첫 단계 — 그날 예약한 지정가가 하나씩 어떻게 됐는지
    (체결/미체결/거부)와 예약→체결·체결→진입 전환율을 낸다. 기본은 오늘, `--day`로 과거 지정.
    순수 조회라 종료 코드는 항상 0이다. `python -m live.fill_report --day`와 같은 산출물.
    """
    from live.fill_report import render_day_report, resolve_day_window
    from live.order_journal import OrderJournal

    db_path = args.db if args.db is not None else settings.db_path
    start_ms, end_ms, day_key = resolve_day_window(args.day)
    journal = OrderJournal(db_path)
    try:
        print(render_day_report(journal, start_ms=start_ms, end_ms=end_ms, day_key=day_key))
    finally:
        journal.close()
    return 0


def cmd_stop_width(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock stop-width [--day …] [--days N]` — 손절폭(1R) 해부(WAN-328).

    체결됐는데 **손절폭 가드(0.3%)에 걸려 진입하지 못한** 주문이 왜 그렇게 많은지를 두 축으로
    낸다: 창 안 라이브 체결의 손절폭·거부 사유 분포(§3)와, 같은 셋업을 백테와 짝지어 «진입가가
    갈렸나 · 무효화 경계가 갈렸나»를 지목하는 표(§1, `--with-backtest`).

    순수 조회라 종료 코드는 항상 0이다. 가드 값은 채택값을 **읽기만** 한다 — 바꾸는 것은
    WAN-76/79 소관이고 재-베이스라인 = 사용자 결정이다.

    🚨 **백테 쪽은 「셋업 행」을 먹인다(WAN-333)** — 거래 행(`backtest_timeline_rows`)에는
    존 정체성(조인 키)이 실리지 않아 짝이 **영원히 0건**이었다. 조인 인구조사가 그 상태를
    화면에 찍으므로 「짝 0건」이 표본 부족인지 배선 오류인지 구분된다.

    📌 `--symbol`·`--tf`는 **탐색용 옵트인**이다(WAN-305) — 안 주면 예전처럼 채택 좌표 전부를
    돈다. 좁힌 표는 그 좌표에서만의 결론이라 헤더가 좌표를 밝힌다.
    """
    from live.fill_report import resolve_day_window
    from live.order_journal import OrderJournal
    from live.stop_width_parity import build_report, render_report
    from live.trade_timeline import backtest_setup_rows, live_timeline_rows
    from paper.store import PaperTradeStore

    db_path = args.db if args.db is not None else settings.db_path
    end_start_ms, end_end_ms, day_key = resolve_day_window(args.day)
    days = max(1, args.days)
    start_ms = end_start_ms - (days - 1) * 86_400_000
    label = day_key if days == 1 else f"{days}일 창(끝 {day_key})"
    symbols = _split_csv(args.symbol)
    timeframes = _split_csv(args.tf)
    if symbols is not None or timeframes is not None:
        label += " · 좌표 " + _coordinate_label(symbols, timeframes)
    if days > 1 and args.with_backtest:
        # 백테 대조는 하루 단위 워밍업 규약(WAN-233/295)에 묶여 있다 — 여러 날을 한 번에
        # 돌리면 창마다 다른 워밍업이 섞여 조인이 조용히 어긋난다. 거부한다.
        print("--days > 1과 --with-backtest는 함께 쓸 수 없습니다(워밍업 규약이 하루 단위).")
        return 0

    journal = OrderJournal(db_path)
    try:
        backtest_rows = None
        live_rows = None
        if args.with_backtest:
            store = PaperTradeStore(db_path)
            try:
                live_rows = live_timeline_rows(journal, store, start_ms=start_ms, end_ms=end_end_ms)
            finally:
                store.close()
            backtest_rows = backtest_setup_rows(
                day_start_ms=start_ms,
                day_end_ms=end_end_ms,
                symbols=symbols,
                timeframes=timeframes,
                warmup_days=args.warmup_days,
                jobs=args.jobs,
            )
        report = build_report(
            journal,
            start_ms=start_ms,
            end_ms=end_end_ms,
            window_label=label,
            backtest_rows=backtest_rows,
            live_rows=live_rows,
        )
    finally:
        journal.close()
    print(render_report(report))
    return 0


def cmd_compare(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock compare [--day YYYY-MM-DD]` — 당일 라이브 vs 백테스트 대조(WAN-233).

    같은 KST 하루의 진입 깔때기(탭→예약→체결→진입)를 라이브 장부와 백테스트 채택 엔진으로
    나란히 낸다. 어느 단계에서 갈리는지가 "진입이 너무 안 됨"의 진단이다. 순수 조회라 종료
    코드는 항상 0이다. `python -m live.live_vs_backtest`와 같은 산출물.

    📌 `--symbol`·`--tf`는 **탐색용 옵트인**이다(WAN-333 §3b — `trades`·`stop-width`와 같은
    낱말·같은 규약). 안 주면 채택 좌표 전부를 돈다(WAN-305 원칙).
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES
    from live.live_vs_backtest import (
        DEFAULT_WARMUP_DAYS,
        compare_day,
        render_comparison,
        resolve_day_window,
    )
    from live.order_journal import OrderJournal

    db_path = args.db if args.db is not None else settings.db_path
    warmup_days = args.warmup_days if args.warmup_days is not None else DEFAULT_WARMUP_DAYS
    start_ms, end_ms, day_key = resolve_day_window(args.day)
    symbols = _split_csv(args.symbol) or list(DEFAULT_SYMBOLS)
    timeframes = _split_csv(args.tf) or list(DEFAULT_TIMEFRAMES)
    journal = OrderJournal(db_path)
    try:
        comp = compare_day(
            journal,
            day_start_ms=start_ms,
            day_end_ms=end_ms,
            day_key=day_key,
            symbols=symbols,
            timeframes=timeframes,
            warmup_days=warmup_days,
            jobs=args.jobs,
        )
    finally:
        journal.close()
    print(render_comparison(comp, by_cell=args.by_cell))
    return 0


def cmd_parity(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock parity [--start … --end …]` — 페이퍼↔백테스트 파리티 대조(WAN-247).

    여러 날 창을 묶어 (심볼·TF)별 **체결률·실현 R**을 라이브 장부와 백테스트 채택 엔진으로
    나란히 낸다. 낙관 측정이 아니라 파리티(배선) 감사다 — 둘 다 `baseline`·1분봉이라 큐
    우선순위를 모델링하지 않는다. 순수 조회라 종료 코드는 항상 0이다.
    """
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS
    from live.order_journal import OrderJournal
    from live.paper_parity import (
        build_parity_report,
        render_parity,
        resolve_cells,
        resolve_window,
    )
    from paper.store import PaperTradeStore

    if (args.start is None) != (args.end is None):
        print("--start와 --end는 함께 줘야 합니다(하나만 주면 창이 모호합니다).")
        return 0

    db_path = args.db if args.db is not None else settings.db_path
    warmup_days = args.warmup_days if args.warmup_days is not None else DEFAULT_WARMUP_DAYS
    journal = OrderJournal(db_path)
    store = PaperTradeStore(db_path)
    try:
        start_ms, end_ms, start_key, end_key = resolve_window(
            journal, store, start=args.start, end=args.end
        )
        cells = resolve_cells(journal, store, symbols=args.symbols, timeframes=args.tf)
        report = build_parity_report(
            journal,
            store,
            start_ms=start_ms,
            end_ms=end_ms,
            start_key=start_key,
            end_key=end_key,
            cells=cells,
            warmup_days=warmup_days,
            jobs=args.jobs,
        )
    finally:
        journal.close()
        store.close()
    print(render_parity(report, by_cell=args.by_cell))
    return 0


def cmd_trades(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock trades [--day YYYY-MM-DD]` — 당일(KST) 거래별 타임라인(WAN-234/239).

    들어간 셋업이 **언제 예약 → 얼마에 체결 → 어디서 청산 → 손익 얼마**였는지 거래 한 줄로
    본다. 라이브(주문 장부 + 페이퍼 라운드트립)가 주인공이고, 백테스트 채택 엔진을 대조로
    병기한다. 라이브 숫자는 `alphablock fills`/`compare` 당일 조회와 같은 장부·같은 창이다.

    백테 대조는 무거워(27칸 × 워밍업) **미리 계산해 캐시에 담고 조회는 캐시만 읽는다**
    (WAN-239): 기본 조회는 캐시를 읽고, 미스면 무거운 계산으로 **폴백하지 않고** "아직 계산
    안 됨"을 명시한다. 야간 크론은 `--persist-cache`로 전일 하루치를 미리 적재한다. 수동
    재계산은 `--recompute`(캐시 무시), 라이브만은 `--no-backtest`다. 순수 조회라 종료 코드는
    항상 0이다.

    📌 **지금 엔진 캐시가 없으면 보관 중인 옛 엔진 판을 라벨 달아 보여 준다(WAN-325)** —
    배포로 엔진 소스가 바뀌면 과거 날짜가 통째로 미스가 되는데(설계대로) 그 행은 지워지지
    않고 남아 있고, 하루치 재계산은 서버 6분 23초다(WAN-322). 배지(`백테 대조 엔진:`)가
    **옛 판의 이름·지문**으로 바뀌고 상태 줄이 「옛 엔진 결과」임을 밝힌다 — 즉 조용히
    내주지 않는다. 엄격 조회는 `--no-stale`.
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES
    from live.order_journal import OrderJournal
    from live.timeline_cache import TimelineCacheStore, current_engine_label, load_cached_day
    from live.trade_timeline import (
        STATUS_BACKTEST_CLOSED,
        DayTimeline,
        TimelineRow,
        backtest_timeline_rows,
        live_timeline_rows,
        render_day_timeline,
        resolve_day_window,
    )
    from paper.store import PaperTradeStore

    db_path = args.db if args.db is not None else settings.db_path
    start_ms, end_ms, day_key = resolve_day_window(args.day)
    symbols = _split_csv(args.symbol)
    timeframes = _split_csv(args.tf)

    # --- 캐시 정리 경로: 세어 보여 주고, `--prune-apply`가 있을 때만 지운다(WAN-297 §2-6). ---
    if args.prune_cache:
        return _prune_timeline_cache(args, db_path)

    # --- 야간 크론 적재 경로: 계산 후 저장만 하고 표는 렌더하지 않는다(WAN-239 §2). ---
    if args.persist_cache:
        return _persist_timeline_cache(
            args,
            db_path,
            day_key=day_key,
            symbols=symbols,
            timeframes=timeframes,
        )

    # --- 라이브 부분은 언제나 즉시 조회(가볍다). ---
    journal = OrderJournal(db_path)
    store = PaperTradeStore(db_path)
    try:
        live = live_timeline_rows(journal, store, start_ms=start_ms, end_ms=end_ms)
    finally:
        store.close()
        journal.close()

    backtest_rows: list[TimelineRow] = []
    engine_label: str | None = None
    status_note: str | None = None

    if args.no_backtest:
        status_note = "백테 대조 생략(`--no-backtest`) — 라이브만 봅니다."
    elif args.recompute:
        # 명시적 온디맨드 재계산(캐시 무시, 무겁다) — 사용자가 골랐을 때만.
        engine_label = current_engine_label()
        backtest_rows = backtest_timeline_rows(
            day_start_ms=start_ms,
            day_end_ms=end_ms,
            symbols=symbols,
            timeframes=timeframes,
            warmup_days=args.warmup_days,
            jobs=args.jobs,
        )
        status_note = "백테 대조 즉시 재계산(`--recompute`) — 캐시를 읽지 않았습니다."
    else:
        # 기본 조회: 캐시만 읽는다. 미스는 폴백하지 않고 명시한다(WAN-239 §3).
        syms = symbols if symbols is not None else list(DEFAULT_SYMBOLS)
        tfs = timeframes if timeframes is not None else list(DEFAULT_TIMEFRAMES)
        cache = TimelineCacheStore(db_path)
        try:
            result = load_cached_day(
                cache,
                day_key=day_key,
                symbols=syms,
                timeframes=tfs,
                warmup_days=args.warmup_days,
                allow_stale=not args.no_stale,
            )
        finally:
            cache.close()
        # WAN-297: 캐시에는 셋업 전부(청산·미진입·미체결·건너뜀)가 담긴다. 이 표는 WAN-234
        # 「거래별 타임라인」이라 **청산 행만** 싣는다 — `--recompute` 경로
        # (`backtest_timeline_rows`)와 같은 모양이고, 그 둘이 비트 동일함은 실데이터 회귀
        # 테스트가 고정한다(`test_cell_setup_timeline_closed_rows_match_cell_trades`).
        backtest_rows = [r for r in result.rows if r.status == STATUS_BACKTEST_CLOSED]
        # 배지는 **실제로 읽은 판**의 것이다(옛 판이면 옛 판의 이름·지문) — 배지가 지금
        # 엔진을 가리키면서 행은 옛 엔진인 상태가 이 저장소가 금지하는 조용한 실패다.
        engine_label = result.label
        notes: list[str] = []
        if result.stale is not None:
            stale = result.stale
            notes.append(
                f"⚠️ **옛 엔진 결과입니다({stale.num_cells}칸)** — 지금 엔진 캐시가 없어 "
                f"**{stale.created_label()}**에 계산해 둔 판을 대신 보여 줍니다(배포로 엔진이 "
                "바뀌면 과거 날짜가 미스가 되는데 옛 행은 지우지 않습니다 — WAN-325). "
                "**값이 지금 엔진과 다를 수 있고**, 라이브 열과의 차이를 집행 차이로 읽으면 "
                "안 됩니다(엔진이 바뀐 몫이 섞입니다). 최신 엔진 판은 `--persist-cache`나 "
                "`--recompute`로 만드세요. 엄격 조회는 `--no-stale`."
            )
        if result.misses:
            notes.append(
                f"🚨 백테 대조 **아직 계산 안 됨** — {len(result.misses)}/{len(syms) * len(tfs)}칸 "
                "캐시 미스(야간 크론 대기 또는 `--persist-cache`로 적재, 즉시 보려면 "
                "`--recompute`). 조회 시 무거운 재계산은 하지 않습니다."
            )
        if notes:
            status_note = "\n\n".join(notes)

    timeline = DayTimeline(day_key=day_key, live=tuple(live), backtest=tuple(backtest_rows))
    print(render_day_timeline(timeline, engine_label=engine_label, status_note=status_note))
    return 0


def _persist_timeline_cache(
    args: argparse.Namespace,
    db_path: str,
    *,
    day_key: str,
    symbols: list[str] | None,
    timeframes: list[str] | None,
) -> int:
    """`--persist-cache` 경로 — `--day`에서 거슬러 `--days N`일치 적재(WAN-239 §2 · WAN-297 §2).

    N일 루프가 여기 있는 이유는 **배포 뒤 되채우기**다(WAN-318 §5 런북이 손으로 돌리던 `for`
    루프의 자리) — 엔진 소스가 바뀌면 과거 캐시가 전부 미스되므로 화면에서 보려는 날짜를
    한 번에 채울 수 있어야 한다. 하루씩 순서대로 돌고, **한 날이 실패해도 나머지를 계속
    돌리지 않는다**(조용히 절반만 채워진 캐시를 만들지 않는다 — 예외는 그대로 올린다).
    """
    from datetime import timedelta

    from live.timeline_cache import TimelineCacheStore, persist_day
    from live.trade_timeline import resolve_day_window

    if args.days < 1:
        print("`--days`는 1 이상이어야 합니다.", file=sys.stderr)
        return 2

    first_day = date.fromisoformat(day_key)
    day_keys = [(first_day - timedelta(days=offset)).isoformat() for offset in range(args.days)]
    day_keys.reverse()  # 오래된 날부터 채운다(중간에 멈춰도 앞쪽이 이어진다).

    print(f"# 당일 백테 타임라인 캐시 적재 · {len(day_keys)}일 — WAN-239/297")
    cache = TimelineCacheStore(db_path)
    printed_label = False
    try:
        for key in day_keys:
            start_ms, end_ms, _ = resolve_day_window(key)
            report = persist_day(
                cache,
                day_start_ms=start_ms,
                day_end_ms=end_ms,
                day_key=key,
                symbols=symbols,
                timeframes=timeframes,
                warmup_days=args.warmup_days,
                jobs=args.jobs,
                replace=args.persist_replace,
            )
            if not printed_label:  # 엔진 배지는 한 번만(N일 루프에서 같은 줄이 반복되지 않게).
                print(f"엔진: **{report.label}**")
                printed_label = True
            print(
                f"{key} (KST): 적재 {len(report.persisted)}셀(셋업 {report.total_rows}행) · "
                f"건너뜀(지문 동일) {len(report.skipped)}셀"
            )
            if report.skipped and not args.persist_replace:
                print("  이미 같은 지문으로 적재돼 있습니다 — 다시 적재하려면 `--persist-replace`.")
    finally:
        cache.close()
    return 0


def _prune_timeline_cache(args: argparse.Namespace, db_path: str) -> int:
    """`--prune-cache` 경로 — 정리 후보를 세어 보여 주고, `--prune-apply`에만 삭제한다.

    **자동 삭제는 없다**(WAN-194 원칙 — "무엇을 지웠는지 모르는 DB"를 저장소가 스스로 만들지
    않는다). 기본 기준은 「지금 엔진 리비전이 **아닌** 셀」이고, `--prune-before`로 날짜
    기준을 더할 수 있다(합집합). 두 기준이 다 없으면 스토어가 `ValueError`로 거부한다.
    """
    from backtest.trade_store import engine_source_revision
    from live.timeline_cache import TimelineCacheStore

    keep_revision = None if args.prune_keep_all_revisions else engine_source_revision()
    if keep_revision is None and args.prune_before is None:
        print(
            "정리 기준이 없습니다 — `--prune-before`를 주거나 "
            "`--prune-keep-all-revisions`를 빼세요(기준 없는 일괄 삭제는 거부합니다).",
            file=sys.stderr,
        )
        return 2

    cache = TimelineCacheStore(db_path)
    try:
        candidates = cache.stale_cells(keep_revision=keep_revision, before_day=args.prune_before)
        print("# 타임라인 캐시 정리 — WAN-297 §2-6")
        criteria = []
        if keep_revision is not None:
            criteria.append(f"리비전 != `{keep_revision}`(지금 엔진)")
        if args.prune_before is not None:
            criteria.append(f"날짜 < `{args.prune_before}`(KST)")
        print(f"기준: {' 또는 '.join(criteria)}")
        by_revision: dict[str, int] = {}
        for cand in candidates:
            by_revision[cand.revision] = by_revision.get(cand.revision, 0) + 1
        print(f"후보 {len(candidates)}셀(셋업 {sum(c.num_rows for c in candidates)}행)")
        for revision, count in sorted(by_revision.items()):
            print(f"  - 리비전 `{revision}`: {count}셀")
        if not args.prune_apply:
            print("세기만 했습니다(읽기 전용) — 실제로 지우려면 `--prune-apply`.")
            return 0
        deleted = cache.delete_cells([c.run_id for c in candidates])
        print(f"삭제 {deleted}셀. (VACUUM은 하지 않습니다 — 사람이 판단합니다, WAN-194)")
    finally:
        cache.close()
    return 0


def _split_csv(value: str | None) -> list[str] | None:
    """`--symbol BTCUSDT,ETHUSDT` 같은 콤마 목록을 리스트로. 없으면 None(= 기본 좌표)."""
    if value is None:
        return None
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or None


def _coordinate_label(symbols: list[str] | None, timeframes: list[str] | None) -> str:
    """좁힌 좌표를 헤더에 밝힌다 (WAN-333 §3b — 좁힌 표는 그 좌표에서만의 결론이다)."""
    sym = ",".join(symbols) if symbols else "채택 전부"
    tfs = ",".join(timeframes) if timeframes else "채택 전부"
    return f"{sym} × {tfs}"


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock doctor` — DB 무결성·위생 점검(WAN-194 §2·§4·§5).

    읽기 전용이 기본이고, 이상이 하나라도 있으면 종료 코드 1을 낸다(cron·감시에서
    바로 쓸 수 있게). `--drop-recovery-artifacts`만 파괴적이며 명시적 옵트인이다.

    DB 점검에 앞서 환경 설정 드리프트(WAN-309)를 찍는다: 환경변수가 채택 좌표와
    다르면 경고, `.env.example`에만 있는 키는 목록 한 줄. **종료 코드에는 반영하지
    않는다** — 좁혀서 테스트 중일 수 있는 의도된 차이라 경고이지 에러가 아니고,
    doctor의 종료 코드는 DB 무결성 판정(`report.healthy`) 전용으로 남긴다.
    """
    from config.drift import (
        check_coordinate_drift,
        env_example_only_keys,
        render_drift_lines,
    )
    from data.integrity import (
        SalvageableRowsPresent,
        drop_recovery_artifacts,
        inspect,
        render_report,
        salvage_ohlcv,
    )

    db_path = args.db if args.db is not None else settings.db_path

    drift_lines = render_drift_lines(check_coordinate_drift(settings))
    if drift_lines:
        print("환경 설정 드리프트 (WAN-309):")
        print("\n".join(f"  {line}" for line in drift_lines))
        print()
    example_only = env_example_only_keys()
    if example_only:
        # 키 이름만 출력한다 — 값(비밀 포함)은 절대 싣지 않는다.
        print(
            f"ℹ️ `.env.example`에만 있는 키 {len(example_only)}개(코드 기본값으로 동작):"
            f" {', '.join(example_only)}"
        )
        print()

    if args.salvage_ohlcv is not None:
        # 인자 없이 `--salvage-ohlcv`만 주면 빈 리스트다 = "사라진 TF만 알아서".
        timeframes = tuple(args.salvage_ohlcv) or None
        results = salvage_ohlcv(db_path, timeframes=timeframes, dry_run=args.dry_run)
        if not results:
            print("복원할 캔들이 없습니다(복구 산출물이 없거나 유일본이 없음).")
        for result in results:
            if result.dry_run:
                # 안 썼으므로 "건너뜀"을 세지 않는다 — 0행 삽입은 결과가 아니라 미실행이다.
                print(f"복원 예정: `{result.timeframe}` 후보 {result.candidates:,}행 (쓰기 없음)")
            else:
                print(
                    f"복원: `{result.timeframe}` 후보 {result.candidates:,}행 →"
                    f" 삽입 {result.inserted:,}행 (중복·기존 {result.skipped:,}행 건너뜀)"
                )
        if not args.dry_run and results:
            print("⚠️ 기존 행은 덮어쓰지 않았다(충돌 시 살아 있는 쪽이 이긴다).")

    if args.drop_recovery_artifacts:
        try:
            dropped = drop_recovery_artifacts(db_path, force=args.force)
        except SalvageableRowsPresent as exc:
            print(f"🚨 거부: {exc}")
            return 1
        if dropped:
            print(f"복구 산출 테이블 삭제: {', '.join(dropped)}")
            print(
                "⚠️ 파일 크기는 아직 줄지 않았다(페이지가 프리리스트로 갔을 뿐이다)."
                " 줄이려면 러너·수집기를 멈춘 뒤 `VACUUM`을 직접 돌릴 것."
            )
        else:
            print("삭제할 복구 산출 테이블이 없습니다.")

    since_ms: int | None = None
    if args.orphans_since is not None:
        since = datetime.strptime(args.orphans_since, "%Y-%m-%d").replace(tzinfo=KST)
        since_ms = int(since.timestamp() * 1000)

    report = inspect(db_path, quick_check=not args.skip_quick_check, orphan_since_ms=since_ms)
    print(render_report(report))

    if not report.healthy and args.notify_on_failure:
        _notify_doctor_failure(report, settings)

    return 0 if report.healthy else 1


def _doctor_alert_text(report: IntegrityReport, hostname: str) -> str:
    """doctor 이상을 폰에서 한눈에 읽을 짧은 경고로 요약한다(WAN-185).

    `render_report`는 화면·로그용 전체 마크다운이라 텔레그램엔 길다 — 무결성 판정
    (`healthy`)을 무너뜨린 카테고리만 골라 한 줄로 만든다.

    🚨 **서식을 쓰지 않는다(WAN-321 §2).** 옛 판은 `*굵게*`·`` `코드` ``를 섞은 레거시
    Markdown이었는데, 본문에 실리는 것이 하필 **테이블·열 이름**(`open_positions`)이라
    밑줄이 「닫히지 않은 이탤릭」으로 읽혀 텔레그램이 **400으로 거부**했다 — 즉 이상이
    났을 때만 나가는 경고가 **정확히 그때 안 나갔다**. 알림은 읽히기만 하면 되므로
    표현력을 버리고 평문으로 보낸다(호출부가 `parse_mode=None`을 준다).
    """
    reasons: list[str] = []
    if not report.quick_check_ok:
        reasons.append("PRAGMA quick_check 손상")
    if report.recovery_artifacts:
        reasons.append(f"복구 산출물 {len(report.recovery_artifacts)}개")
    if report.orphan_fills:
        reasons.append(f"처분 미기록 체결 {len(report.orphan_fills)}건")
    if report.empty_cumulative_ledgers:
        names = ", ".join(t.name for t in report.empty_cumulative_ledgers)
        reasons.append(f"빈 누적 장부({names})")
    body = "; ".join(reasons) if reasons else "이상 감지"
    return (
        f"🚨 AlphaBlock DB 이상 — {hostname}\n"
        f"DB: {report.db_path}\n"
        f"{body}\n"
        f"서버에서 alphablock doctor 로 확인하세요."
    )


def _notify_doctor_failure(report: IntegrityReport, settings: Settings) -> None:
    """무결성 이상을 텔레그램으로 알린다 — 설정이 없으면 로그로만 남긴다(경고 유실 방지).

    전송은 **평문**이다(`parse_mode=None`, WAN-321 §2) — 경고문에 테이블·열 이름이 실려
    레거시 Markdown 파서가 400으로 거부하던 자리다.

    전송에 실패하면 **ERROR로 올리고 경고 본문을 함께 남긴다**(WAN-321 §2). 옛 판은
    `WARNING` 한 줄이라 「경보를 못 보냈다」는 사실 자체가 사실상 경보되지 않았고, 본문도
    남지 않아 무슨 이상이었는지 로그만 보고는 알 수 없었다. **종료 코드는 건드리지
    않는다** — doctor의 종료 코드는 DB 무결성 판정 전용이고(이미 이상이라 1이다), 전송
    실패로 그 뜻을 흐리지 않는다.
    """
    from common.telegram import build_telegram_client

    text = _doctor_alert_text(report, socket.gethostname())
    client = build_telegram_client(settings)
    if client is None:
        logger.warning("doctor 이상 감지 — 텔레그램 미설정으로 경고 미전송:\n%s", text)
        return
    if not client.send_message(text, parse_mode=None):
        logger.error(
            "🚨 doctor 이상 경고의 텔레그램 전송이 실패했습니다 — 폰으로는 아무것도 가지"
            " 않았습니다. 경고 본문:\n%s",
            text,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alphablock",
        description="AlphaBlock 실행 CLI — 수집·시그널 러너·상태 조회 (WAN-31)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="데이터 수집기(백필 + 실시간 스트림)")
    p_collect.add_argument(
        "--once",
        action="store_true",
        help="백필만 1회 수행하고 종료(실시간 스트림 없음)",
    )
    p_collect.add_argument(
        "--repair-on-start",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="시작 시 갭 자동 복구 1회 수행(기본: 설정값, 켬). --no-repair-on-start로 끔",
    )
    p_collect.set_defaults(func=cmd_collect)

    p_backfill = sub.add_parser("backfill", help="저장된 시리즈의 내부 갭을 1회 복구(WAN-35)")
    p_backfill.add_argument(
        "--repair",
        action="store_true",
        help="갭을 탐지해 그 구간만 재수집(현재 backfill의 유일한 동작)",
    )
    p_backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="복구 실패 시 텔레그램 경고를 보내지 않고 로그로만 남김",
    )
    p_backfill.set_defaults(func=cmd_backfill)

    p_history = sub.add_parser(
        "history",
        help="지정 구간 대량 백필(예: 1분봉 6개월/3년) — WAN-44",
    )
    p_history.add_argument(
        "--days",
        type=int,
        required=True,
        help="현재로부터 몇 일 전까지 백필할지(예: 6개월=180, 3년=1095)",
    )
    p_history.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="대상 심볼(기본: 설정 symbols). 예: BTC/USDT:USDT ETH/USDT:USDT",
    )
    p_history.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="대상 타임프레임(기본: 1m). 예: 1m",
    )
    p_history.set_defaults(func=cmd_history)

    p_verify = sub.add_parser(
        "verify",
        help="저장된 OHLCV의 갭·중복·상위TF 정합성 검증 — WAN-44",
    )
    p_verify.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="대상 심볼(기본: 설정 symbols)",
    )
    p_verify.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="검증할 타임프레임(기본: 1m 15m 1h 4h 1d)",
    )
    p_verify.add_argument(
        "--sample-buckets",
        type=int,
        default=500,
        help="정합성 비교에 쓸 상위TF 최근 봉 표본 수(기본 500)",
    )
    p_verify.add_argument(
        "--strict",
        action="store_true",
        help="갭이 하나라도 있으면 실패로 처리(기본: 갭은 경고만, 중복·역순·불일치만 실패)",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_partial = sub.add_parser(
        "partial-bars",
        help="저장 상위TF 봉의 전 이력 부분 봉 스캔(읽기 전용) — WAN-327",
    )
    p_partial.add_argument(
        "--symbols", nargs="+", default=None, help="대상 심볼(기본: 설정 symbols)"
    )
    p_partial.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="대상 상위TF(기본: 4h 1d). 1분봉과 대조한다",
    )
    p_partial.add_argument("--start", default=None, metavar="YYYY-MM-DD", help="창 시작(UTC)")
    p_partial.add_argument("--end", default=None, metavar="YYYY-MM-DD", help="창 끝(UTC)")
    p_partial.add_argument(
        "--chunk-days", type=int, default=120, help="1분봉 로딩 창(일, 기본 120) — 메모리 노브"
    )
    p_partial.add_argument("--csv", default=None, help="봉 단위 결과를 이 경로에 CSV로 저장")
    p_partial.set_defaults(func=cmd_partial_bars)

    p_live = sub.add_parser("live", help="실시간 시그널 러너(페이퍼)")
    p_live.add_argument("--once", action="store_true", help="한 번만 폴링하고 종료")
    p_live.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 로그로만 출력")
    p_live.add_argument(
        "--test-message",
        action="store_true",
        help="테스트 메시지를 한 번 보내고 종료(텔레그램 연결 확인)",
    )
    p_live.set_defaults(func=cmd_live)

    p_status = sub.add_parser("status", help="운영 상태(Health) 요약 출력")
    p_status.add_argument(
        "--bar-count",
        action="store_true",
        help="시리즈별 저장 봉 수도 센다(대용량 DB에서 수십 초 걸릴 수 있음)",
    )
    p_status.set_defaults(func=cmd_status)

    p_watch = sub.add_parser("watch", help="운영 상태 워치(이상 시 텔레그램 경고)")
    p_watch.add_argument("--once", action="store_true", help="한 번만 점검하고 종료")
    p_watch.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 로그로만 출력")
    p_watch.add_argument(
        "--test-message",
        action="store_true",
        help="테스트 메시지를 한 번 보내고 종료(텔레그램 연결 확인)",
    )
    p_watch.set_defaults(func=cmd_watch)

    p_fills = sub.add_parser(
        "fills",
        help="당일(KST) 주문별 체결 여부 조회 — 오늘 예약한 지정가가 체결됐나(WAN-232)",
    )
    p_fills.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    p_fills.add_argument(
        "--day",
        default="today",
        metavar="YYYY-MM-DD",
        help="조회할 KST 날짜(기본: 오늘). 예: 2026-08-02",
    )
    p_fills.set_defaults(func=cmd_fills)

    p_stop_width = sub.add_parser(
        "stop-width",
        help="손절폭(1R) 해부 — 가드 0.3%에 걸린 체결의 분포·라이브 대 백테 귀속(WAN-328)",
    )
    p_stop_width.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    p_stop_width.add_argument(
        "--day",
        default="today",
        metavar="YYYY-MM-DD",
        help="창의 마지막 KST 날짜(기본: 오늘). 예: 2026-08-17",
    )
    p_stop_width.add_argument(
        "--days", type=int, default=1, help="--day에서 거슬러 올라갈 일수(§3 표본 확대)"
    )
    p_stop_width.add_argument(
        "--with-backtest",
        action="store_true",
        help="같은 셋업 백테 대조(§1)까지 낸다 — 채택 좌표 48셀 × 워밍업이라 무겁다",
    )
    p_stop_width.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help="백테스트 워밍업 길이(일). 라이브 전-이력 존 재고 근사 노브 — 길수록 느리다",
    )
    p_stop_width.add_argument(
        "--jobs", type=int, default=1, help="백테스트 (심볼, TF) 병렬 워커 수(기본 1)"
    )
    p_stop_width.add_argument(
        "--symbol",
        default=None,
        metavar="SYM[,SYM…]",
        help="백테 대조 좌표를 좁힌다(탐색용 옵트인). 미지정 = 채택 유니버스 전부",
    )
    p_stop_width.add_argument(
        "--tf",
        default=None,
        metavar="TF[,TF…]",
        help="백테 대조 TF를 좁힌다(탐색용 옵트인). 미지정 = 채택 작업 TF 전부",
    )
    p_stop_width.set_defaults(func=cmd_stop_width)

    p_compare = sub.add_parser(
        "compare",
        help="당일(KST) 라이브 vs 백테스트 대조 — 탭→예약→체결→진입 funnel(WAN-233)",
    )
    p_compare.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    p_compare.add_argument(
        "--day",
        default="today",
        metavar="YYYY-MM-DD",
        help="대조할 KST 날짜(기본: 오늘). 예: 2026-08-02",
    )
    p_compare.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help="백테스트 워밍업 길이(일). 라이브 전-이력 존 재고 근사 노브 — 길수록 느리다",
    )
    p_compare.add_argument("--by-cell", action="store_true", help="심볼×TF별 대조 표도 출력")
    p_compare.add_argument(
        "--symbol",
        default=None,
        metavar="SYM[,SYM…]",
        help="백테 대조 좌표를 좁힌다(탐색용 옵트인). 미지정 = 채택 유니버스 전부",
    )
    p_compare.add_argument(
        "--tf",
        default=None,
        metavar="TF[,TF…]",
        help="백테 대조 TF를 좁힌다(탐색용 옵트인). 미지정 = 채택 작업 TF 전부",
    )
    p_compare.add_argument(
        "--jobs", type=int, default=1, help="백테스트 (심볼, TF) 병렬 워커 수(기본 1)"
    )
    p_compare.set_defaults(func=cmd_compare)

    p_parity = sub.add_parser(
        "parity",
        help="페이퍼↔백테스트 파리티 — 창별 체결률·실현 R 대조(WAN-247)",
    )
    p_parity.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    p_parity.add_argument(
        "--start", default=None, metavar="YYYY-MM-DD", help="창 시작(KST). --end와 함께"
    )
    p_parity.add_argument(
        "--end", default=None, metavar="YYYY-MM-DD", help="창 끝(KST, 포함). --start와 함께"
    )
    p_parity.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help="백테스트 워밍업 길이(일). 라이브 전-이력 존 재고 근사 노브 — 길수록 느리다",
    )
    p_parity.add_argument("--symbols", default=None, help="대조 심볼(콤마, 기본: 장부에 있는 셀)")
    p_parity.add_argument("--tf", default=None, help="대조 TF(콤마, 기본: 장부에 있는 셀)")
    p_parity.add_argument("--by-cell", action="store_true", help="심볼×TF별 대조 표도 출력")
    p_parity.add_argument(
        "--jobs", type=int, default=1, help="백테스트 (심볼, TF) 병렬 워커 수(기본 1)"
    )
    p_parity.set_defaults(func=cmd_parity)

    p_trades = sub.add_parser(
        "trades",
        help="당일(KST) 거래별 타임라인 — 예약→체결가→청산가→손익, 라이브|백테스트(WAN-234)",
    )
    p_trades.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    p_trades.add_argument(
        "--day",
        default="today",
        metavar="YYYY-MM-DD",
        help="조회할 KST 날짜(기본: 오늘). 예: 2026-08-02",
    )
    p_trades.add_argument(
        "--no-backtest",
        action="store_true",
        help="백테스트 대조를 생략하고 라이브만 빠르게(27셀 × 워밍업이 무겁다)",
    )
    p_trades.add_argument(
        "--persist-cache",
        action="store_true",
        help="백테 대조를 미리 계산해 캐시에 적재만 하고 종료(야간 크론, WAN-239 §2)",
    )
    p_trades.add_argument(
        "--persist-replace",
        action="store_true",
        help="적재 시 같은 지문의 셀이 있어도 덮어쓴다(`--persist-cache`와 함께)",
    )
    p_trades.add_argument(
        "--days",
        type=int,
        default=1,
        metavar="N",
        help=(
            "`--persist-cache`와 함께: `--day`에서 **거슬러** N일치를 적재한다(기본 1 = "
            "그날만). 배포 뒤 되채우기용(WAN-297 §2)"
        ),
    )
    p_trades.add_argument(
        "--prune-cache",
        action="store_true",
        help=(
            "캐시 정리 후보를 **세어 보여만** 준다(옛 엔진 리비전 셀 · `--prune-before` 이전 "
            "날짜). 실제 삭제는 `--prune-apply`를 함께 줘야 한다(WAN-297 §2-6)"
        ),
    )
    p_trades.add_argument(
        "--prune-before",
        default=None,
        metavar="YYYY-MM-DD",
        help="정리 기준에 「이 KST 날짜 이전의 셀」을 추가한다(`--prune-cache`와 함께)",
    )
    p_trades.add_argument(
        "--prune-keep-all-revisions",
        action="store_true",
        help=(
            "정리에서 리비전 기준을 뺀다 — 옛 엔진 셀을 남기고 `--prune-before`만 본다"
            "(`--prune-cache`와 함께)"
        ),
    )
    p_trades.add_argument(
        "--prune-apply",
        action="store_true",
        help="`--prune-cache`가 센 셀을 **실제로 삭제**한다(파괴적 · 명시 옵트인)",
    )
    p_trades.add_argument(
        "--recompute",
        action="store_true",
        help="캐시를 무시하고 백테 대조를 즉시 재계산(무겁다 — 수동 확인용, WAN-239)",
    )
    p_trades.add_argument(
        "--no-stale",
        action="store_true",
        help=(
            "지금 엔진 캐시가 없을 때 **옛 엔진 판으로 대신 보여 주지 않는다**(WAN-325). "
            "기본은 라벨을 달아 보여 준다 — 스크립트가 「오늘 엔진으로 적재됐나」를 판정할 "
            "때만 이 플래그를 쓴다"
        ),
    )
    p_trades.add_argument(
        "--symbol",
        default=None,
        metavar="SYM[,SYM...]",
        help="백테스트 대조 심볼(콤마 목록). 생략 시 채택 좌표 전 종목",
    )
    p_trades.add_argument(
        "--tf",
        default=None,
        metavar="TF[,TF...]",
        help="백테스트 대조 TF(콤마 목록). 생략 시 15m,1h,4h",
    )
    p_trades.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help="백테스트 워밍업 길이(일). 라이브 전-이력 존 재고 근사 노브 — 길수록 느리다",
    )
    p_trades.add_argument(
        "--jobs", type=int, default=1, help="백테스트 (심볼, TF) 병렬 워커 수(기본 1)"
    )
    p_trades.set_defaults(func=cmd_trades)

    p_doctor = sub.add_parser(
        "doctor",
        help="DB 무결성·위생 점검(손상·복구 산출물·빈 장부·처분 미기록 체결, WAN-194)",
    )
    p_doctor.add_argument("--db", default=None, help="점검할 DB 경로(기본: 설정의 db_path)")
    p_doctor.add_argument(
        "--skip-quick-check",
        action="store_true",
        help="`PRAGMA quick_check`를 건너뛴다(수 GB DB에서 느림 — 인구조사만 볼 때)",
    )
    p_doctor.add_argument(
        "--orphans-since",
        default=None,
        metavar="YYYY-MM-DD",
        help="이 날짜(KST) 이후 체결만 처분 미기록으로 본다(WAN-194 열 도입 이전은 판별 불가)",
    )
    p_doctor.add_argument(
        "--salvage-ohlcv",
        nargs="*",
        default=None,
        metavar="TF",
        help=(
            "복구 산출물에 갇힌 캔들을 `ohlcv`로 되돌린다(기존 행은 덮어쓰지 않음)."
            " 인자 없이 주면 본 테이블에서 사라진 TF만, TF를 주면 그것만(WAN-195)"
        ),
    )
    p_doctor.add_argument(
        "--dry-run",
        action="store_true",
        help="`--salvage-ohlcv`를 세기만 하고 쓰지 않는다",
    )
    p_doctor.add_argument(
        "--drop-recovery-artifacts",
        action="store_true",
        help="`lost_and_found` 등 `.recover` 산출 테이블을 삭제한다(파괴적 — VACUUM은 안 한다)",
    )
    p_doctor.add_argument(
        "--force",
        action="store_true",
        help="복원 가능한 유일본이 남아 있어도 드롭한다(기본은 거부 — WAN-195)",
    )
    p_doctor.add_argument(
        "--notify-on-failure",
        action="store_true",
        help="무결성 이상 시 텔레그램 경고를 보낸다(systemd 타이머·cron 감시용 — WAN-185)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    """콘솔 스크립트 진입점(`alphablock`)."""
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    func = args.func
    result: int = func(args, settings)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
