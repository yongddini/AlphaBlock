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
import os
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from common import timefmt
from common.timefmt import KST
from config import get_settings
from config.settings import Settings
from dashboard.health import HealthLevel, SeriesFreshness, runner_cycle_budget_ms
from dashboard.health_data import HealthView, build_health_view, monitor_scope_for
from data.repair import format_plan_excluded
from data.tick_probe import MARKET_LABEL, SOURCE_LABEL

if TYPE_CHECKING:
    from data.funding import FundingRateStore
    from data.integrity import IntegrityReport
    from data.partial_bars import BarDiscrepancy, SeriesScan
    from data.tick_probe import ProbeResult, Projection, RestAvailability
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


def _funding_lines(view: HealthView) -> list[str]:
    """펀딩 신선도 절(WAN-407 §2).

    🚨 **판정 자를 새로 만드는 게 아니라 화면에 연결하는 일이다** — `HealthView.funding`은
    이미 계산돼 있었고(`dashboard.health.compute_funding_status`) **종합 배지(`compute_overall`)
    에도 이미 들어가 있었다**. 그런데 이 CLI는 OHLCV만 그려서, 펀딩이 멈추면 **배지만 노랗게
    변하고 이유는 어디에도 안 보였다** — 이 저장소가 반복해 데인 「실패가 성공과 같은 모양」
    (WAN-194/318 §3/321)의 펀딩 축이다.

    ⚠️ **왜 이게 조용하면 위험한가**: 우리는 롱 온리이고 무기한선물에서 롱은 대개 펀딩을
    **지불**한다. 페이퍼 장부는 펀딩 데이터가 없으면 `funding_pct`를 그냥 0으로 두므로
    (`paper/store.py`), 수집이 멈추면 성과가 **항상 실제보다 좋게** 나온다.

    📌 **예측 행 주의** — 거래소는 다음 정산을 미리 준다. 그 행의 `funding_time`은 **미래**라
    `(예측)`을 함께 찍는다(확정 이력이 아니라는 뜻).

    🚨 **판정(지연·상태)은 마지막 확정 정산으로 낸다(WAN-422 §2).** 옛 문장은 *「판정에 쓰는
    행이 예측일 수 있어 최대 ~8시간 낙관 — 늦을 뿐 놓치지는 않는다」*였는데 **틀렸다**:
    예측 행은 수집기가 5분마다 다음 정산으로 갱신하므로 **확정이 영원히 멈춰도 늘 미래**였고,
    2026-09-07~18 확정 정지를 12일간 12종목 전부 `[OK]`로 찍었다. 이제 확정 시각을 함께
    찍고 그 지연으로 판정한다(예측 행은 **표시만**).
    """
    lines = ["펀딩 신선도:"]
    if not view.funding:
        lines.append("  대상 심볼 없음 — 수집 대상 설정을 확인하세요.")
        return lines
    for f in view.funding:
        if f.funding_time is None:
            lines.append(
                f"  {_LEVEL_TEXT[f.level]} {f.symbol}  저장된 펀딩 없음"
                " — 백테스트·페이퍼가 이 종목의 펀딩비를 0으로 계산합니다."
            )
            continue
        predicted = " (예측)" if f.is_predicted else ""
        confirmed = (
            "확정 이력 없음"
            if f.confirmed_funding_time is None
            else f"마지막 확정 {_fmt_time(f.confirmed_funding_time)}"
        )
        lines.append(
            f"  {_LEVEL_TEXT[f.level]} {f.symbol}"
            f"  최신 {_fmt_time(f.funding_time)}{predicted}"
            f" · {confirmed} (지연 {_fmt_lag(f.lag_ms)})"
        )
    if any(f.level is HealthLevel.STALE for f in view.funding):
        lines.append(
            "  ⚠️ 펀딩 수집이 멈추면 결측 구간 펀딩비가 **0으로 계산**됩니다 — 롱 온리라"
            " 성과가 실제보다 좋게 나옵니다"
            " (`uv run python -m data.funding --backfill-only`로 메웁니다)."
        )
    return lines


