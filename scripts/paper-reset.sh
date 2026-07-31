#!/usr/bin/env bash
# paper-reset.sh — 페이퍼 매매 장부를 초기화하고 깨끗한 상태에서 다시 시작한다.
#
# 왜 필요한가: 채택 엔진/config가 바뀌면(재-베이스라인) 옛 규칙으로 쌓인 페이퍼
# 거래기록이 새 규칙 성과와 섞여 의미가 없어진다. 이 스크립트가 "거래기록만" 비운다.
#
# ⚠️ 시세 데이터는 절대 건드리지 않는다 — ohlcv / funding_rate 테이블은 손대지 않고,
#    거래기록 4개 테이블(paper_trades · open_positions · live_limit_orders ·
#    live_runner_sessions)만 비운다. DB 파일 하나(기본 data/ohlcv.db)에 시세와
#    거래기록이 함께 들어 있으므로 파일을 통째로 지우면 안 된다.
#
# 사용법:
#   scripts/paper-reset.sh [옵션]
#
# 옵션:
#   --db PATH            DB 경로 (기본: $ALPHABLOCK_DB 또는 data/ohlcv.db)
#   --services "A B"     정지/재시작할 systemd 유닛 (기본: "alphablock-live alphablock-dashboard")
#   --include-backtest   백테스트 --persist 장부(backtest_* 5개 테이블)도 함께 비운다
#   --no-restart         초기화 후 서비스를 다시 켜지 않는다(수동 재시작)
#   --no-backup          DB 백업을 건너뛴다(권장하지 않음)
#   --dry-run            실제로 지우지 않고 무엇을 할지만 출력한다
#   --yes                확인 프롬프트 없이 진행한다
#   -h, --help           이 도움말
#
# 예:
#   scripts/paper-reset.sh --dry-run          # 먼저 뭐가 지워질지 확인
#   scripts/paper-reset.sh                     # 확인 프롬프트 후 초기화
#   sudo scripts/paper-reset.sh --yes          # 서버에서 무인 실행(systemctl 필요)

set -euo pipefail

DB="${ALPHABLOCK_DB:-data/ohlcv.db}"
SERVICES="alphablock-live alphablock-dashboard"
RESTART=1
BACKUP=1
DRY_RUN=0
ASSUME_YES=0
INCLUDE_BACKTEST=0

PAPER_TABLES=(paper_trades open_positions live_limit_orders live_runner_sessions)
BACKTEST_TABLES=(backtest_runs backtest_trades backtest_trade_exits backtest_setups backtest_equity)
# 절대 건드리면 안 되는 시세 테이블 — 안전 확인용
PROTECTED_TABLES=(ohlcv funding_rate)

log()  { printf '%s\n' "$*"; }
err()  { printf '❌ %s\n' "$*" >&2; }
run()  { if [[ "$DRY_RUN" -eq 1 ]]; then log "  [dry-run] $*"; else eval "$@"; fi; }

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db)               DB="$2"; shift 2 ;;
        --services)         SERVICES="$2"; shift 2 ;;
        --include-backtest) INCLUDE_BACKTEST=1; shift ;;
        --no-restart)       RESTART=0; shift ;;
        --no-backup)        BACKUP=0; shift ;;
        --dry-run)          DRY_RUN=1; shift ;;
        --yes|-y)           ASSUME_YES=1; shift ;;
        -h|--help)          usage 0 ;;
        *) err "알 수 없는 옵션: $1"; usage 1 ;;
    esac
done

# --- 사전 점검 ----------------------------------------------------------------
command -v sqlite3 >/dev/null || { err "sqlite3 를 찾을 수 없습니다."; exit 1; }
[[ -f "$DB" ]] || { err "DB 파일이 없습니다: $DB (--db 로 경로를 지정하세요)"; exit 1; }

HAVE_SYSTEMCTL=0
command -v systemctl >/dev/null && HAVE_SYSTEMCTL=1

count() { sqlite3 "$DB" "SELECT COUNT(*) FROM $1;" 2>/dev/null || echo "N/A"; }

TABLES=("${PAPER_TABLES[@]}")
[[ "$INCLUDE_BACKTEST" -eq 1 ]] && TABLES+=("${BACKTEST_TABLES[@]}")

