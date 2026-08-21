"""1분보다 잘게 보는 데이터의 **하루치 실측** (WAN-347 §0). 읽기 전용.

## 왜 이 모듈이 있나

WAN-336이 잰 것: 채택 북 `oos_warm` 순손익의 **약 48%**가 「진입한 그 1분 안에서 익절」한
거래에 실려 있다(467건 / 7.37%). 그게 성립하려면 그 1분 안에서 **저가가 먼저 · 고가가
나중**이어야 하는데 **1분봉은 안의 순서를 모른다**. 진값을 좁히는 유일한 길이 1분보다 잘게
보는 데이터이고, 그걸 모으기 **전에** 크기부터 재는 것이 이 모듈이다.

🚨 **추정으로 고르지 않는다.** 「1초봉이 체결내역보다 두 자릿수 작다」는 추정이 있었고 이
저장소는 추정을 서버로 옮겨 두 번 틀렸다(WAN-203 셀 비용 2배 과대 추정 · WAN-194 doctor
12초를 서버 기준으로 인용). 그래서 이 모듈은 **실제로 받아서 실제로 넣어 보고 잰다** —
행 수 · 압축 후 바이트 · SQLite 테이블 바이트 · 받는 시간 · 넣는 시간.

## 무엇을 재나 — 축이 「자료 종류」 하나가 아니라 「시장 × 자료 종류」다

📌 **1초봉과 체결내역은 같은 시장에 나란히 있지 않다** — 이것이 이 모듈의 첫 실측이고,
이슈가 표로 물은 「1초봉 vs 체결내역」이 실은 **고를 수 있는 두 값이 아니다**:

* **USDⓈ-M 선물(우리가 매매·백테하는 시장)에는 1초봉이 없다.** REST가
  `-1120 Invalid interval`로 거절하고 아카이브에도 `1s` 디렉터리가 없다.
* **1초봉은 현물 전용 상품**이다. 즉 1초봉을 쓰려면 **다른 시장의 가격**으로 선물의 봉내
  순서를 추정하게 된다(베이시스·체결 시차가 섞인다).

그래서 격자를 `(시장, 자료)`로 두고 **없는 칸도 「없음」으로 표에 남긴다** — 조용히 빼면
다음 사람이 「왜 1초봉 열이 비었지」를 다시 조사한다.

## 받는 경로가 하나뿐인 것도 실측이다

선물 `aggTrades` REST는 **최근 2일**만 돌려준다(`-4166 Search window is restricted to
recent 2 days only`). 즉 **§2(의심 구간 과거 백필)는 REST로 불가능**하고 일자별 아카이브
(`data.binance.vision`)가 유일한 길이다. 이 모듈은 그 사실을 주장하지 않고 **한 번씩 실제로
찔러 보고 거래소가 준 응답을 그대로 적는다**(`probe_rest_availability`).

## 이 모듈이 하지 않는 것

* ❌ **상시 수집기 배선(§1)·의심 구간 백필(§2)** — §0 표를 보고 범위를 정한 뒤다.
* ❌ **프로덕션 DB에 쓰기.** SQLite 크기는 **스크래치 파일**에 candidate 스키마로 넣어
  재고, 기본적으로 잰 뒤 지운다(`keep=False`). 기존 테이블은 열지도 않는다(WAN-194 원칙).
* ❌ **호가창(depth)** — 우리 질문(봉내 순서)에 불필요하고 제일 크다(WAN-98이 취소된 이유).
* ❌ **백테스트를 틱/1초봉으로 올리는 것** — 엔진은 1분봉 서브스텝 위에 서 있고
  (`backtest/substep.py`) 그 승격은 거대한 재-베이스라인 + 사용자 결정이다.

## 형식 함정 둘 — 실측으로 확인했고 코드가 막는다

1. **아카이브 CSV의 헤더 유무가 자료마다 다르다** — 선물 `aggTrades`는 헤더가 **있고**
   현물 `1s` 클라인은 **없다**. 첫 칸이 정수가 아니면 헤더로 보고 건너뛴다.
2. **시각 단위가 자료마다 다르다** — 선물 `aggTrades`의 `transact_time`은 **밀리초**인데
   현물 `1s` 클라인의 `open_time`은 **마이크로초**다(2025년 이후 파일). 크기로 판별해
   ms로 정규화한다(`normalize_epoch_ms`). 안 하면 2026년 데이터가 **서기 58,000년**으로
   들어가고, 그건 조용히 통과한다.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

#: 일자별 아카이브(공개 · 인증 불필요). §2 백필의 **유일한** 경로다(위 도크스트링).
VISION_BASE = "https://data.binance.vision"
#: USDⓈ-M 선물 REST(우리가 매매·백테하는 시장).
FAPI_BASE = "https://fapi.binance.com"
#: 현물 REST — **1초봉이 있는 유일한 시장**이지만 우리가 매매하는 시장이 아니다.
SPOT_BASE = "https://api.binance.com"

#: 대표 3종목(이슈 지정): 가장 활발 · 중간 · 가장 한산.
#:
#: ⚠️ **활발/한산의 순서는 고정된 사실이 아니라 이 실측이 확인할 것**이다 — 표에 행 수가
#: 같이 나오므로 순서가 어긋나면 그 표에서 바로 보인다.
DEFAULT_PROBE_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "SOLUSDT", "TRXUSDT")

#: 마이크로초 판별선. 2001-09-09(ms=1e12)을 훌쩍 넘는 값은 ms일 수 없다 —
#: ms로 1e14는 서기 5138년이라 시세 데이터에 나올 수 없고, 마이크로초 2026년은 1.78e15다.
_MICROS_THRESHOLD = 100_000_000_000_000

_DAY_MS = 86_400_000
#: 스크래치 하위 디렉터리 이름 — 준 경로를 통째로 지우지 않기 위한 울타리다.
SCRATCH_DIRNAME = "wan347-tick-probe"
#: SQLite 배치 크기 — 1GB 박스에서 돌므로 행을 통째로 들고 있지 않는다.
_INSERT_BATCH = 50_000
_HTTP_TIMEOUT_S = 120.0

Market = Literal["future", "spot"]
Source = Literal["agg_trades", "klines_1s"]

#: 사람이 읽는 이름(표·문서용).
MARKET_LABEL: dict[Market, str] = {"future": "선물(USDⓈ-M)", "spot": "현물"}
SOURCE_LABEL: dict[Source, str] = {"agg_trades": "체결내역", "klines_1s": "1초봉"}


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    """무엇을 재는가 — `(종목, 시장, 자료)` 한 칸."""

    symbol: str
    market: Market
    source: Source

    @property
    def label(self) -> str:
        return f"{self.symbol} {MARKET_LABEL[self.market]} {SOURCE_LABEL[self.source]}"


def default_specs(symbols: Sequence[str]) -> list[ProbeSpec]:
    """이슈가 물은 격자 — 종목마다 세 칸.

    선물 1초봉 칸을 **일부러 넣는다**: 없다는 사실이 이 실측의 결론 중 하나라
    표에 「없음」으로 남아야 한다(빼면 다음 사람이 다시 조사한다).
    """
    specs: list[ProbeSpec] = []
    for symbol in symbols:
        specs.append(ProbeSpec(symbol, "future", "agg_trades"))
        specs.append(ProbeSpec(symbol, "future", "klines_1s"))
        specs.append(ProbeSpec(symbol, "spot", "klines_1s"))
    return specs


# ---------------------------------------------------------------------------
# HTTP — `common.telegram`과 같은 패턴(주입 가능한 transport라 테스트에 네트워크 불필요)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """HTTP 한 번의 결과. 4xx도 **예외가 아니라 값**으로 온다(없음을 표에 적어야 한다)."""

    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, limit: int = 200) -> str:
        return self.body[:limit].decode("utf-8", errors="replace").strip()


class HttpTransport(Protocol):
    """`GET url` 하나. 네트워크 의존부를 여기 한 겹으로 가둔다.

    인자를 **위치 전용**(`/`)으로 둔 것은 취향이 아니다 — 그래야 평범한 함수·람다가 그대로
    이 프로토콜을 만족해 테스트가 가짜 transport를 한 줄로 주입한다.
    """

    def __call__(self, url: str, /) -> HttpResponse: ...


def urllib_transport(url: str) -> HttpResponse:
    """표준 라이브러리만으로 GET한다(새 의존성 없음 — `common.telegram` 선례)."""
    request = urllib.request.Request(url, headers={"User-Agent": "alphablock-tick-probe/1"})
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as resp:
            status = int(resp.status)
            return HttpResponse(status=status, body=resp.read())
    except urllib.error.HTTPError as exc:  # 4xx/5xx는 「없음」이라는 실측 결과다
        return HttpResponse(status=int(exc.code), body=exc.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return HttpResponse(status=0, body=str(exc).encode("utf-8"))


# ---------------------------------------------------------------------------
# URL 조립
# ---------------------------------------------------------------------------


def vision_url(spec: ProbeSpec, day: str) -> str:
    """일자별 아카이브 zip 주소. `day`는 `YYYY-MM-DD`(UTC)."""
    root = "futures/um" if spec.market == "future" else "spot"
    if spec.source == "agg_trades":
        return (
            f"{VISION_BASE}/data/{root}/daily/aggTrades/{spec.symbol}/"
            f"{spec.symbol}-aggTrades-{day}.zip"
        )
    return f"{VISION_BASE}/data/{root}/daily/klines/{spec.symbol}/1s/{spec.symbol}-1s-{day}.zip"


def rest_probe_url(spec: ProbeSpec, day_start_ms: int) -> str:
    """「REST로 그 과거 하루를 받을 수 있나」를 한 번 찔러 보는 주소.

    받아 오려는 게 아니라 **거래소가 뭐라고 하는지**를 표에 적으려는 것이다.
    """
    base = FAPI_BASE if spec.market == "future" else SPOT_BASE
    prefix = "fapi/v1" if spec.market == "future" else "api/v3"
    if spec.source == "agg_trades":
        return (
            f"{base}/{prefix}/aggTrades?symbol={spec.symbol}"
            f"&startTime={day_start_ms}&endTime={day_start_ms + 60_000}&limit=1"
        )
    return f"{base}/{prefix}/klines?symbol={spec.symbol}&interval=1s&limit=1"


# ---------------------------------------------------------------------------
# 파싱 — 형식 함정 둘을 여기서 막는다
# ---------------------------------------------------------------------------


def normalize_epoch_ms(raw: int) -> int:
    """마이크로초로 적힌 시각을 밀리초로 되돌린다(현물 1초봉 아카이브가 그렇다).

    🚨 안 하면 2026년이 서기 58,000년으로 들어가고 **조용히 통과한다** — 행 수도 크기도
    멀쩡해 보이는데 시각만 틀린, 이 저장소가 가장 경계하는 부류의 실패다.
    """
    return raw // 1000 if raw >= _MICROS_THRESHOLD else raw


def _is_header(line: str) -> bool:
    """첫 칸이 정수가 아니면 헤더 줄이다(선물 체결내역엔 있고 현물 1초봉엔 없다)."""
    head = line.split(",", 1)[0].strip()
    if not head:
        return False
    try:
        int(head)
    except ValueError:
        return True
    return False


def iter_agg_trade_rows(lines: Iterable[str]) -> Iterator[tuple[int, int, float, float]]:
    """체결내역 CSV → `(체결 묶음 id, 시각ms, 가격, 수량)`.

    묶음 id를 버리지 않는 이유: **같은 시각·가격·수량의 체결이 실제로 있어서** 그것 없이는
    자연키가 없다. 키가 없으면 UPSERT가 서로 다른 체결을 하나로 접어 **행이 조용히 준다**.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped or _is_header(stripped):
            continue
        parts = stripped.split(",")
        # agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker
        yield (
            int(parts[0]),
            normalize_epoch_ms(int(parts[5])),
            float(parts[1]),
            float(parts[2]),
        )