def _excluded_lines(view: HealthView) -> list[str]:
    """저장돼 있으나 지금 수집 계획에 없는 시리즈(WAN-458) — 판정에서 뺀 정보 표시.

    종목마다 한 줄로 묶는다(팔 목록에서 빠진 종목은 TF가 9개라 한 줄씩 찍으면 주마다 쌓인다).
    판정에서 뺐다고 화면에서까지 지우면 WAN-156과 같은 침묵이 된다(WAN-157).
    """
    if not view.monitor_scope_applied:
        return ["  ℹ️ 수집 계획을 못 읽어 저장된 시리즈 전부를 판정합니다(옛 동작)."]
    if not view.excluded:
        return []
    by_symbol: dict[str, list[SeriesFreshness]] = {}
    for f in view.excluded:
        by_symbol.setdefault(f.symbol, []).append(f)
    lines = [
        f"  ℹ️ 수집 대상 아님(목록 제외) {len(by_symbol)}종목 · {len(view.excluded)}시리즈"
        " — 판정·경보에서 뺐습니다(낡습니다):"
    ]
    for symbol in sorted(by_symbol):
        rows = by_symbol[symbol]
        tfs = ",".join(f.timeframe for f in sorted(rows, key=lambda r: r.expected_interval_ms or 0))
        last = max((f.last_open_time for f in rows if f.last_open_time is not None), default=None)
        lines.append(f"    {symbol} [{tfs}]  최신 {_fmt_time(last)}")
    return lines


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
        if extra and view.monitor_scope_applied:
            # 수집 계획으로 좁힌 뒤에도 남은 설정 밖 종목은 팔 목록·보유 종목이다(WAN-458) —
            # 낡는 게 아니라 지금 모으고 있다. 낡는 것은 아래 「목록 제외」 절이 따로 찍는다.
            lines.append(f"  ℹ️ 설정 밖이지만 수집 계획(팔 목록·보유)에 있음: {len(extra)}종목")
        elif extra:
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
    lines.extend(_excluded_lines(view))

    lines.extend(_funding_lines(view))

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
        tf_untracked = [u for u in rep.untracked_series if u.reason == "timeframe"]
        plan_untracked = [u for u in rep.untracked_series if u.reason == "plan"]
        if tf_untracked:
            # 판정에서 뺐다고 화면에서까지 지우면 WAN-156과 같은 침묵이 된다(WAN-157).
            names = ", ".join(f"{u.symbol} {u.timeframe}" for u in tf_untracked)
            lines.append(f"  ⚠️ 저장돼 있으나 수집 대상이 아님(낡습니다): {names}")
        if plan_untracked:
            # 팔 목록에서 빠진 종목 등 — 매주 교체의 정상 결과다(WAN-466).
            lines.append(f"  ℹ️ {format_plan_excluded(plan_untracked)}")

    return "\n".join(lines)


def _build_health_view(settings: Settings, *, include_bar_count: bool = False) -> HealthView:
    return build_health_view(
        settings.db_path,
        include_bar_count=include_bar_count,
        # 펀딩 판정 대상은 **설정 유니버스**를 따라간다(WAN-407 §2, 리터럴 하드코딩 금지).
        # 기본값(저장된 심볼 전체)으로 두면 옛 유니버스 잔재(수집 대상이 아닌 심볼)가 영원히
        # STALE로 떠 상시 빨간불이 된다 — 진짜 이상과 구분되지 않는 그 실패를 WAN-321이
        # 고쳤고, 여기서 되살리지 않는다.
        funding_symbols=list(settings.symbols),
        runtime_state_path=settings.live_runtime_state_path,
        poll_interval_seconds=settings.live_poll_interval_seconds,
        stale_multiplier=settings.health_stale_multiplier,
        collector_heartbeat_path=settings.collector_heartbeat_path,
        collector_heartbeat_interval_seconds=settings.collector_heartbeat_interval_seconds,
        repair_state_path=settings.repair_state_path,
        cycle_budget_ms=runner_cycle_budget_ms(settings.live_signal_timeframes),
        # 신선도 판정 = 지금 수집 계획(WAN-458) — 워치와 같은 함수(화면과 폰이 같은 대상).
        scope=monitor_scope_for(settings),
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
    tf_untracked = [u for u in summary.untracked_series if u.reason == "timeframe"]
    plan_untracked = [u for u in summary.untracked_series if u.reason == "plan"]
    if plan_untracked:
        # 팔 목록에서 빠진 종목 등 — 정상 결과라 종료 코드를 흔들지 않는다(WAN-466).
        print(f"ℹ️ {format_plan_excluded(plan_untracked)} — 판정에서 제외했습니다.")
    if tf_untracked:
        # 결함이 아니라 설정과 실제의 어긋남 — 보이되 종료 코드는 흔들지 않는다(WAN-157).
        print(
            f"ℹ️ 저장돼 있으나 수집 대상이 아님(낡습니다) {len(tf_untracked)}건"
            f" — 판정에서 제외했습니다:"
        )
        for untracked in tf_untracked:
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
        f" 노이즈(거래량·가격) {report.total_noise}봉)"
    )
    return "\n".join(lines)


