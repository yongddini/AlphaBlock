"""WAN-395: 익절 배수 꺾임 격자 — 판정 관문과 배선을 **동작으로** 고정한다.

이 파일이 지키는 것은 넷이다:

1. **후보 생성 인자가 WAN-386과 같다** — 검산 (d)(겹치는 배수 4점 ≡ WAN-381 CSV)는 두 모듈이
   **글자 그대로 같은 후보**를 만들 때만 성립하는데, 그것을 5시간짜리 격자로만 확인할 수는
   없다. 두 호출의 `run_cells` 인자를 실제로 캡처해 대조한다(WAN-330 스파이 패턴).
2. **판정이 세 갈래로 갈린다** — 이슈가 착수 전에 못 박은 갈래를 사람이 표를 보고 고르지
   않는다. 코드가 고르고, 그 코드가 여기서 세 방향 전부 시험된다.
3. **부호 관문** — `sign_is_decided`가 거짓이면 「0을 넘었다」를 안 찍는다(WAN-394 §1).
4. **잔존율 함정** — 기준이 0 언저리이거나 부호가 갈리면 비율을 **내지 않는다**(WAN-115).
"""

from __future__ import annotations

from typing import Any

import pytest

from backtest import harness
from backtest import wan386_confirmation_pnl as wan386
from backtest import wan395_exit_multiple_inflection as wan395
from backtest.confirmation_arm import ARM_BASE
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan381_exit_scales import MULTIPLES as WAN381_MULTIPLES
from common.costs import Liquidity

# --------------------------------------------------------------------------- #
# 격자 정의 — 점을 손으로 못 옮기게
# --------------------------------------------------------------------------- #


def test_multiples_extend_wan381_downward_only() -> None:
    """이 이슈가 여는 것은 **아래쪽 두 점**뿐이다 — 위쪽은 WAN-386이 이미 냈다(단조 악화)."""
    assert wan395.NEW_MULTIPLES == (0.4, 0.5)
    assert set(wan395.MULTIPLES) == set(WAN381_MULTIPLES) | set(wan395.NEW_MULTIPLES)
    assert tuple(sorted(wan395.MULTIPLES)) == wan395.MULTIPLES
    # 새 점은 전부 WAN-381 격자의 아래다.
    assert max(wan395.NEW_MULTIPLES) < min(WAN381_MULTIPLES)


def test_checked_multiples_are_exactly_the_overlap() -> None:
    """검산 (d)가 덮는 것은 **겹치는 점 전부**여야 한다 — 골라 덮으면 그만큼 안 본 것이다."""
    assert set(wan395.CHECK_MULTIPLES) == set(WAN381_MULTIPLES)


def test_guard_is_fixed_at_the_adopted_value_not_an_axis() -> None:
    """가드는 축이 아니라 고정값이다(WAN-381 §3이 그 축을 닫았다)."""
    assert wan395.ADOPTED_MULTIPLE == 1.5
    assert wan395.place.__defaults__ is None  # 키워드 전용
    import inspect

    assert inspect.signature(wan395.place).parameters["guard"].default is ADOPTED_STOP_GUARD


# --------------------------------------------------------------------------- #
# 1. 후보 생성 인자가 WAN-386과 같다 (검산 (d)의 전제)
# --------------------------------------------------------------------------- #


