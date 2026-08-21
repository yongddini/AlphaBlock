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
#   ./scripts/install-systemd.sh                 # 넷 다(수집기 + 러너 + 대시보드 + 무결성 타이머)
#   ./scripts/install-systemd.sh collector       # 수집기만
#   ./scripts/install-systemd.sh live            # 러너만
#   ./scripts/install-systemd.sh dashboard       # 대시보드만
#   ./scripts/install-systemd.sh doctor          # DB 점검 타이머 두 쌍(전수 + 싼 점검)
#   ./scripts/install-systemd.sh watch           # 운영 상태 워치 타이머(러너·수집 정지 경보)
#
# 🚨 워치(WAN-344)를 빼먹지 말 것 — 수집기와 러너는 별개 프로세스라 **러너만 죽으면**
# 봉은 계속 신선하고 대시보드도 정상으로 보인다. doctor 는 DB 무결성만 보지 러너 생존을
# 안 본다. `alphablock watch` 가 러너 정지를 아는 유일한 장치인데, 서버 실측(2026-08-20)
# 에서 크론·타이머 어디에도 등록돼 있지 않아 34분·41분 정지를 아무도 몰랐다.
# 간격은 ALPHABLOCK_WATCH_INTERVAL 로 지정한다(기본 10min).
#
# 무결성 점검은 타이머 **두 쌍**으로 돈다(WAN-318 §2):
#   • alphablock-doctor.timer        전수(`PRAGMA quick_check` 포함)  기본 1d
#   • alphablock-doctor-light.timer  싼 점검만(--skip-quick-check)    기본 1h
# 간격은 ALPHABLOCK_DOCTOR_INTERVAL / ALPHABLOCK_DOCTOR_LIGHT_INTERVAL 로 지정한다. 예:
#   ALPHABLOCK_DOCTOR_INTERVAL=12h ./scripts/install-systemd.sh doctor
# 🚨 주기를 실행 시간보다 짧게 잡지 말 것 — 옛 기본값 15min 은 서버 실측 18분+ 전수
# 스캔보다 짧아 DB 풀스캔이 상시 걸려 있었다(WAN-318 §1).
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
# WAN-318 §1: 전수 점검은 하루 1회(옛 기본값 15min 은 서버 실측 18분+ 실행보다 짧았다).
DOCTOR_INTERVAL="${ALPHABLOCK_DOCTOR_INTERVAL:-1d}"
# WAN-318 §2: quick_check 를 뺀 싼 점검은 자주 본다(로컬 7.3GB 실측 90초 → 1h 는 40배 여유).
DOCTOR_LIGHT_INTERVAL="${ALPHABLOCK_DOCTOR_LIGHT_INTERVAL:-1h}"
# WAN-344: 러너 정지를 몇 분 만에 알 것인가 = 이 값. 설정 기본값
# health_watch_interval_seconds(600초)와 같은 10분이다. 실측 정지 공백이 11·34·41분이라
# 이보다 크게 잡으면 그만큼 늦게 안다.
WATCH_INTERVAL="${ALPHABLOCK_WATCH_INTERVAL:-10min}"
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

    render_unit "$template" "$rendered"

    sudo install -m 644 "$rendered" "$UNIT_DIR/$unit"
    rm -f "$rendered"

    sudo systemctl daemon-reload
    sudo systemctl enable --now "$unit"
    echo "✅ 설치·시작: $unit (로그: $LOG_DIR/${label}.log)"
}

# 템플릿의 __PLACEHOLDER__ 를 실제 값으로 치환해 $2 에 렌더한다.
render_unit() {
    local template="$1" out="$2"
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
        -e "s|__DOCTOR_INTERVAL__|${DOCTOR_INTERVAL}|g" \
        -e "s|__DOCTOR_LIGHT_INTERVAL__|${DOCTOR_LIGHT_INTERVAL}|g" \
        -e "s|__WATCH_INTERVAL__|${WATCH_INTERVAL}|g" \
        "$template" > "$out"
}

# DB 점검(WAN-185, 분리 = WAN-318 §2): oneshot 서비스 + 타이머 한 쌍을 설치한다.
# 서비스는 부팅 자동 시작하지 않고(타이머가 트리거), 타이머만 enable --now 한다.
install_timer_pair() {
    local name="$1" interval="$2" log_name="${3:-doctor}"
    local svc="${name}.service"
    local timer="${name}.timer"
    local rendered
    rendered="$(mktemp)"

    render_unit "$TEMPLATE_DIR/${svc}.template" "$rendered"
    sudo install -m 644 "$rendered" "$UNIT_DIR/$svc"
    render_unit "$TEMPLATE_DIR/${timer}.template" "$rendered"
    sudo install -m 644 "$rendered" "$UNIT_DIR/$timer"
    rm -f "$rendered"

    sudo systemctl daemon-reload
    sudo systemctl enable --now "$timer"
    echo "✅ 설치·시작: $timer (간격 ${interval} · 로그: $LOG_DIR/${log_name}.log)"
}

# 전수 점검(하루 1회) + 싼 점검(자주) 두 쌍을 함께 건다 — 한쪽만 걸면 「무겁지만 드문
# 점검」이나 「자주 보지만 손상은 못 보는 점검」 한쪽만 남는다(WAN-318 §2).
install_doctor() {
    install_timer_pair alphablock-doctor "$DOCTOR_INTERVAL"
    install_timer_pair alphablock-doctor-light "$DOCTOR_LIGHT_INTERVAL"
}

# 운영 상태 워치(WAN-32, 등록 = WAN-344): oneshot + 타이머 한 쌍.
# 상시 service 가 아니라 timer + `--once` 인 이유는 유닛 템플릿 주석에 적었다(쿨다운
# 상태가 파일로 영속화돼 있고, 상시 프로세스는 「감시의 감시」가 또 필요하다).
install_watch() {
    install_timer_pair alphablock-watch "$WATCH_INTERVAL" watch
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
    doctor)
        install_doctor
        ;;
    watch)
        install_watch
        ;;
    all)
        install_one collector
        install_one live
        install_one dashboard
        install_doctor
        install_watch
        ;;
    *)
        echo "사용법: $0 [collector|live|dashboard|doctor|watch|all]" >&2
        exit 1
        ;;
esac

echo
echo "상태 확인: systemctl status alphablock-collector alphablock-live alphablock-dashboard"
echo "타이머 확인: systemctl list-timers 'alphablock-*'   (doctor 두 쌍 = WAN-185/318 · 워치 = WAN-344)"
echo "수집 확인: uv run -- alphablock status   (웹소켓이 1분봉을 받는지 = WAN-174 완료 기준)"
echo
echo "🚨 워치를 등록했으면 경보가 실제로 **도착하는지** 1회 확인하세요(WAN-344 §4-4):"
echo "   uv run -- alphablock watch --test-message   # 폰에 안 오면 종료 코드 1/2"
echo "   등록만 하고 도착을 안 보면 「감시는 도는데 아무 데도 안 가는」 그 자리입니다."
