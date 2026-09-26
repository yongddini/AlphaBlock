"""WAN-430 — 스토캐스틱 팔의 15m + TF별 분해.

무엇을 **동작으로** 고정하는가:
- `wan424.TIMEFRAMES`를 **건드리지 않는다**(건드리면 공개 CSV와 재현 검산이 함께 죽는다).
- 스코프가 **라벨 필터가 아니라 재배치**다(`place`를 스코프마다 다시 부른다 — WAN-316/389).
- 후보·팔·필터·배치가 **wan424의 그 객체**다(사본을 만들면 두 표가 조용히 갈라진다).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pytest

from backtest import wan424_stoch_ob_arm as wan424
from backtest.wan424_stoch_ob_arm import ArmRow
from backtest.wan430_stoch_arm_15m import (
    FLOOR_CSV,
    FLOOR_SUMMARY_MD,
    GRID_CSV,
    NEW_TIMEFRAME,
    SCOPE_ALL,
    SCOPE_NO_15M,
    SUMMARY_MD,
    TIMEFRAMES_10,
    FloorRow,
    ScopedRow,
    _cells_in_scope,
    build_arm_cells,
    build_base_payloads,
    checksum_lines,
    filtered_payloads,
    frame_to_rows,
    gate_lines,
    place,
    render_floor_summary,
    render_summary,
    rows_to_frame,
    run_grid,
    scopes_for,
)


def _row(
    scope: str,
    *,
    hold: int = 4,
    floor: float = 0.04,
    threshold: float | None = 25.0,
    segment: str = "oos_warm",
    trades: int = 679,
    net: float = 0.1646,
) -> ScopedRow:
    return ScopedRow(
        scope=scope,
        hold=hold,
        floor=floor,
        threshold=threshold,
        segment=segment,
        num_trades=trades,
        mean_net_r=net,
        se_net_r=0.025,
        fixed_return=1.12,
        fixed_mdd=0.328,
        compound_return=2.0,
        compound_mdd=0.5,
        compound_ruined=False,
    )


# --------------------------------------------------------------------------- #
# 좌표 — 기존 상수를 건드리지 않는다
# --------------------------------------------------------------------------- #


def test_the_nine_tf_constant_is_untouched() -> None:
    """🚨 여기에 15m을 더하면 공개 `wan424_stoch_ob_arm.csv`가 재현되지 않는다."""
    assert NEW_TIMEFRAME not in wan424.TIMEFRAMES
    assert len(wan424.TIMEFRAMES) == 9


def test_the_new_list_is_the_old_one_plus_fifteen_minutes_only() -> None:
    """축을 하나만 흔든다(WAN-394) — TF 하나 말고는 아무것도 안 더한다."""
    assert tuple(TIMEFRAMES_10) == tuple([NEW_TIMEFRAME, *wan424.TIMEFRAMES])
    assert len(TIMEFRAMES_10) == 10


def test_the_wiring_is_wan424s_objects_not_copies() -> None:
    assert build_base_payloads is wan424.build_base_payloads
    assert build_arm_cells is wan424.build_arm_cells
    assert filtered_payloads is wan424.filtered_payloads
    assert place is wan424.place


# --------------------------------------------------------------------------- #
# 스코프
# --------------------------------------------------------------------------- #


def test_scopes_are_the_two_aggregates_plus_every_timeframe() -> None:
    scopes = scopes_for(TIMEFRAMES_10)
    assert scopes[0] == SCOPE_ALL
    assert scopes[1] == SCOPE_NO_15M
    assert tuple(scopes[2:]) == TIMEFRAMES_10


def test_the_nine_tf_scope_is_dropped_when_there_is_no_fifteen_minutes() -> None:
    """🚨 15m이 없으면 `__no15m__`은 `__all__`과 **같은 지갑**이다 — 라벨만 둘이면 검산 a′가
    「같은 걸 두 번 쟀다」를 통과로 보고한다(WAN-367 하드 제로 부류)."""
    scopes = scopes_for(wan424.TIMEFRAMES)
    assert SCOPE_NO_15M not in scopes
    assert scopes[0] == SCOPE_ALL


def test_scope_order_follows_the_canonical_timeframe_order() -> None:
    """표 순서가 입력 순서에 흔들리면 같은 표가 실행마다 다르게 보인다."""
    assert scopes_for(("4h", "15m", "1h")) == (SCOPE_ALL, SCOPE_NO_15M, "15m", "1h", "4h")


class _P:
    def __init__(self, symbol: str, timeframe: str) -> None:
        self.symbol, self.timeframe = symbol, timeframe


def test_cells_in_scope_picks_the_right_wallet() -> None:
    payloads = [_P("BTC", "15m"), _P("BTC", "1h"), _P("ETH", "15m")]
    assert len(_cells_in_scope(payloads, SCOPE_ALL)) == 3  # type: ignore[arg-type]
    assert [p.timeframe for p in _cells_in_scope(payloads, SCOPE_NO_15M)] == ["1h"]  # type: ignore[arg-type]
    assert len(_cells_in_scope(payloads, "15m")) == 2  # type: ignore[arg-type]
    assert _cells_in_scope(payloads, "4h") == []  # type: ignore[arg-type]


def test_every_scope_gets_its_own_placement_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """🚨 스코프는 **재배치**다 — 한 번 배치하고 거래를 라벨로 나누면 자본·슬롯 경합이 빠진다
    (WAN-316/389가 못 박은 구분). `place` 호출 수와 넘어간 칸 집합으로 건다."""
    seen: list[tuple[str, ...]] = []

    def fake_place(payloads: Any, **kwargs: Any) -> list[ArmRow]:
        seen.append(tuple(sorted(p.timeframe for p in payloads)))
        return []

    payloads = [_P("BTC", "15m"), _P("BTC", "4h")]
    monkeypatch.setattr("backtest.wan430_stoch_arm_15m.place", fake_place)
    monkeypatch.setattr("backtest.wan430_stoch_arm_15m.filtered_payloads", lambda *a, **k: payloads)
    monkeypatch.setattr("backtest.wan430_stoch_arm_15m.FLOORS", (0.04,))
    monkeypatch.setattr("backtest.wan430_stoch_arm_15m.THRESHOLDS", (25.0,))
    monkeypatch.setattr("backtest.wan430_stoch_arm_15m.HOLD_BARS", (4,))

    run_grid(payloads, [], timeframes=("15m", "4h"), log=False)  # type: ignore[arg-type]
    assert seen == [("15m", "4h"), ("4h",), ("15m",), ("4h",)]


# --------------------------------------------------------------------------- #
# 행 — wan424 타입과 오갈 수 있어야 한다
# --------------------------------------------------------------------------- #


def test_a_scoped_row_round_trips_through_the_wan424_type() -> None:
    """재현 검산(`reference_check`)이 `ArmRow`를 받으므로 스코프를 떼고 돌려줄 수 있어야 한다."""
    arm = ArmRow(4, 0.04, 25.0, "oos_warm", 679, 0.1646, 0.025, 1.12, 0.328, 2.0, 0.5, False)
    scoped = ScopedRow.of(SCOPE_NO_15M, arm)
    assert scoped.scope == SCOPE_NO_15M
    assert scoped.as_arm_row() == arm


def test_the_csv_round_trip_restores_a_missing_threshold() -> None:
    """`threshold=None`이 NaN으로 갔다 오면 `%K 끔` 팔이 조용히 사라진다."""
    rows = [_row(SCOPE_ALL, threshold=None), _row("15m")]
    restored = frame_to_rows(rows_to_frame(rows))
    assert restored[0].threshold is None
    assert restored[1].threshold == pytest.approx(25.0)
    assert not any(isinstance(r.threshold, float) and math.isnan(r.threshold) for r in restored)


# --------------------------------------------------------------------------- #
# 관문 ③ · 검산
# --------------------------------------------------------------------------- #


def test_the_gate_counts_timeframes_above_the_noise_line() -> None:
    rows = [
        _row("15m", net=-0.02),
        _row("1h", net=0.10),
        _row("4h", net=0.002),  # 잡음선 안 → 양수로 세지 않는다
    ]
    (line,) = gate_lines(rows, timeframes=("15m", "1h", "4h"))
    assert "1/3 TF" in line
    # −0.02 ± 0.05 는 0과 구분되지 않는다 — 「음수」로 단정하지 않는다.
    assert "15m **⚠️ 부호 미결정**" in line


def test_the_gate_prints_both_rulers_because_they_disagree() -> None:
    """🚨 자가 둘이고 답이 다르다 — 라벨이 아니라 **두 숫자가 실제로 갈리는 판**으로 건다.

    엄격한 자만 찍으면 WAN-423 §6의 「9개 중 8개」 옆에서 「15m이 관문을 무너뜨렸다」로 읽히는데,
    무너뜨린 것은 15m이 아니라 **자**다. 이 판은 원래 자로 3/3인데 엄격한 자로는 0/3이다.
    """
    rows = [
        _row("15m", net=0.002),
        _row("1h", net=0.003),
        _row("4h", net=0.004),  # 셋 다 양수지만 전부 잡음선 안
    ]
    (line,) = gate_lines(rows, timeframes=("15m", "1h", "4h"))
    assert "`net R > 0`) 2/2 TF → **3/3 TF**(15m 포함)" in line
    assert "0/3 TF" in line  # 엄격한 자로는 하나도 안 선다


def test_the_raw_ruler_shows_what_adding_fifteen_minutes_did() -> None:
    """원래 자의 분모가 9 → 10으로 늘고 그 옆에 15m 포함 판이 선다(완료기준 2)."""
    tfs = ("15m", "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d", "1w")
    rows = [_row(tf, net=0.10) for tf in tfs]
    (line,) = gate_lines(rows, timeframes=tfs)
    assert "9/9 TF → **10/10 TF**(15m 포함)" in line


def test_the_raw_ruler_does_not_apply_the_sample_gate() -> None:
    """원래 자는 표본 하한을 걸지 않는다 — 걸면 WAN-423 §6과 다른 수가 된다."""
    rows = [_row("15m", net=0.30, trades=13), _row("1h", net=0.10)]
    (line,) = gate_lines(rows, timeframes=("15m", "1h"))
    assert "1/1 TF → **2/2 TF**(15m 포함)" in line  # 13거래짜리도 원래 자에는 들어간다
    assert "1/1 TF (표본 미달 1TF 제외)" in line  # 엄격한 자에서는 빠진다


def test_the_gate_reports_fifteen_minutes_even_when_it_is_positive() -> None:
    rows = [_row("15m", net=0.08), _row("1h", net=0.10)]
    (line,) = gate_lines(rows, timeframes=("15m", "1h"))
    assert "2/2 TF" in line
    assert "15m **양수**" in line
    assert "+0.0800R" in line


def test_the_gate_ignores_segments_other_than_the_confirmation_one() -> None:
    """앞구간을 세면 「뒷구간 양수」가 아니라 다른 말이 된다."""
    rows = [_row("15m", segment="is", net=0.5), _row("1h", net=0.10)]
    (line,) = gate_lines(rows, timeframes=("15m", "1h"))
    assert "1/1 TF" in line  # `is` 행은 세지 않는다
    assert "15m **—**" in line


def test_the_checksum_flags_a_mismatch_against_the_published_table(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🚨 (a′)가 실패를 실패로 찍어야 한다 — 통과만 찍으면 검산이 아니다."""
    published = tmp_path / "wan424_stoch_ob_arm.csv"
    pd.DataFrame(
        [
            {
                "hold": 4,
                "floor": 0.04,
                "threshold": 25.0,
                "segment": "oos_warm",
                "num_trades": 679,
                "mean_net_r": 0.1646,
            }
        ]
    ).to_csv(published, index=False)
    monkeypatch.setattr(wan424, "CSV_PATH", published)

    ok = checksum_lines([_row(SCOPE_NO_15M, trades=679, net=0.1646)])
    assert any(line.startswith("- ✅ (a′)") for line in ok)

    bad = checksum_lines([_row(SCOPE_NO_15M, trades=678, net=0.1646)])
    assert any(line.startswith("- ❌ (a′)") for line in bad)


