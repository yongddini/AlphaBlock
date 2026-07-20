"""WAN-130 확인 격자 요약 — CSV를 심볼평균 표로 접는다(일회성 집계, 엔진 무관).

새 리포트 모듈(`backtest/wanNN_*.py`)이 아니다 — 숫자는 전부 범용 CLI
(`python -m backtest.run`, WAN-101)가 냈고, 이 스크립트는 그 CSV를 `groupby`로 접어
문서에 붙일 표만 찍는다. 엔진·파라미터를 하나도 만들지 않으므로 결과를 바꿀 수 없다.

```
uv run python scripts/wan130_summarize.py out/wan130/wan130_confirm.csv
```
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def summarize(path: Path) -> pd.DataFrame:
    """(TF × 구간 × 포지션 정책)별 심볼평균 — 채택 판단이 읽는 축(WAN-111/114와 같은 순서)."""
    frame = pd.read_csv(path)
    return frame.groupby(
        ["timeframe", "segment", "position_mode"], as_index=False, dropna=False
    ).agg(
        total_return=("total_return", "mean"),
        plus_symbols=("total_return", lambda s: int((s > 0).sum())),
        symbols=("total_return", "count"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        num_trades=("num_trades", "mean"),
        fill_rate=("fill_rate", "mean"),
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    view = summarize(Path(argv[1]))
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(view.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
