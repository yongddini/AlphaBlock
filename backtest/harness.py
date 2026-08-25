"""범용 백테스트 하네스 — 실행기가 공유하는 골격 (WAN-101).

`backtest/wan68_*.py` … `wan99_*.py` 12개 스크립트는 저마다 같은 일을 다시 짰다:
저장소에서 최근 N년을 로드하고, 1분봉·펀딩비를 붙이고, 파라미터를 조립하고, 지정가(B안)
엔진에 태우고, 지표를 행으로 모아 표로 렌더한다. 그 반복이 "익절 1.5R 말고
2R이면?" 같은 한 줄짜리 질문에도 새 스크립트 + PR을 요구하게 만들었다. 이 모듈은 그
공통 골격을 한곳에 모아 `backtest.run`(범용 CLI)이 파라미터만 받아 즉시 답을 내게 한다.

## 이 모듈이 책임지는 것

1. **데이터 로딩** — `load_market_data`가 (심볼, TF)의 상위TF·1분봉·펀딩비를 한 번에
   묶어 `MarketData`로 낸다. 심볼 축약형(`BTCUSDT`)도 저장소 표기(`BTC/USDT:USDT`)로
   정규화한다.
2. **파라미터 조립** — `build_params`가 지정가(B안)에 맞는 `rsi_mode`(`realtime`)를
   **한 세트로** 묶는다(WAN-41).
3. **실행** — `run_once`가 지정가(B안) 엔진(`run_zone_limit_backtest_verbose`, 다중
   포지션이면 `run_zone_limit_portfolio_backtest`)을 태운다. A안(종가 진입)은
   WAN-208/WAN-215로 제거됐다.
4. **구간 분할** — `segments_for` / `slice_window`가 IS/OOS·워크포워드 창을 **시간으로**
   가른다. 각 구간은 초기자본에서 새로 시작하는 독립 백테스트다(WAN-99와 동일 규칙).
5. **렌더** — `render_table` / `render_csv` / `render_json`.

## 이 모듈이 하지 않는 것

해석과 권고는 하지 않는다. 리포트 모듈(`wanNN_*.py`)이 결론 문장을 쓰는 곳이고, 여기는
**숫자를 내는 곳**이다. 기본값도 바꾸지 않는다 — `build_params()`에 아무것도 주지
않으면 채택 기본값(`ConfluenceParams()`)이 그대로 나온다.
"""

from __future__ import annotations

import enum
import json
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest.models import BacktestConfig, BacktestResult, ExitReason
from backtest.portfolio import PortfolioParams
from backtest.sweep import default_backtest_config
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    ZoneLimitStats,
    run_zone_limit_backtest_verbose,
    run_zone_limit_portfolio_backtest,
)
from common.costs import Liquidity
from config.settings import get_settings
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import (
    BandBar,
    ConfluenceParams,
    InvalidationCancel,
    OrderBlockParams,
    OrderBlockResult,
    RsiGateMode,
)
from strategy.order_blocks import OrderBlockDetector

# --------------------------------------------------------------------------- #
# 기본값 — 채택 좌표 (WAN-182 = WAN-179 결정 실행)
# --------------------------------------------------------------------------- #

#: 채택 유니버스 = 12종목 (WAN-307 = 사용자 결정 2026-08-14 「12로 하자」 —
#: WAN-182의 9종목(기존 6종목(WAN-111) + DOGE·LINK·LTC(WAN-175/176))에 **ADV 상위 3**
#: (ADA·DOT·BCH)을 더했다). 합류 순서는 자유 파라미터가 아니라 **동결된 유동성 규칙**이다
#: (못 박은 창의 일중 달러 거래대금 내림차순, `wan300_universe_size.CANDIDATE_SYMBOLS` 상위
#: 3 = `universe_symbols(12)`) — WAN-304 사다리에서 잰 12-유니버스와 정확히 같은 집합.
#: 근거는 WAN-304의 렌즈별 MDD(12종목은 baseline↔pen_5bp에 +0.1%p로 거의 안 흔들리는데
#: 15종목은 +4.3%p 튄다)와 밀림율(12는 4.24%로 5% 포화선 아래)이다.
#:
#: **수집·측정 대상**일 뿐 실거래 대상이 아니다(`ALPHABLOCK_LIVE_TRADING=false` 불변 —
#: WAN-111 원칙). 순서는 「기존 → 신규」로 고정한다(wan176과 동일 — leave-one-out 표에서
#: 합류 세대가 어디부터인지 눈으로 갈리게).
#:
#: 펀딩: 12종목 전부 **자기 확정 펀딩 데이터가 채택 창을 덮는다**(DOGE·LINK·LTC는 WAN-292
#: 백필, ADA·DOT·BCH는 상장 초기부터 수집분 존재 — 2026-08-14 실측). WAN-180의 대리 규칙
#: (`apply_funding_proxy`)은 존치하되 데이터가 있으면 자동 무동작이다.
DEFAULT_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
    "ADA/USDT:USDT",
    "DOT/USDT:USDT",
    "BCH/USDT:USDT",
)

#: 작업 TF = 15m·1h·2h·4h (WAN-252 — WAN-182의 15m·1h·4h에 2h를 승격, 사용자 결정
#: 2026-08-05). WAN-182가 4h를 승격한 것과 **같은 패턴의 좌표 재-베이스라인**이다 —
#: 엔진(`ConfluenceParams()`)이 아니라 좌표만 바뀐다. 근거는 WAN-236(2h 성능)·WAN-243
#: (2h 재진입 널)이 이미 낸 표이고 새 격자는 없다. 2h는 저장된 1h에서 무손실 리샘플로
#: 파생된다(`OhlcvStore.load`의 `_DERIVED_TIMEFRAMES`, WAN-24 — 사전 적재 불필요).
#: 1d는 표본 미달(OOS 심볼당 ~10거래 < WAN-84 유효 기준 20건)로 제외 유지.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "2h", "4h")

#: 채택 창 = 못 박은 6년 (WAN-182, 실효 5.85년). 시작점은 SOL 상장(2020-09-14) 하한 —
#: 9종목이 **같은 창**을 보게 하는 균일 창 원칙(WAN-175/176). 끝은 마지막 미완 하루를
#: 잘라낸 2026-07-22 00:00 UTC. `--years N`(마지막 봉 기준 미끄러지는 창)과 달리 새 봉이
#: 쌓여도 움직이지 않는다 — 옛 리포트가 비트 단위로 재현되지 않던 원인(CLAUDE.md)의 반대.
DEFAULT_START: str = "2020-09-15"
DEFAULT_END: str = "2026-07-22"

#: `--years`를 명시한 실행의 기본 폭. **CLI 기본 창이 아니다** — 인자 없는 실행은
#: `DEFAULT_START`~`DEFAULT_END` 못 박은 창을 쓴다(WAN-182).
DEFAULT_YEARS: float = 3.0

#: WAN-101~WAN-181 범용 CLI·리포트의 기본 좌표 — 3심볼 × 1h × 최근 3년(미끄러지는 창).
#:
#: **WAN-182가 기본 좌표를 9종목 × 15m·1h·4h × 못 박은 6년 창으로 옮겼다.** 그 이전
#: 수치를 결론 문장에 박아 둔 리포트는 이 값을 **명시 고정**해 당시 좌표의 기록으로
#: 보존한다 — 고정하지 않으면 기본값을 따라 조용히 9종목/6년으로 다시 돌아 본문과
#: 어긋난다(WAN-91/95/112 부류, `LEGACY_RSI_GATE_MODE` 문단과 같은 원칙). 고정 대상
#: 목록은 [`docs/decisions/wan182.md`](../docs/decisions/wan182.md) §파급이다.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan95) — 기본값이
#: 움직이면 그 수치는 낡은 것이 되어야 맞다.
#: ⚠️ 대부분의 옛 리포트 모듈은 좌표를 **자기 모듈 상수로** 들고 있어(wan70/81/84/88 등)
#: 이 전환의 영향을 받지 않는다 — 고정이 필요한 것은 harness 기본값을 직접 읽던
#: 모듈(wan104/wan108)뿐이다.
LEGACY_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
LEGACY_TIMEFRAMES: tuple[str, ...] = ("1h",)
LEGACY_YEARS: float = 3.0

#: WAN-182~WAN-306 시절의 채택 유니버스 = 9종목. **WAN-307이 기본 좌표를 12종목으로
#: 옮겼다** — 결론 문장을 9종목 수치(CSV·요약 md)에 박아 둔 리포트는 이 값을 **명시
#: 고정**해 당시 좌표의 기록으로 보존한다(위 `LEGACY_SYMBOLS`(3심볼)와 같은 원칙, WAN-182
#: 파급 패턴). 고정하지 않으면 `harness.DEFAULT_SYMBOLS`를 알리아스하던 리포트가 조용히
#: 12종목으로 다시 돌아 본문과 어긋난다(WAN-91/95/112 부류의 조용한 실패).
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan95 · 범용 CLI) —
#: 기본값이 움직이면 그 수치는 낡은 것이 되어야 맞다. 고정 대상 목록은
#: [`docs/decisions/wan307.md`](../docs/decisions/wan307.md) §파급이다.
LEGACY_NINE_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
)
DB_PATH = "data/ohlcv.db"
CACHE_DIR = "data/cache"


def default_jobs() -> int:
    """`--jobs`를 명시하지 않았을 때 쓸 병렬 워커 수(WAN-294).

    `Settings.backtest_jobs`(env `ALPHABLOCK_BACKTEST_JOBS`, 기본 4)를 읽는다. 리터럴을
    박지 않는 이유는 코어 수가 다른 리눅스 서버(WAN-174)에서 환경변수로 덮기 위해서다.
    `--jobs`는 결과를 안 바꾸는 순수 성능 노브이므로(WAN-121: 직렬=병렬 비트 동일) 이
    기본값을 CLI·리포트가 공유해도 측정값·재현성은 불변이다.
    """
    return get_settings().backtest_jobs


_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

#: IS 비중 — 앞 2/3에서 고르고 뒤 1/3(OOS)로 검증한다(WAN-99와 동일).
IS_FRACTION = 2.0 / 3.0

