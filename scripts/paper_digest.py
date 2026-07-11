"""페이퍼 성과 주간/일간 다이제스트 텔레그램 발송 CLI (WAN-36).

WAN-33이 `paper_trades`에 누적한 가상 거래를 읽어 한 기간의 성과 요약(거래 수·승률·
순손익률·합계 R·MDD, 시리즈별 상위/하위, 백테스트 패리티 불일치)을 만들고, WAN-32와
같은 텔레그램 경로(`live.telegram.TelegramClient`)로 폰에 보낸다. 화면을 열지 않아도
"얼마 벌었나"가 주기적으로 오게 하는 것이 목적이다.

기간은 `--since/--until`로 지정하거나, 없으면 `--days`(기본 7일) 창을 지금 기준으로
잡는다. cron/launchd로 주 1회 실행하면 주간 다이제스트가 된다.

안전장치: 실제 발송은 `ALPHABLOCK_PAPER_DIGEST_ENABLED=true`이고 텔레그램이 설정된
경우에만 한다. `--dry-run`은 발송 없이 stdout으로 미리보기만 한다(설정 무관).

사용법::

    # 최근 7일 다이제스트 미리보기(발송 없음)
    uv run python scripts/paper_digest.py --dry-run

    # 특정 기간, 패리티 비교 없이
    uv run python scripts/paper_digest.py --since 2024-01-01 --until 2024-01-08 --no-parity
"""

from __future__ import annotations

import argparse
import logging
import sqlite3

import pandas as pd

from config.settings import Settings, get_settings
from live.runner import build_telegram_client
from paper.digest import build_digest, format_period_label
from paper.performance import build_performance
from paper.store import PaperTradeRecord, PaperTradeStore

_logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="SQLite 경로 (기본: 설정값)")
    parser.add_argument("--symbols", default=None, help="쉼표로 구분한 심볼 필터 (기본: 전체)")
    parser.add_argument("--timeframes", default=None, help="쉼표로 구분한 TF 필터 (기본: 전체)")
    parser.add_argument("--since", default=None, help="시작일 (ISO, 진입시각 기준). 없으면 --days")
    parser.add_argument("--until", default=None, help="종료일 (ISO, 배타적). 없으면 현재 시각")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="--since 미지정 시 집계 창(일). 기본: 설정값(paper_digest_days)",
    )
    parser.add_argument("--no-parity", action="store_true", help="패리티(백테스트 비교) 생략")
    parser.add_argument(
        "--dry-run", action="store_true", help="발송 없이 다이제스트를 stdout으로만 출력"
    )
    return parser.parse_args(argv)


def _to_ms(value: str | None) -> int | None:
    if value is None:
        return None
    return int(pd.Timestamp(value, tz="UTC").timestamp() * 1000)


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parts = [v.strip() for v in value.split(",") if v.strip()]
    return parts or None


def _now_ms() -> int:
    return int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)


def resolve_window(
    settings: Settings,
    *,
    since: str | None,
    until: str | None,
    days: int | None,
) -> tuple[int | None, int | None]:
    """(since_ms, until_ms)를 계산한다. `--since`가 없으면 지금 기준 `days`일 창."""
    until_ms = _to_ms(until)
    since_ms = _to_ms(since)
    if since_ms is None:
        window_days = days if days is not None else settings.paper_digest_days
        anchor = until_ms if until_ms is not None else _now_ms()
        since_ms = anchor - window_days * 86_400_000
    return since_ms, until_ms


