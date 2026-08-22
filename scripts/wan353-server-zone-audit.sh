#!/usr/bin/env bash
#
# WAN-353 — 「존 없음」 갈래 감사를 서버에서 **순서대로 한 번에** 돌린다 (WAN-343 §5 서버 몫).
#
# 로컬 개발 세션에는 서버 SSH 접근이 없고(WAN-195/314/337/342와 같은 제약) 판정에 필요한
# 데이터가 로컬에 없다 — `live_limit_orders` **0행** · 상위TF가 2026-08-13에서 멈춘다.
# 그래서 서버 몫을 이 스크립트로 넘긴다.
#
# 🚨 이 스크립트가 존재하는 이유는 **순서**다. 그냥 `--zone-audit` 두 줄을 치면 WAN-334를 네
#    번 포기하게 만든 「매번 처음부터 재계산」이 그대로 재현된다:
#      1) `stop-width --with-backtest`는 **옛 엔진 판을 기본 거부**한다(WAN-335 규약 — 파리티
#         측정이라 엔진이 다른 두 판을 빼면 「집행 차이」가 아니라 「엔진이 바뀐 몫」이 섞인다).
#      2) 서버의 마지막 되채우기(WAN-338, 08-20) 뒤 **캐시 지문을 이루는 엔진 파일이 바뀌었다**
#         (WAN-336/345/346이 얹은 옵트인 관측·배선). 캐시 **버전**은 그대로이고 **엔진 지문**이
#         움직였으므로 그 두 날은 지금 엔진 기준으로 **전 칸 미스**일 수 있다.
#    그래서 순서가 「지문 확인 → 미스면 되채우기 → 그다음에 감사」다.
#
# ⚠️ `--jobs` 기본값이 **2**인 이유: 서버는 2코어 1GB이고 워커마다 1분봉 사본이 들어간다
#    (WAN-324/354). `--jobs`는 결과를 안 바꾸는 순수 성능 노브라(WAN-121: 직렬 = 병렬 비트
#    동일) **판정은 하나도 안 움직인다**. 이슈 본문의 `--jobs 4`를 그대로 쓰지 말 것.
#
# 무엇을 쓰나:
#   - 단계 2(되채우기)만 DB에 쓴다 — `timeline_cache_*` 테이블에 캐시 셀을 **적재**한다
#     (`alphablock trades --persist-cache`, 야간 크론과 **같은 경로**). 삭제·정리는 하지 않는다
#     (`--prune-*`는 안 쓴다 · WAN-194/297 원칙).
#   - 나머지 단계는 전부 읽기 전용이다. 엔진·전략·기본값·토대를 건드리지 않는다.
#
# 사용:
#   ./scripts/wan353-server-zone-audit.sh                      # stdout으로
#   ./scripts/wan353-server-zone-audit.sh -o wan353-report.md  # 파일로(권장 — 붙여넣기용)
#   ./scripts/wan353-server-zone-audit.sh -j 1                 # 워커 1개(러너와 다투지 않게)
#   ./scripts/wan353-server-zone-audit.sh -n                   # 되채우기 건너뛰기(이미 했다면)
#   DAYS="2026-08-17 2026-08-18" ./scripts/wan353-server-zone-audit.sh
#
# 🚨 `-n`(되채우기 건너뛰기)을 줘도 감사는 **틀리지 않는다** — `stop-width`가 미스인 칸을
#    스스로 계산한다. 다만 그 계산이 캐시에 남지 않아 **다음 실행이 또 처음부터 돈다**.

set -uo pipefail

OUT=""
JOBS="${JOBS:-2}"
BACKFILL=1
while getopts "o:j:nh" opt; do
  case "$opt" in
    o) OUT="$OPTARG" ;;
    j) JOBS="$OPTARG" ;;
    n) BACKFILL=0 ;;
    h) sed -n '2,40p' "$0"; exit 0 ;;
    *) exit 2 ;;
  esac
done

# 판정 대상 두 날(KST). WAN-342 실측에서 `(a) 존 없음`이 13/17씩 나온 날이다.
DAYS="${DAYS:-2026-08-17 2026-08-18}"
RUNTIME_STATE="${RUNTIME_STATE:-data/live_runtime_state.json}"
# 완료기준 4 — WAN-343 §1이 「재시작과 겹치는 유일한 클러스터」로 남긴 08-18 그 시각.
LONE_CLUSTER_DAY="2026-08-18"
LONE_CLUSTER_TIME="10:04"

emit() { if [ -n "$OUT" ]; then printf '%s\n' "$*" >>"$OUT"; else printf '%s\n' "$*"; fi; }
section() { emit ""; emit "## $*"; emit ""; }
fence_open() { emit '```'; }
fence_close() { emit '```'; }

