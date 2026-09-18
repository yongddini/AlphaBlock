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
# 기본 동작(WAN-399 = 사용자 결정 2026-09-07·09-18): **장부 CSV 내보내기 → DELETE** 까지만 한다.
#   • 지우기 전에 거래기록 4개 테이블을 CSV로 떨군다(시각이 붙은 새 디렉터리 — 덮어쓰지
#     않는다). 내보낸 행 수를 다시 읽어 검증하고, **실패하면 삭제하지 않는다**.
#     `live_limit_orders`는 파리티 측정(WAN-409 `alphablock cascade`)의 유일한 원자료다.
#   • DB 전체 백업·VACUUM 은 **기본에서 뺐다** — 둘 다 DB 파일(수 GB)을 통째로 다시 쓰는데
#     지우는 것은 장부 수백 행뿐이다. 필요하면 --backup / --vacuum 으로 명시한다.
#   • 러너 정지 확인은 그대로다 — 이 스크립트의 진짜 안전장치다.
#
# 사용법:
#   scripts/paper-reset.sh [옵션]
#
# 옵션:
#   --db PATH            DB 경로 (기본: $ALPHABLOCK_DB 또는 data/ohlcv.db)
#   --services "A B"     정지/재시작할 systemd 유닛 (기본: "alphablock-live alphablock-dashboard")
#   --export-dir DIR     장부 CSV를 떨굴 상위 디렉터리 (기본: <DB 디렉터리>/paper-reset-exports)
#                        실행마다 그 아래 YYYYmmdd-HHMMSS/ 를 새로 만든다
#   --include-backtest   백테스트 --persist 장부(backtest_* 5개 테이블)도 함께 비운다
#                        (그 테이블은 CSV로 내보내지 않는다 — 코드로 다시 적재할 수 있다)
#   --backup             DB 전체를 db-backup.sh(검증하는 백업)로 먼저 뜬다(옵트인)
#   --vacuum             삭제 뒤 VACUUM 으로 파일을 다시 쓴다(옵트인 · 수 GB면 오래 걸린다)
#   --no-backup          (호환용 · 무동작) 백업은 이제 기본으로 꺼져 있다
#   --no-restart         초기화 후 서비스를 다시 켜지 않는다(수동 재시작)
#   --dry-run            실제로 지우지 않고 무엇을 할지만 출력한다
#   --yes                확인 프롬프트 없이 진행한다
#   -h, --help           이 도움말
#
# 예:
#   scripts/paper-reset.sh --dry-run          # 먼저 뭐가 지워지고 어디에 CSV가 떨어질지 확인
#   scripts/paper-reset.sh                     # 확인 프롬프트 후 초기화(CSV + DELETE)
#   sudo scripts/paper-reset.sh --yes          # 서버에서 무인 실행(systemctl 필요)

set -euo pipefail

DB="${ALPHABLOCK_DB:-data/ohlcv.db}"
SERVICES="alphablock-live alphablock-dashboard"
RESTART=1
BACKUP=0
VACUUM=0
EXPORT_PARENT=""
NO_BACKUP_NOTICE=0
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

# 머리말(2번째 줄부터 첫 비-주석 줄 직전까지)만 출력한다 — 줄 번호를 고정하면 옵션을 더하거나
# 뺄 때마다 코드가 새거나 도움말이 잘린다(WAN-399 PM 확인: 옛 `2,40p`가 코드 10줄을 뱉었다).
usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db)               DB="$2"; shift 2 ;;
        --services)         SERVICES="$2"; shift 2 ;;
        --export-dir)       EXPORT_PARENT="$2"; shift 2 ;;
        --include-backtest) INCLUDE_BACKTEST=1; shift ;;
        --no-restart)       RESTART=0; shift ;;
        --backup)           BACKUP=1; shift ;;
        --vacuum)           VACUUM=1; shift ;;
        --no-backup)        NO_BACKUP_NOTICE=1; shift ;;
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
if [[ "$INCLUDE_BACKTEST" -eq 1 ]]; then TABLES+=("${BACKTEST_TABLES[@]}"); fi