SEGMENT_FULL = "full"
SEGMENT_IS = "is"
SEGMENT_OOS = "oos"
SEGMENT_OOS_WARM = "oos_warm"
"""따뜻한 연속 OOS(WAN-166) — 전 구간을 연속으로 돌려 존·지표를 데운 뒤, OOS 경계
이후에 **탭이 발생한** 셋업만 신선한 초기자본으로 평가한다. 차가운 `oos`(물리적 절단,
존 재고 0에서 시작)와 같은 기간을 재되 실전(연속 차트)에 맞는 현실적 추정이다."""


# --------------------------------------------------------------------------- #
# 심볼 정규화
# --------------------------------------------------------------------------- #

#: 축약형 심볼의 견적통화 후보. 긴 것부터 봐야 `USDT`가 `USD`로 잘리지 않는다.
_QUOTES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "USD")


def normalize_symbol(symbol: str) -> str:
    """`BTCUSDT` 같은 축약형을 저장소 표기(`BTC/USDT:USDT`)로 바꾼다.

    이미 `/`가 있으면 사용자가 정식 표기를 쓴 것으로 보고 그대로 둔다(`BTC/USDT`처럼
    무기한선물 접미사가 없으면 붙여준다). 이 정규화가 없으면 CLI가 `--symbol BTCUSDT`를
    빈 결과로 조용히 넘겨, 사용자는 "데이터가 없다"와 "표기를 틀렸다"를 구분할 수 없다.
    """
    text = symbol.strip().upper()
    if not text:
        raise ValueError("빈 심볼입니다.")
    if "/" in text:
        return text if ":" in text else f"{text}:{text.split('/')[1]}"
    for quote in _QUOTES:
        if text.endswith(quote) and len(text) > len(quote):
            return f"{text[: -len(quote)]}/{quote}:{quote}"
    raise ValueError(
        f"심볼 표기를 알 수 없습니다: {symbol!r}. "
        "`BTC/USDT:USDT`(정식) 또는 `BTCUSDT`(축약) 형태로 주세요."
    )


# --------------------------------------------------------------------------- #
# 체결 가정 프리셋 (WAN-96/99 축 재사용)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FillPreset:
    """체결 가정 한 단계 (WAN-96 `ConservatismLevel`·WAN-99 `FillAssumption`과 동일 축).

    `baseline`이 채택 기본값(닿으면 체결)이고, 나머지는 그 위에 보수화만 얹는다 —
    전략 파라미터는 건드리지 않는다.
    """

    name: str
    penetration_bps: float = 0.0
    dropout_rate: float = 0.0
    seeds: tuple[int, ...] = (0,)
    """탈락 추첨 시드들. 탈락이 있는 가정만 여러 개를 돌려 단일 시드의 운을 배제한다."""
    note: str = ""


#: CLI `--fill`이 받는 프리셋. 값은 WAN-96 `CONSERVATISM_LEVELS`와 일대일로 맞춘다
#: (`tests/test_harness.py`가 그 일치를 고정한다) — 같은 이름이 리포트와 CLI에서 다른
#: 뜻이면 두 결과를 나란히 놓고 비교할 수 없기 때문이다.
FILL_PRESETS: tuple[FillPreset, ...] = (
    FillPreset(name="baseline", note="닿으면 체결 — 채택 기본값(WAN-95)"),
    FillPreset(name="pen_1bp", penetration_bps=1.0, note="지정가 1bp 관통 요구"),
    FillPreset(name="pen_5bp", penetration_bps=5.0, note="지정가 5bp 관통 요구"),
    FillPreset(name="drop_25", dropout_rate=0.25, seeds=(0, 1, 2, 3, 4), note="체결 25% 탈락"),
    FillPreset(name="drop_50", dropout_rate=0.5, seeds=(0, 1, 2, 3, 4), note="체결 50% 탈락"),
    FillPreset(
        name="pen_5bp_drop_50",
        penetration_bps=5.0,
        dropout_rate=0.5,
        seeds=(0, 1, 2, 3, 4),
        note="관통 5bp + 50% 탈락 — WAN-96 최악 가정",
    ),
)

FILL_PRESETS_BY_NAME: dict[str, FillPreset] = {p.name: p for p in FILL_PRESETS}

#: 공식 체결 렌즈(WAN-104) = 유일한 렌즈다. **신규 리포트는 이것 단독으로 낸다(WAN-128)** —
#: `pen_5bp`·`pen_5bp_drop_50` 3렌즈 병기 요구는 WAN-128(사용자 재-베이스라인)이 폐지했다.
#: 두 민감도 렌즈는 삭제가 아니라 **옵트인**이라 `FILL_PRESETS`·CLI `--fill`에 그대로 남는다
#: (수동 확인용). 기본 실행이 `baseline` 단독인 것은 `build_params`의 `fill=BASELINE_FILL`
#: 기본 인자와 `run._fills_from_args`의 무인자 분기(`--fill` 없으면 `(BASELINE_FILL,)`)가
#: 강제한다 — 3렌즈를 자동으로 뱉는 코드 경로는 없다(각 `wanNN_*.py`의 `LENS_NAMES` 3렌즈
#: 상수는 그 리포트만의 것이고 당시 기록으로 보존한다). 배경은 docs/decisions/wan128.md.
BASELINE_FILL = FILL_PRESETS[0]


def fill_preset(name: str) -> FillPreset:
    """이름으로 프리셋을 찾는다. 없으면 지원 목록과 함께 거부한다."""
    try:
        return FILL_PRESETS_BY_NAME[name]
    except KeyError as exc:
        supported = ", ".join(FILL_PRESETS_BY_NAME)
        raise ValueError(f"알 수 없는 체결 가정: {name!r} (지원: {supported})") from exc


# --------------------------------------------------------------------------- #
# 파라미터 조립
# --------------------------------------------------------------------------- #

#: `entry_mode` → 짝이 되는 `rsi_mode`. 두 필드는 **한 세트**다(WAN-41/95): 지정가는 봉
#: 중간에 체결되므로 확정봉 RSI를 쓰면 체결 시점과 판정 시점이 어긋난다. A안(종가 진입)이
#: WAN-208/WAN-215로 제거돼 지금은 지정가(B안) 단독이다.
_RSI_MODE_FOR_ENTRY: dict[str, str] = {"zone_limit": "realtime"}

ENTRY_MODES: tuple[str, ...] = tuple(_RSI_MODE_FOR_ENTRY)

#: 재탭 정책 축(WAN-138). `ConfluenceParams.retap_mode`의 `Literal`과 같은 값이라야
#: 격자가 pydantic 검증에 걸리기 전에 CLI에서 깔끔한 오류를 낸다.
RETAP_MODES: tuple[str, ...] = ("every_tap", "once")

#: WAN-81~WAN-122 채택 엔진의 RSI 게이트 — 첫 탭 면제 + 재탭 `extreme`(롱 `RSI<=30`).
#:
#: **WAN-123이 기본값을 `"unconditional"`(게이트 제거)로 옮겼다.** 그 이전 수치를 결론
#: 문장에 박아 둔 리포트는 이 값을 **명시 고정**해 당시 엔진의 기록으로 보존한다 —
#: 고정하지 않으면 기본값을 따라 새 게이트(거래 +13~14%)로 조용히 다시 돌아 본문과
#: 어긋난다. WAN-112가 `zone_limit_offset_bps=0.0`을 고정한 것과 같은 패턴이고, 고정
#: 대상 목록은 [`docs/decisions/wan123.md`](../docs/decisions/wan123.md) §파급이다.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan88·wan95) — 기본값이
#: 움직이면 그 수치는 낡은 것이 되어야 맞다.
LEGACY_RSI_GATE_MODE: RsiGateMode = "first_tap_free"

#: WAN-70~WAN-131 채택 엔진의 이격 밴드 표본 — 탭 봉 **최종 종가**(`band_bar="tap"`).
#:
#: **WAN-132가 기본값을 `"intrabar_live"`(봉내 라이브)로 옮겼다.** 그 이전 수치를 결론
#: 문장에 박아 둔 리포트는 이 값을 **명시 고정**해 당시 엔진의 기록으로 보존한다 —
#: `band_bar`는 `LEGACY_RSI_GATE_MODE`·`zone_limit_offset_bps`와 달리 **아무 리포트도
#: 고정해 두지 않았으므로**(WAN-120 §6-2 경고), 고정하지 않으면 기본값을 따라 조용히
#: 새 밴드로 다시 돌아 본문과 어긋난다. 고정 대상 목록은
#: [`docs/decisions/wan132.md`](../docs/decisions/wan132.md) §파급이다.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan95) — 기본값이
#: 움직이면 그 수치는 낡은 것이 되어야 맞다.
#: ⚠️ A안(`entry_mode="close"`)만 도는 리포트에는 **아무 효과가 없다** — 봉 단위에서
#: `intrabar_live`는 `tap`과 정확히 같은 값이라(`ConfluenceStrategy.deviation_band_at`)
#: 고정할 것도 움직일 것도 없다.
LEGACY_BAND_BAR: BandBar = "tap"

#: WAN-19~WAN-148 채택 엔진의 존 병합 — 겹치는 오더블록을 하나로 접음(`combine_obs=True`).
#:
#: **WAN-149가 기본값을 `False`(원본 존 단위 분리)로 옮겼다.** 그 이전 수치를 결론 문장에
#: 박아 둔 리포트는 이 값을 **명시 고정**해 당시 엔진의 기록으로 보존한다 — `combine_obs`는
#: `LEGACY_BAND_BAR`와 같은 부류라 **거의 아무 리포트도 고정해 두지 않았으므로**, 고정하지
#: 않으면 기본값을 따라 조용히 분리 엔진으로 다시 돌아 본문과 어긋난다. 그 어긋남은 이
#: 저장소가 반복해 당한 「바꿨다고 믿으면서 안 바뀐 것」의 거울상이다 — **「안 바꿨다고
#: 믿었는데 바뀐 것」**. 고정 대상 목록은
#: [`docs/decisions/wan149.md`](../docs/decisions/wan149.md) §2다.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan95) — 기본값이
#: 움직이면 그 수치는 낡은 것이 되어야 맞다.
#: ⚠️ **이미 이 축을 명시한 모듈은 손대지 않는다**(wan134/wan77/wan81) — 특히
#: `wan134_zone_merge_autopsy`는 `combine_obs`가 실험 변수라 고정하면 실험이 깨진다.
LEGACY_COMBINE_OBS: bool = True

