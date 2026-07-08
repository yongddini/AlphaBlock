# AlphaBlock — Claude Code 개발 가이드

오더블록(Order Block) 기반 암호화폐 자동매매 프로젝트. **이 저장소에서 Claude Code의 역할은 "개발자"다.** 완료 판단·상태 관리·다음 작업 제안 등 PM 업무는 별도 자동화(Cowork PM 러너)가 담당하므로 여기서 신경 쓰지 않는다.

## 역할 분담 (중요)

- **Claude Code (여기, 로컬)** = 개발. 아래 "개발 워크플로우"만 수행한다.
- **PM 러너 (Cowork, 매시간)** = 완료 판단(In Review→Done), 상태 관리, 다음 Todo 제안, 보고. 코드는 건드리지 않는다.
- 승인은 사용자가 이슈를 **Approved** 상태로 옮기는 것으로 표현된다.

## Linear 연결

이 워크스페이스는 Linear로 이슈를 관리한다. Linear MCP를 연결해 사용:

```
claude mcp add --transport http linear-server https://mcp.linear.app/mcp
```

세션에서 `/mcp`로 인증. 팀은 **Alphabot** (key: `WAN`).

## 개발 워크플로우

1. **Approved 이슈만 개발한다.** Linear에서 team=Alphabot, state=`Approved` 이슈를 우선순위 순으로 가져온다. Backlog/Todo/Rejected 는 절대 개발하지 않는다.
2. 착수할 이슈를 **In Progress**로 바꾼다.
3. 이슈의 완료 기준(Acceptance Criteria)을 충족하도록 이 저장소에서 개발한다.
4. 커밋 메시지 앞에 이슈 식별자를 붙인다. 예: `WAN-7: 오더블록 탐지 로직 추가`.
5. 완료하면: 변경 요약(변경 파일, 커밋/PR, 완료기준 충족 여부, 테스트 결과)을 이슈에 코멘트로 남기고 상태를 **In Review**로 옮긴다. (Done으로 직접 옮기지 않는다 — 완료 판단은 PM 러너가 한다.)
6. PM 러너가 미흡하다고 판단해 **Approved로 되돌리고** 변경요청 코멘트를 달면, 그 피드백을 반영해 다시 3~5를 수행한다.

## 품질 게이트 (커밋/리뷰요청 전 필수 통과)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .            # strict
uv run pytest
```

처음이라면 `uv sync --dev` 로 의존성 설치, `uv run pre-commit install` 로 훅 설치.

## 안전 규칙

- **실거래 자동 활성화 금지.** `ALPHABLOCK_LIVE_TRADING` 기본값은 `false`. 실주문/자금 이동 코드는 사용자가 명시적으로 승인·수행하기 전까지 활성화하지 않는다.
- API 키·시크릿은 코드에 하드코딩하지 않는다. `.env`(커밋 금지, `.gitignore` 포함)나 환경변수로만 주입하고, 새 설정은 `.env.example`에 예시를 추가한다.
- 테스트가 실패하거나 완료기준을 못 채운 상태로 In Review로 넘기지 않는다.

## 프로젝트 구조

```
AlphaBlock/
├── data/        # 시세 수집·저장 (OHLCV, 웹소켓) — WAN-6
├── strategy/    # 오더블록 탐지·시그널 생성 — WAN-7
├── execution/   # 주문 실행·포지션·리스크 관리 — WAN-9
├── backtest/    # 백테스팅 엔진 — WAN-8
├── config/      # 설정 로딩 (pydantic-settings)
├── tests/       # 테스트
└── pyproject.toml
```

## 기술 스택

Python 3.11+ · uv · ccxt · pandas · asyncio · pydantic-settings
