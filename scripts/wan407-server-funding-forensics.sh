#!/usr/bin/env bash
#
# WAN-407 §1 — 펀딩 수집이 정말 멈췄나 (읽기 전용 서버 증거 수집)
#
# 로컬 세션에는 서버 SSH가 없어(WAN-195/314/353과 같은 제약) 서버 몫을 이 스크립트로
# 넘긴다. 판정 대상: 페이퍼 장부 창(2026-09-01 KST 리셋 이후)에 펀딩 정산 행이 쌓이고
# 있는가.
#
# 🚨 **주의 — 「파리티가 커버리지 0.0%를 경고했다」는 증거가 아니다.**
#    그 경고는 `alphablock parity`의 자기 배선에서 나왔고(대조 셀을 `funding=False`로
#    로드하면서 cfg는 펀딩 켬), **펀딩이 100% 채워진 창에서도** 떴다. WAN-407이 그
#    라벨을 고쳤다(docs/decisions/wan407.md §1). 그래서 「멈췄나」는 **아직 열린 질문**
#    이고, 이 스크립트가 그 답을 낸다.
#
# 무엇을 보나:
#   1) funding_rate 테이블의 심볼별 마지막 정산 시각·건수(설정 유니버스 기준)
#   2) 페이퍼 창(기본 2026-09-01 KST ~ 지금)의 커버리지 — 기대 정산 대비 실제
#   3) `ALPHABLOCK_FUNDING_ENABLED` 설정값과 수집기 로그의 펀딩 줄
#      — `data/collector.py`는 꺼져 있으면 "펀딩 수집 비활성화"를 로그에 남긴다.
#        **답이 거기 있을 수 있다.**
#   4) 수집기 재시작·배포 시각과 마지막 펀딩 행의 선후
#
# 사용:
#   ./scripts/wan407-server-funding-forensics.sh                 # stdout
#   ./scripts/wan407-server-funding-forensics.sh -o report.md    # 파일로
#   START=2026-09-01 ./scripts/wan407-server-funding-forensics.sh
#
# 🚨 **읽기 전용이다** — DB는 mode=ro로 열고 아무것도 고치지 않는다. 메우는 조치(§3
#    백필)는 **판정 뒤** 사람이 한다(WAN-194 원칙).

set -uo pipefail

OUT=""
while getopts "o:h" opt; do
  case "$opt" in
    o) OUT="$OPTARG" ;;
    h) sed -n '2,31p' "$0"; exit 0 ;;
    *) exit 2 ;;
  esac
done

DB="${DB:-data/ohlcv.db}"
LOG="${LOG:-logs/collector.log}"
# 페이퍼 장부 리셋 시점(KST) — WAN-392 §5 결정. 필요하면 START로 덮는다.
START="${START:-2026-09-01}"

emit() { if [ -n "$OUT" ]; then printf '%s\n' "$*" >>"$OUT"; else printf '%s\n' "$*"; fi; }
# WAL DB는 `-shm`이 없으면 `mode=ro`로 못 연다(로컬처럼 수집기가 안 도는 상태). 그때만
# 평범하게 연다 — 던지는 문장이 전부 SELECT라 어느 쪽이든 쓰지 않는다. ⚠️ `immutable=1`로
# 우회하지 않는다: 살아 있는 DB에 그걸 쓰면 WAL을 통째로 무시해 **낡은 값을 읽는다**(멈춘
# 수집을 살아 있는 것처럼 보이게 만드는 정확히 그 부류의 실패다).
sqlro() { sqlite3 "file:${DB}?mode=ro" "$1" 2>/dev/null || sqlite3 "$DB" "$1"; }
section() { emit ""; emit "## $*"; emit ""; }
run() {
  emit '```'
  emit "\$ $*"
  eval "$@" 2>&1 | sed 's/^/  /' | while IFS= read -r line; do emit "$line"; done
  emit '```'
}

[ -n "$OUT" ] && : >"$OUT"
emit "# WAN-407 §1 펀딩 수집 포렌식 ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
emit ""
emit "- DB: \`$DB\` · 로그: \`$LOG\` · 창 시작(KST): \`$START\`"
emit "- ⚠️ 파리티의 「커버리지 0.0%」는 **이 질문의 증거가 아니다**(WAN-407 §1에서 라벨 수정)."

section "1. 심볼별 마지막 펀딩 정산 (확정 행만)"
run "sqlro \"SELECT symbol, COUNT(*) AS n, datetime(MIN(funding_time)/1000,'unixepoch') AS first_utc, datetime(MAX(funding_time)/1000,'unixepoch') AS last_utc FROM funding_rate WHERE is_predicted=0 GROUP BY symbol ORDER BY last_utc DESC, symbol\""
emit ""
emit "읽는 법: 정상이면 \`last_utc\`가 **8시간 안**이다(정산 주기). 12종목이 **같은 시각**에"
emit "멈춰 있으면 수집기 축(프로세스·설정)이고, 몇 종목만 뒤처졌으면 그 심볼의 백필 축이다."
emit "⚠️ 예측 행(\`is_predicted=1\`)은 **미래** 시각이라 여기서 뺐다 — 섞으면 멈춘 수집이"
emit "살아 있는 것처럼 보인다."

