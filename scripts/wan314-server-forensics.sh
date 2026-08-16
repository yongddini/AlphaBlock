#!/usr/bin/env bash
#
# WAN-314 §1 — 서버 증거 수집 (읽기 전용): 2026-08-16 15m 봉 결측 사고
#
# 로컬 개발 세션에는 서버 SSH 접근이 없어(WAN-195와 같은 제약) 서버 몫의 대조를
# 이 스크립트로 넘긴다. 대상 사고: open_time 1786888800000(= 2026-08-16 23:00 KST
# 15분봉)이 기존 9종목 전부에서 빠져 러너가 15m 평가를 30분 넘게 건너뛰었다.
#
# 무엇을 대조하나 (docs/decisions/wan314.md §1):
#   1) DB에 그 봉이 지금 있는가(사후 복구 여부) · 이웃 봉(22:45)의 closed/값 상태
#      — 22:45 봉은 기동 백필이 형성 중에 저장했다면 부분 봉이 확정 라벨을 달고
#      남았을 수 있다(고가/저가/종가가 22:5x 시점 값으로 잘림).
#   2) collector.log의 그 시각대: 백필 시작/완료 · 갭 복구 · 웹소켓 접속 시각
#      — 「기동 백필 ~ 스트림 접속」 창과 결측 봉의 확정 시각(23:15 KST)의 선후.
#   3) 1m 시리즈의 같은 창 결측(같은 메커니즘이면 1m도 같은 창에서 빠져 있어야 한다).
#
# 사용:
#   ./scripts/wan314-server-forensics.sh                # stdout으로
#   ./scripts/wan314-server-forensics.sh -o report.md   # 파일로
#
# 🚨 **읽기 전용이다** — DB는 mode=ro로 열고 아무것도 고치지 않는다. 구멍을 메우는
#    조치는 `uv run alphablock backfill --repair`(사용자 수행)다.

set -uo pipefail

OUT=""
while getopts "o:h" opt; do
  case "$opt" in
    o) OUT="$OPTARG" ;;
    h) sed -n '2,25p' "$0"; exit 0 ;;
    *) exit 2 ;;
  esac
done

DB="${DB:-data/ohlcv.db}"
LOG="${LOG:-logs/collector.log}"

# 사고 좌표(UTC epoch ms). 23:00 KST 15분봉 = UTC 14:00.
MISSING_15M=1786888800000        # 2026-08-16 14:00:00 UTC (23:00 KST) — 결측 봉
PREV_15M=1786887900000           # 13:45 UTC (22:45 KST) — 부분 저장 의심 봉
NEXT_15M=1786889700000           # 14:15 UTC (23:15 KST) — 스트림 재개 후 첫 봉 후보
WINDOW_START=1786886100000       # 13:15 UTC (22:15 KST)
WINDOW_END=1786891500000         # 14:45 UTC (23:45 KST)

emit() { if [ -n "$OUT" ]; then printf '%s\n' "$*" >>"$OUT"; else printf '%s\n' "$*"; fi; }
section() { emit ""; emit "## $*"; emit ""; }
run() {
  emit '```'
  emit "\$ $*"
  eval "$@" 2>&1 | sed 's/^/  /' | while IFS= read -r line; do emit "$line"; done
  emit '```'
}

sql() { sqlite3 "file:${DB}?mode=ro" "$1"; }

[ -n "$OUT" ] && : >"$OUT"
emit "# WAN-314 서버 포렌식 ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
emit ""
emit "- DB: \`$DB\` · 로그: \`$LOG\`"
emit "- 결측 봉: open_time $MISSING_15M = 2026-08-16 23:00 KST (15m)"

section "1. 결측 봉·이웃 봉의 현재 상태 (15m, 전 심볼)"
run "sqlite3 'file:${DB}?mode=ro' \"SELECT symbol, open_time, datetime(open_time/1000,'unixepoch') AS utc, closed, open, high, low, close, volume FROM ohlcv WHERE timeframe='15m' AND open_time IN ($PREV_15M, $MISSING_15M, $NEXT_15M) ORDER BY symbol, open_time\""
emit ""
emit "읽는 법: $MISSING_15M 행이 **없으면** 아직 복구 전(= 러너가 계속 건너뜀)."
emit "$PREV_15M(22:45 KST) 행이 있고 high==low 근처거나 volume이 유난히 작으면 기동"
emit "백필이 형성 중에 저장한 부분 봉이 확정 라벨로 남은 것이다(WAN-314 §1-b —"
emit "수정 전 백필은 형성 중 봉도 closed=1로 저장했다)."

section "2. 같은 창의 1m 결측 (같은 메커니즘 검증, BTC만)"
run "sqlite3 'file:${DB}?mode=ro' \"SELECT open_time, datetime(open_time/1000,'unixepoch') AS utc FROM ohlcv WHERE timeframe='1m' AND symbol='BTC/USDT:USDT' AND open_time BETWEEN $WINDOW_START AND $WINDOW_END ORDER BY open_time\""
emit ""
emit "읽는 법: 「기동 백필 ~ 스트림 접속」 창이 원인이면 1m도 **같은 창**이 통째로"
emit "비어 있어야 한다(15m 한 봉만 빠지고 1m이 멀쩡하면 다른 메커니즘이다)."

section "3. collector.log — 백필/갭 복구/웹소켓 접속 타임라인"
run "grep -n -E '백필 시작|백필 완료|백필 총|갭 자동 복구|웹소켓 접속|꼬리 따라잡기|스트림' '$LOG' | tail -80"
emit ""
emit "읽는 법(KST 로그, WAN-172): ① 「백필 시작」(기존 심볼 꼬리 시각 ≈ 이 시각) →"
emit "② 「갭 자동 복구」(이때 23:00 봉은 아직 꼬리라 「갭 없음」이 정상) → ③ 「웹소켓"
emit "접속」. ③이 23:15 KST(결측 봉 확정 시각) **이후**면 인과 (a) 확정 — 그 봉의"
emit "확정 이벤트를 받을 소켓이 없었다."

section "4. 러너 로그의 건너뜀 관측 (있으면)"
run "grep -n '평가 창에 구멍' logs/*.log 2>/dev/null | tail -20"

section "5. 상태 파일"
run "cat data/repair_state.json 2>/dev/null | head -60"
run "python3 -c \"import json;d=json.load(open('data/live_runtime_state.json'));print(json.dumps(d.get('data_gap_skips',[]),indent=2,ensure_ascii=False))\" 2>/dev/null"

emit ""
emit "---"
emit "수집이 끝나면: 구멍이 남아 있으면 \`uv run alphablock backfill --repair\`(수동 복구)."
emit "재발 방지 코드(형성 중 봉 제외 + 접속 직후 꼬리 따라잡기)는 WAN-314 PR 배포로 적용된다."
