"""범용 백테스트 하네스 — 실행기가 공유하는 골격 (WAN-101).

`backtest/wan68_*.py` … `wan99_*.py` 12개 스크립트는 저마다 같은 일을 다시 짰다:
저장소에서 최근 N년을 로드하고, 1분봉·펀딩비를 붙이고, 파라미터를 조립하고, A안/B안
엔진을 골라 태우고, 지표를 행으로 모아 표로 렌더한다. 그 반복이 "익절 1.5R 말고
2R이면?" 같은 한 줄짜리 질문에도 새 스크립트 + PR을 요구하게 만들었다. 이 모듈은 그
공통 골격을 한곳에 모아 `backtest.run`(범용 CLI)이 파라미터만 받아 즉시 답을 내게 한다.

## 이 모듈이 책임지는 것

1. **데이터 로딩** — `load_market_data`가 (심볼, TF)의 상위TF·1분봉·펀딩비를 한 번에
   묶어 `MarketData`로 낸다. 심볼 축약형(`BTCUSDT`)도 저장소 표기(`BTC/USDT:USDT`)로
   정규화한다.
2. **파라미터 조립** — `build_params`가 `entry_mode`에 맞는 `rsi_mode`를 **한 세트로**
   묶고(WAN-41), 그 경로에서 무의미한 노브가 조용히 무시되지 않도록 거부한다.
3. **경로 스위치** — `run_once`가 `entry_mode`에 따라 A안(`sweep.evaluate`)/B안
   (`run_zone_limit_backtest_verbose`)을 태운다. `entry_mode`는 라벨이 아니라 경로
   스위치라는 WAN-95 규칙을 CLI에서도 유지한다.
4. **구간 분할** — `segments_for` / `slice_window`가 IS/OOS·워크포워드 창을 **시간으로**
   가른다. 각 구간은 초기자본에서 새로 시작하는 독립 백테스트다(WAN-99와 동일 규칙).
5. **렌더** — `render_table` / `render_csv` / `render_json`.

## 이 모듈이 하지 않는 것

해석과 권고는 하지 않는다. 리포트 모듈(`wanNN_*.py`)이 결론 문장을 쓰는 곳이고, 여기는
**숫자를 내는 곳**이다. 기본값도 바꾸지 않는다 — `build_params()`에 아무것도 주지
않으면 채택 기본값(`ConfluenceParams()`)이 그대로 나온다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest.metrics import build_metrics
from backtest.models import BacktestConfig, BacktestResult, ExitReason, Trade
from backtest.portfolio import PortfolioParams
from backtest.sweep import bars_per_year, default_backtest_config, evaluate
from backtest.zone_limit_backtest import (
    ZoneLimitStats,
    build_result_from_trades,
    run_zone_limit_backtest_verbose,
    run_zone_limit_portfolio_backtest,
)
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import BandBar, ConfluenceParams, OrderBlockResult, RsiGateMode
from strategy.order_blocks import OrderBlockDetector

# --------------------------------------------------------------------------- #
# 기본값 (기존 리포트 스크립트와 동일한 좌표)
# --------------------------------------------------------------------------- #

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1h",)
DEFAULT_YEARS: float = 3.0
DB_PATH = "data/ohlcv.db"
CACHE_DIR = "data/cache"

_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

#: IS 비중 — 앞 2/3에서 고르고 뒤 1/3(OOS)로 검증한다(WAN-99와 동일).
IS_FRACTION = 2.0 / 3.0

SEGMENT_FULL = "full"
SEGMENT_IS = "is"
SEGMENT_OOS = "oos"


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
#: 중간에 체결되므로 확정봉 RSI를 쓰면 체결 시점과 판정 시점이 어긋난다.
_RSI_MODE_FOR_ENTRY: dict[str, str] = {"close": "closed_bar", "zone_limit": "realtime"}

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


def build_params(
    *,
    entry_mode: str = "zone_limit",
    take_profit_r: float | None = None,
    offset_bps: float | None = None,
    fill: FillPreset = BASELINE_FILL,
    seed: int = 0,
    short_enabled: bool | None = None,
    retap_mode: str | None = None,
    base: ConfluenceParams | None = None,
) -> ConfluenceParams:
    """CLI 인자를 `ConfluenceParams`로 조립한다.

    아무것도 주지 않으면 **채택 기본값 그대로**(`ConfluenceParams()`)를 반환한다 —
    그래야 CLI 기본 실행이 WAN-95/96/99 리포트의 기준선 셀과 같은 엔진을 탄다.

    ⚠️ `offset_bps=None`이 "채택 기본값에 맡긴다"는 뜻이고, 0.0은 **명시적 0bp 요청**이다.
    둘을 가르지 않고 0.0을 기본 인자로 두면 이 함수가 `ConfluenceParams`의 기본 오프셋을
    **말없이 덮어써서**, WAN-112가 기본값을 2bp로 올려도 CLI 기본 실행만 혼자 0bp로 도는
    조용한 갈라짐이 생긴다(= 위 첫 문단의 약속이 깨진다).

    지정가 전용 노브(`offset_bps`·`fill`)를 종가 진입에 주면 **거부한다**. 조용히
    무시하면 "오프셋 5bp로 돌렸다"고 믿는 사용자에게 오프셋이 없는 결과를 주게 되는데,
    그것이 WAN-91(`funding_enabled`만 켜고 펀딩을 안 넘김)·WAN-95(`entry_mode`가 라벨일
    뿐이던 시절)가 각각 한 번씩 겪은 조용한 실패다.
    """
    if entry_mode not in _RSI_MODE_FOR_ENTRY:
        supported = ", ".join(_RSI_MODE_FOR_ENTRY)
        raise ValueError(f"알 수 없는 진입 방식: {entry_mode!r} (지원: {supported})")
    if entry_mode == "close":
        if offset_bps:
            raise ValueError(
                "지정가 오프셋(--offset-bps)은 종가 진입(--entry-mode close)에 적용되지 "
                "않습니다. 존에 거는 주문이 없으니 오프셋을 얹을 가격도 없습니다."
            )
        if fill.name != BASELINE_FILL.name:
            raise ValueError(
                f"체결 가정(--fill {fill.name})은 종가 진입(--entry-mode close)에 적용되지 "
                "않습니다. 탭이 곧 진입이라 미체결이라는 개념이 없습니다."
            )

    update: dict[str, object] = {
        "entry_mode": entry_mode,
        "rsi_mode": _RSI_MODE_FOR_ENTRY[entry_mode],
        "fill_penetration_bps": fill.penetration_bps,
        "fill_dropout_rate": fill.dropout_rate,
        "fill_dropout_seed": seed,
    }
    if entry_mode == "close":
        # A안은 오프셋을 읽지 않는다(`apply_zone_limit_offset` 호출부가 B안뿐). 채택
        # 기본값의 2bp를 그대로 들고 있으면 리포트에 "오프셋 2bp"라 찍히면서 실제로는
        # 아무 데도 안 쓰이는 거짓 라벨이 된다 — WAN-95가 고친 그 병이다.
        update["zone_limit_offset_bps"] = 0.0
    elif offset_bps is not None:
        update["zone_limit_offset_bps"] = offset_bps
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
    funding_enabled: bool | None = None,
    seed: int = 0,
) -> BacktestConfig:
    """공용 팩토리(`default_backtest_config`) 위에 비용·펀딩 오버라이드만 얹는다.

    `BacktestConfig()`를 직접 만들지 않는 이유는 WAN-65와 같다 — 그러면
    `settings.effective_risk_sizing`이 조용히 빠져 모든 진입이 자본 100%를 쓰는
    경로로 되돌아간다.
    """
    cfg = default_backtest_config(timeframe, seed=seed)
    update: dict[str, object] = {}
    if fee_rate is not None:
        update["fee_rate"] = fee_rate
    if maker_fee_rate is not None:
        update["maker_fee_rate"] = maker_fee_rate
    if slippage is not None:
        update["slippage"] = slippage
    if funding_enabled is not None:
        update["funding_enabled"] = funding_enabled
    return cfg.model_copy(update=update) if update else cfg


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
) -> MarketData:
    """(심볼, TF)의 상위TF·1분봉·펀딩비를 한 번에 로드한다.

    `start_ms`/`end_ms`를 주면 그 창으로, 아니면 최근 `years`년으로 자른다. `need_1m`이
    False면 1분봉을 아예 읽지 않는다 — 종가 전용 실행에서 수백 MB를 읽지 않기 위해서다.

    데이터가 없으면 빈 `MarketData`를 반환한다(예외를 던지지 않는다) — 격자 실행에서
    심볼 하나가 없다고 전체가 죽으면 안 되므로, 호출부가 `empty`를 보고 건너뛴다.
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

    rates: list[FundingRate] = []
    if funding:
        rates = FundingRateStore(db_path).get_rates(
            symbol, start_ms=window_start, end_ms=window_end, include_predicted=True
        )
    return MarketData(symbol, timeframe, htf_df, df_1m, rates)


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

    def __post_init__(self) -> None:
        if not 0.0 <= self.start_fraction < self.end_fraction <= 1.0:
            raise ValueError(
                f"구간 비율이 잘못됐습니다: [{self.start_fraction}, {self.end_fraction})"
            )


