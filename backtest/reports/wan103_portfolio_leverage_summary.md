# WAN-103: 동시 다중 포지션 + 포트폴리오 레버리지 — 레버리지 스윕·단일 포지션 대조

재현: `uv run python -m backtest.wan103_portfolio_leverage_report`

채택 기본값(`ConfluenceParams()` = 롱 온리·지정가·`first_tap_free`·고정 1.5R) 위에서 **포지션 제약만** 바꿔 돌린 결과다. 전략 파라미터는 하나도 건드리지 않았다. 설계 결정 5개와 결론은 [`docs/decisions/wan103.md`](../../docs/decisions/wan103.md).

> ⚠️ **`total_return`은 공식 렌즈 `baseline`(닿으면 체결) 기준**이라 상한으로 읽어야 한다(WAN-104). 체결 가정 민감도는 이 리포트의 축이 아니다 — 이 표가 묻는 건 *같은 체결 가정에서* 포지션 제약을 풀면 무엇이 달라지는가다.

## series 범위 — (심볼, TF)가 자기 자본으로 도는 현행 백테스트 조건

`single`이 채택 엔진 그 자체(동시 1포지션)이고, `lev_*`가 같은 셋업 풀을 동시 다중 포지션으로 배치한 것이다. 두 행의 차이는 **오직 포지션 제약**이다.