def format_partial_bar_scan(scans: Sequence[SeriesScan], *, top: int = 15) -> str:
    """스캔 결과를 사람이 읽는 표로 요약한다(순수 함수, 테스트용).

    시리즈별 합계 + **손상 봉의 일자별(KST) 분포**를 낸다. 「몇 개·언제」가 이 스캔의
    질문이라(WAN-327 §1) 개별 봉이 아니라 날짜로 뭉쳐 보여 준다.
    """
    lines: list[str] = ["부분 봉 스캔 (저장 상위TF 봉 vs 같은 구간 1분봉 합)", ""]
    lines.append("시리즈 (비교 버킷 · 손상 · 노이즈[거래량·가격]):")
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
    # 가격 오차는 **틱 배수**가 판정자다(WAN-337 §2) — bp는 사람이 크기를 가늠하는 참고 열로
    # 남긴다(두 축은 같은 순서로 정렬되지도 않는다: `PRICE_NOISE_TICKS` 주석의 실측).
    lines.append(
        "  날짜        봉수  partial  price_only  최소 거래량%  최대 가격오차틱  (bp)  종목"
    )
    for day in sorted(by_day):
        items = by_day[day]
        syms = sorted({d.symbol.split("/")[0] for d in items})
        shown = ",".join(syms[:6]) + ("…" if len(syms) > 6 else "")
        partial = sum(1 for d in items if d.kind == "partial")
        lines.append(
            f"  {day}  {len(items):>4}  {partial:>7}  {len(items) - partial:>10}"
            f"  {min(d.volume_ratio for d in items) * 100:>11.1f}"
            f"  {max(d.max_price_ticks for d in items):>15.1f}"
            f"  {max(d.max_price_bp for d in items):>6.1f}  {shown}"
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
                "max_price_ticks",
                "price_tick",
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
                        f"{d.max_price_ticks:.3f}",
                        f"{d.price_tick:.10g}",
                        f"{d.max_price_bp:.3f}",
                    ]
                )
    return out


def _fmt_bytes(value: float) -> str:
    """사람이 읽는 크기. `data.integrity._fmt_bytes`와 같은 자(표를 나란히 읽는다)."""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{int(size)}B" if unit == "B" else f"{size:,.1f}{unit}"
        size /= 1024.0
    return f"{size:,.1f}TB"


def format_tick_probe_grid(results: Sequence[ProbeResult]) -> str:
    """§0 격자 표 — 없는 칸도 「없음」으로 남긴다(조용히 빼지 않는다)."""
    lines = ["## §0-A 하루치 실측 (칸 = 종목 × 시장 × 자료)", ""]
    if not results:
        return "\n".join([*lines, "(잰 칸 없음)"])
    lines.append(
        f"{'종목':<10} {'시장':<12} {'자료':<8} {'행 수':>12} {'받은(zip)':>11}"
        f" {'푼 CSV':>11} {'gzip(최소열)':>12} {'SQLite':>11} {'받기s':>7} {'넣기s':>7}"
    )
    lines.append("-" * 118)
    for res in results:
        if not res.available:
            lines.append(
                f"{res.spec.symbol:<10} {MARKET_LABEL[res.spec.market]:<12}"
                f" {SOURCE_LABEL[res.spec.source]:<8} {'— 없음':>12}   {res.note}"
            )
            continue
        lines.append(
            f"{res.spec.symbol:<10} {MARKET_LABEL[res.spec.market]:<12}"
            f" {SOURCE_LABEL[res.spec.source]:<8} {res.rows:>12,}"
            f" {_fmt_bytes(res.download_bytes):>11} {_fmt_bytes(res.raw_bytes):>11}"
            f" {_fmt_bytes(res.gzip_bytes):>12} {_fmt_bytes(res.sqlite_bytes):>11}"
            f" {res.download_s:>7.1f} {res.ingest_s:>7.1f}"
        )
    return "\n".join(lines)


