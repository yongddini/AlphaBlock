#!/usr/bin/env bash
#
# WAN-354 — 백테 병렬 워커 수가 서버에서 **실제로** 무엇인지 확인 (읽기 전용)
#
# 로컬 개발 세션에는 서버 SSH 접근이 없어(WAN-195/314와 같은 제약) 서버 몫의 확인을
# 이 스크립트로 넘긴다. 묻는 것은 하나다: **`ALPHABLOCK_BACKTEST_JOBS`가 세 경로에서
# 진짜로 먹는가.**
#
# 🚨 라벨이 아니라 **동작**으로 판정한다(완료 기준 2) — 설정 파일에 줄이 있는지가 아니라
#    (a) 파이썬이 그 값으로 푸는지, (b) 그 수만큼 **워커 프로세스가 실제로 뜨는지** 를 본다.
#    이 저장소가 반복해 겪은 실패가 「설정했다고 믿으면서 기본값으로 도는」 것이다
#    (WAN-91/95/112/123/159).
#
# 무엇을 보나:
#   0) 하드웨어 — nproc / MemTotal (설정해야 할 값의 기준)
#   1) .env — 존재 여부 + ALPHABLOCK_BACKTEST_JOBS 줄
#   2) 파이썬이 실제로 푸는 값 — 저장소 CWD와 저장소 밖 CWD 둘 다
#   3) 크론 — 야간 적재 줄이 무엇을 부르는지 (환경변수 주입 여부)
#   4) systemd — 유닛의 WorkingDirectory / Environment (그 자리가 .env 를 읽는 근거)
#   5) 동작 확인 — 인자 없이 fan-out 했을 때 **워커 프로세스가 몇 개 뜨는가**
#
# 사용:
#   ./scripts/wan354-server-jobs-check.sh                # stdout 으로
#   ./scripts/wan354-server-jobs-check.sh -o report.md   # 파일로
#
# 🚨 **읽기 전용이다** — DB 도 설정도 아무것도 고치지 않는다. 값을 바꾸는 것은 사람이
#    `.env` 에 한 줄 넣는 것뿐이다(docs/ops/server-migration.md §2a).

set -uo pipefail

OUT=""
while getopts "o:h" opt; do
  case "$opt" in
    o) OUT="$OPTARG" ;;
    h) sed -n '2,27p' "$0"; exit 0 ;;
    *) exit 2 ;;
  esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"

emit() { if [[ -n "$OUT" ]]; then printf '%s\n' "$*" >> "$OUT"; else printf '%s\n' "$*"; fi; }
[[ -n "$OUT" ]] && : > "$OUT"

emit "# WAN-354 서버 확인 — 백테 병렬 워커 수"
emit ""
emit "- 수집 시각: $(date '+%Y-%m-%d %H:%M:%S %Z')"
emit "- 호스트: $(hostname 2>/dev/null || echo '?') · 저장소: \`$REPO_DIR\`"
emit ""

# --------------------------------------------------------------------------- #
emit "## 0. 하드웨어 — 설정해야 할 값의 기준"
emit ""
emit '```'
emit "nproc            : $(nproc 2>/dev/null || echo '(nproc 없음)')"
if [[ -r /proc/meminfo ]]; then
  emit "MemTotal         : $(awk '/MemTotal/ {printf "%.1f GB", $2/1048576}' /proc/meminfo)"
  emit "MemAvailable     : $(awk '/MemAvailable/ {printf "%.1f GB", $2/1048576}' /proc/meminfo)"
fi
emit '```'
emit ""
emit "📌 **워커 수는 이 코어 수에 맞춘다.** 코어보다 워커가 많으면 문맥 전환에 더해"
emit "**워커마다 1분봉 사본**을 들어 메모리 압박까지 생긴다(WAN-324)."
emit ""

# --------------------------------------------------------------------------- #
emit "## 1. \`.env\` — 값이 적혀 있나"
emit ""
emit '```'
if [[ -f "$REPO_DIR/.env" ]]; then
  emit ".env             : 있음 ($REPO_DIR/.env)"
  line="$(grep -E '^[[:space:]]*ALPHABLOCK_BACKTEST_JOBS=' "$REPO_DIR/.env" || true)"
  if [[ -n "$line" ]]; then
    emit "설정 줄          : $line"
  else
    emit "설정 줄          : ❌ 없음 → 코드 기본값(4, M1 기준)으로 돈다"
  fi
else
  emit ".env             : ❌ 없음 → 코드 기본값(4, M1 기준)으로 돈다"
fi
emit '```'
emit ""

