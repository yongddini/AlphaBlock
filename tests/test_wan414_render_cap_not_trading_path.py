"""표시 개수 캡은 화면에만 걸린다 — 매매 경로는 전체 아카이브를 본다 (WAN-414 이유 ②).

CLAUDE.md 「트레이딩뷰는 우리 존의 정본 참조가 아니다」의 세 이유 중 ②(표시 개수)를
**동작으로** 못 박는다. 나머지 둘은 이미 테스트가 있다 — ①(존 병합)은
`tests/test_wan149_zone_merge_default.py`, ③(존 수명)은 `test_order_blocks.py::
test_max_distance_to_last_bar_filters_render_not_archive`(WAN-47).

기존 `test_zone_count_limits_selected_order_blocks`는 `params.zone_limit == 1`이라는
**라벨**만 본다 — 그 값이 실제로 어디에 걸리는지는 아무도 안 걸어 뒀다. 이 저장소가
반복해 경계한 「라벨과 동작이 어긋남」(WAN-91/95/112/123/159)의 이 축 판이라,
여기서는 같은 봉을 `zone_count` 두 값으로 돌려 **산출물의 차이**로 확인한다:

* `order_blocks`(아카이브)·`signals`·`retap_signals`(매매 경로) — `zone_count`에
  **전혀 반응하지 않는다**.
* `rendered_order_blocks`(화면 그림) — 방향별 `zone_limit`개로 **잘린다**.
* 그래서 **화면에 없는 존의 탭 시그널**이 존재한다 — 2026-09-14 DOGE 제보가 정확히
  이 모양이었다(사용자 트레이딩뷰 `Zone Count`가 `Low`=3이라 그 존이 안 보였다).

매매 경로에 캡을 걸면(= 이 문서 문단이 거짓이 되면) 아래 넷째 단언이 깨진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from strategy.models import OrderBlockParams
from strategy.order_blocks import detect_order_blocks

# `test_order_blocks.py::_BULL_BARS`의 강세 시나리오(swing_length=3) — t11에서
# 존 A(98~103)가 확정된다. 그 뒤 탭 봉 하나와, 같은 모양을 +30 올린 복제를 붙여
# **둘 다 살아 있는 강세 존 두 개**를 만든다(가격이 98 아래로 안 가므로 A는 무효화되지
# 않는다 · 존 B는 128~133으로 더 최신이라 렌더 정렬에서 A를 밀어낸다).
_BULL_BARS: list[tuple[float, float, float, float, float]] = [
    (100, 102, 90, 95, 10),
    (95, 100, 93, 98, 10),
    (98, 101, 94, 99, 10),
    (99, 103, 95, 101, 10),
    (101, 110, 100, 108, 10),
    (108, 109, 104, 106, 15),
    (106, 107, 103, 105, 20),
    (105, 106, 102, 104, 25),
    (104, 105, 100, 102, 10),
    (102, 104, 99, 101, 10),
    (101, 103, 98, 100, 10),
    (100, 115, 99, 112, 30),
]
_TAP_BAR: tuple[float, float, float, float, float] = (112, 113, 100, 110, 10)
"""존 A(98~103) 안으로 들어갔다 나온 탭 봉 — 무효화(저가 < 98)는 아니다."""

_SHIFT = 30.0

_BARS: list[tuple[float, float, float, float, float]] = [
    *_BULL_BARS,
    _TAP_BAR,
    *((o + _SHIFT, h + _SHIFT, lo + _SHIFT, c + _SHIFT, v) for o, h, lo, c, v in _BULL_BARS[1:]),
]


def _make_df(bars: Sequence[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
    )


def _params(zone_count: str) -> OrderBlockParams:
    return OrderBlockParams.model_validate(
        {
            "swing_length": 3,
            "atr_length": 3,
            "max_atr_mult": 100.0,
            "combine_obs": False,
            "zone_count": zone_count,
        }
    )


def test_zone_count_caps_render_view_only() -> None:
    """`zone_count`는 화면만 자르고 아카이브·시그널은 비트 단위로 같다."""
    df = _make_df(_BARS)
    capped = detect_order_blocks(df, _params("one"))
    full = detect_order_blocks(df, _params("high"))

    # 전제: 마지막 봉에서 강세 존 둘이 **둘 다 살아 있다**(이게 깨지면 아래 대조가 무의미).
    assert len(capped.order_blocks) == 2
    assert [ob.breaker for ob in capped.order_blocks] == [False, False]

    # ① 매매 경로(아카이브·시그널)는 `zone_count`에 반응하지 않는다.
    assert capped.order_blocks == full.order_blocks
    assert capped.signals == full.signals
    assert capped.retap_signals == full.retap_signals

    # ② 화면 그림만 방향별 `zone_limit`개로 잘린다.
    assert len(capped.rendered_order_blocks) == 1
    assert len(full.rendered_order_blocks) == 2
    # 남는 것은 가장 최신 확정 존(= 복제한 위쪽 존).
    assert capped.rendered_order_blocks[0].bottom == 128.0


def test_signal_exists_on_a_zone_the_screen_does_not_draw() -> None:
    """캡 밖 존(화면에 없음)에도 탭 시그널이 난다 — DOGE 2026-09-14 제보의 모양."""
    df = _make_df(_BARS)
    result = detect_order_blocks(df, _params("one"))

    rendered_keys = {(ob.start_time, ob.confirmed_time) for ob in result.rendered_order_blocks}
    off_screen = [
        signal
        for signal in result.signals
        if (signal.order_block.start_time, signal.order_block.confirmed_time) not in rendered_keys
    ]
    assert off_screen, "화면 밖 존의 시그널이 없으면 이 시나리오가 무너진 것이다"
    # 그 시그널의 존은 아래쪽(오래된) 존이고, 깨지지도 않았다.
    assert off_screen[0].order_block.bottom == 98.0
    assert off_screen[0].order_block.breaker is False
