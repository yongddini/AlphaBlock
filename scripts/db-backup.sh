#!/usr/bin/env bash
# db-backup.sh — SQLite DB를 **검증된 백업**으로만 남긴다 (WAN-318 §4).
#
# 왜 필요한가(2026-08-17 실사고): `sqlite3 .backup`이 doctor가 DB를 붙잡은 상태에서 돌다
# 중간에 끊겼는데, **잘린 1.5GB 파일이 4.0GB 백업과 똑같은 이름으로 남았다**. 나중에 그걸
# 복구본으로 쓰면 DB의 3분의 2가 조용히 사라진다 — 「실패가 성공과 같은 모양」(WAN-194
# 계열)이다. 이 스크립트는 그 모양을 없앤다:
#
#   1) 백업을 **임시 이름**(`<out>.inprogress`)으로 받는다 — 최종 이름은 검증 후에만 붙는다.
#   2) 받은 뒤 **검증**한다: sqlite3 종료 코드 + 헤더가 말하는 크기(page_count × page_size)와
#      실제 파일 크기 일치 + 스키마를 읽을 수 있는지. 잘린 파일은 여기서 걸린다.
#   3) 실패하면 산출물을 `<out>.FAILED`로 이름 붙여 남기고 **종료 코드 1**을 낸다.
#      최종 이름(`<out>`)은 **절대** 생기지 않는다.
#
# ⚠️ 백업 전에 러너·수집기·doctor를 멈추는 것이 원칙이다(경합하면 `.backup`이 계속 재시도
#    하거나 끊긴다 — 위 사고가 그 경우다). 이 스크립트는 systemd가 있으면 그 유닛들이 도는지
#    보고 **거부**한다(`--allow-running`으로 무시 가능).
#
# 사용법:
#   scripts/db-backup.sh [옵션]
#
# 옵션:
#   --db PATH            원본 DB (기본: $ALPHABLOCK_DB 또는 data/ohlcv.db)
#   --out PATH           백업 경로 (기본: <db>.bak-YYYYmmdd-HHMMSS)
#   --services "A B"     정지 여부를 확인할 systemd 유닛
#                        (기본: alphablock-live alphablock-collector alphablock-doctor
#                                alphablock-doctor-light)
#   --allow-running      위 유닛이 돌고 있어도 강행한다(권장하지 않음)
#   --quick-check        백업본에 `PRAGMA quick_check`까지 돌린다(수 GB면 수 분~수십 분)
#   --verify-only PATH   백업을 새로 뜨지 않고 기존 파일만 검증한다(서버의 옛 백업 점검용)
#   --dry-run            무엇을 할지만 출력한다
#   -h, --help           이 도움말
#
# 예:
#   scripts/db-backup.sh --dry-run
#   sudo systemctl stop alphablock-live alphablock-collector && scripts/db-backup.sh
#   scripts/db-backup.sh --verify-only data/ohlcv.db.bak-20260817-1027   # 잘렸는지 확인
set -euo pipefail

DB="${ALPHABLOCK_DB:-data/ohlcv.db}"
OUT=""
SERVICES="alphablock-live alphablock-collector alphablock-doctor alphablock-doctor-light"
ALLOW_RUNNING=0
QUICK_CHECK=0
VERIFY_ONLY=""
DRY_RUN=0

log() { printf '%s\n' "$*"; }
err() { printf '❌ %s\n' "$*" >&2; }

usage() { sed -n '2,37p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db)            DB="$2"; shift 2 ;;
        --out)           OUT="$2"; shift 2 ;;
        --services)      SERVICES="$2"; shift 2 ;;
        --allow-running) ALLOW_RUNNING=1; shift ;;
        --quick-check)   QUICK_CHECK=1; shift ;;
        --verify-only)   VERIFY_ONLY="$2"; shift 2 ;;
        --dry-run)       DRY_RUN=1; shift ;;
        -h|--help)       usage 0 ;;
        *) err "알 수 없는 옵션: $1"; usage 1 ;;
    esac
done

command -v sqlite3 >/dev/null || { err "sqlite3 를 찾을 수 없습니다."; exit 1; }

# 파일 크기(바이트) — GNU/BSD stat 차이를 흡수한다.
file_size() { wc -c < "$1" | tr -d ' '; }