# 내보내기 대상 = 페이퍼 장부 4개(백테스트 장부는 코드로 다시 적재할 수 있고 클 수 있어 뺀다 ·
# 시세 테이블은 애초에 안 지우므로 절대 내보내지 않는다 — 수 GB를 CSV로 뱉는 사고 방지).
EXPORT_TABLES=("${PAPER_TABLES[@]}")
[[ -n "$EXPORT_PARENT" ]] || EXPORT_PARENT="$(dirname "$DB")/paper-reset-exports"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXPORT_DIR="$EXPORT_PARENT/$STAMP"

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
log "지우기 전에 CSV로 내보낼 장부 → $EXPORT_DIR/"
for t in "${EXPORT_TABLES[@]}"; do printf '  • %s.csv\n' "$t"; done
log ""
log "DB 전체 백업: $( [[ "$BACKUP" -eq 1 ]] && echo '예 (--backup)' || echo '안 함 (기본 · --backup 으로 켬)')"
log "VACUUM:       $( [[ "$VACUUM" -eq 1 ]] && echo '예 (--vacuum)' || echo '안 함 (기본 · --vacuum 으로 켬)')"
if [[ "$NO_BACKUP_NOTICE" -eq 1 ]]; then
    log "ℹ --no-backup 은 이제 기본입니다(WAN-399) — 주지 않아도 백업을 뜨지 않습니다."
fi
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

# --- 2) 장부 CSV 내보내기 (삭제보다 먼저 · 실패하면 삭제하지 않는다) -----------
# WAN-399(사용자 결정 2026-09-18): 리셋이 장부를 통째로 잃지 않게 한다 — WAN-409 §3이
# `live_limit_orders`를 이 스크립트에 잃어 「판정 불가」로 닫혔다. 옛 규칙 장부는 새 규칙
# 장부와 섞이면 못 쓰므로 DB에서는 비우되, 기록은 파일로 남긴다(WAN-392 §5 손작업의 자동화).
# 검증: 내보낸 CSV를 메모리 DB로 다시 읽어 행 수가 원본과 같은지 본다(따옴표 안 줄바꿈이
# 있어도 줄 수가 아니라 CSV 레코드 수로 잰다). ⚠️ CSV는 NULL 과 빈 문자열을 가르지 못한다
# (둘 다 빈 칸) — 측정 원자료로는 충분하지만 DB로 비트 복원하는 수단은 아니다(그건 --backup).
export_table() {
    local t="$1" out="$EXPORT_DIR/$1.csv" src got
    src="$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t;")" || return 1
    # 헤더는 항상 쓴다(0행 테이블도 열 이름이 남게) — `-header`는 0행이면 헤더조차 안 쓴다.
    sqlite3 "$DB" "SELECT group_concat(name, ',') FROM pragma_table_info('$t');" > "$out" || return 1
    [[ -s "$out" ]] || return 1
    sqlite3 -csv "$DB" "SELECT * FROM $t;" >> "$out" || return 1
    # 명령은 stdin 으로 넘긴다 — 인자 여러 개를 차례로 실행하는 CLI 동작은 옛 sqlite3
    # (서버 배포판)에 없을 수 있다.
    got="$(printf '.mode csv\n.import "%s" x\nSELECT COUNT(*) FROM x;\n' "$out" | sqlite3 :memory:)" || return 1
    if [[ "$got" != "$src" ]]; then
        err "$t: 원본 ${src}행인데 CSV에서 ${got}행을 읽었습니다."
        return 1
    fi
    printf '  • %-22s %s행 → %s\n' "$t" "$src" "$out"
}

log ""
log "▶ 장부 CSV 내보내기: $EXPORT_DIR/"
if [[ "$DRY_RUN" -eq 1 ]]; then
    for t in "${EXPORT_TABLES[@]}"; do log "  [dry-run] $t → $EXPORT_DIR/$t.csv"; done
