"""WAN-405: LuxAlgo 탐지기 이식 — 파인 원본 대조 + 「알려진 차이」 고정.

🚨 이 파일이 지키는 것은 셋이다:

1. **파인과 조건식이 같다** — 전사 파인(`strategy/reference/luxalgo_ob_detector.pine`)에
   적힌 조건이 파이썬 이식에 실제로 있는가. WAN-400이 「이슈 전제가 틀렸다」를 잡은 방법이다.
2. **「알려진 차이」가 사라지지 않는다** — 우리 소멸은 원본 루프의 결함을 재현하지 않아
   **더 많이 죽인다**. 그걸 나중에 버그로 오해해 「고치면」 원본에서 멀어진다.
3. **끄면 비트 재현** — 채택 경로(`detector="flux"`)가 이 축이 생기기 전과 같은 존을 낸다.
"""

from __future__ import annotations

import pathlib

import pandas as pd
import pytest

from backtest import harness
from strategy.lux_order_blocks import (
    LuxOrderBlockParams,
    _pivot_high,
    detect_lux_order_blocks,
)
from strategy.models import OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import detect_order_blocks

PINE = pathlib.Path("strategy/reference/luxalgo_ob_detector.pine")


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    """`(open, high, low, close, volume)` 목록을 1h 봉 프레임으로."""
    step = 3_600_000
    return pd.DataFrame(
        {
            "open_time": [i * step for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [r[4] for r in rows],
            "closed": [True] * len(rows),
        }
    )


# --------------------------------------------------------------------------- #
# 1. 파인 원본 대조 (완료기준 9)
# --------------------------------------------------------------------------- #


def test_pine_reference_is_vendored() -> None:
    """원문 사양이 저장소에 있어야 대조가 성립한다."""
    assert PINE.exists(), f"{PINE}가 없습니다 — 대조할 원본이 없으면 이식이 표류한다."


@pytest.mark.parametrize(
    "fragment",
    [
        "ta.pivothigh(volume, length, length)",  # 존을 만드는 사건
        "high[length] > ta.highest(high, length)",  # 추세 상태 os
        "low[length]  < ta.lowest(low,  length)",
        "hl2[length], low[length]",  # 강세 존 = 봉의 아래 절반
        "high[length], hl2[length]",  # 약세 존 = 봉의 위 절반
        "ta.lowest(low,  length)",  # 소멸 비교 대상
        "ta.highest(high, length)",
    ],
)
def test_pine_carries_the_conditions_we_ported(fragment: str) -> None:
    """이식의 근거가 되는 조건식이 **파일에 실제로** 있는가."""
    assert fragment in PINE.read_text(), fragment


def test_python_port_matches_the_pine_zone_geometry() -> None:
    """강세 존 = `[hl2[length], low[length]]` — 파인이 적은 그 좌표인가.

    거래량 피벗을 **한 봉에만** 세우고 그 앞뒤를 평평하게 둬서 존이 정확히 어디에
    생기는지 눈으로 셀 수 있게 만든다.
    """
    length = 5
    rows = [(10.0, 11.0, 9.0, 10.0, 1.0) for _ in range(30)]
    # 하락 추세를 만들어 os == 1(저점형)로 보낸다.
    for i in range(6, 14):
        rows[i] = (10.0, 11.0 - i * 0.2, 9.0 - i * 0.2, 10.0 - i * 0.2, 1.0)
    pivot = 14
    rows[pivot] = (8.0, 9.0, 6.0, 7.0, 99.0)  # 거래량 국소 최대
    frame = _bars(rows)
    result = detect_lux_order_blocks(frame, LuxOrderBlockParams(length=length))
    bulls = [z for z in result.order_blocks if z.direction is OrderBlockDirection.BULLISH]
    assert bulls, "거래량 피벗 봉에서 강세 존이 나와야 한다."
    zone = next(z for z in bulls if z.start_time == frame["open_time"][pivot])
    assert zone.bottom == pytest.approx(6.0)  # low[length]
    assert zone.top == pytest.approx((9.0 + 6.0) / 2)  # hl2[length]
    # 생성 지연은 **고정 length봉**이다(파인이 `n - length`를 왼쪽 변으로 쓴다).
    assert zone.confirmed_time == frame["open_time"][pivot + length]


def test_pivot_high_requires_strictly_greater_on_both_sides() -> None:
    """`ta.pivothigh`는 동률을 피벗으로 세지 않는다 — 그 규약을 못 박는다."""
    values = [1.0, 2.0, 3.0, 2.0, 1.0]
    assert _pivot_high(values, t=4, length=2)
    tie = [1.0, 3.0, 3.0, 2.0, 1.0]
    assert not _pivot_high(tie, t=4, length=2)


# --------------------------------------------------------------------------- #
# 2. 「알려진 차이」 — 원본보다 더 많이 죽인다 (★사용자 결정 (가))
# --------------------------------------------------------------------------- #


def _v_shape(dip: int | None = None) -> pd.DataFrame:
    """하락 → 바닥 → 상승의 V 픽스처.

    LuxAlgo의 `os`는 **스윙 저점**(`low[length] < lowest(low, length)`)에서만 1이 되므로
    단조 하락으로는 강세 존이 안 생긴다 — 그래서 V가 필요하다. 거래량 피벗은 봉 20에
    세우고(확정은 `20 + length`), `dip`을 주면 그 봉의 저가만 존 바닥 아래로 내린다.
    """
    rows: list[tuple[float, float, float, float, float]] = []
    for i in range(40):
        if i <= 3:
            base = 100.0
        elif i <= 8:
            base = 100.0 - (i - 3) * 3.0
        else:
            base = 85.0 + (i - 8) * 2.0
        rows.append((base, base + 1.0, base - 1.0, base, 1.0))
    o, h, low, c, _ = rows[20]
    rows[20] = (o, h, low, c, 99.0)  # 거래량 국소 최대
    if dip is not None:
        o, h, _, c, v = rows[dip]
        rows[dip] = (o, h, rows[20][2] - 0.5, c, v)
    return _bars(rows)


def test_birth_mitigation_kills_zones_that_price_already_passed() -> None:
    """탄생 시점 소급 검사 — 존이 확정되는 순간 이미 바닥이 뚫렸으면 곧바로 죽는다.

    🚨 이건 우리 채택 탐지기에 **없는 동작**이고 LuxAlgo 원본의 정의다(존 바닥은
    `low[length]`인데 소멸 검사는 그 **이후 length봉**을 보고, 생성과 소멸이 **같은 봉에서
    연달아** 돈다). 반사실 팔에서 같은 존이 그 봉에 안 죽는 것을 함께 확인해 「그 규칙이
    실제로 걸렸다」를 동작으로 고정한다.
    """
    frame = _v_shape(dip=24)
    on = detect_lux_order_blocks(frame, LuxOrderBlockParams(length=5))
    off = detect_lux_order_blocks(frame, LuxOrderBlockParams(length=5, birth_mitigation=False))
    zone_on = next(z for z in on.order_blocks if z.direction is OrderBlockDirection.BULLISH)
    zone_off = next(z for z in off.order_blocks if z.direction is OrderBlockDirection.BULLISH)
    assert zone_on.break_time == zone_on.confirmed_time, "확정 봉에서 곧바로 죽어야 한다."
    assert zone_off.break_time is not None
    assert zone_off.break_time > zone_off.confirmed_time, (
        "반사실 팔에서는 확정 봉을 건너뛰어야 한다(규칙이 실제로 걸렸다는 증거)."
    )
    # 두 팔의 **존 자체**는 같다 — 바뀌는 것은 죽는 시각뿐이다.
    assert (zone_on.top, zone_on.bottom, zone_on.start_time) == (
        zone_off.top,
        zone_off.bottom,
        zone_off.start_time,
    )


def test_mitigation_uses_the_rolling_window_not_just_this_bar() -> None:
    """확정 이후로는 우리 규칙과 **글자 그대로 같다** — 바닥을 뚫은 **그 봉**에 죽는다.

    📌 그래서 「length봉은 지울 기회가 length번」이지 「length봉 뒤에 지워진다」가 **아니다**
    (이식에 지연을 넣으면 이 테스트가 깨진다). 근거는 귀납이다: 탄생 시점 검사를 통과했다면
    그 창의 최저가 이미 존 바닥 위이므로, 이후 창이 한 칸 밀려 최저가 바닥 아래로 가려면
    **새로 들어온 그 봉**이 뚫은 것이다.
    """
    frame = _v_shape(dip=30)  # 확정(25) 뒤에 뚫는다
    result = detect_lux_order_blocks(frame, LuxOrderBlockParams(length=5))
    zone = next(z for z in result.order_blocks if z.direction is OrderBlockDirection.BULLISH)
    assert zone.break_time == int(frame["open_time"][30]), "뚫은 **그 봉**에 죽어야 한다."


def test_lux_zone_death_is_immediate_no_breaker_limbo() -> None:
    """LuxAlgo에는 breaker 구간이 없다 — 무효화가 곧 소멸이다(FluxCharts의 2단계와 다르다)."""
    result = detect_lux_order_blocks(_v_shape(dip=30), LuxOrderBlockParams(length=5))
    dead = [z for z in result.order_blocks if z.break_time is not None]
    assert dead
    assert all(z.swept_time == z.break_time for z in dead)


# --------------------------------------------------------------------------- #
# 3. 축을 끄면 비트 재현 · 켜면 실제로 다르다 (검산 (b))
# --------------------------------------------------------------------------- #


def test_flux_detector_is_untouched_by_the_new_axis() -> None:
    """채택 경로(`detector="flux"`)는 축이 생기기 전과 **같은 함수**를 탄다."""
    rows = [
        (10.0 + i % 3, 12.0 + i % 5, 8.0 - i % 4, 10.0 + i % 2, 1.0 + i % 7) for i in range(120)
    ]
    frame = _bars(rows)
    market = harness.MarketData(
        symbol="TEST", timeframe="1h", htf_df=frame, df_1m=pd.DataFrame(), funding_rates=[]
    )
    direct = detect_order_blocks(frame, OrderBlockParams())
    through = harness.detect_order_blocks(market)
    assert [z.model_dump() for z in through.order_blocks] == [
        z.model_dump() for z in direct.order_blocks
    ]


def test_lux_rejects_combine_obs() -> None:
    """LuxAlgo에는 병합이 없다 — 조용히 무시하면 「병합 켬」 라벨을 단 채 안 병합된다."""
    frame = _bars([(10.0, 11.0, 9.0, 10.0, 1.0) for _ in range(20)])
    market = harness.MarketData(
        symbol="TEST", timeframe="1h", htf_df=frame, df_1m=pd.DataFrame(), funding_rates=[]
    )
    with pytest.raises(ValueError, match="combine_obs"):
        harness.detect_order_blocks(market, OrderBlockParams(combine_obs=True), detector="lux")


def test_lux_is_registered_as_an_engine_source_file() -> None:
    """탐지기는 백테 수치를 만든다 — 소스 지문에 안 들어가면 캐시가 옛 결과를 내준다."""
    from backtest.trade_store import ENGINE_SOURCE_FILES, scan_engine_source_tree

    assert "strategy/lux_order_blocks.py" in ENGINE_SOURCE_FILES
    assert scan_engine_source_tree() == []