KlineRow = tuple[int, float, float, float, float, float]


def iter_kline_rows(lines: Iterable[str]) -> Iterator[KlineRow]:
    """1초봉 CSV → `(open_time ms, open, high, low, close, volume)`."""
    for line in lines:
        stripped = line.strip()
        if not stripped or _is_header(stripped):
            continue
        parts = stripped.split(",")
        yield (
            normalize_epoch_ms(int(parts[0])),
            float(parts[1]),
            float(parts[2]),
            float(parts[3]),
            float(parts[4]),
            float(parts[5]),
        )


# ---------------------------------------------------------------------------
# candidate 스키마 — §1이 만들 새 테이블의 **후보**다(여기선 스크래치에만 만든다)
# ---------------------------------------------------------------------------

#: 봉내 순서 질문(WAN-336)에 필요한 **최소** 열만 담는다. 호가·매수자메이커·체결 id 범위는
#: 그 질문에 안 쓰이므로 뺐다 — 더 담으면 그만큼 커진다(이 표의 수치는 최소판 기준).
AGG_TRADE_SCHEMA = """
CREATE TABLE tick_probe_agg_trades (
    symbol   TEXT    NOT NULL,
    agg_id   INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    price    REAL    NOT NULL,
    qty      REAL    NOT NULL,
    PRIMARY KEY (symbol, agg_id)
);
CREATE INDEX ix_tick_probe_agg_trades_ts ON tick_probe_agg_trades (symbol, ts);
"""