def format_tick_probe_ratios(results: Sequence[ProbeResult]) -> str:
    """같은 종목에서 「체결내역 ÷ 1초봉」 배수 — 추정이 아니라 잰 값이다."""
    by_symbol: dict[str, dict[tuple[str, str], ProbeResult]] = {}
    for res in results:
        if res.available:
            by_symbol.setdefault(res.spec.symbol, {})[(res.spec.market, res.spec.source)] = res
    lines = ["## §0-B 종목별 배수 (체결내역 ÷ 1초봉 · SQLite 기준)", ""]
    any_row = False
    for symbol, cells in by_symbol.items():
        agg = cells.get(("future", "agg_trades"))
        kline = cells.get(("spot", "klines_1s"))
        if agg is None or kline is None or kline.sqlite_bytes == 0:
            continue
        any_row = True
        ratio = agg.sqlite_bytes / kline.sqlite_bytes
        lines.append(
            f"  {symbol:<10} 체결내역 {_fmt_bytes(agg.sqlite_bytes):>10}"
            f" / 1초봉 {_fmt_bytes(kline.sqlite_bytes):>10} = {ratio:>6.2f}배"
            f"   (행 {agg.rows:,} vs {kline.rows:,})"
        )
    if not any_row:
        lines.append("  (두 자료가 함께 잰 종목이 없어 배수를 못 낸다)")
    else:
        lines.append("")
        lines.append(
            "  ⚠️ 1초봉은 거래가 없어도 초마다 한 행이라 **바닥이 고정**이다"
            "(종목·일 86,400행). 한산한 종목일수록 배수가 1에 가까워지고 역전될 수도 있다."
        )
    return "\n".join(lines)


def format_tick_probe_rest(rows: Sequence[RestAvailability]) -> str:
    """REST로 그 과거 하루를 받을 수 있나 — 거래소 응답 그대로."""
    lines = ["## §0-C REST 가용성 (거래소 응답 그대로)", ""]
    if not rows:
        return "\n".join([*lines, "(찔러 보지 않았다 — `--skip-rest`)"])
    for row in rows:
        mark = "OK" if row.ok else "✗"
        lines.append(
            f"  {mark:<3} {row.spec.symbol:<10} {MARKET_LABEL[row.spec.market]:<12}"
            f" {SOURCE_LABEL[row.spec.source]:<8} HTTP {row.status:<4} {row.message}"
        )
    return "\n".join(lines)


def format_tick_probe_projection(
    projections: Sequence[Projection],
    *,
    universe: int,
    db_bytes: int,
    disk_free_bytes: int,
    disk_total_bytes: int,
) -> str:
    """유니버스 환산 + 이 박스의 여유 — 완료기준 2(범위 판정)의 입력이다."""
    from data.tick_probe import days_until_full

    lines = [
        f"## §0-D {universe}종목 환산과 서버 여유",
        "",
        f"  현재 DB {_fmt_bytes(db_bytes)} · 디스크 여유 {_fmt_bytes(disk_free_bytes)}"
        f" / {_fmt_bytes(disk_total_bytes)}",
        "",
    ]
    if not projections:
        lines.append("  (환산할 칸이 없다)")
        return "\n".join(lines)
    for proj in projections:
        daily = proj.projected_daily_sqlite
        days = days_until_full(disk_free_bytes, daily)
        span = f"띠 {_fmt_bytes(proj.projected_daily_low)}~{_fmt_bytes(proj.projected_daily_high)}"
        horizon = "∞" if days is None else f"{days:,.0f}일"
        yearly = _fmt_bytes(proj.projected_yearly_sqlite)
        lines.append(
            f"  {MARKET_LABEL[proj.market]} {SOURCE_LABEL[proj.source]}:"
            f" 하루 ≈ {_fmt_bytes(daily)} ({span}) · 1년 ≈ {yearly}"
            f" · 이 여유로 {horizon}"
        )
        lines.append(
            f"      (실측 {proj.measured_symbols}종목 합 {_fmt_bytes(proj.measured_sqlite_bytes)}"
            f" · 받은 바이트 합 {_fmt_bytes(proj.measured_download_bytes)})"
        )
    lines.append("")
    lines.append(
        "  🚨 하루 수치는 **평균×N 추정**이고 띠가 그 폭이다 — 체결내역은 종목별로 자릿수가"
        " 갈린다. 정확한 값이 필요하면 `--symbols`로 12종목을 그냥 다 재라(종목당 요청 한 번)."
    )
    return "\n".join(lines)


