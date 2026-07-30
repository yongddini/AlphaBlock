"""통합 트레이딩 웹 대시보드 (WAN-15).

캔들+오더블록+시그널 차트와 백테스트 성과를 한 화면에서 보여주는
Streamlit 앱과 그 지원 모듈들을 담는다. 실행 방법은 저장소 README 참고.

옛 A안(종가 시그널) 분석 파이프라인(`dashboard.pipeline`·`dashboard.analysis_cache`)은
WAN-199(분석 탭 B안 조회 전환) → WAN-208로 제거됐다. 분석 탭은 `backtest.run --persist`가
적재한 B안 실행을 조회만 한다.
"""

from __future__ import annotations

__all__: list[str] = []
