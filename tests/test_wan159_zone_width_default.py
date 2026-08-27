"""WAN-159 → **WAN-384**: 존폭 필터의 기본값 규약 — 라벨이 아니라 **동작**으로 고정한다.

🔁 **기본값이 두 번 움직였다** — WAN-159가 `None`(꺼짐) → `1.28`(좁은 존만)으로 올렸고
**WAN-384가 다시 `None`으로 되돌렸다**(`docs/decisions/wan384.md`). 그래도 **끄기(명시적
`None`)와 미지정(`UNSET`)을 가르는 규약은 그대로 산다** — 지금은 두 결과가 같아 보이지만
`base`가 **핀된 파라미터**(옛 리포트의 `1.28`)면 갈리기 때문이다. 이 파일이 지키는 것:

1. **채택 기본값은 꺼짐이다**(WAN-384) — 그리고 옛 핀 상수는 `1.28`이다.
2. **`build_params(None)` = 끄기, 미지정(`UNSET`) = `base`를 물려받는다** — 이래야 옛 리포트
   핀 위에서 「필터 끔」 라벨을 단 채 1.28로 도는 이중 필터를 피한다(WAN-91/95/112/123 부류).
3. **`UNSET`은 피클을 넘어도 싱글턴** — `--jobs` 병렬에서 워커로 피클되는데 깨지면 「필터 끔」
   축이 워커에서만 다르게 해석된다.
4. **CLI `none` = 끄기, 인자 미지정 = 채택 기본값** — 축의 배선 자체가 산다.
5. **A안(종가)은 필터를 강제로 끈다** — 필드를 안 읽으므로 양수 문턱은 거부, 끄기는 허용.
6. **`pin_zone_width`의 두 세대** — 인자 없이 부르면 끄기(무동작), `LEGACY_ZONE_WIDTH_FILTER_ON`을
   주면 필터 켠 옛 리포트를 그 시절 문턱에 고정한다.
"""

from __future__ import annotations

import pickle

from backtest.harness import (
    BASELINE_FILL,
    LEGACY_MAX_ZONE_WIDTH_ATR,
    LEGACY_ZONE_WIDTH_FILTER_ON,
    UNSET,
    build_params,
    pin_zone_width,
)
from backtest.run import Grid, build_parser, grid_from_args, iter_combos
from strategy.models import ConfluenceParams


def test_adopted_default_is_off_and_the_two_legacy_pins_differ() -> None:
    """WAN-384: 채택 기본값은 꺼짐이고, 옛 핀은 **두 세대**다."""
    assert ConfluenceParams().max_zone_width_atr is None
    assert build_params().max_zone_width_atr is None
    # WAN-158 이전(필터가 아예 없던 엔진) — 지금은 기본값과 같아 무동작이다.
    assert LEGACY_MAX_ZONE_WIDTH_ATR is None
    # WAN-159~383(필터를 켠 채 결론을 낸 리포트) — 이쪽을 명시해야 실제로 고정된다.
    assert LEGACY_ZONE_WIDTH_FILTER_ON == 1.28


def test_build_params_none_turns_off_unset_inherits_base() -> None:
    """WAN-159 완료기준 3의 규약 — WAN-384 이후에도 `base` 위에서 그대로 산다."""
    assert build_params(max_zone_width_atr=None).max_zone_width_atr is None  # 끄기
    assert build_params().max_zone_width_atr is None  # 미지정 = 채택 기본값(= 꺼짐)
    assert build_params(max_zone_width_atr=1.15).max_zone_width_atr == 1.15
    # 🚨 여기가 규약이 살아 있는 자리다 — base가 켜 둔 필터를 미지정은 **물려받고**
    # 명시적 None은 **끈다**. 옛 리포트 핀(1.28) 위에서 이 구분이 이중 필터를 막는다.
    base = ConfluenceParams(max_zone_width_atr=1.24)
    assert build_params(base=base).max_zone_width_atr == 1.24
    assert build_params(max_zone_width_atr=None, base=base).max_zone_width_atr is None
    pinned = ConfluenceParams(max_zone_width_atr=LEGACY_ZONE_WIDTH_FILTER_ON)
    assert build_params(base=pinned).max_zone_width_atr == 1.28


def test_unset_survives_pickle_as_a_singleton() -> None:
    """`--jobs` 병렬이 축을 워커로 피클한다 — 언피클 후에도 같은 싱글턴이라야 비교가 산다."""
    assert pickle.loads(pickle.dumps(UNSET)) is UNSET
    grid = Grid(
        symbols=("BTC/USDT:USDT",),
        timeframes=("1h",),
        take_profit_rs=(1.5,),
        offsets_bps=(2.0,),
        fills=(BASELINE_FILL,),
    )
    restored = pickle.loads(pickle.dumps(grid))
    assert restored.max_zone_widths_atr == (UNSET,)
    assert restored.max_zone_widths_atr[0] is UNSET


def test_cli_none_is_off_and_unspecified_is_the_adopted_default() -> None:
    """CLI `none`(끄기)과 인자 미지정(채택 기본값 = WAN-384 꺼짐) — 축의 배선을 본다.

    ⚠️ 지금은 **두 결과가 같다**(둘 다 꺼짐). 갈리는 자리는 `base`가 핀된 파라미터일
    때이고 그건 `test_build_params_none_turns_off_unset_inherits_base`가 지킨다.
    """
    unspecified = grid_from_args(build_parser().parse_args(["--symbol", "BTCUSDT"]))
    assert unspecified.max_zone_widths_atr == (UNSET,)
    (combo,) = iter_combos(unspecified)
    assert combo.max_zone_width_atr is UNSET  # 라벨이 아니라 센티넬 그 자체로 실려 간다.
    assert build_params(max_zone_width_atr=combo.max_zone_width_atr).max_zone_width_atr is None

    off = grid_from_args(
        build_parser().parse_args(["--symbol", "BTCUSDT", "--max-zone-width-atr", "none"])
    )
    assert off.max_zone_widths_atr == (None,)
    (off_combo,) = iter_combos(off)
    assert build_params(max_zone_width_atr=off_combo.max_zone_width_atr).max_zone_width_atr is None


def test_pin_zone_width_has_two_legacy_generations() -> None:
    """옛 리포트 고정 헬퍼 — 인자 없이 부르면 끄기(WAN-384 이후 무동작), 명시하면 그 문턱."""
    on = ConfluenceParams(max_zone_width_atr=1.28)
    assert pin_zone_width(on).max_zone_width_atr is None
    assert pin_zone_width(on, 1.15).max_zone_width_atr == 1.15
    # 🚨 WAN-384 이후 「필터를 켠 채 낸 리포트」를 고정하려면 이쪽을 **명시**해야 한다.
    off = ConfluenceParams()  # 채택 기본값 = 꺼짐
    assert pin_zone_width(off, LEGACY_ZONE_WIDTH_FILTER_ON).max_zone_width_atr == 1.28
    assert pin_zone_width(off).max_zone_width_atr is None  # 인자 생략 = 아무 일도 안 한다
    # 다른 필드는 손대지 않는다.
    assert pin_zone_width(on).zone_limit_offset_bps == on.zone_limit_offset_bps