def _capture(monkeypatch: pytest.MonkeyPatch, module: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_run_cells(*args: Any, **kwargs: Any) -> list[Any]:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return []

    monkeypatch.setattr(module, "run_cells", fake_run_cells)
    return seen


def test_candidate_kwargs_match_wan386(monkeypatch: pytest.MonkeyPatch) -> None:
    """🚨 두 모듈이 **같은 후보**를 만드는가 — 검산 (d)를 5시간 없이 지키는 자.

    다른 것은 이 이슈가 의도적으로 바꾼 셋(`confirmation_multiples`·`payload_cache`·`fill`)
    뿐이어야 한다. 여기가 어긋나면 겹치는 배수 4점이 비트 일치할 리 없다.
    """
    mine = _capture(monkeypatch, wan395)
    wan395.build_payloads(["BTC/USDT:USDT"], ["4h"], start="2024-01-01", end="2024-02-01", jobs=1)

    theirs = _capture(monkeypatch, wan386)
    wan386.build_payloads(
        ["BTC/USDT:USDT"],
        ["4h"],
        start="2024-01-01",
        end="2024-02-01",
        jobs=1,
        arms=(ARM_BASE,),
    )

    assert mine["args"] == theirs["args"]
    volatile = {"confirmation_multiples", "payload_cache", "fill"}
    assert {k: v for k, v in mine["kwargs"].items() if k not in volatile} == {
        k: v for k, v in theirs["kwargs"].items() if k not in volatile
    }
    assert tuple(mine["kwargs"]["confirmation_multiples"]) == wan395.MULTIPLES


def test_baseline_lens_passes_no_fill_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """공식 렌즈는 `fill`을 **안 넘긴다** — `None`이 곧 baseline이라 WAN-381과 비트 같아진다."""
    seen = _capture(monkeypatch, wan395)
    wan395.build_payloads(["BTC/USDT:USDT"], ["4h"], start="2024-01-01", end="2024-02-01", jobs=1)
    assert seen["kwargs"]["fill"] is None

    seen = _capture(monkeypatch, wan395)
    wan395.build_payloads(
        ["BTC/USDT:USDT"],
        ["4h"],
        start="2024-01-01",
        end="2024-02-01",
        jobs=1,
        lens=wan395.STRESS_LENS,
    )
    assert seen["kwargs"]["fill"] == harness.fill_preset(wan395.STRESS_LENS)


def test_take_profit_liquidity_is_named_in_all_three_places() -> None:
    """완료기준 7 — 후보 생성 · 배치 · LOO 배치 셋 다에서 **채택 회계를 명시**(WAN-370/373).

    🚨 라벨이 아니라 **값과 호출 경로**로 건다: 후보 생성은 `_cell_kwargs()`가 실제로 내는
    값으로, 배치는 `place()` 소스가 그 상수를 넘기는지로, LOO는 그 `place()`를 쓰는지로.
    LOO가 자기 배치를 따로 만들면(= `place`를 안 쓰면) 이 검사가 무의미해지므로 함께 본다.
    """
    import inspect

    assert wan395._cell_kwargs()["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    place_src = inspect.getsource(wan395.place)
    assert "take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY" in place_src
    for fn in (wan395.build_grid, wan395.build_leave_one_out):
        assert "place(" in inspect.getsource(fn), fn.__name__


def test_entry_liquidity_checksum_reads_the_candidate_not_a_label() -> None:
    """검산 (c)는 **후보의 값**을 본다(WAN-370) — 설정 라벨을 읽으면 WAN-396 부류를 놓친다."""
    import inspect

    src = inspect.getsource(wan395.run_checksum)
    assert "c.entry_liquidity is not Liquidity.MAKER" in src
    # 두 이름이 실제로 갈려야 이 검산이 뜻을 갖는다(WAN-373 가드와 같은 전제).
    assert len({Liquidity.MAKER, Liquidity.TAKER}) == 2


# --------------------------------------------------------------------------- #
# 2. 판정 — 세 갈래가 코드로 갈린다
# --------------------------------------------------------------------------- #


def _row(
    multiple: float,
    net: float,
    *,
    segment: str = PRIMARY_OOS,
    lens: str = wan395.BASELINE_LENS,
    stderr: float = 0.0001,
    trades: int = 10_000,
    win_rate: float = 0.5,
) -> wan395.MultipleRow:
    """판정 함수만 시험하는 최소 행 — 무거운 배치 없이 갈래를 고정한다."""
    return wan395.MultipleRow(
        arm=ARM_BASE,
        guard=ADOPTED_STOP_GUARD,
        multiple=multiple,
        segment=segment,
        adopted_point=(multiple == wan395.ADOPTED_MULTIPLE and lens == wan395.BASELINE_LENS),
        num_cells=48,
        num_symbols=12,
        num_trades=trades,
        win_rate=win_rate,
        mean_net_r=net,
        mean_gross_r=net + 0.1,
        total_return_flat=-0.5,
        max_drawdown=0.3,
        return_over_mdd=None,
        peak_concurrency=14,
        max_concurrent_risk=0.11,
        max_effective_concurrent_risk=0.17,
        clamp_rate=0.4,
        mean_effective_risk=0.005,
        liquidation_events=0,
        guard_cut=100,
        guard_kept=900,
        symbols_below_gate=0,
        min_symbol_trades=300,
        lens=lens,
        net_r_stderr=stderr,
        gross_r=net + 0.15,
        cost_r=0.2,
        breakeven_win_rate=(1.0 + 0.2) / (1.0 + multiple),
        breakeven_win_rate_zero_cost=1.0 / (1.0 + multiple),
        win_rate_margin=win_rate - (1.2 / (1.0 + multiple)),
        same_step_tp_trades=int(trades * 0.1),
        same_step_tp_trade_share=0.1,
        same_step_tp_net_r_share=None,
    )


def _curve(values: dict[float, float], **kwargs: Any) -> list[wan395.MultipleRow]:
    return [_row(m, v, **kwargs) for m, v in values.items()]


_BRANCH_A = {0.4: -0.02, 0.5: -0.03, 0.6: -0.05, 0.8: -0.08, 1.0: -0.10, 1.5: -0.12}
_BRANCH_B = {0.4: -0.051, 0.5: -0.049, 0.6: -0.050, 0.8: -0.08, 1.0: -0.10, 1.5: -0.12}
_BRANCH_C = {0.4: -0.09, 0.5: -0.07, 0.6: -0.05, 0.8: -0.08, 1.0: -0.10, 1.5: -0.12}


def test_branch_a_keeps_improving_below_the_grid() -> None:
    """갈래 ① — 0.4~0.5R이 더 좋으면 「꺾임이 아직 더 아래」이고 **끝점을 최적이라 안 쓴다**."""
    line = wan395.inflection_verdict(_curve(_BRANCH_A), segment=PRIMARY_OOS)
    assert "갈래 ①" in line
    assert "최적값" in line  # 끝점 인용 금지 경고가 함께 나가야 한다


def test_branch_b_is_flat_within_the_noise_line() -> None:
    """갈래 ② — 잡음선 안이면 「0.4·0.5R이 더 낫다」고 **말하지 않는다**."""
    line = wan395.inflection_verdict(_curve(_BRANCH_B), segment=PRIMARY_OOS)
    assert "갈래 ②" in line
    assert "잡음선" in line


def test_branch_c_closes_the_axis() -> None:
    """갈래 ③ — 새 점이 더 나쁘면 배수 축도 닫힌다(가드 축이 WAN-381 §3으로 닫힌 것과 같은 형식)."""
    line = wan395.inflection_verdict(_curve(_BRANCH_C), segment=PRIMARY_OOS)
    assert "갈래 ③" in line
    assert "닫힌다" in line


def test_the_three_branches_are_actually_distinct() -> None:
    """돌연변이 확인 — 세 입력이 **서로 다른 문장**을 내야 갈래가 뜻을 갖는다."""
    lines = {
        wan395.inflection_verdict(_curve(v), segment=PRIMARY_OOS)
        for v in (_BRANCH_A, _BRANCH_B, _BRANCH_C)
    }
    assert len(lines) == 3


def test_verdict_refuses_a_partial_curve() -> None:
    """점이 모자라면 갈래를 고르지 않는다 — 「없는 점」을 있는 셈 치면 답이 지어진다."""
    partial = _curve({0.6: -0.05, 1.5: -0.12})
    assert "판정 불가" in wan395.inflection_verdict(partial, segment=PRIMARY_OOS)


def test_noise_line_is_the_repo_convention() -> None:
    """±0.005R은 이 저장소의 규약이다(WAN-366/370) — 결과를 보고 선을 옮기지 못하게 상수로."""
    assert wan395.NOISE_R == 0.005


# --------------------------------------------------------------------------- #
# 3. 부호 관문 (WAN-394 §1)
# --------------------------------------------------------------------------- #


def test_sign_gate_withholds_the_verdict_inside_two_standard_errors() -> None:
    """WAN-381 최선(−0.0023 ± 0.0057)·WAN-394 최선(＋0.0039 ± 0.0079)이 정확히 이 자리다."""
    undecided = _row(0.6, -0.0023, stderr=0.0057)
    assert not wan395.sign_is_decided(undecided)
    line = wan395.sign_line([undecided], segment=PRIMARY_OOS)
    assert "부호가 정해지지 않았다" in line

    decided = _row(0.6, -0.1200, stderr=0.0057)
    assert wan395.sign_is_decided(decided)
    assert "음수" in wan395.sign_line([decided], segment=PRIMARY_OOS)


def test_sign_gate_is_not_vacuous_for_positive_values() -> None:
    """양수가 나와도 **같은 검사**를 한다 — 관문이 한쪽만 막으면 낙관에 유리하게 기운다."""
    assert not wan395.sign_is_decided(_row(0.4, 0.0039, stderr=0.0079))
    assert wan395.sign_is_decided(_row(0.4, 0.0400, stderr=0.0079))


# --------------------------------------------------------------------------- #
# 4. 잔존율 함정 (WAN-115)
# --------------------------------------------------------------------------- #


def test_residual_ratio_is_withheld_when_the_base_is_near_zero() -> None:
    """기준이 0 언저리면 잔존율은 뜻을 잃는다 — 172%가 「유지」로 읽히던 그 자리."""
    rows = [
        _row(0.6, 0.001),
        _row(0.6, -0.02, lens=wan395.STRESS_LENS),
    ]
    line = wan395.residual_line(rows, segment=PRIMARY_OOS)
    assert "잔존율을 내지 않는다" in line
    assert "잔존 " not in line


def test_residual_ratio_is_withheld_when_the_sign_flips() -> None:
    rows = [_row(0.6, 0.05), _row(0.6, -0.02, lens=wan395.STRESS_LENS)]
    assert "잔존율을 내지 않는다" in wan395.residual_line(rows, segment=PRIMARY_OOS)


def test_residual_ratio_is_reported_when_it_means_something() -> None:
    rows = [
        _row(0.6, -0.10, trades=10_000),
        _row(0.6, -0.05, lens=wan395.STRESS_LENS, trades=9_000),
    ]
    line = wan395.residual_line(rows, segment=PRIMARY_OOS)
    assert "잔존 50%" in line
    assert "-10.0%" in line  # 거래 수 감소율을 옆에 둔다


def test_residual_line_says_so_when_section_two_has_not_run() -> None:
    """§2를 안 돌렸으면 **안 돌렸다고 적는다** — 빈칸을 「영향 없음」으로 읽지 않게."""
    line = wan395.residual_line(_curve(_BRANCH_C), segment=PRIMARY_OOS)
    assert "아직 안 돌았다" in line


# --------------------------------------------------------------------------- #
# 5. 손익분기 승률 — 이슈 본문 표(비용 0)를 재현한다
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("multiple", "expected"),
    [(0.4, 0.714), (0.5, 0.667), (0.6, 0.625), (0.8, 0.556), (1.0, 0.500), (1.5, 0.400)],
)
def test_zero_cost_breakeven_matches_the_issue_table(multiple: float, expected: float) -> None:
    """이슈가 인용한 표는 `1/(1+R)`이다 — 그 자를 재현해야 두 표를 이어 읽을 수 있다."""
    assert wan395._row_zero_cost_breakeven(multiple) == pytest.approx(expected, abs=5e-4)


def test_cost_pushes_the_breakeven_line_up() -> None:
    """실제로 넘어야 하는 선은 **비용반영** 판이고, 그것이 더 높다(비용R이 양수인 한)."""
    row = _row(1.5, -0.12)
    assert row.breakeven_win_rate > row.breakeven_win_rate_zero_cost
    assert row.win_rate_margin == pytest.approx(row.win_rate - row.breakeven_win_rate)


# --------------------------------------------------------------------------- #
# 6. 검산 (d) — 겹치는 점만, 채택 가드만, 공식 렌즈만
# --------------------------------------------------------------------------- #


def test_cross_check_reads_only_the_adopted_guard_rows() -> None:
    """WAN-381은 **가드 5점**을 냈다 — 그중 채택 가드 행만 우리 공선의 상대다.

    🚨 가드를 안 거르면 같은 배수의 다른 가드 행과 짝지어져 **차가 0이 아닌 게 정상인데
    「어긋났다」로 보인다**(WAN-386 파일럿이 좌표 차이를 배선 오류로 오독한 그 부류).
    """
    rows = [_row(m, -0.05) for m in wan395.CHECK_MULTIPLES]
    checks = wan395.cross_check_wan381(rows)
    assert checks, "겹치는 행이 있어야 한다"
    assert all("(d)" in c.check for c in checks)
    # 실제 CSV에는 가드 5점이 있는데, 우리는 채택 가드 하나만 낸다 —
    # 그래도 「겹치는 행이 없음」으로 떨어지면 안 된다.
    assert not any(c.metric == "matched_rows" for c in checks)


def test_cross_check_ignores_the_stress_lens_rows() -> None:
    """§2 행은 **다른 렌즈**라 WAN-381(공식 렌즈)의 상대가 아니다 — 섞으면 거짓 불일치가 난다."""
    stress_only = [_row(m, -0.05, lens=wan395.STRESS_LENS) for m in wan395.CHECK_MULTIPLES]
    checks = wan395.cross_check_wan381(stress_only)
    assert [c.metric for c in checks] == ["matched_rows"]


def test_cross_check_reports_a_missing_counterpart_instead_of_passing() -> None:
    """상대 CSV가 없으면 **조용히 통과하지 않는다** — 검산이 없는 것과 통과는 다르다."""
    checks = wan395.cross_check_wan381([], path=wan395.REPORTS_DIR / "does-not-exist.csv")
    assert len(checks) == 1
    assert checks[0].abs_diff == 1.0


def test_cross_check_catches_a_moved_number() -> None:
    """돌연변이 확인 — 값을 흔들면 차가 실제로 벌어진다(공허한 검산이 아니다)."""
    good = [_row(m, -0.05) for m in wan395.CHECK_MULTIPLES]
    baseline_worst = max(c.abs_diff for c in wan395.cross_check_wan381(good))
    moved = [_row(m, -0.05, trades=1) for m in wan395.CHECK_MULTIPLES]
    assert max(c.abs_diff for c in wan395.cross_check_wan381(moved)) > baseline_worst


# --------------------------------------------------------------------------- #
# 7. `--append`가 덮어쓰기가 아니라 갱신이다
# --------------------------------------------------------------------------- #


def test_append_keeps_both_lenses_side_by_side() -> None:
    """§2를 이어 붙여도 §1 행이 살아 있어야 잔존율을 짝지을 수 있다."""
    first = _curve(_BRANCH_C)
    second = [_row(1.5, -0.20, lens=wan395.STRESS_LENS)]
    merged = wan395._merge(first, second)
    assert len(merged) == len(first) + 1
    assert {r.lens for r in merged} == {wan395.BASELINE_LENS, wan395.STRESS_LENS}


def test_append_replaces_the_same_cell_rather_than_duplicating() -> None:
    """같은 (렌즈, 배수, 구간)을 다시 돌리면 **새 행이 이긴다** — 중복 행은 표를 두 배로 읽는다."""
    merged = wan395._merge(_curve(_BRANCH_C), [_row(0.6, -0.99)])
    picked = wan395.pick(merged, multiple=0.6, segment=PRIMARY_OOS)
    assert picked is not None
    assert picked.mean_net_r == -0.99
    assert sum(1 for r in merged if r.multiple == 0.6) == 1


# --------------------------------------------------------------------------- #
# 8. 판정 점 — LOO와 §2가 도는 자리
# --------------------------------------------------------------------------- #


def test_judgment_points_always_include_the_adopted_multiple() -> None:
    """채택 점은 **언제나** 판정 점이다 — 그것이 없으면 「지금과 비교해」가 성립하지 않는다."""
    points = wan395.judgment_multiples(_curve(_BRANCH_C))
    assert wan395.ADOPTED_MULTIPLE in points
    assert 0.6 in points  # 갈래 ③의 최선


def test_judgment_points_do_not_duplicate_when_the_adopted_point_wins() -> None:
    flat = {m: -0.10 if m != 1.5 else -0.01 for m in wan395.MULTIPLES}
    assert wan395.judgment_multiples(_curve(flat)) == [wan395.ADOPTED_MULTIPLE]


def test_flip_row_names_the_reversal() -> None:
    """앞구간에서 고른 값이 뒷구간에서 최선이 아니면 **그 사실을 낸다**(WAN-161)."""
    rows = [
        *_curve({0.4: -0.02, 0.6: -0.05}, segment="is"),
        *_curve({0.4: -0.06, 0.6: -0.03}),
    ]
    is_best, oos_best, flipped = wan395.flip_rows(rows)
    assert (is_best, oos_best) == ("0.4R", "0.6R")
    assert flipped


# --------------------------------------------------------------------------- #
# 9. 「같은 분 익절」 net R 몫 — 100%를 넘으면 퍼센트가 아니다
# --------------------------------------------------------------------------- #


def _with_share(share: float | None) -> wan395.MultipleRow:
    row = _row(0.4, 0.0066)
    return row.model_copy(update={"same_step_tp_net_r_share": share})


def test_share_above_one_is_not_printed_as_a_percentage() -> None:
    """🚨 실측이 `1977%`를 냈다 — 그건 「몇 %쯤」이 아니라 **「나머지는 합쳐서 손실」**이다.

    퍼센트로 적으면 40%쯤으로 읽히므로 배수와 문장으로 바꾼다(WAN-115가 잔존율 172%를
    「유지」로 읽던 자리에서 세운 관행의 이 축 판).
    """
    cell = wan395.same_step_share_cell(_with_share(19.77))
    assert "%" not in cell
    assert "×19.8" in cell and "순손익 전부보다 크다" in cell


def test_share_below_one_is_a_plain_percentage() -> None:
    assert wan395.same_step_share_cell(_with_share(0.48)) == "48%"


def test_negative_share_is_flagged_not_read() -> None:
    """분모가 음수면 부호가 뒤집힌 채 나온다 — 숫자를 숨기지 않되 **읽지 말라고** 적는다."""
    assert "읽지 말 것" in wan395.same_step_share_cell(_with_share(-0.30))


def test_withheld_share_stays_withheld_through_a_csv_round_trip(tmp_path: Any) -> None:
    """🚨 **실제로 뚫렸던 가드다** — pandas가 빈 칸을 NaN으로 읽고 pydantic이 유효한 float으로
    받아 `--from-csv` 요약이 `nan%`를 찍었다(「라벨과 동작이 어긋남」의 직렬화 축 변종).

    표시하는 쪽마다 막지 않고 **모델에서 한 번** 되돌리는지를 왕복으로 건다.
    """
    path = tmp_path / "grid.csv"
    wan395.rows_to_frame([_with_share(None)]).to_csv(path, index=False)
    restored = wan395.grid_from_csv(path)[0]
    assert restored.same_step_tp_net_r_share is None
    assert wan395.same_step_share_cell(restored) == "—(분모가 뜻을 잃음)"


def test_the_round_trip_guard_is_not_vacuous(tmp_path: Any) -> None:
    """돌연변이 확인 — 값이 있는 행은 왕복해도 **그대로 살아남아야** 한다."""
    path = tmp_path / "grid.csv"
    wan395.rows_to_frame([_with_share(0.48)]).to_csv(path, index=False)
    restored = wan395.grid_from_csv(path)[0]
    assert restored.same_step_tp_net_r_share == pytest.approx(0.48)


# --------------------------------------------------------------------------- #
# 10. 「끝점이 최선」과 「아직 오르는 중」을 가르는 마지막 한 걸음
# --------------------------------------------------------------------------- #


def test_branch_a_says_the_curve_already_flattened_when_the_last_step_is_noise() -> None:
    """🚨 실측이 이 자리다 — 0.4R이 0.6R보다 낫지만 **0.5R → 0.4R이 ＋0.0031R**이다.

    그 둘을 안 가르면 「더 내려가면 더 좋아진다」로 읽힌다. 갈래는 ①이되 **공선은 이미
    평평하다**는 문장이 함께 나가야 한다.
    """
    flattening = {0.4: 0.0066, 0.5: 0.0035, 0.6: -0.0064, 0.8: -0.0395, 1.0: -0.0726, 1.5: -0.1194}
    line = wan395.inflection_verdict(_curve(flattening), segment=PRIMARY_OOS)
    assert "갈래 ①" in line
    assert "평평해졌다" in line
    assert "여기서 멈췄다" in line


def test_branch_a_says_still_climbing_when_the_last_step_is_real() -> None:
    """돌연변이 확인 — 마지막 걸음이 잡음선 밖이면 **반대 문장**이 나가야 한다."""
    climbing = {0.4: 0.0400, 0.5: 0.0100, 0.6: -0.0064, 0.8: -0.0395, 1.0: -0.0726, 1.5: -0.1194}
    line = wan395.inflection_verdict(_curve(climbing), segment=PRIMARY_OOS)
    assert "갈래 ①" in line
    assert "아직 오르는 중" in line
    assert "평평해졌다" not in line


# --------------------------------------------------------------------------- #
# 11. 공짜 검산 — `gross_r − cost_r == mean_net_r`
# --------------------------------------------------------------------------- #


def test_identity_closes_on_a_consistent_row() -> None:
    """두 열이 다른 경로에서 온다(분해 vs 북이 실현한 손익) — 닫히면 독립 검산이다."""
    row = _row(0.4, 0.0066).model_copy(update={"gross_r": 0.0867, "cost_r": 0.0801})
    assert "닫힌다" in wan395.identity_line([row], segment=PRIMARY_OOS)


def test_identity_line_catches_a_broken_decomposition() -> None:
    """돌연변이 확인 — 분해를 흔들면 **시끄럽게** 찍혀야 한다(조용히 통과하면 검산이 아니다)."""
    row = _row(0.4, 0.0066).model_copy(update={"gross_r": 0.5000, "cost_r": 0.0801})
    line = wan395.identity_line([row], segment=PRIMARY_OOS)
    assert "안 닫힌다" in line


def test_identity_is_not_claimed_for_the_after_slippage_ruler() -> None:
    """🚨 자를 섞지 말라는 경고가 **문장에 실려 나가야** 한다(WAN-393 §2: R이 셋이다)."""
    row = _row(0.4, 0.0066).model_copy(update={"gross_r": 0.0867, "cost_r": 0.0801})
    assert "다른 자" in wan395.identity_line([row], segment=PRIMARY_OOS)