def _write_tick_probe_csv(results: Sequence[ProbeResult], path: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "symbol",
                "market",
                "source",
                "day",
                "available",
                "note",
                "rows",
                "download_bytes",
                "raw_bytes",
                "gzip_bytes",
                "sqlite_bytes",
                "download_s",
                "ingest_s",
            ]
        )
        for res in results:
            writer.writerow(
                [
                    res.spec.symbol,
                    res.spec.market,
                    res.spec.source,
                    res.day,
                    int(res.available),
                    res.note,
                    res.rows,
                    res.download_bytes,
                    res.raw_bytes,
                    res.gzip_bytes,
                    res.sqlite_bytes,
                    f"{res.download_s:.3f}",
                    f"{res.ingest_s:.3f}",
                ]
            )
    return out


def _lower_priority(nice_level: int) -> str:
    """수집기·러너에 CPU를 양보한다(WAN-318 §1 관행 · 완료기준 4).

    ⚠️ 이것만으로 디스크 경합이 사라지지 않는다 — 런북이 `ionice -c 3`을 함께 건다.
    """
    if nice_level <= 0:
        return "적용 안 함"
    try:
        current = os.nice(nice_level)
    except (OSError, PermissionError, AttributeError) as exc:  # pragma: no cover - OS 의존
        return f"실패({exc})"
    return f"nice={current}"