def test_the_checksum_skips_instead_of_lying_when_there_is_no_nine_tf_scope() -> None:
    lines = checksum_lines([_row("15m")])
    assert any("건너뜀" in line for line in lines)
    assert not any("✅ (a′)" in line for line in lines)


def test_checksum_b_is_labelled_observation() -> None:
    """TF별 합 ≠ 한 지갑은 **틀린 게 아니라 경합의 크기**다."""
    rows = [_row(SCOPE_ALL, trades=1000), _row("15m", trades=600), _row("1h", trades=500)]
    lines = checksum_lines(rows)
    (line,) = [x for x in lines if "(b)" in x]
    assert "관측" in line
    assert "10.00%p" in line


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def test_the_summary_has_a_standalone_fifteen_minute_table() -> None:
    body = render_summary([_row(SCOPE_ALL), _row("15m")], timeframes=TIMEFRAMES_10)
    assert "15m 단독" in body
    assert "처음 내는 행" in body


def test_the_summary_says_the_scope_is_a_re_placement() -> None:
    body = render_summary([_row(SCOPE_ALL)], timeframes=TIMEFRAMES_10)
    assert "라벨 필터가 아니라 재배치" in body
    assert "WAN-213/316" in body


def test_the_summary_carries_the_fifteen_minute_fragility_warning() -> None:
    """15m이 양수로 나오면 이 경고가 헤드라인 옆에 있어야 한다(이슈 완료기준 3)."""
    body = render_summary([_row("15m", net=0.2)], timeframes=("15m",))
    assert "가장 취약하다" in body
    assert "양수여도" in body


