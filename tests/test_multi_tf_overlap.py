"""다중TF 오더블록 겹침 캐스케이드 테스트 (WAN-126).

두 층을 검증한다:

1. **겹침 로직**(`backtest.multi_tf_overlap`): 세 겹침 정의의 판정·중첩 관계, first-touched
   선택, 캐스케이드가 첫 겹침에서 멈추는지, 빈/무데이터 TF를 건너뛰는지.
2. **🚨 룩어헤드 가드**(협상 불가 완료기준): `order_block_zone_provider`가 탭 시각 이후
   확정된 하위TF 존을 **동작으로** 배제하는지 — provider 단위와 엔진(`build_zone_limit_
   candidates`) end-to-end 양쪽에서. 미래 존이 새어 들어오면 이 이슈의 결과가 통째로
   무의미해지므로, 그 누출을 라벨이 아니라 거래 산출로 막는다.
3. **3팔 분리**: `A`(대조=기본 동작 동일)·`B`(선별만, 씨앗 상위TF 존)·`C`(선별+가격, 씨앗
   하위TF 존)의 진입가·손절·1R 차이.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import BacktestConfig
from backtest.multi_tf_overlap import (
    MultiTfOverlapParams,
    ZoneProvider,
    choose_refinement_zone,
    find_refinement,
    order_block_zone_provider,
    zones_overlap,
)
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
)

BULL = OrderBlockDirection.BULLISH
BEAR = OrderBlockDirection.BEARISH


def _zone(
    direction: OrderBlockDirection,
    bottom: float,
    top: float,
    *,
    confirmed_time: int = 0,
    volume: float = 1.0,
) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=confirmed_time,
        confirmed_time=confirmed_time,
        ob_volume=volume,
        ob_low_volume=volume / 2,
        ob_high_volume=volume / 2,
    )


# --------------------------------------------------------------------------- #
# 겹침 정의
# --------------------------------------------------------------------------- #


def test_contained_requires_full_containment() -> None:
    htf = _zone(BULL, 100.0, 110.0)
    assert zones_overlap(htf, _zone(BULL, 102.0, 108.0), "contained")  # 완전 포함
    assert not zones_overlap(htf, _zone(BULL, 98.0, 108.0), "contained")  # 하단이 삐져나감
    assert not zones_overlap(htf, _zone(BULL, 102.0, 112.0), "contained")  # 상단이 삐져나감


def test_touch_is_any_intersection_including_edge() -> None:
    htf = _zone(BULL, 100.0, 110.0)
    assert zones_overlap(htf, _zone(BULL, 105.0, 115.0), "touch")  # 부분 교차
    assert zones_overlap(htf, _zone(BULL, 110.0, 120.0), "touch")  # 경계 접촉
    assert not zones_overlap(htf, _zone(BULL, 111.0, 120.0), "touch")  # 완전 바깥


def test_proximal_in_uses_entry_side_end() -> None:
    htf = _zone(BULL, 100.0, 110.0)
    # 롱: 하위 존 근단 = 상단. 상단이 상위 존 안이면 겹침(하단은 밖이어도 됨).
    assert zones_overlap(htf, _zone(BULL, 90.0, 105.0), "proximal_in")
    assert not zones_overlap(htf, _zone(BULL, 90.0, 99.0), "proximal_in")  # 상단이 밑으로
    # 숏: 근단 = 하단.
    htf_s = _zone(BEAR, 100.0, 110.0)
    assert zones_overlap(htf_s, _zone(BEAR, 105.0, 120.0), "proximal_in")  # 하단 105 안
    assert not zones_overlap(htf_s, _zone(BEAR, 111.0, 120.0), "proximal_in")


@pytest.mark.parametrize("direction", [BULL, BEAR])
def test_definitions_are_nested(direction: OrderBlockDirection) -> None:
    """contained ⊂ proximal_in ⊂ touch — 확정 사양 §3 (독립 증거로 세지 말라는 근거)."""
    htf = _zone(direction, 100.0, 110.0)
    # 완전 포함 존은 세 정의 모두 참이어야 한다.
    inside = _zone(direction, 102.0, 108.0)
    assert zones_overlap(htf, inside, "contained")
    assert zones_overlap(htf, inside, "proximal_in")
    assert zones_overlap(htf, inside, "touch")
    # 여러 무작위성 없는 조합에서 함의(contained→proximal_in→touch)가 깨지지 않는다.
    for bottom, top in [(95.0, 105.0), (108.0, 112.0), (90.0, 130.0), (101.0, 109.0)]:
        ltf = _zone(direction, bottom, top)
        if zones_overlap(htf, ltf, "contained"):
            assert zones_overlap(htf, ltf, "proximal_in")
        if zones_overlap(htf, ltf, "proximal_in"):
            assert zones_overlap(htf, ltf, "touch")


# --------------------------------------------------------------------------- #
# first-touched 선택
# --------------------------------------------------------------------------- #


def test_choose_refinement_picks_first_touched_long() -> None:
    """롱은 근단(상단)이 가장 높은 존을 먼저 닿는다."""
    lower = _zone(BULL, 100.0, 104.0)
    higher = _zone(BULL, 101.0, 108.0)
    assert choose_refinement_zone(_zone(BULL, 100.0, 110.0), [lower, higher], BULL) is higher


def test_choose_refinement_picks_first_touched_short() -> None:
    """숏은 근단(하단)이 가장 낮은 존을 먼저 닿는다."""
    lower = _zone(BEAR, 100.0, 104.0)
    higher = _zone(BEAR, 106.0, 110.0)
    assert choose_refinement_zone(_zone(BEAR, 100.0, 110.0), [lower, higher], BEAR) is lower


# --------------------------------------------------------------------------- #
# 캐스케이드
# --------------------------------------------------------------------------- #


def _provider_from(mapping: dict[str, list[OrderBlock]]) -> ZoneProvider:
    def provider(tf: str, _time: int, direction: OrderBlockDirection) -> list[OrderBlock]:
        return [z for z in mapping.get(tf, []) if z.direction is direction]

    return provider


def test_cascade_stops_at_first_overlap_rung() -> None:
    htf = _zone(BULL, 100.0, 110.0)
    provider = _provider_from(
        {
            "15m": [],  # 겹침 없음 → 다음 칸으로
            "5m": [_zone(BULL, 102.0, 108.0, confirmed_time=1)],  # 첫 겹침 → 여기서 멈춤
            "1m": [_zone(BULL, 103.0, 107.0, confirmed_time=2)],  # 보지 않음
        }
    )
    params = MultiTfOverlapParams(arm="C", definition="contained", ladder=("15m", "5m", "1m"))
    ref = find_refinement(htf, 10, params, provider)
    assert ref is not None
    assert ref.timeframe == "5m"
    assert ref.zone.top == 108.0


def test_cascade_skips_missing_tf_and_returns_none_without_overlap() -> None:
    htf = _zone(BULL, 100.0, 110.0)
    # 5m 데이터 없음(실측 상황) — provider가 빈 목록을 내고, 나머지에도 겹침이 없다.
    provider = _provider_from({"15m": [_zone(BULL, 200.0, 210.0)], "1m": []})
    params = MultiTfOverlapParams(arm="B", definition="contained", ladder=("15m", "5m", "1m"))
    assert find_refinement(htf, 10, params, provider) is None


def test_cascade_respects_direction_match() -> None:
    htf = _zone(BULL, 100.0, 110.0)
    # 같은 자리에 반대 방향(공급) 존만 있으면 겹침이 아니다.
    provider = _provider_from({"15m": [_zone(BEAR, 102.0, 108.0)]})
    params = MultiTfOverlapParams(arm="B", ladder=("15m",))
    assert find_refinement(htf, 10, params, provider) is None


# --------------------------------------------------------------------------- #
# 🚨 룩어헤드 가드 (provider 단위)
# --------------------------------------------------------------------------- #


def test_zone_provider_excludes_future_confirmed_zones() -> None:
    """탭 시각 이후 확정된 하위TF 존은 provider 뷰에 나타나지 않는다(핵심 완료기준)."""
    setup_time = 1_000
    past = _zone(BULL, 100.0, 110.0, confirmed_time=setup_time - 100)
    future = _zone(BULL, 100.0, 110.0, confirmed_time=setup_time + 100)
    result = OrderBlockResult(order_blocks=[past, future], signals=[], retap_signals=[])
    provider = order_block_zone_provider({"15m": result}, combine=False)

    at_setup = provider("15m", setup_time, BULL)
    tops = sorted(z.top for z in at_setup)
    # 과거 존만 보이고 미래 존은 배제된다.
    assert len(at_setup) == 1
    assert tops == [110.0]
    # 미래 시점으로 오면 그제야 둘 다 보인다(클리핑이 시각에 반응함을 확인).
    assert len(provider("15m", setup_time + 200, BULL)) == 2


def test_zone_provider_unknown_tf_returns_empty() -> None:
    provider = order_block_zone_provider({}, combine=False)
    assert provider("5m", 10, BULL) == []


# --------------------------------------------------------------------------- #
# 엔진 end-to-end (3팔 + 룩어헤드)
# --------------------------------------------------------------------------- #


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _base_params() -> ConfluenceParams:
    # deviation_filter를 꺼 3팔의 차이를 씨앗 존 하나로 격리한다(볼린저가 가격을 덮어쓰면
    # C의 순수 가격 효과가 흐려진다 — 확정 사양 §2의 편향은 리포트가 다루고, 여기서는
    # 배선 자체를 검증하므로 끈다). 합성 시드는 숏 셋업을 내므로 short을 켠다.
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )


class _Fixture:
    """상위TF 오더블록을 **한 번만** 탐지해 여러 팔 실행이 같은 OB 인스턴스를 공유하게 한다.

    build_zone_limit_candidates는 `order_block_result`를 안 주면 매 호출마다 재탐지해 새
    OB 객체를 만든다 — 그러면 팔 간 `order_block is ...` 조인이 깨진다. 공유하면 동일성이
    성립하고, 팔의 차이가 겹침 로직 하나로 격리된다.
    """

    def __init__(self) -> None:
        from strategy.order_blocks import OrderBlockDetector

        self.htf, self.one_min = _synthetic_pair()
        self.cfg = BacktestConfig()
        self.obr = OrderBlockDetector().run(self.htf)

    def run(self, params: ConfluenceParams, **kwargs: object) -> list[_Candidate]:
        cands, _ = build_zone_limit_candidates(
            self.htf,
            self.one_min,
            "1h",
            params=params,
            cfg=self.cfg,
            order_block_result=self.obr,
            **kwargs,  # type: ignore[arg-type]
        )
        return cands


def test_arm_a_is_identical_to_no_overlap() -> None:
    """`A`(대조)는 겹침을 무시하므로 overlap=None과 비트 단위로 같은 후보를 낸다."""
    fx = _Fixture()
    params = _base_params()
    baseline = fx.run(params)
    arm_a = fx.run(
        params,
        overlap=MultiTfOverlapParams(arm="A", ladder=("15m",)),
        zone_provider=_provider_from({}),
    )
    assert [c.__dict__ for c in arm_a] == [c.__dict__ for c in baseline]


def test_arm_b_blocks_all_when_no_overlap() -> None:
    """겹침이 하나도 없으면 `B`/`C`는 진입이 없다(규칙의 핵심)."""
    fx = _Fixture()
    params = _base_params()
    empty = _provider_from({})
    assert (
        fx.run(params, overlap=MultiTfOverlapParams(arm="B", ladder=("15m",)), zone_provider=empty)
        == []
    )
    assert (
        fx.run(params, overlap=MultiTfOverlapParams(arm="C", ladder=("15m",)), zone_provider=empty)
        == []
    )


def test_arm_b_passthrough_when_overlap_universal() -> None:
    """겹침이 항상 성립하면 `B`는 대조(A)와 같은 씨앗·같은 거래를 낸다(1R 불변)."""
    fx = _Fixture()
    params = _base_params()
    baseline = fx.run(params)
    # 모든 가격을 덮는 거대한 존(양방향) → touch가 항상 참.
    universal = {
        "15m": [_zone(BULL, 0.0, 1e12), _zone(BEAR, 0.0, 1e12)],
    }
    arm_b = fx.run(
        params,
        overlap=MultiTfOverlapParams(arm="B", definition="touch", ladder=("15m",)),
        zone_provider=_provider_from(universal),
    )
    # B는 씨앗이 상위TF 존이므로 진입가·손절이 A와 같고, 겹침이 항상 성립하니 개수도 같다.
    assert len(arm_b) == len(baseline)
    assert [c.entry_price for c in arm_b] == [c.entry_price for c in baseline]
    assert [c.stop_price for c in arm_b] == [c.stop_price for c in baseline]
    # 진단 필드: 겹침을 찾은 TF가 기록된다.
    assert all(c.refinement_tf == "15m" for c in arm_b)


def test_arm_c_swaps_seed_to_lower_tf_zone() -> None:
    """`C`는 겹치는 하위TF 존을 씨앗으로 삼아 손절(1R)을 그 존 무효화 경계로 재계산한다."""
    fx = _Fixture()
    params = _base_params()
    baseline = fx.run(params)
    assert baseline, "합성 시나리오가 최소 한 셋업을 내야 한다."
    htf_ob = baseline[0].order_block
    assert htf_ob is not None
    is_long = htf_ob.direction is BULL
    # 상위 존 안에 완전히 든 더 좁은 하위TF 존을 만든다.
    width = htf_ob.top - htf_ob.bottom
    ltf = _zone(
        htf_ob.direction,
        htf_ob.bottom + width * 0.25,
        htf_ob.top - width * 0.25,
        confirmed_time=htf_ob.confirmed_time,
    )
    provider = _provider_from({"15m": [ltf]})
    arm_c = fx.run(
        params,
        overlap=MultiTfOverlapParams(arm="C", definition="contained", ladder=("15m",)),
        zone_provider=provider,
    )
    # 그 상위 존에서 나온 C 후보를 찾아 손절가가 하위TF 존 원단인지 확인한다.
    matched = [c for c in arm_c if c.order_block is htf_ob]
    assert matched, "겹침이 성립한 상위 존은 C 후보를 내야 한다."
    ltf_distal = ltf.bottom if is_long else ltf.top
    htf_distal = htf_ob.bottom if is_long else htf_ob.top
    assert matched[0].stop_price == pytest.approx(ltf_distal)
    assert matched[0].stop_price != pytest.approx(htf_distal)
    # 좁아진 존이므로 1R(진입가~손절)이 상위 존보다 작다.
    assert abs(matched[0].entry_price - ltf_distal) < abs(matched[0].entry_price - htf_distal)


def test_engine_lookahead_future_ltf_zone_does_not_enable_entry() -> None:
    """엔진 end-to-end: 겹치는 하위TF 존이 상위TF 탭 **이후**에 확정되면 진입이 안 생긴다.

    같은 존을 탭 **이전** 확정으로 바꾸면 진입이 생긴다(양성 대조) — 즉 차이를 만든 것은
    오직 확정 시점이고, 미래 존이 새어 들어오지 않음이 거래 산출로 증명된다.
    """
    fx = _Fixture()
    params = _base_params()
    baseline = fx.run(params)
    assert baseline
    htf_ob = baseline[0].order_block
    assert htf_ob is not None
    trigger = baseline[0].trigger_time
    width = htf_ob.top - htf_ob.bottom
    overlapping = _zone(htf_ob.direction, htf_ob.bottom + width * 0.2, htf_ob.top - width * 0.2)

    def result_with(confirmed: int) -> OrderBlockResult:
        zone = overlapping.model_copy(update={"confirmed_time": confirmed, "start_time": confirmed})
        return OrderBlockResult(order_blocks=[zone], signals=[], retap_signals=[])

    overlap = MultiTfOverlapParams(arm="B", definition="contained", ladder=("15m",))

    future = order_block_zone_provider({"15m": result_with(trigger + 10_000_000)}, combine=False)
    past = order_block_zone_provider({"15m": result_with(trigger - 10_000_000)}, combine=False)

    cands_future = fx.run(params, overlap=overlap, zone_provider=future)
    cands_past = fx.run(params, overlap=overlap, zone_provider=past)

    matched_future = [c for c in cands_future if c.order_block is htf_ob]
    matched_past = [c for c in cands_past if c.order_block is htf_ob]
    assert matched_future == []  # 미래 존은 새어 들어오지 않는다.
    assert matched_past, "과거 확정 존은 정상적으로 진입을 낸다(양성 대조)."


def test_arm_bc_require_zone_provider() -> None:
    fx = _Fixture()
    params = _base_params()
    for overlap in (
        MultiTfOverlapParams(arm="B", ladder=("15m",)),
        MultiTfOverlapParams(arm="C", ladder=("15m",)),
    ):
        with pytest.raises(ValueError, match="zone_provider"):
            fx.run(params, overlap=overlap)
