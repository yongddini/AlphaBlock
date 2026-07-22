"""WAN-159: 존폭 필터를 채택 기본값으로 — 라벨이 아니라 **동작**으로 고정한다.

기본값이 `None`(꺼짐) → `1.28`(좁은 존만)으로 옮겨졌다(`docs/decisions/wan159.md`). 이
전환의 급소는 **끄기(`None`)와 미지정(채택 기본값)이 갈라진다**는 것이다 — 옛 규약에서 둘은
같은 `None`이었다. 이 파일이 지키는 것:

1. **채택 기본값은 1.28이다.**
2. **`build_params(None)` = 끄기, 미지정(`UNSET`) = 채택 기본값** — 이래야 「필터 끔」 라벨을
   단 채 1.28로 도는 이중 필터를 피한다(WAN-91/95/112/123 부류의 조용한 실패).
3. **`UNSET`은 피클을 넘어도 싱글턴** — `--jobs` 병렬에서 워커로 피클되는데 깨지면 「필터 끔」
   축이 워커에서만 다르게 해석된다.
4. **CLI `none` = 끄기, 인자 미지정 = 채택 기본값(1.28)** — 둘이 갈라진다.
5. **A안(종가)은 필터를 강제로 끈다** — 필드를 안 읽으므로 양수 문턱은 거부, 끄기는 허용.
6. **`pin_zone_width`는 옛 리포트를 끄기로 고정**한다(파급 처리의 헬퍼).
"""

from __future__ import annotations

import pickle

import pytest

from backtest.harness import (
    BASELINE_FILL,
    LEGACY_MAX_ZONE_WIDTH_ATR,
    UNSET,
    build_params,
    pin_zone_width,
)
from backtest.run import Grid, build_parser, grid_from_args, iter_combos
from strategy.models import ConfluenceParams


def test_adopted_default_is_1_28() -> None:
    assert ConfluenceParams().max_zone_width_atr == 1.28
    assert build_params().max_zone_width_atr == 1.28
    assert LEGACY_MAX_ZONE_WIDTH_ATR is None


def test_build_params_none_turns_off_unset_inherits_default() -> None:
    """완료기준 3: `None`은 끄기, 미지정은 채택 기본값."""
    assert build_params(max_zone_width_atr=None).max_zone_width_atr is None  # 끄기
    assert build_params().max_zone_width_atr == 1.28  # 미지정 = 채택 기본값
    assert build_params(max_zone_width_atr=1.15).max_zone_width_atr == 1.15
    # base가 켜 둔 필터를 미지정은 물려받고, 명시적 None은 끈다.
    base = ConfluenceParams(max_zone_width_atr=1.24)
    assert build_params(base=base).max_zone_width_atr == 1.24
    assert build_params(max_zone_width_atr=None, base=base).max_zone_width_atr is None


def test_unset_survives_pickle_as_a_singleton() -> None:
    """`--jobs` 병렬이 축을 워커로 피클한다 — 언피클 후에도 같은 싱글턴이라야 비교가 산다."""
    assert pickle.loads(pickle.dumps(UNSET)) is UNSET
    grid = Grid(
        symbols=("BTC/USDT:USDT",),
        timeframes=("1h",),
        entry_modes=("zone_limit",),
        take_profit_rs=(1.5,),
        offsets_bps=(2.0,),
        fills=(BASELINE_FILL,),
    )
    restored = pickle.loads(pickle.dumps(grid))
    assert restored.max_zone_widths_atr == (UNSET,)
    assert restored.max_zone_widths_atr[0] is UNSET


def test_cli_none_is_off_and_unspecified_is_the_adopted_default() -> None:
    """CLI `none`(끄기)과 인자 미지정(채택 기본값 1.28)이 갈라진다(WAN-159)."""
    unspecified = grid_from_args(build_parser().parse_args(["--symbol", "BTCUSDT"]))
    assert unspecified.max_zone_widths_atr == (UNSET,)
    (combo,) = iter_combos(unspecified)
    assert build_params(max_zone_width_atr=combo.max_zone_width_atr).max_zone_width_atr == 1.28

    off = grid_from_args(
        build_parser().parse_args(["--symbol", "BTCUSDT", "--max-zone-width-atr", "none"])
    )
    assert off.max_zone_widths_atr == (None,)
    (off_combo,) = iter_combos(off)
    assert build_params(max_zone_width_atr=off_combo.max_zone_width_atr).max_zone_width_atr is None


def test_close_entry_forces_the_filter_off_but_rejects_a_positive_threshold() -> None:
    # 미지정/끄기는 A안에서 안전하게 꺼진다.
    assert build_params(entry_mode="close").max_zone_width_atr is None
    assert build_params(entry_mode="close", max_zone_width_atr=None).max_zone_width_atr is None
    # 양수 문턱은 "A안이 조용히 무시하는 필터를 켰다"는 오해라 거부한다.
    with pytest.raises(ValueError, match="존폭"):
        build_params(entry_mode="close", max_zone_width_atr=1.24)


def test_close_grid_allows_off_but_rejects_a_threshold() -> None:
    """혼합 격자(close,zone_limit)에서 끄기는 무해하므로 허용, 양수 문턱은 거부."""
    Grid(
        symbols=("BTC/USDT:USDT",),
        timeframes=("1h",),
        entry_modes=("close", "zone_limit"),
        take_profit_rs=(1.5,),
        offsets_bps=(0.0,),
        fills=(BASELINE_FILL,),
        max_zone_widths_atr=(None,),
    )
    with pytest.raises(ValueError, match="존폭"):
        Grid(
            symbols=("BTC/USDT:USDT",),
            timeframes=("1h",),
            entry_modes=("close",),
            take_profit_rs=(1.5,),
            offsets_bps=(0.0,),
            fills=(BASELINE_FILL,),
            max_zone_widths_atr=(1.28,),
        )


def test_pin_zone_width_turns_the_filter_off_by_default() -> None:
    """옛 리포트 고정 헬퍼 — 기본값은 끄기(`LEGACY_MAX_ZONE_WIDTH_ATR`)."""
    on = ConfluenceParams()  # 1.28
    assert pin_zone_width(on).max_zone_width_atr is None
    assert pin_zone_width(on, 1.15).max_zone_width_atr == 1.15
    # 다른 필드는 손대지 않는다.
    assert pin_zone_width(on).zone_limit_offset_bps == on.zone_limit_offset_bps
