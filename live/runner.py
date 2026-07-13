"""실시간(폴링) 시그널 러너 (WAN-25).

저장소(WAN-6 수집분)를 주기적으로 폴링해 각 시리즈(심볼·TF)에 WAN-23 컨플루언스
전략을 재평가하고, **새로** 나타난 확정 진입/계획 청산만 텔레그램으로 알린다.

## 중복 방지 (재시작 안전)

전략은 매 폴링마다 최근 봉 전체를 재평가하므로 과거 신호가 반복 등장한다. 시리즈별
**워터마크**(마지막으로 처리한 신호 `open_time`)를 JSON 파일(`WatermarkStore`)에
저장해, `time > watermark`인 신호만 새 신호로 보고 처리한다. 러너를 재시작해도
워터마크가 남아 있어 같은 신호를 다시 보내지 않는다.

## 프라이밍 (첫 실행 폭주 방지)

한 시리즈를 처음 볼 때(워터마크 없음)는 현재까지의 과거 신호를 모두 "처리됨"으로
간주해 조용히 워터마크만 최신 봉으로 올리고 아무것도 보내지 않는다. 이후 새로 닫힌
봉에서 발생하는 신호부터 알림을 보낸다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from common.telegram import build_telegram_client
from config.settings import Settings, get_settings
from data.funding import FundingRateStore
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live.executor import PaperExecutor
from live.notifier import Notifier, SignalEvent, collect_events
from live.runtime_state import RuntimeStateStore
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.confluence import ConfluenceStrategy

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
Series = tuple[str, str]


def _series_key(symbol: str, timeframe: str) -> str:
    return f"{symbol}|{timeframe}"


class WatermarkStore:
    """시리즈별 워터마크(마지막 처리 신호 `open_time`)를 JSON 파일로 영속화한다.

    파일이 없으면 빈 상태로 시작한다. 저장은 임시 파일에 쓴 뒤 원자적으로
    바꿔치기해 중간 크래시로 파일이 손상되지 않게 한다.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._marks: dict[str, int] = self._read()

    def _read(self) -> dict[str, int]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _logger.warning("워터마크 파일을 읽지 못해 빈 상태로 시작: %s", exc)
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): int(v) for k, v in raw.items() if isinstance(v, int | float)}

    def get(self, symbol: str, timeframe: str) -> int | None:
        """해당 시리즈의 워터마크. 아직 없으면(=미프라이밍) None."""
        return self._marks.get(_series_key(symbol, timeframe))

    def set(self, symbol: str, timeframe: str, value: int) -> None:
        """워터마크를 갱신하고 파일에 즉시 저장한다."""
        self._marks[_series_key(symbol, timeframe)] = int(value)
        self._flush()

    def _flush(self) -> None:
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._marks, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