| symbol | timeframe | segment | scenario | num_trades | total_return | max_drawdown | win_rate | peak_concurrency | max_concurrent_risk_ratio | liquidations |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | is | single | 421 | 34.34 | 11.15 | 52.73 | 1 | — | — |
| BTC/USDT:USDT | 15m | is | lev_1 | 442 | 32.31 | 13.74 | 52.26 | 3 | 0.024 | 0.0 |
| BTC/USDT:USDT | 15m | is | lev_2 | 457 | 49.12 | 23.31 | 52.08 | 5 | 0.041 | 0.0 |
| BTC/USDT:USDT | 15m | is | lev_3 | 460 | 49.98 | 28.5 | 51.96 | 6 | 0.056 | 0.0 |
| BTC/USDT:USDT | 15m | is | peak_N | 461 | 48.05 | 29.7 | 51.84 | 6 | 0.06 | 0.0 |
| BTC/USDT:USDT | 15m | oos | single | 215 | 4.14 | 11.42 | 48.37 | 1 | — | — |
| BTC/USDT:USDT | 15m | oos | lev_1 | 226 | 5.66 | 12.68 | 48.67 | 2 | 0.02 | 0.0 |
| BTC/USDT:USDT | 15m | oos | lev_2 | 232 | 2.16 | 16.71 | 48.71 | 3 | 0.024 | 0.0 |
| BTC/USDT:USDT | 15m | oos | lev_3 | 233 | 1.39 | 17.03 | 48.5 | 3 | 0.028 | 0.0 |
| BTC/USDT:USDT | 1h | is | single | 131 | 3.55 | 9.94 | 48.85 | 1 | — | — |
| BTC/USDT:USDT | 1h | is | lev_1 | 137 | 2.57 | 12.06 | 48.91 | 3 | 0.022 | 0.0 |
| BTC/USDT:USDT | 1h | is | lev_2 | 139 | 8.41 | 14.12 | 48.2 | 3 | 0.03 | 0.0 |
| BTC/USDT:USDT | 1h | is | lev_3 | 139 | 7.78 | 14.52 | 48.2 | 3 | 0.03 | 0.0 |
| BTC/USDT:USDT | 1h | oos | single | 74 | 8.23 | 5.2 | 48.65 | 1 | — | — |
| BTC/USDT:USDT | 1h | oos | lev_1 | 80 | 4.57 | 6.19 | 46.25 | 3 | 0.02 | 0.0 |
| BTC/USDT:USDT | 1h | oos | lev_2 | 80 | 2.72 | 9.42 | 46.25 | 3 | 0.027 | 0.0 |
| BTC/USDT:USDT | 1h | oos | lev_3 | 80 | 1.66 | 10.34 | 46.25 | 3 | 0.03 | 0.0 |
| BTC/USDT:USDT | 4h | is | single | 31 | 6.68 | 4.85 | 51.61 | 1 | — | — |
| BTC/USDT:USDT | 4h | is | lev_1 | 31 | 6.68 | 4.85 | 51.61 | 1 | 0.01 | 0.0 |
| BTC/USDT:USDT | 4h | is | lev_2 | 31 | 6.88 | 4.85 | 51.61 | 1 | 0.01 | 0.0 |
| BTC/USDT:USDT | 4h | is | lev_3 | 31 | 6.88 | 4.85 | 51.61 | 1 | 0.01 | 0.0 |
| BTC/USDT:USDT | 4h | oos | single | 15 | -3.94 | 4.22 | 33.33 | 1 | — | — |
| BTC/USDT:USDT | 4h | oos | lev_1 | 18 | -6.42 | 6.42 | 27.78 | 2 | 0.02 | 0.0 |
| BTC/USDT:USDT | 4h | oos | lev_2 | 18 | -6.69 | 6.69 | 27.78 | 2 | 0.02 | 0.0 |
| BTC/USDT:USDT | 4h | oos | lev_3 | 18 | -6.69 | 6.69 | 27.78 | 2 | 0.02 | 0.0 |
| ETH/USDT:USDT | 15m | is | single | 513 | 13.72 | 21.22 | 49.71 | 1 | — | — |
| ETH/USDT:USDT | 15m | is | lev_1 | 531 | 19.86 | 21.55 | 50.28 | 3 | 0.022 | 0.0 |
| ETH/USDT:USDT | 15m | is | lev_2 | 539 | 39.98 | 23.67 | 49.91 | 3 | 0.03 | 0.0 |
| ETH/USDT:USDT | 15m | is | lev_3 | 542 | 37.69 | 27.66 | 49.63 | 4 | 0.036 | 0.0 |
| ETH/USDT:USDT | 15m | is | peak_N | 542 | 35.86 | 28.7 | 49.63 | 4 | 0.04 | 0.0 |
| ETH/USDT:USDT | 15m | oos | single | 274 | 51.48 | 6.29 | 55.11 | 1 | — | — |
| ETH/USDT:USDT | 15m | oos | lev_1 | 296 | 51.34 | 6.67 | 55.07 | 4 | 0.039 | 0.0 |
| ETH/USDT:USDT | 15m | oos | lev_2 | 308 | 79.58 | 10.72 | 55.19 | 5 | 0.04 | 0.0 |
| ETH/USDT:USDT | 15m | oos | lev_3 | 309 | 90.37 | 12.3 | 55.02 | 5 | 0.05 | 0.0 |
| ETH/USDT:USDT | 15m | oos | peak_N | 309 | 89.08 | 12.81 | 55.02 | 5 | 0.05 | 0.0 |
| ETH/USDT:USDT | 1h | is | single | 148 | 16.55 | 12.08 | 51.35 | 1 | — | — |
| ETH/USDT:USDT | 1h | is | lev_1 | 156 | 18.58 | 12.08 | 51.28 | 3 | 0.03 | 0.0 |
| ETH/USDT:USDT | 1h | is | lev_2 | 156 | 25.74 | 13.61 | 51.28 | 3 | 0.03 | 0.0 |
| ETH/USDT:USDT | 1h | is | lev_3 | 157 | 28.35 | 13.29 | 51.59 | 3 | 0.03 | 0.0 |
| ETH/USDT:USDT | 1h | oos | single | 78 | 16.52 | 7.26 | 55.13 | 1 | — | — |
| ETH/USDT:USDT | 1h | oos | lev_1 | 83 | 13.46 | 9.18 | 53.01 | 3 | 0.03 | 0.0 |
| ETH/USDT:USDT | 1h | oos | lev_2 | 83 | 18.16 | 8.73 | 53.01 | 3 | 0.03 | 0.0 |
| ETH/USDT:USDT | 1h | oos | lev_3 | 83 | 19.34 | 8.6 | 53.01 | 3 | 0.03 | 0.0 |
| ETH/USDT:USDT | 4h | is | single | 30 | 5.08 | 4.01 | 50.0 | 1 | — | — |
| ETH/USDT:USDT | 4h | is | lev_1 | 30 | 5.08 | 4.01 | 50.0 | 1 | 0.01 | 0.0 |
| ETH/USDT:USDT | 4h | is | lev_2 | 30 | 5.43 | 4.23 | 50.0 | 1 | 0.01 | 0.0 |
| ETH/USDT:USDT | 4h | is | lev_3 | 30 | 5.43 | 4.23 | 50.0 | 1 | 0.01 | 0.0 |
| ETH/USDT:USDT | 4h | oos | single | 17 | 2.99 | 3.07 | 52.94 | 1 | — | — |
| ETH/USDT:USDT | 4h | oos | lev_1 | 18 | 1.92 | 3.07 | 50.0 | 2 | 0.02 | 0.0 |
| ETH/USDT:USDT | 4h | oos | lev_2 | 18 | 2.91 | 3.07 | 50.0 | 2 | 0.02 | 0.0 |
| ETH/USDT:USDT | 4h | oos | lev_3 | 18 | 3.24 | 3.07 | 50.0 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 15m | is | single | 609 | 86.57 | 10.24 | 51.56 | 1 | — | — |
| SOL/USDT:USDT | 15m | is | lev_1 | 628 | 85.48 | 10.24 | 51.27 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 15m | is | lev_2 | 628 | 125.05 | 12.95 | 51.27 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 15m | is | lev_3 | 628 | 127.48 | 13.39 | 51.27 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 15m | oos | single | 283 | 14.18 | 12.16 | 50.88 | 1 | — | — |
| SOL/USDT:USDT | 15m | oos | lev_1 | 295 | 11.52 | 12.16 | 50.17 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 15m | oos | lev_2 | 296 | 20.48 | 15.8 | 50.0 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 15m | oos | lev_3 | 296 | 24.66 | 15.62 | 50.0 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 1h | is | single | 157 | 28.56 | 6.34 | 51.59 | 1 | — | — |
| SOL/USDT:USDT | 1h | is | lev_1 | 173 | 29.7 | 10.84 | 50.87 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 1h | is | lev_2 | 173 | 35.19 | 11.29 | 50.87 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 1h | is | lev_3 | 173 | 36.13 | 11.29 | 50.87 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 1h | oos | single | 65 | -0.21 | 5.41 | 44.62 | 1 | — | — |
| SOL/USDT:USDT | 1h | oos | lev_1 | 72 | -4.6 | 8.12 | 41.67 | 3 | 0.025 | 0.0 |
| SOL/USDT:USDT | 1h | oos | lev_2 | 72 | -4.16 | 8.03 | 41.67 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 1h | oos | lev_3 | 72 | -4.01 | 7.88 | 41.67 | 3 | 0.03 | 0.0 |
| SOL/USDT:USDT | 4h | is | single | 40 | 3.3 | 9.88 | 45.0 | 1 | — | — |
| SOL/USDT:USDT | 4h | is | lev_1 | 41 | 4.79 | 8.57 | 46.34 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 4h | is | lev_2 | 41 | 4.79 | 8.57 | 46.34 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 4h | is | lev_3 | 41 | 4.79 | 8.57 | 46.34 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 4h | oos | single | 15 | 9.49 | 1.07 | 66.67 | 1 | — | — |
| SOL/USDT:USDT | 4h | oos | lev_1 | 17 | 12.61 | 1.07 | 70.59 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 4h | oos | lev_2 | 17 | 12.76 | 1.07 | 70.59 | 2 | 0.02 | 0.0 |
| SOL/USDT:USDT | 4h | oos | lev_3 | 17 | 12.76 | 1.07 | 70.59 | 2 | 0.02 | 0.0 |

