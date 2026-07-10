# 오더블록 시각 검증 & TradingView 패리티 (WAN-13)

`strategy/order_blocks.py`(WAN-7)의 탐지 결과가 TradingView "Volumized Order
Blocks"(Fluxchart, `strategy/reference/`)와 동일한 위치에 그려지는지 확인하기
위한 도구 모음이다.

## 구성

- `chart.py` — OHLCV 캔들 + 탐지 오더블록 오버레이를 PNG로 저장 (`render_order_block_chart`).
- `fixtures.py` — TV 정답 오더블록 데이터셋(fixture) 로더 (`load_fixture`, `list_fixtures`).
- `report.py` — 탐지 결과 vs fixture 비교, 일치율/불일치 리포트 (`compare_to_fixture`).
- `fixtures/*.json` — 정답 데이터셋 파일.
- `../../scripts/parity_report.py` — 위 셋을 묶어 실행하는 CLI.

## 실행

```bash
uv run python scripts/parity_report.py strategy/parity/fixtures/btcusdt_1h_bullish_sample.json
```

`out/parity/<symbol>_<timeframe>.png` 에 오버레이 이미지가 저장되고, 표준출력에
패리티 리포트(일치율, 항목별 오차, 과탐지 목록)가 출력된다.

## fixture 포맷

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "source": "캡처 절차·일자 등 출처 설명",
  "candles": [
    {"open_time": 0, "open": 100, "high": 102, "low": 90, "close": 95, "volume": 10}
  ],
  "tv_order_blocks": [
    {
      "direction": "bull",
      "top": 103.0,
      "bottom": 98.0,
      "start_time": 36000000,
      "invalidated": true,
      "break_time": 43200000,
      "note": "선택: 캡처 시 참고사항"
    }
  ]
}
```

- `candles`: 검증에 사용할 OHLCV 구간 전체(`data.storage.OhlcvStore.load()`가
  반환하는 것과 같은 스키마의 부분집합). 실제 저장분에서 export한 구간이면 된다.
- `tv_order_blocks`: TV 차트에서 읽은 오더블록 좌표. `direction`은 `"bull"` 또는
  `"bear"`, `invalidated`는 TV에서 존이 깨진(빗금/반투명) 상태로 보이는지,
  `break_time`은 무효화된 봉의 `open_time`(모르면 생략).

## TV 정답 확보 절차 (수기 캡처, 자동 스크래핑 금지)

1. TradingView에서 대상 심볼·타임프레임 차트를 열고 "Volumized Order Blocks"
   (Fluxchart) 인디케이터를 기본 파라미터로 추가한다.
2. 화면에 보이는 각 오더블록 박스에 대해: 방향(강세/약세), 상단가(top),
   하단가(bottom), 시작 시각(박스 왼쪽 변의 봉 시각), 무효화(빗금/반투명) 여부,
   무효화 시각을 기록한다. 봉에 마우스를 올리면 정확한 OHLC·시각을 확인할 수
   있다.
3. 같은 구간의 원본 OHLCV를 `data.storage.OhlcvStore.load()`로 조회해
   `candles` 배열로 export한다(`open_time`은 UTC ms).
4. 위 fixture 포맷으로 JSON을 작성해 `fixtures/`에 저장하고, `source` 필드에
   캡처 일자·계정·인디케이터 설정을 남긴다.
5. `scripts/parity_report.py`로 리포트를 실행해 일치율을 확인한다.

**현재 리포에 포함된 `*_sample.json` 두 개는 실제 TradingView 캡처가 아니라,**
`strategy/reference/README.md` 알고리즘 명세를 기준으로 손으로 계산한 값이다
(각 파일의 `source` 필드에 명시). 도구의 동작을 시연하고 회귀 검증하기 위한
placeholder이며, 위 절차에 따라 실제 TV 캡처로 교체되어야 한다.

## 허용 오차

기본값(`report.DEFAULT_PRICE_TOLERANCE_PCT`, `DEFAULT_TIME_TOLERANCE_MS`):

- 가격(top/bottom): TV 값 대비 상대 오차 **0.05%** 이내.
- 시작 시각(start_time): **0ms**(정확히 일치) — 두 시스템 모두 같은 봉 인덱싱을
  쓰므로 시작 시각은 반올림 없이 일치해야 한다.

부동소수점 계산 차이 등으로 오차가 필요하면 `compare_to_fixture(...,
price_tolerance_pct=..., time_tolerance_ms=...)`로 조정한다.
