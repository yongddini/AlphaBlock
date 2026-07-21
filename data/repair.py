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

⚠️ **갭 0이 「이상 없음」은 아니다 (WAN-156)**: `find_gaps`는 봉과 봉 **사이**만 보므로
시리즈가 통째로 멈춘 「꼬리 정지」를 구조적으로 못 잡는다. 그래서 이 모듈은 복구와
별개로 `data.freshness`의 신선도 판정을 함께 돌려 `RepairSummary.stale_series`에
싣는다 — **`gaps_found: 0`이어도 정지가 보고서에 뜬다.** 펀딩비 시리즈도 같은 자로
본다(OHLCV 백필이 채우지 않는 별도 경로라 같이 밀린다).

📌 **수집 대상이 아닌 TF는 결함이 아니라 「미추적」이다 (WAN-157)**: 저장소에는 예전에
받아 둔 뒤 `ALPHABLOCK_TIMEFRAMES`에서 빠진 TF(5m)가 남아 있다. 그 시리즈는 **고장이
아니라 설정**이라 고칠 계획이 없는데, 신선도 판정에 넣으면 매 실행이 종료 코드 1 +
텔레그램 경고가 되어 **사람이 경고를 무시하게 된다** — 그게 정확히 WAN-156 사고(5일간
아무도 몰랐다)의 재발 경로다. 그래서 판정 대상을 `list_series()` ∩ 설정 TF로 좁히되,
🚨 **빠진 시리즈를 조용히 지우지 않고** `RepairSummary.untracked_series`로 계속 보인다
(설정과 실제의 어긋남을 감추면 WAN-156과 같은 종류의 침묵을 다시 만든다). 미추적은
`has_defect`에 안 들어가고 텔레그램도 안 탄다 — **보이되 울지 않는다.**

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

from common.telegram import build_telegram_client
from config.settings import Settings, get_settings
from data.backfill import DEFAULT_LIMIT, FetchOHLCV
from data.backfill import backfill_symbol as _backfill_symbol
from data.freshness import (
    DEFAULT_STALE_MULTIPLIER,
    StaleSeries,
    find_stale_funding,
    find_stale_series,
    format_stale,
)
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


