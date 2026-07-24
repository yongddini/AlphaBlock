#!/usr/bin/env bash
#
# AlphaBlock 리눅스 서버 1회 셋업 (WAN-174)
#
# 오라클 무료 티어(VM.Standard.E2.1.Micro, 1 OCPU · 1GB RAM · x86) 기준으로
# 페이퍼 수집기·러너·대시보드 상시 가동에 필요한 최소 준비를 한다. 멱등이다
# (이미 돼 있는 단계는 건너뛴다). 저장소가 clone 된 디렉터리 안에서 실행한다:
#
#   git clone https://github.com/yongddini/AlphaBlock.git && cd AlphaBlock
#   ./scripts/setup-server.sh
#
# 하는 일:
#   1. 스왑 2GB — 1GB 박스는 순간 스파이크 방어용으로 사실상 필수(PM 실측 제약).
#   2. uv 설치(없으면) + `uv sync` 로 파이썬 의존성 설치.
#   3. 다음 단계 안내(.env 배치 → DB 이전 → install-systemd.sh).
#
# 안전: 실거래 설정을 만들지 않는다(ALPHABLOCK_LIVE_TRADING 기본 false 불변).
# 무거운 백테스트 격자는 이 서버에서 돌리지 않는다 — OOM 난다(로컬 맥에서 돌린다).
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "❌ 이 스크립트는 리눅스 서버 전용입니다." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWAP_FILE="/swapfile"
SWAP_SIZE_MB=2048

# --- 1. 스왑 2GB -----------------------------------------------------------
if swapon --show | grep -q .; then
    echo "✅ 스왑이 이미 켜져 있습니다:"
    swapon --show
else
    echo "➕ 스왑 ${SWAP_SIZE_MB}MB 생성 중 ($SWAP_FILE)..."
    # fallocate 가 안 되는 파일시스템(xfs 구버전 등)이면 dd 로 대체.
    sudo fallocate -l "${SWAP_SIZE_MB}M" "$SWAP_FILE" 2>/dev/null \
        || sudo dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$SWAP_SIZE_MB" status=progress
    sudo chmod 600 "$SWAP_FILE"
    sudo mkswap "$SWAP_FILE"
    sudo swapon "$SWAP_FILE"
    if ! grep -q "^$SWAP_FILE" /etc/fstab; then
        echo "$SWAP_FILE none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
    fi
    echo "✅ 스왑 활성화 + /etc/fstab 등록(재부팅 유지)."
fi

# --- 2. uv + 의존성 --------------------------------------------------------
if ! command -v uv >/dev/null; then
    echo "➕ uv 설치 중..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 설치 직후 현재 셸 PATH 반영(기본 설치 경로).
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "✅ uv: $(command -v uv)"

echo "➕ 파이썬 의존성 설치(uv sync)..."
(cd "$REPO_DIR" && uv sync)
echo "✅ 의존성 설치 완료."

# --- 3. 다음 단계 안내 ------------------------------------------------------
cat <<'NEXT'

다음 단계 (자세한 절차: docs/ops/server-migration.md):
  1. .env 배치 — 로컬 맥에서: scp .env <서버>:~/AlphaBlock/.env
     (텔레그램 토큰 등. 커밋 금지 파일이므로 손으로 옮긴다.)
  2. DB 이전 — 로컬 수집기 정지 후 data/ohlcv.db 를 scp 로 복사
     (⚠️ WAL 정합성: 복사 전 반드시 수집기를 멈추고 체크포인트).
  3. 상시 구동 등록: ./scripts/install-systemd.sh
  4. 수집 확인: uv run -- alphablock status
     (웹소켓이 1분봉을 실시간으로 받는지 = WAN-174 완료 기준)
NEXT
