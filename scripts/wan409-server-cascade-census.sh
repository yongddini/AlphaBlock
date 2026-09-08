#!/usr/bin/env bash
#
# WAN-409 §3 — 라이브 「같은 존 연속 체결」 인구조사를 서버에서 돌린다.
#
# 로컬 개발 세션에는 서버 SSH 접근이 없고(WAN-195/314/337/342/353과 같은 제약) 판정에 필요한
# 데이터가 로컬에 아예 없다 — `live_limit_orders` **0행** · `paper_trades` 2행. 러너는 서버에서
# 돈다. 그래서 서버 몫을 이 스크립트로 넘긴다.
#
# 🚨 **세기가 먼저다.** 페이퍼 장부는 2026-09-01에 리셋됐고(WAN-392 §과거 장부 = 사용자 결정)
#    `live_limit_orders`는 `paper-reset.sh`의 `PROTECTED_TABLES`(= `ohlcv`·`funding_rate`)에
#    **없어 함께 비워졌다**. 백테 비율(2.64%)을 그 표본에 적용하면 기대 건수가 한 자릿수라,
#    `alphablock cascade`는 문턱(10건) 미만이면 **판정하지 않고** 「표본 부족 · 관측 계속」으로
#    적는다 — 그 출력이 곧 이 §의 답이다(지어내지 않는다, WAN-194).
#
# 🚨 **「표본 부족」을 「라이브는 안 한다」로 읽지 말 것** — 안 났다와 못 봤다는 다르다
#    (WAN-367). 그 경우 §3은 열어 둔 채 표본이 쌓이면 다시 돌린다.
#
# 무엇을 쓰나: **아무것도 안 쓴다.** 전부 읽기 전용 조회다(DB·엔진·전략·기본값 불변, WAN-194).
#
# 사용:
#   ./scripts/wan409-server-cascade-census.sh                   # stdout으로
#   ./scripts/wan409-server-cascade-census.sh -o wan409.md      # 파일로(권장 — 붙여넣기용)
#   ./scripts/wan409-server-cascade-census.sh -d 7              # 최근 7일 창만

set -uo pipefail

OUT=""
DAYS=""

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--out)  OUT="$2"; shift 2 ;;
        -d|--days) DAYS="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) printf '❌ 모르는 인자: %s\n' "$1" >&2; usage 1 ;;
    esac
done

emit() { if [[ -n "$OUT" ]]; then printf '%s\n' "$*" >> "$OUT"; else printf '%s\n' "$*"; fi; }
run()  { if [[ -n "$OUT" ]]; then "$@" >> "$OUT" 2>&1; else "$@"; fi; }

[[ -n "$OUT" ]] && : > "$OUT"

emit "# WAN-409 §3 — 라이브 「같은 존 연속 체결」 인구조사"
emit ""
emit "실행 시각: $(date '+%Y-%m-%d %H:%M:%S %Z') · 호스트: $(hostname)"
emit ""

emit "## 0. 표본이 있나 (장부 인구조사)"
emit ""
emit '```'
run uv run alphablock doctor --skip-quick-check
emit '```'
emit ""

emit "## 1. 인구조사 — 장부 전수"
emit ""
emit '```'
run uv run alphablock cascade
emit '```'
emit ""

if [[ -n "$DAYS" ]]; then
    emit "## 2. 인구조사 — 최근 ${DAYS}일 창"
    emit ""
    emit '```'
    run uv run alphablock cascade --days "$DAYS"
    emit '```'
    emit ""
fi

emit "## 참고 — 같이 읽을 것"
emit ""
emit "* 백테 쪽 표: \`backtest/reports/wan409_invalidation_cascade_summary.md\`"
emit "* 이 표에 **「무효화 봉 안 체결」 칸이 없다** — 라이브 장부에 대응 필드가 없어서이고,"
emit "  안 잰 것을 0%로 적지 않는다(WAN-194)."
emit "* 페이퍼 \`r_multiple\` ↔ 백테 \`net R\`만 맞댄다(WAN-393 §2 — 이름이 비슷한 셋째 자가 있다)."

[[ -n "$OUT" ]] && printf '완료 → %s\n' "$OUT"
exit 0