## pooled 범위 — 유니버스 전체가 자본 하나를 공유(라이브 조건)

`single`은 유니버스 전체가 포지션 1개를 나눠 쓰는 WAN-83 global 조건이다. `peak_N`의 N은 **IS에서 잰 자연 겹침 최댓값**이며, OOS 행은 그 N을 그대로 적용한 검증이다(OOS에서 잰 N을 OOS에 쓰면 look-ahead다).

| universe | segment | scenario | leverage | num_trades | total_return | max_drawdown | win_rate | sharpe | peak_concurrency | max_open_notional_ratio | max_concurrent_risk_ratio | liquidations |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 1h_3sym | is | single | 1.0 | 314 | 42.14 | 12.42 | 50.96 | 10.1 | 1 | 1.0 | 0.01 | 0.0 |
| 1h_3sym | is | lev_1 | 1.0 | 437 | 36.39 | 18.33 | 48.97 | 6.85 | 4 | 1.0 | 0.04 | 0.0 |
| 1h_3sym | is | lev_2 | 2.0 | 464 | 66.1 | 18.4 | 50.22 | 9.12 | 5 | 2.0 | 0.05 | 0.0 |
| 1h_3sym | is | lev_3 | 3.0 | 468 | 81.02 | 16.83 | 50.21 | 10.21 | 5 | 3.0 | 0.05 | 0.0 |
| 1h_3sym | is | peak_N | 5.0 | 469 | 86.96 | 16.47 | 50.32 | 10.65 | 5 | 5.0 | 0.05 | 0.0 |
| 1h_3sym | oos | single | 1.0 | 144 | 30.39 | 6.12 | 54.17 | 16.06 | 1 | 1.0 | 0.01 | 0.0 |
| 1h_3sym | oos | lev_1 | 1.0 | 211 | 20.48 | 9.39 | 49.29 | 8.43 | 4 | 1.0 | 0.037 | 0.0 |
| 1h_3sym | oos | lev_2 | 2.0 | 230 | 20.65 | 12.27 | 47.83 | 6.97 | 6 | 2.0 | 0.06 | 0.0 |
| 1h_3sym | oos | lev_3 | 3.0 | 234 | 19.49 | 13.85 | 47.44 | 6.35 | 6 | 3.0 | 0.06 | 0.0 |
| 1h_3sym | oos | peak_N | 5.0 | 235 | 15.13 | 16.11 | 47.23 | 5.09 | 6 | 5.0 | 0.06 | 0.0 |
| multi_tf | is | single | 1.0 | 1076 | 172.79 | 15.89 | 52.32 | 18.77 | 1 | 1.0 | 0.01 | 0.0 |
| multi_tf | is | lev_1 | 1.0 | 1627 | 238.61 | 20.37 | 51.44 | 16.65 | 4 | 1.0 | 0.04 | 0.0 |
| multi_tf | is | lev_2 | 2.0 | 1940 | 627.59 | 23.07 | 51.08 | 18.44 | 6 | 2.0 | 0.053 | 0.0 |
| multi_tf | is | lev_3 | 3.0 | 2092 | 774.23 | 28.35 | 50.86 | 17.49 | 7 | 3.0 | 0.068 | 0.0 |
| multi_tf | is | peak_N | 11.0 | 2202 | 795.47 | 30.06 | 50.68 | 16.14 | 11 | 10.741 | 0.11 | 0.0 |
| multi_tf | oos | single | 1.0 | 497 | 34.11 | 14.89 | 49.9 | 12.32 | 1 | 1.0 | 0.01 | 0.0 |
| multi_tf | oos | lev_1 | 1.0 | 791 | 47.92 | 20.97 | 50.95 | 11.71 | 6 | 1.0 | 0.043 | 0.0 |
| multi_tf | oos | lev_2 | 2.0 | 957 | 108.28 | 28.98 | 51.1 | 14.44 | 8 | 2.0 | 0.079 | 0.0 |
| multi_tf | oos | lev_3 | 3.0 | 1047 | 188.55 | 31.25 | 51.29 | 17.35 | 10 | 3.0 | 0.104 | 0.0 |
| multi_tf | oos | peak_N | 11.0 | 1126 | 167.8 | 35.5 | 50.44 | 14.32 | 12 | 9.075 | 0.12 | 0.0 |