def _runner_status_line(settings: Settings) -> str | None:
    """거래 0건 다이제스트에 붙일 러너 상태 한 줄(조립 실패 시 None)."""
    try:
        from dashboard.health import HealthLevel
        from dashboard.health_data import build_health_view

        view = build_health_view(
            settings.db_path,
            runtime_state_path=settings.live_runtime_state_path,
            poll_interval_seconds=settings.live_poll_interval_seconds,
            stale_multiplier=settings.health_stale_multiplier,
            collector_heartbeat_path=settings.collector_heartbeat_path,
            collector_heartbeat_interval_seconds=settings.collector_heartbeat_interval_seconds,
        )
    except Exception:  # noqa: BLE001 — 상태 조립 실패가 다이제스트를 막지 않도록.
        _logger.debug("러너 상태 조립 실패 — 상태 줄 생략", exc_info=True)
        return None

    if not view.runner.ran:
        return "러너: 미실행 (`alphablock live`)"
    lag_ms = view.runner.lag_ms
    lag = "—" if lag_ms is None else f"{max(lag_ms, 0) / 60_000:.0f}분"
    ok = "정상" if view.runner.level is HealthLevel.OK else "지연"
    return f"러너: {ok} · 마지막 폴링 {lag} 전"


def generate_digest(
    settings: Settings,
    *,
    since_ms: int | None,
    until_ms: int | None,
    symbols: list[str] | None,
    timeframes: list[str] | None,
    include_parity: bool,
) -> str:
    """저장소에서 읽어 다이제스트 문자열을 만든다(발송 없음, DB 없음/0건도 안전)."""
    db_path = settings.db_path
    target_series: list[tuple[str, str]] = []
    records: list[PaperTradeRecord] = []
    try:
        with PaperTradeStore(db_path) as store:
            all_series = store.list_series()
            target_series = [
                (s, tf)
                for s, tf in all_series
                if (symbols is None or s in symbols) and (timeframes is None or tf in timeframes)
            ]
            records = [
                r
                for s, tf in target_series
                for r in store.list_records(s, tf, start_ms=since_ms, end_ms=until_ms)
            ]
    except sqlite3.Error:
        _logger.warning("페이퍼 거래 저장소를 읽지 못했습니다 (%s) — 거래 없음으로 처리", db_path)

    period_label = format_period_label(since_ms, until_ms)
    performance = build_performance(records)

    if performance.overall.num_trades == 0:
        return build_digest(
            performance,
            period_label=period_label,
            runner_line=_runner_status_line(settings),
        )

    parity_flagged: list[tuple[str, str]] | None = None
    if include_parity:
        try:
            from paper.parity import build_parity_report

            report = build_parity_report(
                db_path,
                settings=settings,
                series=target_series,
                start_ms=since_ms,
                end_ms=until_ms,
            )
            parity_flagged = [(r.symbol, r.timeframe) for r in report.flagged_rows]
        except Exception:  # noqa: BLE001 — 패리티 재실행 실패가 성과 발송을 막지 않도록.
            _logger.warning("패리티 리포트 조립 실패 — 패리티 요약 생략", exc_info=True)
            parity_flagged = None

    return build_digest(
        performance,
        period_label=period_label,
        parity_flagged=parity_flagged,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args(argv)
    settings = get_settings()
    if args.db_path:
        settings = settings.model_copy(update={"db_path": args.db_path})

    since_ms, until_ms = resolve_window(
        settings, since=args.since, until=args.until, days=args.days
    )
    text = generate_digest(
        settings,
        since_ms=since_ms,
        until_ms=until_ms,
        symbols=_split(args.symbols),
        timeframes=_split(args.timeframes),
        include_parity=not args.no_parity,
    )

    if args.dry_run:
        print(text)
        return 0

    if not settings.paper_digest_enabled:
        _logger.warning(
            "ALPHABLOCK_PAPER_DIGEST_ENABLED=false — 발송을 건너뜁니다. "
            "미리보려면 --dry-run 을 쓰세요."
        )
        print(text)
        return 0

    telegram = build_telegram_client(settings)
    if telegram is None:
        _logger.warning("텔레그램 미설정(ALPHABLOCK_TELEGRAM_*) — 발송 생략. 내용:")
        print(text)
        return 0

    ok = telegram.send_message(text)
    _logger.info("다이제스트 발송 %s", "성공" if ok else "실패")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
