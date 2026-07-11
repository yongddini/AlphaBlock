"""바이낸스 USDⓈ-M 선물 **테스트넷** 드라이런 검증 스크립트 (WAN-27).

실계좌 자금 위험 없이, 테스트넷에서 실제 주문으로 실행 경로를 왕복 검증한다:

    진입(시장가) → 포지션 조회 → 손절/익절 부착 → 미체결 주문 취소 → 청산 → 정합 확인

WAN-9 실행 브로커(`CcxtLiveBroker`)를 **테스트넷 엔드포인트로** 배선해 그대로 쓴다.
새 주문 로직을 만들지 않는다. 진입/청산은 브로커의 `place_order`를 재사용하고,
손절/익절(STOP_MARKET / TAKE_PROFIT_MARKET)·포지션·잔고 조회·취소는 거래소 특화
API라 하부 ccxt 인스턴스를 직접 호출한다.

**수동 실행 전용.** 실제 네트워크·테스트넷 키가 필요하며 자동화 테스트에서는 돌리지
않는다(테스트는 sandbox 배선/키 격리만 모킹으로 검증). 안전 가드:

* `ALPHABLOCK_USE_TESTNET=true`  — 아니면 즉시 거부(실계좌 경로 절대 접근 안 함).
* `ALPHABLOCK_LIVE_TRADING=true` — 테스트넷에 실주문을 넣기 위해 필요.
* `ALPHABLOCK_TESTNET_API_KEY` / `ALPHABLOCK_TESTNET_API_SECRET` 설정.

실행 예:

    uv run python -m scripts.testnet_dryrun --symbol BTC/USDT:USDT --qty 0.001
"""

from __future__ import annotations

import argparse
import logging
import sys

from config.settings import Settings, get_settings
from execution.broker import CcxtLiveBroker, build_live_broker
from execution.models import Order, OrderSide, OrderType

_logger = logging.getLogger("alphablock.testnet")


def _guard(settings: Settings) -> None:
    """테스트넷·실주문 전제가 모두 충족됐는지 확인한다. 아니면 SystemExit로 중단."""
    if not settings.use_testnet:
        raise SystemExit(
            "거부: use_testnet=False. 이 스크립트는 테스트넷에서만 동작합니다. "
            "ALPHABLOCK_USE_TESTNET=true 로 설정하세요."
        )
    if not settings.live_trading:
        raise SystemExit(
            "거부: live_trading=False. 테스트넷에 실주문을 넣으려면 "
            "ALPHABLOCK_LIVE_TRADING=true 로 설정하세요."
        )
    if not settings.has_testnet_credentials:
        raise SystemExit(
            "거부: 테스트넷 API 키/시크릿이 없습니다. "
            "ALPHABLOCK_TESTNET_API_KEY / ALPHABLOCK_TESTNET_API_SECRET 를 설정하세요."
        )


def _log_position(broker: CcxtLiveBroker, symbol: str) -> float:
    """현재 오픈 포지션 수량(계약 수)을 로깅하고 반환한다. 없으면 0."""
    positions = broker.exchange.fetch_positions([symbol])
    contracts = 0.0
    for pos in positions:
        amount = pos.get("contracts")
        if amount:
            contracts = float(amount)
            _logger.info(
                "포지션: %s side=%s contracts=%s entry=%s uPnL=%s",
                symbol,
                pos.get("side"),
                contracts,
                pos.get("entryPrice"),
                pos.get("unrealizedPnl"),
            )
    if contracts == 0.0:
        _logger.info("포지션 없음: %s", symbol)
    return contracts