# 명령을 돌리고 출력을 그대로 옮긴다. 종료 코드를 삼키지 않고 함께 적는다 —
# 「실패가 성공과 같은 모양」(WAN-194/318/321)을 이 스크립트에서 재현하지 않는다.
run() {
  local started elapsed rc
  started=$(date +%s)
  fence_open
  emit "\$ $*"
  # shellcheck disable=SC2086
  eval "$@" >"$TMP_OUT" 2>&1
  rc=$?
  while IFS= read -r line; do emit "$line"; done <"$TMP_OUT"
  elapsed=$(( $(date +%s) - started ))
  emit ""
  emit "[종료 코드 $rc · ${elapsed}초]"
  fence_close
  return $rc
}

TMP_OUT="$(mktemp)"
TMP_AUDIT="$(mktemp)"
trap 'rm -f "$TMP_OUT" "$TMP_AUDIT"' EXIT

[ -n "$OUT" ] && : >"$OUT"
emit "# WAN-353 존 대장 감사 — 서버 실행 기록 ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
emit ""
emit "- 대상 날짜(KST): \`$DAYS\` · 워커: \`--jobs $JOBS\` · 되채우기: $([ "$BACKFILL" = 1 ] && echo '함' || echo '건너뜀(-n)')"
emit "- 저장소: \`$(pwd)\` · HEAD: \`$(git rev-parse --short HEAD 2>/dev/null || echo '?')\` ($(git log -1 --format=%cd --date=short 2>/dev/null || echo '?'))"
emit ""
emit "> 🚨 **읽는 법은 미리 못 박혀 있다**(wan343.md §5) — 도구가 마지막에 찍는 「판정:」 한"
emit "> 줄이 완료기준 2의 답이다. 과반이 없으면 **한 사유로 닫지 않는다**(WAN-161 규약)."

section "0. 엔진 지문 — 지금 코드가 무엇인가"

emit "캐시 키에 **엔진 소스 지문**이 들어간다(WAN-106/253/318). 배포로 이 값이 바뀌면 과거"
emit "날짜가 통째로 미스가 되는데, 그건 버그가 아니라 설계대로다."
emit ""
run "uv run python -c \"from backtest.trade_store import engine_source_revision; print('엔진 소스 지문:', engine_source_revision())\""
run "uv run python -c \"from live.timeline_cache import TIMELINE_CACHE_VERSION as v; print('캐시 버전:', v)\""

section "1. 두 날이 지금 엔진으로 적재돼 있나 (엄격 조회 · 읽기 전용)"

emit "\`--no-stale\`은 **옛 엔진 판으로 대신 보여 주지 않는다**(WAN-325). 즉 아래에 「캐시 미스」가"
emit "찍히면 그 날짜는 지금 엔진으로는 **아직 없다**는 뜻이고, 되채우기가 필요하다."

NEED_BACKFILL=""
for day in $DAYS; do
  emit ""
  emit "### $day"
  run "uv run alphablock trades --day $day --no-stale --jobs $JOBS"
  if grep -q "캐시 미스\|아직 계산 안 됨" "$TMP_OUT"; then
    NEED_BACKFILL="$NEED_BACKFILL $day"
    emit "→ **미스**: 되채우기 대상."
  else
    emit "→ **적중**: 지금 엔진 판이 이미 있다."
  fi
done

section "2. 되채우기 (야간 크론과 같은 경로 · 캐시에 **적재**한다)"

if [ "$BACKFILL" != 1 ]; then
  emit "\`-n\`으로 건너뛴다. ⚠️ 감사 결과는 틀리지 않지만(미스 칸은 \`stop-width\`가 직접 계산한다)"
  emit "그 계산이 캐시에 안 남아 **다음 실행이 또 처음부터 돈다**."
elif [ -z "$NEED_BACKFILL" ]; then
  emit "되채울 날짜가 없다 — 두 날 모두 지금 엔진 판이 적중했다."
else
  emit "대상:\`$NEED_BACKFILL\`. 하루씩 따로 돈다(한 날이 실패해도 다른 날이 남는다)."
  emit ""
  emit "🚨 서버 실측으로 하루치 48셀이 **6분 23초**였다(WAN-322/324). 2코어에서 그보다 오래"
  emit "걸릴 수 있으니 \`tmux\`/\`nohup\` 안에서 돌리는 것을 권한다."
  for day in $NEED_BACKFILL; do
    emit ""
    emit "### $day 적재"
    run "uv run alphablock trades --day $day --persist-cache --jobs $JOBS"
  done
fi

section "3. 존 대장 감사 — 완료기준 1·2 (이 절이 판정을 낸다)"

emit "\`--zone-audit\`은 \`--unpaired\`를 함축한다. 짝 없는 **라이브** 셋업을 백테 존 아카이브와"
emit "대조해 「존 없음」을 갈래로 가른다(창 밖 / 존 미탐지 / 확정 시각 / 무효화 선행 / 소멸 선행 /"
emit "탭 기록 없음 / 설명 안 됨 / 대상 아님). 백테 쪽 짝 없는 행은 \`대상 아님\`이다 — 러너가 존"
emit "대장을 영속화하지 않아 같은 자로 잴 수 없다(WAN-306 · 지어내지 않는다)."