section "2. 페이퍼 창의 커버리지 (기대 정산 대비 실제)"
run "uv run python -c \"
from config.settings import get_settings
from data.funding import FundingRateStore, symbol_coverage_row, format_coverage_table
import pandas as pd
s = get_settings()
start = int(pd.Timestamp('${START}', tz='Asia/Seoul').timestamp()*1000)
end = int(pd.Timestamp.now(tz='UTC').timestamp()*1000)
with FundingRateStore(s.db_path) as st:
    print(format_coverage_table([symbol_coverage_row(st, sym, start_ms=start, end_ms=end) for sym in s.symbols]))
\""
emit ""
emit "읽는 법: **100%면 안 멈춘 것이다.** 0%면 그 창에 확정 정산이 하나도 없다(= 멈췄다)."
emit "중간값이면 **언제부터** 끊겼는지를 §1의 \`last_utc\`가 가리킨다."

section "3. 설정값과 수집기 로그의 펀딩 줄"
run "grep -E '^ALPHABLOCK_FUNDING' .env 2>/dev/null || echo '(.env에 ALPHABLOCK_FUNDING* 없음 → 코드 기본값 True)'"
run "uv run python -c \"from config.settings import get_settings as g; s=g(); print('funding_enabled =', s.funding_enabled); print('backtest_funding_enabled =', s.backtest_funding_enabled)\""
run "grep -n -E '펀딩' '$LOG' 2>/dev/null | tail -40"
emit ""
emit "읽는 법: 로그에 **「펀딩 수집 비활성화 (funding_enabled=False)」**가 있으면 그것이"
emit "답이다(원인 = 설정). 「펀딩 백필 총 0 건 저장」이 반복되면 요청은 갔는데 거래소가"
emit "빈 응답을 준 것이다(원인 = 심볼/네트워크). 아무 줄도 없으면 **펀딩 루프 자체가 안"
emit "돌고 있다**(원인 = 프로세스 — \`python -m data.funding\`이 어디서도 안 돈다)."

section "4. 펀딩 프로세스·유닛이 실제로 도는가"
run "systemctl list-units --all 2>/dev/null | grep -iE 'alphablock' || echo '(systemd 유닛 조회 불가)'"
run "systemctl list-timers --all 2>/dev/null | grep -iE 'alphablock' || echo '(타이머 없음)'"
run "ps -ef | grep -iE 'data.funding|alphablock' | grep -v grep || echo '(관련 프로세스 없음)'"
emit ""
emit "읽는 법: 펀딩 최신화는 \`alphablock collect\`(수집기)가 함께 돌린다"
emit "(\`data/collector.py\`) — 수집기가 살아 있는데 펀딩만 멈췄으면 §3의 로그가 이유를"
emit "말해 준다. 수집기 자체가 죽었으면 OHLCV도 같이 멈춰 있어야 한다(§5로 교차 확인)."

section "5. 교차 확인 — OHLCV도 같이 멈췄나"
run "sqlro \"SELECT timeframe, datetime(MAX(open_time)/1000,'unixepoch') AS last_utc, COUNT(DISTINCT symbol) AS syms FROM ohlcv GROUP BY timeframe ORDER BY timeframe\""
emit ""
emit "읽는 법: OHLCV는 신선한데 펀딩만 낡았으면 **펀딩 경로만의 사고**다 — 그리고 그"
emit "상태는 \`alphablock status\`의 「데이터 신선도」가 전부 초록이라 예전에는 화면에서"
emit "안 보였다(WAN-407 §2가 고친 자리). 지금은 「펀딩 신선도」 절에 뜬다."

section "6. 지금 화면은 뭐라고 하나 (수정 후 확인)"
run "uv run alphablock status 2>&1 | sed -n '/펀딩 신선도/,\$p' | head -30"

emit ""
emit "---"
emit "## 판정 뒤에 할 일 (§3 — 사용자 수행)"
emit ""
emit "멈춘 구간이 확인되면 백필한다(DB를 **쓰는** 단계라 판정 뒤에 한다):"
emit ""
emit '```'
emit "uv run python -m data.funding --backfill-only --start ${START}"
emit '```'
emit ""
emit "그리고 **원인을 함께 고친다** — 백필은 과거를 메울 뿐 앞으로를 보장하지 않는다."
emit "설정이 꺼져 있었으면 \`.env\`(\`ALPHABLOCK_FUNDING_ENABLED=true\`) 후 수집기 재시작,"
emit "프로세스가 죽어 있었으면 수집기 유닛을 살린다. 재발 감시는 \`alphablock watch\`가"
emit "맡는다(펀딩 STALE을 폰으로 보낸다 — WAN-344가 등록한 타이머)."
emit ""
emit "⚠️ **옛 페이퍼 장부를 다시 계산하지 말 것**(WAN-371 §과거 장부와 같은 이유) —"
emit "\`realized_pnl\`·\`equity_after\`는 정산 **순서**의 산물이라 행 하나를 고쳐 쓸 수 없다."
emit "백필이 되면 **그 이후 거래부터** 펀딩이 정확해진다."
