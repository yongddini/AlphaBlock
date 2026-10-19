"""WAN-459 §1: 「마지막 정산 이후」를 **확정**과 **평가 포함** 두 숫자로 — 숫자 일치를 동작으로.

낮에는 추세추종이 정산되지 않아 확정 잔고 변화가 거의 늘 0이다(2026-10-19 11:24 실사용 화면).
그래서 「/잔고」와 하루 요약이 같은 함수로 두 숫자를 나란히 낸다:

* 확정 = 실현 잔고 변화(팔 실현 + 추세추종 정산 + 잔차)
* 평가 포함 = 확정 + 팔 평가손익 변화 + 추세추종 미정산 평가손익

두 값을 **장부와 독립된 경로**(스냅샷의 실현 잔고 · 「지금 평가 총액(참고)」)와 대조한다 — 내역의
덧셈만 확인하면 같은 식을 두 번 쓴 것뿐이다.

§2: 사람이 읽는 출력에 「엔진 C」가 남지 않는다(식별자·로그·docstring은 그대로).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paper.wallet_summary import (
    _signed,
    build_balance_reply,
    format_daily_wallet_summary,
    read_latest_mark,
    read_wallet_snapshot,
    wallet_change,
)
from strategy import tsmom
from strategy.models import SignalExitReason
from tests import test_wan457_wallet_summary as ws


def _midday(rig: ws._Rig, *, ts_factor: float, doge: float, arm_trade: bool) -> int:
    """마지막 정산 5시간 뒤 — 추세추종 가격 이동 · DOGE 평가가 · (선택) 열린 DOGE 청산."""
    mark = rig.ledger.marks()[-1]
    now = mark.mark_ms + 5 * 3_600_000
    if arm_trade:
        t = mark.mark_ms + 3_600_000
        rig.wallet.exit(
            ws.DOGE,
            "1h",
            exit_price=0.24,
            exit_time=t,
            reason=SignalExitReason.TAKE_PROFIT,
            now_ms=t,
        )
    for asset in tsmom.ASSETS:
        holding = rig.ledger.holding(asset)
        assert holding is not None
        ws._minute(rig.store, asset, now - 60_000, holding.price * ts_factor)
    ws._minute(rig.store, ws.DOGE, now - 60_000, doge)
    return now


@pytest.mark.parametrize("arm_trade", [False, True])
def test_balance_reply_confirmed_and_marked_match_independent_paths(
    tmp_path: Path, arm_trade: bool
) -> None:
    rig = ws._Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    now = _midday(rig, ts_factor=1.02, doge=0.25, arm_trade=arm_trade)
    mark = read_latest_mark(rig.db, now)
    assert mark is not None

    snap = read_wallet_snapshot(rig.db, initial_equity=ws.INITIAL, at_ms=now, live_engine_c=True)
    unsettled = snap.engine_c_unsettled
    assert unsettled is not None and unsettled != 0.0
    change = wallet_change(
        rig.db,
        current=snap.as_mark(),
        previous=mark,
        initial_equity=ws.INITIAL,
        engine_c_unsettled=unsettled,
    )

    # 확정 == 실현 잔고 변화(스냅샷 경로). 팔 거래가 없으면 0 — 사용자가 본 그 화면.
    assert change.confirmed == pytest.approx(snap.realized - mark.realized, abs=1e-9)
    if arm_trade:
        record = max(rig.wallet._store.list_records(), key=lambda r: r.exit_time)  # noqa: SLF001
        assert record.realized_pnl is not None
        assert change.confirmed == pytest.approx(record.realized_pnl, abs=1e-9)
        assert change.confirmed != 0.0
    else:
        assert change.confirmed == pytest.approx(0.0, abs=1e-9)
    # 평가 포함 == 「지금 평가 총액(참고)」 − 마지막 정산 총자산(독립 경로).
    marked = change.with_marks
    assert marked is not None
    assert marked == pytest.approx((snap.equity + unsettled) - mark.equity, abs=1e-9)
    # 그리고 이슈가 요구한 등식 — 평가 포함 = 확정 + 추세추종 미정산 + 팔 평가손익 변화.
    assert marked == change.confirmed + change.arm_unrealized_change + unsettled
    assert change.arm_unrealized_change != 0.0  # DOGE 0.21 → 0.25(또는 청산으로 0)
    assert change.total == pytest.approx(change.confirmed + change.arm_unrealized_change)

    reply = build_balance_reply(rig.db, initial_equity=ws.INITIAL, now_ms=now)
    head = next(line for line in reply.splitlines() if line.startswith("마지막 정산("))
    assert "이후: 확정 " + _signed(change.confirmed) in head
    assert "· 평가 포함 " + _signed(marked) in head
    assert f"  · 추세추종 미정산 평가손익 {_signed(unsettled)} (평가 포함에만)" in reply
    assert f"  · 팔 평가손익 변화 {_signed(change.arm_unrealized_change)} (평가 포함에만)" in reply
    assert "장부로 설명 안 되는 몫" not in reply
    assert "엔진 C" not in reply


def test_balance_reply_without_trend_price_says_so_instead_of_guessing(tmp_path: Path) -> None:
    rig = ws._Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    mark = rig.ledger.marks()[-1]
    now = mark.mark_ms + 5 * 3_600_000  # 그 시각 1분봉이 없다 → 추세추종 가격 없음.
    reply = build_balance_reply(rig.db, initial_equity=ws.INITIAL, now_ms=now)
    head = next(line for line in reply.splitlines() if line.startswith("마지막 정산("))
    assert "확정 +$0.00" in head and "평가 포함 가격 없음" in head
    assert "추세추종 미정산 평가손익 가격 없음" in reply
    assert "엔진 C" not in reply


def test_daily_summary_uses_the_same_shape_and_marked_equals_ledger_change(
    tmp_path: Path,
) -> None:
    rig = ws._Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    prev, mark = rig.ledger.marks()
    change = wallet_change(rig.db, current=mark, previous=prev, initial_equity=ws.INITIAL)
    # 방금 정산했다 → 추세추종 미정산 0 → 평가 포함 == 장부 총자산 변화.
    assert change.engine_c_unsettled == 0.0
    assert change.with_marks == pytest.approx(mark.equity - prev.equity, abs=1e-9)
    assert change.confirmed == pytest.approx(mark.realized - prev.realized, abs=1e-9)
    summary = rig.sent[-1]
    assert summary.startswith("💰 지갑 요약")
    head = next(line for line in summary.splitlines() if " 대비: 확정 " in line)
    assert "· 평가 포함 " + _signed(mark.equity - prev.equity) in head
    assert "  · 추세추종 미정산 평가손익 +$0.00 (평가 포함에만)" in summary
    snap = read_wallet_snapshot(rig.db, initial_equity=ws.INITIAL, at_ms=mark.mark_ms)
    assert format_daily_wallet_summary(snap, mark=mark, change=change) == summary


def test_user_facing_runner_messages_say_trend_following_not_engine_c(tmp_path: Path) -> None:
    rig = ws._Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    assert rig.sent and all("엔진 C" not in text for text in rig.sent)
    assert any(text.startswith("📈 추세추종 리밸런싱") for text in rig.sent)
    assert any("추세추종 보유" in text for text in rig.sent)
