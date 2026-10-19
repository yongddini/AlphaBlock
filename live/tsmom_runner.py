"""엔진 C(시계열 모멘텀) 일 리밸런싱 — 페이퍼 러너 안에서 하루 1회 (WAN-451 ⑥).

사용자 결정(2026-10-14, WAN-451 §3 ⑥): 엔진 C는 별도 프로세스가 아니라 **기존 러너 안에서**
돈다. 한 지갑을 쓰므로 프로세스를 하나로 두면 DB 쓰기 경합이 없다. 러너가 매 폴링마다
`TsmomRebalancer.tick(now)`를 부르고, 새로 닫힌 일봉이 있을 때만 실제로 일한다.

## 하루에 하는 일 (자산마다 BTC → ETH 순서)

1. 저장소에서 **확정** 1d 봉을 읽는다(`open_time + 1일 ≤ 지금` — 형성 중인 오늘 봉은 안 본다).
2. 마지막 확정일 D가 원장의 마지막 정산일보다 뒤면 `strategy.tsmom`으로 목표를 내고,
   `paper.tsmom_ledger.plan_rebalance`로 정산 + 증감한다.
3. 원장에 **먼저** 쓰고, 그다음 지갑 자본에 더한다. 그 사이에 죽어도 재시작 때 지갑 자본은
   원장 합계에서 다시 계산되므로 어긋나지 않는다(`PaperExecutor._restore_equity`).
4. 두 자산이 다 끝나면 시가평가 자본을 남기고(⑨) 하루 알림을 보낸다(⑩).

## 러너가 죽어 있던 날 — 거꾸로 거래하지 않는다

오더블록 러너(WAN-306)는 공백을 **따라잡기 재생**한다. 그쪽은 주문이 실제로 걸려 있었으니
그 사이 체결이 진짜로 났을 수 있다. 엔진 C는 다르다 — 리밸런싱은 러너가 **해야만** 일어나는
매매라, 러너가 죽어 있던 날에는 매매가 없었던 것이 사실이다. 그래서 빠진 날을 소급해서
거래하지 않는다. 그 기간은 **보유를 그대로 유지한 채** 다음 리밸런싱에서 한 번에 정산한다
(가격 손익과 그 구간 펀딩 전부). 로그와 알림에 공백 일수를 남긴다.

⚠️ 그 결과 공백이 있으면 그 기간만큼 백테스트(매일 리밸런싱)와 다르다 — 알고 받는 차이이고
장부에서 보인다(`as_of_ms`가 하루 넘게 건너뛴 행).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from common.timefmt import format_kst, format_kst_zoned
from data.funding import FundingRateStore, settled_funding_rates
from execution.models import Position
from paper.tsmom_ledger import EquityMark, RebalanceRow, TsmomLedgerStore, plan_rebalance
from strategy import tsmom

_logger = logging.getLogger(__name__)

DAY_MS = tsmom.DAY_MS
#: 확정 일봉을 이만큼 읽는다 — 가장 긴 룩백 250일 + 변동성 60일 + 여유.
DEFAULT_LOOKBACK_DAYS = 400


class BarSource(Protocol):
    """봉을 읽는 곳(`data.storage.OhlcvStore`가 만족한다)."""

    def load(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame: ...


class TsmomWallet(Protocol):
    """엔진 C가 읽고 쓰는 지갑(`live.executor.PaperExecutor`가 만족한다)."""

    @property
    def equity(self) -> float: ...

    @property
    def open_positions(self) -> list[Position]: ...

    def apply_external_pnl(self, amount: float) -> None: ...


@dataclass
class TsmomRebalancer:
    """하루 1회 엔진 C 리밸런싱을 집행한다(페이퍼 · 실주문 없음)."""

    store: BarSource
    ledger: TsmomLedgerStore
    wallet: TsmomWallet
    funding_store: FundingRateStore | None = None
    notify: Callable[[str], object] | None = None
    """하루 알림을 보낼 함수(텔레그램 `send_message` 등). None이면 로그만."""
    wallet_summary: Callable[[EquityMark], str] | None = None
    """방금 남긴 시가평가 자본으로 「💰 지갑 요약」을 만드는 함수(WAN-457 §1). None이면 안 보낸다.

    리밸런싱 알림 **뒤에** 별도 메시지로 보낸다 — 리밸런싱은 매일 일어나므로 조용한 날에도 온다.
    실패해도 매매 기록·리밸런싱 알림을 막지 않는다."""
    assets: Sequence[str] = tsmom.ASSETS
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    _failed_day: dict[str, int] = field(default_factory=dict)
    """자산 → 마지막으로 실패를 알린 일봉(같은 실패를 매 폴링 반복해 알리지 않는다)."""

    def tick(self, now_ms: int) -> list[RebalanceRow]:
        """새로 닫힌 일봉이 있으면 리밸런싱한다. 할 일이 없으면 빈 목록."""
        done: list[RebalanceRow] = []
        gaps: dict[str, int] = {}
        for asset in self.assets:
            row, gap_days = self._rebalance_asset(asset, now_ms)
            if row is not None:
                done.append(row)
                if gap_days > 1:
                    gaps[asset] = gap_days
        if done:
            mark_ms = max(r.executed_ms for r in done)
            mark = self._equity_mark(mark_ms)
            self.ledger.record_mark(mark)
            message = format_daily_rebalance(done, equity=mark.equity, gaps=gaps)
            _logger.info("엔진 C 리밸런싱(WAN-451):\n%s", message)
            if self.notify is not None:
                try:
                    self.notify(message)
                except Exception:  # noqa: BLE001 — 알림 실패가 매매 기록을 막지 않는다.
                    _logger.exception("엔진 C 리밸런싱 알림 실패")
            self._send_wallet_summary(mark)
        return done

    def _send_wallet_summary(self, mark: EquityMark) -> None:
        """하루 1회 「💰 지갑 요약」(WAN-457 §1) — 조회·전송 실패는 로그만 남긴다."""
        if self.wallet_summary is None:
            return
        try:
            summary = self.wallet_summary(mark)
        except Exception:  # noqa: BLE001 — 요약 조회 실패가 리밸런싱을 막지 않는다.
            _logger.exception("지갑 요약 생성 실패(WAN-457)")
            return
        _logger.info("지갑 요약(WAN-457):\n%s", summary)
        if self.notify is not None:
            try:
                self.notify(summary)
            except Exception:  # noqa: BLE001
                _logger.exception("지갑 요약 알림 실패(WAN-457)")

    # -- 내부 -------------------------------------------------------------

    def _closed_daily(self, asset: str, now_ms: int) -> pd.DataFrame:
        start = now_ms - (self.lookback_days + 2) * DAY_MS
        frame = self.store.load(asset, "1d", start_ms=start, end_ms=now_ms)
        if frame.empty:
            return frame
        if "closed" in frame.columns:
            frame = frame[frame["closed"].astype(bool)]
        frame = frame[frame["open_time"].astype("int64") + DAY_MS <= now_ms]
        return frame.sort_values("open_time").reset_index(drop=True)

    def _rebalance_asset(self, asset: str, now_ms: int) -> tuple[RebalanceRow | None, int]:
        bars = self._closed_daily(asset, now_ms)
        if bars.empty:
            return None, 0
        last_day = int(bars["open_time"].iloc[-1])
        holding = self.ledger.holding(asset)
        if holding is not None and holding.as_of_ms >= last_day:
            return None, 0  # 이미 정산했다 — 할 일 없음(매 폴링 대부분이 여기).
        try:
            target = tsmom.target_from_daily_closes(asset, bars["open_time"], bars["close"])
        except ValueError as exc:
            if self._failed_day.get(asset) != last_day:
                self._failed_day[asset] = last_day
                _logger.error("엔진 C %s 리밸런싱 보류 — %s", asset, exc)
                if self.notify is not None:
                    self.notify(
                        f"⚠️ 엔진 C {asset} 리밸런싱 보류 ({format_kst_zoned(now_ms)})\n{exc}\n"
                        "보유는 그대로 유지하고, 일봉이 채워지면 다음 폴링에 정산한다."
                    )
            return None, 0
        funding = []
        if self.funding_store is not None and holding is not None and holding.qty != 0.0:
            funding = settled_funding_rates(
                self.funding_store,
                asset,
                start_ms=holding.as_of_ms + DAY_MS,
                end_ms=target.effective_from_ms,
            )
        row = plan_rebalance(
            target,
            price=float(bars["close"].iloc[-1]),
            equity_before=self.wallet.equity,
            holding=holding,
            funding_rates=funding,
        )
        self.ledger.append(row)
        self.wallet.apply_external_pnl(row.net_pnl)
        self._failed_day.pop(asset, None)
        gap_days = 0 if holding is None else (target.as_of_ms - holding.as_of_ms) // DAY_MS
        if gap_days > 1:
            _logger.warning(
                "엔진 C %s: %d일 공백 — 소급 거래 없이 보유를 유지한 채 한 번에 정산했다",
                asset,
                gap_days,
            )
        return row, int(gap_days)

    def _equity_mark(self, mark_ms: int) -> EquityMark:
        positions = self.wallet.open_positions
        unrealized = 0.0
        for p in positions:
            last = self._last_price(p.symbol, mark_ms)
            if last is not None:
                unrealized += p.realized_pnl(last)
        return EquityMark(
            mark_ms=mark_ms,
            realized=self.wallet.equity,
            unrealized=unrealized,
            open_positions=len(positions),
        )

    def _last_price(self, symbol: str, at_ms: int) -> float | None:
        frame = self.store.load(symbol, "1m", start_ms=at_ms - 10 * 60_000, end_ms=at_ms)
        if frame.empty:
            return None
        return float(frame.sort_values("open_time")["close"].iloc[-1])


def _side(qty: float) -> str:
    if qty > 0:
        return "롱"
    if qty < 0:
        return "숏"
    return "없음"


def format_daily_rebalance(
    rows: Sequence[RebalanceRow], *, equity: float, gaps: dict[str, int] | None = None
) -> str:
    """하루 알림(⑩) — 목표 배율 · 바뀐 양 · 상승 신호 수 · 롱/숏 전환 강조. 평문."""
    gaps = gaps or {}
    lines = [f"📈 엔진 C 리밸런싱 · {format_kst(rows[0].executed_ms)} KST"]
    for r in rows:
        name = r.asset.split("/")[0]
        before, after = _side(r.prev_qty), _side(r.new_qty)
        flip = before != after and before != "없음" and after != "없음"
        ups = round((r.signal + 1.0) / 2.0 * len(tsmom.LOOKBACKS)) if r.signal == r.signal else 0
        head = f"{'🔁 ' if flip else ''}{name}: {before} → {after}"
        lines.append(
            f"{head} · 배율 {r.weight:+.2f} (상승 신호 {ups}/{len(tsmom.LOOKBACKS)})"
            f" · 목표 명목 {r.target_notional:+,.0f}"
        )
        lines.append(
            f"   거래 {r.traded_notional:,.0f} · 정산 {r.settled_pnl:+,.2f}"
            f" · 펀딩 {0.0 - r.funding:+,.2f} · 비용 {0.0 - r.cost:,.2f}"
        )
        if r.asset in gaps:
            lines.append(f"   ⚠️ {gaps[r.asset]}일 공백 — 보유 유지로 한 번에 정산")
    lines.append(f"지갑 자본 {equity:,.2f}")
    return "\n".join(lines)