KLINE_1S_SCHEMA = """
CREATE TABLE tick_probe_klines_1s (
    symbol    TEXT    NOT NULL,
    open_time INTEGER NOT NULL,
    open      REAL    NOT NULL,
    high      REAL    NOT NULL,
    low       REAL    NOT NULL,
    close     REAL    NOT NULL,
    volume    REAL    NOT NULL,
    PRIMARY KEY (symbol, open_time)
);
"""


def _schema_for(source: Source) -> tuple[str, str]:
    """`(DDL, INSERT문)` — 열 개수는 두 문자열이 함께 들고 있다."""
    if source == "agg_trades":
        return (AGG_TRADE_SCHEMA, "INSERT INTO tick_probe_agg_trades VALUES (?, ?, ?, ?, ?)")
    return (KLINE_1S_SCHEMA, "INSERT INTO tick_probe_klines_1s VALUES (?, ?, ?, ?, ?, ?, ?)")


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """한 칸(`ProbeSpec`) 하루치의 실측."""

    spec: ProbeSpec
    day: str
    available: bool
    note: str
    """없으면 왜 없는지(거래소 응답 그대로). 있으면 빈 문자열."""
    rows: int
    download_bytes: int
    """실제로 받은 바이트 = 아카이브 zip 크기(= 압축 후 디스크)."""
    raw_bytes: int
    """압축을 푼 CSV 바이트."""
    gzip_bytes: int
    """최소 열만 남겨 우리가 다시 gzip한 크기 — 「파일로 보관」 안의 비용."""
    sqlite_bytes: int
    """candidate 테이블에 실제로 넣었을 때의 DB 파일 크기 — 「DB에 넣기」 안의 비용."""
    download_s: float
    ingest_s: float

    @property
    def elapsed_s(self) -> float:
        return self.download_s + self.ingest_s


