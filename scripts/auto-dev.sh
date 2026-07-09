#!/usr/bin/env bash
# AlphaBlock 무인 자동개발 루틴 (Claude Code 헤드리스)
# Approved 상태 이슈를 하나 가져와 CLAUDE.md 워크플로우대로 개발하고 In Review로 옮긴다.
# cron/launchd 에서 주기 실행하도록 설계됨. 실행 결과는 로그로 남긴다.
set -euo pipefail

PROJECT_DIR="$HOME/AlphaBlock"
LOG="$PROJECT_DIR/.auto-dev.log"
cd "$PROJECT_DIR"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') auto-dev 시작 =====" >> "$LOG"

claude -p "Alphabot 팀(WAN)에서 state=Approved 이슈를 우선순위 높은 순으로 '하나만' 골라 CLAUDE.md의 개발 워크플로우대로 진행해줘: In Progress로 전환 → 완료기준 충족하게 개발 → 품질게이트(uv run ruff check . / ruff format --check . / mypy . / pytest) 통과 → 이슈식별자로 커밋 → 작업요약 코멘트 남기고 In Review로 이동. Approved 이슈가 하나도 없으면 아무 변경도 하지 말고 '처리할 Approved 이슈 없음'만 출력하고 종료해. execution/주문실행·실거래(live_trading) 활성화는 하지 마." \
  --allowedTools "Read,Edit,Write,Bash(uv *),Bash(git *),Bash(mkdir *),mcp__linear-server" \
  --permission-mode acceptEdits \
  >> "$LOG" 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') auto-dev 종료 =====" >> "$LOG"
