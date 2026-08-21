#!/usr/bin/env bash
#
# AlphaBlock 서버 재배포 — 코드 갱신 표준 절차 (WAN-185)
#
# "코드는 고쳤는데 화면은 옛것" 재발 방지. 서버가 main 을 깔끔히 추적하지 못하고
# 파일이 손으로 얹히거나 streamlit/러너 프로세스가 오래 떠 있으면, 디스크 소스는
# 새것인데 돌고 있는 프로세스(또는 __pycache__ 바이트코드)가 옛 모듈을 붙들어
# ImportError·옛 화면이 재발한다(PM 운영 메모 2026-07-25, WAN-190 사건). 브라우저
# 새로고침으로는 안 고쳐진다 — 프로세스를 실제로 재시작해야 새 코드가 뜬다.
#
# 그래서 배포를 항상 세 단계 한 세트로 묶는다:
#   1) git 동기화 (fetch + fast-forward pull)
#   2) 옛 바이트코드 캐시(__pycache__/*.pyc) 정리
#   3) systemd 서비스 재시작 + 상태 확인
#
# 사용 (서버 저장소 루트, 예 /home/rocky/AlphaBlock):
#   ./scripts/deploy.sh                       # 셋 다(collector + live + dashboard)
#   ./scripts/deploy.sh dashboard             # 대시보드만
#   ./scripts/deploy.sh collector live        # 수집기 + 러너만
#   ./scripts/deploy.sh --no-pull dashboard   # git 동기화 없이 캐시 정리 + 재시작만
#   ./scripts/deploy.sh --dry-run             # 실행할 명령만 출력(리눅스 밖에서도 미리보기)
#
# 안전: 페이퍼 전용이다. .env 를 건드리지 않으므로 ALPHABLOCK_LIVE_TRADING(기본 false)은
# 이 스크립트로 바뀌지 않는다 — 실주문을 유발하지 않는다. DB 도 손대지 않는다(수집기가
# 잠깐 멈췄다 재시작될 뿐이며, WAL 정합성 있는 이전은 docs/ops/server-migration.md §3).
#
# ⚠️ 타이머로 도는 유닛(doctor 두 쌍 · watch)은 여기서 재시작하지 않는다 — oneshot 이라
# 다음 주기에 새 코드로 그냥 뜬다. 단 scripts/systemd/*.template 이 바뀐 배포는 코드만으로
# 반영되지 않으니 install-systemd.sh 를 다시 돌릴 것(WAN-318 §7, WAN-344).
#
# 최초 설치는 scripts/install-systemd.sh, 서버 준비는 scripts/setup-server.sh,
# 전체 절차는 docs/ops/server-migration.md 참고.
set -euo pipefail

VALID_LABELS=(collector live dashboard)
DRY_RUN=0
DO_PULL=1
declare -a SERVICES=()

usage() {
    sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

is_valid_label() {
    local want="$1" label
    for label in "${VALID_LABELS[@]}"; do
        [[ "$label" == "$want" ]] && return 0
    done
    return 1
}

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --no-pull) DO_PULL=0 ;;
        -h | --help) usage 0 ;;
        -*)
            echo "❌ 알 수 없는 옵션: $arg" >&2
            usage 1 >&2
            ;;
        *)
            if is_valid_label "$arg"; then
                SERVICES+=("$arg")
            else
                echo "❌ 알 수 없는 서비스: $arg (collector|live|dashboard 중 하나)" >&2
                exit 1
            fi
            ;;
    esac
done

# 서비스 미지정이면 셋 다.
if [[ ${#SERVICES[@]} -eq 0 ]]; then
    SERVICES=("${VALID_LABELS[@]}")
fi

# --dry-run 이 아니면 리눅스 + systemd 가 필수(실제 재시작 대상). 미리보기는 어디서나 된다.
if [[ "$DRY_RUN" -eq 0 ]]; then
    if [[ "$(uname -s)" != "Linux" ]]; then
        echo "❌ 이 스크립트는 리눅스 서버 전용입니다(미리보기는 --dry-run)." >&2
        exit 1
    fi
    if ! command -v systemctl >/dev/null; then
        echo "❌ systemctl 을 찾을 수 없습니다(systemd 미탑재 배포판)." >&2
        exit 1
    fi
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# 실행 헬퍼: --dry-run 이면 명령만 출력하고 넘어간다.
run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf 'DRY  %s\n' "$*"
    else
        "$@"
    fi
}

echo "▶ AlphaBlock 재배포 — $REPO_DIR"
echo "  대상 서비스: ${SERVICES[*]}"
[[ "$DRY_RUN" -eq 1 ]] && echo "  (미리보기 — 실제로 실행하지 않습니다)"

# --- 0. working tree 정합성 -------------------------------------------------
# 추적 중인 파일에 손으로 얹힌 변경(수정·스테이지)이 있으면 fast-forward pull 이
# 깨지거나 조용히 덮인다 → 멈추고 알린다. 반면 untracked 파일(백업 CSV 등)은
# fast-forward 를 막지 않으므로 정보로만 알리고 계속 진행한다.
tracked_changes="$(git status --porcelain --untracked-files=no)"
if [[ -n "$tracked_changes" ]]; then
    echo "⚠️  추적 중인 파일에 커밋 안 된 변경이 있습니다:" >&2
    echo "$tracked_changes" >&2
    echo "    먼저 정리하세요: git stash  또는  git checkout -- <파일>" >&2
    if [[ "$DRY_RUN" -eq 0 ]]; then
        exit 1
    fi
    echo "    (--dry-run 이라 계속 진행합니다)" >&2
fi
untracked="$(git ls-files --others --exclude-standard)"
if [[ -n "$untracked" ]]; then
    echo "ℹ️  추적 안 되는 파일이 있습니다(진행에 지장 없음 — 정보용):"
    echo "$untracked" | sed 's/^/     /'
fi

# --- 1. git 동기화 ----------------------------------------------------------
if [[ "$DO_PULL" -eq 1 ]]; then
    echo "── 1) git 동기화 (fetch + fast-forward)"
    before="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    run git fetch --prune
    # --ff-only: 서버에서 예기치 않은 머지 커밋이 생기지 않게 한다(어긋나면 멈춘다).
    run git pull --ff-only
    if [[ "$DRY_RUN" -eq 0 ]]; then
        after="$(git rev-parse --short HEAD)"
        if [[ "$before" == "$after" ]]; then
            echo "   이미 최신입니다 ($after)."
        else
            echo "   $before → $after"
        fi
    fi
else
    echo "── 1) git 동기화 건너뜀 (--no-pull)"
fi

# --- 2. 바이트코드 캐시 정리 ------------------------------------------------
echo "── 2) __pycache__ / *.pyc 정리"
run find "$REPO_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +

# --- 3. systemd 서비스 재시작 ----------------------------------------------
echo "── 3) systemd 서비스 재시작"
declare -a RESTARTED=()
for label in "${SERVICES[@]}"; do
    unit="alphablock-${label}.service"
    if [[ "$DRY_RUN" -eq 0 ]] && ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then
        echo "   ⚠️  $unit 미설치 — 건너뜀 (먼저 install-systemd.sh $label)" >&2
        continue
    fi
    run sudo systemctl restart "$unit"
    RESTARTED+=("$unit")
done

if [[ ${#RESTARTED[@]} -gt 0 ]]; then
    run systemctl status "${RESTARTED[@]}" --no-pager
fi

echo "✅ 재배포 완료. (페이퍼 전용 — ALPHABLOCK_LIVE_TRADING 불변)"