def unavailable(spec: ProbeSpec, day: str, note: str) -> ProbeResult:
    """「이 칸은 존재하지 않는다」를 결과로 만든다 — 조용히 빼지 않는다."""
    return ProbeResult(
        spec=spec,
        day=day,
        available=False,
        note=note,
        rows=0,
        download_bytes=0,
        raw_bytes=0,
        gzip_bytes=0,
        sqlite_bytes=0,
        download_s=0.0,
        ingest_s=0.0,
    )


@dataclass(frozen=True, slots=True)
class RestAvailability:
    """「REST로 그 과거 하루를 받을 수 있나」 한 번 찔러 본 결과."""

    spec: ProbeSpec
    status: int
    message: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


# ---------------------------------------------------------------------------
# 실측
# ---------------------------------------------------------------------------


def day_start_ms(day: str) -> int:
    """`YYYY-MM-DD`(UTC) → epoch ms. 데이터 창이라 표시용 KST가 아니라 UTC다."""
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def probe_rest_availability(
    specs: Sequence[ProbeSpec],
    day: str,
    *,
    transport: HttpTransport = urllib_transport,
) -> list[RestAvailability]:
    """칸마다 REST를 한 번씩 찔러 거래소 응답을 그대로 적는다.

    주장을 코드로 적지 않고 **응답을 표에 남기는** 것이 요점이다 — 「선물엔 1초봉이 없다」
    「체결내역 REST는 최근 2일뿐」이 다음 사람에게 재현 가능한 사실로 남는다.
    """
    start = day_start_ms(day)
    out: list[RestAvailability] = []
    for spec in specs:
        resp = transport(rest_probe_url(spec, start))
        message = resp.text()
        if resp.ok:
            message = "ok"
        else:
            try:
                payload = json.loads(resp.body.decode("utf-8", errors="replace"))
                if isinstance(payload, dict) and "msg" in payload:
                    message = f"{payload.get('code')}: {payload['msg']}"
            except (ValueError, UnicodeDecodeError):
                pass
        out.append(RestAvailability(spec=spec, status=resp.status, message=message))
    return out