#: 옛 리포트가 그대로 넘겨 쓰는 탐지 파라미터(병합 ON). `OrderBlockParams`는 frozen이라
#: 모듈 상수로 공유해도 안전하다.
LEGACY_OB_PARAMS = OrderBlockParams(combine_obs=LEGACY_COMBINE_OBS)


def pin_combine_obs(
    params: OrderBlockParams | None = None, *, combine_obs: bool = LEGACY_COMBINE_OBS
) -> OrderBlockParams:
    """탐지 파라미터의 `combine_obs`만 갈아끼운다(다른 필드는 손대지 않는다).

    `pin_band_bar`와 같은 자리다 — 리포트가 `OrderBlockParams(...)`를 아예 만들지 않고
    기본값에 맡기던 곳이 대부분이라, 고정 보일러플레이트를 각자 짜면 한 곳을 빠뜨리는
    것이 정확히 WAN-149 §2가 경고한 조용한 실패가 된다.
    """
    return (params or OrderBlockParams()).model_copy(update={"combine_obs": combine_obs})


def pin_band_bar(params: ConfluenceParams, band_bar: BandBar = LEGACY_BAND_BAR) -> ConfluenceParams:
    """`params`의 이격 밴드 표본 시점만 갈아끼운다(다른 필드는 손대지 않는다).

    `deviation_filter`가 꺼져 있으면(`None`) 밴드 자체가 없으므로 그대로 돌려준다.
    리포트가 `ConfluenceParams(...)` 한 줄로 조립될 때 중첩 모델 한 필드를 고정하려면
    `model_copy` 두 번이 필요한데, 그 보일러플레이트를 빠뜨리는 것이 정확히 WAN-120이
    경고한 조용한 실패라 여기 한 곳에 모은다.
    """
    deviation = params.deviation_filter
    if deviation is None:
        return params
    return params.model_copy(
        update={"deviation_filter": deviation.model_copy(update={"band_bar": band_bar})}
    )


#: WAN-158~WAN-158 채택 엔진의 존폭 필터 — **꺼짐**(`max_zone_width_atr=None`, 전부 매매).
#:
#: **WAN-159가 기본값을 `1.28`(좁은 존만 매매)로 옮겼다.** 그 이전 수치를 결론 문장에 박아
#: 둔 리포트는 이 값을 **명시 고정**해 당시 엔진의 기록으로 보존한다 — `combine_obs`·`band_bar`와
#: 같은 부류라 **거의 아무 리포트도 고정해 두지 않았으므로**, 고정하지 않으면 기본값을 따라
#: 조용히 필터 켜진 엔진으로 다시 돌아 본문과 어긋난다(「안 바꿨다고 믿었는데 바뀐 것」).
#: 고정 대상 목록은 [`docs/decisions/wan159.md`](../docs/decisions/wan159.md) §파급이다.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan95) — 기본값이 움직이면
#: 그 수치는 낡은 것이 되어야 맞다.
#: ⚠️ **이미 이 축을 명시한 모듈은 손대지 않는다**(wan155/wan161) — 문턱을 자기 입력으로
#: 명시(`1.28` 또는 `None`)하므로 기본값 전환과 무관하게 같은 행을 낸다.
#: 🚨 **wan133/142/152/154는 특히 빠뜨리면 안 된다** — 후보를 자기가 걸러 좁은 존 팔을
#: 만드는데 엔진이 먼저 1.28로 걸러 버리면 「이중 필터」가 되어 판정 근거표가 조용히 망가진다.
LEGACY_MAX_ZONE_WIDTH_ATR: float | None = None


def pin_zone_width(
    params: ConfluenceParams, threshold: float | None = LEGACY_MAX_ZONE_WIDTH_ATR
) -> ConfluenceParams:
    """`params`의 존폭 필터 문턱만 갈아끼운다(다른 필드는 손대지 않는다).

    `pin_band_bar`와 같은 자리다 — 리포트가 `ConfluenceParams(...)`를 기본값에 맡기던 곳이
    대부분이라, 고정 보일러플레이트를 각자 짜면 한 곳을 빠뜨리는 것이 정확히 WAN-159 §파급이
    경고한 「이중 필터」·「조용히 필터 켜진 엔진」이 된다. 기본값 `None`이 **끄기**다.
    """
    return params.model_copy(update={"max_zone_width_atr": threshold})


#: 무효화 취소 시점의 **옛 값** — WAN-365 전까지의 채택 동작(소급 취소, WAN-364가 이름 붙인
#: 룩어헤드). 채택 기본값은 `ConfluenceParams().invalidation_cancel`(= `"bar_close"` = 인과)다.
#:
#: 🚨 `invalidation_cancel`은 **WAN-364가 처음 만든 축이라 기존 리포트가 하나도 명시하지
#: 않았다** — `LEGACY_BAND_BAR`(WAN-132)·`LEGACY_COMBINE_OBS`(WAN-149)와 **글자 그대로 같은
#: 함정**이다. 고정하지 않으면 옛 결론 리포트가 전부 조용히 인과 엔진으로 다시 돌아 본문과
#: 어긋난다 — 「바꿨다고 믿으면서 안 바뀐 것」의 거울상인 **「안 바꿨다고 믿으면서 바뀐 것」**.
#: 그래서 옛 수치를 결론에 박아 둔 모듈은 `pin_invalidation_cancel`(또는 엔진 호출부의
#: `invalidation_cancel=` 인자)로 **명시 고정**한다. 목록은
#: [`docs/decisions/wan365.md`](../docs/decisions/wan365.md) §파급.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan95) — 기본값이 움직이면
#: 그 수치는 낡은 것이 되어야 맞다.
#: ⚠️ **이미 이 축을 팔로 명시한 모듈도 손대지 않는다**(wan364) — 두 값을 자기 입력으로
#: 넘기므로 기본값 전환과 무관하게 같은 행을 낸다.
LEGACY_INVALIDATION_CANCEL: InvalidationCancel = "bar_open"


def pin_invalidation_cancel(
    params: ConfluenceParams, mode: InvalidationCancel = LEGACY_INVALIDATION_CANCEL
) -> ConfluenceParams:
    """`params`의 무효화 취소 시점만 갈아끼운다(다른 필드는 손대지 않는다).

    `pin_band_bar`·`pin_zone_width`와 같은 자리다. 기본값 `"bar_open"`이 **옛 동작**(소급
    취소)이라, 옛 결론 CSV를 재현해야 하는 모듈이 이 한 줄로 그 시절 엔진에 고정된다.
    """
    return params.model_copy(update={"invalidation_cancel": mode})


class _Unset(enum.Enum):
    """`build_params(max_zone_width_atr=...)`의 「인자 미지정」 센티넬.

    존폭 필터는 **끄기 = 명시적 `None`**이라, `None`을 "손대지 않는다(채택 기본값에 맡긴다)"로
    쓰던 `offset_bps` 규약을 그대로 못 쓴다(둘이 충돌한다). 그래서 "미지정"을 이 센티넬로
    따로 표현한다 — `is UNSET`이면 `base`의 값을 물려받고(= 채택 기본값 = `1.28`), 명시적
    `None`이면 **끈다**. WAN-159 완료기준 3.

    ⚠️ **`enum.Enum`이라야 한다** — CLI 축(`Grid`)이 `--jobs` 병렬에서 워커로 **피클**되는데,
    평범한 `object()` 센티넬은 언피클 때 **새 인스턴스**가 되어 `!= (UNSET,)` 비교가 깨진다.
    Enum 멤버는 이름으로 피클돼 언피클 후에도 **같은 싱글턴**이라 `is`·`==`가 성립한다.
    """

    UNSET = "unset"


UNSET = _Unset.UNSET

#: 존폭 필터 인자의 타입 — 실제 문턱(`float`)·끄기(`None`)·미지정(`UNSET`). CLI 축이
#: 「미지정」을 「끄기」와 갈라 나르려고 쓴다(run.py의 `Grid`/`Combo`).
ZoneWidthArg = float | None | _Unset

#: 지정가 유효기간 인자의 타입 — 실제 봉 수(`int`)·무기한(`None`)·미지정(`UNSET`)(WAN-222).
#: 존폭 필터와 **같은 규약**이다: `None`이 "무기한 대기"라는 **유의미한 값**이라
#: (`offset_bps`처럼) "손대지 않는다"로 못 쓴다 — 그래서 미지정을 센티넬로 따로 나른다.
#: `is UNSET`이면 `base`의 값을 물려받고(= 채택 기본값 = `24`), 명시적 `None`이면 무기한이다.
LimitValidBarsArg = int | None | _Unset

#: 유동성 한도(ADV 비례 절대 명목 상한, `max_notional_adv_fraction`) 인자의 타입 —
#: 실제 프랙션(`float`)·끄기(`None`)·미지정(`UNSET`)(WAN-279). 존폭 필터·유효기간과 **같은
#: 규약**이다: `None`이 "끄기"라는 유의미한 값이라 "손대지 않는다"로 못 쓴다 — 미지정을
#: 센티넬로 따로 나른다. `is UNSET`이면 `build_config`가 `risk_sizing`을 손대지 않아 채택
#: 기본값(= `PositionSizingParams`의 `0.005`)을 물려받고, 명시적 `None`이면 **끈다**.
AdvCapArg = float | None | _Unset

#: WAN-244~WAN-278 북 측정 리포트가 상한을 **끈** 채 낸 수치(= `max_notional_adv_fraction=None`).
#:
#: **WAN-279가 기본값을 `0.005`(0.5% ADV, 유동성 한도)로 올렸다.** 그 이전 수치를 결론
#: 문장에 박아 둔 북 리포트(wan169/180/261/264/269/271 등)는 이 값을 **명시 고정**해 당시
#: 회계의 기록으로 보존한다 — `combine_obs`·`band_bar`·`max_zone_width_atr`과 같은 부류라
#: 고정하지 않으면 기본값을 따라 조용히 상한 켜진 북으로 다시 돌아 본문과 어긋난다
#: (「안 바꿨다고 믿었는데 바뀐 것」, WAN-91/95/112 부류). 이 저장소의 북 후보 생성은 전부
#: `wan169.run_cell`을 지나므로 그 한 곳이 상한을 `task.adv_fraction`(기본 `None`)으로 **항상
#: 명시 고정**해 고정을 중앙화한다 — 채택 북(`book_cli.run_book`)만 `UNSET`으로 옵트인한다.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다** — 기본값이 움직이면 그
#: 수치는 낡은 것이 되어야 맞다. per-cell 리포트(wan95)는 상한이 자본 규모상 사실상 발동하지
#: 않아(inert) 비트 재현된다 — 별도 고정이 필요 없다.
LEGACY_MAX_NOTIONAL_ADV_FRACTION: float | None = None