FULL_SEGMENT = Segment(name=SEGMENT_FULL, window=0, start_fraction=0.0, end_fraction=1.0)


def segments_for(*, oos: bool = False, walkforward: int = 0) -> tuple[Segment, ...]:
    """실행할 구간 목록.

    - 기본: 전 구간 하나.
    - `oos=True`: 전 구간 + IS(앞 2/3) + OOS(뒤 1/3). 전 구간을 함께 내는 건 IS/OOS가
      전체와 얼마나 다른지 볼 기준선이 필요하기 때문이다.
    - `walkforward=N`: 창을 N등분해 각 창 안에서 다시 IS/OOS로 가른다(겹치지 않는 롤링).
      한 번의 IS/OOS 분할은 경계를 어디에 긋느냐에 성적이 좌우되므로, 여러 창에서
      반복해 그 운을 걷어낸다.
    """
    if walkforward < 0:
        raise ValueError("--walkforward는 0 이상이어야 합니다.")
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


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def coverage_window(df_1m: pd.DataFrame) -> tuple[int, int]:
    """1분봉이 커버하는 시간창 `[start, end]`(ms)."""
    times = df_1m["open_time"].astype("int64")
    return int(times.min()), int(times.max())


def windowed_result(
    trades: list[Trade],
    cfg: BacktestConfig,
    timeframe: str,
    start: int,
    end: int,
    *,
    funding_coverage: float | None = None,
) -> BacktestResult:
    """A안 거래를 창으로 한정해 B안과 동일한 방식으로 재집계한 결과(WAN-41/95 공정 창).

    지정가(B안)는 1분봉이 커버하는 구간의 셋업만 평가하므로, 종가(A안) 성과도 같은
    창으로 한정해야 두 진입 방식이 **같은 기간**을 놓고 비교된다.

    `funding_coverage`는 원본 결과의 값을 그대로 물려받는다 — 창으로 자르는 건 거래
    집계일 뿐 펀딩 커버리지를 바꾸지 않는데, 넘기지 않으면 재집계에서 `None`으로 사라져
    "펀딩을 반영했는지"를 알 수 없게 된다(WAN-95).
    """
    in_window = [t for t in trades if start <= t.entry_time <= end]
    if not in_window:
        metrics = build_metrics(
            initial_capital=cfg.initial_capital,
            equities=[cfg.initial_capital],
            trades=[],
            annualization_factor=bars_per_year(timeframe),
            funding_coverage=funding_coverage,
        )
        return BacktestResult(config=cfg, trades=[], equity_curve=[], metrics=metrics)
    return build_result_from_trades(
        in_window, cfg, timeframe, funding_coverage_value=funding_coverage
    )


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
    """한 번의 백테스트 결과 + 지정가 진단(종가 진입이면 `stats`가 None)."""

    result: BacktestResult
    stats: ZoneLimitStats | None


