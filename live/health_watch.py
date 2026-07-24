"""운영 상태(Health) 워치 — 이상 감지 시 자동 텔레그램 경고 (WAN-32).

WAN-30의 Health 판정 로직(`dashboard.health` / `dashboard.health_data.build_health_view`)을
그대로 재사용해 주기적으로 상태를 점검하고, 이상이 감지되면 WAN-25의 텔레그램 전송
경로(`common.telegram.TelegramClient`)로 폰에 경고를 보낸다. 화면(대시보드)을 열지 않아도
수집·러너가 조용히 멈춘 사실을 알 수 있게 하는 것이 목적이다.

경고 대상
--------
* **데이터 수집 지연**: 시리즈(심볼·TF)의 최신 봉이 TF 주기 대비 stale(WAN-30 판정).
* **펀딩비 갱신 지연**: 심볼의 펀딩 갱신이 정산 주기 대비 stale.
* **러너 하트비트 끊김**: 실시간 시그널 러너(WAN-25)가 돌다가 폴링을 멈춤.
* **수집기 하트비트 끊김**: 상시 구동 수집기(WAN-31)가 돌다가 하트비트를 멈춤.

한 번도 실행된 적 없는(UNKNOWN) 프로세스는 "끊김"이 아니라 "미실행"이므로 경고하지
않는다 — 복구할 대상이 없기 때문이다.

플래핑 방지
----------
* 동일 이슈(안정적 `key`)는 **쿨다운**(기본 60분) 내 1회만 보낸다. 이상이 계속되면
  쿨다운마다 1회씩 리마인더를 보내되, 그 사이 중복 경고가 쏟아지지 않는다.
* 이상이 사라지면 **"✅ 정상 복구"** 알림을 이슈당 1회 보낸다.
* 경고 상태는 JSON 파일(`WatchStateStore`)로 영속화해, 워치를 재시작해도 방금 보낸
  경고를 다시 보내거나 복구 알림을 놓치지 않는다.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from common.telegram import build_telegram_client
from common.timefmt import format_kst_zoned, kst_log_format, use_kst_logging
from config.settings import Settings, get_settings
from dashboard.health import HealthLevel
from dashboard.health_data import HealthView, build_health_view

_logger = logging.getLogger(__name__)


# -- 경고 판정 (순수 함수) -----------------------------------------------------


class Alert(BaseModel):
    """감지된 이상 하나. `key`는 쿨다운·복구 추적용 안정적 식별자다."""

    model_config = ConfigDict(frozen=True)

    key: str
    """같은 이상은 점검을 반복해도 동일한 값(예: `data:BTC/USDT:USDT:1h`)."""
    title: str
    """복구 알림 등에 쓰는 짧은 라벨."""
    detail: str
    """텔레그램으로 보낼 경고 본문(마크다운)."""


def _fmt_at(ms: int | None) -> str:
    """경고 본문에 넣는 마지막 갱신 시각(KST, WAN-172).

    폰으로 받는 경고라 "3.0시간 전"만으로는 언제인지 감이 안 온다 — 절대 시각을
    KST로 함께 준다. ⚠️ 판정에 쓰는 값은 UTC epoch ms 그대로이고 표시만 바뀐다.
    """
    return format_kst_zoned(ms)


def _fmt_lag(lag_ms: int | None) -> str:
    """지연(ms)을 사람이 읽는 한국어 문자열로. 음수/None은 방어적으로 처리."""
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


def evaluate_alerts(view: HealthView) -> list[Alert]:
    """Health 뷰에서 현재 발효 중인 경고 목록을 만든다(부수효과 없음).

    STALE(빨강)만 경고 대상이다. UNKNOWN(미실행/데이터 없음)은 경고하지 않는다.
    """
    alerts: list[Alert] = []

    for f in view.freshness:
        if f.level is HealthLevel.STALE:
            lag = _fmt_lag(f.lag_ms)
            alerts.append(
                Alert(
                    key=f"data:{f.symbol}:{f.timeframe}",
                    title=f"데이터 수집 지연: {f.symbol} {f.timeframe}",
                    detail=(
                        f"⚠️ *데이터 수집 지연* — `{f.symbol}` `{f.timeframe}`\n"
                        f"최신 봉 {_fmt_at(f.last_open_time)} (*{lag}* 지연).\n"
                        "수집기가 살아있는지 확인하세요."
                    ),
                )
            )

    for fund in view.funding:
        if fund.level is HealthLevel.STALE:
            lag = _fmt_lag(fund.lag_ms)
            alerts.append(
                Alert(
                    key=f"funding:{fund.symbol}",
                    title=f"펀딩비 갱신 지연: {fund.symbol}",
                    detail=(
                        f"⚠️ *펀딩비 갱신 지연* — `{fund.symbol}`\n"
                        f"마지막 펀딩 갱신 {_fmt_at(fund.funding_time)} (*{lag}* 전)."
                    ),
                )
            )

    if view.runner.ran and view.runner.level is HealthLevel.STALE:
        lag = _fmt_lag(view.runner.lag_ms)
        alerts.append(
            Alert(
                key="runner",
                title="러너 하트비트 끊김",
                detail=(
                    "⚠️ *러너 하트비트 끊김*\n"
                    f"마지막 폴링 {_fmt_at(view.runner.last_poll_ms)} (*{lag}* 전). "
                    "시그널 러너(`alphablock live`)가 멈췄을 수 있습니다."
                ),
            )
        )

    if view.collector.ran and view.collector.level is HealthLevel.STALE:
        lag = _fmt_lag(view.collector.lag_ms)
        alerts.append(
            Alert(
                key="collector",
                title="수집기 하트비트 끊김",
                detail=(
                    "⚠️ *수집기 하트비트 끊김*\n"
                    f"마지막 하트비트 {_fmt_at(view.collector.last_beat_ms)} (*{lag}* 전). "
                    "수집기(`alphablock collect`)가 멈췄을 수 있습니다."
                ),
            )
        )

    return alerts


# -- 쿨다운·복구 조정 (순수 함수) ---------------------------------------------


class AlertRecord(BaseModel):
    """현재 발효 중으로 추적하는 경고 하나의 상태."""

    model_config = ConfigDict(frozen=True)

    title: str
    last_notified_ms: int
    """마지막으로 이 경고를 보낸 시각(ms). 쿨다운 기준."""


class WatchState(BaseModel):
    """워치가 파일로 영속화하는 상태(발효 중인 경고 집합)."""

    active: dict[str, AlertRecord] = {}


class Outbound(BaseModel):
    """이번 점검에서 실제로 보내야 할 메시지."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["alert", "recovery"]
    text: str


