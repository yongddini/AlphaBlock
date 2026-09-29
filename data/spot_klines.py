"""바이낸스 **현물** 월별 봉 아카이브 → 격리된 「현물 루트」 DB (WAN-438).

## 왜 있나

채택 좌표의 6년 창(2020-09-15~)에는 **2018 하락장과 2020-03 코로나 폭락이 없다**. 선물 아카이브는
2020-01부터라 그 앞을 데울 수 없어서, 현물 1분봉으로 창 밖 폭락을 넣어 본다(사용자 제안 「스팟으로
보면 되잖아」). 이 모듈은 받기 · 검증 · 적재까지만 한다 — 측정은 `backtest.wan438_spot_stress`.

## 무엇을 받나

`data.binance.vision/data/spot/monthly/klines/<SYM>/<TF>/<SYM>-<TF>-<YYYY-MM>.zip` (+ `.CHECKSUM`).
CSV 열: `open_time · open · high · low · close · volume · close_time · …`. 헤더 없음.
⚠️ 2025년 이후 현물 파일은 시각이 **마이크로초**다 — 우리 구간(~2021)은 ms지만 판별해 되돌린다.

* **1분봉**(체결·청산 서브스텝)과 **거래소 원본 상위TF**(1h·4h·6h·8h·12h·1d·1w)를 받는다. 상위TF를
  1분봉에서 만들지 않는 이유: `data.resample`은 구성 1분봉이 **하나라도** 빠지면 그 봉을 만들지 않아
  (설계대로) 거래소 점검(2018년에 수 시간짜리가 있다)이 끼면 1d·1w 봉이 통째로 사라진다. 선물 DB의
  상위TF도 거래소 원본이다 — 같은 성질로 맞춘다. 2h는 선물과 똑같이 1h에서 **파생**한다
  (`data.storage._DERIVED_TIMEFRAMES`).

## 격리 — 운영 DB는 열지 않는다(WAN-194)

받은 zip은 `data/cache/wan438-spot/`(gitignore), 적재는 **별도 루트**(`<root>/data/ohlcv.db`)다.
저장 심볼은 **선물 표기**(`BTC/USDT:USDT`)로 둔다 — 백테 러너(`wan169.run_cells`)가 심볼을 그 표기로
정규화하고 상대경로 `data/ohlcv.db`를 읽으므로, 루트를 작업 디렉터리로 삼으면 러너 코드를 한 줄도 안
고치고 현물을 돈다. 🚨 **그래서 이 루트를 운영 DB 자리에 두면 안 된다** — `guard_root`가
루트가 저장소 `data/ohlcv.db`와 같은 파일이면 거부한다.

## 검증 (완료 기준 1)

* **체크섬** — `.CHECKSUM`(sha256)과 대조해 다르면 거부(받다 끊긴 파일이 정상 이름으로 남지 않는다).
* **봉 수 · 시각 연속성** — 월마다 기대 봉 수(그 달 분 수, 첫 달은 첫 봉부터) 대 저장 봉 수 · 구멍
  개수 · 가장 긴 구멍 · 중복 · 격자 밖 시각 · 그 달 밖 시각. **구멍은 지어내지 않는다**(보간 없음).
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import sqlite3
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from data.models import timeframe_to_ms
from data.storage import OhlcvStore
from data.tick_probe import VISION_BASE, HttpResponse, urllib_transport

LISTING_BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DEFAULT_CACHE_DIR = Path("data/cache/wan438-spot")

#: 현물 1분봉이 2018-01~2020-08에 있는 종목(이슈 §종목 · 2026-09-29 확인). 순서는 상장 순.
SPOT_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "LTCUSDT",
    "NEOUSDT",
    "XRPUSDT",
    "TRXUSDT",
    "LINKUSDT",
    "ADAUSDT",
    "ETCUSDT",
    "VETUSDT",
    "XLMUSDT",
    "DOGEUSDT",
    "ALGOUSDT",
    "ATOMUSDT",
    "THETAUSDT",
    "XMRUSDT",
    "XTZUSDT",
    "ZECUSDT",
    "DASHUSDT",
    "BCHUSDT",
)

#: 받는 TF — 1분봉 + 거래소 원본 상위TF. 2h는 1h에서 파생(선물 DB와 같은 규약), 3d는 이슈에서 제외.
NATIVE_TIMEFRAMES: tuple[str, ...] = ("1m", "1h", "4h", "6h", "8h", "12h", "1d", "1w")

_MICROS_THRESHOLD = 10**14
"""ms 시각은 2286년까지 1e13대 — 이보다 크면 마이크로초다(2025년 이후 현물 파일)."""

Transport = Callable[[str], HttpResponse]

_CONTENTS_RE = re.compile(r"<Contents><Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", re.S)
_MARKER_RE = re.compile(r"<NextMarker>([^<]+)</NextMarker>")


RETRIES = 4


def retrying(transport: Transport, *, retries: int = RETRIES, pause_s: float = 1.0) -> Transport:
    """네트워크 오류(상태 0)·5xx만 다시 부른다 — 4xx는 「없음」이라는 실측 결과라 그대로 둔다."""

    def call(url: str) -> HttpResponse:
        response = transport(url)
        for attempt in range(retries):
            if response.status != 0 and response.status < 500:
                break
            time.sleep(pause_s * (attempt + 1))
            response = transport(url)
        return response

    return call


def to_store_symbol(bare: str) -> str:
    """`BTCUSDT` → `BTC/USDT:USDT`(러너가 정규화하는 표기 — 모듈 머리말 §격리)."""
    if not bare.endswith("USDT"):
        raise ValueError(f"USDT 종목만 다룹니다: {bare!r}")
    return f"{bare[:-4]}/USDT:USDT"


def month_url(symbol: str, timeframe: str, month: str) -> str:
    base = f"{VISION_BASE}/data/spot/monthly/klines/{symbol}/{timeframe}"
    return f"{base}/{symbol}-{timeframe}-{month}.zip"


# --------------------------------------------------------------------------- #
# 목록 — 받기 전에 정확한 크기를 잰다(이슈 §데이터)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MonthFile:
    symbol: str
    timeframe: str
    month: str
    size_bytes: int


def list_months(
    symbol: str, timeframe: str, *, transport: Transport = urllib_transport
) -> list[MonthFile]:
    """S3 목록에서 그 종목·TF의 월별 zip과 **정확한 크기**를 얻는다(`.CHECKSUM` 제외)."""
    prefix = f"data/spot/monthly/klines/{symbol}/{timeframe}/"
    transport = retrying(transport)
    marker: str | None = None
    out: list[MonthFile] = []
    while True:
        url = f"{LISTING_BASE}?delimiter=/&prefix={prefix}"
        if marker:
            url += f"&marker={marker}"
        response = transport(url)
        if not response.ok:
            raise RuntimeError(f"목록 실패 {symbol} {timeframe}: HTTP {response.status}")
        text = response.body.decode("utf-8")
        for key, size in _CONTENTS_RE.findall(text):
            if not key.endswith(".zip"):
                continue
            month = key.rsplit("-", 2)[-2] + "-" + key.rsplit("-", 1)[-1].removesuffix(".zip")
            out.append(MonthFile(symbol, timeframe, month, int(size)))
        found = _MARKER_RE.search(text)
        if "<IsTruncated>true</IsTruncated>" not in text or not found:
            break
        marker = found.group(1)
    return sorted(out, key=lambda f: f.month)


def select_months(files: Iterable[MonthFile], first: str, last: str) -> list[MonthFile]:
    """`first` ≤ 월 ≤ `last`(`YYYY-MM` 문자열 비교)."""
    return [f for f in files if first <= f.month <= last]


# --------------------------------------------------------------------------- #
# 받기 — 체크섬 검증 후에만 최종 이름을 붙인다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MonthFetch:
    file: MonthFile
    path: Path | None
    seconds: float
    cached: bool
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


def cache_path(f: MonthFile, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / f.symbol / f.timeframe / f"{f.symbol}-{f.timeframe}-{f.month}.zip"


def fetch_month(
    f: MonthFile, *, cache_dir: Path = DEFAULT_CACHE_DIR, transport: Transport = urllib_transport
) -> MonthFetch:
    final = cache_path(f, cache_dir)
    if final.exists() and final.stat().st_size == f.size_bytes:
        return MonthFetch(f, final, 0.0, True)
    final.parent.mkdir(parents=True, exist_ok=True)
    transport = retrying(transport)
    started = time.monotonic()
    url = month_url(f.symbol, f.timeframe, f.month)
    body = transport(url)
    check = transport(url + ".CHECKSUM")
    elapsed = time.monotonic() - started
    if not body.ok:
        return MonthFetch(f, None, elapsed, False, f"HTTP {body.status}")
    if len(body.body) != f.size_bytes:
        return MonthFetch(f, None, elapsed, False, f"크기 {len(body.body)} ≠ 목록 {f.size_bytes}")
    if check.ok:
        want = check.body.decode("utf-8").split()[0].lower()
        got = hashlib.sha256(body.body).hexdigest()
        if want != got:
            return MonthFetch(f, None, elapsed, False, f"체크섬 불일치 {got[:12]} ≠ {want[:12]}")
    tmp = final.with_suffix(".zip.part")
    tmp.write_bytes(body.body)
    tmp.replace(final)
    note = "" if check.ok else f"체크섬 파일 없음(HTTP {check.status}) — 크기만 대조"
    return MonthFetch(f, final, elapsed, False, note)


def fetch_months(
    files: Sequence[MonthFile],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    transport: Transport = urllib_transport,
    jobs: int = 8,
) -> list[MonthFetch]:
    """I/O 바운드라 스레드. `jobs`는 성능 노브이지 결과 축이 아니다."""
    if jobs <= 1 or len(files) <= 1:
        return [fetch_month(f, cache_dir=cache_dir, transport=transport) for f in files]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(
            pool.map(lambda f: fetch_month(f, cache_dir=cache_dir, transport=transport), files)
        )


# --------------------------------------------------------------------------- #
# 읽기 · 검증
# --------------------------------------------------------------------------- #

_OHLCV_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


def read_zip_bytes(data: bytes) -> pd.DataFrame:
    """zip 안 CSV 한 장 → `open_time · open · high · low · close · volume`(시각은 ms)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"CSV가 정확히 하나여야 합니다: {archive.namelist()}")
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(6))
    frame.columns = list(_OHLCV_COLUMNS)
    if not pd.api.types.is_integer_dtype(frame["open_time"]):
        # 헤더가 붙은 파일(최근 일부) — 첫 행을 버린다.
        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()]
        frame = frame.astype({"open_time": "int64"} | {c: float for c in _OHLCV_COLUMNS[1:]})
    times = frame["open_time"].to_numpy(np.int64)
    if len(times) and int(times.max()) > _MICROS_THRESHOLD:
        frame["open_time"] = times // 1000
    return frame.reset_index(drop=True)


