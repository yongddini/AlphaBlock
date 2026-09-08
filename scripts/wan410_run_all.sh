#!/usr/bin/env bash
# WAN-410 — 파트를 순서대로 돈다(§1-0이 먼저다: 이슈가 「§1-0이 안 끝나면 §1-1을 시작하지
# 않는다」고 못 박았다). 각 파트는 CSV에 이어 붙이므로 중간에 죽어도 앞쪽이 남는다.
set -euo pipefail
JOBS="${1:-2}"
cd "$(dirname "$0")/.."
for part in defs grid checksum loo null; do
  echo "=== [$(date '+%H:%M:%S')] --part ${part} ==="
  uv run python -m backtest.wan410_daily_loss_circuit_breaker --part "${part}" --jobs "${JOBS}"
done
echo "=== [$(date '+%H:%M:%S')] 완료 ==="