def reconcile(
    alerts: list[Alert],
    state: WatchState,
    *,
    now_ms: int,
    cooldown_ms: int,
) -> tuple[list[Outbound], WatchState]:
    """현재 경고와 이전 상태를 비교해 보낼 메시지와 다음 상태를 계산한다(순수 함수).

    - 새로 나타난 경고 → 경고 1회, 발효 상태로 등록.
    - 계속 발효 중이고 쿨다운이 지났으면 → 리마인더 1회(타임스탬프 갱신).
    - 계속 발효 중이지만 쿨다운 이내면 → 아무것도 보내지 않음(중복 억제).
    - 이전엔 발효 중이었으나 이번엔 사라진 경고 → "정상 복구" 1회, 상태에서 제거.
    """
    current = {a.key: a for a in alerts}
    new_active: dict[str, AlertRecord] = {}
    outbound: list[Outbound] = []

    for key, alert in current.items():
        prev = state.active.get(key)
        if prev is None or now_ms - prev.last_notified_ms >= cooldown_ms:
            outbound.append(Outbound(kind="alert", text=alert.detail))
            new_active[key] = AlertRecord(title=alert.title, last_notified_ms=now_ms)
        else:
            # 쿨다운 이내 — 재전송하지 않고 마지막 발송 시각을 유지한다.
            new_active[key] = prev

    for key, prev in state.active.items():
        if key not in current:
            outbound.append(Outbound(kind="recovery", text=f"✅ *정상 복구* — {prev.title}"))

    return outbound, WatchState(active=new_active)


# -- 상태 영속화 ---------------------------------------------------------------


