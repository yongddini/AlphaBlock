"""데몬 설치 스크립트·launchd 템플릿 정합성 테스트 (WAN-31 · WAN-48).

launchctl 을 실제로 부르지 않고, 템플릿 내용과 설치/해제 스크립트가 세 데몬
(collector·live·dashboard)을 대칭으로 다루는지 파일 내용으로 검증한다. 대시보드
데몬(WAN-48)의 안전 요건(headless·localhost 바인딩·포트 자리표시자)도 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_LAUNCHD = _REPO / "scripts" / "launchd"
_INSTALL = _REPO / "scripts" / "install-daemons.sh"
_UNINSTALL = _REPO / "scripts" / "uninstall-daemons.sh"

_LABELS = ("collector", "live", "dashboard")


@pytest.mark.parametrize("label", _LABELS)
def test_plist_template_exists(label: str) -> None:
    assert (_LAUNCHD / f"com.alphablock.{label}.plist.template").is_file()


@pytest.mark.parametrize("label", _LABELS)
def test_plist_template_has_keep_alive_and_run_at_load(label: str) -> None:
    text = (_LAUNCHD / f"com.alphablock.{label}.plist.template").read_text()
    # 로그인 시 자동 시작·크래시 시 재시작.
    assert "<key>RunAtLoad</key>" in text
    assert "<key>KeepAlive</key>" in text
    # 설치 스크립트가 치환하는 공통 자리표시자.
    for placeholder in ("__UV_BIN__", "__WORKDIR__", "__PATH__", "__LOG_DIR__"):
        assert placeholder in text


def test_dashboard_template_is_safe_and_headless() -> None:
    text = (_LAUNCHD / "com.alphablock.dashboard.plist.template").read_text()
    # 첫 실행 이메일 프롬프트 억제.
    assert "--server.headless" in text
    # 로컬(127.0.0.1) 바인딩만 — 외부 노출 금지.
    assert "--server.address" in text
    assert "127.0.0.1" in text
    # 포트는 설치 스크립트가 치환한다.
    assert "__DASHBOARD_PORT__" in text
    assert "dashboard/app.py" in text
    # 대시보드는 streamlit 을 띄울 뿐, 실주문 경로인 러너/수집 CLI 커맨드를 켜지 않는다.
    assert "<string>streamlit</string>" in text
    assert "<string>live</string>" not in text
    assert "<string>collect</string>" not in text


@pytest.mark.parametrize("label", _LABELS)
def test_install_and_uninstall_handle_each_label(label: str) -> None:
    install = _INSTALL.read_text()
    uninstall = _UNINSTALL.read_text()
    fq = f"com.alphablock.{label}"
    # 개별 설치·해제 대상으로 명시돼 있어야 한다.
    assert fq in install
    assert fq in uninstall
    # case 분기(개별 인자)도 노출돼 있어야 한다.
    assert f"    {label})" in install
    assert f"    {label})" in uninstall


def test_install_substitutes_dashboard_port() -> None:
    install = _INSTALL.read_text()
    # 포트 자리표시자 치환 + 환경변수 기본값 8501.
    assert "__DASHBOARD_PORT__" in install
    assert "ALPHABLOCK_DASHBOARD_PORT" in install
    assert "8501" in install