log "═══════════════════════════════════════════════════════════════"
log " 페이퍼 거래기록 초기화"
log " DB: $DB"
log "═══════════════════════════════════════════════════════════════"
log ""
log "지울 테이블(현재 행 수):"
for t in "${TABLES[@]}"; do printf '  • %-22s %s행\n' "$t" "$(count "$t")"; done
log ""
log "보호(그대로 유지)되는 시세 테이블:"
for t in "${PROTECTED_TABLES[@]}"; do printf '  • %-22s %s행 (유지)\n' "$t" "$(count "$t")"; done
log ""

# --- 확인 ---------------------------------------------------------------------
if [[ "$ASSUME_YES" -ne 1 && "$DRY_RUN" -ne 1 ]]; then
    read -r -p "위 거래기록을 정말 비웁니까? (시세는 유지) [yes/N] " ans
    [[ "$ans" == "yes" ]] || { log "취소했습니다."; exit 0; }
fi

# --- 1) 러너 정지 -------------------------------------------------------------
if [[ "$HAVE_SYSTEMCTL" -eq 1 && -n "$SERVICES" ]]; then
    log ""
    log "▶ 서비스 정지: $SERVICES"
    run "sudo systemctl stop $SERVICES"
    # 정지 확인 — 하나라도 아직 active면 삭제 중단(방금 지운 자리에 새 행이 생김)
    if [[ "$DRY_RUN" -ne 1 ]]; then
        for svc in $SERVICES; do
            if systemctl is-active --quiet "$svc"; then
                err "$svc 가 아직 실행 중입니다. 삭제를 중단합니다."
                exit 1
            fi
        done
    fi
else
    log ""
    log "⚠ systemctl 이 없거나 서비스 미지정 — 러너를 수동으로 먼저 멈추세요."
    if [[ "$ASSUME_YES" -ne 1 && "$DRY_RUN" -ne 1 ]]; then
        read -r -p "러너가 멈춰 있습니까? [yes/N] " ans
        [[ "$ans" == "yes" ]] || { log "먼저 러너를 멈추고 다시 실행하세요."; exit 0; }
    fi
fi

# --- 2) 백업 -----------------------------------------------------------------
if [[ "$BACKUP" -eq 1 ]]; then
    BAK="${DB}.bak-$(date +%Y%m%d-%H%M%S)"
    log ""
    log "▶ 백업: $BAK"
    run "cp '$DB' '$BAK'"
fi

# --- 3) 거래기록만 삭제 (한 트랜잭션) ----------------------------------------
log ""
log "▶ 거래기록 삭제 + VACUUM"
DELETE_SQL="BEGIN;"
for t in "${TABLES[@]}"; do DELETE_SQL+=" DELETE FROM $t;"; done
DELETE_SQL+=" COMMIT;"
run "sqlite3 '$DB' \"$DELETE_SQL\""
run "sqlite3 '$DB' 'VACUUM;'"

# --- 4) 재시작 ---------------------------------------------------------------
if [[ "$RESTART" -eq 1 && "$HAVE_SYSTEMCTL" -eq 1 && -n "$SERVICES" ]]; then
    log ""
    log "▶ 서비스 재시작: $SERVICES"
    run "sudo systemctl start $SERVICES"
fi

log ""
log "완료 후 행 수:"
for t in "${TABLES[@]}"; do printf '  • %-22s %s행\n' "$t" "$(count "$t")"; done
log ""
if [[ "$DRY_RUN" -eq 1 ]]; then
    log "✅ (dry-run) 실제로는 아무것도 바뀌지 않았습니다."
else
    log "✅ 초기화 완료. 페이퍼 러너가 빈 장부에서 다시 시작합니다."
    # 주의: 이 `if` 를 `[[ ... ]] && log` 단축평가로 되돌리지 말 것 — 스크립트의 마지막
    # 문장이라 백업이 꺼져 있으면(`[[ 0 -eq 1 ]]` = false) set -e 아래에서 종료 코드가
    # 1이 되어 성공했는데도 실패로 보인다(회귀 테스트가 종료 코드로 고정한다).
    if [[ "$BACKUP" -eq 1 ]]; then
        log "   되돌리려면: systemctl stop $SERVICES && cp '${BAK:-백업파일}' '$DB'"
    fi
fi
