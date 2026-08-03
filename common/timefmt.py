"""운영 출력용 시각 포맷터 — **한국시간(KST) 고정** (WAN-172).

사용자 결정(2026-07-22): *"이거 전부 다 한국시간으로 해주면 안돼?"* → *"무조건
한국시간"*. 폰으로 텔레그램 경고를 받는 운영자가 볼 때마다 9시간을 더하지 않도록,
사람이 읽는 모든 출력(=`alphablock status`·텔레그램 경고·러너 로그·`fill_report`·
대시보드)의 시각을 KST로 통일한다.

## 이 모듈이 여기(`common/`)에 있는 이유

시각 포맷이 두 벌로 갈라지면 **같은 사건이 화면과 로그에서 다른 시각으로 보인다**.
KST 포맷은 원래 대시보드(`backtest.report.format_time_kst`, WAN-146)에만 있었고
`backtest` 레이어에 묶여 있어 `cli`/`data`/`live`가 가져다 쓸 수 없었다 — 그래서
각자 UTC로 찍고 있었다. 레이어 독립 패키지인 `common/`으로 올려 **한 함수**를
공유한다(`common/__init__.py`의 레이어 규칙 참고).

## ⚠️ 표시 계층 전용 — 저장·계산은 UTC(epoch ms) 그대로다

시간대 변환은 **문자열을 만드는 순간에만** 일어난다. 신선도 판정·워터마크·DB·
백테스트 어디에도 로컬 시각을 넣지 않는다 — 로컬 시각을 저장하면 서버 이전·
서머타임에서 값이 꼬이고 백테스트 재현이 깨진다. 백테스트 CSV의 UTC 열
(`backtest.report`의 `진입시각(UTC)` 등)도 **UTC 그대로 유지**한다(WAN-106 병기 규약).

시간대는 **설정값이 아니라 상수**다(사용자가 KST 고정을 명시). 설정으로 빼면
"로그는 KST인데 경고는 UTC" 같은 어긋남이 다시 생긴다.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

__all__ = [
    "KST",
    "KST_LABEL",
    "MISSING_TIME",
    "format_kst",
    "format_kst_zoned",
    "format_utc",
    "kst_day_bounds",
    "kst_day_bounds_for_date",
    "kst_day_key",
    "kst_log_format",
    "kst_time_converter",
    "use_kst_logging",
]

#: 운영 출력 시간대. 고정값이다(위 독스트링 참고).
KST = ZoneInfo("Asia/Seoul")

#: 시각 뒤에 붙이는 시간대 표기. "22:06"만 찍으면 어느 시간대인지 알 수 없다.
KST_LABEL = "KST"

#: 시각이 없을 때 찍는 자리표시자(`alphablock status`·대시보드가 함께 쓴다).
MISSING_TIME = "—"

_FMT_MINUTES = "%Y-%m-%d %H:%M"
_FMT_SECONDS = "%Y-%m-%d %H:%M:%S"


def format_kst(ms: int | None, *, seconds: bool = False, missing: str = MISSING_TIME) -> str:
    """epoch ms → `YYYY-MM-DD HH:MM`(KST). 시간대 표기는 붙이지 않는다.

    이미 열 이름이나 안내 문구가 KST임을 밝히는 자리(표 컬럼 등)에서 쓴다. 시각이
    단독으로 나가는 자리에는 `format_kst_zoned`를 쓸 것.
    """
    if ms is None:
        return missing
    return datetime.fromtimestamp(ms / 1000, tz=KST).strftime(
        _FMT_SECONDS if seconds else _FMT_MINUTES
    )


def format_kst_zoned(ms: int | None, *, seconds: bool = False, missing: str = MISSING_TIME) -> str:
    """epoch ms → `YYYY-MM-DD HH:MM KST`. 시간대 표기가 붙는다.

    로그·텔레그램·CLI처럼 시각이 문장 속에 단독으로 나가는 자리의 기본형이다.
    """
    if ms is None:
        return missing
    return f"{format_kst(ms, seconds=seconds)} {KST_LABEL}"


def format_utc(ms: int | None, *, seconds: bool = False, missing: str = MISSING_TIME) -> str:
    """epoch ms → `YYYY-MM-DD HH:MM`(UTC). **데이터 열 전용**.

    백테스트 CSV의 UTC 병기 열처럼 "KST 전환이 건드리면 안 되는" 자리에서만 쓴다.
    사람이 읽는 운영 출력에는 쓰지 않는다.
    """
    if ms is None:
        return missing
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime(
        _FMT_SECONDS if seconds else _FMT_MINUTES
    )


def _kst_day_bounds_from_midnight(start: datetime) -> tuple[int, int]:
    """KST 자정 datetime → `[자정, 다음 자정)` epoch ms 경계.

    `kst_day_bounds`(ms 기준)와 `kst_day_bounds_for_date`(달력 날짜 기준)가 **같은 식**을
    공유하도록 하나로 뺐다 — 두 진입점이 다른 산식을 쓰면 "당일" 창이 갈라진다(WAN-232가
    조회 창을 일일 요약 funnel과 비트 단위로 맞춰야 하는 이유). KST는 서머타임이 없어 하루가
    정확히 24h지만, 경계는 `timedelta`로 잡아 상수(86_400_000)를 박지 않는다.
    """
    start_ms = int(start.timestamp() * 1000)
    end_ms = int((start + timedelta(days=1)).timestamp() * 1000)
    return start_ms, end_ms


def kst_day_bounds(ms: int) -> tuple[int, int]:
    """epoch ms가 속한 **KST 하루**의 `[자정, 다음 자정)` 경계를 epoch ms로 돌려준다.

    반환값은 여전히 **UTC epoch(ms)** 이다 — 시간대 변환이 아니라 "이 순간이 어느 KST
    날짜에 속하는가"의 경계를 잡는 계산이다(저장·비교는 UTC epoch 불변, 위 독스트링).
    일일 손실 서킷브레이커(WAN-38)·일일 요약(WAN-221)처럼 사람이 체감하는 "당일" 경계가
    KST일 때 쓴다.
    """
    local = datetime.fromtimestamp(ms / 1000, tz=KST)
    return _kst_day_bounds_from_midnight(local.replace(hour=0, minute=0, second=0, microsecond=0))


def kst_day_bounds_for_date(day: date) -> tuple[int, int]:
    """KST **달력 날짜**의 `[자정, 다음 자정)` 경계를 epoch ms로 돌려준다.

    `kst_day_bounds(ms)`와 **같은 산식**(`_kst_day_bounds_from_midnight` 공유)을 쓰되,
    입력이 epoch ms가 아니라 달력 날짜다 — 일일 요약(WAN-221)·당일 주문별 조회(WAN-232)처럼
    "이 날짜의 하루"를 창으로 잡을 때 쓴다. 그래서 두 도구가 같은 날에 대해 **비트 단위로
    같은 창**을 본다(회귀 테스트가 고정).
    """
    return _kst_day_bounds_from_midnight(datetime(day.year, day.month, day.day, tzinfo=KST))


def kst_day_key(ms: int) -> str:
    """epoch ms → KST 날짜 키 `YYYY-MM-DD`(일일 카운터 롤오버 판정용, WAN-38)."""
    return datetime.fromtimestamp(ms / 1000, tz=KST).strftime("%Y-%m-%d")


def kst_time_converter(secs: float | None) -> time.struct_time:
    """`logging.Formatter.converter`용 변환기 — 로그 `%(asctime)s`를 KST로 찍는다.

    표준 `logging`은 기본이 **머신 로컬 시간**이라, 서버(UTC)와 노트북(KST)에서
    같은 사건의 로그 시각이 달라진다(WAN-174 리눅스 서버 이전). 이 변환기를 물리면
    어디서 돌든 KST다.
    """
    epoch = time.time() if secs is None else secs
    return datetime.fromtimestamp(epoch, tz=KST).timetuple()


def kst_log_format(fmt: str | None = None) -> str:
    """로그 포맷 문자열에 시간대 표기를 넣어 돌려준다(기본 러너·CLI 포맷)."""
    if fmt is not None:
        return fmt
    return f"%(asctime)s {KST_LABEL} %(levelname)s %(name)s: %(message)s"


def use_kst_logging() -> None:
    """이후 만들어지는 모든 `logging` 포매터의 `asctime`을 KST로 고정한다.

    `logging.basicConfig` **앞뒤 어디서 불러도** 동작한다(`Formatter.converter`는
    클래스 속성이라 이미 만들어진 포매터에도 적용된다).
    """
    # ⚠️ `staticmethod`로 감싸는 것이 핵심이다 — `converter`는 **클래스 속성**이고
    # `logging`은 `self.converter(record.created)`로 부른다. 순수 파이썬 함수를 그대로
    # 넣으면 디스크립터라 `self`가 첫 인자로 묶여 TypeError가 난다(기본값
    # `time.localtime`은 빌트인이라 안 묶인다). 회귀 테스트가 동작으로 고정한다.
    logging.Formatter.converter = staticmethod(kst_time_converter)