class UntrackedSeries(BaseModel):
    """저장돼 있으나 수집 대상(설정 TF)이 아닌 시리즈 하나 (WAN-157).

    결함이 아니라 **설정과 실제의 어긋남**이다. 판정(`has_defect`)에는 안 들어가지만
    보고서·CLI에는 계속 찍혀야 한다 — 감추면 WAN-156과 같은 종류의 침묵이 된다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    last_ms: int | None
    """마지막으로 저장된 봉의 시각(저장된 봉이 없으면 None)."""
    lag_ms: int | None
    """`now_ms - last_ms`. 낡은 정도를 사람이 읽을 수 있게 함께 싣는다."""


class RepairSummary(BaseModel):
    """한 번의 복구 실행 전체 요약(상태 파일로 영속화)."""

    model_config = ConfigDict(frozen=True)

    ran_at_ms: int
    series: list[SeriesRepair] = []
    stale_series: list[StaleSeries] = []
    """꼬리가 멈춘 시리즈(WAN-156). 갭 복구로는 메울 수 없는 결함이라 별도로 싣는다.

    비어 있는 것이 정상이고, 여기에 뭔가 있으면 `gaps_found: 0`이어도 이상이다.
    """
    untracked_series: list[UntrackedSeries] = []
    """저장돼 있으나 수집 대상 TF가 아닌 시리즈(WAN-157).

    ⚠️ **결함이 아니다** — `has_defect`에 안 들어가고 텔레그램도 안 탄다. 다만
    「보이지 않게」 하지는 않는다: 설정에서 뺀 TF가 DB에 남아 낡아 가는 사실은
    사람이 알아야 하고, 그게 이 필드가 존재하는 이유다.
    """

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

    @property
    def has_stale(self) -> bool:
        """꼬리가 멈춘 시리즈가 하나라도 있으면 참."""
        return bool(self.stale_series)

    @property
    def has_defect(self) -> bool:
        """오류 또는 정지 — 「이번 복구 결과가 이상 없음인가」의 정본 판정.

        `has_error`만 보면 WAN-156 사고(전 시리즈 `gaps_found: 0` · 오류 없음 ·
        그런데 5일 정지)를 그대로 다시 놓친다.
        """
        return self.has_error or self.has_stale


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
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
    funding_last_times: Sequence[tuple[str, int | None]] = (),
    tracked_timeframes: Sequence[str] | None = None,
) -> RepairSummary:
    """저장된 모든(또는 지정한) 시리즈의 갭을 탐지·복구하고 꼬리 신선도를 판정한다.

    `series`가 None이면 저장소에 실제로 존재하는 시리즈(`store.list_series()`)를
    대상으로 한다. 이 목록은 네이티브 저장 TF만 포함하므로, 리샘플 파생 TF(2h 등)는
    복구 대상에서 자연히 제외된다(원본 1h를 복구하면 파생도 정확해진다).

    복구가 끝난 **뒤** 각 시리즈의 마지막 봉으로 신선도를 판정해
    `RepairSummary.stale_series`에 담는다(WAN-156). 복구 후에 재는 이유는, 갭 복구가
    꼬리까지 채웠다면 정지가 아니기 때문이다. `funding_last_times`를 주면 펀딩비
    시리즈도 같은 자로 본다 — 비워 두면(기본) OHLCV만 판정한다.

    `tracked_timeframes`(= 설정의 수집 대상 TF)를 주면 그 목록에 없는 TF를 **복구·
    신선도 판정에서 빼고** `RepairSummary.untracked_series`에 따로 싣는다(WAN-157).
    안 주면(기본) 예전처럼 저장된 전부를 판정하므로 기존 호출부는 그대로 동작한다.
    """
    stored = list(series) if series is not None else store.list_series()
    if tracked_timeframes is None:
        targets = stored
        untracked_pairs: list[tuple[str, str]] = []
    else:
        tracked = set(tracked_timeframes)
        targets = [(sym, tf) for sym, tf in stored if tf in tracked]
        untracked_pairs = [(sym, tf) for sym, tf in stored if tf not in tracked]
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
    ran_at = now_ms()
    stale = find_stale_series(
        [(symbol, tf, store.last_open_time(symbol, tf)) for symbol, tf in targets],
        now_ms=ran_at,
        stale_multiplier=stale_multiplier,
    )
    stale += find_stale_funding(
        list(funding_last_times),
        now_ms=ran_at,
        stale_multiplier=stale_multiplier,
    )
    if stale:
        logger.warning(
            "꼬리 정지 시리즈 %d건 — 갭 복구로는 메울 수 없습니다:\n%s",
            len(stale),
            "\n".join(f"  {format_stale(s)}" for s in stale),
        )

    untracked: list[UntrackedSeries] = []
    for symbol, timeframe in untracked_pairs:
        last = store.last_open_time(symbol, timeframe)
        untracked.append(
            UntrackedSeries(
                symbol=symbol,
                timeframe=timeframe,
                last_ms=last,
                lag_ms=None if last is None else ran_at - last,
            )
        )
    if untracked:
        # 결함이 아니므로 warning이 아니라 info다 — 그래도 조용히 사라지지는 않는다.
        logger.info(
            "저장돼 있으나 수집 대상이 아닌 시리즈 %d건(낡습니다): %s",
            len(untracked),
            ", ".join(f"{u.symbol} {u.timeframe}" for u in untracked),
        )
    return RepairSummary(
        ran_at_ms=ran_at,
        series=results,
        stale_series=stale,
        untracked_series=untracked,
    )


# -- 오케스트레이션 (설정 배선 + 텔레그램 경고) --------------------------------


def alert_on_failure(summary: RepairSummary, settings: Settings) -> bool:
    """복구 중 오류가 있었거나 꼬리가 멈춘 시리즈가 있으면 텔레그램으로 경고한다.

    보낼 게 있었고 실제로 전송했으면 True. 이상이 없거나 텔레그램 미설정이면
    False(로그만). 이 경로는 WAN-25/32의 `TelegramClient`를 재사용한다.

    ⚠️ 정지(`stale_series`)도 경고 대상이다 — WAN-156 사고에서 복구는 매번 「오류
    없음」으로 끝났고, 정지를 아는 유일한 장치(`live.health_watch`)는 텔레그램 출구가
    막혀 침묵했다. 복구 경로가 스스로 말하게 해 두 장치가 동시에 조용해지지 않게 한다.

    ⚠️ 반면 미추적 시리즈(`untracked_series`)는 **보내지 않는다**(WAN-157) — 고칠
    계획이 없는 항목을 매번 울리면 진짜 경고까지 무시하게 된다. 그쪽은 보고서·CLI·
    `alphablock status`에서 눈으로 본다.
    """
    failed = [s for s in summary.series if s.error]
    if not failed and not summary.stale_series:
        return False

    lines: list[str] = []
    if failed:
        lines.append("⚠️ *갭 복구 실패*")
        for s in failed:
            lines.append(f"`{s.symbol}` `{s.timeframe}` — {s.error}")
    if summary.stale_series:
        if lines:
            lines.append("")
        lines.append("🚨 *수집 정지(꼬리 신선도)* — 갭 복구로는 메울 수 없습니다")
        for stale in summary.stale_series:
            lines.append(f"`{stale.symbol}` `{stale.timeframe}` — {format_stale(stale)}")
    text = "\n".join(lines)

    telegram = build_telegram_client(settings)
    if telegram is None:
        logger.warning("텔레그램 미설정 — 데이터 이상 경고를 로그로만 남깁니다:\n%s", text)
        return False
    return telegram.send_message(text)


def run_repair(
    settings: Settings | None = None,
    *,
    dry_run: bool = False,
) -> RepairSummary:
    """단발 갭 복구(`alphablock backfill --repair`).

    거래소·저장소를 만들어 저장된 모든 시리즈의 갭을 복구하고, 요약을 상태 파일에
    남긴 뒤 반환한다. 오류나 꼬리 정지가 있으면(그리고 `dry_run`이 아니면) 텔레그램
    경고를 보낸다.

    신선도 판정에는 **설정에 등록된 심볼 전부**의 펀딩비도 포함한다(WAN-156 §5) —
    OHLCV 백필은 펀딩비를 채우지 않으므로 따로 밀릴 수 있다.

    판정 대상 TF는 **설정의 수집 대상**(`settings.timeframes`)으로 좁힌다(WAN-157).
    거기서 빠진 TF는 고장이 아니라 설정이므로 종료 코드·텔레그램을 흔들지 않되,
    `RepairSummary.untracked_series`로 계속 보인다.
    """
    settings = settings or get_settings()

    from data.exchange import create_exchange
    from data.funding import FundingRateStore

    exchange = create_exchange(settings)
    store = OhlcvStore(settings.db_path)
    funding_store = FundingRateStore(settings.db_path)
    try:
        funding_last = [
            (symbol, funding_store.last_funding_time(symbol)) for symbol in settings.symbols
        ]
        summary = repair_all(
            exchange,
            store,
            stale_multiplier=settings.health_stale_multiplier,
            funding_last_times=funding_last,
            tracked_timeframes=settings.timeframes,
        )
    finally:
        funding_store.close()
        store.close()

    RepairStateStore(settings.repair_state_path).save(summary)
    logger.info(
        "갭 복구 완료: %d 시리즈 점검, %d 시리즈에서 %d봉 채움%s%s",
        len(summary.series),
        len(summary.repaired_series),
        summary.total_filled,
        f", {summary.total_remaining}봉 잔여" if summary.total_remaining else "",
        f", 🚨 정지 {len(summary.stale_series)}건" if summary.has_stale else "",
    )
    if summary.untracked_series:
        logger.info(
            "판정 제외(수집 대상 TF 아님) %d 시리즈 — 설정: %s",
            len(summary.untracked_series),
            ", ".join(settings.timeframes),
        )
    if not dry_run:
        alert_on_failure(summary, settings)
    return summary
