"""페이퍼 트레이딩 성과 추적 & 백테스트 대비 패리티 (WAN-33).

WAN-25 페이퍼 러너가 체결(진입→익절/손절)한 가상 거래를 **거래 단위로 SQLite에
영속 저장**하고(`paper.store`), 저장분으로 성과 지표(총 PnL·승률·손익비·MDD·거래 수)를
집계하며(`paper.performance`), 같은 기간·시리즈를 백테스트로 재실행해 라이브 페이퍼
결과와 비교하는 패리티 리포트(`paper.parity`)를 제공한다.
"""

from __future__ import annotations

from paper.parity import (
    ParityReport,
    ParityRow,
    ParityThresholds,
    build_parity_report,
)
from paper.performance import (
    PaperPerformance,
    PerfMetrics,
    SeriesPerformance,
    TradeStat,
    build_performance,
    compute_metrics,
)
from paper.report import (
    format_performance,
    performance_to_dataframe,
    records_to_dataframe,
)
from paper.store import (
    PaperTradeRecord,
    PaperTradeRecorder,
    PaperTradeStore,
    build_record,
)

__all__ = [
    "PaperPerformance",
    "PaperTradeRecord",
    "PaperTradeRecorder",
    "PaperTradeStore",
    "ParityReport",
    "ParityRow",
    "ParityThresholds",
    "PerfMetrics",
    "SeriesPerformance",
    "TradeStat",
    "build_parity_report",
    "build_performance",
    "build_record",
    "compute_metrics",
    "format_performance",
    "performance_to_dataframe",
    "records_to_dataframe",
]
