"""레이어 독립 공용 유틸리티 패키지 (WAN-42).

어느 상위 레이어(`data`/`live`/`paper`/`dashboard` …)에서나 자유롭게 쓰이지만
**그 어떤 프로젝트 패키지에도 역으로 의존하지 않는** 가벼운 도구를 모은다.
표준 라이브러리 + `config.settings`(최하위 설정 레이어)까지만 임포트하며,
`data`/`strategy`/`execution`/`backtest`/`live`/`paper` 를 절대 임포트하지 않는다.

이렇게 분리해 두면 하위 레이어(`data`)가 상위 레이어(`live`)의 유틸리티를
쓰려고 `live` 패키지 전체를 끌어오는 역방향 의존(→ 순환 임포트)이 사라진다.
레이어 규칙은 `docs/architecture-layers.md` 참고.

무거운 하위 모듈을 eager 하게 re-export 하지 않는다 — 사용처에서
`from common.heartbeat import HeartbeatStore` 처럼 필요한 모듈만 직접 임포트한다.
"""

from __future__ import annotations

__all__: list[str] = []