## 겹침 분포 — 동시 k개를 들고 있던 시간 비중 (명목 상한 없이 잰 자연 수요)

N(= `peak_concurrency`)이 **얼마나 드문 사건인지**를 보여준다. N은 최댓값이라 단 한 순간에만 성립할 수 있고, 그 한 순간에 맞춰 레버리지를 정하면 나머지 시간 전체가 과도한 상한 아래에서 도는 셈이다.

| universe | segment | concurrency | time_share | peak_concurrency |
| -- | -- | -- | -- | -- |
| 1h_3sym | is | 0 | 72.46 | 5 |
| 1h_3sym | is | 1 | 18.9 | 5 |
| 1h_3sym | is | 2 | 7.13 | 5 |
| 1h_3sym | is | 3 | 0.85 | 5 |
| 1h_3sym | is | 4 | 0.54 | 5 |
| 1h_3sym | is | 5 | 0.13 | 5 |
| 1h_3sym | oos | 0 | 70.94 | 6 |
| 1h_3sym | oos | 1 | 18.28 | 6 |
| 1h_3sym | oos | 2 | 8.01 | 6 |
| 1h_3sym | oos | 3 | 1.68 | 6 |
| 1h_3sym | oos | 4 | 0.68 | 6 |
| 1h_3sym | oos | 5 | 0.28 | 6 |
| 1h_3sym | oos | 6 | 0.14 | 6 |
| multi_tf | is | 0 | 50.21 | 11 |
| multi_tf | is | 1 | 25.91 | 11 |
| multi_tf | is | 2 | 13.6 | 11 |
| multi_tf | is | 3 | 5.8 | 11 |
| multi_tf | is | 4 | 2.74 | 11 |
| multi_tf | is | 5 | 1.08 | 11 |
| multi_tf | is | 6 | 0.53 | 11 |
| multi_tf | is | 7 | 0.08 | 11 |
| multi_tf | is | 8 | 0.05 | 11 |
| multi_tf | is | 9 | 0.01 | 11 |
| multi_tf | is | 10 | 0.0 | 11 |
| multi_tf | is | 11 | 0.0 | 11 |
| multi_tf | oos | 0 | 42.18 | 12 |
| multi_tf | oos | 1 | 23.69 | 12 |
| multi_tf | oos | 2 | 15.65 | 12 |
| multi_tf | oos | 3 | 8.47 | 12 |
| multi_tf | oos | 4 | 3.9 | 12 |
| multi_tf | oos | 5 | 2.9 | 12 |
| multi_tf | oos | 6 | 1.83 | 12 |
| multi_tf | oos | 7 | 0.7 | 12 |
| multi_tf | oos | 8 | 0.31 | 12 |
| multi_tf | oos | 9 | 0.18 | 12 |
| multi_tf | oos | 10 | 0.09 | 12 |
| multi_tf | oos | 11 | 0.06 | 12 |
| multi_tf | oos | 12 | 0.03 | 12 |

