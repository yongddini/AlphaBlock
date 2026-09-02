"""WAN-384: 존폭 필터 폐지 — 라벨이 아니라 **동작**으로 고정한다.

기본값이 `1.28`(좁은 존만) → `None`(꺼짐)으로 되돌아갔다(`docs/decisions/wan384.md`).
이 전환의 급소는 **핀의 방향이 WAN-132/149/159와 반대**라는 것이다 —
`LEGACY_MAX_ZONE_WIDTH_ATR`는 이미 `None`이라 이번엔 아무것도 고정하지 못하고, 보존해야
하는 것은 **필터를 켠 채로 결론을 낸 리포트**다. 이 파일이 지키는 것:

1. **채택 기본값은 꺼짐이고, 그 실행이 실제로 넓은 존까지 매매한다**(라벨이 아니라 후보 집합).
2. **필터는 옵트인으로 살아 있다** — 켜면 후보가 실제로 줄어든다.
3. **옛 핀 상수가 둘로 갈린다** — `LEGACY_MAX_ZONE_WIDTH_ATR`(= `None`, 무동작) vs
   `LEGACY_ZONE_WIDTH_FILTER_ON`(= `1.28`, 실제로 고정).
4. **필터를 켠 채 결론을 낸 모듈이 전부 그 핀을 든다** — 하나라도 빠지면 조용히 새 엔진으로
   다시 돌아 본문과 어긋난다(WAN-91/95/112/123/159 부류). 새 모듈이 생겼는데 핀도 예외
   등록도 안 하면 이 테스트가 **먼저** 깨진다.
5. **북 후보 생성 경로가 실제로 그 값을 넘긴다** — 호출부를 가로채 인자로 확인한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from backtest import harness
from backtest.harness import (
    LEGACY_MAX_ZONE_WIDTH_ATR,
    LEGACY_ZONE_WIDTH_FILTER_ON,
    build_params,
    pin_zone_width,
)
from backtest.run import build_parser, grid_from_args, iter_combos
from backtest.run import parse_date_ms as harness_parse
from strategy.models import ConfluenceParams

# --------------------------------------------------------------------------- #
# 1 · 기본값이 꺼짐이다
# --------------------------------------------------------------------------- #


def test_adopted_default_no_longer_filters_by_zone_width() -> None:
    """WAN-159의 `1.28`이 폐지됐다 — 존 두께로는 안 거른다."""
    assert ConfluenceParams().max_zone_width_atr is None
    assert build_params().max_zone_width_atr is None


def test_the_two_legacy_pins_point_in_opposite_directions() -> None:
    """🚨 핀이 **두 세대**다 — 어느 쪽을 쓰는지가 곧 「어느 시절 표인가」다."""
    assert LEGACY_MAX_ZONE_WIDTH_ATR is None  # WAN-158 이전(지금은 기본값과 같아 무동작)
    assert LEGACY_ZONE_WIDTH_FILTER_ON == 1.28  # WAN-159~383(실제로 고정한다)
    off = ConfluenceParams()
    assert pin_zone_width(off).max_zone_width_atr is None  # 인자 생략 = 무동작
    assert pin_zone_width(off, LEGACY_ZONE_WIDTH_FILTER_ON).max_zone_width_atr == 1.28


def test_cli_can_still_ask_for_the_old_threshold() -> None:
    """옛 엔진을 요청하는 길이 남아 있다 — 결정문의 재현 명령이 실제로 돈다."""
    grid = grid_from_args(
        build_parser().parse_args(["--symbol", "BTCUSDT", "--max-zone-width-atr", "1.28"])
    )
    (combo,) = iter_combos(grid)
    assert build_params(max_zone_width_atr=combo.max_zone_width_atr).max_zone_width_atr == 1.28


# --------------------------------------------------------------------------- #
# 2 · 동작 — 넓은 존이 실제로 매매된다 / 켜면 실제로 줄어든다
# --------------------------------------------------------------------------- #


#: 실데이터 대조 좌표 — 합성 시장에는 존폭이 갈릴 만한 셋업이 안 생겨(ATR 대비 존이 늘
#: 좁다) 필터 축을 못 잰다. 창은 wan366 회귀 테스트와 같은 값이라 CI 게이트도 같이 걸린다.
_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-07-01"


def _real_market() -> Any:
    """🚨 게이트는 엔진 호출 **전에** 판정한다 — 안 그러면 CI의 빈 DB가 skip이 아니라
    실패로 끝난다(이 저장소가 이미 겪은 실패)."""
    from backtest.run import parse_date_ms

    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")
    return market


def test_turning_the_filter_on_actually_removes_setups() -> None:
    """옵트인 경로가 살아 있다 — 켜면 넓은 존 셋업이 실제로 빠진다.

    라벨이 아니라 **셋업 집합**으로 건다: 켠 판의 셋업이 끈 판의 **진부분집합**이어야 한다
    (필터는 거르기만 하므로 — WAN-161이 같은 자리에서 쓴 불변식). 개수만 보면 「같은 개수의
    다른 셋업이 통과」한 경우를 놓친다.
    """
    from backtest.zone_limit_backtest import SetupDiagnostic, build_zone_limit_candidates

    market = _real_market()
    obr = harness.detect_order_blocks(market)
    cfg = harness.legacy_build_config(market.timeframe)

    def _setups(threshold: float | None) -> list[SetupDiagnostic]:
        sink: list[SetupDiagnostic] = []
        build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            market.timeframe,
            params=build_params(max_zone_width_atr=threshold),
            cfg=cfg,
            observe_zone_width_atr=True,
            order_block_result=obr,
            setup_sink=sink,
        )
        return sink

    off = _setups(None)
    on = _setups(LEGACY_ZONE_WIDTH_FILTER_ON)
    assert off, "실데이터에 셋업이 없어 검사가 성립하지 않습니다."

    def _keys(rows: list[SetupDiagnostic]) -> set[tuple[int, int]]:
        return {(d.trigger_time, d.tap_index) for d in rows}

    keys_on, keys_off = _keys(on), _keys(off)
    assert keys_on, "필터 켠 판에 셋업이 없어 부분집합 검사가 성립하지 않습니다."
    assert keys_on < keys_off, "필터를 켰는데 셋업이 하나도 안 줄었습니다(배선 누락)."
    # 살아남은 셋업은 정의상 문턱 이하여야 한다 — 엔진과 관측이 다른 값을 보면 안 된다.
    ratios = [d.zone_width_atr for d in on if d.zone_width_atr is not None]
    assert ratios and max(ratios) <= LEGACY_ZONE_WIDTH_FILTER_ON


def test_default_run_matches_the_explicit_off_run() -> None:
    """「아무것도 안 준 실행」이 「명시적으로 끈 실행」과 같은 후보를 낸다(기본값 확인)."""
    assert build_params() == build_params(max_zone_width_atr=None)


# --------------------------------------------------------------------------- #
# 3 · 파급 — 필터를 켠 채 결론을 낸 모듈이 전부 핀을 든다
# --------------------------------------------------------------------------- #

#: 존폭 축을 **자기 입력으로 명시**하거나 엔진 필터를 끄고 밖에서 컷하거나 엔진을 아예 안
#: 도는 모듈 — 핀이 필요 없다(이유는 `docs/decisions/wan384.md` §4-2).
_NO_PIN_NEEDED: frozenset[str] = frozenset(
    {
        # 문턱이 실험 변수인 모듈
        "wan161_threshold_x_tp_multiple",
        "wan201_matched_null_filter_nine",
        "wan203_narrow_zone_selection",
        "wan176_nine_symbol_rebaseline",
        "wan155_tp_ruler_vs_multiple",
        # 엔진 필터를 끄고 밖에서 컷한다
        "wan378_zone_thickness_grid",
        # 엔진을 안 돈다(CSV·원자료만 읽거나 탐지만 한다)
        "wan226_reservation_compare",
        "wan229_reentry_census_15m",  # wan228 census 엔진 재사용 = 그쪽 핀이 덮는다
        "wan254_formation_census",
        "wan348_same_minute_tp",
        "wan362_same_minute_roundtrip",
        # WAN-396은 **적재된 CSV의 열만 되계산한다** — 후보를 만들지도, 북을 돌리지도 않아
        # 존폭 문턱이 닿을 자리가 없다. 읽는 표들이 각자 자기 좌표를 이미 들고 있고, 이 모듈은
        # 그 좌표를 바꾸지 않고 허수 진입 슬리피지만 걷어낸다(`net_r`은 손도 안 댄다).
        "wan396_entry_slippage_correction",
        # 채택된 것을 재는 리포트 — 기본값이 움직이면 낡아야 맞다
        "wan95_zone_limit_report",
        # 오늘 채택 북 위의 **순수 관측**이라 핀 없음이 설계다(WAN-305) — 필터를 켠 채 낸
        # 표가 아니라 「지금 매매하는 거래의 경로」를 재는 표이고, 그 거래 집합이 곧 필터
        # 꺼진 채택 기본값이다. 검산 (a)가 「인자 없는 채택 북」과의 등식을 못 박는다.
        "wan383_confirmation_entry",
        # 그 §3(WAN-386)도 같다 — 필터를 켠 채 낸 표가 아니라 **필터 꺼진 채택 북의 셋업**
        # 위에서 진입 시점만 갈아끼운 표다. 검산 (a-1)/(a-2)가 그 등식을 못 박는다.
        "wan386_confirmation_pnl",
        # 존 병합 × 재탭 2×2(WAN-388)도 같다 — 필터를 켠 채 낸 표가 아니라 **필터 꺼진 채택
        # 북** 위에서 존 정의와 재탭 정책만 갈아끼운 표다(핀 없음이 설계, WAN-305). 검산
        # (a-1)/(a-2)가 `split_every` 팔 ≡ 인자 없는 채택 북을 못 박는다.
        "wan388_merge_x_retap",
        # 출구 눈금 격자(WAN-381)도 같다 — 필터를 켠 채 낸 표가 아니라 **필터 꺼진 채택 북의
        # 셋업** 위에서 손절폭 가드와 익절 배수만 갈아끼운 표다(핀 없음이 설계, WAN-305).
        # 검산 (a-1)/(a-2)가 「기준 팔 후보 ≡ 엔진 base+재진입」과 「그 지갑 ≡ 채택 북 지갑」을
        # 못 박고, (d)가 겹치는 6조합이 `wan386_confirmation_grid.csv`와 비트 일치함을 낸다.
        "wan381_exit_scales",
        # §1 인구조사는 **탐지만** 한다 — 존폭 필터는 진입 층(지정가 확정 이후)이라 이 표의
        # 탭 수·클러스터 분포에 아예 닿지 않는다.
        "wan388_merge_retap_census",
        # 위치 축 널의 **오늘 좌표 재측정**(WAN-403)이다 — 필터를 켠 채 낸 표가 아니라 정확히
        # 그 반대다: 옛 판(WAN-248)이 1.28을 켠 채 돌았기에 「필터를 끈 오늘 좌표에서 다시」가
        # 이 이슈의 존재 이유이고, 핀 없음(`harness.UNSET`)이 **측정 대상 그 자체**다(WAN-305).
        # 옛 판은 `wan248_zone_position_null`이 `LEGACY_ZONE_WIDTH_FILTER_ON`으로 계속 고정한다.
        "wan403_zone_position_null_today",
        # WAN-400 §0도 같은 부류다 — `OrderBlockParams`만 쓰고 `ConfluenceParams`를 아예
        # 만들지 않아 `max_zone_width_atr`이 읽힐 자리가 없다(탐지 층 탭만 센다).
        "wan400_merge_parity_census",
        # 재탭 × 재진입 귀속(WAN-389)도 같다 — WAN-388의 후속이고 **필터 꺼진 채택 북** 위에서
        # 재탭 정책과 재진입 배치만 갈아끼운 표다(핀 없음이 설계, WAN-305). 핀을 걸면 검산 (a)
        # (`split_every` ≡ WAN-388 공개 CSV)가 오히려 깨진다 — 그쪽이 핀 없이 낸 표다.
        "wan389_retap_attribution",
        # 세 축 격자(WAN-394 §1)도 같다 — 필터를 켠 채 낸 표가 아니라 **필터 꺼진 채택 북**
        # 위에서 재탭 정책·재진입 배치·익절 배수만 갈아끼운 표다(핀 없음이 설계, WAN-305).
        # 핀을 걸면 검산 (a)(1.5R 네 칸 ≡ WAN-389 CSV)와 (b)(배수 4점 ≡ WAN-381 CSV)가
        # 오히려 깨진다 — 둘 다 핀 없이 낸 표다.
        "wan394_retap_reentry_tp",
        # 익절 배수 꺾임(WAN-395)도 같다 — WAN-381의 후속이고 **필터 꺼진 채택 북의 셋업**
        # 위에서 익절 배수만 갈아끼운 표다(핀 없음이 설계, WAN-305). 핀을 걸면 검산 (a-2)
        # (채택 점 지갑 ≡ 인자 없는 채택 북)와 (d)(겹치는 배수 4점 ≡ WAN-381 공개 CSV)가
        # 오히려 깨진다 — 둘 다 핀 없이 낸 표다.
        "wan395_exit_multiple_inflection",
        # 슬리피지 실측(WAN-397)도 같다 — 필터를 켠 채 낸 표가 아니라 **필터 꺼진 채택 북**의
        # 손절 봉과 요율 민감도다(핀 없음이 설계, WAN-305). 핀을 걸면 검산 (a)(기준 팔 ≡
        # WAN-395 공개 CSV의 채택 점)가 오히려 깨진다 — 그쪽도 핀 없이 낸 표다.
        "wan397_stop_slippage",
    }
)

#: WAN-159 채택(2026-07-21) 이후 번호의 모듈만 대상 — 그 이전 리포트는 필터가 없던 시절의
#: 기록이라 `LEGACY_MAX_ZONE_WIDTH_ATR`(= 끄기) 쪽이고 이 전환에 무영향이다.
_FIRST_PINNED_ISSUE = 159

#: 🚨 **「핀」으로 치는 형태 — 느슨하게 잡으면 이 가드가 무의미해진다.**
#:
#: 처음 판은 토큰이 `"max_zone_width_atr="`였는데, 그건 **핀이 아니라 파라미터 통과
#: (passthrough)에도 걸린다** — `max_zone_width_atr=task.max_zone_width_atr` 한 줄이 있는
#: 모듈이 「고정했다」로 통과했고, 실제로 `wan169_leverage_book.main()`이 핀 없이 새어
#: 나갔다(PM 검토가 잡았다). 「그 이름을 어딘가에 썼다」가 아니라 **「값에 못 박았다」**
#: 로만 통과해야 한다 — 이 저장소가 반복해 경계한 「라벨과 동작이 어긋남」
#: (WAN-91/95/112/123/159)의 **테스트 축 변종**이다.
#:
#: 통과하는 형태는 셋뿐이다:
#: * 둘째 세대 핀 상수(`LEGACY_ZONE_WIDTH_FILTER_ON`) — 직접 쓰든 모듈 상수로 받든.
#: * 첫째 세대 핀 상수(`LEGACY_MAX_ZONE_WIDTH_ATR`) — 「필터 없던 시절 판」 선언.
#: * **리터럴** 대입(`max_zone_width_atr=None` · `max_zone_width_atr=1.28`).
#:
#: ⚠️ `max_zone_width_atr=<식별자>`(`task.…` · `threshold` · `harness.UNSET`)는 **핀이
#: 아니다** — 값이 실행 시점에 정해지므로 이 파일만 읽어서는 무엇으로 도는지 알 수 없다.
_PIN_CONSTANTS = ("LEGACY_ZONE_WIDTH_FILTER_ON", "LEGACY_MAX_ZONE_WIDTH_ATR")
_PIN_LITERAL = re.compile(r"max_zone_width_atr\s*=\s*(None|\d)")


def _is_pinned(text: str) -> bool:
    """이 모듈이 존폭 문턱을 **값에 못 박았는가**(라벨을 썼는가가 아니라)."""
    if any(token in text for token in _PIN_CONSTANTS):
        return True
    return _PIN_LITERAL.search(text) is not None


def _post_159_modules() -> list[Path]:
    out: list[Path] = []
    for path in sorted(Path("backtest").glob("wan*.py")):
        m = re.match(r"wan(\d+)", path.stem)
        if m and int(m.group(1)) >= _FIRST_PINNED_ISSUE:
            out.append(path)
    return out


def test_every_post_wan159_report_pins_or_is_exempt() -> None:
    """🚨 이 이슈의 진짜 위험을 **미래에도** 막는다.

    필터를 켠 채 결론을 낸 모듈이 하나라도 핀을 안 들면 조용히 필터 꺼진 엔진으로 다시 돌아
    본문과 어긋난다(WAN-132/149/159가 겪은 부류의 거울상). 새 모듈을 만들면 **핀을 넣거나
    `_NO_PIN_NEEDED`에 이유와 함께 등록**해야 이 테스트가 통과한다.

    ⚠️ **이 스캔은 그물이지 증명이 아니다** — 파일 어딘가에 핀 형태가 있으면 통과하므로,
    **기계 코드와 자기 리포트를 겸하는 모듈**은 못 잡는다. 실제 사례가 `wan169`다: 공유
    기계인 `run_cells_multi`가 `max_zone_width_atr=None`을 들고 있어 자기 `main()`의 핀이
    빠져도 이 스캔은 통과한다(PM 검토가 그 구멍으로 샌 것을 잡았다). 그 부류는 **호출부를
    가로채는 스파이 테스트**가 맡는다(`test_wan169_main_pins_the_threshold_at_its_call_site`
    · `test_book_modules_pass_the_pin_to_run_cells`). 두 층이 함께 있어야 한다.
    """
    modules = _post_159_modules()
    assert modules, "대상 모듈을 하나도 못 찾았습니다 — 경로 규약이 바뀌었습니다."
    missing = [
        path.stem
        for path in modules
        if path.stem not in _NO_PIN_NEEDED and not _is_pinned(path.read_text())
    ]
    assert not missing, (
        f"존폭 핀이 없는 모듈: {missing} — 필터를 켠 채 낸 표라면 "
        "`harness.LEGACY_ZONE_WIDTH_FILTER_ON`으로 고정하고, 아니라면 `_NO_PIN_NEEDED`에 "
        "이유와 함께 등록하세요(docs/decisions/wan384.md §4)."
    )


def test_exemption_list_has_no_stale_entries() -> None:
    """예외 목록이 실재하는 모듈만 담는다 — 이름이 낡으면 예외가 조용히 무효가 된다."""
    names = {p.stem for p in Path("backtest").glob("wan*.py")}
    stale = _NO_PIN_NEEDED - names
    assert not stale, f"없는 모듈이 예외로 등록돼 있습니다: {stale}"


# --------------------------------------------------------------------------- #
# 4 · 호출부로 건다 — 북 후보 생성이 실제로 그 값을 넘긴다
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("module_path", "call"),
    [
        (
            "backtest.wan372_macd_color",
            lambda mod: mod.build_payloads(
                ["BTCUSDT"], ["1h"], start="2026-01-01", end="2026-01-10", jobs=1
            ),
        ),
        (
            "backtest.wan370_cost_decomposition",
            lambda mod: mod.build_payloads(
                ["BTCUSDT"], ["1h"], start="2026-01-01", end="2026-01-10", jobs=1
            ),
        ),
    ],
)
def test_book_modules_pass_the_pin_to_run_cells(
    monkeypatch: pytest.MonkeyPatch, module_path: str, call: Any
) -> None:
    """라벨이 아니라 **호출부**로 건다 — 실제로 넘어가는 인자를 가로채 확인한다."""
    import importlib

    mod = importlib.import_module(module_path)
    seen: dict[str, Any] = {}

    def _spy(*args: Any, **kwargs: Any) -> list[Any]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(mod, "run_cells", _spy, raising=True)
    call(mod)
    assert seen["max_zone_width_atr"] == LEGACY_ZONE_WIDTH_FILTER_ON, (
        "존폭 핀이 안 넘어갔습니다 — 이 표는 필터를 켠 채 낸 기록입니다(WAN-384 §4)."
    )


def test_pinned_report_fingerprints_say_1_28() -> None:
    """산출물 지문이 「1.28」이라고 말한다 — md만 봐도 어느 엔진인지 드러나야 한다."""
    from backtest.wan164_short_today_engine import describe_engine as wan164_engine
    from backtest.wan282_resistance_short_mirror import describe_engine as wan282_engine
    from backtest.wan350_conservative_null import describe_engine as wan350_engine

    for describe in (wan164_engine, wan282_engine, wan350_engine):
        assert "max_zone_width_atr=1.28" in describe()


def test_harness_module_exposes_the_new_pin() -> None:
    assert harness.LEGACY_ZONE_WIDTH_FILTER_ON == 1.28


# --------------------------------------------------------------------------- #
# 5 · 핀이 옛 엔진을 되살린다 (실데이터 · 비트 일치)
# --------------------------------------------------------------------------- #

#: WAN-384 **이전** 기본 실행의 동결 기준값 — BTC 1h · 못 박은 대조 창 · per-cell 단일.
#: 출처는 `tests/test_run_regression_real_data.py`의 옛 `_WAN95_PINNED`(같은 좌표)다.
_PIN_START = "2023-07-15"
_PIN_END = "2026-07-15"

_PRE_WAN384_CELL = {
    "num_trades": 182,
    "win_rate": 0.4340659340659341,
    "total_return": -0.08581565507555988,
    "max_drawdown": 0.14782008977072353,
    "fill_rate": 0.8328445747800587,
}


def test_pinning_1_28_reproduces_the_pre_wan384_engine() -> None:
    """🚨 핀이 **라벨이 아니라 값**임을 실데이터 비트 일치로 고정한다.

    이 등식이 깨지면 「1.28로 고정했다」고 적어 둔 49개 리포트가 전부 다른 엔진을 도는
    것이다 — 이 이슈의 진짜 위험이 정확히 그 자리다(WAN-384 §4).
    """
    from backtest.run import RunOptions, run_grid

    args = build_parser().parse_args(
        [
            "--symbol",
            "BTCUSDT",
            "--tf",
            "1h",
            "--positions",
            "single",
            "--max-zone-width-atr",
            "1.28",
        ]
    )
    grid = grid_from_args(args)
    (combo,) = iter_combos(grid)
    assert combo.max_zone_width_atr == 1.28
    start_ms, end_ms = harness_parse(_PIN_START), harness_parse(_PIN_END)
    market = harness.load_market_data("BTC/USDT:USDT", "1h", start_ms=start_ms, end_ms=end_ms)
    if market.empty or market.df_1m.empty:
        pytest.skip("BTC 1h 실데이터가 없어 건너뜁니다(CI 기본).")
    # 창을 못 박는다(WAN-162) — 미끄러지는 창이면 이 대조가 날짜에 따라 어긋난다.
    rows = run_grid(grid, RunOptions(years=3.0, start_ms=start_ms, end_ms=end_ms), log=False)
    row = next(r for r in rows if r.segment == harness.SEGMENT_FULL)
    for column, expected in _PRE_WAN384_CELL.items():
        actual = getattr(row, column)
        assert actual == pytest.approx(float(expected), abs=1e-9), (
            f"{column}: 핀 {actual} != WAN-384 이전 기준 {expected} — 핀이 옛 엔진을 "
            "되살리지 못했습니다."
        )


def test_passthrough_does_not_count_as_a_pin() -> None:
    """🚨 이 가드의 급소 — 「그 이름을 썼다」가 「값에 못 박았다」로 통과하면 안 된다.

    처음 판이 정확히 그래서 `wan169_leverage_book.main()`을 놓쳤다(PM 검토가 잡았다).
    """
    # 핀이 아니다 — 값이 실행 시점에 정해진다.
    assert not _is_pinned("max_zone_width_atr=task.max_zone_width_atr")
    assert not _is_pinned("max_zone_width_atr=threshold")
    assert not _is_pinned("max_zone_width_atr=harness.UNSET")
    assert not _is_pinned("max_zone_width_atr=head.zone_width")
    # 핀이다 — 상수 또는 리터럴.
    assert _is_pinned("max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON")
    assert _is_pinned("max_zone_width_atr=harness.LEGACY_MAX_ZONE_WIDTH_ATR")
    assert _is_pinned("max_zone_width_atr=None")
    assert _is_pinned("max_zone_width_atr=1.28")


def test_wan169_main_pins_the_threshold_at_its_call_site(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """🚨 돌연변이 확인 — `wan169.main()`의 **실제 호출부**에 핀이 실려 나가는가.

    wan169 리포트 CSV는 **2026-07-22 산출**이라 WAN-159(07-21)의 필터 1.28이 켜진 판이다.
    같은 자리에 WAN-305·WAN-365 핀이 이미 있는데 WAN-384 핀만 빠져 있었다 — 파일을 읽는
    가드(위)와 별개로, **인자가 정말 넘어가는지**를 호출부에서 가로채 확인한다.
    """
    import backtest.wan169_leverage_book as mod

    class _Stop(Exception):
        pass

    seen: dict[str, Any] = {}

    def _spy(*args: Any, **kwargs: Any) -> list[Any]:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(mod, "run_cells", _spy, raising=True)
    with pytest.raises(_Stop):
        mod.main(
            [
                "--symbols",
                "BTCUSDT",
                "--tf",
                "1h",
                "--out-cells",
                str(tmp_path / "c.csv"),
                "--out-grid",
                str(tmp_path / "g.csv"),
                "--out-md",
                str(tmp_path / "s.md"),
            ]
        )
    assert seen["max_zone_width_atr"] == LEGACY_ZONE_WIDTH_FILTER_ON, (
        "wan169 리포트는 필터 1.28이 켜진 판인데 핀이 안 넘어갔습니다 — 지금 돌리면 "
        "커밋된 CSV와 다른 행이 나옵니다(WAN-384 §4)."
    )
    # 같은 자리의 옛 핀 둘도 함께 살아 있어야 한다(하나만 남으면 그 CSV는 여전히 못 되살린다).
    assert seen["reentry"] is False
    assert seen["invalidation_cancel"] == harness.LEGACY_INVALIDATION_CANCEL