: >"$TMP_AUDIT"
for day in $DAYS; do
  emit ""
  emit "### $day"
  run "uv run alphablock stop-width --day $day --with-backtest --zone-audit --jobs $JOBS"
  { echo "===== $day ====="; cat "$TMP_OUT"; } >>"$TMP_AUDIT"
done

section "4. 완료기준 4 — 08-18 ${LONE_CLUSTER_TIME} 1건이 같은 원인으로 설명되나"

emit "WAN-343 §1이 러너 재시작 가설을 기각하면서 **딱 하나** 겹친 클러스터로 남긴 시각이다."
emit "아래에 그 시각 행이 잡히면 그 행의 **사유 열**이 답이다 — 다른 행들과 같은 사유면 「같은"
emit "원인으로 설명된다」이고, 혼자 다르면 **그 1건만 재시작 계정**이다."
emit ""
fence_open
grep -n "$LONE_CLUSTER_TIME" "$TMP_AUDIT" | sed 's/^/  /' || true
if ! grep -q "$LONE_CLUSTER_TIME" "$TMP_AUDIT"; then
  emit "  (그 시각 행이 감사 표에 없다 — 짝이 지어졌거나 그 날 짝 없는 셋업에 없다."
  emit "   위 §3의 ${LONE_CLUSTER_DAY} 표 전체를 눈으로 확인할 것. 지어내지 않는다.)"
fi
fence_close

section "5. 완료기준 5 — data_gap_skips 대조 (WAN-314 §3 · 읽기 전용)"

emit "러너가 **데이터 결측으로 건너뛴 평가**의 기록이다. ⚠️ 이건 **현재 상태 스냅샷**이라 판정"
emit "두 날 당시 기록이 이미 덮였을 수 있다 — 없으면 「**확인 불가**」로 적고 지어내지 않는다"
emit "(WAN-194 원칙)."
emit ""
if [ -f "$RUNTIME_STATE" ]; then
  run "uv run python -c \"
from common.timefmt import format_kst
from live.runtime_state import RuntimeStateStore
skips = RuntimeStateStore('$RUNTIME_STATE').load().data_gap_skips
if not skips:
    print('data_gap_skips: 0건 — 지금 스냅샷에는 기록이 없다.')
    print('→ 완료기준 5는 「확인 불가」다(당시 기록이 덮였는지 애초에 없었는지 이 파일로는 못 가른다).')
else:
    print(f'data_gap_skips: {len(skips)}건')
    for s in skips:
        span = f'{format_kst(s.gap_start_ms)} ~ {format_kst(s.gap_end_ms)}'
        done = format_kst(s.resolved_ms) if s.resolved_ms else '미해소'
        print(f'   {s.symbol:<12}{s.timeframe:<5}{span}  건너뜀 {s.skip_count}회 · 해소 {done}')
        print(f'      {s.summary}')
\""
else
  emit "\`$RUNTIME_STATE\` 파일이 없다 — 완료기준 5는 **확인 불가**다."
fi

section "6. 붙여넣을 것 — 이슈 코멘트용 요약"

emit "§3의 두 「사유별 집계」 블록과 두 「판정:」 줄을 그대로 옮기고, §4·§5의 결과를 한 줄씩"
emit "덧붙이면 완료기준 1·2·4·5가 닫힌다. 완료기준 3(후속)은 판정에 따라 갈린다:"
emit ""
emit "| 과반 사유 | 후속 |"
emit "| -- | -- |"
emit "| \`무효화 선행\` | **CLAUDE.md 문단**(결함 아님 — \`on_htf_bars\`의 알려진 근사). 「무효화 봉 = 탭 봉」 건수를 **크기**로 함께 적는다 |"
emit "| \`존 미탐지\` / \`확정 시각\` | **엔진 파리티 결함** → 별도 이슈(창 가설은 WAN-343 §2가 이미 기각했다) |"
emit "| \`창 밖\` | 대조 도구의 워밍업 근사 → \`--warmup-days\`를 늘려 재확인 |"
emit "| 과반 없음 | **한 사유로 닫지 않는다** — 사유마다 따로 본다(WAN-161 규약) |"
emit ""
emit "⚠️ 크기(08-17 23.3% · 08-18 37.8%)를 「파리티 대조가 그만큼 틀렸다」로 읽지 말 것 — 짝 없는"
emit "셋업은 손절폭 표에서 **빠지고**(WAN-333) 편향 점검 Δ가 0.000%p·−0.014%p로 작다."
emit "**WAN-334의 손절폭 판정은 안 뒤집힌다.**"
emit ""
emit "⚠️ 엔진·기본값·토대 불변 · 손절폭 가드(0.3%)·존폭 필터(1.28) 손대지 않음 ·"
emit "\`ALPHABLOCK_LIVE_TRADING=false\` 유지(완료기준 6)."

[ -n "$OUT" ] && printf '기록: %s\n' "$OUT"
exit 0