def read_month(path: Path) -> pd.DataFrame:
    return read_zip_bytes(path.read_bytes())


def month_bounds(month: str) -> tuple[int, int]:
    """`YYYY-MM`의 [시작, 끝) UTC ms."""
    year, mon = (int(x) for x in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=UTC)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class MonthCheck:
    """한 달 파일의 봉 수 · 시각 연속성 검사(1분봉·일중 TF는 격자, 1w는 월요일 앵커)."""

    symbol: str
    timeframe: str
    month: str
    bars: int
    expected: int
    """그 달 안(첫 달은 첫 봉부터) 격자 칸 수 — 1w는 판정 안 함(-1)."""
    gaps: int
    """빠진 칸 수(연속성)."""
    longest_gap_bars: int
    duplicates: int
    off_grid: int
    outside_month: int

    @property
    def ok(self) -> bool:
        return self.duplicates == 0 and self.off_grid == 0 and self.outside_month == 0


def on_grid(frame: pd.DataFrame, timeframe: str) -> pd.Series:
    """봉 시각이 그 TF 격자 위에 있는가(1w는 월요일 00:00 UTC).

    🚨 거래소 원본에 **격자 밖 1분봉**이 실제로 있다(실측 2026-09-29): 2017-12-04 06:00부터
    BTC·ETH·BNB·NEO가 +20.8초, 2018-02-09~10(장시간 점검 직후)에 BTC·ETH·BNB·LTC·NEO가
    +14.8~16.8초 밀려 있다. 분으로 내려 맞추면 시각을 지어내는 것이라 **버리고 구멍으로 센다**
    (`MonthCheck.off_grid`가 그 수다).
    """
    t = frame["open_time"].astype("int64")
    if timeframe == "1w":
        return (t % 86_400_000 == 0) & (((t // 86_400_000) + 3) % 7 == 0)
    return t % timeframe_to_ms(timeframe) == 0


def check_month(frame: pd.DataFrame, *, symbol: str, timeframe: str, month: str) -> MonthCheck:
    lo, hi = month_bounds(month)
    t = np.sort(frame["open_time"].to_numpy(np.int64))
    tf_ms = timeframe_to_ms(timeframe)
    outside = int(((t < lo) | (t >= hi)).sum()) if timeframe != "1w" else 0
    dup = int(len(t) - len(np.unique(t)))
    if timeframe == "1w":
        # 거래소 주봉은 월요일 00:00 UTC 앵커(에포크는 목요일) — 격자 판정은 요일로 한다.
        weekday = ((t // 86_400_000) + 3) % 7  # 월요일=0
        off = int(((t % 86_400_000) != 0).sum() + (weekday != 0).sum())
        return MonthCheck(symbol, timeframe, month, len(t), -1, 0, 0, dup, off, 0)
    off = int((t % tf_ms != 0).sum())
    u = np.unique(t)
    if len(u) == 0:
        return MonthCheck(symbol, timeframe, month, 0, 0, 0, 0, dup, off, outside)
    first = max(lo, int(u[0]))
    expected = (hi - first) // tf_ms
    steps = np.diff(np.concatenate(([first - tf_ms], u, [hi]))) // tf_ms - 1
    steps = steps[steps > 0]
    return MonthCheck(
        symbol,
        timeframe,
        month,
        len(t),
        int(expected),
        int(steps.sum()),
        int(steps.max()) if len(steps) else 0,
        dup,
        off,
        outside,
    )


# --------------------------------------------------------------------------- #
# 현물 루트 적재
# --------------------------------------------------------------------------- #

_INSERT = (
    "INSERT OR IGNORE INTO ohlcv (symbol, timeframe, open_time, open, high, low, close, volume,"
    " closed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)"
)


def root_db_path(root: Path) -> Path:
    """러너가 읽는 상대경로(`data/ohlcv.db`)의 루트 판."""
    return root / "data" / "ohlcv.db"


def guard_root(root: Path, *, repo_db: Path = Path("data/ohlcv.db")) -> Path:
    """🚨 루트 DB가 저장소 운영 DB와 같은 파일이면 거부한다(WAN-194)."""
    db = root_db_path(root).resolve()
    if repo_db.exists() and db == repo_db.resolve():
        raise ValueError(f"현물 루트가 운영 DB를 가리킵니다: {db}")
    return db


def load_into_root(
    root: Path,
    frames: Iterable[tuple[str, str, pd.DataFrame]],
    *,
    start_ms: dict[tuple[str, str], int] | None = None,
    end_ms: int | None = None,
) -> dict[tuple[str, str], int]:
    """(선물 표기 심볼, TF, 봉) 들을 루트 DB에 넣는다 → (심볼, TF)별 적재 행 수.

    `start_ms`는 (심볼, TF)별 하한(없으면 전부) · `end_ms`는 공통 상한(미포함). 같은 시각이 두 번
    오면 첫 것을 남긴다(`INSERT OR IGNORE` — 월 경계 주봉이 두 파일에 걸칠 수 있다).
    """
    db = guard_root(root)
    OhlcvStore(db).close()  # 스키마 생성(운영 스키마 그대로)
    counts: dict[tuple[str, str], int] = {}
    conn = sqlite3.connect(db)
    try:
        for symbol, timeframe, frame in frames:
            t = frame["open_time"].to_numpy(np.int64)
            keep = np.ones(len(t), dtype=bool)
            lo = (start_ms or {}).get((symbol, timeframe))
            if lo is not None:
                keep &= t >= lo
            if end_ms is not None:
                keep &= t < end_ms
            sub = frame[keep]
            rows = [
                (
                    symbol,
                    timeframe,
                    int(r[0]),
                    float(r[1]),
                    float(r[2]),
                    float(r[3]),
                    float(r[4]),
                    float(r[5]),
                )
                for r in sub[list(_OHLCV_COLUMNS)].itertuples(index=False, name=None)
            ]
            before = conn.total_changes
            with conn:
                conn.executemany(_INSERT, rows)
            counts[(symbol, timeframe)] = counts.get((symbol, timeframe), 0) + (
                conn.total_changes - before
            )
    finally:
        conn.close()
    return counts


# --------------------------------------------------------------------------- #
# 불가능한 틱 격리 — 거래소 가격 보호 필터 밖의 봉
# --------------------------------------------------------------------------- #

PRICE_BAND = 5.0
"""바이낸스 현물 PERCENT_PRICE 필터(배율 상 5 · 하 0.2) — 평균가 대비 이 배율 밖의 주문은 거래소가
거부하므로 그 가격의 체결은 **원리적으로 불가능**하다. 결과를 보고 고른 값이 아니라 거래소 규칙이다.

🚨 실측(2026-09-29): LINK/USDT 현물 2020-03-12 10:48 1분봉 저가 **0.0001**(시가 2.137 · 종가 2.2).
그 한 틱이 LINK의 깊은 존 수십 개를 같은 1분에 「체결 → 손절」시켜 실재하지 않는 거래를 만들었다.
"""


def impossible_mask(frame: pd.DataFrame, band: float = PRICE_BAND) -> pd.Series:
    """저가가 min(시가, 종가)의 1/band 미만이거나 고가가 max(시가, 종가)의 band배 초과인 봉."""
    body_lo = frame[["open", "close"]].min(axis=1)
    body_hi = frame[["open", "close"]].max(axis=1)
    return (frame["low"] < body_lo / band) | (frame["high"] > body_hi * band)


@dataclass(frozen=True, slots=True)
class Quarantine:
    symbol: str
    timeframe: str
    open_time: int
    action: str
    """`삭제`(1분봉 — 구멍으로 센다) 또는 `저가·고가 재계산`(그 분을 품은 상위TF 봉)."""
    old_low: float
    new_low: float
    old_high: float
    new_high: float


def quarantine_impossible_ticks(root: Path, *, band: float = PRICE_BAND) -> list[Quarantine]:
    """루트 DB에서 불가능한 1분봉을 지우고, 그 분을 품은 상위TF 봉의 저가·고가를 남은 1분봉으로
    다시 잰다(시가·종가·거래량은 그대로). 상위TF 봉 자체에 1분봉 근거 없이 불가능한 값이 있으면
    손대지 않고 `검토 필요`로 남긴다(지어내지 않는다)."""
    db = guard_root(root)
    conn = sqlite3.connect(db)
    out: list[Quarantine] = []
    lo, hi = 1.0 / band, band
    cond = "(low < ? * MIN(open, close) OR high > ? * MAX(open, close))"
    try:
        bad_1m = conn.execute(
            f"SELECT symbol, open_time, low, high FROM ohlcv WHERE timeframe = '1m' AND {cond}",
            (lo, hi),
        ).fetchall()
        with conn:
            for symbol, t, low, high in bad_1m:
                conn.execute(
                    "DELETE FROM ohlcv WHERE symbol = ? AND timeframe = '1m' AND open_time = ?",
                    (symbol, t),
                )
                out.append(Quarantine(symbol, "1m", int(t), "삭제", low, math.nan, high, math.nan))
        bad_htf = conn.execute(
            f"SELECT symbol, timeframe, open_time, low, high FROM ohlcv "
            f"WHERE timeframe != '1m' AND {cond}",
            (lo, hi),
        ).fetchall()
        with conn:
            for symbol, tf, t, low, high in bad_htf:
                end = int(t) + timeframe_to_ms(tf)
                new_low, new_high = conn.execute(
                    "SELECT MIN(low), MAX(high) FROM ohlcv WHERE symbol = ? AND timeframe = '1m' "
                    "AND open_time >= ? AND open_time < ?",
                    (symbol, int(t), end),
                ).fetchone()
                explained = any(q.symbol == symbol and int(t) <= q.open_time < end for q in out)
                if new_low is None or not explained:
                    out.append(Quarantine(symbol, tf, int(t), "검토 필요", low, low, high, high))
                    continue
                conn.execute(
                    "UPDATE ohlcv SET low = ?, high = ? "
                    "WHERE symbol = ? AND timeframe = ? AND open_time = ?",
                    (new_low, new_high, symbol, tf, int(t)),
                )
                out.append(
                    Quarantine(symbol, tf, int(t), "저가·고가 재계산", low, new_low, high, new_high)
                )
    finally:
        conn.close()
    return out