else
    if ! mkdir -p "$EXPORT_PARENT"; then
        err "내보내기 상위 디렉터리를 만들 수 없습니다: $EXPORT_PARENT — 삭제를 중단합니다."
        exit 1
    fi
    # `-p` 없이 만든다 — 같은 초에 두 번 돌아 이미 있으면 덮어쓰지 않고 멈춘다.
    if ! mkdir "$EXPORT_DIR"; then
        err "내보내기 디렉터리를 새로 만들 수 없습니다: $EXPORT_DIR — 삭제를 중단합니다."
        exit 1
    fi
    for t in "${EXPORT_TABLES[@]}"; do
        if ! export_table "$t"; then
            err "$t CSV 내보내기에 실패했습니다 — 기록 없이 장부만 사라지지 않게 삭제를 중단합니다."
            err "(부분 산출물은 $EXPORT_DIR 에 그대로 남깁니다)"
            exit 1
        fi
    done
fi

# --- 3) 백업 (옵트인) --------------------------------------------------------
# WAN-318 §4: `cp` 대신 검증하는 백업 스크립트를 쓴다 — 잘린 산출물이 정상 백업과 같은
# 이름으로 남지 않게(2026-08-17 사고: 1.5GB 잘린 파일이 4.0GB 백업 이름을 달고 있었다).
# 러너는 위에서 이미 멈췄으므로 --allow-running 으로 유닛 확인을 건너뛴다(수집기·doctor 는
# 이 스크립트의 관심 밖이고, 멈춰야 하는 것은 장부를 쓰는 쪽이다).
# WAN-399: 기본에서 뺐다 — 재-베이스라인 리셋은 장부를 일부러 버리는 일이라 수 GB 시세
# 사본으로 지킬 것이 없다(남길 기록은 위 CSV가 남긴다).
if [[ "$BACKUP" -eq 1 ]]; then
    BAK="${DB}.bak-${STAMP}"
    log ""
    log "▶ 백업: $BAK"
    BACKUP_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/db-backup.sh"
    run "'$BACKUP_SH' --db '$DB' --out '$BAK' --allow-running"
fi

# --- 4) 거래기록만 삭제 (한 트랜잭션) ----------------------------------------
log ""
if [[ "$VACUUM" -eq 1 ]]; then
    log "▶ 거래기록 삭제 + VACUUM"
else
    log "▶ 거래기록 삭제"
fi
DELETE_SQL="BEGIN;"
for t in "${TABLES[@]}"; do DELETE_SQL+=" DELETE FROM $t;"; done
DELETE_SQL+=" COMMIT;"
run "sqlite3 '$DB' \"$DELETE_SQL\""
# WAN-399: VACUUM 은 DB 파일을 통째로 다시 쓴다(수 GB) — 지운 장부 수백 행으로는 회수할
# 공간이 사실상 없어 기본에서 뺐다. 자동으로 켜지 않는다(WAN-194 원칙).
if [[ "$VACUUM" -eq 1 ]]; then
    run "sqlite3 '$DB' 'VACUUM;'"
elif [[ "$INCLUDE_BACKTEST" -eq 1 ]]; then
    log "ℹ 백테스트 장부까지 지웠습니다. 파일 크기를 실제로 줄이려면 --vacuum 을 함께 주십시오."
fi

# --- 5) 재시작 ---------------------------------------------------------------
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
    log "   지운 장부 기록: $EXPORT_DIR/"
    # 주의: 이 `if` 를 `[[ ... ]] && log` 단축평가로 되돌리지 말 것 — 스크립트의 마지막
    # 문장이라 백업이 꺼져 있으면(`[[ 0 -eq 1 ]]` = false) set -e 아래에서 종료 코드가
    # 1이 되어 성공했는데도 실패로 보인다. WAN-399 이후 백업은 **기본으로 꺼져** 있어
    # 되돌리면 기본 실행이 **매번** 실패로 보인다(회귀 테스트가 종료 코드 0으로 고정한다).
    if [[ "$BACKUP" -eq 1 ]]; then
        log "   되돌리려면: systemctl stop $SERVICES && cp '${BAK:-백업파일}' '$DB'"
    fi
fi
