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
* 단 **수집 계획에서 빠져** 사라진 데이터 경고(팔 목록 교체, WAN-458)는 복구가 아니므로
  「ℹ️ 감시 대상에서 제외」로 구분해 1회 보낸다.
* 경고 상태는 JSON 파일(`WatchStateStore`)로 영속화해, 워치를 재시작해도 방금 보낸
  경고를 다시 보내거나 복구 알림을 놓치지 않는다.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from common.heartbeat import HeartbeatStore
from common.telegram import build_telegram_client
from common.timefmt import format_kst_zoned, kst_log_format, use_kst_logging
from config.settings import Settings, get_settings
from dashboard.health import HealthLevel, SeriesFreshness, runner_cycle_budget_ms
from dashboard.health_data import HealthView, build_health_view, monitor_scope_for
from data.collection_plan import build_plan

_logger = logging.getLogger(__name__)

#: 워치 종료 코드(WAN-344) — systemd 가 `systemctl --failed` 로 잡는 신호다.
WATCH_OK = 0
#: 보낼 곳은 있는데 전송이 실패했다(네트워크·API 거부).
WATCH_DELIVERY_FAILED = 1
#: 보낼 곳 자체가 없다(ALPHABLOCK_TELEGRAM_* 미설정) — 감시가 도는데 아무 데도 안 간다.
WATCH_TELEGRAM_UNCONFIGURED = 2


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
                    key=data_alert_key(f.symbol, f.timeframe),
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
            # 판정 기준 = 마지막 **확정** 정산(WAN-422 §2). 예측 행은 매 주기 갱신돼
            # 수집이 멈춰도 늘 「방금」으로 보이므로 경고문도 확정 시각을 말한다.
            if fund.confirmed_funding_time is not None:
                since = f"마지막 확정 정산 {_fmt_at(fund.confirmed_funding_time)} (*{lag}* 전)."
            else:
                since = (
                    "확정 펀딩 이력이 저장돼 있지 않습니다"
                    f" (최신 표시 행 {_fmt_at(fund.funding_time)})."
                )
            alerts.append(
                Alert(
                    key=f"funding:{fund.symbol}",
                    title=f"펀딩비 갱신 지연: {fund.symbol}",
                    detail=f"⚠️ *펀딩비 갱신 지연* — `{fund.symbol}`\n{since}",
                )
            )

    if view.runner.ran and view.runner.level is HealthLevel.STALE:
        # 완주 지연(WAN-313)은 「하트비트는 뛰는데 한 바퀴를 못 끝냄」일 때만 — 새 필드
        # 없이 만들어진 STALE(옛 호출부)은 예전처럼 하트비트 끊김으로 안내한다.
        if view.runner.heartbeat_stale or not view.runner.cycle_stale:
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
        else:
            # 하트비트는 뛰는데 한 바퀴 완주가 예산을 넘겼다(WAN-313) — 프로세스 정지가
            # 아니라 「최단 TF를 못 따라가는」 상태라 원인 안내가 다르다.
            lag = _fmt_lag(view.runner.cycle_lag_ms)
            alerts.append(
                Alert(
                    key="runner",
                    title="러너 한 바퀴 완주 지연",
                    detail=(
                        "⚠️ *러너 한 바퀴 완주 지연*\n"
                        f"마지막 완주 {_fmt_at(view.runner.last_cycle_ms)} (*{lag}* 전). "
                        "러너는 살아 있지만 전체 시리즈 한 바퀴가 최단 TF 주기를 넘고"
                        " 있습니다 — 시리즈 수·데이터 로드 병목을 확인하세요(WAN-313)."
                    ),
                )
            )

    # 러너는 살아 있는데 그 러너의 알림만 안 나가는 상태(WAN-351) — 하트비트로는 안 보인다.
    # 🚨 「한동안 발송이 없다」로는 경고하지 않는다: 팔은 진입이 드물어 조용한 날이 정상이고
    # (상승장마다 울리는 거짓 경보 = WAN-321 부류), 그 판정은 「보낼 게 없었다」와 「못
    # 보냈다」를 못 가른다. 여기서 보는 것은 조용한 날에 **원리적으로 안 생기는** 두 신호뿐이다
    # — 「보내려다 실패했다」(실패 수 > 0)와 「보낼 길이 없다」(드라이런).
    if view.runner.ran and (
        view.runner.notification_failures > 0 or view.runner.notification_mode == "dry_run"
    ):
        if view.runner.notification_failures > 0:
            body = (
                f"마지막 성공 이후 전송 실패 *{view.runner.notification_failures}회* — 마지막 실패"
                f" {_fmt_at(view.runner.last_notification_failure_ms)}. 러너 로그의"
                " 「텔레그램 전송 실패」를 확인하세요."
            )
        else:
            body = (
                "러너가 텔레그램 미설정(드라이런)으로 돌고 있어 매매 알림이 로그로만 갑니다."
                " 설정을 고쳤다면 러너를 재시작하세요."
            )
        alerts.append(
            Alert(
                key=RUNNER_DELIVERY_ALERT_KEY,
                title="러너 알림 경로 막힘",
                detail=f"⚠️ *러너 알림 경로 막힘*\n{body}",
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
                    "수집기(`alphablock collect`)가 멈췄거나 새 봉을 못 받고 있습니다"
                    "(하트비트 = 새 봉 저장 · WAN-470)."
                ),
            )
        )

    return alerts


