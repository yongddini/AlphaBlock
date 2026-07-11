#!/usr/bin/env bash
#
# AlphaBlock 상시 구동 데몬 설치 (WAN-31, macOS launchd)
#
# 데이터 수집기(alphablock collect)와 실시간 시그널 러너(alphablock live)를
# launchd 에이전트로 등록해 로그인 시 자동 시작·크래시 시 자동 재시작(KeepAlive)
# 되게 한다. plist 템플릿의 경로 자리표시자를 실제 값으로 치환해 설치한다.
#
# 사용:
#   ./scripts/install-daemons.sh                 # 수집기 + 러너 둘 다
#   ./scripts/install-daemons.sh collector       # 수집기만
#   ./scripts/install-daemons.sh live            # 러너만
#
# 안전: 러너는 페이퍼 모드(live_trading=false)로만 돈다. 실주문은 하지 않는다.
#
# 해제는 scripts/uninstall-daemons.sh, 로그 확인은 README "상시 구동" 절 참고.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DIR="$REPO_DIR/scripts/launchd"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="${ALPHABLOCK_LOG_DIR:-$REPO_DIR/logs}"

# uv 실행 파일 절대 경로(launchd는 셸 PATH를 물려받지 않는다).
UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
    echo "❌ uv 를 찾을 수 없습니다. 먼저 uv 를 설치하세요 (https://docs.astral.sh/uv/)." >&2
    exit 1
fi

# 절전으로 데몬이 멈추지 않도록 caffeinate 로 감싼다(있으면). 없으면 env(무동작)로.
CAFFEINATE_BIN="$(command -v caffeinate || command -v env)"

# ProgramArguments 의 PATH: uv 디렉터리 + 시스템 기본.
UV_DIR="$(dirname "$UV_BIN")"
AGENT_PATH="$UV_DIR:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$AGENTS_DIR" "$LOG_DIR"

install_one() {
    local label="$1"
    local template="$TEMPLATE_DIR/${label}.plist.template"
    local target="$AGENTS_DIR/${label}.plist"

    if [[ ! -f "$template" ]]; then
        echo "❌ 템플릿이 없습니다: $template" >&2
        exit 1
    fi

    sed \
        -e "s|__CAFFEINATE__|${CAFFEINATE_BIN}|g" \
        -e "s|__UV_BIN__|${UV_BIN}|g" \
        -e "s|__WORKDIR__|${REPO_DIR}|g" \
        -e "s|__PATH__|${AGENT_PATH}|g" \
        -e "s|__LOG_DIR__|${LOG_DIR}|g" \
        "$template" >"$target"

    # 이미 로드돼 있으면 먼저 언로드(재설치 대비). 실패는 무시.
    launchctl unload "$target" 2>/dev/null || true
    launchctl load "$target"
    echo "✅ 설치·로드됨: $label  (로그: $LOG_DIR/${label#com.alphablock.}.log)"
}

case "${1:-all}" in
    all)
        install_one "com.alphablock.collector"
        install_one "com.alphablock.live"
        ;;
    collector)
        install_one "com.alphablock.collector"
        ;;
    live)
        install_one "com.alphablock.live"
        ;;
    *)
        echo "사용법: $0 [all|collector|live]" >&2
        exit 2
        ;;
esac

echo
echo "상태 확인:  launchctl list | grep alphablock"
echo "로그 보기:  tail -f $LOG_DIR/collector.log $LOG_DIR/live.log"
echo "요약 보기:  uv run alphablock status"