# 백업 파일 한 개를 검증한다. 성공 0 / 실패 1.
#
# 잘린 백업을 잡는 핵심은 **헤더가 말하는 크기와 실제 크기의 대조**다: `.backup`이 중간에
# 끊겨도 첫 페이지(헤더)에는 원본의 page_count 가 적혀 있으므로 둘이 어긋난다. 파일을
# 열어 스키마를 읽는 것까지 해야 "열리기는 하는가"도 함께 확인된다.
verify_backup() {
    local path="$1"

    if [[ ! -f "$path" ]]; then
        err "검증 실패 — 파일이 없습니다: $path"
        return 1
    fi

    local actual
    actual="$(file_size "$path")"
    if [[ "$actual" -eq 0 ]]; then
        err "검증 실패 — 0바이트 파일: $path"
        return 1
    fi

    local page_count page_size expected
    page_count="$(sqlite3 "$path" 'PRAGMA page_count;' 2>/dev/null || echo "")"
    page_size="$(sqlite3 "$path" 'PRAGMA page_size;' 2>/dev/null || echo "")"
    if [[ -z "$page_count" || -z "$page_size" ]]; then
        err "검증 실패 — SQLite 파일로 열리지 않습니다: $path"
        return 1
    fi
    expected=$(( page_count * page_size ))
    if [[ "$actual" -lt "$expected" ]]; then
        err "검증 실패 — 잘린 파일: $path (헤더 ${expected}B / 실제 ${actual}B)"
        return 1
    fi

    if ! sqlite3 "$path" 'SELECT count(*) FROM sqlite_master;' >/dev/null 2>&1; then
        err "검증 실패 — 스키마를 읽을 수 없습니다: $path"
        return 1
    fi

    # 저널이 남아 있으면 `.backup`이 중간에 끊긴 흔적이다(사고 당시 -journal 1.0K).
    if [[ -f "${path}-journal" ]]; then
        err "검증 실패 — 저널이 남아 있습니다(중단 흔적): ${path}-journal"
        return 1
    fi

    if [[ "$QUICK_CHECK" -eq 1 ]]; then
        local result
        result="$(sqlite3 "$path" 'PRAGMA quick_check;' 2>/dev/null || echo "")"
        if [[ "$result" != "ok" ]]; then
            err "검증 실패 — PRAGMA quick_check: ${result:-(실행 불가)}"
            return 1
        fi
        log "  • quick_check = ok"
    fi

    log "  • 크기 ${actual}B = 헤더(${page_count}페이지 × ${page_size}B) · 스키마 읽힘"
    return 0
}

# --- 검증 전용 모드 -----------------------------------------------------------
if [[ -n "$VERIFY_ONLY" ]]; then
    log "▶ 백업 검증: $VERIFY_ONLY"
    if verify_backup "$VERIFY_ONLY"; then
        log "✅ 정상 백업입니다."
        exit 0
    fi
    log "🚨 이 파일을 복구본으로 쓰지 마세요."
    exit 1
fi

# --- 사전 점검 ----------------------------------------------------------------
[[ -f "$DB" ]] || { err "DB 파일이 없습니다: $DB (--db 로 경로를 지정하세요)"; exit 1; }
[[ -n "$OUT" ]] || OUT="${DB}.bak-$(date +%Y%m%d-%H%M%S)"

if [[ "$ALLOW_RUNNING" -ne 1 ]] && command -v systemctl >/dev/null && [[ -n "$SERVICES" ]]; then
    # 배열이 아니라 문자열로 모은다 — bash 3.2(맥 기본)는 `set -u` 아래에서 빈 배열의
    # `${#arr[@]}` 를 unbound 로 보고 죽는다.
    running=""
    for svc in $SERVICES; do
        state="$(systemctl is-active "$svc" 2>/dev/null || true)"
        case "$state" in
            active|activating|reloading|deactivating) running="$running $svc($state)" ;;
        esac
    done
    if [[ -n "$running" ]]; then
        err "다음 유닛이 아직 돌고 있습니다:${running}"
        err "백업 전에 멈추세요:  sudo systemctl stop ${SERVICES}"
        err "(경합 상태의 .backup 은 중간에 끊겨 잘린 백업을 남긴 전례가 있다 — WAN-318 §4)"
        err "그래도 강행하려면 --allow-running"
        exit 1
    fi
fi

TMP="${OUT}.inprogress"
SRC_BYTES="$(file_size "$DB")"

log "═══════════════════════════════════════════════════════════════"
log " SQLite 백업 (검증 후에만 최종 이름을 붙인다)"
log " 원본: $DB (${SRC_BYTES}B)"
log " 대상: $OUT"
log "═══════════════════════════════════════════════════════════════"

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "  [dry-run] sqlite3 '$DB' \".backup '$TMP'\""
    log "  [dry-run] 검증(헤더 크기 대조 · 스키마 읽기) 후 '$TMP' → '$OUT'"
    log "✅ (dry-run) 실제로는 아무것도 만들지 않았습니다."
    exit 0
fi

# 이전 실행이 남긴 임시 산출물은 여기서 치운다(최종 이름은 절대 안 건드린다).
rm -f "$TMP" "${TMP}-journal" "${TMP}-wal"

log ""
log "▶ 백업 중…"
backup_rc=0
sqlite3 "$DB" ".backup '$TMP'" || backup_rc=$?

fail() {
    local reason="$1"
    if [[ -f "$TMP" ]]; then
        mv -f "$TMP" "${OUT}.FAILED"
        # 주의: `[[ ... ]] && mv` 단축평가로 되돌리지 말 것 — 저널이 없으면 이 문장이 1을
        # 돌려주고 `set -e` 아래에서 아래 안내가 출력되기 전에 죽는다(paper-reset.sh 선례).
        if [[ -f "${TMP}-journal" ]]; then
            mv -f "${TMP}-journal" "${OUT}.FAILED-journal"
        fi
        err "$reason — 산출물을 '${OUT}.FAILED' 로 남깁니다(정상 백업으로 오인하지 않게)."
    else
        err "$reason — 산출물이 없습니다."
    fi
    err "최종 이름 '${OUT}' 은 만들지 않았습니다."
    exit 1
}

[[ "$backup_rc" -eq 0 ]] || fail "sqlite3 .backup 실패(종료 코드 $backup_rc)"

log "▶ 검증 중…"
verify_backup "$TMP" || fail "백업 검증 실패"

mv -f "$TMP" "$OUT"
log ""
log "✅ 백업 완료(검증됨): $OUT ($(file_size "$OUT")B)"
log "   되돌리려면 러너를 멈춘 뒤:  cp '$OUT' '$DB'"