#: 익절 청산 유동성의 **옛 값** — WAN-370 전까지 이 저장소의 모든 백테스트가 쓴 값이다
#: (청산은 사유와 무관하게 테이커 4bp ＋ 슬리피지 5bp). 채택 기본값은
#: `BacktestConfig().take_profit_liquidity`(= `maker` = 2bp · 슬리피지 0)다.
#:
#: 🚨 `take_profit_liquidity`는 **WAN-370이 처음 만든 축이라 기존 리포트가 하나도 명시하지
#: 않았다** — `LEGACY_BAND_BAR`(WAN-132)·`LEGACY_COMBINE_OBS`(WAN-149)·
#: `LEGACY_INVALIDATION_CANCEL`(WAN-365)과 **글자 그대로 같은 함정**이다. 고정하지 않으면
#: 옛 결론 리포트가 전부 조용히 「익절 메이커」 엔진으로 다시 돌아 본문과 어긋난다
#: (「안 바꿨다고 믿으면서 바뀐 것」). 그래서 옛 수치를 결론에 박아 둔 모듈은
#: `legacy_build_config`(= 이 값으로 고정된 `build_config`)를 쓴다. 목록은
#: [`docs/decisions/wan370.md`](../docs/decisions/wan370.md) §파급.
#:
#: ⚠️ 반대로 **"지금 채택된 것"을 재는 리포트는 고정하지 않는다**(wan95·wan366/368) —
#: 기본값이 움직이면 그 수치는 낡은 것이 되어야 맞다.
LEGACY_TAKE_PROFIT_LIQUIDITY: Liquidity = Liquidity.TAKER

#: 채택 값(= 익절 지정가). 새 측정·대조 도구는 **인자를 안 주면** 이 값을 물려받는다
#: (WAN-305 원칙: 기본값이 곧 페이퍼가 뛰는 규칙). 명시가 필요할 때 쓰는 이름이다.
ADOPTED_TAKE_PROFIT_LIQUIDITY: Liquidity = Liquidity.MAKER


def build_params(
    *,
    entry_mode: str = "zone_limit",
    take_profit_r: float | None = None,
    offset_bps: float | None = None,
    fill: FillPreset = BASELINE_FILL,
    seed: int = 0,
    short_enabled: bool | None = None,
    retap_mode: str | None = None,
    max_zone_width_atr: float | None | _Unset = UNSET,
    limit_valid_bars: int | None | _Unset = UNSET,
    invalidation_cancel: InvalidationCancel | None = None,
    base: ConfluenceParams | None = None,
) -> ConfluenceParams:
    """CLI 인자를 `ConfluenceParams`로 조립한다.

    아무것도 주지 않으면 **채택 기본값 그대로**(`ConfluenceParams()`)를 반환한다 —
    그래야 CLI 기본 실행이 WAN-95/96/99 리포트의 기준선 셀과 같은 엔진을 탄다.

    ⚠️ `offset_bps=None`이 "채택 기본값에 맡긴다"는 뜻이고, 0.0은 **명시적 0bp 요청**이다.
    둘을 가르지 않고 0.0을 기본 인자로 두면 이 함수가 `ConfluenceParams`의 기본 오프셋을
    **말없이 덮어써서**, WAN-112가 기본값을 2bp로 올려도 CLI 기본 실행만 혼자 0bp로 도는
    조용한 갈라짐이 생긴다(= 위 첫 문단의 약속이 깨진다).

    ⚠️ **`max_zone_width_atr`은 규약이 반대다** — 끄기가 `None`이라 `offset_bps`처럼
    `None`을 "손대지 않는다"로 쓸 수 없다(둘이 충돌한다, WAN-159). "미지정"은 센티넬
    `UNSET`으로 표현한다: `UNSET`이면 `base`의 값을 물려받고(= 채택 기본값 = `1.28`),
    명시적 `None`이면 **끈다**(= `1.28`을 덮어써 `None`으로). 이래야 wan155/wan161·CLI의
    `none` 팔이 「필터 끔」 라벨을 단 채 조용히 1.28로 도는 이중 필터를 피한다.

    ⚠️ **`limit_valid_bars`도 같은 규약이다**(WAN-222) — 무기한이 `None`이라 미지정을
    센티넬 `UNSET`으로 나른다: `UNSET`이면 `base`의 값(= 채택 기본값 = `24`)을 물려받고,
    명시적 `None`이면 **무기한**(존 무효화까지 대기)이다. 미지정과 무기한을 안 가르면
    「유효기간 24」 실행에 `--limit-valid-bars none` 라벨이 붙는 조용한 실패가 생긴다.

    ⚠️ **`invalidation_cancel`은 `offset_bps` 규약이다**(WAN-365) — `None`(기본)이 "손대지
    않는다" = 채택 기본값(`"bar_close"` = 인과)이고, 값을 주면 그것으로 덮어쓴다. 옛 결론
    리포트는 `LEGACY_INVALIDATION_CANCEL`(= `"bar_open"` = 소급 취소)을 명시로 넘긴다.

    진입 방식은 지정가(B안) 단독이다(A안은 WAN-208/WAN-215로 제거). 알 수 없는
    `entry_mode`는 `ValueError`로 거부한다.
    """
    if entry_mode not in _RSI_MODE_FOR_ENTRY:
        supported = ", ".join(_RSI_MODE_FOR_ENTRY)
        raise ValueError(f"알 수 없는 진입 방식: {entry_mode!r} (지원: {supported})")

    update: dict[str, object] = {
        "entry_mode": entry_mode,
        "rsi_mode": _RSI_MODE_FOR_ENTRY[entry_mode],
        "fill_penetration_bps": fill.penetration_bps,
        "fill_dropout_rate": fill.dropout_rate,
        "fill_dropout_seed": seed,
    }
    if offset_bps is not None:
        update["zone_limit_offset_bps"] = offset_bps
    if max_zone_width_atr is not UNSET:
        # 명시적 `None`은 **끄기**다(채택 기본값 1.28을 덮어쓴다). `UNSET`이면
        # `base`(= 채택 기본값)의 값을 그대로 물려받는다 — 위 독스트링 규약.
        update["max_zone_width_atr"] = max_zone_width_atr
    if limit_valid_bars is not UNSET:
        # 명시적 `None`은 **무기한**이다(채택 기본값 24를 덮어쓴다). `UNSET`이면
        # `base`(= 채택 기본값 24)의 값을 그대로 물려받는다 — 위 독스트링 규약.
        update["limit_valid_bars"] = limit_valid_bars
    if invalidation_cancel is not None:
        update["invalidation_cancel"] = invalidation_cancel
    if take_profit_r is not None:
        update["take_profit_r"] = take_profit_r
    if short_enabled is not None:
        update["short_enabled"] = short_enabled
    if retap_mode is not None:
        if retap_mode not in RETAP_MODES:
            supported = ", ".join(RETAP_MODES)
            raise ValueError(f"알 수 없는 재탭 정책: {retap_mode!r} (지원: {supported})")
        update["retap_mode"] = retap_mode
    return (base or ConfluenceParams()).model_copy(update=update)


def build_config(
    timeframe: str,
    *,
    fee_rate: float | None = None,
    maker_fee_rate: float | None = None,
    slippage: float | None = None,
    take_profit_liquidity: Liquidity | None = None,
    funding_enabled: bool | None = None,
    max_notional_adv_fraction: AdvCapArg = UNSET,
    min_stop_distance_fraction: float | None = None,
    seed: int = 0,
) -> BacktestConfig:
    """공용 팩토리(`default_backtest_config`) 위에 비용·펀딩 오버라이드만 얹는다.

    `BacktestConfig()`를 직접 만들지 않는 이유는 WAN-65와 같다 — 그러면
    `settings.effective_risk_sizing`이 조용히 빠져 모든 진입이 자본 100%를 쓰는
    경로로 되돌아간다.

    ⚠️ **`max_notional_adv_fraction`은 존폭 필터·유효기간과 같은 센티넬 규약이다**(WAN-279) —
    끄기가 `None`이라 `None`을 "손대지 않는다"로 못 쓴다. `UNSET`(기본)이면 `risk_sizing`을
    손대지 않아 채택 기본값(= `PositionSizingParams`의 `0.005` = 유동성 한도 켜짐)을 물려받고,
    명시적 `None`이면 **끈다**(옛 상한-끔 북 리포트 재현), `float`이면 그 프랙션으로 고정한다.
    안 가르면 "상한 끔" 라벨을 단 채 조용히 0.005로 도는 이중 배선이 된다(WAN-91/95/112 부류).
    `risk_sizing`이 `None`(사이징 비활성)이면 상한 개념 자체가 없어 이 인자를 무시한다.

    `take_profit_liquidity`(익절 청산 유동성, WAN-370)는 `offset_bps` 규약이다 —
    `None`(기본)이 "손대지 않는다"라 **채택 기본값**(`maker` = 지정가 2bp · 슬리피지 0)을
    물려받고, 값을 주면 그것으로 고정한다. 옛 CSV를 재현해야 하는 모듈은 `build_config` 대신
    `legacy_build_config`를 쓴다(그쪽이 `LEGACY_TAKE_PROFIT_LIQUIDITY`를 항상 명시로 넘긴다).

    `min_stop_distance_fraction`(손절폭 가드, WAN-76/79 · WAN-366 옵트인)은 `offset_bps`
    규약이다 — `None`(기본)이 "손대지 않는다"라 채택 기본값(`0.003` = 0.3%)을 물려받고,
    값을 주면 그것으로 덮어쓴다(`0.0` = 가드 끔). ⚠️ **가드는 후보 생성이 아니라 사이징에
    걸리므로**(`execution.sizing.size_with_reason`의 `stop_too_tight`) 같은 후보를 가드만
    바꿔 다시 시퀀싱할 수 있다 — wan76 §3·WAN-197이 쓰던 성질이고, WAN-366 사다리의
    `L2→L3` 단이 후보 재생성 없이 서는 이유다.
    """
    cfg = default_backtest_config(timeframe, seed=seed)
    update: dict[str, object] = {}
    if fee_rate is not None:
        update["fee_rate"] = fee_rate
    if maker_fee_rate is not None:
        update["maker_fee_rate"] = maker_fee_rate
    if slippage is not None:
        update["slippage"] = slippage
    if take_profit_liquidity is not None:
        update["take_profit_liquidity"] = take_profit_liquidity
    if funding_enabled is not None:
        update["funding_enabled"] = funding_enabled
    sizing_update: dict[str, object] = {}
    if max_notional_adv_fraction is not UNSET:
        # 명시적 `None`은 **끄기**(채택 0.005를 덮어씀), `float`이면 그 프랙션으로 고정.
        sizing_update["max_notional_adv_fraction"] = max_notional_adv_fraction
    if min_stop_distance_fraction is not None:
        sizing_update["min_stop_distance_fraction"] = min_stop_distance_fraction
    if sizing_update and cfg.risk_sizing is not None:
        update["risk_sizing"] = cfg.risk_sizing.model_copy(update=sizing_update)
    return cfg.model_copy(update=update) if update else cfg


