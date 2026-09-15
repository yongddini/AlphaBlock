"""바이낸스 USDⓈ-M 선물 일별 **metrics** 아카이브(미결제약정 등 5분 스냅샷) 접근 (WAN-417).

## 무엇을 받나

`data.binance.vision/data/futures/um/daily/metrics/<SYM>/<SYM>-metrics-<YYYY-MM-DD>.zip`.
안의 CSV 한 장 · 열은 `create_time · symbol · sum_open_interest(코인 수량) ·
sum_open_interest_value(USDT 명목) · count_toptrader_long_short_ratio ·
sum_toptrader_long_short_ratio · count_long_short_ratio · sum_taker_long_short_vol_ratio`.
이 모듈은 **미결제약정 두 열만** 낸다(WAN-417 §G-0이 묻는 것이 그것이고, 롱숏·테이커 비율은
구간 집계라 시각 규약이 다르다 — 섞어 읽지 않는다).

## 🚨 형식 함정 — 실측으로 못 박은 것 (2026-09-15 · BTC 2020-10-01 파일)

* **간격은 5분**이다 — 첫 두 서로 다른 `create_time`의 차가 **300초**(`00:00:00 → 00:05:00`).
* **그런데 옛 파일은 행이 전부 두 번씩 적혀 있다** — 그래서 「577행」이 나왔다(288 스냅샷 × 2
  + 헤더 1 = 577). 「5분이면 288행」과 「577행」이 **둘 다 맞았다**. 최근 파일(2025-10-11)은
  중복이 없다(289행). 이 모듈은 `create_time`으로 **중복을 접되 값이 다르면 셈한다**(조용히
  하나를 고르지 않는다 — 몇 건인지 인구조사에 실린다).
* 🚨 **`create_time`의 뜻이 2024-03-04에 바뀐다** — 그 전에는 **값을 잰 시각**이고, 그날부터는
  **5분 구간의 시작 라벨이고 값은 구간 끝(라벨 + 5분)에 잰다.** 파일의 함의 가격(명목 ÷ 수량)을
  저장 1분봉 시가와 시차별로 맞추면 2024-03-03까지는 시차 **0분**, 2024-03-04부터는 **+5분**에서만
  맞는다(12종목 전부 같은 날 · 오차 0.02~0.6bp — WAN-417 `--part audit`). 같은 날 소수 자릿수도
  8 → 16으로 바뀐다. 이 모듈은 **값을 잰 시각**(`times_ms`)으로 되돌려 낸다
  (`MEASURED_SHIFT_FROM_DAY`). 🚨 이걸 안 하면 「진입 이하 마지막 스냅샷」이 **체결 뒤 최대 5분의
  가격**을 담는다 — 실제로 그 룩어헤드가 「직전 가격 급락 = 나쁨」이라는 가짜 효과를 2024년 이후에만
  만들었다. 시각은 **UTC**로 읽는다.
* 파일 안의 `symbol` 열이 파일 이름과 다르면 **거부**한다(엉뚱한 파일을 펼치면 판정 전체가
  무효다 — WAN-348 (c) 규약).

## 하지 않는 것

* ❌ **상시 수집·DB 적재** — 받은 zip은 `data/cache/`(gitignore) 아래 캐시 파일이고 프로덕션
  DB는 열지 않는다(WAN-194 · WAN-347/348 선례). 저장 방식(DB vs 파일)은 사용자 결정 자리이고
  이 모듈은 그중 **되돌리기 쉬운 쪽**(파일)만 안다.
* ❌ **보간** — 없는 날·없는 스냅샷을 지어내지 않는다. 구멍은 호출부가 세어 표에 싣는다.
* ❌ **전수 자동 정리** — 지우는 코드가 없다(WAN-194/297 원칙).

## ⚠️ 캐시는 「같은 이름이면 같은 내용」에 기댄다

확정된 과거 하루라 내용이 바뀌지 않는다. 받다가 끊긴 파일이 정상 파일과 같은 이름으로 남지 않게
임시 이름으로 받아 zip이 열리는 것까지 확인한 뒤 최종 이름을 붙인다(WAN-318 §백업 규약).
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from data.agg_trade_archive import archive_symbol
from data.tick_probe import VISION_BASE, HttpResponse, urllib_transport

#: 캐시 기본 위치 — `data/cache/`는 gitignore다(WAN-348 체결내역 캐시와 같은 자리).
DEFAULT_CACHE_DIR = Path("data/cache/wan417-metrics")
#: 스냅샷 간격 — **파일에서 실측**한 값(첫 두 서로 다른 `create_time`의 차 300초). `read_day`가
#: 파일마다 이 값을 다시 확인해 어긋나면 거부한다(「5분이라고 들었다」가 아니라 값으로).
SNAPSHOT_INTERVAL_MS = 300_000
SNAPSHOTS_PER_DAY = 86_400_000 // SNAPSHOT_INTERVAL_MS  # 288
MEASURED_SHIFT_FROM_DAY = "2024-03-04"
"""이 날(UTC) 파일부터 `create_time`은 구간 시작 라벨이고 값은 **라벨 + 5분**에 잰 것이다(실측 ·
12종목 같은 날). 🚨 날짜를 바꾸면 룩어헤드가 되살아난다 — `--part audit`이 매 실행 다시 잰다."""


def measured_shift_ms(day: str) -> int:
    """그 날 파일의 라벨 → 값을 잰 시각 보정(ms)."""
    return SNAPSHOT_INTERVAL_MS if day >= MEASURED_SHIFT_FROM_DAY else 0


#: S3 목록 — 존재하는 날짜를 하나하나 HEAD로 찌르지 않고 한 번에 얻는다(500키씩 페이지).
LISTING_BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

Transport = Callable[[str], HttpResponse]

_KEY_RE = re.compile(r"<Key>([^<]+)</Key>")
_MARKER_RE = re.compile(r"<NextMarker>([^<]+)</NextMarker>")
_DAY_RE = re.compile(r"-metrics-(\d{4}-\d{2}-\d{2})\.zip$")

OI_COLUMNS: tuple[str, str] = ("sum_open_interest", "sum_open_interest_value")
"""두 단위 — 코인 수량 · USDT 명목. **둘 다** 낸다(한 열만 「OI」라 부르지 않는다, WAN-417)."""


def metrics_url(symbol: str, day: str) -> str:
    """일자별 metrics zip 주소. `day`는 `YYYY-MM-DD`(UTC)."""
    bare = archive_symbol(symbol)
    return f"{VISION_BASE}/data/futures/um/daily/metrics/{bare}/{bare}-metrics-{day}.zip"


def listing_url(symbol: str, marker: str | None = None) -> str:
    bare = archive_symbol(symbol)
    url = f"{LISTING_BASE}?delimiter=/&prefix=data/futures/um/daily/metrics/{bare}/"
    return f"{url}&marker={marker}" if marker else url


def parse_listing_page(text: str, symbol: str) -> tuple[list[str], str | None]:
    """목록 XML 한 페이지 → (그 페이지의 날짜들, 다음 페이지 marker | None).

    `.CHECKSUM` 키는 날짜 정규식에 안 걸려 저절로 빠진다.
    """
    bare = archive_symbol(symbol)
    days: list[str] = []
    for key in _KEY_RE.findall(text):
        if not key.endswith(".zip") or f"/{bare}/{bare}-metrics-" not in key:
            continue
        match = _DAY_RE.search(key)
        if match:
            days.append(match.group(1))
    truncated = "<IsTruncated>true</IsTruncated>" in text
    marker = _MARKER_RE.search(text)
    return days, (marker.group(1) if truncated and marker else None)


def list_available_days(symbol: str, *, transport: Transport = urllib_transport) -> list[str]:
    """아카이브에 **실제로 존재하는** 날짜 전부(정렬 · 중복 없음). 목록을 못 받으면 죽는다
    (빈 목록을 「데이터 없음」으로 조용히 넘기면 커버리지 인구조사가 거짓이 된다)."""
    days: list[str] = []
    marker: str | None = None
    for _ in range(1000):  # 페이지 상한 — 6년치도 30페이지 안이다.
        response = transport(listing_url(symbol, marker))
        if response.status != 200:
            raise RuntimeError(f"{symbol} 목록 HTTP {response.status}: {response.text()}")
        page, marker = parse_listing_page(response.body.decode("utf-8", errors="replace"), symbol)
        days.extend(page)
        if marker is None:
            break
    else:  # pragma: no cover - 방어
        raise RuntimeError(f"{symbol} 목록 페이지가 끝나지 않습니다.")
    return sorted(set(days))


@dataclass(frozen=True, slots=True)
class DayFetch:
    """하루 파일 하나를 확보한 결과."""

    symbol: str
    day: str
    status: int
    path: Path | None
    size_bytes: int
    seconds: float
    """받는 데 걸린 시간 — **캐시 적중이면 0.0**."""
    cached: bool
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


def cache_path(symbol: str, day: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    bare = archive_symbol(symbol)
    return cache_dir / f"{bare}-metrics-{day}.zip"


def fetch_day(
    symbol: str,
    day: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    transport: Transport = urllib_transport,
) -> DayFetch:
    """그 종목·그 하루의 metrics zip을 확보한다(있으면 캐시, 없으면 받는다)."""
    bare = archive_symbol(symbol)
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_path(bare, day, cache_dir)
    if final.exists():
        return DayFetch(bare, day, 200, final, final.stat().st_size, 0.0, True)
    started = time.monotonic()
    response = transport(metrics_url(bare, day))
    elapsed = time.monotonic() - started
    if response.status != 200:
        return DayFetch(
            bare, day, response.status, None, 0, elapsed, False, f"HTTP {response.status}"
        )
    tmp = final.with_suffix(".zip.part")
    tmp.write_bytes(response.body)
    try:
        with zipfile.ZipFile(tmp) as archive:
            if not archive.namelist():
                raise zipfile.BadZipFile("빈 zip")
    except zipfile.BadZipFile as exc:
        tmp.unlink(missing_ok=True)
        return DayFetch(bare, day, response.status, None, 0, elapsed, False, f"손상: {exc}")
    tmp.replace(final)
    return DayFetch(bare, day, 200, final, len(response.body), elapsed, False)


def fetch_days(
    symbol: str,
    days: Iterable[str],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    transport: Transport = urllib_transport,
    jobs: int = 8,
) -> list[DayFetch]:
    """여러 날을 받는다 — I/O 바운드라 스레드. `jobs`는 성능 노브이지 결과 축이 아니다."""
    wanted = list(days)
    if jobs <= 1 or len(wanted) <= 1:
        return [fetch_day(symbol, d, cache_dir=cache_dir, transport=transport) for d in wanted]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(
            pool.map(
                lambda d: fetch_day(symbol, d, cache_dir=cache_dir, transport=transport), wanted
            )
        )


# --------------------------------------------------------------------------- #
# 파싱 — 형식 함정(중복 행 · 간격 · 심볼)을 여기서 막는다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DayRows:
    """하루 파일 하나를 읽은 결과 — 스냅샷과 **인구조사 값**을 함께 낸다."""

    symbol: str
    day: str
    times_ms: np.ndarray
    """정렬된 **값을 잰 시각**(UTC epoch ms) — 중복을 접고 `measured_shift_ms`를 더한 뒤."""
    oi_coin: np.ndarray
    oi_usdt: np.ndarray
    raw_rows: int
    """헤더를 뺀 원래 행 수(중복 포함)."""
    duplicate_rows: int
    """`create_time`이 같은 행이 다시 나온 횟수(값이 같든 다르든)."""
    conflicting_rows: int
    """같은 `create_time`인데 **값이 다른** 행 수 — 0이 아니면 그 파일은 믿지 못한다."""
    offgrid_rows: int
    """5분 격자에서 벗어난 스냅샷 수(실측: 전 아카이브 17개 · 몇 초 흔들림). **격자로 끌어오지
    않고 뺀다** — 끌어오면 그 순간에 없던 값을 지어낸다. 빠진 자리는 구멍으로 남는다."""
    measured_shift_ms: int
    """라벨 → 값을 잰 시각 보정(2024-03-04부터 +5분). `times_ms`에는 이미 더해져 있다."""
    zero_rows: int
    """미결제약정이 **0 이하**로 적힌 행(실측: 전 아카이브 2,703행 · 12종목이 **같은 달**에 함께
    — 2022-03에 종목마다 정확히 117행). 대형 선물의 OI는 0일 수 없으니 거래소 쪽 게시 결함이다
    (WAN-367 「하드 제로는 경보」). **값으로 쓰지 않고 뺀다** — 그대로 두면 기준점이 아닌 자리에서
    조용히 −100% 경로 점이 된다. 한 단위만 0이어도 그 스냅샷 전체를 뺀다(두 단위가 같은 스냅샷
    집합을 보게)."""
    spill_rows: int
    """**다음 날 첫 1분** 시각이 적힌 행(실측: 전 아카이브 5개 · 전부 2024-04-01~03). 그 시각은
    다음 날 파일의 몫이라 **뺀다** — 셋 중 셋이 다음 날 파일의 같은 시각과 값이 달랐다(어느 쪽을
    고를지 이 파일이 정하지 않는다)."""
    min_gap_ms: int | None
    """격자 위 서로 다른 시각 사이의 **최소** 차 — 「간격이 몇 분인가」의 실측값. 첫 두 행의 차로
    재면 그 사이 스냅샷 하나가 빠진 날(실측 BTC 2021-02-18 `00:00 → 00:10`)을 간격 변경으로
    오판한다."""

    @property
    def snapshots(self) -> int:
        return int(len(self.times_ms))


def parse_create_time(text: str) -> int:
    """`YYYY-MM-DD HH:MM:SS`(UTC) → epoch ms."""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp() * 1000)


def read_day_text(text: str, *, symbol: str, day: str) -> DayRows:
    """CSV 본문 → `DayRows`. 파일 이름의 종목·날짜와 안의 내용이 어긋나면 거부한다."""
    bare = archive_symbol(symbol)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{bare} {day}: 빈 파일")
    header = lines[0].split(",")
    try:
        i_time = header.index("create_time")
        i_sym = header.index("symbol")
        i_coin = header.index(OI_COLUMNS[0])
        i_usdt = header.index(OI_COLUMNS[1])
    except ValueError as exc:
        raise ValueError(f"{bare} {day}: 기대한 열이 없습니다 — {header}") from exc
    seen: dict[int, tuple[float, float]] = {}
    duplicates = 0
    conflicts = 0
    spill = 0
    zeros = 0
    raw = 0
    next_midnight = parse_create_time(f"{day} 00:00:00") + 86_400_000
    for line in lines[1:]:
        cols = line.split(",")
        raw += 1
        if cols[i_sym] != bare:
            raise ValueError(f"{bare} {day}: 파일 안의 종목이 {cols[i_sym]!r}입니다 — 엉뚱한 파일")
        t = parse_create_time(cols[i_time])
        if not cols[i_time].startswith(day):
            if next_midnight <= t < next_midnight + 60_000:
                spill += 1  # 다음 날 파일의 몫 — 세고 뺀다.
                continue
            raise ValueError(f"{bare} {day}: 파일 밖 날짜의 행 {cols[i_time]!r}")
        value = (float(cols[i_coin]), float(cols[i_usdt]))
        if value[0] <= 0.0 or value[1] <= 0.0:
            zeros += 1  # 게시 결함 — 값으로 쓰지 않는다(구멍으로 남는다).
            continue
        prior = seen.get(t)
        if prior is None:
            seen[t] = value
            continue
        duplicates += 1
        if prior != value:
            conflicts += 1
    on_grid = sorted(t for t in seen if t % SNAPSHOT_INTERVAL_MS == 0)
    offgrid = len(seen) - len(on_grid)
    shift = measured_shift_ms(day)
    times = np.array(on_grid, dtype=np.int64) + shift
    coin = np.array([seen[t][0] for t in on_grid], dtype=np.float64)
    usdt = np.array([seen[t][1] for t in on_grid], dtype=np.float64)
    min_gap = int(np.diff(times).min()) if len(times) >= 2 else None
    if min_gap is not None and min_gap != SNAPSHOT_INTERVAL_MS:
        # 간격은 실측으로 못 박은 상수다 — 다른 파일이 다른 간격이면 조용히 섞이지 않게 죽는다.
        raise ValueError(
            f"{bare} {day}: 격자 위 최소 스냅샷 간격이 {min_gap}ms — 기대 {SNAPSHOT_INTERVAL_MS}ms"
        )
    return DayRows(
        symbol=bare,
        day=day,
        times_ms=times,
        oi_coin=coin,
        oi_usdt=usdt,
        raw_rows=raw,
        duplicate_rows=duplicates,
        conflicting_rows=conflicts,
        offgrid_rows=offgrid,
        measured_shift_ms=shift,
        zero_rows=zeros,
        spill_rows=spill,
        min_gap_ms=min_gap,
    )


def read_day(path: Path, *, symbol: str, day: str) -> DayRows:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names:
            raise ValueError(f"{path}: 빈 zip")
        with archive.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8").read()
    return read_day_text(text, symbol=symbol, day=day)


# --------------------------------------------------------------------------- #
# 종목 시계열 — 여러 날을 이어 붙인 정렬 배열 (없는 날은 그냥 없다)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MetricsSeries:
    """한 종목의 미결제약정 스냅샷 전부 — 시각은 정렬 · 유일(조회는 `searchsorted`)."""

    symbol: str
    times_ms: np.ndarray
    oi_coin: np.ndarray
    oi_usdt: np.ndarray
    days_read: tuple[str, ...] = ()
    census: dict[str, int] = field(default_factory=dict)

    def index_at_or_before(self, t_ms: int) -> int:
        """`t_ms` 이하 마지막 스냅샷의 인덱스(없으면 −1) — 「그 순간에 알 수 있는 값」."""
        return int(np.searchsorted(self.times_ms, t_ms, side="right")) - 1

    def values_at(self, times: np.ndarray, column: str) -> np.ndarray:
        """**정확히** 그 시각의 스냅샷 값. 없으면 NaN — 보간하지 않는다."""
        source = self.oi_coin if column == OI_COLUMNS[0] else self.oi_usdt
        pos = np.searchsorted(self.times_ms, times, side="left")
        pos_clipped = np.minimum(pos, len(self.times_ms) - 1)
        hit = (pos < len(self.times_ms)) & (self.times_ms[pos_clipped] == times)
        out = np.full(len(times), np.nan, dtype=np.float64)
        out[hit] = source[pos_clipped[hit]]
        return out


def build_series(symbol: str, day_rows: Sequence[DayRows]) -> MetricsSeries:
    """읽은 날들을 시각순으로 잇는다. 날 사이 중복 시각(있을 리 없다)이 나오면 거부."""
    bare = archive_symbol(symbol)
    ordered = sorted(day_rows, key=lambda d: d.day)
    if any(d.symbol != bare for d in ordered):
        raise ValueError(f"{bare}: 다른 종목의 하루가 섞여 있습니다")
    if not ordered:
        empty = np.array([], dtype=np.int64)
        return MetricsSeries(bare, empty, np.array([]), np.array([]))
    times = np.concatenate([d.times_ms for d in ordered])
    gaps = np.diff(times)
    if np.any(gaps <= 0):
        raise ValueError(f"{bare}: 날을 이어 붙였더니 시각이 되돌아갑니다")
    census = {
        "offgrid_rows": int(sum(d.offgrid_rows for d in ordered)),
        "spill_rows": int(sum(d.spill_rows for d in ordered)),
        "zero_rows": int(sum(d.zero_rows for d in ordered)),
        "files_shifted": int(sum(1 for d in ordered if d.measured_shift_ms)),
        "missing_snapshots_in_files": int(
            sum(max(SNAPSHOTS_PER_DAY - d.snapshots, 0) for d in ordered)
        ),
        "gaps_regular": int((gaps == SNAPSHOT_INTERVAL_MS).sum()),
        "gaps_longer": int((gaps > SNAPSHOT_INTERVAL_MS).sum()),
        "raw_rows": int(sum(d.raw_rows for d in ordered)),
        "duplicate_rows": int(sum(d.duplicate_rows for d in ordered)),
        "conflicting_rows": int(sum(d.conflicting_rows for d in ordered)),
        "files_with_duplicates": int(sum(1 for d in ordered if d.duplicate_rows > 0)),
        "files_short": int(sum(1 for d in ordered if d.snapshots < SNAPSHOTS_PER_DAY)),
        "files_long": int(sum(1 for d in ordered if d.snapshots > SNAPSHOTS_PER_DAY)),
        "snapshots": int(len(times)),
    }
    return MetricsSeries(
        bare,
        times,
        np.concatenate([d.oi_coin for d in ordered]),
        np.concatenate([d.oi_usdt for d in ordered]),
        tuple(d.day for d in ordered),
        census,
    )


def load_series(
    symbol: str,
    days: Iterable[str],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> MetricsSeries:
    """캐시에 **있는 날만** 읽어 시계열을 만든다 — 없는 날은 받지 않는다(받는 것은 `fetch_days`)."""
    rows: list[DayRows] = []
    for day in days:
        path = cache_path(symbol, day, cache_dir)
        if path.exists():
            rows.append(read_day(path, symbol=symbol, day=day))
    return build_series(symbol, rows)


def day_range(day_lo: str, day_hi: str) -> list[str]:
    """`[day_lo, day_hi]`의 UTC 날짜 목록."""
    lo = datetime.strptime(day_lo, "%Y-%m-%d").replace(tzinfo=UTC)
    hi = datetime.strptime(day_hi, "%Y-%m-%d").replace(tzinfo=UTC)
    out: list[str] = []
    cursor = lo
    while cursor <= hi:
        out.append(cursor.strftime("%Y-%m-%d"))
        cursor = datetime.fromtimestamp(cursor.timestamp() + 86_400, tz=UTC)
    return out
