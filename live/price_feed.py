"""웹소켓 틱 피드 — 대기 지정가 체결을 실시간 체결가로 감지한다 (WAN-246, 페이퍼 한정).

존-지정가 페이퍼 러너(`live.zone_limit_runner`)는 본래 저장소의 **확정 1분봉**을
서브스텝으로 흘려 체결을 판정한다(백테스트 1분봉 서브스텝과 **같은 자** — 파리티).
이 모듈은 그 위에 **옵트인 틱 경로**를 얹는다: ccxt.pro `watch_trades`로 심볼별 실시간
체결가를 받아, 이미 걸려 있는 대기 주문의 `on_price`를 매 틱 호출해 **체결 감지 지연**
(현재 1분봉 확정 + 폴링 간격, 최대 1~2분)을 줄인다.

## 무엇을 바꾸고 무엇을 안 바꾸나

* **예약(arming)·상위TF 존 탐지·만료 계수는 그대로 확정 1분봉 경로가 담당한다** — 틱은
  *이미 걸린* 주문의 체결만 앞당긴다. 그래서 존 대장·지표 시딩은 백테스트와 같은 안정
  경로를 타고, 틱은 순수하게 "닿았는가"를 더 촘촘히 볼 뿐이다.
* **기본값은 꺼짐**(`Settings.live_tick_feed_enabled=False`) — 러너가 이 피드를 안 받으면
  예전과 **비트 단위로 같다**(소켓을 열지 않는다).

## 파리티 경고 (WAN-246 §2/§3 · `docs/decisions/wan246.md`)

틱이라고 "닿으면 체결" 낙관(WAN-96)이 사라지지 않는다 — 큐 우선순위·부분 체결은 호가·
체결 데이터(WAN-98, Canceled) 소관이라 틱 **가격**만으로는 여전히 상한이다. 그리고 틱
경로를 켜면 라이브가 1분봉 백테스트보다 미세해진다: 체결 *여부*는 1분봉이 이미 그 분의
저가·고가를 담고 있어 봉 단위로 같지만, `intrabar_live` 밴드가 봉 안에서 표본을
현재가로 다시 잡으므로 **체결 순간의 지정가**가 1분봉 종가로 잰 값과 미세하게 갈릴 수
있다(경계 근처 저관통 체결에 국한, `live.tick_parity`가 그 갈림을 잰다).

## 안전

페이퍼 한정: **공개 체결 스트림만 구독**하고 실주문·잔고 API는 절대 부르지 않는다.
`ALPHABLOCK_LIVE_TRADING` 기본값 `false` 불변.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolTick:
    """심볼 하나의 실시간 체결가 틱.

    가격은 심볼 단위다(TF와 무관) — 러너가 이 심볼을 감시하는 각 (symbol, timeframe)
    시리즈의 대기 주문에 같은 가격을 반영한다. 단일 체결가라 러너는 low=high=close=price로
    `on_price`에 넣는다(백테스트 서브스텝과 같은 계약).
    """

    symbol: str
    price: float
    time_ms: int


@runtime_checkable
class PriceFeed(Protocol):
    """러너가 소비하는 틱 피드 인터페이스. `drain`은 논블로킹(쌓인 틱을 한 번에 비운다)."""

    def drain(self) -> list[SymbolTick]:
        """마지막 호출 이후 쌓인 틱을 모두 반환하고 버퍼를 비운다(러너 스레드에서 호출)."""
        ...

    def close(self) -> None:
        """백그라운드 구독을 정지하고 자원을 정리한다."""
        ...


class NullPriceFeed:
    """틱을 내지 않는 피드(기본값). 러너가 이걸 받으면 예전과 비트 단위로 같게 돈다."""

    def drain(self) -> list[SymbolTick]:
        return []

    def close(self) -> None:
        return None


class CcxtProTickFeed:
    """ccxt.pro `watch_trades`로 심볼별 실시간 체결가를 큐에 쌓는 피드 (WAN-246).

    백그라운드 스레드가 asyncio 이벤트 루프를 돌리고, 심볼마다 `watch_trades`를 반복
    구독해 체결이 올 때마다 `SymbolTick`을 스레드 안전 큐에 넣는다. 러너 스레드는
    `drain()`으로 쌓인 틱을 논블로킹으로 비운다. `close()`가 루프를 정지하고 거래소
    연결을 닫는다.

    거래소 객체는 주입할 수 있다(`exchange=`) — 네트워크 없이 테스트에서 가짜 구현으로
    `_consume` 루프·큐·drain을 그대로 검증하기 위한 seam이다. 주입하지 않으면
    `ccxt.pro.<exchange_id>`를 지연 생성한다(공개 시세만 쓰므로 자격 증명 불필요).
    """

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        exchange_id: str = "binanceusdm",
        exchange: Any | None = None,
        max_buffer: int = 100_000,
    ) -> None:
        self._symbols = list(dict.fromkeys(symbols))  # 중복 제거, 순서 유지.
        self._exchange_id = exchange_id
        self._exchange = exchange
        self._queue: queue.Queue[SymbolTick] = queue.Queue(maxsize=max_buffer)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_task: asyncio.Task[None] | None = None

    # -- 러너 스레드 API -----------------------------------------------------

    def start(self) -> None:
        """백그라운드 구독 스레드를 시작한다(이미 돌고 있으면 무시)."""
        if self._thread is not None:
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run_loop, name="ccxtpro-tick-feed", daemon=True)
        self._thread = thread
        thread.start()

    def drain(self) -> list[SymbolTick]:
        ticks: list[SymbolTick] = []
        while True:
            try:
                ticks.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return ticks

    def close(self) -> None:
        self._stop.set()
        loop = self._loop
        task = self._main_task
        if loop is not None and task is not None:
            # watch_trades는 데이터가 올 때까지 블록하므로 태스크를 취소해 대기를 깬다
            # (실 ccxt.pro는 close()가 대기 중인 watch 퓨처를 거부한다 — 취소가 같은 효과).
            loop.call_soon_threadsafe(task.cancel)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10.0)
        self._thread = None

    # -- 백그라운드 스레드 ---------------------------------------------------

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            main_task = loop.create_task(self._main())
            self._main_task = main_task
            loop.run_until_complete(main_task)
        except asyncio.CancelledError:
            pass  # close()가 취소한 정상 종료.
        except Exception:  # noqa: BLE001 — 스레드 최상단: 조용히 죽지 않도록 로그로 드러낸다.
            _logger.exception("틱 피드 루프 종료(예외)")
        finally:
            self._main_task = None
            loop.close()
            self._loop = None

    async def _main(self) -> None:
        exchange = self._exchange
        if exchange is None:
            import ccxt.pro as ccxtpro  # 지연 임포트: 피드를 켤 때만 필요하다.

            exchange = getattr(ccxtpro, self._exchange_id)({"enableRateLimit": True})
            self._exchange = exchange
        try:
            await asyncio.gather(*(self._consume(exchange, s) for s in self._symbols))
        finally:
            await self._safe_close(exchange)

    async def _consume(self, exchange: Any, symbol: str) -> None:
        """한 심볼의 체결 스트림을 반복 구독해 틱을 큐에 넣는다(정지 신호까지)."""
        while not self._stop.is_set():
            try:
                trades = await exchange.watch_trades(symbol)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 일시적 소켓 오류는 재시도(수집기와 같은 관행).
                if self._stop.is_set():
                    return
                _logger.warning("watch_trades 실패, 재시도: %s", symbol)
                await asyncio.sleep(1.0)
                continue
            for trade in trades or []:
                self._push_trade(symbol, trade)

    def _push_trade(self, symbol: str, trade: dict[str, Any]) -> None:
        price = trade.get("price")
        ts = trade.get("timestamp")
        if price is None or ts is None:
            return  # 가격·시각이 없는 체결은 버린다(지어내지 않는다).
        self._push(SymbolTick(symbol=symbol, price=float(price), time_ms=int(ts)))

    def _push(self, tick: SymbolTick) -> None:
        try:
            self._queue.put_nowait(tick)
        except queue.Full:
            # 러너가 못 따라올 만큼 틱이 몰리면 가장 오래된 것을 버리고 최신을 넣는다
            # (체결 감지엔 최신 가격이 중요하다).
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(tick)
            except (queue.Empty, queue.Full):
                pass

    @staticmethod
    async def _safe_close(exchange: Any) -> None:
        close = getattr(exchange, "close", None)
        if close is None:
            return
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — 종료 정리 실패는 로그만.
            _logger.exception("틱 피드 거래소 종료 실패")
