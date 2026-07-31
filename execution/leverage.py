"""레버리지 북 사이징 회계 — 백테스트·실시간이 공유하는 순수 함수 (WAN-169/180/213/171).

이 모듈은 「칸 = (종목, TF)마다 1포지션 · 여러 칸 동시 · 한 지갑(공유 자본)」 북의
**사이징 결정**만 담는다(배수 N을 어디에 싣나 · 북 명목 상한 · cap-only 합성 여유).
배치·시퀀싱·손익·청산 검사는 `backtest.leverage_book`(무거운 백테스트 자료형에 묶임)이
가지고, 실시간 집행은 `execution.engine`이 가진다 — 둘 다 이 모듈의 결정 함수를
**그대로** 호출한다(로직 이중화 금지, WAN-171 완료 기준).

## 왜 `backtest.leverage_book`이 아니라 여기인가 (WAN-171)

`backtest.leverage_book`은 `backtest.zone_limit_backtest`(전략·지표·서브스텝 사슬)를
import해서 무겁고, `config.settings`·`execution.engine`이 그 스택을 끌어오면 import
사이클과 시동 비용이 생긴다. 이 모듈은 `execution.sizing`(+pydantic)만 의존하므로
설정·집행 계층이 가볍게 물려 쓸 수 있다. `backtest.leverage_book`은 하위 호환을 위해
여기 정의를 **재수출**한다(기존 import·CSV 재현 불변).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from execution.sizing import PositionSizingParams

#: 최악 가정 청산 검사에 쓰는 기본 유지증거금률(명목 대비, WAN-103 결정 4).
#: 여기가 정본이고 `backtest.portfolio`가 이 값을 import한다(두 곳에 두면 드리프트).
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.005

LeverageMode = Literal["combined", "cap_only"]
"""배수 N을 어디에 싣는가 (WAN-180).

* `combined` — **매 거래 사이징 N배**(리스크 1%→N% · 거래당 천장 N× · 북 상한 N×).
  WAN-169 사용자 확정 방식.
* `cap_only` — **북 명목 상한만 N배**. 거래당 리스크·거래당 천장은 1배 그대로다
  (같은 크기 포지션을 더 많이 동시에 — 밀림을 줄이는 팔, WAN-180 팔 B). WAN-213 채택.
