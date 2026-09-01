"""WAN-396 회귀 — 진입 유동성의 **단일 소스**는 거래(엔진이 쓴 값)이지 설정이 아니다.

이 저장소가 여섯 번째로 겪은 「라벨과 동작이 어긋남」(WAN-91/95/112/123/159/194)의 회계 축
변종이다: 엔진은 후보의 `entry_liquidity`(기본 **메이커**)로 체결가·수수료를 만드는데
비용 분해는 `BacktestConfig.entry_liquidity`(기본 **테이커**)를 읽어 붙지도 않은 진입
슬리피지 5bp를 계상했다.

🚨 **`net`으로는 안 잡힌다** — 분해가 `entry_ref`를 슬리피지만큼 밀어 되돌리므로 `gross`와
`slippage`가 **똑같이 부풀고 상쇄된다**. 그래서 이 파일은 `gross`와 `slippage`를 **각각**
검사한다(완료기준 2).

⚠️ 옛 `tests/test_wan370_cost_decomposition.py`가 이 버그를 못 잡은 이유도 여기 있다 —
그 픽스처가 `BacktestConfig(entry_liquidity=MAKER)`를 **명시**해 설정과 엔진을 우연히
맞춰 놨다. 프로덕션(`harness.build_config`)은 그 필드를 아무 데도 안 넘긴다.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import replace

import pytest

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.wan370_cost_decomposition import decompose_trade
from backtest.zone_limit_backtest import _Candidate, _to_trade
from common.costs import Liquidity
from execution.sizing import PositionSizingParams

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _cfg(*, entry_liquidity: Liquidity = Liquidity.TAKER) -> BacktestConfig:
    """프로덕션과 같은 설정 — `entry_liquidity`는 **기본 테이커 그대로** 둔다.

    `harness.build_config`이 그 필드를 안 건드리므로 이것이 실제로 분해에 도달하던 값이다.
    """
    return BacktestConfig(
        initial_capital=10_000.0,
        entry_liquidity=entry_liquidity,
        risk_sizing=PositionSizingParams(risk_per_trade=0.01, leverage=1.0),
    )


def _cand(*, entry_liquidity: Liquidity = Liquidity.MAKER) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=1_000,
        exit_price=101.5,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.0,
        trigger_time=0,
        entry_liquidity=entry_liquidity,
    )


def _trade(cfg: BacktestConfig, cand: _Candidate) -> Trade:
    trade = _to_trade(cand, cfg.initial_capital, cfg)
    assert trade is not None
    return trade


# --------------------------------------------------------------------------- #
# §1 — 판정 (가): 엔진은 진입에 슬리피지를 붙이지 않는다
# --------------------------------------------------------------------------- #


def test_maker_entry_fill_equals_the_limit_price() -> None:
    """메이커 진입이면 체결가가 곧 지정가다 — 붙은 슬리피지가 **없다**(판정 (가)의 근거).

    `zone_limit_backtest._to_trade`가 `cand.entry_liquidity`를 읽는다는 사실을 라벨이 아니라
    **가격**으로 고정한다.
    """
    cfg = _cfg()  # 설정은 테이커(프로덕션 기본값) — 그런데도 붙으면 안 된다.
    trade = _trade(cfg, _cand())
    assert trade.entry_price == pytest.approx(100.0, rel=1e-15)


def test_taker_entry_fill_is_pushed_by_slippage() -> None:
    """반증 — 후보가 테이커면 진입가가 실제로 밀린다(위 테스트가 공허하지 않다)."""
    cfg = _cfg(entry_liquidity=Liquidity.MAKER)  # 설정은 메이커여도 후보가 이긴다.
    trade = _trade(cfg, _cand(entry_liquidity=Liquidity.TAKER))
    assert trade.entry_price > 100.0
    assert trade.entry_price == pytest.approx(100.0 * (1.0 + cfg.slippage), rel=1e-15)


@pytest.mark.parametrize("liquidity", [Liquidity.MAKER, Liquidity.TAKER])
def test_trade_records_the_liquidity_the_engine_used(liquidity: Liquidity) -> None:
    """거래가 자기 진입 유동성을 들고 나간다 — 분해가 물어볼 단일 소스다."""
    cfg = _cfg()
    trade = _trade(cfg, _cand(entry_liquidity=liquidity))
    assert trade.entry_liquidity is liquidity


def test_trade_default_matches_the_candidate_default() -> None:
    """두 기본값이 갈라지면 「기본값이라 비트 재현」이라는 주장이 거짓이 된다."""
    assert Trade.model_fields["entry_liquidity"].default is _Candidate.entry_liquidity


# --------------------------------------------------------------------------- #
# §2 — 분해가 설정이 아니라 거래를 읽는다 (완료기준 2)
# --------------------------------------------------------------------------- #


def test_decomposition_charges_no_entry_slippage_for_a_maker_entry() -> None:
    """설정이 **테이커**여도 메이커 진입 거래의 분해에는 진입 슬리피지가 없다.

    🚨 `gross`와 `slippage`를 **각각** 본다 — 두 항이 상쇄돼 `net`으로는 안 보이는 버그다.
    """
    cfg = _cfg(entry_liquidity=Liquidity.TAKER)
    trade = _trade(cfg, _cand())
    parts = decompose_trade(trade, cfg)

    # 익절은 메이커(WAN-370)이고 진입도 메이커이므로 이 거래의 슬리피지는 **정확히 0**이다.
    assert parts.slippage == pytest.approx(0.0, abs=1e-12)
    # gross는 순수 가격 이동 그대로 — 진입 참조가가 밀리지 않았다.
    assert parts.gross == pytest.approx((101.5 - 100.0) * trade.quantity, rel=1e-12)
    assert parts.residual == pytest.approx(0.0, abs=1e-9)


def test_decomposition_still_charges_entry_slippage_for_a_taker_entry() -> None:
    """반증 — 후보가 테이커면 분해도 진입 슬리피지를 **문다**(위 테스트가 공허하지 않다)."""
    cfg = _cfg(entry_liquidity=Liquidity.MAKER)  # 설정은 메이커여도 거래가 이긴다.
    trade = _trade(cfg, _cand(entry_liquidity=Liquidity.TAKER))
    parts = decompose_trade(trade, cfg)
    assert parts.slippage > 0.0
    assert parts.residual == pytest.approx(0.0, abs=1e-9)


def test_config_entry_liquidity_no_longer_moves_the_decomposition() -> None:
    """같은 거래를 설정만 바꿔 분해해도 **모든 성분이 같다** — 사문화된 필드임을 동작으로 고정."""
    cand = _cand()
    maker_cfg = _cfg(entry_liquidity=Liquidity.MAKER)
    taker_cfg = _cfg(entry_liquidity=Liquidity.TAKER)
    trade = _trade(maker_cfg, cand)
    a = decompose_trade(trade, maker_cfg)
    b = decompose_trade(trade, taker_cfg)
    assert a == b


def test_net_is_unchanged_by_the_fix() -> None:
    """판정 (가) — 손익은 처음부터 맞았다. 분해의 `net`은 실현손익 그대로다."""
    cfg = _cfg()
    trade = _trade(cfg, _cand())
    assert decompose_trade(trade, cfg).net == pytest.approx(trade.realized_pnl, rel=1e-15)


# --------------------------------------------------------------------------- #
# §3 — 공개 CSV 보정에 쓴 닫힌 식 (docs/decisions/wan396.md §3)
# --------------------------------------------------------------------------- #


def test_phantom_entry_slippage_equals_the_closed_form_correction() -> None:
    """허수 진입 슬리피지 == `entry_fee × K`, `K = (slip/(1+slip)) / maker_rate`.

    거래 단위 산출물이 남아 있지 않아 공개 CSV는 **열만** 되계산했다(WAN-396 §3). 그 보정이
    근사가 아니라 **정확한 항등식**임을 여기서 고정한다 — 무너지면 그 표가 무효다.
    """
    cfg = _cfg(entry_liquidity=Liquidity.TAKER)
    trade = _trade(cfg, _cand())

    costs = cfg.cost_model
    maker_rate = costs.fee_rate(Liquidity.MAKER)
    slip = cfg.slippage
    k = (slip / (1.0 + slip)) / maker_rate

    # 옛 분해가 계상하던 허수 진입 슬리피지를 그대로 재현한다.
    phantom_ref = trade.entry_price / (1.0 + slip)
    phantom = (trade.entry_price - phantom_ref) * trade.quantity

    assert phantom == pytest.approx(trade.entry_fee * k, rel=1e-12)
    assert k == pytest.approx(2.4987506246876565, rel=1e-12)


def test_correction_constant_matches_the_repo_default_rates() -> None:
    """K가 채택 요율(메이커 2bp · 슬리피지 5bp)에서 나온 값임을 못 박는다."""
    cfg = harness.build_config("1h")
    assert cfg.slippage == pytest.approx(0.0005)
    assert cfg.cost_model.fee_rate(Liquidity.MAKER) == pytest.approx(0.0002)


# --------------------------------------------------------------------------- #
# §4 — 배선 가드: 아무도 설정 쪽 필드를 다시 읽지 않는다
# --------------------------------------------------------------------------- #

#: 설정 객체를 담는 이름들 — 이 이름의 `.entry_liquidity`를 읽으면 버그가 되돌아온다.
_CONFIG_NAMES = frozenset({"cfg", "config", "backtest_config", "bt_cfg"})

#: 스캔 대상. `backtest/models.py`는 필드 **정의**라 예외다.
_SCAN_ROOTS = ("backtest", "live", "paper", "qc")
_ALLOWED = frozenset({"backtest/models.py"})


def _config_attribute_reads(path: pathlib.Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "entry_liquidity"
            and isinstance(node.value, ast.Name)
            and node.value.id in _CONFIG_NAMES
        ):
            hits.append(node.lineno)
    return hits


def test_no_module_reads_entry_liquidity_off_a_config() -> None:
    """`cfg.entry_liquidity`를 읽는 모듈이 하나도 없어야 한다(WAN-396 재발 방지).

    진입 유동성의 정본은 **거래·후보**다. 설정 쪽 필드는 기본값이 테이커라 읽는 순간
    엔진이 실제로 한 것과 반대를 본다.
    """
    offenders: dict[str, list[int]] = {}
    for root in _SCAN_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel in _ALLOWED:
                continue
            lines = _config_attribute_reads(path)
            if lines:
                offenders[rel] = lines
    assert not offenders, (
        "설정 객체에서 진입 유동성을 읽는 곳이 있습니다 — 거래(`trade.entry_liquidity`)나 "
        f"후보(`cand.entry_liquidity`)를 읽으십시오: {offenders}"
    )


def test_the_wiring_guard_actually_detects_the_old_pattern(tmp_path: pathlib.Path) -> None:
    """가드가 공허하지 않다 — 옛 코드 모양을 주면 실제로 잡는다."""
    sample = tmp_path / "sample.py"
    sample.write_text("x = costs.slippage_for(cfg.entry_liquidity)\n", encoding="utf-8")
    assert _config_attribute_reads(sample) == [1]


def test_production_config_still_carries_the_stale_default() -> None:
    """함정이 아직 거기 있다는 기록 — 그래서 가드가 필요하다.

    `harness.build_config`은 `entry_liquidity`를 아무 데도 안 넘기므로 프로덕션 설정은
    **테이커**를 들고 다니는데, 그 실행의 거래는 전부 **메이커** 진입이다.
    """
    cfg = harness.build_config("1h")
    assert cfg.entry_liquidity is Liquidity.TAKER
    trade = _trade(cfg, _cand())
    assert trade.entry_liquidity is Liquidity.MAKER


def test_reentry_and_ladder_paths_keep_the_recorded_liquidity() -> None:
    """부분 청산(래더)·재진입 후보도 자기 유동성을 잃지 않는다(WAN-345 부류 예방)."""
    cfg = _cfg()
    cand = replace(_cand(), is_reentry=True)
    assert _trade(cfg, cand).entry_liquidity is Liquidity.MAKER


# --------------------------------------------------------------------------- #
# §5 — 공개 CSV 보정 도구 (backtest/wan396_entry_slippage_correction.py)
# --------------------------------------------------------------------------- #


def test_published_csv_correction_holds_the_identity_on_every_row() -> None:
    """여섯 CSV 전 행에서 검산 (a)가 기계 정밀도로 닫힌다 — 보정이 정당한 근거.

    닫히지 않으면 `load_rows`가 죽는다. 여기서는 실제로 읽어 잔차 크기까지 확인한다.
    """
    from backtest.wan396_entry_slippage_correction import SOURCE_CSVS, load_rows

    rows = load_rows()
    assert rows
    assert max(r.identity_abs for r in rows) < 1e-9
    assert {r.source for r in rows} == set(SOURCE_CSVS)


def test_published_csv_correction_leaves_the_independent_gross_untouched() -> None:
    """검산 (b) — 이 버그에 안 걸리는 자(`mean_gross_r_after_slippage`)가 안 움직인다."""
    from backtest.wan396_entry_slippage_correction import load_rows

    deltas = [r.independent_gross_delta for r in load_rows() if r.independent_gross_delta]
    assert deltas, "wan394 계열에 그 열이 있어야 한다"
    assert max(deltas) < 1e-9


def test_correction_only_moves_the_three_columns() -> None:
    """보정 대상은 셋뿐 — `net_r`은 목록에 없다(판정 (가))."""
    from backtest.wan396_entry_slippage_correction import CORRECTED_COLUMNS

    assert CORRECTED_COLUMNS == ("gross_r", "slippage_r", "cost_r")
    assert "net_r" not in CORRECTED_COLUMNS and "mean_net_r" not in CORRECTED_COLUMNS


def test_correction_rejects_a_row_it_cannot_explain() -> None:
    """설명 안 되는 슬리피지를 **조용히 보정하지 않는다** — 죽는다."""
    from backtest.wan396_entry_slippage_correction import correct_row

    row = {
        "arm": "made_up",
        "segment": "oos_warm",
        "entry_fee_r": "0.03",
        "slippage_r": "9.99",
        "stop_fee_r": "0.03",
        "other_fee_r": "0.0",
        "take_profit_fee_r": "0.01",
        "gross_r": "0.1",
        "cost_r": "0.2",
        "net_r": "-0.1",
    }
    corrected = correct_row("fake.csv", row, k=2.4987506246876565, kx=1.250625312656328)
    assert corrected.identity_abs > 1e-9


def test_correction_verdict_uses_the_same_noise_band_as_wan370() -> None:
    """판정 자를 새로 쓰지 않는다 — ±0.005R은 0과 구분 못 한다(WAN-366/370 규약)."""
    from backtest.wan370_cost_decomposition import NOISE_R
    from backtest.wan396_entry_slippage_correction import verdict

    assert NOISE_R == 0.005
    assert verdict(0.05).startswith("(나)")
    assert verdict(-0.05).startswith("(가)")
    assert verdict(NOISE_R / 2).startswith("(0 근처)")
