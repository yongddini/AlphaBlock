"""통합 트레이딩 웹 대시보드 (WAN-15).

캔들+오더블록+시그널 차트와 백테스트 성과를 한 화면에서 보여주는
Streamlit 앱과 그 지원 모듈들을 담는다. 실행 방법은 저장소 README 참고.
"""

from __future__ import annotations

from dashboard.pipeline import PipelineResult, run_pipeline

__all__ = ["PipelineResult", "run_pipeline"]
