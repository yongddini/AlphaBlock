"""트레이딩뷰(TV) 정답 오더블록 데이터셋(fixture) 로딩.

fixture 파일은 JSON이며, TV 차트에서 수기로 캡처한 오더블록 좌표
(`tv_order_blocks`)와 그 좌표를 재현하기 위한 OHLCV 봉(`candles`)을 함께
담는다. 정답 확보 절차는 `strategy/parity/README.md` 참고.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from strategy.models import OrderBlockDirection, OrderBlockParams

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TvCandle(BaseModel):
    """fixture에 포함된 OHLCV 봉 하나."""

    model_config = ConfigDict(frozen=True)

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class TvOrderBlock(BaseModel):
    """TV 차트에서 수기로 읽은 오더블록 정답 좌표 하나."""

    model_config = ConfigDict(frozen=True)

    direction: OrderBlockDirection
    top: float
    bottom: float
    start_time: int
    invalidated: bool = False
    break_time: int | None = None
    note: str | None = None


class TvFixture(BaseModel):
    """심볼·타임프레임 하나에 대한 TV 정답 데이터셋."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    source: str
    """정답의 출처(예: 실제 TV 캡처 여부·캡처 일자·대체 근거)를 명시한다."""
    candles: list[TvCandle]
    tv_order_blocks: list[TvOrderBlock]
    params: OrderBlockParams = OrderBlockParams()
    """TV 인디케이터 설정과 맞춘 탐지 파라미터. 생략 시 기본값."""

    def to_dataframe(self) -> pd.DataFrame:
        """탐지기 입력용 OHLCV DataFrame으로 변환한다."""
        return pd.DataFrame(
            {
                "open_time": [c.open_time for c in self.candles],
                "open": [c.open for c in self.candles],
                "high": [c.high for c in self.candles],
                "low": [c.low for c in self.candles],
                "close": [c.close for c in self.candles],
                "volume": [c.volume for c in self.candles],
            }
        )


def load_fixture(path: str | Path) -> TvFixture:
    """fixture JSON 파일을 로드한다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return TvFixture.model_validate(data)


def list_fixtures(directory: str | Path = FIXTURES_DIR) -> list[Path]:
    """디렉터리 내 fixture JSON 파일 경로를 정렬해 반환한다."""
    return sorted(Path(directory).glob("*.json"))