def cmd_tick_probe(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock tick-probe` — 1분보다 잘게 보는 데이터의 하루치 실측(WAN-347 §0).

    읽기 전용이다: 프로덕션 DB를 **열지도 않고**(파일 크기만 stat) 잰 파일은 스크래치에
    두었다 지운다(`--keep`으로 남긴다). 완료기준 3이 「§0만 하고 감당 안 되면 거기서
    닫는다」라 이 명령은 **표를 찍는 데서 끝난다** — 수집기를 만들지 않는다.
    """
    from data.tick_probe import (
        DEFAULT_PROBE_SYMBOLS,
        default_specs,
        measured_required_kinds,
        probe_all,
        probe_rest_availability,
        project,
    )

    nice_note = _lower_priority(args.nice)
    symbols = args.symbols or list(DEFAULT_PROBE_SYMBOLS)
    day = args.day or (date.today() - timedelta(days=1)).isoformat()
    universe = args.universe or len(settings.symbols)
    specs = default_specs(symbols)

    scratch = Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="tick-probe-"))
    print(f"# WAN-347 §0 — {day}(UTC) 하루치 실측 · {nice_note} · 스크래치 {scratch}")
    print(f"# 대표 종목 {', '.join(symbols)} · 유니버스 {universe}종목 기준 환산\n")

    results = probe_all(
        specs,
        day,
        scratch_dir=scratch,
        with_sqlite=not args.no_sqlite,
        keep=args.keep,
    )
    print(format_tick_probe_grid(results))
    print()
    print(format_tick_probe_ratios(results))
    print()
    rest_rows = [] if args.skip_rest else probe_rest_availability(specs, day)
    print(format_tick_probe_rest(rest_rows))
    print()

    db_path = Path(settings.db_path)
    db_bytes = db_path.stat().st_size if db_path.exists() else 0
    usage = shutil.disk_usage(db_path.parent if str(db_path.parent) else Path("."))
    print(
        format_tick_probe_projection(
            project(results, universe=universe),
            universe=universe,
            db_bytes=db_bytes,
            disk_free_bytes=usage.free,
            disk_total_bytes=usage.total,
        )
    )

    if args.csv:
        print(f"\nCSV: {_write_tick_probe_csv(results, args.csv)}")
    if args.keep:
        print(f"\n스크래치를 남겼다: {scratch} — 다 보고 지울 것(1GB 박스다).")

    # 이슈가 물은 두 자료 중 **하나라도** 재지 못했으면 종료 코드 1(표가 반쪽이면 판정 불가).
    return 0 if measured_required_kinds(results) else 1


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
    """`alphablock watch` — 운영 상태 워치(이상 시 텔레그램 경고, WAN-32).

    종료 코드(WAN-344): 0 정상 · 1 전송 실패 · 2 텔레그램 미설정. `--require-delivery`
    가 있어야 점검 모드에서 그 구분이 코드로 나온다(systemd 타이머가 쓰는 모드) —
    없으면 예전처럼 미설정 시 드라이런으로 접고 0을 낸다. `--test-message` 는 연결
    확인이 목적이라 플래그 없이도 실패를 코드로 낸다.
    """
    from live.health_watch import run_health_watch

    if args.dry_run and args.require_delivery:
        print("--dry-run 과 --require-delivery 는 함께 쓸 수 없습니다.", file=sys.stderr)
        return 2

    return run_health_watch(
        settings,
        once=args.once,
        dry_run=args.dry_run,
        test_message=args.test_message,
        require_delivery=args.require_delivery,
    )


def _funding_census_lines(db_path: str, settings: Settings) -> list[str]:
    """doctor에 찍는 펀딩 신선도 한 줄짜리 인구조사(WAN-407 §2).

    🚨 **종료 코드에는 반영하지 않는다.** 바로 위 환경 드리프트(WAN-309)와 같은 규약이다 —
    doctor의 종료 코드는 **DB 무결성 판정 전용**이고(`report.healthy`), 여기에 새 실패
    조건을 얹으면 「점검 항목을 늘리는 것」과 「종료 코드를 바꾸는 것」이 한 번에 섞인다.
    펀딩 정지의 **경보 경로는 이미 있다** — `alphablock watch`가 STALE 펀딩을 폰으로 보낸다
    (`live.health_watch.evaluate_alerts`). doctor는 사람이 볼 때 **같은 화면에서 보이게**만 한다.

    ⚠️ 판정 대상은 설정 유니버스다 — 저장된 심볼 전체로 하면 옛 유니버스 잔재가 영원히
    STALE로 떠 상시 빨간불이 된다(WAN-321이 고친 실패 부류).
    """
    from data.freshness import find_stale_funding, format_stale
    from data.funding import FundingRateStore

    symbols = list(settings.symbols)
    if not symbols:
        return []
    now_ms = int(time.time() * 1000)
    with FundingRateStore(db_path) as store:
        rows = [(symbol, _latest_funding_ms(store, symbol)) for symbol in symbols]
    missing = [symbol for symbol, last in rows if last is None]
    stale = find_stale_funding(
        rows, now_ms=now_ms, stale_multiplier=settings.health_stale_multiplier
    )
    lines: list[str] = []
    if not stale and not missing:
        lines.append(f"✅ {len(symbols)}종목 전부 최신 — 결측 없음.")
        return lines
    for item in stale:
        lines.append(f"⚠️ {format_stale(item)}")
    for symbol in missing:
        lines.append(f"⚠️ {symbol}: 저장된 펀딩이 하나도 없습니다(펀딩비가 0으로 계산됩니다).")
    lines.append(
        "ℹ️ 종료 코드에는 반영하지 않습니다(무결성 판정 전용) — 폰 경보는"
        " `alphablock watch`가 보냅니다. 메우기: `uv run python -m data.funding --backfill-only`."
    )
    return lines


def _latest_funding_ms(store: FundingRateStore, symbol: str) -> int | None:
    """그 심볼의 마지막 펀딩 정산 시각(예측 행 포함 — 화면의 `status`와 같은 자)."""
    latest = store.latest(symbol)
    return latest.funding_time if latest is not None else None


def cmd_tsmom_parity(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock tsmom-parity` — 엔진 C 페이퍼 원장 ↔ 저장 일봉으로 다시 계산한 값 (WAN-451).

    읽기 전용이다. 원장 행마다 그날 목표(배율·신호)를 같은 단일 소스(`strategy.tsmom`)로 다시
    내고 체결가·목표 명목과 함께 맞댄다. 🔴 불일치가 하나라도 있으면 종료 코드 1(감시에 물릴 수
    있게). 배율 끝자리만 다른 「거의 일치」는 정보로만 찍고 0이다(WAN-459 §3).
    러너 공백으로 건너뛴 날은 보여 주되 실패로 세지 않는다(소급 거래하지 않는 설계).
    """
    import time as _time

    from data.storage import OhlcvStore
    from live.tsmom_parity import check_tsmom_parity, render_tsmom_parity
    from paper.engine_c_report import read_engine_c

    db_path = args.db or settings.db_path
    now = int(_time.time() * 1000)
    since = None if args.days is None else now - int(args.days) * 86_400_000
    view = read_engine_c(db_path, since_ms=since)
    with OhlcvStore(db_path) as store:
        report = check_tsmom_parity(store, view.rows, now_ms=now)
    print(render_tsmom_parity(report))
    return 0 if report.ok else 1


def cmd_universe(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock universe [--at YYYY-MM-DD]` — 팔의 그 주 종목 목록(WAN-451 ③).

    읽기 전용이다. 수집기(`ALPHABLOCK_UNIVERSE_ENABLED`)가 월요일 00:00 UTC마다 남긴 목록과 지난주
    대비 변화 · 순위표를 보여 준다. `--at`은 UTC 날짜이고 그 날짜가 속한 주를 본다. 그 주 목록이
    없으면 종료 코드 1(감시에 물릴 수 있게).
    """
    import time as _time
    from datetime import UTC, datetime

    from data.universe import rebalance_at
    from data.universe_pool import UniverseStore, render_rotation

    db_path = args.db if args.db is not None else settings.db_path
    if args.at:
        at_ms = int(datetime.fromisoformat(args.at).replace(tzinfo=UTC).timestamp() * 1000)
    else:
        at_ms = int(_time.time() * 1000)
    want = rebalance_at(at_ms)
    with UniverseStore(db_path) as store:
        snap = store.at(at_ms)
        prev = store.previous(snap.rebalance_ms) if snap is not None else None
    print(render_rotation(snap, prev, want_ms=want, extra=args.extra))
    return 0 if snap is not None and snap.rebalance_ms == want else 1


def cmd_arm_check(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock arm-check` — 팔을 켜기 전 점검(WAN-451 ③ · 읽기 전용).

    설정 · 이번 주 목록 · 그 주 종목 데이터(최신 · 2년 이력) · 남은 장부를 본다. 하나라도 막히면
    종료 코드 1이다. 런북: docs/ops/wan451-arm-paper.md.
    """
    import time as _time

    from live.arm_check import render, run_check

    db_path = args.db if args.db is not None else settings.db_path
    check = run_check(settings, db_path, now_ms=int(_time.time() * 1000))
    print(render(check))
    return 0 if check.ready else 1


def cmd_arm_parity(args: argparse.Namespace, settings: Settings) -> int:
    """`alphablock arm-parity --db PATH` — 팔 페이퍼 장부 ↔ 정본 백테스트(WAN-451 ③).

    서버 DB를 내려받아 **로컬에서** 돌린다(정본 후보 생성이 2년치 탐지라 서버에서는 몇 시간 ·
    러너와 코어를 다툰다). 읽기 전용이고 어긋나면 종료 코드 1이다.
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from backtest import harness
    from live.arm_ledger_parity import render, run

    def _ms(text: str | None) -> int | None:
        if text is None:
            return None
        return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)

    db = Path(args.db if args.db is not None else settings.db_path)
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    report = run(db, since=_ms(args.since), until=_ms(args.until), jobs=jobs)
    print(render(report))
    return 0 if report.ok else 1


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
    funding_lines = _funding_census_lines(db_path, settings)
    if funding_lines:
        print("펀딩 수집 신선도 (WAN-407 §2):")
        print("\n".join(f"  {line}" for line in funding_lines))
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

    report = inspect(
        db_path,
        quick_check=not args.skip_quick_check,
        orphan_since_ms=since_ms,
        engine_c_enabled=settings.live_tsmom_enabled,
    )
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

    p_tick = sub.add_parser(
        "tick-probe",
        help="1분보다 잘게 보는 데이터의 하루치 실측(읽기 전용) — WAN-347 §0",
    )
    p_tick.add_argument(
        "--day",
        default=None,
        metavar="YYYY-MM-DD",
        help="잴 날짜(UTC · 기본 어제 — 오늘치 아카이브는 아직 없다)",
    )
    p_tick.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="대표 종목(기본 BTCUSDT SOLUSDT TRXUSDT = 활발·중간·한산)",
    )
    p_tick.add_argument(
        "--universe",
        type=int,
        default=None,
        help="환산에 쓸 종목 수(기본: 설정 symbols 개수 — 하드코딩하지 않는다)",
    )
    p_tick.add_argument(
        "--no-sqlite",
        action="store_true",
        help="SQLite 적재를 건너뛴다(행 수·압축 크기만 — 훨씬 빠르지만 DB 비용은 못 잰다)",
    )
    p_tick.add_argument(
        "--keep", action="store_true", help="잰 스크래치 파일을 지우지 않는다(기본: 지움)"
    )
    p_tick.add_argument("--scratch", default=None, help="스크래치 디렉터리(기본: 임시 디렉터리)")
    p_tick.add_argument("--skip-rest", action="store_true", help="REST 가용성 찔러보기를 건너뛴다")
    p_tick.add_argument(
        "--nice",
        type=int,
        default=19,
        help="자기 우선순위를 이만큼 낮춘다(기본 19 — 러너·수집기 양보. 0이면 안 낮춤)",
    )
    p_tick.add_argument("--csv", default=None, help="칸 단위 결과를 이 경로에 CSV로 저장")
    p_tick.set_defaults(func=cmd_tick_probe)

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
    p_watch.add_argument(
        "--require-delivery",
        action="store_true",
        help="텔레그램 미설정·전송 실패를 0이 아닌 종료 코드로 낸다(systemd 감시용, WAN-344)",
    )
    p_watch.set_defaults(func=cmd_watch)

    p_tsmom_parity = sub.add_parser(
        "tsmom-parity",
        help="추세추종 페이퍼 원장 ↔ 저장 일봉 재계산 대조(WAN-451 · 불일치면 종료 코드 1)",
    )
    p_tsmom_parity.add_argument("--db", default=None, help="DB 경로(기본: 설정의 db_path)")
    p_tsmom_parity.add_argument(
        "--days", type=int, default=None, help="최근 N일 리밸런싱만(기본: 전부)"
    )
    p_tsmom_parity.set_defaults(func=cmd_tsmom_parity)

    p_universe = sub.add_parser(
        "universe",
        help="팔의 그 주 종목 목록 · 지난주 대비 변화(WAN-451 · 그 주 목록이 없으면 종료 코드 1)",
    )
    p_universe.add_argument("--db", default=None, help="DB 경로(기본: 설정의 db_path)")
    p_universe.add_argument("--at", default=None, help="UTC 날짜 YYYY-MM-DD(기본: 지금)")
    p_universe.add_argument(
        "--extra", type=int, default=5, help="목록 밖 다음 순위를 몇 개 더 보일지(기본 5)"
    )
    p_universe.set_defaults(func=cmd_universe)

    p_arm_check = sub.add_parser(
        "arm-check",
        help="팔을 켜기 전 점검 — 설정 · 이번 주 목록 · 데이터 · 장부(WAN-451)",
    )
    p_arm_check.add_argument("--db", default=None, help="DB 경로(기본: 설정의 db_path)")
    p_arm_check.set_defaults(func=cmd_arm_check)

    p_arm_parity = sub.add_parser(
        "arm-parity",
        help="팔 페이퍼 장부 ↔ 정본 백테스트 거래 대조(WAN-451 · 로컬에서 서버 DB 복사본으로)",
    )
    p_arm_parity.add_argument("--db", default=None, help="DB 경로(서버 DB 복사본)")
    p_arm_parity.add_argument("--since", default=None, help="UTC 시각(기본: 첫 가동 세션)")
    p_arm_parity.add_argument("--until", default=None, help="UTC 시각(기본: BTC 1분봉 끝)")
    p_arm_parity.add_argument("--jobs", type=int, default=None, help="병렬 워커 수")
    p_arm_parity.set_defaults(func=cmd_arm_parity)

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
