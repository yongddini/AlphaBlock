#!/usr/bin/env bash
#
# AlphaBlock systemd 서비스 해제 (WAN-174, 리눅스)
#
# scripts/install-systemd.sh 로 등록한 서비스를 정지·비활성화하고 유닛 파일을
# 지운다. 로그(logs/*.log)와 DB 는 남긴다. macOS launchd 판은
# scripts/uninstall-daemons.sh.
#
# 사용:
#   ./scripts/uninstall-systemd.sh               # 셋 다
#   ./scripts/uninstall-systemd.sh collector     # 수집기만
#   ./scripts/uninstall-systemd.sh live          # 러너만
#   ./scripts/uninstall-systemd.sh dashboard     # 대시보드만
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]] || ! command -v systemctl >/dev/null; then
    echo "❌ 리눅스 systemd 환경이 아닙니다." >&2
    exit 1
fi

UNIT_DIR="/etc/systemd/system"

uninstall_one() {
    local label="$1"
    local unit="alphablock-${label}.service"

    if [[ -f "$UNIT_DIR/$unit" ]]; then
        sudo systemctl disable --now "$unit" || true
        sudo rm -f "$UNIT_DIR/$unit"
        echo "🗑️  해제: $unit"
    else
        echo "ℹ️  설치돼 있지 않음: $unit"
    fi
}

TARGET="${1:-all}"
case "$TARGET" in
    collector)
        uninstall_one collector
        ;;
    live)
        uninstall_one live
        ;;
    dashboard)
        uninstall_one dashboard
        ;;
    all)
        uninstall_one collector
        uninstall_one live
        uninstall_one dashboard
        ;;
    *)
        echo "사용법: $0 [collector|live|dashboard|all]" >&2
        exit 1
        ;;
esac

sudo systemctl daemon-reload
