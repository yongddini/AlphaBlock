"""대시보드 실시간 차트 — 브라우저가 직접 구독하는 미확정 kline 배선 (WAN-147).

## 무엇을 하나

`dashboard.lightweight_chart`가 그린 정적 차트에 **형성 중인 봉**을 얹는다. 브라우저
(iframe 안 JS)가 바이낸스 선물 웹소켓(`wss://fstream.binance.com/ws/<sym>@kline_<tf>`)에
직접 붙어 미확정 kline을 받고, 마지막 캔들과 볼린저 하단선을 `series.update()`로
갱신한다 — 트레이딩뷰가 하는 것과 같은 방식이다.

**파이썬을 경유하지 않는다**: 수집기(`data.collector`)를 상시 돌릴 필요가 없고, 화면을
열면 그때 붙었다가 닫으면 끊긴다. 서버 부하도 0이다.

## 🚨 표시 계층 전용 — 엔진은 건드리지 않는다

이 모듈이 만드는 값은 **화면에만** 쓰인다. 백테스트 결과·거래 표·성과 지표는 전부
확정봉 기준으로 이미 계산된 것을 그대로 보여주고, 실시간 값이 그 숫자에 섞이지 않는다
(그래야 표의 숫자가 화면에서 흔들리지 않는다). 실시간 데이터는 **DB에 저장하지 않는다**.

## 로직 이중화를 최소화한 지점 — 밴드

밴드를 봉내에서 그리려면 SMA20/σ20을 브라우저에서 다시 계산해야 하는데, 수식을 통째로
JS로 옮기면 `strategy.realtime_band.RealtimeBand`와 두 벌이 된다(라벨과 실제가 갈라지는
WAN-91/95/100/112 부류의 사고를 이 저장소는 반복해 겪었다). 그래서:

* 파이썬이 **확정봉 종가 창**(`sma_length-1`개)과 밴드 파라미터를 payload에 실어 보내고,
* JS는 `computeLiveBand`(아래 `LIVE_BAND_JS`)에서 **현재가 한 표본만 얹어** 20표본을
  완성한다 — `RealtimeBand.value`와 **같은 계산을 같은 순서로** 한다.
* 그 두 구현이 실제로 같은 값을 내는지는 **테스트가 Node로 JS를 직접 실행해** 고정한다
  (`tests/test_dashboard_live_chart.py::test_js_band_matches_realtime_band`). 정본은
  여전히 파이썬(`RealtimeBand`)이고 JS는 그 사본임이 테스트로 강제된다.

밴드 창이 **형성 중인 봉과 이어지지 않으면**(DB가 오래돼 사이에 빈 봉이 있으면) JS는
밴드를 갱신하지 않고 화면에 그 사실을 표시한다 — 낡은 창으로 계산한 값을 라이브인 척
그리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from data.models import timeframe_to_ms
from data.stream import to_ws_symbol
from strategy.models import ConfluenceParams, DeviationFilterParams

#: 바이낸스 USDⓈ-M 선물 웹소켓 베이스. `data.stream.FUTURES_WS_BASE`와 같은 값이지만
#: 여기서는 **브라우저가** 붙으므로 파이썬 스트림 소비 경로(`data.stream`)와 무관하다.
LIVE_WS_BASE = "wss://fstream.binance.com"

#: 바이낸스 kline 스트림이 지원하는 인터벌. 대시보드 TF가 이 목록에 없으면 라이브를
#: 아예 켜지 않는다(없는 스트림에 붙으면 조용히 아무 메시지도 안 오는 상태가 된다).
LIVE_INTERVALS: frozenset[str] = frozenset(
    {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"}
)

#: 형성 중인 봉 색(옅은 몸통) — 확정봉과 시각적으로 구분하기 위한 값. 사용자가 확정된
#: 것과 진행 중인 것을 헷갈리지 않아야 한다(WAN-147 완료 기준).
LIVE_BULL_COLOR = "rgba(38, 166, 154, 0.45)"
LIVE_BEAR_COLOR = "rgba(239, 83, 80, 0.45)"


def live_stream_url(symbol: str, timeframe: str) -> str:
    """단일 kline 스트림 URL. `symbol`은 ccxt 표기(`"BTC/USDT:USDT"`)를 받는다."""
    return f"{LIVE_WS_BASE}/ws/{to_ws_symbol(symbol)}@kline_{timeframe}"


#: 봉내 라이브 밴드 계산 — `strategy.realtime_band.RealtimeBand.value`의 JS 사본.
#:
#: ⚠️ 이 문자열을 고칠 때는 반드시 파이썬 쪽(`RealtimeBand`)과 나란히 고친다. 두
#: 구현의 일치는 `tests/test_dashboard_live_chart.py`가 Node로 실행해 검증한다.
#: 표준편차는 파이썬과 동일하게 **2-패스 모표준편차**(ddof=0)로 구한다 — 제곱합 누적은
#: BTC처럼 큰 가격에서 상쇄 오차가 커진다(`realtime_band._population_stdev` 주석).
LIVE_BAND_JS = """
function computeLiveBand(closedCloses, livePrice, params) {
  // params: {smaLength, anchor: "sma"|"close", widthKind: "stdev"|"pct",
  //          widthValue, directionSign}
  const windowSize = Math.max(params.smaLength - 1, 0);
  const needsWindow = params.anchor === "sma" || params.widthKind === "stdev";
  if (needsWindow && closedCloses.length < windowSize) return null;
  const price = Number(livePrice);
  if (!Number.isFinite(price)) return null;

  let samples = null;
  if (needsWindow) {
    samples = closedCloses.slice(closedCloses.length - windowSize).concat([price]);
  }

  let anchorVal;
  if (params.anchor === "sma") {
    let total = 0;
    for (const s of samples) total += s;
    anchorVal = total / samples.length;
  } else {
    anchorVal = price;
  }

  let widthVal;
  if (params.widthKind === "pct") {
    widthVal = anchorVal * params.widthValue;
  } else {
    let total = 0;
    for (const s of samples) total += s;
    const mean = total / samples.length;
    let acc = 0;
    for (const s of samples) acc += (s - mean) * (s - mean);
    widthVal = Math.sqrt(acc / samples.length) * params.widthValue;
  }

  if (!Number.isFinite(anchorVal) || !Number.isFinite(widthVal)) return null;
  return anchorVal - params.directionSign * widthVal;
}
"""


@dataclass(frozen=True)
class LiveChartConfig:
    """차트 HTML에 실어 보낼 실시간 설정 한 벌.

    `band_*`가 `None`이면 캔들만 라이브로 갱신하고 밴드는 확정봉까지만 그린다
    (밴드 폭이 `atr`이면 형성 중인 봉의 고가·저가가 필요해 실시간 값이 될 수 없다 —
    `RealtimeBand`가 같은 이유로 `atr`을 거부한다).
    """

    stream_url: str
    symbol: str
    timeframe: str
    interval_ms: int
    #: 차트에 그려진 마지막 확정봉의 open_time(ms). JS가 "다음 봉"을 계산하는 기준이다.
    last_bar_open_ms: int
    #: 밴드 창에 쓸 확정봉 종가(`sma_length-1`개, 시간 오름차순). `None`이면 밴드 라이브 없음.
    band_closes: tuple[float, ...] | None
    band_params: dict[str, object] | None
    band_color: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "streamUrl": self.stream_url,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "intervalMs": self.interval_ms,
            "lastBarOpenMs": self.last_bar_open_ms,
            "bandCloses": list(self.band_closes) if self.band_closes is not None else None,
            "bandParams": self.band_params,
            "bandColor": self.band_color,
            "liveColors": {"up": LIVE_BULL_COLOR, "down": LIVE_BEAR_COLOR},
        }


def band_js_params(filter_params: DeviationFilterParams, direction_sign: int) -> dict[str, object]:
    """`computeLiveBand`에 넘길 파라미터 dict(파이썬 정본에서 그대로 옮긴 값)."""
    return {
        "smaLength": filter_params.sma_length,
        "anchor": filter_params.anchor,
        "widthKind": filter_params.width_kind,
        "widthValue": filter_params.width_value,
        "directionSign": direction_sign,
    }


def build_live_config(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    conf_params: ConfluenceParams | None,
    band_color: str | None,
    direction_sign: int = 1,
) -> LiveChartConfig | None:
    """차트에 실을 실시간 설정을 만든다. 라이브를 켤 수 없으면 `None`.

    `frame`은 차트에 그려지는 **확정봉** 프레임(`open_time` 오름차순)이다. 밴드 창은
    그 꼬리에서 `sma_length-1`개 종가를 떼어 쓴다 — 형성 중인 봉의 20번째 표본 자리를
    현재가에게 남겨 두는 `RealtimeBand`의 계약과 같다.

    `direction_sign=1`은 롱(하단선)이다. 채택 기본값이 롱 온리(`short_enabled=False`,
    WAN-87)이므로 화면에도 하단선만 그린다.
    """
    if timeframe not in LIVE_INTERVALS or frame.empty:
        return None
    interval_ms = timeframe_to_ms(timeframe)
    # 미확정 봉(리샘플 TF의 꼬리 — `data.resample`)은 창에서 뺀다. 그 봉의 종가는 아직
    # 확정 종가가 아니라 밴드 창에 넣으면 20번째 표본 자리를 두 번 쓰는 셈이 된다.
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
        if frame.empty:
            return None
    last_bar_open_ms = int(frame["open_time"].iloc[-1])

    band_closes: tuple[float, ...] | None = None
    band_params: dict[str, object] | None = None
    filter_params = conf_params.deviation_filter if conf_params is not None else None
    # `atr` 폭은 실시간 값이 될 수 없다(형성 중인 봉의 고가·저가가 필요) — `RealtimeBand`가
    # 같은 이유로 거부하므로 여기서도 값을 지어내지 않고 밴드 라이브만 끈다.
    if filter_params is not None and filter_params.width_kind != "atr" and band_color is not None:
        needed = max(filter_params.sma_length - 1, 0)
        closes = [float(v) for v in frame["close"].tolist()[-needed:]] if needed else []
        if len(closes) == needed:
            band_closes = tuple(closes)
            band_params = band_js_params(filter_params, direction_sign)

    return LiveChartConfig(
        stream_url=live_stream_url(symbol, timeframe),
        symbol=symbol,
        timeframe=timeframe,
        interval_ms=interval_ms,
        last_bar_open_ms=last_bar_open_ms,
        band_closes=band_closes,
        band_params=band_params,
        band_color=band_color if band_closes is not None else None,
    )
