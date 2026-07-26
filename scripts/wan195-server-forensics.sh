#!/usr/bin/env bash
#
# WAN-195 §1·§2·§5 — 서버 포렌식 증거 수집 (읽기 전용)
#
# WAN-194/195의 잔여 작업은 **서버(rocky@orderblock)에서 사람이 실행**해야 한다
# (개발 세션에는 SSH 접근이 없다). 이 스크립트가 그 실행을 한 줄로 만든다.
#
# 사용:
#   ./scripts/wan195-server-forensics.sh                # 결과를 stdout + 파일로
#   ./scripts/wan195-server-forensics.sh -o report.md   # 파일 지정
#
# 🚨 **읽기 전용이다** — DB를 열더라도 `mode=ro`이고, 삭제·VACUUM·복구를 하지 않는다.
#    파괴적 조치(lost_and_found 드롭 + VACUUM)는 사용자 결정이고 러너 정지가 선행이라
#    이 스크립트가 대신하지 않는다(런북은 docs/decisions/wan195.md §5).
#
# ⚠️ `PRAGMA quick_check`는 3.8GB DB에서 수십 초 걸린다 — `SKIP_QUICK_CHECK=1`로 끈다.

set -uo pipefail

OUT=""
while getopts "o:h" opt; do
  case "$opt" in
    o) OUT="$OPTARG" ;;
    h) sed -n '2,20p' "$0"; exit 0 ;;
    *) exit 2 ;;
  esac
done

DB="${DB:-data/ohlcv.db}"
SKIP_QUICK_CHECK="${SKIP_QUICK_CHECK:-0}"

emit() { if [ -n "$OUT" ]; then printf '%s\n' "$*" >>"$OUT"; else printf '%s\n' "$*"; fi; }
section() { emit ""; emit "## $*"; emit ""; }
run() {
  # 명령 하나를 코드블록에 담아 출력한다. 실패해도 계속한다(증거 수집이 목적).
  emit '```'
  emit "\$ $*"
  eval "$@" 2>&1 | sed 's/^/  /' | while IFS= read -r line; do emit "$line"; done
  emit '```'
}

# 서버는 Rocky(GNU coreutils)라 GNU 플래그가 정답이지만, 개발자가 macOS에서 스크립트를
# 확인할 때 조용히 깨지면 "돌려봤다"가 거짓이 된다 — 둘 다 되게 두고 자동으로 고른다.
if ls --time-style=full-iso / >/dev/null 2>&1; then
  ls_long() { ls -la --time-style=full-iso "$@"; }
else
  ls_long() { ls -laT "$@"; }
fi
if stat -c '%n' / >/dev/null 2>&1; then
  stat_meta() { stat -c '%n birth=%w mtime=%y size=%s' "$@"; }
else
  stat_meta() { stat -f '%N birth=%SB mtime=%Sm size=%z' -t '%Y-%m-%d %H:%M:%S' "$@"; }
fi

[ -n "$OUT" ] && : >"$OUT"

emit "# WAN-195 서버 포렌식 — $(date '+%Y-%m-%d %H:%M:%S %Z')"
emit ""
emit "호스트: \`$(hostname)\` · 사용자: \`$(whoami)\` · CWD: \`$(pwd)\`"
emit ""
emit "> 읽기 전용 수집이다. 이 파일을 그대로 WAN-195 코멘트에 붙이면 된다."

# ── §1 손상·복구 타임라인 ────────────────────────────────────────────────
section "§1 손상·복구 타임라인"

emit "### DB 파일과 백업 유무"
run "ls_long ${DB%/*}/ | grep -E 'ohlcv|fuse_hidden' || true"

emit "### 파일 생성/변경 시각 (stat)"
for f in "$DB" "$DB.corrupt.bak" "$DB.old" "$DB-wal" "$DB-shm"; do
  [ -e "$f" ] && run "stat_meta '$f'"
done

emit "### 복구 흔적 — 셸 히스토리의 recover/dump/reindex"
run "grep -nE '\\.recover|\\.dump|reindex|integrity_check|sqlite3' ~/.bash_history 2>/dev/null | tail -40 || echo '(히스토리 없음/비활성)'"