def test_the_summary_says_which_gates_were_not_rerun() -> None:
    """관문 넷 중 셋은 WAN-423 좌표의 것이다 — 안 돌린 것을 밝힌다."""
    body = render_summary([_row("15m")], timeframes=("15m",))
    assert "다시 돌리지 않았다" in body or "다시 돌리지 않았" in body
    assert "WAN-394" in body


@pytest.mark.skipif(not GRID_CSV.exists(), reason="공개 CSV 미적재")
def test_the_published_grid_has_every_scope_and_reproduces_the_nine_tf_table() -> None:
    frame = pd.read_csv(GRID_CSV)
    scopes = set(frame.scope.unique())
    assert SCOPE_ALL in scopes and SCOPE_NO_15M in scopes
    assert NEW_TIMEFRAME in scopes
    rows = frame_to_rows(frame)
    assert any(line.startswith("- ✅ (a′)") for line in checksum_lines(rows))


# --------------------------------------------------------------------------- #
# §2 손절폭 하한 — 머리말이 **그 아래 표와 어긋나면 안 된다**
# --------------------------------------------------------------------------- #


def _floor_row(floor: float, *, trades: int, width: float) -> FloorRow:
    return FloorRow(
        floor=floor,
        hold=4,
        threshold=25.0,
        segment="oos_warm",
        num_trades=trades,
        mean_net_r=0.1,
        se_net_r=0.05,
        mean_cost_r=0.03,
        mean_gross_r=0.13,
        median_stop_width=width,
    )


