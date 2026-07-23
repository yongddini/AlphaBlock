"""systemd 서비스 템플릿·설치 스크립트 정합성 테스트 (WAN-174, 리눅스 서버 이전).

systemctl 을 실제로 부르지 않고, 템플릿 내용과 설치/해제 스크립트가 세 서비스
(collector·live·dashboard)를 대칭으로 다루는지 파일 내용으로 검증한다 —
launchd 판(tests/test_daemon_scripts.py, WAN-31/48)과 같은 방식이다. 대시보드의
안전 요건(headless·localhost 바인딩·포트 자리표시자)과 서버 셋업 스크립트의
1GB 박스 제약(스왑) 반영도 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SYSTEMD = _REPO / "scripts" / "systemd"
_INSTALL = _REPO / "scripts" / "install-systemd.sh"
_UNINSTALL = _REPO / "scripts" / "uninstall-systemd.sh"
_SETUP = _REPO / "scripts" / "setup-server.sh"

_LABELS = ("collector", "live", "dashboard")


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
