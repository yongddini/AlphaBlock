# WAN-84 신 엔진(WAN-81) OOS·워크포워드 + 무작위 진입 매칭 널 재검정

3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS, 로컬 `data/ohlcv.db` 실데이터 3년. 재현: `python -m backtest.wan84_new_engine_validation`.
원자료: `backtest/reports/wan84_random_entry_new_engine.csv`(매칭 널), `backtest/reports/wan84_cost_sensitivity.csv`(비용 민감도), `backtest/reports/wan84_buy_hold.csv`(바이앤홀드), `backtest/reports/wan84_side_breakdown.csv`(롱/숏 분해).

> **선행 조건**: WAN-81(PR #63) 머지 완료. 이 리포트는 저장소 현재 기본값
> (`ConfluenceParams()` = 신 엔진: 볼린저 진입가, 첫탭 무조건/재탭 RSI, 고정
> 1:1.5R 익절, 숏 활성화)로 산출했다. WAN-19/22/46/50/58/68/70/71/73/74/75/76 등
> 기존 수치는 전부 구 엔진 기준이라 이 리포트의 절대값과 비교할 수 없다
> (`backtest/reports/wan81_summary.md` 기존 수치 무효 선언 참고).

## 무작위 진입 매칭 널 (신 엔진 청산 규칙 그대로, WAN-70 방법론 재사용)

| 심볼 | TF | 구간 | 게이트 | 실제수익 | n | 무작위평균 | 95% CI | p |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | new_engine | -0.248 | 490 | -0.005 | [-0.208, 0.186] | 0.995 |
| BTC/USDT:USDT | 15m | OOS | new_engine | -0.099 | 259 | -0.000 | [-0.136, 0.148] | 0.905 |
| BTC/USDT:USDT | 1h | IS | new_engine | 0.082 | 157 | 0.054 | [-0.126, 0.235] | 0.340 |
| BTC/USDT:USDT | 1h | OOS | new_engine | 0.050 | 92 | 0.075 | [-0.073, 0.236] | 0.595 |
| BTC/USDT:USDT | 4h | IS | new_engine | 0.033 | 43 | 0.089 | [-0.008, 0.211] | 0.850 |
| BTC/USDT:USDT | 4h | OOS | new_engine | -0.079 | 21 | -0.030 | [-0.089, 0.023] | 0.950 |
| BTC/USDT:USDT | 1d | IS | new_engine | -0.040 | 4 | -0.020 | [-0.040, 0.001] | 0.750 |
| BTC/USDT:USDT | 1d | OOS | new_engine | -0.026 | 5 | -0.001 | [-0.002, -0.001] | 1.000 |
| ETH/USDT:USDT | 15m | IS | new_engine | -0.285 | 554 | -0.241 | [-0.436, -0.050] | 0.675 |
| ETH/USDT:USDT | 15m | OOS | new_engine | 0.069 | 296 | 0.244 | [0.033, 0.513] | 0.930 |
| ETH/USDT:USDT | 1h | IS | new_engine | 0.010 | 155 | 0.126 | [-0.073, 0.350] | 0.870 |
| ETH/USDT:USDT | 1h | OOS | new_engine | 0.278 | 116 | 0.320 | [0.121, 0.516] | 0.660 |
| ETH/USDT:USDT | 4h | IS | new_engine | 0.043 | 40 | 0.109 | [0.008, 0.214] | 0.870 |
| ETH/USDT:USDT | 4h | OOS | new_engine | 0.010 | 25 | 0.043 | [-0.038, 0.116] | 0.775 |
| ETH/USDT:USDT | 1d | IS | new_engine | 0.013 | 6 | 0.015 | [-0.012, 0.045] | 0.470 |
| ETH/USDT:USDT | 1d | OOS | new_engine | -0.024 | 6 | -0.011 | [-0.025, 0.011] | 0.880 |
| SOL/USDT:USDT | 15m | IS | new_engine | -0.035 | 644 | 0.145 | [-0.201, 0.579] | 0.810 |
| SOL/USDT:USDT | 15m | OOS | new_engine | -0.180 | 304 | 0.020 | [-0.186, 0.255] | 0.975 |
| SOL/USDT:USDT | 1h | IS | new_engine | -0.097 | 175 | 0.170 | [-0.033, 0.446] | 1.000 |
| SOL/USDT:USDT | 1h | OOS | new_engine | -0.077 | 95 | -0.015 | [-0.173, 0.129] | 0.770 |
| SOL/USDT:USDT | 4h | IS | new_engine | 0.080 | 57 | 0.112 | [-0.012, 0.222] | 0.700 |
| SOL/USDT:USDT | 4h | OOS | new_engine | -0.016 | 18 | -0.011 | [-0.081, 0.064] | 0.570 |
| SOL/USDT:USDT | 1d | IS | new_engine | -0.001 | 5 | 0.007 | [-0.026, 0.050] | 0.470 |
| SOL/USDT:USDT | 1d | OOS | new_engine | -0.026 | 5 | -0.027 | [-0.050, -0.001] | 0.475 |

## 비용 민감도 (1.0x/1.5x/2.0x, 동일 후보 재시퀀싱)

| 심볼 | TF | 구간 | 배율 | 총수익률 | 거래수 |
| -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | 1.0x | -0.248 | 490 |
| BTC/USDT:USDT | 15m | IS | 1.5x | -0.441 | 490 |
| BTC/USDT:USDT | 15m | IS | 2.0x | -0.585 | 490 |
| BTC/USDT:USDT | 15m | OOS | 1.0x | -0.099 | 259 |
| BTC/USDT:USDT | 15m | OOS | 1.5x | -0.232 | 259 |
| BTC/USDT:USDT | 15m | OOS | 2.0x | -0.346 | 259 |
| BTC/USDT:USDT | 1h | IS | 1.0x | 0.082 | 157 |
| BTC/USDT:USDT | 1h | IS | 1.5x | -0.001 | 157 |
| BTC/USDT:USDT | 1h | IS | 2.0x | -0.079 | 157 |
| BTC/USDT:USDT | 1h | OOS | 1.0x | 0.050 | 92 |
| BTC/USDT:USDT | 1h | OOS | 1.5x | 0.004 | 92 |
| BTC/USDT:USDT | 1h | OOS | 2.0x | -0.041 | 92 |
| BTC/USDT:USDT | 4h | IS | 1.0x | 0.033 | 43 |
| BTC/USDT:USDT | 4h | IS | 1.5x | 0.019 | 43 |
| BTC/USDT:USDT | 4h | IS | 2.0x | 0.005 | 43 |
| BTC/USDT:USDT | 4h | OOS | 1.0x | -0.079 | 21 |
| BTC/USDT:USDT | 4h | OOS | 1.5x | -0.085 | 21 |
| BTC/USDT:USDT | 4h | OOS | 2.0x | -0.092 | 21 |
| BTC/USDT:USDT | 1d | IS | 1.0x | -0.040 | 4 |
| BTC/USDT:USDT | 1d | IS | 1.5x | -0.041 | 4 |
| BTC/USDT:USDT | 1d | IS | 2.0x | -0.041 | 4 |
| BTC/USDT:USDT | 1d | OOS | 1.0x | -0.026 | 5 |
| BTC/USDT:USDT | 1d | OOS | 1.5x | -0.026 | 5 |
| BTC/USDT:USDT | 1d | OOS | 2.0x | -0.027 | 5 |
| ETH/USDT:USDT | 15m | IS | 1.0x | -0.285 | 554 |
| ETH/USDT:USDT | 15m | IS | 1.5x | -0.485 | 554 |
| ETH/USDT:USDT | 15m | IS | 2.0x | -0.629 | 554 |
| ETH/USDT:USDT | 15m | OOS | 1.0x | 0.069 | 296 |
| ETH/USDT:USDT | 15m | OOS | 1.5x | -0.092 | 296 |
| ETH/USDT:USDT | 15m | OOS | 2.0x | -0.229 | 296 |
| ETH/USDT:USDT | 1h | IS | 1.0x | 0.010 | 155 |
| ETH/USDT:USDT | 1h | IS | 1.5x | -0.056 | 155 |
| ETH/USDT:USDT | 1h | IS | 2.0x | -0.118 | 155 |
| ETH/USDT:USDT | 1h | OOS | 1.0x | 0.278 | 116 |
| ETH/USDT:USDT | 1h | OOS | 1.5x | 0.214 | 116 |
| ETH/USDT:USDT | 1h | OOS | 2.0x | 0.153 | 116 |
| ETH/USDT:USDT | 4h | IS | 1.0x | 0.043 | 40 |
| ETH/USDT:USDT | 4h | IS | 1.5x | 0.029 | 40 |
| ETH/USDT:USDT | 4h | IS | 2.0x | 0.015 | 40 |
| ETH/USDT:USDT | 4h | OOS | 1.0x | 0.010 | 25 |
| ETH/USDT:USDT | 4h | OOS | 1.5x | 0.003 | 25 |
| ETH/USDT:USDT | 4h | OOS | 2.0x | -0.004 | 25 |
| ETH/USDT:USDT | 1d | IS | 1.0x | 0.013 | 6 |
| ETH/USDT:USDT | 1d | IS | 1.5x | 0.013 | 6 |
| ETH/USDT:USDT | 1d | IS | 2.0x | 0.012 | 6 |
| ETH/USDT:USDT | 1d | OOS | 1.0x | -0.024 | 6 |
| ETH/USDT:USDT | 1d | OOS | 1.5x | -0.025 | 6 |
| ETH/USDT:USDT | 1d | OOS | 2.0x | -0.026 | 6 |
| SOL/USDT:USDT | 15m | IS | 1.0x | -0.035 | 644 |
| SOL/USDT:USDT | 15m | IS | 1.5x | -0.319 | 644 |
| SOL/USDT:USDT | 15m | IS | 2.0x | -0.520 | 644 |
| SOL/USDT:USDT | 15m | OOS | 1.0x | -0.180 | 304 |
| SOL/USDT:USDT | 15m | OOS | 1.5x | -0.309 | 304 |
| SOL/USDT:USDT | 15m | OOS | 2.0x | -0.418 | 304 |
| SOL/USDT:USDT | 1h | IS | 1.0x | -0.097 | 175 |
| SOL/USDT:USDT | 1h | IS | 1.5x | -0.155 | 175 |
| SOL/USDT:USDT | 1h | IS | 2.0x | -0.208 | 175 |
| SOL/USDT:USDT | 1h | OOS | 1.0x | -0.077 | 95 |
| SOL/USDT:USDT | 1h | OOS | 1.5x | -0.111 | 95 |
| SOL/USDT:USDT | 1h | OOS | 2.0x | -0.143 | 95 |
| SOL/USDT:USDT | 4h | IS | 1.0x | 0.080 | 57 |
| SOL/USDT:USDT | 4h | IS | 1.5x | 0.068 | 57 |
| SOL/USDT:USDT | 4h | IS | 2.0x | 0.056 | 57 |
| SOL/USDT:USDT | 4h | OOS | 1.0x | -0.016 | 18 |
| SOL/USDT:USDT | 4h | OOS | 1.5x | -0.021 | 18 |
| SOL/USDT:USDT | 4h | OOS | 2.0x | -0.025 | 18 |
| SOL/USDT:USDT | 1d | IS | 1.0x | -0.001 | 5 |
| SOL/USDT:USDT | 1d | IS | 1.5x | -0.001 | 5 |
| SOL/USDT:USDT | 1d | IS | 2.0x | -0.002 | 5 |
| SOL/USDT:USDT | 1d | OOS | 1.0x | -0.026 | 5 |
| SOL/USDT:USDT | 1d | OOS | 1.5x | -0.026 | 5 |
| SOL/USDT:USDT | 1d | OOS | 2.0x | -0.027 | 5 |

## 롱/숏 분해 (비용 1.0x 기준)

| 심볼 | TF | 구간 | 방향 | 총수익률 | 거래수 | 승률 |
| -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | long | 0.046 | 245 | 0.506 |
| BTC/USDT:USDT | 15m | IS | short | -0.294 | 245 | 0.420 |
| BTC/USDT:USDT | 15m | OOS | long | -0.024 | 158 | 0.481 |
| BTC/USDT:USDT | 15m | OOS | short | -0.075 | 101 | 0.436 |
| BTC/USDT:USDT | 1h | IS | long | -0.018 | 68 | 0.456 |
| BTC/USDT:USDT | 1h | IS | short | 0.100 | 89 | 0.528 |
| BTC/USDT:USDT | 1h | OOS | long | 0.045 | 49 | 0.510 |
| BTC/USDT:USDT | 1h | OOS | short | 0.005 | 43 | 0.465 |
| BTC/USDT:USDT | 4h | IS | long | 0.012 | 22 | 0.455 |
| BTC/USDT:USDT | 4h | IS | short | 0.021 | 21 | 0.476 |
| BTC/USDT:USDT | 4h | OOS | long | -0.060 | 13 | 0.231 |
| BTC/USDT:USDT | 4h | OOS | short | -0.019 | 8 | 0.375 |
| BTC/USDT:USDT | 1d | IS | long | -0.010 | 1 | 0.000 |
| BTC/USDT:USDT | 1d | IS | short | -0.030 | 3 | 0.000 |
| BTC/USDT:USDT | 1d | OOS | long | -0.016 | 4 | 0.250 |
| BTC/USDT:USDT | 1d | OOS | short | -0.010 | 1 | 0.000 |
| ETH/USDT:USDT | 15m | IS | long | -0.033 | 269 | 0.476 |
| ETH/USDT:USDT | 15m | IS | short | -0.252 | 285 | 0.453 |
| ETH/USDT:USDT | 15m | OOS | long | 0.143 | 165 | 0.527 |
| ETH/USDT:USDT | 15m | OOS | short | -0.074 | 131 | 0.443 |
| ETH/USDT:USDT | 1h | IS | long | -0.040 | 78 | 0.449 |
| ETH/USDT:USDT | 1h | IS | short | 0.050 | 77 | 0.481 |
| ETH/USDT:USDT | 1h | OOS | long | 0.181 | 57 | 0.561 |
| ETH/USDT:USDT | 1h | OOS | short | 0.098 | 59 | 0.508 |
| ETH/USDT:USDT | 4h | IS | long | 0.023 | 19 | 0.474 |
| ETH/USDT:USDT | 4h | IS | short | 0.020 | 21 | 0.476 |
| ETH/USDT:USDT | 4h | OOS | long | 0.013 | 13 | 0.462 |
| ETH/USDT:USDT | 4h | OOS | short | -0.003 | 12 | 0.417 |
| ETH/USDT:USDT | 1d | IS | long | -0.006 | 3 | 0.333 |
| ETH/USDT:USDT | 1d | IS | short | 0.019 | 3 | 0.667 |
| ETH/USDT:USDT | 1d | OOS | long | -0.014 | 5 | 0.400 |
| ETH/USDT:USDT | 1d | OOS | short | -0.010 | 1 | 0.000 |
| SOL/USDT:USDT | 15m | IS | long | 0.207 | 325 | 0.492 |
| SOL/USDT:USDT | 15m | IS | short | -0.241 | 319 | 0.439 |
| SOL/USDT:USDT | 15m | OOS | long | -0.033 | 163 | 0.485 |
| SOL/USDT:USDT | 15m | OOS | short | -0.148 | 141 | 0.426 |
| SOL/USDT:USDT | 1h | IS | long | 0.010 | 89 | 0.438 |
| SOL/USDT:USDT | 1h | IS | short | -0.108 | 86 | 0.384 |
| SOL/USDT:USDT | 1h | OOS | long | -0.007 | 45 | 0.444 |
| SOL/USDT:USDT | 1h | OOS | short | -0.070 | 50 | 0.380 |
| SOL/USDT:USDT | 4h | IS | long | 0.019 | 27 | 0.444 |
| SOL/USDT:USDT | 4h | IS | short | 0.061 | 30 | 0.500 |
| SOL/USDT:USDT | 4h | OOS | long | 0.018 | 10 | 0.500 |
| SOL/USDT:USDT | 4h | OOS | short | -0.035 | 8 | 0.250 |
| SOL/USDT:USDT | 1d | IS | long | 0.005 | 2 | 0.500 |
| SOL/USDT:USDT | 1d | IS | short | -0.006 | 3 | 0.333 |
| SOL/USDT:USDT | 1d | OOS | long | -0.020 | 2 | 0.000 |
| SOL/USDT:USDT | 1d | OOS | short | -0.005 | 3 | 0.333 |

OOS 기준 롱 평균 총수익률=0.0307(유효 셀 9개), 숏 평균 총수익률=-0.0382(유효 셀 7개). **숏이 롱 수익을 갉아먹는다**

## 바이앤홀드 벤치마크

| 심볼 | TF | 구간 | 바이앤홀드 수익률 | 봉수 |
| -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | 2.769 | 70128 |
| BTC/USDT:USDT | 15m | OOS | -0.478 | 35064 |
| BTC/USDT:USDT | 1h | IS | 2.773 | 17532 |
| BTC/USDT:USDT | 1h | OOS | -0.479 | 8766 |
| BTC/USDT:USDT | 4h | IS | 2.805 | 4383 |
| BTC/USDT:USDT | 4h | OOS | -0.479 | 2191 |
| BTC/USDT:USDT | 1d | IS | 2.926 | 730 |
| BTC/USDT:USDT | 1d | OOS | -0.471 | 365 |
| ETH/USDT:USDT | 15m | IS | 0.478 | 70128 |
| ETH/USDT:USDT | 15m | OOS | -0.411 | 35064 |
| ETH/USDT:USDT | 1h | IS | 0.478 | 17532 |
| ETH/USDT:USDT | 1h | OOS | -0.413 | 8766 |
| ETH/USDT:USDT | 4h | IS | 0.497 | 4383 |
| ETH/USDT:USDT | 4h | OOS | -0.406 | 2191 |
| ETH/USDT:USDT | 1d | IS | 0.536 | 730 |
| ETH/USDT:USDT | 1d | OOS | -0.401 | 365 |
| SOL/USDT:USDT | 15m | IS | 4.648 | 70128 |
| SOL/USDT:USDT | 15m | OOS | -0.539 | 35064 |
| SOL/USDT:USDT | 1h | IS | 4.589 | 17532 |
| SOL/USDT:USDT | 1h | OOS | -0.543 | 8766 |
| SOL/USDT:USDT | 4h | IS | 4.778 | 4383 |
| SOL/USDT:USDT | 4h | OOS | -0.536 | 2191 |
| SOL/USDT:USDT | 1d | IS | 4.856 | 730 |
| SOL/USDT:USDT | 1d | OOS | -0.530 | 365 |

## OOS/워크포워드 (`backtest.ab_walkforward`, 변형 B = 신 엔진 채택 진입 방식)

| timeframe | num_windows | oos_num_trades | mean_oos_total_return | mean_oos_profit_factor | mean_return_gap | mean_oos_fill_rate |
| -- | -- | -- | -- | -- | -- | -- |
| 15m | 24 | 1697 | -0.0210 | 0.9314 | -0.0830 | 0.2874 |
| 1h | 21 | 457 | 0.0065 | 1.1235 | 0.0191 | 0.3319 |
| 4h | 21 | 113 | 0.0046 | 1.3817 | 0.0125 | 0.4073 |
| 1d | 24 | 8 | -0.0014 | 0.1392 | -0.0037 | 0.2500 |

원자료: `backtest/reports/wan50_ab_walkforward.csv`, `backtest/reports/wan50_ab_walkforward_summary.csv` (신 기본값으로 갱신됨).

## 매칭 널 판정

유효 셀 17개(거래 20건 미만 7개 제외) 중 0개가 p≤0.05 & 실제>무작위평균. 판정: **엣지 없다**

> OOS 셀만 보면: 8개가 유효 표본(거래 20건 이상)이다.

## 결론

신 엔진(WAN-81)에서도 매칭 널 대비 유의한 엣지가 있다는 증거는 **없다** — 볼린저 진입가·첫탭 무조건·고정 1.5R 익절·숏 활성화로 규칙이 크게 바뀌었지만, WAN-70(구 엔진)의 '진입 타이밍에 엣지 없음' 결론이 신 엔진에서도 유지된다.

OOS 기준 롱 평균 총수익률=0.0307(유효 셀 9개), 숏 평균 총수익률=-0.0382(유효 셀 7개). **숏이 롱 수익을 갉아먹는다**
