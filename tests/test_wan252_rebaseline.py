"""WAN-252 재-베이스라인(2h 작업 TF 승격 — 15m·1h·2h·4h) 회귀.

라벨이 아니라 **동작**으로 고정한다(WAN-91/95/112 부류의 조용한 실패 방지):

- 새 기본값이 실제로 15m·1h·2h·4h 네 축을 돈다(CLI 파서 → 격자).
- 2h는 저장된 1h에서 무손실 리샘플로 파생된다(사전 적재 없이 로더가 투명하게 만든다).
- wan95 채택 성과가 2h를 포함하고 TF 정렬(`_TF_ORDER`)이 15m<1h<2h<4h로 찍힌다.
- **범위 밖 불변**: 수집 유니버스 TF(2h는 파생이라 미수집) · 라이브 감시 TF(WAN-191의
  15m·1h·4h) · 엔진 토대(`ConfluenceParams()`)는 손대지 않았다.
"""

from __future__ import annotations

import os

import pytest

from backtest import harness
from backtest.run import build_parser, grid_from_args
from backtest.wan95_zone_limit_report import _TF_ORDER
from config.settings import (
    _default_live_signal_timeframes,
    _default_timeframes,
)

_FOUR_TFS = ("15m", "1h", "2h", "4h")
_DB = "data/ohlcv.db"


# --------------------------------------------------------------- 채택 좌표(TF)


def test_default_timeframes_promote_2h() -> None:
    """작업 TF = 15m·1h·2h·4h — WAN-182의 3축에 2h가 더해졌다(순서 유지)."""
    assert harness.DEFAULT_TIMEFRAMES == _FOUR_TFS
    # 2h는 1h와 4h 사이에 온다(리샘플 배수 순).
    assert harness.DEFAULT_TIMEFRAMES.index("2h") == 2


def test_bare_cli_runs_four_timeframes_including_2h() -> None:
    """인자 없는 `backtest.run`이 실제로 네 TF를 돈다(파서 산출물에서 확인)."""
    grid = grid_from_args(build_parser().parse_args([]))
    assert grid.timeframes == _FOUR_TFS


def test_explicit_tf_still_wins() -> None:
    """`--tf`를 명시하면 그대로다 — 2h 승격이 명시 축을 덮지 않는다."""
    grid = grid_from_args(build_parser().parse_args(["--tf", "1h,4h"]))
    assert grid.timeframes == ("1h", "4h")


# ------------------------------------------------------------ wan95 정렬 · 승격


def test_wan95_tf_order_includes_2h_between_1h_and_4h() -> None:
    """wan95 손익표 정렬이 2h를 1h와 4h 사이에 놓는다(누락 시 정렬이 NaN으로 깨진다)."""
    assert _TF_ORDER["1h"] < _TF_ORDER["2h"] < _TF_ORDER["4h"]
    # 1d는 여전히 맨 뒤(표본 미달 제외 유지, 존재만).
    assert _TF_ORDER["2h"] < _TF_ORDER["1d"]


# --------------------------------------------------------- 범위 밖 불변(TF 축)


def test_collection_timeframes_do_not_add_2h() -> None:
    """수집 TF는 2h를 담지 않는다 — 2h는 1h에서 파생(온더플라이)이라 저장 대상이 아니다."""
    assert "2h" not in _default_timeframes()


def test_live_signal_timeframes_unchanged_by_2h_promotion() -> None:
    """라이브 감시 TF는 WAN-191의 15m·1h·4h 그대로 — 2h 승격은 측정 축만 넓힌다."""
    assert tuple(_default_live_signal_timeframes()) == ("15m", "1h", "4h")


# ------------------------------------------------------------- 2h 데이터 파생


@pytest.mark.skipif(not os.path.exists(_DB), reason="ohlcv.db 없음(합성/CI)")
def test_2h_resamples_on_the_fly_from_stored_1h() -> None:
    """2h가 저장 행 없이 1h에서 파생되고 짝수시(UTC)로 정렬된다(사전 적재 불필요).

    ⚠️ skip 판정은 **파일 존재가 아니라 실제 데이터 유무**로 한다(회귀 테스트 관례,
    `test_run_regression_real_data.py`). CI는 빈 `data/ohlcv.db`를 만들 수 있어 파일은
    있지만 1h 봉이 0행이면 2h도 빈 프레임이다 — 그 경우 단언이 아니라 skip이 맞다.
    """
    from data.storage import OhlcvStore

    store = OhlcvStore(_DB)
    df = store.load("BTC/USDT:USDT", "2h")
    if df.empty:
        pytest.skip("BTC 1h 실데이터가 없어 2h 파생을 검증할 수 없습니다(CI 기본).")
    # 2h 봉의 open_time은 전부 UTC 짝수시(00,02,04,…) = 2시간 격자에 정렬.
    two_h_ms = 2 * 60 * 60 * 1000
    assert (df["open_time"] % two_h_ms == 0).all()
    # 봉 간격은 항상 2시간의 양의 배수이고, 인접 봉 최소 간격은 정확히 2시간(리샘플 무결성).
    diffs = [int(d) for d in df["open_time"].diff().dropna().unique()]
    assert diffs and all(d > 0 and d % two_h_ms == 0 for d in diffs)
    assert min(diffs) == two_h_ms
