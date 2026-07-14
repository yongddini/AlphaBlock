# WAN-81 메인 엔진 재산출 — 구 엔진 대비 비교

구 엔진(WAN-73까지 기본값: `retap_mode=once`, `rsi_gate_mode=extreme`, `take_profit_mode=line`, `deviation_filter=None`, `short_enabled=False`) vs 신 엔진(WAN-81 기본값: 볼린저 진입가, `retap_mode=every_tap`, `rsi_gate_mode=first_tap_free`, `take_profit_mode=fixed_r`(1.5R), `short_enabled=True`).

## 심볼 × TF × 엔진별 성과

| symbol | timeframe | engine | num_trades | long_trades | short_trades | win_rate | total_return | max_drawdown |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | old | 231 | 231 | 0 | 0.4113 | -0.4629 | 0.4832 |
| BTC/USDT:USDT | 15m | new | 1278 | 684 | 594 | 0.5321 | -0.6174 | 0.6843 |
| BTC/USDT:USDT | 1h | old | 60 | 60 | 0 | 0.3500 | -0.1739 | 0.2001 |
| BTC/USDT:USDT | 1h | new | 405 | 203 | 202 | 0.5753 | 0.0690 | 0.2261 |
| BTC/USDT:USDT | 4h | old | 17 | 17 | 0 | 0.4118 | -0.0425 | 0.0577 |
| BTC/USDT:USDT | 4h | new | 115 | 61 | 54 | 0.5217 | 0.0448 | 0.1535 |
| BTC/USDT:USDT | 1d | old | 3 | 3 | 0 | 0.3333 | -0.0134 | 0.0201 |
| BTC/USDT:USDT | 1d | new | 17 | 11 | 6 | 0.5882 | 0.0539 | 0.0907 |
| ETH/USDT:USDT | 15m | old | 261 | 261 | 0 | 0.4100 | -0.5446 | 0.5825 |
| ETH/USDT:USDT | 15m | new | 1517 | 802 | 715 | 0.5399 | -0.5660 | 0.7136 |
| ETH/USDT:USDT | 1h | old | 68 | 68 | 0 | 0.3235 | -0.2670 | 0.3062 |
| ETH/USDT:USDT | 1h | new | 465 | 242 | 223 | 0.5742 | 0.4164 | 0.1654 |
| ETH/USDT:USDT | 4h | old | 13 | 13 | 0 | 0.3846 | -0.0383 | 0.0648 |
| ETH/USDT:USDT | 4h | new | 116 | 61 | 55 | 0.5862 | 0.1692 | 0.0781 |
| ETH/USDT:USDT | 1d | old | 5 | 5 | 0 | 0.4000 | -0.0126 | 0.0348 |
| ETH/USDT:USDT | 1d | new | 22 | 13 | 9 | 0.5000 | 0.0073 | 0.1109 |
| SOL/USDT:USDT | 15m | old | 251 | 251 | 0 | 0.4263 | -0.3159 | 0.3206 |
| SOL/USDT:USDT | 15m | new | 1775 | 892 | 883 | 0.5358 | -0.4294 | 0.5750 |
| SOL/USDT:USDT | 1h | old | 56 | 56 | 0 | 0.3393 | -0.1729 | 0.2177 |
| SOL/USDT:USDT | 1h | new | 490 | 241 | 249 | 0.5694 | 0.3562 | 0.2153 |
| SOL/USDT:USDT | 4h | old | 24 | 24 | 0 | 0.4167 | -0.0067 | 0.0637 |
| SOL/USDT:USDT | 4h | new | 120 | 62 | 58 | 0.5417 | 0.1709 | 0.1403 |
| SOL/USDT:USDT | 1d | old | 1 | 1 | 0 | 0.0000 | -0.0102 | 0.0102 |
| SOL/USDT:USDT | 1d | new | 18 | 8 | 10 | 0.4444 | -0.0140 | 0.0533 |

## §5(구 WAN-82 버그) 수정으로 되살아난 진입 수

- 구 버그(병합 단위 전체 `entered`) 시그널 총합: 9535
- 신 로직(개별 존 `entered`) 시그널 총합: 11740
- 되살아난 진입 수(신 - 구): 2205

## 갭D: min_stop_distance_fraction(0.003)으로 기각된 진입 수

- 신 엔진 확정 진입 중 손절 거리 하한 미만으로 기각된 건수: 2544

## 기존 수치 무효 선언

WAN-19/22/46/50/58/68/70/71/73/74/75/76 등 기존 성과 리포트는 모두 이 이슈(WAN-81) 이전 구 엔진 기준이다. 이 리포트 이후로는 위 표의 `new` 행을 기준으로 삼는다.
