"""WAN-405: 새 북 측정 모듈이 **후보 캐시를 잊으면** 같은 좌표를 또 몇 시간 태운다.

사용자 지적 2026-09-03: *「너 어차피 대화 까먹는거 아니야?」* — 맞다. 「다음엔 캐시를
켜겠다」는 **약속은 세션과 함께 사라진다.** 그래서 그 약속을 여기 옮긴다.

🚨 이건 가정이 아니라 **이미 일어난 일**이다: WAN-405가 3팔 × 36칸을 **4시간 44분** 태우고
payload를 하나도 안 남겨, 「거래별로 어디서 손절났나」를 보려면 그 시간을 **통째로 다시**
써야 하는 상태로 끝났다. 캐시를 켰으면 몇 분이었다.

⚠️ **WAN-373 가드와 같은 모양이고 뜻만 다르다.** 그쪽은 *「새 리포트가 옛 회계로 새는 것」*
을 잡고, 이쪽은 *「새 리포트가 후보를 버리는 것」*을 잡는다. 예외 목록의 뜻도 같다 —
**이미 표를 낸 모듈**은 이제 와 캐시를 켜도 얻을 게 없으므로 선언하고 넘어간다.

⚠️ **가드는 「축을 언급했는가」까지만 본다** — 켰는지 껐는지는 모듈마다 옳은 답이 다르므로
강제하지 않는다(WAN-365/373 가드가 그은 것과 같은 선). 잡는 것은 **아무 생각 없이 인자를
빠뜨리는 것** 하나다.
"""

from __future__ import annotations

import pathlib

_BACKTEST = pathlib.Path("backtest")

#: 후보 생성 진입점. 이 이름을 부르면서 캐시 축을 언급조차 안 하면 걸린다.
_GENERATOR = "run_cells"

#: 그 진입점이 사는 모듈 — 이름만으로는 과대 추정된다(`run_cells`는 흔한 이름이다).
#: **북 후보를 쓰려면 반드시 import해야 하므로** 두 조건을 AND로 건다(WAN-373과 같은 방법).
_GENERATOR_MODULE = "wan169_leverage_book"

#: 캐시를 **안 켜도 되는** 모듈 — 전부 WAN-394(캐시 신설) **이전**에 표를 낸 것들이라
#: 이제 와 켜도 얻을 게 없다(다시 돌 일이 없다). 🚨 **새 모듈은 이 목록에 들어갈 수 없다.**
_ALREADY_PUBLISHED: frozenset[str] = frozenset(
    {
        "wan180_leverage_book_nine.py",
        "wan244_capacity_cap.py",
        "wan261_reentry_book.py",
        "wan264_reentry_book_stress.py",
        "wan269_reentry_book_band.py",
        "wan271_reentry_book_band_stress.py",
        "wan276_stop_gap_fill.py",
        "wan277_stop_gap_reentry.py",
        "wan280_reentry_short_transition.py",
        "wan282_resistance_short_mirror.py",
        "wan288_monthly_long_short.py",
        "wan293_monthly_fill_lens.py",
        "wan300_universe_size.py",
        "wan301_short_book_risk.py",
        "wan304_universe_reentry.py",
        "wan312_stop_r_multiple.py",
        "wan323_partial_tp_ladder.py",
        "wan327_partial_bar_impact.py",
        "wan330_partial_tp_ladder_4tf.py",
        "wan336_same_step_tp.py",
        "wan346_conservative_book.py",
        "wan359_tick_targeted_tp.py",
        "wan364_invalidation_cancel.py",
        "wan370_cost_decomposition.py",
        "wan372_macd_color.py",
        "wan376_zone_thickness.py",
        "wan378_zone_thickness_grid.py",
        "wan383_confirmation_entry.py",
        "wan386_confirmation_pnl.py",
        "wan388_merge_x_retap.py",
        "wan389_retap_attribution.py",
    }
)


def _modules_generating_candidates() -> list[str]:
    """`run_cells`를 실제로 부르는 `wan*.py` — import ∧ 호출을 함께 본다."""
    out: list[str] = []
    for path in sorted(_BACKTEST.glob("wan*.py")):
        text = path.read_text()
        if _GENERATOR in text and _GENERATOR_MODULE in text:
            out.append(path.name)
    return out


def test_the_cache_axis_exists_and_is_opt_in() -> None:
    """가드의 **전제** — 기본값이 「안 씀」이라서 「잊으면 태운다」가 성립한다.

    기본값이 뒤집히면(별도 결정) 이 가드의 이유가 사라지므로 조용히 남지 않고 여기서 죽는다.
    """
    import inspect

    from backtest.wan169_leverage_book import run_cells

    assert inspect.signature(run_cells).parameters["payload_cache"].default is None


def test_every_candidate_generating_module_names_the_cache_axis() -> None:
    """후보를 만드는 **새** 모듈이 캐시를 안 언급하면 여기서 걸린다."""
    missing = [
        name
        for name in _modules_generating_candidates()
        if name not in _ALREADY_PUBLISHED and "payload_cache" not in (_BACKTEST / name).read_text()
    ]
    assert not missing, (
        f"후보를 만들면서 `payload_cache`를 언급조차 하지 않은 모듈: {missing} — "
        "`PayloadCache()`를 넘기거나, 껐어야 할 이유가 있으면 "
        "`PayloadCache(read=False, write=False)`를 명시하세요. 🚨 새 모듈은 "
        "`_ALREADY_PUBLISHED`에 들어갈 수 없습니다(그 목록은 WAN-394 이전 표의 스냅샷)."
    )


def test_the_exemption_list_is_a_declaration_not_a_leftover() -> None:
    """목록이 낡으면(파일이 사라지거나 이미 캐시를 켰으면) 시끄럽게 죽는다."""
    stale: list[str] = []
    for name in sorted(_ALREADY_PUBLISHED):
        path = _BACKTEST / name
        assert path.exists(), f"{name}이 없습니다 — 예외 목록을 정리하세요."
        if "payload_cache" in path.read_text():
            stale.append(name)
    assert not stale, f"이미 캐시를 켠 모듈이 예외 목록에 남아 있습니다: {stale}"


def test_this_issues_arms_actually_cache() -> None:
    """🚨 WAN-405 본인이 그 실수를 했으므로, 이 모듈만은 **동작으로** 건다.

    라벨(문자열 언급)이 아니라 **기본 인자로 캐시가 실제로 넘어가는지**를 본다.
    """
    import inspect

    from backtest import wan366_causal_ablation as mod

    source = inspect.getsource(mod.detector_payloads)
    assert "payload_cache=" in source, "후보 생성이 캐시를 안 넘깁니다."
    assert "PayloadCache()" in source, "인자를 안 주면 캐시를 스스로 만들어야 합니다."