#: 러너 알림 경로 막힘 경고의 키(WAN-351) — 실패가 다음 성공으로 해소되면 복구 알림이 간다.
RUNNER_DELIVERY_ALERT_KEY = "runner_delivery"


def data_alert_key(symbol: str, timeframe: str) -> str:
    """데이터 수집 지연 경고의 안정적 키 — `evaluate_alerts`와 제외 판정이 같은 식을 쓴다."""
    return f"data:{symbol}:{timeframe}"


def excluded_alert_keys(view: HealthView) -> frozenset[str]:
    """수집 계획에서 빠져 판정 대상이 아닌 시리즈의 경고 키(WAN-458).

    이 키로 발효 중이던 경고가 사라지면 「정상 복구」가 아니라 「감시 대상에서 제외」다 —
    봉이 다시 들어온 게 아니라 일부러 안 모으기 시작한 것이기 때문이다.
    """
    return frozenset(data_alert_key(f.symbol, f.timeframe) for f in view.excluded)


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
    collector_restarts: list[int] = []
    """워치가 수집기에 재시작 신호를 보낸 시각들(ms · WAN-470) — 쿨다운·하루 상한 판정용."""


class Outbound(BaseModel):
    """이번 점검에서 실제로 보내야 할 메시지."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["alert", "recovery", "excluded", "action"]
    key: str
    """어느 이슈의 메시지인가(`Alert.key`). 전송 실패를 되돌릴 때 쓴다(WAN-344)."""
    text: str


def reconcile(
    alerts: list[Alert],
    state: WatchState,
    *,
    now_ms: int,
    cooldown_ms: int,
    excluded_keys: frozenset[str] = frozenset(),
) -> tuple[list[Outbound], WatchState]:
    """현재 경고와 이전 상태를 비교해 보낼 메시지와 다음 상태를 계산한다(순수 함수).

    - 새로 나타난 경고 → 경고 1회, 발효 상태로 등록.
    - 계속 발효 중이고 쿨다운이 지났으면 → 리마인더 1회(타임스탬프 갱신).
    - 계속 발효 중이지만 쿨다운 이내면 → 아무것도 보내지 않음(중복 억제).
    - 이전엔 발효 중이었으나 이번엔 사라진 경고 → "정상 복구" 1회, 상태에서 제거.
    - 단 사라진 이유가 **수집 계획에서 빠져서**(`excluded_keys`, WAN-458)면 복구가 아니므로
      "감시 대상에서 제외" 1회로 구분한다.
    """
    current = {a.key: a for a in alerts}
    new_active: dict[str, AlertRecord] = {}
    outbound: list[Outbound] = []

    for key, alert in current.items():
        prev = state.active.get(key)
        if prev is None or now_ms - prev.last_notified_ms >= cooldown_ms:
            outbound.append(Outbound(kind="alert", key=key, text=alert.detail))
            new_active[key] = AlertRecord(title=alert.title, last_notified_ms=now_ms)
        else:
            # 쿨다운 이내 — 재전송하지 않고 마지막 발송 시각을 유지한다.
            new_active[key] = prev

    for key, prev in state.active.items():
        if key in current:
            continue
        if key in excluded_keys:
            outbound.append(
                Outbound(
                    kind="excluded",
                    key=key,
                    text=(
                        f"ℹ️ *감시 대상에서 제외* — {prev.title}\n"
                        "지금 수집 계획(팔 그 주 목록·보유 종목)에 없어 더 이상 모으지 않습니다."
                        " 복구된 것이 아닙니다."
                    ),
                )
            )
        else:
            outbound.append(
                Outbound(kind="recovery", key=key, text=f"✅ *정상 복구* — {prev.title}")
            )

    return outbound, state.model_copy(update={"active": new_active})


def apply_delivery_failures(
    next_state: WatchState,
    previous: WatchState,
    undelivered: list[Outbound],
) -> WatchState:
    """전송에 **실패한** 메시지는 「보냈다」로 기록하지 않는다(순수 함수, WAN-344).

    이게 없으면 전송이 실패해도 `reconcile`이 만든 「방금 보냄」 상태가 그대로 저장돼
    **쿨다운(기본 1시간)이 걸린다** — 즉 경보가 안 갔는데 그 뒤 한 시간은 재시도조차
    하지 않는다. WAN-321이 고친 「경보가 안 가는데 그 사실도 경보되지 않는」 자리의
    이웃이라, 실패한 것만 이전 상태로 되돌려 **다음 점검이 다시 보내게** 한다.

    - 실패한 **경고**: 이전 기록이 있으면 그 타임스탬프로 되돌리고(리마인더 재시도),
      없으면 아예 지운다(다음 점검에 새 경고로 다시 나간다).
    - 실패한 **복구·제외 알림**: 이전 기록을 되살린다 → 다음 점검에서 다시 잡힌다.
    """
    active = dict(next_state.active)
    for message in undelivered:
        prev = previous.active.get(message.key)
        if message.kind == "alert":
            if prev is None:
                active.pop(message.key, None)
            else:
                active[message.key] = prev
        elif message.kind != "action" and prev is not None:  # recovery/excluded — 되살린다.
            active[message.key] = prev
    # 「조치」(수집기 재시작) 알림은 되돌릴 게 없다 — 재시작 자체는 이미 일어났다(WAN-470).
    return next_state.model_copy(update={"active": active})


# -- 수집기 자동 재시작 백스톱 (WAN-470) ---------------------------------------

_DAY_MS = 86_400_000
#: 재시작 실패·하루 상한 도달 경고의 키 — 쿨다운·복구 알림은 일반 경고와 같은 경로를 탄다.
COLLECTOR_RESTART_ALERT_KEY = "collector_restart"


@dataclass(frozen=True)
class CollectorRestartPolicy:
    """워치가 수집기를 다시 띄우는 조건(WAN-470).

    판정 대상은 **기본 격자**(설정 심볼 × 설정 TF)뿐이다 — 팔 목록은 매주 바뀌어 새로 든 종목이
    아직 비어 있는 것이 정상이라(WAN-458/468) 그걸로 재시작하면 교체 날 고리가 된다. 기본 격자는
    수집기가 늘 받는 시리즈라 그게 비면 수집기 안 무언가가 멎은 것이다.
    """

    base_pairs: frozenset[tuple[str, str]]
    after_ms: int
    """「다음 봉이 닫힌 뒤」 이만큼 지나도 비어 있으면 재시작한다."""
    cooldown_ms: int
    max_per_day: int
    enabled: bool = True


def collector_restart_reason(
    view: HealthView, policy: CollectorRestartPolicy, *, now_ms: int
) -> str | None:
    """기본 격자 시리즈가 충분히 오래 비어 있으면 그 사유 한 줄(아니면 None · 순수 함수).

    「비어 있다」 = 마지막 봉 다음 봉이 닫힌 시각(`마지막 봉 + 2 × TF`)에서 `after_ms` 넘게 지났는데
    그 봉이 없다. STALE(빨강) 판정도 함께 요구한다 — 화면·경고와 다른 기준으로 프로세스를 죽이지
    않게.
    """
    overdue: list[tuple[int, SeriesFreshness]] = []
    for f in view.freshness:
        if (f.symbol, f.timeframe) not in policy.base_pairs:
            continue
        if f.level is not HealthLevel.STALE or f.last_open_time is None:
            continue
        if f.expected_interval_ms is None:
            continue
        over = now_ms - (f.last_open_time + 2 * f.expected_interval_ms)
        if over >= policy.after_ms:
            overdue.append((over, f))
    if not overdue:
        return None
    over, worst = max(overdue, key=lambda item: item[0])
    return (
        f"기본 격자 {len(overdue)}개 시리즈가 비어 있음 — 가장 오래된 {worst.symbol} "
        f"{worst.timeframe} 최신 봉 {_fmt_at(worst.last_open_time)} (다음 봉 {_fmt_lag(over)} 지연)"
    )


RestartDecision = Literal["restart", "cooldown", "capped"]


def decide_collector_restart(
    state: WatchState, policy: CollectorRestartPolicy, *, now_ms: int
) -> tuple[RestartDecision, int]:
    """(결정, 지난 24시간 재시작 수). 쿨다운 안이면 조용히 기다리고, 하루 상한이면 멈춘다."""
    recent = [t for t in state.collector_restarts if now_ms - t < _DAY_MS]
    if recent and now_ms - max(recent) < policy.cooldown_ms:
        return "cooldown", len(recent)
    if len(recent) >= policy.max_per_day:
        return "capped", len(recent)
    return "restart", len(recent)


def _process_command(pid: int) -> str | None:
    """pid의 명령줄(없으면 None). 리눅스는 /proc, 그 밖은 `ps`."""
    proc = Path(f"/proc/{pid}/cmdline")
    try:
        if proc.exists():
            return proc.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return None
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    command = out.stdout.strip()
    return command or None


def is_collector_command(command: str) -> bool:
    """수집기 프로세스의 명령줄인가(`alphablock collect` · `python -m data.collector`)."""
    return ("alphablock" in command and " collect" in command) or "data.collector" in command


def signal_collector(heartbeat_path: str | Path) -> tuple[bool, str]:
    """하트비트 파일의 pid가 **수집기 프로세스가 맞으면** SIGTERM을 보낸다 — (성공, 설명).

    systemd `Restart=always`가 다시 띄운다(유닛의 `SuccessExitStatus=143`이 이 종료를 정상으로
    기록한다). pid가 재사용됐을 수 있어 명령줄을 확인한다 — 확인이 안 되면 보내지 않는다.
    `systemctl restart`를 쓰지 않는 이유: 워치는 일반 사용자로 돌아 시스템 유닛을 다룰 권한이
    없다(sudo 규칙을 늘리지 않는다). 수집기는 같은 사용자라 신호는 보낼 수 있다.
    """
    pid = HeartbeatStore(heartbeat_path).load().pid
    if pid is None:
        return False, "하트비트 파일에 pid가 없습니다(WAN-470 이전 수집기 — 재시작 뒤부터 기록)"
    command = _process_command(pid)
    if command is None:
        return False, f"pid {pid} 프로세스가 없습니다(이미 재시작 중일 수 있음)"
    if not is_collector_command(command):
        return False, f"pid {pid}는 수집기가 아닙니다({command[:80]}) — 신호를 보내지 않음"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return False, f"pid {pid}에 SIGTERM 실패: {exc}"
    return True, f"pid {pid}에 SIGTERM"


Restarter = Callable[[], tuple[bool, str]]


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
    """텔레그램 미설정(드라이런) 시 경고를 로그로만 남긴다.

    ⚠️ **True 를 낸다** — 「로그에 남겼다」를 전달로 친다. 전송 실패는 쿨다운을 걸지 않는데
    (WAN-344 `apply_delivery_failures`) 드라이런이 실패로 취급되면 같은 경고가 점검마다
    로그를 채운다. 드라이런의 쿨다운 거동은 예전 그대로 두는 쪽이 옳다 — 「보낼 곳이
    없다」는 사실은 종료 코드(`--require-delivery`)가 따로 낸다.
    """
    _logger.info("[드라이런] Health 경고:\n%s", text)
    return True


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
        restart_policy: CollectorRestartPolicy | None = None,
        restart_collector: Restarter | None = None,
    ) -> None:
        self._restart_policy = restart_policy
        self._restart_collector = restart_collector
        self._view_provider = view_provider
        self._notify = notify
        self._store = store
        self._cooldown_ms = int(cooldown_seconds * 1000)
        self._interval = interval_seconds
        self._sleep = sleep
        self._now_ms = now_ms
        self._failed_sends = 0

    @property
    def failed_sends(self) -> int:
        """이 워치가 지금까지 보내지 **못한** 메시지 수(WAN-344 종료 코드 근거)."""
        return self._failed_sends

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
        actions, alerts, state = self._collector_backstop(view, alerts, state)
        outbound, new_state = reconcile(
            alerts,
            state,
            now_ms=self._now_ms(),
            cooldown_ms=self._cooldown_ms,
            excluded_keys=excluded_alert_keys(view),
        )
        outbound = actions + outbound
        undelivered = [m for m in outbound if not self._notify(m.text)]
        self._failed_sends += len(undelivered)
        # 실패한 것만 이전 상태로 되돌린다 — 안 그러면 「안 갔는데 쿨다운은 걸린」 상태가
        # 된다(WAN-344).
        self._store.save(apply_delivery_failures(new_state, state, undelivered))
        if outbound:
            _logger.info(
                "Health 점검: 경고 %d건, 복구 %d건, 감시 제외 %d건%s",
                sum(1 for m in outbound if m.kind == "alert"),
                sum(1 for m in outbound if m.kind == "recovery"),
                sum(1 for m in outbound if m.kind == "excluded"),
                f", 전송 실패 {len(undelivered)}건" if undelivered else "",
            )
        if undelivered:
            _logger.error(
                "Health 경고 전송 실패 %d건 — 다음 점검에서 재시도합니다(쿨다운 미적용).",
                len(undelivered),
            )
        return outbound

    def _collector_backstop(
        self, view: HealthView, alerts: list[Alert], state: WatchState
    ) -> tuple[list[Outbound], list[Alert], WatchState]:
        """기본 격자가 오래 비면 수집기를 다시 띄운다(WAN-470) — (조치 알림, 경고, 상태).

        이번 사건(2026-10-22)에서 실제로 감지한 것은 워치뿐이었다. 수집기 안 원인이 무엇이든
        공백을 「워치 주기 + `after`」 안으로 닫는 바깥 상한이다. 쿨다운 안이면 조용히 기다리고,
        하루 상한이면 재시작을 멈추고 경고로 사람을 부른다.
        """
        policy = self._restart_policy
        if policy is None or not policy.enabled or self._restart_collector is None:
            return [], alerts, state
        now = self._now_ms()
        reason = collector_restart_reason(view, policy, now_ms=now)
        if reason is None:
            return [], alerts, state
        decision, count = decide_collector_restart(state, policy, now_ms=now)
        if decision == "cooldown":
            return [], alerts, state
        if decision == "capped":
            capped = Alert(
                key=COLLECTOR_RESTART_ALERT_KEY,
                title="수집기 자동 재시작 중단(하루 상한)",
                detail=(
                    "🚨 *수집기 자동 재시작 중단* (WAN-470)\n"
                    f"지난 24시간에 이미 {count}회 재시작했는데 아직 비어 있습니다 — {reason}.\n"
                    "재시작으로 안 풀리는 문제입니다. 수집기 로그(`logs/collector.log`)를 보세요."
                ),
            )
            return [], [*alerts, capped], state
        ok, detail = self._restart_collector()
        if not ok:
            failed = Alert(
                key=COLLECTOR_RESTART_ALERT_KEY,
                title="수집기 자동 재시작 실패",
                detail=f"🚨 *수집기 자동 재시작 실패* (WAN-470)\n{reason}.\n{detail}",
            )
            _logger.error("수집기 자동 재시작 실패: %s — %s", reason, detail)
            return [], [*alerts, failed], state
        _logger.warning("수집기 자동 재시작: %s — %s", reason, detail)
        message = Outbound(
            kind="action",
            key="collector_restart_done",
            text=(
                "🔁 *수집기 자동 재시작함* (WAN-470)\n"
                f"{reason}.\n{detail} — 서비스 매니저가 다시 띄우고 기동 백필이 공백을 메웁니다"
                f" (지난 24시간 {count + 1}/{policy.max_per_day}회)."
            ),
        )
        restarted = state.model_copy(
            update={
                "collector_restarts": [
                    *[t for t in state.collector_restarts if now - t < _DAY_MS],
                    now,
                ]
            }
        )
        return [message], alerts, restarted

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
            # 펀딩 판정 대상을 **설정 유니버스**로 좁힌다(WAN-407 §2) — 화면(`alphablock
            # status`)과 폰(이 워치)이 같은 대상을 봐야 한다. 기본값(저장된 심볼 전체)이면
            # 옛 유니버스 잔재가 영원히 STALE로 떠 **상시 빨간불**이 되고, 그러면 진짜
            # 펀딩 정지와 구분되지 않는다(WAN-321이 고친 실패 부류).
            funding_symbols=list(settings.symbols),
            cycle_budget_ms=runner_cycle_budget_ms(settings.live_signal_timeframes),
            # 데이터 신선도 판정을 **지금 수집 계획**으로 좁힌다(WAN-458) — 팔 목록에서 빠진
            # 종목은 일부러 안 모으므로 「멈춤」이 아니다. 계획을 못 읽으면 None = 옛 동작.
            scope=monitor_scope_for(settings),
        )

    return provider


def collector_restart_policy(settings: Settings) -> CollectorRestartPolicy:
    """설정에서 수집기 자동 재시작 조건을 만든다(WAN-470 · 기본 격자 = 수집기와 같은 함수)."""
    return CollectorRestartPolicy(
        base_pairs=frozenset(build_plan(settings.symbols, settings.timeframes).pairs()),
        after_ms=settings.health_watch_collector_restart_after_seconds * 1000,
        cooldown_ms=settings.health_watch_collector_restart_cooldown_seconds * 1000,
        max_per_day=settings.health_watch_collector_restart_max_per_day,
        enabled=settings.health_watch_collector_restart_enabled,
    )


def run_health_watch(
    settings: Settings | None = None,
    *,
    once: bool = False,
    dry_run: bool = False,
    test_message: bool = False,
    require_delivery: bool = False,
) -> int:
    """헬스 워치를 실행하고 **종료 코드**를 낸다(`alphablock watch` 공용).

    - `test_message=True`: 텔레그램 연결 확인용 메시지 1건만 보내고 종료.
    - `dry_run=True`: 텔레그램 전송 없이 경고를 로그로만 출력.
    - `once=True`: 한 번만 점검하고 종료(그 외에는 무한 점검 루프).
    - `require_delivery=True`: 텔레그램이 **없거나** 전송이 실패하면 0이 아닌 코드를 낸다.
      systemd 타이머로 도는 유닛이 쓰는 모드다 — 이게 없으면 「등록은 됐는데 조용히
      드라이런」이 성공으로 보인다(WAN-344 · WAN-321이 고친 실패 부류의 이웃).

    종료 코드: `WATCH_OK`(0) · `WATCH_DELIVERY_FAILED`(1) · `WATCH_TELEGRAM_UNCONFIGURED`(2).
    ⚠️ `require_delivery=False`(기본)면 점검 자체는 예전과 같이 돌고 항상 0을 낸다 —
    바뀐 것은 `test_message` 뿐이다(연결 확인이 실패했는데 0을 내면 확인이 아니다).
    """
    settings = settings or get_settings()
    if dry_run and require_delivery:
        raise ValueError("dry_run 과 require_delivery 는 함께 쓸 수 없습니다(보내지 않는 모드).")

    telegram = None if dry_run else build_telegram_client(settings)
    if test_message:
        if telegram is None:
            _logger.error("텔레그램이 설정되지 않았습니다(ALPHABLOCK_TELEGRAM_*).")
            return WATCH_TELEGRAM_UNCONFIGURED
        ok = telegram.send_message("✅ AlphaBlock Health 워치 테스트 메시지 (WAN-32)")
        _logger.info("테스트 메시지 전송 %s", "성공" if ok else "실패")
        return WATCH_OK if ok else WATCH_DELIVERY_FAILED

    if telegram is None and not dry_run:
        if require_delivery:
            # 조용히 드라이런으로 접지 않는다 — 그러면 「감시가 도는데 아무 데도 안 가는」
            # 상태가 systemd 에서 성공으로 보인다(WAN-344).
            _logger.error(
                "텔레그램 미설정(ALPHABLOCK_TELEGRAM_*) — 경보를 보낼 곳이 없어 중단합니다."
            )
            return WATCH_TELEGRAM_UNCONFIGURED
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
        restart_policy=collector_restart_policy(settings),
        restart_collector=partial(signal_collector, settings.collector_heartbeat_path),
    )
    _logger.info(
        "Health 워치 시작: 점검 %ds, 쿨다운 %ds",
        settings.health_watch_interval_seconds,
        settings.health_watch_cooldown_seconds,
    )
    watch.run(max_checks=1 if once else None)
    if require_delivery and watch.failed_sends:
        return WATCH_DELIVERY_FAILED
    return WATCH_OK


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
    parser.add_argument(
        "--require-delivery",
        action="store_true",
        help="텔레그램 미설정·전송 실패를 0이 아닌 종료 코드로 낸다(systemd 감시용, WAN-344)",
    )
    args = parser.parse_args()

    use_kst_logging()  # 로그 시각도 KST(WAN-172)
    logging.basicConfig(level=logging.INFO, format=kst_log_format())
    if args.dry_run and args.require_delivery:
        parser.error("--dry-run 과 --require-delivery 는 함께 쓸 수 없습니다.")
    raise SystemExit(
        run_health_watch(
            once=args.once,
            dry_run=args.dry_run,
            test_message=args.test_message,
            require_delivery=args.require_delivery,
        )
    )


if __name__ == "__main__":
    main()
