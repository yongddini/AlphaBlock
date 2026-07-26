"""AlphaBlock 실행 CLI 구현 (WAN-31).

기존 진입점(`data.collector.run_collector`, `live.runner.run_signal_runner`,
`dashboard.health_data.build_health_view`)을 얇게 감싸 한 줄 명령으로 노출한다.
비즈니스 로직은 각 모듈에 있고, 여기서는 인자 파싱과 배선만 담당한다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from common import timefmt
from common.timefmt import KST
from config import get_settings
from config.settings import Settings
from dashboard.health import HealthLevel
from dashboard.health_data import HealthView, build_health_view

if TYPE_CHECKING:
    from data.verify import VerifyReport

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
    lines.append("1m→상위TF 리샘플 정합성:")
    if report.parity:
        for p in report.parity:
            status = "OK" if p.ok else f"불일치 {len(p.mismatches)}건"
            lines.append(
                f"  {p.symbol} {p.source_timeframe}→{p.target_timeframe}:"
                f" {p.compared}버킷 비교  {status}"
            )
            for m in p.mismatches[:3]:
                lines.append(
                    f"      {_fmt_time(m.open_time)} {m.field}:"
                    f" 리샘플 {m.resampled} ≠ 저장 {m.stored}"
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
        f" 갭 총 {report.total_gaps}개)"
    )
    return "\n".join(lines)


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
    """`alphablock status` — 운영 상태 요약을 출력."""
    view = _build_health_view(settings, include_bar_count=args.bar_count)
    print(format_status(view, configured_symbols=settings.symbols))
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


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock doctor` — DB 무결성·위생 점검(WAN-194 §2·§4·§5).

    읽기 전용이 기본이고, 이상이 하나라도 있으면 종료 코드 1을 낸다(cron·감시에서
    바로 쓸 수 있게). `--drop-recovery-artifacts`만 파괴적이며 명시적 옵트인이다.
    """
    from data.integrity import (
        SalvageableRowsPresent,
        drop_recovery_artifacts,
        inspect,
        render_report,
        salvage_ohlcv,
    )

    db_path = args.db if args.db is not None else settings.db_path

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
    return 0 if report.healthy else 1


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
