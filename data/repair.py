"""OHLCV 갭 자동 복구 백필 (WAN-35).

`data.gaps.find_gaps`로 시리즈별 누락 구간을 찾고, **그 구간만** WAN-6 수집기
로직(`data.backfill.backfill_symbol`)으로 재수집해 UPSERT한다. 저장소 기본키가
`(symbol, timeframe, open_time)`이라 기존 봉 덮어쓰기는 무해하다.

핵심 성질
--------
* **갭이 없으면 API를 호출하지 않는다.** `find_gaps`가 빈 결과면 곧바로 반환한다.
* **탐지된 구간만** 재수집한다(전체 재백필이 아니라 구멍만).
* 시리즈 단위로 예외를 격리한다 — 한 시리즈 실패가 나머지 복구·수집 데몬을 죽이지
  않도록 오류를 `SeriesRepair.error`에 담아 계속 진행한다. 상위(오케스트레이션)는
  오류가 있으면 WAN-32 텔레그램 경고 경로로 알린다.

복구 결과(`RepairSummary`)는 JSON 상태 파일로 남겨 Health 뷰(WAN-30)와 CLI
`status`가 "마지막 복구에서 시리즈별 몇 봉을 채웠는지"를 보여줄 수 있게 한다.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from config.settings import Settings, get_settings
from data.backfill import DEFAULT_LIMIT, FetchOHLCV
from data.backfill import backfill_symbol as _backfill_symbol
from data.gaps import find_gaps, total_missing
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

logger = logging.getLogger(__name__)


# -- 결과 모델 -----------------------------------------------------------------


class SeriesRepair(BaseModel):
    """한 시리즈(심볼·TF)의 복구 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    gaps_found: int
    """탐지된 갭 구간 수."""
    bars_missing: int
    """복구 전 누락 봉 총수."""
    bars_filled: int
    """이번 복구로 새로 채운(UPSERT한) 봉 수."""
    bars_remaining: int
    """복구 후에도 여전히 비어 있는 봉 수(거래소에도 없는 구간 등)."""
    error: str | None = None
    """복구 중 발생한 오류 요약(없으면 None)."""


class RepairSummary(BaseModel):
    """한 번의 복구 실행 전체 요약(상태 파일로 영속화)."""

    model_config = ConfigDict(frozen=True)

    ran_at_ms: int
    series: list[SeriesRepair] = []

    @property
    def total_filled(self) -> int:
        return sum(s.bars_filled for s in self.series)

    @property
    def total_remaining(self) -> int:
        return sum(s.bars_remaining for s in self.series)

    @property
    def repaired_series(self) -> list[SeriesRepair]:
        """실제로 갭이 있었던(또는 오류가 난) 시리즈만."""
        return [s for s in self.series if s.gaps_found or s.error]

    @property
    def has_error(self) -> bool:
        return any(s.error for s in self.series)


# -- 상태 영속화 ---------------------------------------------------------------