def legacy_build_config(
    timeframe: str,
    *,
    fee_rate: float | None = None,
    maker_fee_rate: float | None = None,
    slippage: float | None = None,
    funding_enabled: bool | None = None,
    max_notional_adv_fraction: AdvCapArg = UNSET,
    min_stop_distance_fraction: float | None = None,
    seed: int = 0,
) -> BacktestConfig:
    """`build_config`을 **WAN-370 이전 비용 회계**(익절도 테이커)에 고정해 부른다.

    옛 수치를 결론 문장에 박아 둔 리포트 모듈이 쓰는 한 줄짜리 핀이다 — 호출 형태는
    `build_config`과 글자 그대로 같고 `take_profit_liquidity`만 항상
    `LEGACY_TAKE_PROFIT_LIQUIDITY`로 명시한다. 이름 자체가 "이 표는 옛 회계의 기록"이라고
    호출부에서 읽히는 것이 요점이다(`pin_band_bar`·`pin_invalidation_cancel`과 같은 자리).
    그 축을 **인자로 받지 않는 것**도 의도다 — 받으면 "legacy"라는 이름을 달고 채택 회계로
    도는 호출이 생긴다(라벨과 동작이 어긋나는 자리, WAN-91/95/112/123/159).

    ⚠️ **새 모듈은 이걸 쓰지 않는다** — 새 측정·대조 도구의 기본값은 채택 규칙이어야 한다
    (WAN-305). 이 함수는 **이미 낸 표를 재현하기 위한 것**이다.
    """
    return build_config(
        timeframe,
        fee_rate=fee_rate,
        maker_fee_rate=maker_fee_rate,
        slippage=slippage,
        take_profit_liquidity=LEGACY_TAKE_PROFIT_LIQUIDITY,
        funding_enabled=funding_enabled,
        max_notional_adv_fraction=max_notional_adv_fraction,
        min_stop_distance_fraction=min_stop_distance_fraction,
        seed=seed,
    )


# --------------------------------------------------------------------------- #
# 데이터 로딩
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MarketData:
    """한 (심볼, TF)의 백테스트 입력 묶음."""

    symbol: str
    timeframe: str
    htf_df: pd.DataFrame
    df_1m: pd.DataFrame
    """1분봉. 지정가(B안) 평가에만 필요하다. 종가 전용 실행이면 비어 있다."""
    funding_rates: list[FundingRate]

    @property
    def empty(self) -> bool:
        return bool(self.htf_df.empty)

    @property
    def start_ms(self) -> int:
        return int(self.htf_df["open_time"].iloc[0])

    @property
    def end_ms(self) -> int:
        return int(self.htf_df["open_time"].iloc[-1])


def load_recent(store: OhlcvStore, symbol: str, timeframe: str, years: float) -> pd.DataFrame:
    """심볼×TF 전체를 로드해 마지막 시각 기준 최근 `years`년만 남긴다."""
    df = store.load(symbol, timeframe)
    if df.empty:
        return df
    last = int(df["open_time"].iloc[-1])
    start = last - int(years * _YEAR_MS)
    return df[df["open_time"] >= start].reset_index(drop=True)


def split_bars(n: int, is_frac: float = 2.0 / 3.0) -> int:
    """`n`봉을 IS `[0, is_end)`/OOS `[is_end, n)`으로 나눈다(단순 분할, 구 WAN-68).

    구 `wan68_short_gate_analysis._split_bars`에서 옮겨 온 순수 헬퍼(WAN-198). 여러
    엣지 널 리포트(wan70/71/88)가 인덱스 기준 IS/OOS 분할에 이 함수를 공유한다 —
    아카이브 모듈에 갇혀 있던 것을 중립 골격으로 끌어올렸다(동작·비트 재현 불변).
    """
    return max(1, min(n - 1, int(round(n * is_frac))))


def load_market_data(
    symbol: str,
    timeframe: str,
    *,
    years: float = DEFAULT_YEARS,
    start_ms: int | None = None,
    end_ms: int | None = None,
    need_1m: bool = True,
    funding: bool = True,
    db_path: str = DB_PATH,
    cache_dir: str = CACHE_DIR,
    repair_htf_from_1m: bool = False,
) -> MarketData:
    """(심볼, TF)의 상위TF·1분봉·펀딩비를 한 번에 로드한다.

    `start_ms`/`end_ms`를 주면 그 창으로, 아니면 최근 `years`년으로 자른다. `need_1m`이
    False면 1분봉을 아예 읽지 않는다 — 종가 전용 실행에서 수백 MB를 읽지 않기 위해서다.

    데이터가 없으면 빈 `MarketData`를 반환한다(예외를 던지지 않는다) — 격자 실행에서
    심볼 하나가 없다고 전체가 죽으면 안 되므로, 호출부가 `empty`를 보고 건너뛴다.

    `repair_htf_from_1m`(WAN-327, **옵트인**)을 켜면 저장 상위TF 봉 중 **손상된 봉만**
    그 구간 1분봉 합으로 갈아끼운 사본을 쓴다(`data.partial_bars.repair_frame`). ⚠️ **DB는
    쓰지 않는다** — 「고치기 전후로 같은 좌표를 돌려 본다」(완료기준 2)를 위한 비파괴
    반사실이고, 실제 수정은 사람이 하는 거래소 재수집이다(WAN-194 원칙). 끄면(기본)
    예전과 **비트 단위로 같다** — 켜지 않으면 리샘플을 계산조차 하지 않는다. 거래량
    노이즈 봉은 손대지 않는다(저장이 정본에 더 가깝다 — 그 판정은 `partial_bars` 소관).
    """
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    if start_ms is not None or end_ms is not None:
        htf_df = store.load(symbol, timeframe, start_ms=start_ms, end_ms=end_ms).reset_index(
            drop=True
        )
    else:
        htf_df = load_recent(store, symbol, timeframe, years)
    if htf_df.empty:
        return MarketData(symbol, timeframe, htf_df, pd.DataFrame(), [])

    window_start = int(htf_df["open_time"].iloc[0])
    window_end = int(htf_df["open_time"].iloc[-1])

    df_1m = pd.DataFrame()
    if need_1m:
        # 상한은 **사용자가 --end로 명시할 때만** 건다. 상위TF 마지막 봉의 `open_time`으로
        # 자르면 그 봉 *내부*의 1분봉이 통째로 사라져, 데이터 끝까지 들고 있던 마지막
        # 포지션의 강제 청산가(`END_OF_DATA`)가 달라진다 — 거래 수·승률은 그대로인데
        # total_return만 어긋나는 종류의 오차다. WAN-95/96/99도 하한만 건다.
        df_1m = store.load(symbol, "1m", start_ms=window_start, end_ms=end_ms).reset_index(
            drop=True
        )

    if repair_htf_from_1m:
        if df_1m.empty:
            raise ValueError(
                f"{symbol} {timeframe}: 손상 봉 교정(repair_htf_from_1m)은 1분봉이 필요한데 "
                "데이터가 없습니다(need_1m=False이거나 1분봉 미보유)."
            )
        from data.partial_bars import repair_frame
        from data.resample import resample_ohlcv

        htf_df, _replaced = repair_frame(htf_df, resample_ohlcv(df_1m, "1m", timeframe))

    rates: list[FundingRate] = []
    if funding:
        rates = FundingRateStore(db_path).get_rates(
            symbol, start_ms=window_start, end_ms=window_end, include_predicted=True
        )
    return MarketData(symbol, timeframe, htf_df, df_1m, rates)