def cell_paths(spec: ProbeSpec, day: str, scratch_dir: Path) -> tuple[Path, Path, Path]:
    """이 칸이 만드는 파일 셋 `(zip, gzip, sqlite)`.

    이름을 **한 곳에서만** 짓는다 — 만드는 쪽과 지우는 쪽이 각자 지으면 지우는 쪽이
    한 발 어긋나고, 그러면 1GB 박스에서 조용히 쌓인다.
    """
    stem = f"{spec.symbol}-{spec.market}-{spec.source}-{day}"
    return (
        scratch_dir / f"{stem}.zip",
        scratch_dir / f"{stem}.csv.gz",
        scratch_dir / f"{stem}.db",
    )


def _ingest(
    spec: ProbeSpec,
    day: str,
    member: io.TextIOWrapper,
    *,
    scratch_dir: Path,
    with_sqlite: bool,
) -> tuple[int, int, int]:
    """CSV를 흘려보내며 `(행 수, gzip 바이트, sqlite 바이트)`를 잰다.

    행을 통째로 들고 있지 않는다 — 1GB 박스에서 BTC 하루치(수백만 행)를 돌려야 한다.
    """
    ddl, insert_sql = _schema_for(spec.source)
    _, gz_path, db_path = cell_paths(spec, day, scratch_dir)
    for stale in (gz_path, db_path):
        stale.unlink(missing_ok=True)

    conn: sqlite3.Connection | None = None
    if with_sqlite:
        conn = sqlite3.connect(db_path)
        # 스크래치라 저널을 남기지 않는다 — 남으면 파일 크기 측정이 그만큼 흐려진다.
        conn.execute("PRAGMA journal_mode=OFF")
        conn.executescript(ddl)

    rows = 0
    batch: list[tuple[object, ...]] = []
    parsed_rows: Iterator[tuple[object, ...]]
    if spec.source == "agg_trades":
        parsed_rows = iter_agg_trade_rows(member)
    else:
        parsed_rows = iter_kline_rows(member)
    try:
        with gzip.open(gz_path, "wt", encoding="utf-8", compresslevel=6) as gz:
            for parsed in parsed_rows:
                rows += 1
                gz.write(",".join(str(v) for v in parsed) + "\n")
                if conn is not None:
                    batch.append((spec.symbol, *parsed))
                    if len(batch) >= _INSERT_BATCH:
                        conn.executemany(insert_sql, batch)
                        batch.clear()
        if conn is not None:
            if batch:
                conn.executemany(insert_sql, batch)
            conn.commit()
            stored = int(conn.execute(_count_sql(spec.source)).fetchone()[0])
            if stored != rows:
                # 자연키가 서로 다른 행을 접었다는 뜻 — 조용히 통과시키면 이 표가 거짓이 된다.
                raise RuntimeError(
                    f"{spec.label}: 파싱 {rows}행인데 저장 {stored}행 — 키가 행을 접었다"
                )
    finally:
        if conn is not None:
            conn.close()

    gzip_bytes = gz_path.stat().st_size
    sqlite_bytes = db_path.stat().st_size if with_sqlite and db_path.exists() else 0
    return rows, gzip_bytes, sqlite_bytes