class RepairStateStore:
    """마지막 복구 요약을 JSON 파일로 영속화한다(원자적 교체)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> RepairSummary | None:
        """파일에서 마지막 복구 요약을 읽는다(없거나 손상 시 None)."""
        if not self._path.exists():
            return None
        try:
            return RepairSummary.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            logger.warning("복구 상태 파일을 읽지 못함: %s", exc)
            return None

    def save(self, summary: RepairSummary) -> None:
        """요약을 파일에 원자적으로 저장한다."""
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


# -- 복구 로직 -----------------------------------------------------------------


def _stored_timestamps(store: OhlcvStore, symbol: str, timeframe: str) -> list[int]:
    """저장된 시리즈의 봉 open_time 목록(오름차순)."""
    df = store.load(symbol, timeframe)
    if df.empty:
        return []
    return [int(t) for t in df["open_time"].tolist()]


def repair_series(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    symbol: str,
    timeframe: str,
    *,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> SeriesRepair:
    """한 시리즈의 내부 갭을 탐지·복구한다.

    갭이 없으면 거래소를 호출하지 않고 즉시 반환한다. 갭이 있으면 각 구간만
    `backfill_symbol`로 재수집한다. 오류는 격리해 `SeriesRepair.error`에 담는다.
    """
    tf_ms = timeframe_to_ms(timeframe)
    gaps = find_gaps(_stored_timestamps(store, symbol, timeframe), timeframe)
    if not gaps:
        return SeriesRepair(
            symbol=symbol,
            timeframe=timeframe,
            gaps_found=0,
            bars_missing=0,
            bars_filled=0,
            bars_remaining=0,
        )

    bars_missing = total_missing(gaps)
    filled = 0
    error: str | None = None
    try:
        for gap in gaps:
            # end_ms는 마지막 누락 봉(포함)이므로 배타적 상한은 한 봉 더 넓힌다.
            filled += _backfill_symbol(
                exchange,
                store,
                symbol,
                timeframe,
                gap.start_ms,
                until_ms=gap.end_ms + tf_ms,
                limit=limit,
                max_retries=max_retries,
                backoff_base=backoff_base,
                sleeper=sleeper,
                now_ms=now_ms,
            )
    except Exception as exc:  # noqa: BLE001 — 시리즈 단위 격리(수집 데몬 보호).
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("갭 복구 실패 %s %s", symbol, timeframe)

    # 복구 후 남은 갭 재계산(거래소에도 없는 구간은 그대로 남을 수 있음).
    remaining = total_missing(find_gaps(_stored_timestamps(store, symbol, timeframe), timeframe))
    result = SeriesRepair(
        symbol=symbol,
        timeframe=timeframe,
        gaps_found=len(gaps),
        bars_missing=bars_missing,
        bars_filled=filled,
        bars_remaining=remaining,
        error=error,
    )
    logger.info(
        "갭 복구 %s %s: 갭 %d개(%d봉) → %d봉 채움, %d봉 잔여%s",
        symbol,
        timeframe,
        result.gaps_found,
        result.bars_missing,
        result.bars_filled,
        result.bars_remaining,
        f" (오류: {error})" if error else "",
    )
    return result


def repair_all(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    *,
    series: Sequence[tuple[str, str]] | None = None,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> RepairSummary:
    """저장된 모든(또는 지정한) 시리즈의 갭을 탐지·복구한다.

    `series`가 None이면 저장소에 실제로 존재하는 시리즈(`store.list_series()`)를
    대상으로 한다. 이 목록은 네이티브 저장 TF만 포함하므로, 리샘플 파생 TF(2h 등)는
    복구 대상에서 자연히 제외된다(원본 1h를 복구하면 파생도 정확해진다).
    """
    targets = list(series) if series is not None else store.list_series()
    results = [
        repair_series(
            exchange,
            store,
            symbol,
            timeframe,
            limit=limit,
            max_retries=max_retries,
            backoff_base=backoff_base,
            sleeper=sleeper,
            now_ms=now_ms,
        )
        for symbol, timeframe in targets
    ]
    return RepairSummary(ran_at_ms=now_ms(), series=results)


# -- 오케스트레이션 (설정 배선 + 텔레그램 경고) --------------------------------


def alert_on_failure(summary: RepairSummary, settings: Settings) -> bool:
    """복구 중 오류가 있었으면 WAN-32 텔레그램 경로로 경고한다.

    보낼 게 있었고 실제로 전송했으면 True. 오류가 없거나 텔레그램 미설정이면
    False(로그만). 이 경로는 WAN-25/32의 `TelegramClient`를 재사용한다.
    """
    failed = [s for s in summary.series if s.error]
    if not failed:
        return False

    lines = ["⚠️ *갭 복구 실패*"]
    for s in failed:
        lines.append(f"`{s.symbol}` `{s.timeframe}` — {s.error}")
    text = "\n".join(lines)

    from live.runner import build_telegram_client

    telegram = build_telegram_client(settings)
    if telegram is None:
        logger.warning("텔레그램 미설정 — 갭 복구 실패 경고를 로그로만 남깁니다:\n%s", text)
        return False
    return telegram.send_message(text)


def run_repair(
    settings: Settings | None = None,
    *,
    dry_run: bool = False,
) -> RepairSummary:
    """단발 갭 복구(`alphablock backfill --repair`).

    거래소·저장소를 만들어 저장된 모든 시리즈의 갭을 복구하고, 요약을 상태 파일에
    남긴 뒤 반환한다. 오류가 있으면(그리고 `dry_run`이 아니면) 텔레그램 경고를
    보낸다.
    """
    settings = settings or get_settings()

    from data.exchange import create_exchange

    exchange = create_exchange(settings)
    store = OhlcvStore(settings.db_path)
    try:
        summary = repair_all(exchange, store)
    finally:
        store.close()

    RepairStateStore(settings.repair_state_path).save(summary)
    logger.info(
        "갭 복구 완료: %d 시리즈 점검, %d 시리즈에서 %d봉 채움%s",
        len(summary.series),
        len(summary.repaired_series),
        summary.total_filled,
        f", {summary.total_remaining}봉 잔여" if summary.total_remaining else "",
    )
    if not dry_run:
        alert_on_failure(summary, settings)
    return summary
