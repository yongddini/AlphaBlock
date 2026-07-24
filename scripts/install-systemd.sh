#!/usr/bin/env bash
#
# AlphaBlock 상시 구동 서비스 설치 (WAN-174, 리눅스 systemd)
#
# 데이터 수집기(alphablock collect)·실시간 시그널 러너(alphablock live)·대시보드
# (streamlit)를 systemd 시스템 서비스로 등록해 부팅 시 자동 시작·크래시 시 자동
# 재시작되게 한다. macOS launchd 판(scripts/install-daemons.sh, WAN-31/48)의
# 리눅스 서버 대응이다 — 로컬 맥은 ASTx가 바이낸스 선물 웹소켓을 막아 수집이
# 불가하므로(WAN-174) 수집·페이퍼 러너를 서버에서 돌린다.
#
# 사용 (저장소 루트가 서버에 clone 돼 있고 `uv sync` 를 마친 상태에서):
#   ./scripts/install-systemd.sh                 # 셋 다(수집기 + 러너 + 대시보드)
#   ./scripts/install-systemd.sh collector       # 수집기만
#   ./scripts/install-systemd.sh live            # 러너만
#   ./scripts/install-systemd.sh dashboard       # 대시보드만
#
# 대시보드 포트는 ALPHABLOCK_DASHBOARD_PORT(기본 8501)로 지정한다. 예:
#   ALPHABLOCK_DASHBOARD_PORT=9000 ./scripts/install-systemd.sh dashboard
# 대시보드는 127.0.0.1 로만 바인딩한다 — 접속은 SSH 터널로:
#   ssh -N -L 8501:127.0.0.1:8501 <서버> 후 http://localhost:8501
#
# 안전: 러너는 페이퍼 모드(live_trading=false)로만 돈다. 실주문은 하지 않는다.
# 시스템 유닛 설치라 sudo 가 필요하다(서비스 자체는 현재 사용자 권한으로 돈다).
#
# 해제는 scripts/uninstall-systemd.sh, 서버 준비·DB 이전 절차는
# docs/ops/server-migration.md 참고.
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "❌ 이 스크립트는 리눅스 서버 전용입니다. macOS 는 scripts/install-daemons.sh 를 쓰세요." >&2
    exit 1
fi
if ! command -v systemctl >/dev/null; then
    echo "❌ systemctl 을 찾을 수 없습니다(systemd 미탑재 배포판)." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DIR="$REPO_DIR/scripts/systemd"
UNIT_DIR="/etc/systemd/system"
LOG_DIR="${ALPHABLOCK_LOG_DIR:-$REPO_DIR/logs}"
DASHBOARD_PORT="${ALPHABLOCK_DASHBOARD_PORT:-8501}"
RUN_USER="$(id -un)"

# uv 실행 파일 절대 경로(systemd 는 셸 PATH 를 물려받지 않는다).
UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
    echo "❌ uv 를 찾을 수 없습니다. 먼저 scripts/setup-server.sh 를 실행하세요." >&2
    exit 1
fi

# ExecStart 하위 프로세스용 PATH: uv 디렉터리 + 시스템 기본.
UV_DIR="$(dirname "$UV_BIN")"
SERVICE_PATH="$UV_DIR:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$LOG_DIR"

install_one() {
    local label="$1"
    local unit="alphablock-${label}.service"
    local template="$TEMPLATE_DIR/${unit}.template"
    local rendered
    rendered="$(mktemp)"

    if [[ ! -f "$template" ]]; then
        echo "❌ 템플릿이 없습니다: $template" >&2
        exit 1
    fi

    sed \
        -e "s|__UV_BIN__|${UV_BIN}|g" \
        -e "s|__WORKDIR__|${REPO_DIR}|g" \
        -e "s|__PATH__|${SERVICE_PATH}|g" \
        -e "s|__LOG_DIR__|${LOG_DIR}|g" \
        -e "s|__RUN_USER__|${RUN_USER}|g" \
        -e "s|__DASHBOARD_PORT__|${DASHBOARD_PORT}|g" \
        "$template" > "$rendered"

    sudo install -m 644 "$rendered" "$UNIT_DIR/$unit"
    rm -f "$rendered"

    sudo systemctl daemon-reload
    sudo systemctl enable --now "$unit"
    echo "✅ 설치·시작: $unit (로그: $LOG_DIR/${label}.log)"
}

TARGET="${1:-all}"
case "$TARGET" in
    collector)
        install_one collector
        ;;
    live)
        install_one live
        ;;
    dashboard)
        install_one dashboard
        ;;
    all)
        install_one collector
        install_one live
        install_one dashboard
        ;;
    *)
        echo "사용법: $0 [collector|live|dashboard|all]" >&2
        exit 1
        ;;
esac

echo
echo "상태 확인: systemctl status alphablock-collector alphablock-live alphablock-dashboard"
echo "수집 확인: uv run -- alphablock status   (웹소켓이 1분봉을 받는지 = WAN-174 완료 기준)"
