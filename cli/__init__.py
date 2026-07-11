"""AlphaBlock 실행 CLI (WAN-31).

`alphablock` 콘솔 스크립트의 진입점을 제공한다. 하위 명령:

* ``alphablock collect`` — 데이터 수집기(백필 + 실시간 스트림).
* ``alphablock live``   — 실시간 시그널 러너(페이퍼).
* ``alphablock status`` — 운영 상태(Health) 요약을 콘솔에 출력.
"""

from __future__ import annotations

from cli.main import main

__all__ = ["main"]
