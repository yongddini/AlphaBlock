"""캔들 + 오더블록 오버레이 차트 렌더링.

TradingView 스크린샷과 나란히 비교할 수 있도록, 지정 OHLCV 구간을 캔들
차트로 그리고 탐지된 오더블록을 박스로 오버레이해 PNG로 저장한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 헤드리스 환경(서버·CI)에서도 렌더링 가능하도록.

import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from strategy.models import OrderBlock, OrderBlockDirection  # noqa: E402

_KOREAN_FONT_CANDIDATES = ("AppleGothic", "Malgun Gothic", "NanumGothic", "Noto Sans CJK KR")
_available = {f.name for f in font_manager.fontManager.ttflist}
for _name in _KOREAN_FONT_CANDIDATES:
    if _name in _available:
        matplotlib.rcParams["font.family"] = _name
        break
matplotlib.rcParams["axes.unicode_minus"] = False

_BULL_COLOR = "#2e7d32"
_BEAR_COLOR = "#c62828"
_BULL_ZONE_COLOR = "#66bb6a"
_BEAR_ZONE_COLOR = "#ef5350"


def render_order_block_chart(
    df: pd.DataFrame,
    order_blocks: Sequence[OrderBlock],
    output_path: str | Path,
    *,
    title: str = "",
) -> Path:
    """캔들 차트에 오더블록을 오버레이해 PNG로 저장하고 경로를 반환한다.

    `df`는 `open_time`, `open`, `high`, `low`, `close` 컬럼을 가진
    시간순 정렬 OHLCV DataFrame이어야 한다.
    """
    frame = df.sort_values("open_time").reset_index(drop=True)
    n = len(frame)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(max(8.0, n * 0.4), 6.0))

    if n > 0:
        step = int(frame["open_time"].diff().median()) if n > 1 else 1
        bar_width = max(step * 0.6, 1)
        for i in range(n):
            row = frame.iloc[i]
            x = float(row["open_time"])
            color = _BULL_COLOR if row["close"] >= row["open"] else _BEAR_COLOR
            ax.plot([x, x], [row["low"], row["high"]], color=color, linewidth=1, zorder=2)
            body_bottom = min(row["open"], row["close"])
            body_height = max(abs(row["close"] - row["open"]), (row["high"] - row["low"]) * 0.01)
            ax.add_patch(
                Rectangle(
                    (x - bar_width / 2, body_bottom),
                    bar_width,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                    zorder=3,
                )
            )
        x_end = float(frame["open_time"].iloc[-1]) + step
    else:
        x_end = 1.0

    for ob in order_blocks:
        is_bull = ob.direction == OrderBlockDirection.BULLISH
        color = _BULL_ZONE_COLOR if is_bull else _BEAR_ZONE_COLOR
        end = ob.break_time if ob.break_time is not None else x_end
        ax.add_patch(
            Rectangle(
                (ob.start_time, ob.bottom),
                max(end - ob.start_time, 1),
                ob.top - ob.bottom,
                facecolor=color,
                alpha=0.15 if ob.breaker else 0.3,
                edgecolor=color,
                linestyle="--" if ob.breaker else "-",
                linewidth=1.2,
                zorder=1,
            )
        )

    ax.set_title(title or "오더블록 오버레이")
    ax.set_xlabel("open_time (ms)")
    ax.set_ylabel("price")
    if n > 0:
        ax.set_xlim(
            float(frame["open_time"].iloc[0]) - bar_width,
            max(x_end, float(frame["open_time"].iloc[-1])) + bar_width,
        )
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)
    return output