def _count_sql(source: Source) -> str:
    table = "tick_probe_agg_trades" if source == "agg_trades" else "tick_probe_klines_1s"
    return f"SELECT COUNT(*) FROM {table}"


def probe_day(
    spec: ProbeSpec,
    day: str,
    *,
    scratch_dir: Path,
    transport: HttpTransport = urllib_transport,
    with_sqlite: bool = True,
) -> ProbeResult:
    """한 칸의 하루치를 실제로 받아 실제로 넣어 보고 잰다."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    url = vision_url(spec, day)

    started = time.monotonic()
    resp = transport(url)
    download_s = time.monotonic() - started

    if resp.status == 404:
        return unavailable(spec, day, f"아카이브 없음(404) — {url}")
    if not resp.ok:
        return unavailable(spec, day, f"HTTP {resp.status}: {resp.text(120)}")

    zip_path, _, _ = cell_paths(spec, day, scratch_dir)
    zip_path.write_bytes(resp.body)
    download_bytes = zip_path.stat().st_size

    ingest_started = time.monotonic()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not names:
                return unavailable(spec, day, "아카이브가 비어 있다")
            info = zf.getinfo(names[0])
            raw_bytes = info.file_size
            with zf.open(names[0]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8")
                rows, gzip_bytes, sqlite_bytes = _ingest(
                    spec, day, text, scratch_dir=scratch_dir, with_sqlite=with_sqlite
                )
    except zipfile.BadZipFile as exc:
        return unavailable(spec, day, f"zip 파손: {exc}")
    ingest_s = time.monotonic() - ingest_started

    return ProbeResult(
        spec=spec,
        day=day,
        available=True,
        note="",
        rows=rows,
        download_bytes=download_bytes,
        raw_bytes=raw_bytes,
        gzip_bytes=gzip_bytes,
        sqlite_bytes=sqlite_bytes,
        download_s=download_s,
        ingest_s=ingest_s,
    )


def probe_all(
    specs: Sequence[ProbeSpec],
    day: str,
    *,
    scratch_dir: Path,
    transport: HttpTransport = urllib_transport,
    with_sqlite: bool = True,
    keep: bool = False,
) -> list[ProbeResult]:
    """격자 전부를 재고, 기본적으로 스크래치를 **지운다**(§0은 잰 파일을 남길 이유가 없다)."""
    # 🚨 준 디렉터리를 통째로 지우지 않는다 — `--scratch`에 공용 경로를 주면 남의 파일을
    # 날린다. 우리 이름의 하위 디렉터리를 만들고 **그것만** 다룬다.
    work = scratch_dir / SCRATCH_DIRNAME
    work.mkdir(parents=True, exist_ok=True)

    results: list[ProbeResult] = []
    for spec in specs:
        results.append(
            probe_day(
                spec,
                day,
                scratch_dir=work,
                transport=transport,
                with_sqlite=with_sqlite,
            )
        )
        if not keep:
            # 🚨 칸마다 바로 지운다 — 12종목을 끝까지 쌓아 두면 1GB 박스에서 GB 단위로
            # 붇는다(BTC 한 칸만 zip 24MB + SQLite 196MB). 크기는 이미 쟀으니 파일은
            # 더 필요 없다.
            for path in cell_paths(spec, day, work):
                path.unlink(missing_ok=True)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return results


# ---------------------------------------------------------------------------
# 환산 — 「12종목이면 하루에 얼마나 느나」
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Projection:
    """한 (시장, 자료) 축의 유니버스 환산.

    🚨 **평균×N은 추정이고 min/max 띠가 그 추정의 폭이다** — 체결내역은 종목별로 자릿수가
    갈리므로(가장 활발 ↔ 가장 한산) 평균 하나로 인용하면 틀린다. 정확한 값이 필요하면
    `--symbols`로 12종목을 그냥 다 재면 된다(아카이브는 종목당 요청 한 번이다).
    """

    market: Market
    source: Source
    measured_symbols: int
    universe: int
    measured_sqlite_bytes: int
    measured_download_bytes: int
    per_symbol_min_sqlite: int
    per_symbol_max_sqlite: int

    @property
    def projected_daily_sqlite(self) -> float:
        if not self.measured_symbols:
            return 0.0
        return self.measured_sqlite_bytes / self.measured_symbols * self.universe

    @property
    def projected_daily_low(self) -> float:
        return float(self.per_symbol_min_sqlite * self.universe)

    @property
    def projected_daily_high(self) -> float:
        return float(self.per_symbol_max_sqlite * self.universe)

    @property
    def projected_yearly_sqlite(self) -> float:
        return self.projected_daily_sqlite * 365.0


def project(results: Sequence[ProbeResult], *, universe: int) -> list[Projection]:
    """(시장, 자료)별로 묶어 유니버스 환산을 낸다. 없는 칸은 환산하지 않는다."""
    buckets: dict[tuple[Market, Source], list[ProbeResult]] = {}
    for res in results:
        if not res.available:
            continue
        buckets.setdefault((res.spec.market, res.spec.source), []).append(res)
    out: list[Projection] = []
    for (market, source), items in buckets.items():
        sizes = [r.sqlite_bytes for r in items]
        out.append(
            Projection(
                market=market,
                source=source,
                measured_symbols=len(items),
                universe=universe,
                measured_sqlite_bytes=sum(sizes),
                measured_download_bytes=sum(r.download_bytes for r in items),
                per_symbol_min_sqlite=min(sizes) if sizes else 0,
                per_symbol_max_sqlite=max(sizes) if sizes else 0,
            )
        )
    return out


#: 이슈가 물은 두 자료 — 하나라도 못 재면 표가 반쪽이라 판정이 성립하지 않는다.
REQUIRED_KINDS: frozenset[tuple[Market, Source]] = frozenset(
    {("future", "agg_trades"), ("spot", "klines_1s")}
)


def measured_required_kinds(results: Sequence[ProbeResult]) -> bool:
    """이슈가 물은 두 자료를 **둘 다** 실제로 쟀는가.

    거짓이면 CLI가 종료 코드 1을 낸다 — 반쪽 표를 「성공」으로 내보내면 그게 이 저장소가
    반복해 경계한 「실패가 성공과 같은 모양」이다(WAN-194/318/321).
    """
    measured = {(r.spec.market, r.spec.source) for r in results if r.available}
    return measured >= REQUIRED_KINDS


def days_until_full(free_bytes: int, daily_bytes: float) -> float | None:
    """이 증가 속도로 남은 디스크가 며칠 버티나. 증가가 0이면 `None`."""
    if daily_bytes <= 0:
        return None
    return free_bytes / daily_bytes
