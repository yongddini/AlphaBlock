"""systemd 서비스 템플릿·설치 스크립트 정합성 테스트 (WAN-174, 리눅스 서버 이전).

systemctl 을 실제로 부르지 않고, 템플릿 내용과 설치/해제 스크립트가 세 서비스
(collector·live·dashboard)를 대칭으로 다루는지 파일 내용으로 검증한다 —
launchd 판(tests/test_daemon_scripts.py, WAN-31/48)과 같은 방식이다. 대시보드의
안전 요건(headless·localhost 바인딩·포트 자리표시자)과 서버 셋업 스크립트의
1GB 박스 제약(스왑) 반영도 확인한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SYSTEMD = _REPO / "scripts" / "systemd"
_INSTALL = _REPO / "scripts" / "install-systemd.sh"
_UNINSTALL = _REPO / "scripts" / "uninstall-systemd.sh"
_SETUP = _REPO / "scripts" / "setup-server.sh"

_LABELS = ("collector", "live", "dashboard")
#: 타이머로 도는 oneshot 점검 쌍(WAN-185 전수 + WAN-318 §2 싼 점검).
_DOCTOR_UNITS = ("alphablock-doctor", "alphablock-doctor-light")
#: 운영 상태 워치(WAN-32) — 등록은 WAN-344.
_WATCH_UNIT = "alphablock-watch"


@pytest.mark.parametrize("label", _LABELS)
def test_unit_template_exists(label: str) -> None:
    assert (_SYSTEMD / f"alphablock-{label}.service.template").is_file()


@pytest.mark.parametrize("label", _LABELS)
def test_unit_template_has_restart_and_placeholders(label: str) -> None:
    text = (_SYSTEMD / f"alphablock-{label}.service.template").read_text()
    # 부팅 시 자동 시작(enable 대상) + 크래시 시 자동 재시작.
    assert "WantedBy=multi-user.target" in text
    assert "Restart=always" in text
    # 재시작 폭주 방지(launchd ThrottleInterval=10 대응).
    assert "RestartSec=10" in text
    # 설치 스크립트가 치환하는 공통 자리표시자.
    for placeholder in (
        "__UV_BIN__",
        "__WORKDIR__",
        "__PATH__",
        "__LOG_DIR__",
        "__RUN_USER__",
    ):
        assert placeholder in text


def test_dashboard_template_is_safe_and_headless() -> None:
    text = (_SYSTEMD / "alphablock-dashboard.service.template").read_text()
    # 첫 실행 이메일 프롬프트 억제.
    assert "--server.headless true" in text
    # 로컬(127.0.0.1) 바인딩만 — 외부 노출 금지, 접속은 SSH 터널.
    assert "--server.address 127.0.0.1" in text
    # 포트는 설치 스크립트가 치환한다.
    assert "__DASHBOARD_PORT__" in text
    assert "dashboard/app.py" in text
    # 대시보드는 streamlit 을 띄울 뿐, 러너/수집 CLI 커맨드를 켜지 않는다.
    assert "streamlit run" in text
    assert "alphablock live" not in text
    assert "alphablock collect" not in text


@pytest.mark.parametrize("label", _LABELS)
def test_install_and_uninstall_handle_each_label(label: str) -> None:
    install = _INSTALL.read_text()
    uninstall = _UNINSTALL.read_text()
    unit = f"alphablock-{label}"
    # 개별 설치·해제 대상으로 명시돼 있어야 한다.
    assert unit in install or f"install_one {label}" in install
    assert f"uninstall_one {label}" in uninstall
    # case 분기(개별 인자)도 노출돼 있어야 한다.
    assert f"    {label})" in install
    assert f"    {label})" in uninstall


def test_install_substitutes_dashboard_port() -> None:
    install = _INSTALL.read_text()
    # 포트 자리표시자 치환 + 환경변수 기본값 8501.
    assert "__DASHBOARD_PORT__" in install
    assert "ALPHABLOCK_DASHBOARD_PORT" in install
    assert "8501" in install


def test_install_is_linux_only_and_renders_all_placeholders() -> None:
    install = _INSTALL.read_text()
    # macOS 에서 실행하면 거부한다(launchd 판과 혼동 방지).
    assert "uname -s" in install and "Linux" in install
    # 템플릿의 모든 자리표시자를 치환한다 — 하나라도 빠지면 유닛에 리터럴이 남는다.
    for label in _LABELS:
        template = (_SYSTEMD / f"alphablock-{label}.service.template").read_text()
        for token in (
            "__UV_BIN__",
            "__WORKDIR__",
            "__PATH__",
            "__LOG_DIR__",
            "__RUN_USER__",
            "__DASHBOARD_PORT__",
        ):
            if token in template:
                assert f"s|{token}|" in install, f"{token} 치환 누락"


@pytest.mark.parametrize("label", _LABELS)
def test_clean_stop_is_not_recorded_as_failure(label: str) -> None:
    """WAN-318 §3: `systemctl stop` 의 SIGTERM 종료(128+15=143)는 성공이다.

    이게 없으면 **정상 정지가 크래시와 화면에서 구분되지 않는다**(실제 오진 사례) —
    WAN-194 의 「정상 거부가 DB 손상과 같은 모양」과 같은 부류다.
    """
    text = (_SYSTEMD / f"alphablock-{label}.service.template").read_text()
    assert "SuccessExitStatus=143" in text


@pytest.mark.parametrize("unit", _DOCTOR_UNITS)
def test_doctor_units_keep_failure_monitoring(unit: str) -> None:
    """🚨 doctor 는 예외다 — 이상 시 종료 코드 1이 `systemctl --failed` 감시의 전부다.

    SuccessExitStatus 를 넣으면 그 감시가 조용히 죽는다(WAN-185 설계). 라벨이 아니라
    유닛 내용으로 잠근다.
    """
    text = (_SYSTEMD / f"{unit}.service.template").read_text()
    # 주석은 "왜 안 넣는지"를 적고 있으므로 **지시문만** 본다.
    assert "SuccessExitStatus" not in _directives(text)
    assert "Type=oneshot" in text
    assert "--notify-on-failure" in text


@pytest.mark.parametrize("unit", _DOCTOR_UNITS)
def test_doctor_units_yield_disk_to_collector_and_runner(unit: str) -> None:
    """WAN-318 §1·§2: 점검은 수집기·러너와 디스크를 두고 싸우지 않는다."""
    text = (_SYSTEMD / f"{unit}.service.template").read_text()
    assert "IOSchedulingClass=idle" in text
    assert "Nice=19" in text


def test_doctor_split_runs_quick_check_only_in_the_full_unit() -> None:
    """WAN-318 §2: 전수 판만 `quick_check` 를 돌고, 잦은 판은 그것만 건너뛴다.

    ⚠️ 점검 **항목을 줄인 게 아니다** — 같은 doctor 를 두 주기로 나눠 돌릴 뿐이라,
    싼 판에 `--skip-quick-check` 가 있고 전수 판에는 없어야 한다.
    """
    full = (_SYSTEMD / "alphablock-doctor.service.template").read_text()
    light = (_SYSTEMD / "alphablock-doctor-light.service.template").read_text()
    assert "--skip-quick-check" not in full
    assert "--skip-quick-check" in light


def test_doctor_interval_is_longer_than_a_measured_run() -> None:
    """WAN-318 §1: 「주기 < 실행 시간」이 되면 전수 스캔이 상시로 걸린다.

    서버 실측(2026-08-17) 전수 1회 = 18분+ 인데 옛 기본값이 15min 이었다. 기본값이 그
    실측보다 짧아지지 않게 **분 단위로 환산해** 잠근다(단위를 바꿔 적어도 걸리게).
    """
    install = _INSTALL.read_text()
    match = re.search(r"DOCTOR_INTERVAL=\"\$\{ALPHABLOCK_DOCTOR_INTERVAL:-([0-9a-z]+)\}\"", install)
    assert match is not None, "전수 점검 간격 기본값을 찾지 못했다"
    assert _to_minutes(match.group(1)) >= 12 * 60, "전수 점검 기본 주기가 서버 실측보다 짧다"

    light = re.search(
        r"DOCTOR_LIGHT_INTERVAL=\"\$\{ALPHABLOCK_DOCTOR_LIGHT_INTERVAL:-([0-9a-z]+)\}\"", install
    )
    assert light is not None, "싼 점검 간격 기본값을 찾지 못했다"
    # 싼 판도 로컬 7.3GB 실측 90초라 「공짜」가 아니다 — 옛 15min 로 되돌리지 않게 잠근다.
    assert _to_minutes(light.group(1)) >= 30


def _directives(unit_text: str) -> str:
    """유닛 파일에서 주석(`#`)을 뺀 지시문만 남긴다."""
    return "\n".join(line for line in unit_text.splitlines() if not line.lstrip().startswith("#"))


def _to_minutes(value: str) -> float:
    """systemd 간격 표기(`15min`·`1h`·`1d`)를 분으로 환산한다."""
    units = {"min": 1.0, "h": 60.0, "d": 1440.0, "s": 1 / 60, "w": 10080.0}
    for suffix, factor in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor
    raise AssertionError(f"알 수 없는 간격 표기: {value}")


@pytest.mark.parametrize("unit", _DOCTOR_UNITS)
def test_install_and_uninstall_handle_both_doctor_timers(unit: str) -> None:
    """한쪽만 설치·해제되면 「무겁지만 드문 점검」이나 「손상을 못 보는 점검」만 남는다."""
    install = _INSTALL.read_text()
    uninstall = _UNINSTALL.read_text()
    for suffix in (".service", ".timer"):
        assert (_SYSTEMD / f"{unit}{suffix}.template").is_file()
    assert f"install_timer_pair {unit}" in install
    assert f"uninstall_timer_pair {unit}" in uninstall


def test_install_substitutes_both_doctor_intervals() -> None:
    install = _INSTALL.read_text()
    for token in ("__DOCTOR_INTERVAL__", "__DOCTOR_LIGHT_INTERVAL__"):
        assert f"s|{token}|" in install, f"{token} 치환 누락"
        assert any(
            token in (_SYSTEMD / f"{unit}.timer.template").read_text() for unit in _DOCTOR_UNITS
        ), f"{token} 를 쓰는 타이머 템플릿이 없다"


def test_setup_server_creates_swap_and_installs_uv() -> None:
    setup = _SETUP.read_text()
    # 1GB 박스 제약(PM 실측): 스왑 2GB + fstab 등록으로 재부팅 유지.
    assert "SWAP_SIZE_MB=2048" in setup
    assert "/etc/fstab" in setup
    assert "mkswap" in setup and "swapon" in setup
    # uv 설치 + 의존성 동기화.
    assert "astral.sh/uv/install.sh" in setup
    assert "uv sync" in setup
    # 실거래 설정을 만들지 않는다(안전 규칙 문서화).
    assert "ALPHABLOCK_LIVE_TRADING" in setup


def test_scripts_are_executable() -> None:
    for script in (_INSTALL, _UNINSTALL, _SETUP):
        assert script.stat().st_mode & 0o111, f"{script.name} 에 실행 권한이 없다"


# -- 운영 상태 워치 (WAN-344) --------------------------------------------------


def test_watch_timer_pair_exists() -> None:
    for suffix in (".service", ".timer"):
        assert (_SYSTEMD / f"{_WATCH_UNIT}{suffix}.template").is_file()


def test_watch_runs_one_check_and_demands_delivery() -> None:
    """🚨 WAN-344 §4-4: 「감시는 도는데 경보는 아무 데도 안 가는」 상태가 성공이면 안 된다.

    `--require-delivery` 가 있어야 텔레그램 미설정·전송 실패가 종료 코드로 나오고
    systemd 가 `failed` 로 기록한다. `--once` 는 타이머 판이라는 뜻이다.
    """
    text = (_SYSTEMD / f"{_WATCH_UNIT}.service.template").read_text()
    assert "Type=oneshot" in text
    assert "alphablock watch --once --require-delivery" in text


def test_watch_keeps_failure_monitoring() -> None:
    """doctor 와 같은 이유로 SuccessExitStatus 를 넣지 않는다 — 넣으면 감시가 죽는다."""
    text = (_SYSTEMD / f"{_WATCH_UNIT}.service.template").read_text()
    assert "SuccessExitStatus" not in _directives(text)


def test_watch_yields_disk_to_collector_and_runner() -> None:
    text = (_SYSTEMD / f"{_WATCH_UNIT}.service.template").read_text()
    assert "IOSchedulingClass=idle" in text
    assert "Nice=19" in text


def test_watch_logs_to_its_own_file() -> None:
    """journald 보존이 짧아(서버 실측 3시간) 과거 조사는 파일 로그로만 된다(WAN-344)."""
    text = (_SYSTEMD / f"{_WATCH_UNIT}.service.template").read_text()
    assert "append:__LOG_DIR__/watch.log" in text


def test_watch_timer_is_installed_and_removable() -> None:
    """설치·해제 양쪽에 있어야 한다 — 한쪽만 있으면 유닛이 남거나 영영 안 걸린다."""
    install = _INSTALL.read_text()
    uninstall = _UNINSTALL.read_text()
    assert f"install_timer_pair {_WATCH_UNIT}" in install
    assert f"uninstall_timer_pair {_WATCH_UNIT}" in uninstall
    assert "    watch)" in install
    assert "    watch)" in uninstall


def test_install_all_includes_the_watch() -> None:
    """🚨 이 이슈의 본문 — 워치가 `all` 에 없으면 「등록돼 있지 않다」가 그대로 재발한다.

    수집기와 러너는 별개 프로세스라 러너만 죽으면 봉은 계속 신선하고 doctor 도 조용하다
    (WAN-344). 라벨이 아니라 `all` 분기의 실제 호출로 잠근다.
    """
    install = _INSTALL.read_text()
    all_branch = install.split("    all)", 1)[1].split(";;", 1)[0]
    assert "install_watch" in all_branch
    uninstall = _UNINSTALL.read_text()
    all_removed = uninstall.split("    all)", 1)[1].split(";;", 1)[0]
    assert "uninstall_watch" in all_removed


def test_install_substitutes_the_watch_interval() -> None:
    install = _INSTALL.read_text()
    assert "s|__WATCH_INTERVAL__|" in install
    assert "__WATCH_INTERVAL__" in (_SYSTEMD / f"{_WATCH_UNIT}.timer.template").read_text()
    assert "ALPHABLOCK_WATCH_INTERVAL" in install


def test_watch_interval_default_is_not_slower_than_the_settings_default() -> None:
    """주기 = 「러너가 죽고 몇 분 만에 아는가」. 실측 정지 공백이 11·34·41분이었다(WAN-344).

    설정 기본값 `health_watch_interval_seconds`(600초 = 10분)보다 느슨해지지 않게 잠근다.
    """
    install = _INSTALL.read_text()
    match = re.search(r"WATCH_INTERVAL=\"\$\{ALPHABLOCK_WATCH_INTERVAL:-([0-9a-z]+)\}\"", install)
    assert match is not None, "워치 간격 기본값을 찾지 못했다"
    assert _to_minutes(match.group(1)) <= 10
