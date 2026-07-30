"""WAN-120 CSV의 공유 단이 WAN-119 CSV와 **비트 단위로 일치**하는지 (커밋된 원본으로).

이 리포트의 모든 주장은 "`L1`·`L2`·`L2i`가 WAN-119의 같은 단이라 그 CSV와 검산된다"에
얹혀 있다 — 그게 참이라야 `L2c`와의 차이를 **지연 한 칸의 몫**이라고 부를 수 있다.
`tests/test_wan120_strict_causal_band.py`는 두 모듈의 **파라미터**가 같음을 고정하지만,
그것만으로는 **실제로 같은 수가 나왔는지**를 말하지 못한다(엔진이 중간에 바뀌면 파라미터가
같아도 수가 갈린다). 그래서 커밋된 두 CSV를 직접 맞춰 본다.

두 CSV 모두 저장소에 커밋돼 있으므로 CI에서 그대로 돈다(실데이터 DB가 필요 없다).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

#: 두 리포트가 공유하는 단 — WAN-120이 `L2c`만 얹었다.
SHARED_LEVELS = ("L1", "L2", "L2i")

#: 격자 한 셀을 특정하는 좌표.
KEYS = ["symbol", "timeframe", "segment", "fill", "seed", "level"]

#: 비교할 지표 전부. 하나라도 빠뜨리면 "비트 단위 일치"가 반쪽 주장이 된다.
METRICS = (
    "num_trades",
    "win_rate",
    "total_return",
    "max_drawdown",
    "sharpe",
    "profit_factor",
    "mean_r",
    "fill_rate",
    "eligible_setups",
    "num_filled",
)

WAN119_CSV = Path("backtest/reports/wan119_intrabar_live_band.csv")
WAN120_CSV = Path("backtest/reports/wan120_strict_causal_band.csv")


def _shared(path: Path) -> pd.DataFrame:
    if not path.exists():
        pytest.skip(f"{path}가 없어 건너뜁니다.")
    frame = pd.read_csv(path)
    return frame[frame["level"].isin(SHARED_LEVELS)].set_index(KEYS).sort_index()


@pytest.fixture(scope="module")
def pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    return _shared(WAN119_CSV), _shared(WAN120_CSV)


def test_shared_rungs_cover_the_same_grid(pair: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """두 표가 같은 셀 집합을 본다 — 좌표가 어긋나면 비교 자체가 성립하지 않는다."""
    wan119, wan120 = pair
    assert len(wan120) > 0, "전제: WAN-120 CSV에 공유 단 행이 있다"
    assert wan119.index.equals(wan120.index)


@pytest.mark.parametrize("metric", METRICS)
def test_shared_rungs_match_wan119_bit_for_bit(
    pair: tuple[pd.DataFrame, pd.DataFrame], metric: str
) -> None:
    """공유 단의 모든 지표가 **완전히 같은 값**이다.

    깨지면 둘 중 하나다: (a) 엔진이 바뀌어 WAN-119 수치가 재현되지 않거나 — 그러면 WAN-119의
    결론도 다시 봐야 한다 — (b) WAN-120 사다리가 그 단을 다르게 조립했거나. 어느 쪽이든
    `L2c` 대조는 무효다. 부동소수 여유를 주지 않는 이유는 **같은 코드로 같은 입력을 돌린
    결과**라 정확히 같아야 하기 때문이다(다르면 그 자체가 신호다).
    """
    wan119, wan120 = pair
    assert (wan119[metric] - wan120[metric]).abs().max() == 0.0
