"""WAN-301 롱+숏 북 정밀 위험 측정의 자(尺)를 동작으로 고정한다.

지키는 것:

1. **시드 평균** — 탈락 렌즈의 위험 행이 (스코프, 팔, 구간) 키로 시드 간 평균되고,
   `num_seeds=1` 평균은 원값을 그대로 보존한다(baseline 팔 불변).
2. **델타·배율** — Δ MDD(롱+숏 − 롱-온리)와 MDD 배율이 올바로 계산되고, 분모 0 근처면
   배율을 정의하지 않는다(WAN-115 부호 함정 가드).
3. **CSV 왕복·append 병합** — 렌즈 키를 포함해 보존·덮어쓰고, 단일 TF append가 기존
   `all` 스코프를 지우지 않는다(wan288/293/296 규약).
4. **컴퓨트 노브(WAN-301 신설)** — `run_cells`가 `cold_segments`/`engine_check`를 받고,
   실데이터에서 `cold_segments=False`가 full·oos_warm을 비트 보존하며 차가운 구간 키를
   **아예 만들지 않는다**(빈 값 위장 금지). `engine_check=False`는 검산 열만 None으로 둔다.
5. **커밋된 baseline·pen_5bp 행이 wan293 월별 북 CSV와 교차 검산** — 같은 후보·같은 북의
   두 접기(월별 vs 전체)라 ∏(1+월수익) ≡ total_return · Σ월 청산수 ≡ 거래수.
6. **요약 렌더** — 판정·델타 표·경고(바닥선 · 복리 착시 · WAN-98 · 측정 전용)가 나온다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import _Task, run_cell, run_cells
from backtest.wan288_monthly_long_short import ARM_LONG_ONLY, ARM_LONG_SHORT
from backtest.wan301_short_book_risk import (
    DEFAULT_LENSES,
    REF_LENS,
    RiskRow,
    _average_risk,
    build_summary_markdown,
    find_row,
    mdd_delta,
    mdd_ratio,
    merge_risk,
    risk_from_csv,
    risk_to_frame,
)

# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #


def _row(
    lens: str = "baseline",
    scope: str = "all",
    arm: str = ARM_LONG_ONLY,
    segment: str = "oos_warm",
    *,
    mdd: float = 0.05,
    ret: float = 0.5,
    liq: float = 0.0,
    risk: float = 0.05,
    trades: float = 100.0,
    seeds: int = 1,
) -> RiskRow:
    return RiskRow(
        lens=lens,
        scope=scope,
        arm=arm,
        segment=segment,
        num_cells=27,
        num_trades=trades,
        win_rate=0.5,
        total_return=ret,
        max_drawdown=mdd,
        peak_concurrency=3.0,
        max_concurrent_risk=risk,
        max_open_notional_ratio=1.2,
        liquidation_events=liq,
        skipped_cell_busy=10.0,
        skipped_notional=2.0,
        num_seeds=seeds,
    )


# --------------------------------------------------------------------------- #
# 1. 시드 평균
# --------------------------------------------------------------------------- #


def test_average_risk_folds_across_seeds() -> None:
    seed0 = [_row("pen_5bp_drop_50", mdd=0.10, ret=0.4, trades=90.0)]
    seed1 = [_row("pen_5bp_drop_50", mdd=0.20, ret=0.6, trades=110.0)]
    out = _average_risk([seed0, seed1], "pen_5bp_drop_50", 2)
    assert len(out) == 1
    r = out[0]
    assert abs(r.max_drawdown - 0.15) < 1e-12
    assert abs(r.total_return - 0.5) < 1e-12
    assert abs(r.num_trades - 100.0) < 1e-12
    assert r.num_seeds == 2
    assert r.num_cells == 27  # 결정적 메타는 보존


def test_average_risk_single_seed_preserves_values() -> None:
    """baseline 팔 불변의 근거 — num_seeds=1 평균은 원값 그대로."""
    rows = [_row(REF_LENS, mdd=0.123456789, ret=0.987654321, risk=0.0777)]
    out = _average_risk([rows], REF_LENS, 1)
    assert len(out) == 1
    assert out[0].max_drawdown == 0.123456789
    assert out[0].total_return == 0.987654321
    assert out[0].max_concurrent_risk == 0.0777
    assert out[0].num_seeds == 1


# --------------------------------------------------------------------------- #
# 2. 델타 · 배율
# --------------------------------------------------------------------------- #


def test_mdd_delta_and_ratio() -> None:
    rows = [
        _row(arm=ARM_LONG_ONLY, mdd=0.04),
        _row(arm=ARM_LONG_SHORT, mdd=0.12),
    ]
    delta = mdd_delta(rows, "baseline", "all", "oos_warm")
    assert delta is not None and abs(delta - 0.08) < 1e-12
    ratio = mdd_ratio(rows, "baseline", "all", "oos_warm")
    assert ratio is not None and abs(ratio - 3.0) < 1e-12
    # 한쪽 팔이 없으면 정의하지 않는다
    assert mdd_delta(rows, "pen_5bp", "all", "oos_warm") is None
    assert mdd_ratio(rows[:1], "baseline", "all", "oos_warm") is None


def test_mdd_ratio_zero_denominator_guard() -> None:
    rows = [
        _row(arm=ARM_LONG_ONLY, mdd=0.0),
        _row(arm=ARM_LONG_SHORT, mdd=0.12),
    ]
    # 분모 0 근처면 배율을 정의하지 않는다(WAN-115 부호 함정 가드). 델타는 성립한다.
    assert mdd_ratio(rows, "baseline", "all", "oos_warm") is None
    delta = mdd_delta(rows, "baseline", "all", "oos_warm")
    assert delta is not None and abs(delta - 0.12) < 1e-12


def test_return_over_mdd_undefined_when_mdd_zero() -> None:
    assert _row(mdd=0.0).return_over_mdd is None
    r = _row(mdd=0.25, ret=0.5).return_over_mdd
    assert r is not None and abs(r - 2.0) < 1e-12


# --------------------------------------------------------------------------- #
# 3. CSV 왕복 · append 병합
# --------------------------------------------------------------------------- #


def test_risk_roundtrip(tmp_path: Path) -> None:
    rows = [
        _row("pen_5bp_drop_50", "1h", ARM_LONG_SHORT, "full", trades=99.4, liq=0.2, seeds=5),
        _row(REF_LENS, "all", ARM_LONG_ONLY, "oos_warm"),
    ]
    path = tmp_path / "risk.csv"
    risk_to_frame(rows).to_csv(path, index=False)
    assert risk_from_csv(path) == rows


def test_merge_risk_overwrites_by_key_and_keeps_all_scope() -> None:
    existing = [
        _row(REF_LENS, "all", ARM_LONG_ONLY, "oos_warm", mdd=0.03),
        _row(REF_LENS, "1h", ARM_LONG_ONLY, "oos_warm", mdd=0.05),
    ]
    new = [
        _row(REF_LENS, "1h", ARM_LONG_ONLY, "oos_warm", mdd=0.07),  # 덮어씀
        _row(REF_LENS, "15m", ARM_LONG_ONLY, "oos_warm", mdd=0.09),  # 새 스코프
    ]
    merged = merge_risk(existing, new)
    by_scope = {r.scope: r for r in merged}
    # 단일 TF append가 기존 `all`을 지우지 않는다(wan296 규약) — 15m은 추가, 1h는 덮어씀.
    assert set(by_scope) == {"all", "1h", "15m"}
    assert by_scope["all"].max_drawdown == 0.03
    assert by_scope["1h"].max_drawdown == 0.07
    assert by_scope["15m"].max_drawdown == 0.09


# --------------------------------------------------------------------------- #
# 4. 컴퓨트 노브 (WAN-301 신설 · wan169 배선)
# --------------------------------------------------------------------------- #


def test_run_cells_accepts_compute_knobs() -> None:
    params = inspect.signature(run_cells).parameters
    assert "cold_segments" in params and params["cold_segments"].default is True
    assert "engine_check" in params and params["engine_check"].default is True


def test_cold_segments_skip_preserves_full_and_warm_on_real_data() -> None:
    """실데이터 게이트 — 노브가 full·oos_warm을 비트 보존하고 차가운 키를 만들지 않는다.

    작은 창(BTC 4h 3개월)으로 게이트한다 — 실데이터가 없으면 skip(CI 기본).
    """
    start_ms = parse_date_ms("2024-01-01")
    end_ms = parse_date_ms("2024-04-01")
    market = harness.load_market_data(
        "BTC/USDT:USDT", "4h", start_ms=start_ms, end_ms=end_ms, need_1m=False, funding=False
    )
    if market.empty:
        pytest.skip("BTC 4h 실데이터가 없어 컴퓨트 노브 대조를 건너뜁니다(CI 기본).")

    base_task = _Task(symbol="BTC/USDT:USDT", timeframe="4h", start_ms=start_ms, end_ms=end_ms)
    full_payload = run_cell(base_task, log=False)
    light_payload = run_cell(
        _Task(
            symbol="BTC/USDT:USDT",
            timeframe="4h",
            start_ms=start_ms,
            end_ms=end_ms,
            cold_segments=False,
            engine_check=False,
        ),
        log=False,
    )
    # 차가운 구간 키가 아예 없다(빈 값 위장 금지 — 요청하면 KeyError로 죽는다).
    assert set(light_payload.candidates) == {"full"}
    assert set(full_payload.candidates) == {"full", "is", "oos"}
    # full 후보·경계는 비트 보존.
    assert light_payload.candidates["full"] == full_payload.candidates["full"]
    assert light_payload.boundary_ms == full_payload.boundary_ms
    # 행: light는 full + oos_warm 두 행이고, 검산 열만 빼면 full 행과 값이 같다.
    light_rows = {r.segment: r for r in light_payload.rows}
    full_rows = {r.segment: r for r in full_payload.rows}
    assert set(light_rows) == {"full", "oos_warm"}
    for seg in ("full", "oos_warm"):
        lite, ref = light_rows[seg], full_rows[seg]
        assert (lite.num_candidates, lite.num_trades, lite.total_return, lite.max_drawdown) == (
            ref.num_candidates,
            ref.num_trades,
            ref.total_return,
            ref.max_drawdown,
        )
    # engine_check=False → 검산 열은 None(조용한 통과가 아니라 검산 대상 축소).
    assert light_rows["full"].engine_total_return is None
    assert full_rows["full"].engine_total_return is not None


# --------------------------------------------------------------------------- #
# 5. 커밋된 행이 wan293 월별 북 CSV와 교차 검산 (완료기준 2)
# --------------------------------------------------------------------------- #


def test_committed_rows_cross_check_wan293() -> None:
    """같은 후보·같은 북(cap_only 5배 · WAN-180 스냅샷)의 두 접기가 서로를 검산한다.

    wan293은 북 에쿼티를 월별로 접었고 wan301은 전체를 접었다 — 시드 평균이 없는 렌즈
    (baseline·pen_5bp)에서 ∏(1+월수익) ≡ 1+total_return, Σ월 청산수 ≡ 거래수여야 한다.
    (시드 평균 렌즈는 평균의 곱 ≠ 곱의 평균이라 대조에서 뺀다.)

    ⚠️ `all` 스코프는 대조에서 뺀다 — 두 CSV의 `all`은 TF 구성이 다르다(wan301은 WAN-302가
    4TF(15m+1h+2h+4h)를 한 실행으로 돌려 재산출했고, wan293의 `all`은 WAN-296 단일 TF
    append 규약으로 1h+2h+4h에 머문다). 같은 라벨이지만 다른 북이라 비교하면 안 된다.
    TF 스코프 대조가 이 사슬을 잇는다 — 15m 스코프 대조가 곧 완료기준 3(15m long_only ≡
    채택 북 계열)의 실체다.
    """
    reports = Path("backtest/reports")
    w301 = reports / "wan301_book_risk.csv"
    w293 = reports / "wan293_monthly_book.csv"
    if not (w301.exists() and w293.exists()):
        pytest.skip("리포트 CSV가 없음(측정 미실행 환경)")

    risk = pd.read_csv(w301)
    monthly = pd.read_csv(w293)
    ours = risk[(risk.num_seeds == 1) & (risk.segment == "oos_warm") & (risk.scope != "all")]
    assert len(ours) > 0
    checked_scopes: set[str] = set()
    for rec in ours.to_dict("records"):
        ref = monthly[
            (monthly.lens == rec["lens"])
            & (monthly.scope == rec["scope"])
            & (monthly.arm == rec["arm"])
        ]
        if ref.empty:
            continue  # wan293이 안 커버한 스코프는 대조 불가.
        growth = float((1.0 + ref.monthly_return).prod())
        assert abs(growth - (1.0 + rec["total_return"])) / max(abs(growth), 1.0) < 1e-9, (
            rec["lens"],
            rec["scope"],
            rec["arm"],
        )
        assert abs(float(ref.num_exits.sum()) - rec["num_trades"]) < 1e-9
        checked_scopes.add(str(rec["scope"]))
    # 네 TF 전부 대조돼야 한다 — 15m이 빠지면 완료기준 3이 주장으로만 남는다(WAN-302).
    assert {"15m", "1h", "2h", "4h"} <= checked_scopes, checked_scopes


def test_committed_csv_covers_four_tf_and_recomputed_all() -> None:
    """WAN-302 완료기준 1을 구조로 고정 — 15m 스코프가 있고 `all`이 4TF(36칸)로 재산출됐다.

    사용자 결정(2026-08-13)으로 `pen_5bp_drop_50`은 15m을 재지 않는다 — 그 렌즈는 WAN-301
    초판(1h·2h·4h · `all`=27칸) 그대로 남고, baseline·pen_5bp만 15m 행 + `all` 36칸이다.
    (렌즈 2 × 스코프 5 + 렌즈 1 × 스코프 4) × 팔 2 × 구간 2 = 56행.
    """
    path = Path("backtest/reports/wan301_book_risk.csv")
    if not path.exists():
        pytest.skip("리포트 CSV가 없음(측정 미실행 환경)")
    frame = pd.read_csv(path)
    assert sorted(frame.scope.unique()) == ["15m", "1h", "2h", "4h", "all"]
    assert len(frame) == 56
    measured = frame[frame.lens.isin(["baseline", "pen_5bp"])]
    frozen = frame[frame.lens == "pen_5bp_drop_50"]
    # baseline·pen_5bp: 15m 포함 · `all`은 9종목 × 4TF = 36칸으로 재산출됐다.
    assert set(measured[measured.scope == "all"].num_cells) == {36}
    assert "15m" in set(measured.scope)
    # drop_50: 15m 없음 · `all`은 3TF 시절(27칸) 동결 — 렌즈 간 `all` 직접 비교 금지.
    assert set(frozen[frozen.scope == "all"].num_cells) == {27}
    assert "15m" not in set(frozen.scope)
    assert set(frame[frame.scope != "all"].num_cells) == {9}
    key_cols = ["lens", "scope", "arm", "segment"]
    assert not frame.duplicated(subset=key_cols).any()


# --------------------------------------------------------------------------- #
# 6. 요약 렌더
# --------------------------------------------------------------------------- #


def test_summary_renders_key_sections() -> None:
    rows = [
        _row(REF_LENS, "all", ARM_LONG_ONLY, "oos_warm", mdd=0.04, ret=0.4),
        _row(REF_LENS, "all", ARM_LONG_SHORT, "oos_warm", mdd=0.12, ret=0.6, risk=0.09),
        _row("pen_5bp", "all", ARM_LONG_ONLY, "oos_warm", mdd=0.05, ret=0.3),
        _row("pen_5bp", "all", ARM_LONG_SHORT, "oos_warm", mdd=0.14, ret=0.4),
        _row(REF_LENS, "1h", ARM_LONG_ONLY, "oos_warm", mdd=0.03, ret=0.2),
        _row(REF_LENS, "1h", ARM_LONG_SHORT, "oos_warm", mdd=0.08, ret=0.3),
        _row(REF_LENS, "all", ARM_LONG_ONLY, "full", mdd=0.06, ret=1.0),
        _row(REF_LENS, "all", ARM_LONG_SHORT, "full", mdd=0.16, ret=1.5),
    ]
    md = build_summary_markdown(rows)
    assert "판정" in md
    assert "Δ MDD" in md
    assert "바닥선" in md  # 6년 MDD = 바닥선 경고
    assert "복리 착시" in md
    assert "WAN-98" in md
    assert "WAN-287" in md  # 측정 전용 — 별도 사용자 결정
    assert "3.00배" in md  # 0.04 → 0.12
    assert "funding_gap=False" in md  # 펀딩 공백 없음 확인 줄
    # 표 안에서 렌즈가 순서대로 나온다(판정 문장의 언급과 무관하게 표 셀 기준).
    assert md.index("| baseline |") < md.index("| pen_5bp |")


def test_summary_flags_mixed_all_composition() -> None:
    """렌즈 간 `all` 칸 수가 다르면(드롭 렌즈 15m 생략, WAN-302) 요약이 직접 비교를 막는다."""
    rows = [
        _row(REF_LENS, "all", ARM_LONG_ONLY, "oos_warm"),  # num_cells=27 (헬퍼 기본)
        _row(REF_LENS, "all", ARM_LONG_SHORT, "oos_warm"),
    ]
    # 같은 칸 수면 경고가 없다.
    assert "렌즈 간 `all` 직접 비교 금지" not in build_summary_markdown(rows)
    mixed = rows + [RiskRow(**{**rows[0].model_dump(), "lens": "pen_5bp_drop_50", "num_cells": 36})]
    md = build_summary_markdown(mixed)
    assert "렌즈 간 `all` 직접 비교 금지" in md
    assert "27칸" in md and "36칸" in md


def test_summary_engine_verify_line_render() -> None:
    """검산 문장은 실행 시 실제 verify_cells 결과가 실리고, --from-csv엔 정본 안내가 나온다."""
    rows = [
        _row(REF_LENS, "1h", ARM_LONG_ONLY, "oos_warm"),
        _row(REF_LENS, "1h", ARM_LONG_SHORT, "oos_warm"),
    ]
    md = build_summary_markdown(rows, engine_verify_line="`baseline` 렌즈: ✅ **일치** — 27칸")
    assert "✅ **일치** — 27칸" in md
    md_from_csv = build_summary_markdown(rows)
    assert "실행 로그와 아래 회귀 테스트가 정본" in md_from_csv


def test_summary_flags_gapped_symbols() -> None:
    rows = [
        _row(REF_LENS, "1h", ARM_LONG_ONLY, "oos_warm"),
        _row(REF_LENS, "1h", ARM_LONG_SHORT, "oos_warm"),
    ]
    md = build_summary_markdown(rows, gapped_symbols=["DOGE"])
    assert "펀딩 공백 종목" in md and "DOGE" in md


def test_default_lenses_are_the_three_band() -> None:
    assert DEFAULT_LENSES == ("baseline", "pen_5bp", "pen_5bp_drop_50")
    assert REF_LENS == "baseline"
    assert find_row([], REF_LENS, "all", ARM_LONG_ONLY, "oos_warm") is None