def test_the_floor_header_does_not_claim_the_top_floor_is_above_the_maximum() -> None:
    """🚨 초판이 표 **바로 위에서** 그 표와 어긋나는 말을 했다 — 「하한 4%는 분포의 최댓값보다
    위」라고 적어 놓고 아래 표에 하한 4% 20거래가 실려 있었다.

    이 저장소가 반복해 경계한 **「라벨과 동작이 어긋남」**(WAN-91/95/112/123/159/194)의
    **리포트 축**이다. 가장 높은 하한에 거래가 있으면 그 문장은 **거짓**이므로, 그 판을 만들어
    머리말이 그 주장을 하지 않는지 건다.
    """
    rows = [_floor_row(0.005, trades=4457, width=0.0086), _floor_row(0.04, trades=20, width=0.049)]
    text = render_floor_summary(rows)
    top = max(r.floor for r in rows)
    assert any(r.floor == top and r.num_trades > 0 for r in rows)
    assert "최대값보다도 위" not in text
    assert "최댓값보다 위" in text  # 아니라고 밝히는 정정 문장 쪽
    assert "상위 1%보다도 바깥" in text


def test_the_floor_header_says_the_distribution_number_is_btc_measured() -> None:
    """0.398%·2.15%는 **BTC 실측**이지 31종목 전체의 값이 아니다 — 출처를 안 밝히면 다음 사람이
    유니버스 전체의 분위로 읽는다."""
    text = render_floor_summary([_floor_row(0.005, trades=10, width=0.009)])
    assert "BTC 15m 손절폭" in text
    assert "실측" in text


def test_report_paths_are_module_relative_not_cwd_relative() -> None:
    """🚨 CWD 상대 경로면 저장소 루트 밖에서 돌릴 때 **엉뚱한 자리에 쓴다**.

    그리고 `wan424`와 **같은 디렉터리**라야 재현 검산(a′)이 상대 표를 찾는다 — 두 모듈이 서로
    다른 곳에 쓰면 「비트 일치」가 「파일 없음」으로 조용히 건너뛰어진다.
    """
    for path in (GRID_CSV, SUMMARY_MD, FLOOR_CSV, FLOOR_SUMMARY_MD):
        assert path.is_absolute(), path
        assert path.parent == wan424.REPORT_DIR, path