def iter_market_data_by_timeframe(
    symbol: str,
    timeframes: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    need_1m: bool = True,
    funding: bool = True,
    db_path: str = DB_PATH,
    cache_dir: str = CACHE_DIR,
) -> Iterator[tuple[str, MarketData]]:
    """한 심볼의 여러 TF를 **1분봉 한 번만 읽어** 순서대로 산출한다 (WAN-324 §1).

    `load_market_data`를 TF마다 부르면 같은 심볼의 1분봉을 TF 수만큼 다시 읽는다 — 채택
    좌표(12종목 × 4TF)의 하루치 적재에서 그게 **48회 SQL 읽기**였고, 서버는 CPU가 아니라
    디스크에서 기다렸다(WAN-322 실측 `real` 6분 23초 / `user` 2분 13초). 1분봉 창의 하한은
    상위TF 첫 봉의 `open_time`이라 TF마다 조금씩 다르므로, **가장 넓은 창을 한 번 읽어
    TF별로 잘라 준다**.

    🚨 **결과는 TF마다 `load_market_data`와 비트 단위로 같아야 한다** — 성능 작업이 숫자를
    바꾸면 그건 버그다(WAN-324 완료 기준 2). 같음이 성립하는 이유는 저장소 조회가
    `open_time >= start AND open_time < end`를 `ORDER BY open_time ASC`로 내주기
    때문이다(`OhlcvStore._load_native`): 넓은 창을 읽어 `open_time >= 그 TF의 하한`으로
    자르면 좁은 창을 직접 읽은 것과 행·순서·dtype이 같다. 회귀 테스트가 **실데이터로**
    고정한다.

    📌 **지연 산출(제너레이터)인 것은 메모리 때문이다** — 120일치 1분봉은 TF마다 사본이라
    넷을 한꺼번에 들면 워커가 그만큼 무거워진다. 소비자가 TF를 하나씩 처리하고 넘기면
    살아 있는 사본은 **공유 원본 + 지금 TF 하나**뿐이다.

    ⚠️ `load_market_data`의 `years`(미끄러지는 창)·`repair_htf_from_1m`(WAN-327 반사실)은
    싣지 않았다 — 이 헬퍼는 **창을 못 박은 하루치 적재 경로**를 위한 것이고, 두 기능은
    TF마다 창·교정이 달라 공유 로드의 전제가 깨진다. 필요하면 `load_market_data`를 쓸 것.
    """
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    try:
        htf_by_tf = {
            tf: store.load(symbol, tf, start_ms=start_ms, end_ms=end_ms).reset_index(drop=True)
            for tf in timeframes
        }
        # 1분봉 하한은 상위TF 첫 봉의 `open_time`이라 TF마다 다르다(4h 첫 봉 대 15m 첫 봉).
        window_starts = {
            tf: int(df["open_time"].iloc[0]) for tf, df in htf_by_tf.items() if not df.empty
        }
        shared_1m = pd.DataFrame()
        if need_1m and window_starts:
            # 상한은 `load_market_data`와 같이 호출부가 준 `end_ms`만 건다(그 봉 내부의
            # 1분봉을 잃지 않기 위해 상위TF 마지막 봉으로 자르지 않는다).
            shared_1m = store.load(
                symbol, "1m", start_ms=min(window_starts.values()), end_ms=end_ms
            ).reset_index(drop=True)

        rate_store = FundingRateStore(db_path) if funding else None
        for tf, htf_df in htf_by_tf.items():
            if htf_df.empty:
                yield tf, MarketData(symbol, tf, htf_df, pd.DataFrame(), [])
                continue
            window_start = window_starts[tf]
            df_1m = pd.DataFrame()
            if need_1m:
                df_1m = shared_1m[shared_1m["open_time"] >= window_start].reset_index(drop=True)
            rates: list[FundingRate] = []
            if rate_store is not None:
                rates = rate_store.get_rates(
                    symbol,
                    start_ms=window_start,
                    end_ms=int(htf_df["open_time"].iloc[-1]),
                    include_predicted=True,
                )
            yield tf, MarketData(symbol, tf, htf_df, df_1m, rates)
    finally:
        store.close()


def load_market_data_by_timeframe(
    symbol: str,
    timeframes: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    need_1m: bool = True,
    funding: bool = True,
    db_path: str = DB_PATH,
    cache_dir: str = CACHE_DIR,
) -> dict[str, MarketData]:
    """`iter_market_data_by_timeframe`을 한 번에 다 받는 판 — TF 슬라이스가 **동시에** 산다.

    ⚠️ 그래서 소비자가 TF를 하나씩 처리할 수 있으면 **반복자 쪽을 쓸 것**: 120일치 1분봉은
    TF마다 사본이라 넷을 한꺼번에 들면 워커 메모리가 그만큼 는다(적재 경로가 반복자를 쓰는
    이유). 이 판은 **네 TF를 나란히 놓고 봐야 하는 곳**(프로파일러·테스트)을 위한 것이다.
    """
    return dict(
        iter_market_data_by_timeframe(
            symbol,
            timeframes,
            start_ms=start_ms,
            end_ms=end_ms,
            need_1m=need_1m,
            funding=funding,
            db_path=db_path,
            cache_dir=cache_dir,
        )
    )


# --------------------------------------------------------------------------- #
# 구간 분할 (IS/OOS · 워크포워드)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Segment:
    """평가 구간 하나 — 전체 창에서 차지하는 시간 비율로 정의한다.

    구간을 **시간**으로 가르는 이유는 WAN-99와 같다: 거래를 사후에 나누면 OOS 사이징
    자본이 IS에서 굴러온 상태라 OOS 수익률이 IS 성과에 오염된다. 각 구간은 초기자본에서
    새로 시작하는 독립 백테스트여야 정직하다.
    """

    name: str
    window: int
    """워크포워드 창 번호(단일 실행이면 0)."""
    start_fraction: float
    end_fraction: float
    eval_start_fraction: float | None = None
    """따뜻한 연속 OOS(WAN-166)의 **평가 경계**. `None`이면 예전 그대로(데이터 창 = 평가 창).

    값이 있으면 데이터는 `[start_fraction, end_fraction)` 전체를 연속으로 태우되(워밍업 —
    존 재고·지표가 살아 있다), **탭이 이 경계 이후인 셋업만** 신선한 초기자본으로 평가한다.
    워밍업 경계가 세그먼트의 1급 속성이라는 점이 전체창 라벨링(WAN-155/161이 리포트마다
    따로 짠 방식)과의 차이다 — 행 좌표(`start_time`·`num_bars`)도 평가 창 기준으로 찍힌다."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.start_fraction < self.end_fraction <= 1.0:
            raise ValueError(
                f"구간 비율이 잘못됐습니다: [{self.start_fraction}, {self.end_fraction})"
            )
        if self.eval_start_fraction is not None and not (
            self.start_fraction <= self.eval_start_fraction < self.end_fraction
        ):
            raise ValueError(
                f"평가 경계가 데이터 창을 벗어났습니다: eval_start={self.eval_start_fraction}, "
                f"창=[{self.start_fraction}, {self.end_fraction})"
            )


FULL_SEGMENT = Segment(name=SEGMENT_FULL, window=0, start_fraction=0.0, end_fraction=1.0)

WARM_OOS_SEGMENT = Segment(
    name=SEGMENT_OOS_WARM,
    window=0,
    start_fraction=0.0,
    end_fraction=1.0,
    eval_start_fraction=IS_FRACTION,
)
"""따뜻한 연속 OOS(WAN-166). 데이터 창은 전 구간(워밍업 = 앞구간 **전체** — 실전의 연속
차트와 같게 가장 따뜻한 상태), 평가 경계는 차가운 OOS와 **같은 지점**(`IS_FRACTION`)이다.

