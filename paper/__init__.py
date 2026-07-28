"""페이퍼 트레이딩 성과 추적 (WAN-33).

페이퍼 러너가 체결(진입→익절/손절)한 가상 거래를 **거래 단위로 SQLite에 영속
저장**하고(`paper.store`), 저장분으로 성과 지표(총 PnL·승률·손익비·MDD·거래 수)를
집계한다(`paper.performance`).

⚠️ **백테스트 대비 패리티 리포트(`paper.parity`)는 WAN-200 §C로 삭제됐다** — 그 모듈은
페이퍼를 A안(종가 시장가) 재실행과 비교했는데, 페이퍼 러너는 WAN-45 이후 B안(존-지정가)이라
**진입 방식이 다른 것끼리 대보는** 상태였다(경고를 신뢰할 수 없었다). 라이브↔백테스트
대조가 다시 필요하면 인-프로세스 재실행이 아니라 **적재된 백테스트 결과**
(`backtest.trade_store`, WAN-106)와 비교하는 방식으로 새로 짠다.
"""

from __future__ import annotations

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
    OpenPosition,
    PaperTradeRecord,
    PaperTradeRecorder,
    PaperTradeStore,
    build_record,
)

__all__ = [
    "OpenPosition",
    "PaperPerformance",
    "PaperTradeRecord",
    "PaperTradeRecorder",
    "PaperTradeStore",
    "PerfMetrics",
    "SeriesPerformance",
    "TradeStat",
    "build_performance",
    "build_record",
    "compute_metrics",
    "format_performance",
    "performance_to_dataframe",
    "records_to_dataframe",
]