# --------------------------------------------------------------------------- #
emit "## 2. 파이썬이 실제로 푸는 값 (CWD 두 곳)"
emit ""
emit "\`.env\` 는 **CWD 기준**으로 먼저 찾고, 없으면 **저장소 루트**를 폴백으로 읽는다"
emit "(WAN-354 — 옛 코드는 CWD 판만 봐서 저장소 밖 실행이 조용히 기본값으로 돌았다)."
emit ""
emit '```'
if [[ -n "$UV_BIN" ]]; then
  probe='from backtest.harness import default_jobs; import os; print(f"{os.getcwd()} -> default_jobs() = {default_jobs()}")'
  emit "저장소 CWD       : $(cd "$REPO_DIR" && "$UV_BIN" run -- python -c "$probe" 2>&1 | tail -1)"
  emit "저장소 밖 CWD    : $(cd / && "$UV_BIN" run --project "$REPO_DIR" -- python -c "$probe" 2>&1 | tail -1)"
else
  emit "❌ uv 를 찾을 수 없습니다(UV_BIN=... 로 지정하세요)."
fi
emit '```'
emit ""

# --------------------------------------------------------------------------- #
emit "## 3. 크론 — 야간 적재가 무엇을 부르나"
emit ""
emit '```'
if crontab -l >/dev/null 2>&1; then
  crontab -l 2>/dev/null | grep -nE 'alphablock|AlphaBlock' | while IFS= read -r l; do emit "$l"; done
  [[ -z "$(crontab -l 2>/dev/null | grep -E 'alphablock|AlphaBlock')" ]] && emit "(alphablock 관련 줄 없음)"
else
  emit "(crontab 없음 또는 읽기 불가)"
fi
emit '```'
emit ""
emit "🚨 크론은 로그인 셸 프로필(\`~/.bashrc\`)을 **안 읽는다** — \`export\` 로는 안 걸린다."
emit "크론 줄이 \`cd <저장소>\` 를 하므로 \`.env\` 가 먹는다(§2 에서 실제로 확인된다)."
emit ""

# --------------------------------------------------------------------------- #
emit "## 4. systemd — 유닛이 \`.env\` 를 읽는 자리"
emit ""
emit '```'
if command -v systemctl >/dev/null 2>&1; then
  for unit in alphablock-collector alphablock-live alphablock-dashboard; do
    conf="$(systemctl cat "${unit}.service" 2>/dev/null | grep -E '^(WorkingDirectory|Environment|EnvironmentFile)=' || true)"
    if [[ -n "$conf" ]]; then
      emit "[$unit]"
      printf '%s\n' "$conf" | while IFS= read -r l; do emit "  $l"; done
    else
      emit "[$unit] (유닛 없음 또는 해당 지시자 없음)"
    fi
  done
else
  emit "(systemctl 없음)"
fi
emit '```'
emit ""
emit "📌 유닛은 \`WorkingDirectory=<저장소>\` 를 두므로 그 \`.env\` 를 읽는다 — 그래서"
emit "\`EnvironmentFile=\` 을 **일부러 넣지 않았다**(두 자리에 같은 값을 두면 갈라진다."
emit "게다가 \`.env\` 에는 API 키·텔레그램 토큰이 있어 \`systemctl show\` 로 새어 나온다)."
emit "⚠️ 대시보드 **화면 버튼은 이 값을 안 읽는다** — 의도적으로 직렬이다(WAN-324)."
emit ""

# --------------------------------------------------------------------------- #
emit "## 5. 동작 확인 — 워커가 실제로 몇 개 뜨는가"
emit ""
emit "설정 파일에 줄이 있는지가 아니라 **프로세스가 그 수만큼 뜨는지**를 센다."
emit ""
emit '```'
if [[ -n "$UV_BIN" ]]; then
  # 자식 프로세스를 실제로 띄우고 부모가 그 수를 센다 — fan-out 이 쓰는 것과 같은
  # ProcessPoolExecutor 다. 계산은 하지 않는다(os.getpid() 만 돌려준다).
  # 워커를 실제로 띄우고 부모가 그 수를 센다 — fan-out 이 쓰는 것과 같은
  # ProcessPoolExecutor 다. 계산은 하지 않는다(각 작업이 자기 PID 만 돌려준다).
  emit "$(cd "$REPO_DIR" && "$UV_BIN" run -- python -m scripts.wan354_jobs_probe 2>&1 | tail -6)"
else
  emit "(uv 없음 — 건너뜀)"
fi
emit '```'
emit ""
emit "📌 CLI 경로도 같은 값을 쓴다(WAN-354): \`alphablock trades|compare|stop-width|parity\` 는"
emit "\`--jobs\` 를 안 주면 이 설정값으로 fan-out 하고, **그 값을 stderr 첫 줄에**"
emit "찍는다. 야간 크론 로그(\`persist.log\`)에서 그 줄로 확인할 수 있다:"
emit ""
emit '```bash'
emit "grep -m1 '^병렬 설정:' ~/persist.log"
emit '```'
emit ""
emit "---"
emit ""
emit "⚠️ \`--jobs\` 는 결과를 안 바꾸는 **순수 성능 노브**다(WAN-121: 직렬 = 병렬 비트 동일)."
emit "이 값을 바꿔도 측정값·재현성·캐시 지문은 하나도 안 움직인다 — 옛 표 재산출도"
emit "캐시 되채우기도 **불필요**하다."

[[ -n "$OUT" ]] && echo "✅ 리포트: $OUT"
exit 0
