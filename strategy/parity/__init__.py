"""오더블록 시각 검증 & 트레이딩뷰(TV) 패리티 도구 (WAN-13).

`strategy/order_blocks.py` 탐지 결과가 트레이딩뷰(Fluxchart Volumized Order
Blocks) 차트와 동일한지 확인하기 위한 세 축:

- `chart`: 캔들 + 탐지 오더블록 오버레이 이미지를 생성한다.
- `fixtures`: TV에서 수기로 캡처한 정답 오더블록 좌표를 로드한다.
- `report`: 탐지 결과와 fixture를 비교해 일치율/불일치 리포트를 만든다.

각 모듈의 세부 사용법은 `strategy/parity/README.md` 참고.
"""

from __future__ import annotations