워밍업 길이를 파라미터로 열지 않는 이유: 길이를 고르는 순간 IS에서 고르는 자유 파라미터가
하나 늘고(WAN-108이 경계한 자리), 앞구간 전체보다 짧은 워밍업은 "실전 연속"이라는 이
방식의 존재 이유를 스스로 깎는다. `start_fraction=0.0`이라 `slice_market`이 항등이 되고,
평가 경계의 ms 환산이 차가운 OOS의 하한과 **비트 단위로 같은 앵커**(전체 창의 첫/끝 봉)
에서 계산된다는 것도 이 고정의 부수 효과다."""


def segments_for(
    *, oos: bool = False, walkforward: int = 0, warm_oos: bool = False
) -> tuple[Segment, ...]:
    """실행할 구간 목록.

    - 기본: 전 구간 하나.
    - `oos=True`: 전 구간 + IS(앞 2/3) + OOS(뒤 1/3). 전 구간을 함께 내는 건 IS/OOS가
      전체와 얼마나 다른지 볼 기준선이 필요하기 때문이다.
    - `warm_oos=True`(WAN-166, **정본 리포트 기준**): 위에 더해 따뜻한 연속 OOS
      (`oos_warm`, 주 수치)를 낸다 — 차가운 `oos`는 함께 나와 과최적화 스트레스로
      병기되고, 두 수치의 차이 자체가 강건성 신호다(차이가 크면 그 성과가 물려받은
      존 재고에 기대고 있다는 뜻).
    - `walkforward=N`: 창을 N등분해 각 창 안에서 다시 IS/OOS로 가른다(겹치지 않는 롤링).
      한 번의 IS/OOS 분할은 경계를 어디에 긋느냐에 성적이 좌우되므로, 여러 창에서
      반복해 그 운을 걷어낸다.
    """
    if walkforward < 0:
        raise ValueError("--walkforward는 0 이상이어야 합니다.")
    if warm_oos and walkforward:
        raise ValueError(
            "--oos-warm과 --walkforward는 함께 쓸 수 없습니다 — 따뜻한 연속 OOS는 "
            "단일 IS/OOS 분할 위에 정의돼 있습니다(WAN-166). 롤링 창의 따뜻한 판은 "
            "필요해지면 별도 이슈로 엽니다."
        )
    if walkforward:
        segments: list[Segment] = []
        span = 1.0 / walkforward
        for i in range(walkforward):
            lo = i * span
            boundary = lo + span * IS_FRACTION
            segments.append(
                Segment(name=SEGMENT_IS, window=i, start_fraction=lo, end_fraction=boundary)
            )
            segments.append(
                Segment(name=SEGMENT_OOS, window=i, start_fraction=boundary, end_fraction=lo + span)
            )
        return tuple(segments)
    if warm_oos:
        return (
            FULL_SEGMENT,
            Segment(name=SEGMENT_IS, window=0, start_fraction=0.0, end_fraction=IS_FRACTION),
            WARM_OOS_SEGMENT,
            Segment(name=SEGMENT_OOS, window=0, start_fraction=IS_FRACTION, end_fraction=1.0),
        )
    if oos:
        return (
            FULL_SEGMENT,
            Segment(name=SEGMENT_IS, window=0, start_fraction=0.0, end_fraction=IS_FRACTION),
            Segment(name=SEGMENT_OOS, window=0, start_fraction=IS_FRACTION, end_fraction=1.0),
        )
    return (FULL_SEGMENT,)


def _time_window(frame: pd.DataFrame, lo: int, hi: int | None) -> pd.DataFrame:
    """`[lo, hi)` 창으로 자른다. `hi=None`이면 상한 없음(끝까지)."""
    times = frame["open_time"].astype("int64")
    mask = times >= lo if hi is None else (times >= lo) & (times < hi)
    return frame[mask].reset_index(drop=True)


def slice_market(market: MarketData, segment: Segment) -> MarketData:
    """구간에 해당하는 시간창으로 `MarketData`를 자른다(1분봉·펀딩비도 함께).

    마지막 구간(`end_fraction == 1.0`)의 상한은 **열어 둔다**. 비율을 시각으로 환산하면
    상한이 정확히 마지막 봉의 `open_time`이 되는데, 상한을 닫으면 그 봉이 어느 구간에도
    속하지 못하고 조용히 사라진다 — IS와 OOS를 합쳐도 전체가 되지 않는다.
    """
    if segment.start_fraction == 0.0 and segment.end_fraction == 1.0:
        return market
    times = market.htf_df["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    span = end - start
    lo = start + int(span * segment.start_fraction)
    hi = None if segment.end_fraction >= 1.0 else start + int(span * segment.end_fraction)

    htf = _time_window(market.htf_df, lo, hi)
    if htf.empty:
        return MarketData(market.symbol, market.timeframe, htf, pd.DataFrame(), [])
    df_1m = market.df_1m if market.df_1m.empty else _time_window(market.df_1m, lo, hi)
    rates = [
        r
        for r in market.funding_rates
        if lo <= r.funding_time and (hi is None or r.funding_time < hi)
    ]
    return MarketData(market.symbol, market.timeframe, htf, df_1m, rates)


def eval_boundary_ms(market: MarketData, segment: Segment) -> int | None:
    """따뜻한 세그먼트의 평가 경계를 ms로 환산한다. 평가 경계가 없으면 `None`.

    환산식은 `slice_market`의 `lo` 계산과 **글자 그대로 같아야 한다** — 그래야 따뜻한
    OOS(`oos_warm`)와 차가운 OOS(`oos`)가 정확히 같은 시각부터 평가되고, 두 행의
    `start_time`이 비트 단위로 일치한다(경계가 1봉이라도 어긋나면 두 방식의 차이에
    "평가 기간이 달랐다"가 섞여 강건성 신호로 읽을 수 없다).

    ⚠️ 앵커는 **넘겨받은 창의 첫/끝 봉**이다. 따뜻한 세그먼트는 `start_fraction=0.0`·
    `end_fraction=1.0`으로 고정돼 있어(`WARM_OOS_SEGMENT` 독스트링) `slice_market`이
    항등이고, 따라서 이 함수가 받는 창 = 전체 창 = 차가운 OOS의 분할 앵커다.
    """
    if segment.eval_start_fraction is None:
        return None
    times = market.htf_df["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    return start + int((end - start) * segment.eval_start_fraction)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def mean_r(result: BacktestResult, take_profit_r: float) -> float | None:
    """청산 사유로 매긴 거래당 평균 R(비용 반영 전).

    이 엔진에서 손절은 손절가 그대로, 고정 R 익절은 진입가 + `take_profit_r`×1R에서
    청산되므로 청산 사유만으로 R이 정확히 정해진다(−1.0R 또는 +`take_profit_r`).
    데이터 종료까지 미청산(`END_OF_DATA`)인 거래는 R이 확정되지 않아 제외한다.

    ⚠️ 진입가가 좋아지거나 나빠져 **1R 자체가 변하는 효과는 이 지표에 잡히지 않는다** —
    R로 정규화하면 "1R이 얼마짜리인가"가 나눠져 사라지기 때문이다(WAN-99). 그 대가는
    `total_return`에서 봐야 한다. 이 지표는 그 옆에서 승/패 구성만 보여준다.
    """
    values: list[float] = []
    for trade in result.trades:
        reason = trade.exits[-1].reason if trade.exits else None
        if reason is ExitReason.STOP_LOSS:
            values.append(-1.0)
        elif reason is ExitReason.TAKE_PROFIT:
            values.append(take_profit_r)
    return sum(values) / len(values) if values else None


@dataclass(frozen=True)
class RunOutcome:
    """한 번의 지정가(B안) 백테스트 결과 + 지정가 진단(`stats`)."""

    result: BacktestResult
    stats: ZoneLimitStats | None


def run_once(
    market: MarketData,
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    order_block_result: OrderBlockResult | None = None,
    portfolio: PortfolioParams | None = None,
    setup_sink: list[SetupDiagnostic] | None = None,
    eval_from_ms: int | None = None,
) -> RunOutcome:
    """지정가(B안) 백테스트를 실행한다.

    `backtest.zone_limit_backtest.run_zone_limit_backtest_verbose`(단일 포지션) 또는
    `run_zone_limit_portfolio_backtest`(동시 다중 포지션, `portfolio` 지정 시)를 탄다.
    A안(종가 진입) 경로는 WAN-208/WAN-215로 제거돼 진입 방식은 `zone_limit` 단독이다.

    `portfolio`를 주면 동시 다중 포지션 회계(WAN-103)로 돈다 — 셋업 탐색·체결
    시뮬레이션은 **완전히 같고**(같은 `build_zone_limit_candidates`) 후보를 배치하는
    회계만 달라진다. `None`(기본)이면 채택 기본값인 동시 1포지션 경로 그대로다:
    포트폴리오 시퀀서로 `max_concurrent=1`을 흉내 내지 않는 이유는 그러면 대조군과
    실험군이 같은 시퀀서를 타서 **시퀀서 버그가 차이를 0으로 감출 수 있기** 때문이다
    (wan103 `series_rows`가 같은 이유로 대조군을 채택 엔진으로 낸다).

    `setup_sink`(WAN-106)를 주면 eligible 셋업별 `SetupDiagnostic`을 채운다 — **미체결
    셋업**("살 뻔했는데 못 산 자리")을 DB에 적재하는 입력이다. 다중 포지션 경로에는 이
    진단이 없으므로 **조용히 빈 리스트를 주는 대신 거부한다**: 미체결이 0건인 것과
    미체결을 셀 줄 모르는 것이 화면에서 같아 보이면 안 된다(WAN-95 부류).

    `eval_from_ms`(WAN-166 따뜻한 연속 OOS)를 주면 `market` 전체를 연속으로 태워 존
    재고·지표를 데운 뒤, **탭이 그 시각 이후인 셋업만** 신선한 초기자본으로 시퀀싱한다.
    단일 포지션 전용이다 — 다중 포지션 경로는 배선하지 않았으므로 조용히 무시하는 대신
    거부한다(WAN-95 부류).
    """
    if eval_from_ms is not None and portfolio is not None:
        raise ValueError(
            "따뜻한 연속 OOS(eval_from_ms)는 동시 다중 포지션 경로에 아직 없습니다"
            "(run_zone_limit_portfolio_backtest에 배선하지 않음, WAN-166). "
            "단일 포지션(채택 기본값)으로 평가하세요."
        )
    if setup_sink is not None and portfolio is not None:
        raise ValueError(
            "미체결 셋업 진단(setup_sink)은 동시 다중 포지션 경로에 아직 없습니다"
            "(run_zone_limit_portfolio_backtest에 setup_sink 인자가 없음). "
            "단일 포지션(채택 기본값)으로 적재하세요."
        )
    if market.df_1m.empty:
        raise ValueError(
            f"{market.symbol} {market.timeframe}: 지정가(zone_limit) 백테스트는 1분봉이 "
            "필요한데 데이터가 없습니다."
        )
    if portfolio is not None:
        pf_result, pf_stats, _pf = run_zone_limit_portfolio_backtest(
            market.htf_df,
            market.df_1m,
            market.timeframe,
            portfolio=portfolio,
            confluence_params=params,
            backtest_config=cfg,
            order_block_result=order_block_result,
            funding_rates=market.funding_rates,
        )
        return RunOutcome(result=pf_result, stats=pf_stats)
    result, stats = run_zone_limit_backtest_verbose(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        confluence_params=params,
        backtest_config=cfg,
        order_block_result=order_block_result,
        funding_rates=market.funding_rates,
        setup_sink=setup_sink,
        eval_from_ms=eval_from_ms,
    )
    return RunOutcome(result=result, stats=stats)


def detect_order_blocks(
    market: MarketData, ob_params: OrderBlockParams | None = None
) -> OrderBlockResult:
    """구간의 오더블록을 탐지한다. 격자 안에서 한 번만 하고 조합들이 공유한다.

    오더블록 탐지는 컨플루언스 파라미터와 무관하므로 이 재사용은 결과를 바꾸지 않는다.

    ⚠️ `ob_params`는 **컨플루언스와 달리 결과를 바꾼다** — `combine_obs`가 여기 있고
    (WAN-149), 병합 여부에 따라 `signals`/`retap_signals`가 통째로 달라진다. 따라서
    이 인자가 다른 조합끼리는 탐지 결과를 공유하면 안 된다. `None`이면 채택 기본값
    (WAN-149: 분리)이고, 옛 수치를 결론에 박아 둔 리포트는 `LEGACY_OB_PARAMS`를 준다.
    """
    return OrderBlockDetector(ob_params).run(market.htf_df)


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class RunRow(BaseModel):
    """격자 한 셀의 좌표 + 성과 지표.

    좌표(심볼·TF·구간·파라미터)를 성과와 같은 행에 두는 건 재현을 위해서다 — CSV만 봐도
    어떤 설정에서 나온 숫자인지 알 수 있어야 한다(WAN-65).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    window: int
    entry_mode: str
    take_profit_r: float
    offset_bps: float
    retap_mode: str = "every_tap"
    """재탭 정책(WAN-138). 기본값은 채택 기본값(`every_tap`) — 이 축이 생기기 전에 만들어진
    행 생성부(옛 리포트 테스트 픽스처 등)는 전부 채택 기본값 시절 행이라 기본값이 곧 정답이다.
    `build_row`는 항상 `params.retap_mode`로 명시 설정하므로 실행 산출 행은 실제 값을 싣는다."""
    position_mode: str = "single"
    """포지션 정책(WAN-130). `"single"` = 채택 기본값(동시 1포지션) · `"multi"` = 동시
    다중 포지션(WAN-103). 기본값이 채택 기본값이라 이 축이 생기기 전 행 생성부(옛 픽스처)는
    그대로 유효하다 — `retap_mode`가 같은 자리에서 쓴 방식이다."""
    portfolio_leverage: float | None = None
    """다중 포지션의 명목 상한 배수(`열린 명목 합 ≤ 자본 × leverage`). 단일 포지션 행은
    `None` — 그 경로에는 포트폴리오 레버리지라는 개념이 없다(`cfg.risk_sizing.leverage`가
    per-trade clamp로만 쓰인다). 0이 아니라 `None`인 이유는 "안 씀"과 "0배"를 가르기 위해서다."""
    combine_obs: bool = False
    """존 병합 여부(WAN-149). `False` = 채택 기본값(원본 존 단위 분리) · `True` = 옛 기본값.

    **실제로 탐지에 넘어간 값**이지 요청 라벨이 아니다(`build_row`가 `OrderBlockParams`를
    받아 그대로 싣는다) — "분리로 돌고 병합 라벨이 붙는" WAN-95 부류를 막는다. 기본값이
    채택 기본값이라 이 축이 생기기 전 행 생성부(옛 픽스처)는 그대로 유효하다."""
    max_zone_width_atr: float | None = None
    """존폭 필터 문턱(WAN-158, ATR 배수). `None` = 채택 기본값(꺼짐 = 전부 매매).

    `portfolio_leverage`와 같은 자리 — "안 씀"과 숫자를 가르려고 0이 아니라 `None`이다.
    **실제로 엔진에 넘어간 `params.max_zone_width_atr`**를 싣는다(요청 라벨이 아니다)."""
    limit_valid_bars: int | None = 24
    """지정가 유효기간(WAN-222, 상위TF 봉 수). `24` = 채택 기본값 · `None` = 무기한(존
    무효화까지 대기).

    기본값이 채택 기본값(`24`)이라 이 축이 생기기 전 행 생성부(옛 픽스처)는 그대로 유효하다
    — `retap_mode`가 같은 자리에서 쓴 방식이다. **실제로 엔진에 넘어간
    `params.limit_valid_bars`**를 싣는다(요청 라벨이 아니다). `None`(무기한)은 CSV에서
    빈 칸이 되고, 되읽을 때 `_empty_leverage_is_none`이 `NaN`을 `None`으로 되돌린다."""
    fill: str
    seed: int
    start_time: int | None
    end_time: int | None
    num_bars: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    sharpe: float | None
    profit_factor: float | None
    mean_r: float | None
    fill_rate: float | None
    eligible_setups: int | None
    num_filled: int | None
    funding_coverage: float | None

    @field_validator("portfolio_leverage", "max_zone_width_atr", "limit_valid_bars", mode="before")
    @classmethod
    def _empty_leverage_is_none(cls, value: object) -> object:
        """CSV 왕복에서 빈 칸(→ `NaN`)을 `None`으로 되돌린다.

        단일 포지션 행은 이 열이 항상 비어 있는데, `pd.read_csv`가 그 빈 칸을 `NaN`(float)로
        읽어 오면 pydantic이 `float | None`의 float 가지로 받아 **저장 전 행과 달라진다** —
        요약만 다시 그리는 `--from-csv` 경로가 원본과 어긋나는 그 사고다(리포트 모듈의
        `rows_from_csv`가 회귀 테스트로 왕복 일치를 고정한다).

        `limit_valid_bars`(WAN-222)의 빈 칸은 **무기한(`None`)**을 뜻한다 — 유한한 봉 수는
        항상 값이 찍히므로 `NaN`은 무기한 팔에서만 나온다.
        """
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