def run_once(
    market: MarketData,
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    order_block_result: OrderBlockResult | None = None,
    fair_window: bool = False,
    portfolio: PortfolioParams | None = None,
) -> RunOutcome:
    """`entry_mode`에 따라 A안/B안 엔진을 태운다 (WAN-95 경로 스위치).

    `entry_mode="close"` → `backtest.sweep.evaluate`(→`BacktestEngine`),
    `"zone_limit"` → `backtest.zone_limit_backtest.run_zone_limit_backtest_verbose`.
    두 진입점은 자기 것이 아닌 `entry_mode`를 `ValueError`로 거부하므로, 이 함수가
    경로를 잘못 고르면 조용한 오답이 아니라 예외로 드러난다.

    `fair_window`는 종가 진입 성과를 1분봉 커버 창으로 한정한다 — 같은 표에서 지정가와
    나란히 비교할 때만 의미가 있다(그때만 기간이 어긋나므로).

    `portfolio`를 주면 동시 다중 포지션 회계(WAN-103)로 돈다 — 셋업 탐색·체결
    시뮬레이션은 **완전히 같고**(같은 `build_zone_limit_candidates`) 후보를 배치하는
    회계만 달라진다. `None`(기본)이면 채택 기본값인 동시 1포지션 경로 그대로다:
    포트폴리오 시퀀서로 `max_concurrent=1`을 흉내 내지 않는 이유는 그러면 대조군과
    실험군이 같은 시퀀서를 타서 **시퀀서 버그가 차이를 0으로 감출 수 있기** 때문이다
    (wan103 `series_rows`가 같은 이유로 대조군을 채택 엔진으로 낸다).
    """
    if portfolio is not None and params.entry_mode != "zone_limit":
        raise ValueError(
            "동시 다중 포지션(portfolio)은 지정가(B안) 전용인데 "
            f'entry_mode="{params.entry_mode}"가 들어왔습니다(WAN-95/103).'
        )
    if params.entry_mode == "zone_limit":
        if market.df_1m.empty:
            raise ValueError(
                f"{market.symbol} {market.timeframe}: 지정가(zone_limit) 백테스트는 1분봉이 "
                "필요한데 데이터가 없습니다. 종가 진입은 --entry-mode close를 쓰세요."
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
        )
        return RunOutcome(result=result, stats=stats)

    result = evaluate(
        market.htf_df,
        confluence_params=params,
        backtest_config=cfg,
        order_block_result=order_block_result,
        funding_rates=market.funding_rates,
    )
    if fair_window and not market.df_1m.empty:
        start, end = coverage_window(market.df_1m)
        result = windowed_result(
            result.trades,
            cfg,
            market.timeframe,
            start,
            end,
            funding_coverage=result.metrics.funding_coverage,
        )
    return RunOutcome(result=result, stats=None)


