#!/usr/bin/env bash
#
# main 브랜치 보호 규칙 적용 (WAN-28)
#
# "CI(WAN-10) green이 아니면 main에 머지 불가" 정책을 GitHub에 실제로 강제한다.
# 브랜치 보호 규칙 변경에는 저장소 관리자(owner) 권한이 필요하므로,
# 이 스크립트는 저장소 소유자가 직접 1회 실행해야 한다.
#
# 사용:
#   ./scripts/setup-branch-protection.sh          # OWNER/REPO 자동 감지
#   REPO=yongddini/AlphaBlock ./scripts/setup-branch-protection.sh
#
# 적용되는 규칙:
#   - PR을 거치지 않은 main 직접 push 금지 (관리자 포함)
#   - 필수 status check: CI 워크플로우의 "quality" 잡 (.github/workflows/ci.yml)
#     → ruff check / ruff format --check / mypy / pytest 가 모두 green이어야 머지 가능
#   - 머지 전 브랜치를 최신 base로 최신화 요구 (strict)
#   - 대화(리뷰 코멘트) 미해결 시 머지 금지
#   - force push / 브랜치 삭제 금지
#
# 수동 머지 정책은 유지된다: 보호 규칙은 "CI 통과 전 머지 차단"만 강제하고,
# 실제 머지 버튼은 사람이 누른다(CLAUDE.md 워크플로우).
#
set -euo pipefail

BRANCH="${BRANCH:-main}"
# CI 잡 이름과 일치해야 한다 (.github/workflows/ci.yml 의 job id "quality").
CHECK_CONTEXT="${CHECK_CONTEXT:-quality}"

if [[ -z "${REPO:-}" ]]; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
fi

echo "▶ 브랜치 보호 적용: repo=${REPO} branch=${BRANCH} required-check=${CHECK_CONTEXT}"

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/${REPO}/branches/${BRANCH}/protection" \
  --input - <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["${CHECK_CONTEXT}"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0
  },
  "required_conversation_resolution": true,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo "✅ 브랜치 보호 적용 완료."
echo
echo "선택: 'Allow auto-merge'를 켜면 'gh pr merge --auto'로 CI green 시 자동 머지 예약이 가능하다"
echo "(수동 머지 정책과 병행 가능한 편의 옵션):"
echo "  gh api --method PATCH /repos/${REPO} -f allow_auto_merge=true"