emit "### journald의 손상 신호"
run "journalctl --since '2026-07-20' --no-pager 2>/dev/null | grep -iE 'malformed|corrupt|disk image|database is locked|SIGKILL|out of memory|oom' | tail -40 || echo '(없음/권한 없음)'"

emit "### 러너·수집기 서비스 재시작 이력"
run "journalctl -u alphablock-runner -u alphablock-collector --since '2026-07-20' --no-pager 2>/dev/null | grep -iE 'Started|Stopped|Failed|Killed|Main process' | tail -40 || echo '(유닛 없음/권한 없음)'"

emit "### ⚠️ 로컬 data/가 서버 마운트인지 (WAN-195 §1의 명시 질문)"
run "mount | grep -iE 'fuse|sshfs|nfs|cifs' || echo '(FUSE/네트워크 마운트 없음)'"
run "df -h '$(dirname "$DB")'"

# ── §2 무결성 + 장부 ─────────────────────────────────────────────────────
section "§2 무결성 · 장부 · LINK 건 분류"

if [ "$SKIP_QUICK_CHECK" = "1" ]; then
  emit "\`quick_check\` 생략(\`SKIP_QUICK_CHECK=1\`) — **손상 없음이 아니라 미확인**이다."
  run "uv run alphablock doctor --skip-quick-check --orphans-since 2026-07-26"
else
  emit "\`quick_check\` 포함(3.8GB에서 수십 초). 끄려면 \`SKIP_QUICK_CHECK=1\`."
  run "uv run alphablock doctor --orphans-since 2026-07-26"
fi

emit "### LINK 15m 체결의 처분(WAN-194 §3 열)"
emit ""
emit "\`entry_status\`가 \`rejected\`면 **정상 거부**(손절폭 가드 등), \`NULL\`이면 **판별 불가**"
emit "(WAN-194 이전 기록) 또는 **진짜 유실**(두 쓰기 사이에서 러너가 죽음)이다."
run "sqlite3 -header -column 'file:$DB?mode=ro' \"SELECT symbol,timeframe,status,entry_status,entry_reject_reason,datetime(fill_ms/1000+32400,'unixepoch') AS 체결_KST FROM live_limit_orders WHERE symbol LIKE '%LINK%' ORDER BY placed_ms DESC LIMIT 10;\" 2>&1 || echo '(열 없음 = 미배포)'"

emit "### 장부 인구조사"
run "sqlite3 'file:$DB?mode=ro' \"SELECT 'open_positions',COUNT(*) FROM open_positions UNION ALL SELECT 'paper_trades',COUNT(*) FROM paper_trades UNION ALL SELECT 'live_limit_orders',COUNT(*) FROM live_limit_orders;\" 2>&1"

# ── §5 손상 벡터 ─────────────────────────────────────────────────────────
section "§5 근본 원인 후보"

emit "### 디스크 여유 (꽉 참 = SQLite 손상 대표 벡터)"
run "df -h"

emit "### WAL 크기·체크포인트 상태"
run "ls -la '$DB-wal' '$DB-shm' 2>/dev/null || echo '(WAL 없음)'"
run "sqlite3 'file:$DB?mode=ro' 'PRAGMA journal_mode; PRAGMA wal_checkpoint;' 2>&1"

emit "### 비정상 종료 흔적"
run "last -x 2>/dev/null | head -20 || echo '(last 없음)'"
run "dmesg 2>/dev/null | grep -iE 'oom|killed process|I/O error|EXT4-fs error|remount' | tail -20 || echo '(dmesg 권한 없음)'"

emit "### 백업 회전 유무"
run "ls -la ${DB%/*}/*.bak ${DB%/*}/*.old 2>/dev/null || echo '(서버에 .bak/.old 없음)'"
run "crontab -l 2>/dev/null | grep -iE 'sqlite|backup|doctor|alphablock' || echo '(관련 cron 없음)'"

emit ""
emit "---"
emit ""
emit "수집 끝. 파괴적 조치(드롭·VACUUM)는 \`docs/decisions/wan195.md\` §5 런북을 따를 것."
emit "🚨 \`lost_and_found\`를 그냥 드롭하지 말 것 — 유일본 캔들이 들어 있으면 \`doctor\`가 거부한다."

[ -n "$OUT" ] && echo "작성: $OUT"
exit 0