def detect_order_blocks(market: MarketData) -> OrderBlockResult:
    """구간의 오더블록을 탐지한다. 격자 안에서 한 번만 하고 조합들이 공유한다.

    오더블록 탐지는 컨플루언스 파라미터와 무관하므로 이 재사용은 결과를 바꾸지 않는다.
    """
    return OrderBlockDetector().run(market.htf_df)


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

    @field_validator("portfolio_leverage", mode="before")
    @classmethod
    def _empty_leverage_is_none(cls, value: object) -> object:
        """CSV 왕복에서 빈 칸(→ `NaN`)을 `None`으로 되돌린다.

        단일 포지션 행은 이 열이 항상 비어 있는데, `pd.read_csv`가 그 빈 칸을 `NaN`(float)로
        읽어 오면 pydantic이 `float | None`의 float 가지로 받아 **저장 전 행과 달라진다** —
        요약만 다시 그리는 `--from-csv` 경로가 원본과 어긋나는 그 사고다(리포트 모듈의
        `rows_from_csv`가 회귀 테스트로 왕복 일치를 고정한다).
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
) -> RunRow:
    """실행 결과를 한 행으로.

    `portfolio`는 `run_once`에 넘긴 것과 **같은 객체**를 준다 — 라벨을 따로 만들면
    "다중으로 돌고 single 라벨이 붙는" WAN-95 부류의 거짓말이 가능해진다.
    """
    m = outcome.result.metrics
    stats = outcome.stats
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
        fill=fill_name,
        seed=params.fill_dropout_seed,
        start_time=market.start_ms if not market.empty else None,
        end_time=market.end_ms if not market.empty else None,
        num_bars=len(market.htf_df),
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
    ("fill", "fill"),
    ("seed", "seed"),
)

_PERCENT_FIELDS = frozenset({"total_return", "win_rate", "max_drawdown", "fill_rate"})


def _fmt_cell(field: str, value: object) -> str:
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