"""


class LeverageBookParams(BaseModel):
    """레버리지 북 회계 파라미터 (WAN-169).

    이 객체를 만드는 곳에서만 북이 돈다 — 기본 경로는 이 모듈을 모른다(옵트인).
    """

    model_config = ConfigDict(frozen=True)

    leverage_multiple: float = Field(default=5.0, gt=0)
    """사이징 배수 N. **매 거래의 크기를 N배**로 키우고(리스크 1% → N%) 북 전체 명목
    상한도 N배가 된다(모듈 독스트링). 1.0이면 채택 사이징 그대로에 자본 공유만 얹는다.

    ⚠️ **기본값 5.0은 채택 값이다(WAN-213 재-베이스라인, 2026-07-30 사용자 결정 「전부 다」)** —
    `ConfluenceParams()`가 채택 전략을 내듯 `LeverageBookParams()`가 채택 북을 낸다. WAN-169
    시절의 중립 기준점(1.0 · combined)은 `LEGACY_BOOK_PARAMS`로 뺐다(비트 동일 검산·회귀
    테스트가 그 상수를 쓴다). 근거는 WAN-180 실측: cap_only는 배수를 올려도 MDD가 거의 안
    늘고(6년 2배 18.9% → 5배 19.6%) 5배가 같은 낙폭으로 복리를 가장 많이 받는 지점이다."""
    leverage_mode: LeverageMode = "cap_only"
    """배수 N을 싣는 자리(WAN-180). 기본 `"cap_only"`(WAN-213 채택) = 북 명목 상한만 N배로
    키우고 거래당 크기·천장은 1배로 둔다(팔 B — 거래당 리스크를 1배로 묶어 낙폭이 배수를
    안 따라간다). `"combined"`는 WAN-169 방식으로 매 거래를 N배 한다(리스크 1%→N%)."""
    maintenance_margin_rate: float = Field(default=DEFAULT_MAINTENANCE_MARGIN_RATE, ge=0, lt=1)
    """최악 가정 청산 검사에 쓰는 유지증거금률(명목 대비, WAN-103 결정 4 재사용)."""


#: WAN-169 중립 기준점(배수 1.0 · combined) — 「채택 사이징 그대로에 자본 공유만 얹은」 북.
#: 이 값에서 칸 하나짜리 북은 채택 단일 포지션 시퀀서와 **비트 단위로 같은 거래**를 낸다
#: (`tests/test_leverage_book.py`가 고정). WAN-213이 클래스 기본값을 채택 북(cap_only 5배)으로
#: 옮긴 뒤, 그 중립 항등을 검정하는 코드는 이 상수를 명시적으로 써야 한다(WAN-159의
#: `LEGACY_MAX_ZONE_WIDTH_ATR`와 같은 「기본값 이동 + 명시 핀」 패턴).
LEGACY_BOOK_PARAMS = LeverageBookParams(leverage_multiple=1.0, leverage_mode="combined")


def scale_sizing_params(
    sizing: PositionSizingParams, multiple: float, *, mode: LeverageMode = "combined"
) -> PositionSizingParams:
    """사이징 파라미터에 배수 N을 싣는다.

    `"combined"`(기본 = WAN-169) — 「매 거래 크기 N배」: `risk_pct` 모드는
    `risk_per_trade`, `fixed_notional` 모드는 `notional_fraction`이 거래 크기를 정하므로
    둘 다 N배 하고, 거래·북 공용 명목 천장(`leverage`)도 N배 한다. 상한만 키우고 크기를
    안 키우면 그것이 WAN-169 당시 폐기된 cap-only 모델이다 — 세 필드를 한 곳에서 함께
    키워 그 어긋남을 막는다.

    `"cap_only"`(WAN-180 팔 B) — 그 폐기됐던 모델을 **명시적 축으로** 되살린 것:
    `leverage`(북 상한)만 N배 하고 거래 크기 노브 둘은 손대지 않는다. ⚠️ 이 결과의
    `leverage`는 북 상한 용도다 — 거래당 천장까지 함께 커지면 안 되므로, 호출부
    (`run_leverage_book`·`resolve_book_sizing`)가 거래당 사이징에는 **원본(1배) 설정**을 쓴다.
    """
    if mode == "cap_only":
        return sizing.model_copy(update={"leverage": sizing.leverage * multiple})
    return sizing.model_copy(
        update={
            "risk_per_trade": sizing.risk_per_trade * multiple,
            "notional_fraction": sizing.notional_fraction * multiple,
            "leverage": sizing.leverage * multiple,
        }
    )


def sizing_notional_cap(sizing: PositionSizingParams, equity: float) -> float:
    """이 자본에서 허용되는 열린 명목 합의 상한 = `자본 × leverage`(min `max_notional_fraction`).

    `position_size`의 clamp와 같은 식이어야 한다 — 여기서 "여유 있음"이라 판정한 진입을
    사이징이 0으로 거부하면 그 스킵이 사이징 거부로 잘못 분류된다(백테스트·라이브 공용).
    """
    cap = equity * sizing.leverage
    if sizing.max_notional_fraction is not None:
        cap = min(cap, equity * sizing.max_notional_fraction)
    return cap


def book_per_trade_sizing(
    base: PositionSizingParams, book: LeverageBookParams
) -> PositionSizingParams:
    """이 북에서 **거래 하나**를 사이징할 파라미터.

    * `combined` — 매 거래 크기가 N배이므로 `scale_sizing_params(base, N)`.
    * `cap_only` — 거래당 크기·천장은 1배 그대로이므로 `base`(북 상한만 N배는
      `resolve_book_sizing`이 `open_notional` 합성으로 건다).

    거래당 리스크 금액(장부 기록)도 이 파라미터의 `risk_per_trade`로 재야 라벨과 실제가
    어긋나지 않는다 — 그래서 집행 계층이 이 함수를 직접 쓴다(WAN-171).
    """
    if book.leverage_mode == "cap_only":
        return base
    return scale_sizing_params(base, book.leverage_multiple, mode="combined")


@dataclass(frozen=True)
class BookSizing:
    """북 사이징 결정 — 거래당 파라미터 + `position_size`에 넘길 합성 `open_notional`.

    * `params` — 거래 하나에 쓸 사이징(`book_per_trade_sizing`).
    * `synthetic_open` — `position_size(open_notional=...)`에 넘길 값. cap_only에서는
      「거래당 천장」과 「북 여유」의 **더 작은 쪽**만 이 진입에 허용되도록 합성한다
      (`position_size`는 상한이 하나뿐이라 여유를 그 min으로 만들어 준다). combined에서는
      실제 열린 명목 그대로다.
    * `cap_exhausted` — 북 명목 상한이 소진돼(여유 ≤ 0) 진입 자체를 스킵해야 하면 True.
    """

    params: PositionSizingParams
    synthetic_open: float
    cap_exhausted: bool


def resolve_book_sizing(
    base: PositionSizingParams,
    book: LeverageBookParams,
    *,
    equity: float,
    open_notional: float,
) -> BookSizing:
    """공유 자본 위에서 새 진입 하나의 사이징 파라미터·합성 여유를 정한다.

    백테스트 배치(`run_leverage_book`)와 실시간 집행(`execution.engine`)이 **이 한 함수**를
    호출해 사이징을 정한다 — 두 경로가 각자 상한식을 복제하면 갈라진다(WAN-95/112/123의
    조용한 실패). 반환한 `params`·`synthetic_open`을 그대로 `execution.sizing.position_size`
    에 넘기면 「칸당 1포지션 · 공유 자본 · 배수 N」 회계가 선다.

    `open_notional`은 지금 열려 있는 모든 칸의 명목 합(공유 지갑). `equity`는 공유 자본.
    """
    eff = scale_sizing_params(base, book.leverage_multiple, mode=book.leverage_mode)
    per_trade = book_per_trade_sizing(base, book)
    book_cap = sizing_notional_cap(eff, equity)
    if open_notional >= book_cap:
        return BookSizing(params=per_trade, synthetic_open=open_notional, cap_exhausted=True)
    if book.leverage_mode == "cap_only":
        per_trade_cap = sizing_notional_cap(per_trade, equity)
        allowed = min(per_trade_cap, book_cap - open_notional)
        synthetic_open = per_trade_cap - allowed
    else:
        synthetic_open = open_notional
    return BookSizing(params=per_trade, synthetic_open=synthetic_open, cap_exhausted=False)