class SignalRunner:
    """저장소를 폴링해 새 신호를 텔레그램으로 알리는 러너."""

    def __init__(
        self,
        *,
        store: OhlcvStore,
        strategy: ConfluenceStrategy,
        notifier: Notifier,
        state: WatermarkStore,
        series: list[Series],
        lookback_bars: int,
        poll_interval_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
        runtime_state: RuntimeStateStore | None = None,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._store = store
        self._strategy = strategy
        self._notifier = notifier
        self._state = state
        self._series = series
        self._lookback_bars = lookback_bars
        self._poll_interval = poll_interval_seconds
        self._sleep = sleep
        self._runtime_state = runtime_state
        self._now_ms = now_ms

    def poll_series(self, symbol: str, timeframe: str) -> list[SignalEvent]:
        """한 시리즈를 한 번 평가하고 새로 발생한 신호를 처리·반환한다.

        프라이밍(첫 관측) 시에는 아무것도 보내지 않고 워터마크만 최신으로 올린다.
        """
        df = self._store.load(symbol, timeframe)
        if df.empty:
            return []
        recent = df.tail(self._lookback_bars)
        closed = recent[recent["closed"].astype(bool)]
        if closed.empty:
            return []
        last_closed_time = int(closed["open_time"].iloc[-1])

        result = self._strategy.run(recent)
        events = collect_events(result, symbol, timeframe)

        watermark = self._state.get(symbol, timeframe)
        if watermark is None:
            # 프라이밍: 현재까지의 신호를 모두 처리됨으로 간주(발송 없음).
            self._state.set(symbol, timeframe, last_closed_time)
            _logger.info(
                "프라이밍 완료: %s %s (워터마크=%d, 과거 신호 %d건 무시)",
                symbol,
                timeframe,
                last_closed_time,
                len(events),
            )
            return []

        new_events = [e for e in events if e.time > watermark]
        if not new_events:
            return []
        for event in new_events:
            self._notifier.handle(event, now_ms=self._now_ms())
        highest = max(e.time for e in new_events)
        self._state.set(symbol, timeframe, max(watermark, highest))
        _logger.info("%s %s: 새 신호 %d건 처리", symbol, timeframe, len(new_events))
        return new_events

    def poll_once(self) -> list[SignalEvent]:
        """모든 시리즈를 한 번씩 폴링한다."""
        emitted: list[SignalEvent] = []
        for symbol, timeframe in self._series:
            try:
                emitted += self.poll_series(symbol, timeframe)
            except Exception:  # noqa: BLE001 — 한 시리즈 오류가 루프 전체를 멈추지 않도록.
                _logger.exception("시리즈 폴링 실패: %s %s", symbol, timeframe)
        if self._runtime_state is not None:
            # 하트비트·현재 포지션·새 신호를 남겨 Health 대시보드(WAN-30)가 읽게 한다.
            self._runtime_state.record(
                now_ms=self._now_ms(),
                open_positions=self._notifier.open_positions,
                new_events=emitted,
            )
        return emitted

    def run(self, *, max_polls: int | None = None) -> None:
        """폴링 루프. `max_polls`가 None이면 무한 반복(테스트에서는 유한 지정)."""
        poll = 0
        while max_polls is None or poll < max_polls:
            self.poll_once()
            poll += 1
            if max_polls is not None and poll >= max_polls:
                break
            self._sleep(self._poll_interval)


def build_series(settings: Settings) -> list[Series]:
    """설정의 심볼×타임프레임 조합을 시리즈 목록으로 만든다."""
    return [
        (symbol, timeframe)
        for symbol in settings.live_signal_symbols
        for timeframe in settings.live_signal_timeframes
    ]


def run_signal_runner(
    settings: Settings | None = None,
    *,
    once: bool = False,
    dry_run: bool = False,
    test_message: bool = False,
) -> None:
    """시그널 러너를 실행한다(`live` CLI/`python -m live.runner` 공용 진입).

    - `test_message=True`: 텔레그램 연결 확인용 메시지 1건만 보내고 종료.
    - `dry_run=True`: 텔레그램 전송 없이 로그로만 출력.
    - `once=True`: 한 번만 폴링하고 종료(그 외에는 무한 폴링 루프).
    """
    settings = settings or get_settings()

    telegram = None if dry_run else build_telegram_client(settings)
    if test_message:
        if telegram is None:
            _logger.error("텔레그램이 설정되지 않았습니다(ALPHABLOCK_TELEGRAM_*).")
            return
        ok = telegram.send_message("✅ AlphaBlock 시그널 러너 테스트 메시지 (WAN-25)")
        _logger.info("테스트 메시지 전송 %s", "성공" if ok else "실패")
        return

    if telegram is None and not dry_run:
        _logger.warning(
            "텔레그램 미설정 — 드라이런으로 실행합니다. ALPHABLOCK_TELEGRAM_* 를 설정하세요."
        )

    store = OhlcvStore(settings.db_path)
    series = build_series(settings)
    # 페이퍼 집행 배선(WAN-34): 시그널 → 사이징/리스크 → 페이퍼 주문 → 포지션 추적.
    # 열린 포지션은 재시작 복구용으로 저장하고(open_positions), 청산 라운드트립은
    # WAN-33 성과 스키마(paper_trades, 같은 DB)에 recorder로 위임 기록해 성과·패리티
    # 리포트가 즉시 집계한다. live_trading이 꺼져 있으면 build_execution_engine이
    # PaperBroker를 써 실주문 API를 부르지 않는다. 기록 실패는 recorder가 삼킨다.
    paper_store = PaperTradeStore(settings.db_path)
    funding_store = FundingRateStore(settings.db_path) if settings.funding_enabled else None
    recorder = PaperTradeRecorder(
        paper_store,
        cost_model=settings.costs,
        funding_store=funding_store,
    )
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper_store,
        recorder=recorder,
        sizing=settings.risk_sizing,
    )
    try:
        runner = SignalRunner(
            store=store,
            strategy=ConfluenceStrategy(settings.confluence),
            notifier=Notifier(telegram, executor=executor),
            state=WatermarkStore(settings.live_signal_state_path),
            series=series,
            lookback_bars=settings.live_signal_lookback_bars,
            poll_interval_seconds=settings.live_poll_interval_seconds,
            runtime_state=RuntimeStateStore(settings.live_runtime_state_path),
        )
        _logger.info(
            "시그널 러너 시작: %d 시리즈, 폴링 %ds (live_trading=%s, 페이퍼 모드)",
            len(series),
            settings.live_poll_interval_seconds,
            settings.live_trading,
        )
        runner.run(max_polls=1 if once else None)
    finally:
        store.close()
        paper_store.close()
        if funding_store is not None:
            funding_store.close()


def main() -> None:
    """CLI 엔트리포인트: `python -m live.runner`."""
    parser = argparse.ArgumentParser(description="WAN-25 실시간 시그널 러너 (페이퍼)")
    parser.add_argument("--once", action="store_true", help="한 번만 폴링하고 종료")
    parser.add_argument(
        "--test-message",
        action="store_true",
        help="테스트 메시지를 한 번 보내고 종료(텔레그램 연결 확인)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램 전송 없이 메시지를 로그로만 출력",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run_signal_runner(
        once=args.once,
        dry_run=args.dry_run,
        test_message=args.test_message,
    )


if __name__ == "__main__":
    main()
