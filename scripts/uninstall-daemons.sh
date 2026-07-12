#!/usr/bin/env bash
#
# AlphaBlock 상시 구동 데몬 해제 (WAN-31, macOS launchd)
#
# install-daemons.sh 로 등록한 launchd 에이전트를 언로드하고 plist 를 제거한다.
# 로그 파일은 남긴다(진단용). 로그까지 지우려면 logs/ 를 직접 삭제한다.
#
# 사용:
#   ./scripts/uninstall-daemons.sh               # 셋 다(수집기 + 러너 + 대시보드)
#   ./scripts/uninstall-daemons.sh collector     # 수집기만
#   ./scripts/uninstall-daemons.sh live          # 러너만
#   ./scripts/uninstall-daemons.sh dashboard     # 대시보드만
set -euo pipefail

AGENTS_DIR="$HOME/Library/LaunchAgents"

uninstall_one() {
    local label="$1"
    local target="$AGENTS_DIR/${label}.plist"

    if [[ -f "$target" ]]; then
        launchctl unload "$target" 2>/dev/null || true
        rm -f "$target"
        echo "🗑️  해제됨: $label"
    else
        echo "ℹ️  설치돼 있지 않음: $label"
    fi
}

case "${1:-all}" in
    all)
        uninstall_one "com.alphablock.collector"
        uninstall_one "com.alphablock.live"
        uninstall_one "com.alphablock.dashboard"
        ;;
    collector)
        uninstall_one "com.alphablock.collector"
        ;;
    live)
        uninstall_one "com.alphablock.live"
        ;;
    dashboard)
        uninstall_one "com.alphablock.dashboard"
        ;;
    *)
        echo "사용법: $0 [all|collector|live|dashboard]" >&2
        exit 2
        ;;
esac