def _place_protective_orders(
    broker: CcxtLiveBroker,
    *,
    symbol: str,
    qty: float,
    entry_price: float,
    stop_pct: float,
    tp_pct: float,
) -> list[str]:
    """진입가 기준 손절(STOP_MARKET)·익절(TAKE_PROFIT_MARKET)을 부착하고 주문 ID를 돌려준다.

    롱 기준. reduceOnly로 포지션 축소 전용. 거래소 특화 파라미터라 ccxt를 직접 호출한다.
    """
    stop_price = round(entry_price * (1.0 - stop_pct), 2)
    take_price = round(entry_price * (1.0 + tp_pct), 2)
    order_ids: list[str] = []
    for label, order_type, trigger in (
        ("손절", "STOP_MARKET", stop_price),
        ("익절", "TAKE_PROFIT_MARKET", take_price),
    ):
        raw = broker.exchange.create_order(
            symbol=symbol,
            type=order_type,
            side="sell",
            amount=qty,
            price=None,
            params={"stopPrice": trigger, "reduceOnly": True},
        )
        order_id = str(raw.get("id"))
        order_ids.append(order_id)
        _logger.info("%s 주문 부착: id=%s trigger=%s", label, order_id, trigger)
    return order_ids


def _cancel_orders(broker: CcxtLiveBroker, symbol: str, order_ids: list[str]) -> None:
    """부착했던 보호 주문을 취소한다(청산 전에 미체결 정리)."""
    for order_id in order_ids:
        try:
            broker.exchange.cancel_order(order_id, symbol)
            _logger.info("주문 취소: id=%s", order_id)
        except Exception as exc:  # noqa: BLE001 — 이미 체결/취소된 경우도 로깅만.
            _logger.warning("주문 취소 실패(무시): id=%s (%s)", order_id, exc)


def run_cycle(broker: CcxtLiveBroker, *, symbol: str, qty: float) -> None:
    """진입→조회→보호주문→취소→청산→정합의 스모크 사이클을 1회 수행한다."""
    _logger.info("=== 테스트넷 드라이런 시작: %s qty=%s ===", symbol, qty)

    # 1) 진입 (시장가 매수) — WAN-9 브로커 재사용
    entry = Order(symbol=symbol, side=OrderSide.BUY, type=OrderType.MARKET, quantity=qty)
    entry_fill = broker.place_order(entry)
    _logger.info(
        "진입 체결: status=%s filled=%s avg=%s fee=%s",
        entry_fill.status.value,
        entry_fill.filled_quantity,
        entry_fill.average_price,
        entry_fill.fee,
    )
    if not entry_fill.is_filled:
        raise SystemExit("진입 주문이 체결되지 않아 사이클을 중단합니다.")

    # 2) 포지션 조회
    _log_position(broker, symbol)

    # 3) 손절/익절 부착 (진입 평균가 기준 ±2%)
    protective_ids = _place_protective_orders(
        broker,
        symbol=symbol,
        qty=entry_fill.filled_quantity,
        entry_price=entry_fill.average_price,
        stop_pct=0.02,
        tp_pct=0.02,
    )

    # 4) 보호 주문 취소 (청산 전 미체결 정리)
    _cancel_orders(broker, symbol, protective_ids)

    # 5) 청산 (reduceOnly 시장가 매도) — WAN-9 브로커 재사용
    close = Order(
        symbol=symbol,
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        quantity=entry_fill.filled_quantity,
        reduce_only=True,
    )
    close_fill = broker.place_order(close)
    _logger.info(
        "청산 체결: status=%s filled=%s avg=%s",
        close_fill.status.value,
        close_fill.filled_quantity,
        close_fill.average_price,
    )

    # 6) 정합 확인: 포지션이 0으로 돌아왔는지 + 잔고 로깅
    remaining = _log_position(broker, symbol)
    balance = broker.exchange.fetch_balance()
    usdt = balance.get("USDT", {})
    _logger.info("잔고(USDT): %s", usdt)
    if remaining != 0.0:
        _logger.warning("경고: 청산 후에도 포지션이 남아 있습니다 (contracts=%s).", remaining)
    else:
        _logger.info("정합 OK: 포지션이 0으로 정리됨.")
    _logger.info("=== 테스트넷 드라이런 완료 ===")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="바이낸스 선물 테스트넷 드라이런 검증(WAN-27).")
    parser.add_argument("--symbol", default="BTC/USDT:USDT", help="검증할 심볼(기본 BTC).")
    parser.add_argument("--qty", type=float, default=0.001, help="진입 수량(테스트넷 소액).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    settings = get_settings()
    _guard(settings)
    broker = build_live_broker(settings)
    run_cycle(broker, symbol=args.symbol, qty=args.qty)
    return 0


if __name__ == "__main__":
    sys.exit(main())