class WatchStateStore:
    """워치 상태를 JSON 파일로 영속화한다(단일 작성자, 원자적 교체)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> WatchState:
        """파일에서 상태를 읽는다(없거나 손상 시 빈 상태)."""
        if not self._path.exists():
            return WatchState()
        try:
            return WatchState.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            _logger.warning("워치 상태 파일을 읽지 못해 빈 상태로 시작: %s", exc)
            return WatchState()

    def save(self, state: WatchState) -> None:
        """상태를 파일에 원자적으로 저장한다."""
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


# -- 워치 루프 -----------------------------------------------------------------

#: 메시지를 실제로 보내는 함수. 성공 True. 텔레그램 미설정 시 로그로만 남긴다.
Notify = Callable[[str], bool]


def _log_notify(text: str) -> bool:
    """텔레그램 미설정(드라이런) 시 경고를 로그로만 남긴다."""
    _logger.info("[드라이런] Health 경고:\n%s", text)
    return False


class HealthWatch:
    """주기적으로 Health를 점검하고 이상 시 경고를 보내는 워치."""

    def __init__(
        self,
        *,
        view_provider: Callable[[], HealthView],
        notify: Notify,
        store: WatchStateStore,
        cooldown_seconds: float,
        interval_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._view_provider = view_provider
        self._notify = notify
        self._store = store
        self._cooldown_ms = int(cooldown_seconds * 1000)
        self._interval = interval_seconds
        self._sleep = sleep
        self._now_ms = now_ms

    def check_once(self) -> list[Outbound]:
        """상태를 한 번 점검하고, 보낼 메시지를 전송한 뒤 반환한다.

        뷰 조립(파일·DB I/O)이 실패해도 워치 루프가 죽지 않도록 방어한다.
        """
        try:
            view = self._view_provider()
        except Exception:  # noqa: BLE001 — 일시적 I/O 오류가 루프 전체를 멈추지 않도록.
            _logger.exception("Health 뷰 조립 실패 — 이번 점검 건너뜀")
            return []

        alerts = evaluate_alerts(view)
        state = self._store.load()
        outbound, new_state = reconcile(
            alerts, state, now_ms=self._now_ms(), cooldown_ms=self._cooldown_ms
        )
        for message in outbound:
            self._notify(message.text)
        self._store.save(new_state)
        if outbound:
            _logger.info(
                "Health 점검: 경고 %d건, 복구 %d건",
                sum(1 for m in outbound if m.kind == "alert"),
                sum(1 for m in outbound if m.kind == "recovery"),
            )
        return outbound

    def run(self, *, max_checks: int | None = None) -> None:
        """점검 루프. `max_checks`가 None이면 무한 반복(테스트에서는 유한 지정)."""
        checks = 0
        while max_checks is None or checks < max_checks:
            self.check_once()
            checks += 1
            if max_checks is not None and checks >= max_checks:
                break
            self._sleep(self._interval)


def build_view_provider(settings: Settings) -> Callable[[], HealthView]:
    """설정으로 Health 뷰를 조립하는 provider를 만든다(WAN-30 로직 재사용)."""

    def provider() -> HealthView:
        return build_health_view(
            settings.db_path,
            runtime_state_path=settings.live_runtime_state_path,
            poll_interval_seconds=settings.live_poll_interval_seconds,
            stale_multiplier=settings.health_stale_multiplier,
            collector_heartbeat_path=settings.collector_heartbeat_path,
            collector_heartbeat_interval_seconds=settings.collector_heartbeat_interval_seconds,
        )

    return provider


def run_health_watch(
    settings: Settings | None = None,
    *,
    once: bool = False,
    dry_run: bool = False,
    test_message: bool = False,
) -> None:
    """헬스 워치를 실행한다(`alphablock watch` / `python -m live.health_watch` 공용).

    - `test_message=True`: 텔레그램 연결 확인용 메시지 1건만 보내고 종료.
    - `dry_run=True`: 텔레그램 전송 없이 경고를 로그로만 출력.
    - `once=True`: 한 번만 점검하고 종료(그 외에는 무한 점검 루프).
    """
    settings = settings or get_settings()

    telegram = None if dry_run else build_telegram_client(settings)
    if test_message:
        if telegram is None:
            _logger.error("텔레그램이 설정되지 않았습니다(ALPHABLOCK_TELEGRAM_*).")
            return
        ok = telegram.send_message("✅ AlphaBlock Health 워치 테스트 메시지 (WAN-32)")
        _logger.info("테스트 메시지 전송 %s", "성공" if ok else "실패")
        return

    if telegram is None and not dry_run:
        _logger.warning(
            "텔레그램 미설정 — 드라이런으로 실행합니다. ALPHABLOCK_TELEGRAM_* 를 설정하세요."
        )

    notify: Notify = telegram.send_message if telegram is not None else _log_notify
    watch = HealthWatch(
        view_provider=build_view_provider(settings),
        notify=notify,
        store=WatchStateStore(settings.health_watch_state_path),
        cooldown_seconds=settings.health_watch_cooldown_seconds,
        interval_seconds=settings.health_watch_interval_seconds,
    )
    _logger.info(
        "Health 워치 시작: 점검 %ds, 쿨다운 %ds",
        settings.health_watch_interval_seconds,
        settings.health_watch_cooldown_seconds,
    )
    watch.run(max_checks=1 if once else None)


def main() -> None:
    """CLI 엔트리포인트: `python -m live.health_watch`."""
    import argparse

    parser = argparse.ArgumentParser(description="WAN-32 운영 상태(Health) 워치 + 텔레그램 경고")
    parser.add_argument("--once", action="store_true", help="한 번만 점검하고 종료")
    parser.add_argument(
        "--test-message",
        action="store_true",
        help="테스트 메시지를 한 번 보내고 종료(텔레그램 연결 확인)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램 전송 없이 경고를 로그로만 출력",
    )
    args = parser.parse_args()

    use_kst_logging()  # 로그 시각도 KST(WAN-172)
    logging.basicConfig(level=logging.INFO, format=kst_log_format())
    run_health_watch(once=args.once, dry_run=args.dry_run, test_message=args.test_message)


if __name__ == "__main__":
    main()