ROW_COLUMNS: tuple[str, ...] = tuple(RunRow.model_fields)


def build_row(
    outcome: RunOutcome,
    market: MarketData,
    *,
    segment: Segment,
    params: ConfluenceParams,
    fill_name: str,
    portfolio: PortfolioParams | None = None,
    order_block: OrderBlockParams | None = None,
) -> RunRow:
    """실행 결과를 한 행으로.

    `portfolio`·`order_block`은 `run_once`/`detect_order_blocks`에 넘긴 것과 **같은
    객체**를 준다 — 라벨을 따로 만들면 "다중으로 돌고 single 라벨이 붙는" WAN-95 부류의
    거짓말이 가능해진다.

    따뜻한 세그먼트(WAN-166)의 행 좌표(`start_time`·`num_bars`)는 데이터 창이 아니라
    **평가 창** 기준이다 — 전 구간을 태웠다는 이유로 전 구간 좌표를 찍으면 차가운 OOS
    행과 나란히 놓았을 때 "다른 기간을 쟀다"로 읽힌다(실제로는 같은 기간의 다른 방식이다).
    """
    m = outcome.result.metrics
    stats = outcome.stats
    start_time = market.start_ms if not market.empty else None
    num_bars = len(market.htf_df)
    boundary = eval_boundary_ms(market, segment)
    if boundary is not None and not market.empty:
        times = market.htf_df["open_time"].astype("int64")
        in_eval = times[times >= boundary]
        start_time = int(in_eval.iloc[0]) if not in_eval.empty else None
        num_bars = int(in_eval.size)
    return RunRow(
        symbol=market.symbol,
        timeframe=market.timeframe,
        segment=segment.name,
        window=segment.window,
        entry_mode=params.entry_mode,
        take_profit_r=params.take_profit_r,
        offset_bps=params.zone_limit_offset_bps,
        retap_mode=params.retap_mode,
        position_mode="single" if portfolio is None else "multi",
        portfolio_leverage=None if portfolio is None else portfolio.leverage,
        combine_obs=(order_block or OrderBlockParams()).combine_obs,
        max_zone_width_atr=params.max_zone_width_atr,
        limit_valid_bars=params.limit_valid_bars,
        fill=fill_name,
        seed=params.fill_dropout_seed,
        start_time=start_time,
        end_time=market.end_ms if not market.empty else None,
        num_bars=num_bars,
        num_trades=m.num_trades,
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        sharpe=m.sharpe,
        profit_factor=m.profit_factor,
        mean_r=mean_r(outcome.result, params.take_profit_r),
        fill_rate=stats.fill_rate if stats else None,
        eligible_setups=stats.eligible if stats else None,
        num_filled=stats.filled if stats else None,
        funding_coverage=m.funding_coverage,
    )


def rows_to_frame(rows: Sequence[RunRow]) -> pd.DataFrame:
    """행들을 컬럼 순서가 고정된 DataFrame으로."""
    records = [row.model_dump() for row in rows]
    return pd.DataFrame(records, columns=list(ROW_COLUMNS))


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #

#: 항상 보여주는 성과 열 (이슈 완료기준: total_return / 승률 / MDD / 거래수 / 체결률 /
#: 평균 R / Sharpe).
_METRIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("return%", "total_return"),
    ("win%", "win_rate"),
    ("mdd%", "max_drawdown"),
    ("trades", "num_trades"),
    ("fill%", "fill_rate"),
    ("meanR", "mean_r"),
    ("sharpe", "sharpe"),
)

#: 축 열. 값이 하나뿐인 축은 표에서 접는다(줄이 좁아야 읽힌다).
_AXIS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("symbol", "symbol"),
    ("tf", "timeframe"),
    ("segment", "segment"),
    ("win#", "window"),
    ("entry", "entry_mode"),
    ("tp_r", "take_profit_r"),
    ("off_bp", "offset_bps"),
    ("retap", "retap_mode"),
    ("pos", "position_mode"),
    ("lev", "portfolio_leverage"),
    ("merge", "combine_obs"),
    ("zw_atr", "max_zone_width_atr"),
    ("lvb", "limit_valid_bars"),
    ("fill", "fill"),
    ("seed", "seed"),
)

_PERCENT_FIELDS = frozenset({"total_return", "win_rate", "max_drawdown", "fill_rate"})


def _fmt_cell(field: str, value: object) -> str:
    if field == "limit_valid_bars":
        # `None`(무기한)은 「해당 없음(—)」이 아니라 실험의 한 팔이라 이름을 준다(WAN-222).
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "none"
        return str(int(value)) if isinstance(value, (int, float)) else str(value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if field in _PERCENT_FIELDS and isinstance(value, float):
        return f"{value * 100:.2f}"
    if field in {"mean_r", "sharpe"} and isinstance(value, float):
        return f"{value:.2f}"
    if field == "take_profit_r" and isinstance(value, float):
        return f"{value:g}"
    if field in {"offset_bps", "portfolio_leverage"} and isinstance(value, float):
        return f"{value:g}"
    return str(value)


def render_table(rows: Sequence[RunRow]) -> str:
    """조합별 1행 비교표(콘솔용).

    값이 하나뿐인 축 열은 접고 머리말에 요약한다 — 축이 9개라 다 펼치면 한 줄이 터미널을
    넘어가고, 정작 봐야 할 성과 열이 밀려난다.
    """
    if not rows:
        return "실행 결과가 없습니다 (데이터 없음 또는 격자가 비어 있음)."
    dicts = [row.model_dump() for row in rows]
    varying = [(h, f) for h, f in _AXIS_COLUMNS if len({d[f] for d in dicts}) > 1]
    fixed = [(h, f) for h, f in _AXIS_COLUMNS if (h, f) not in varying]

    headers = [h for h, _ in varying] + [h for h, _ in _METRIC_COLUMNS]
    fields = [f for _, f in varying] + [f for _, f in _METRIC_COLUMNS]
    table = [[_fmt_cell(f, d[f]) for f in fields] for d in dicts]
    widths = [max(len(headers[i]), *(len(row[i]) for row in table)) for i in range(len(headers))]

    lines: list[str] = []
    if fixed:
        summary = " · ".join(f"{h}={_fmt_cell(f, dicts[0][f])}" for h, f in fixed)
        lines.append(f"[고정] {summary}")
    header_line = "  ".join(h.rjust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("-" * len(header_line))
    lines += ["  ".join(c.rjust(widths[i]) for i, c in enumerate(row)) for row in table]
    return "\n".join(lines)


def render_csv(rows: Sequence[RunRow]) -> str:
    """전 열 CSV(표에서 접힌 축도 모두 포함)."""
    return str(rows_to_frame(rows).to_csv(index=False))


def render_json(rows: Sequence[RunRow]) -> str:
    """전 열 JSON(레코드 배열)."""
    return json.dumps([row.model_dump() for row in rows], ensure_ascii=False, indent=2)


_RENDERERS = {"table": render_table, "csv": render_csv, "json": render_json}

FORMATS: tuple[str, ...] = tuple(_RENDERERS)


def render(rows: Sequence[RunRow], fmt: str) -> str:
    """포맷 이름으로 렌더한다."""
    try:
        renderer = _RENDERERS[fmt]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 출력 형식: {fmt!r} (지원: {', '.join(_RENDERERS)})") from exc
    return renderer(rows)


def write_output(text: str, path: str | Path) -> Path:
    """렌더 결과를 파일로 저장하고 경로를 반환한다."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def iter_seeds(fill: FillPreset, override: Sequence[int] | None = None) -> Iterator[int]:
    """이 체결 가정에서 돌 시드들.

    탈락이 없는 가정은 시드가 결과를 바꾸지 않으므로 하나만 돈다 — 난수를 아예 뽑지
    않기 때문이다(WAN-96). 여러 개를 도는 건 같은 숫자를 N번 계산하는 낭비가 된다.
    """
    if fill.dropout_rate <= 0.0:
        yield 0
        return
    yield from (override if override is not None else fill.seeds)
