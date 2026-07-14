# WAN-71 엣지의 소재지 분해 — 청산 캐리 · 시장 베타 · 비용 취약성

3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS, 로컬 `data/ohlcv.db` 실데이터 3년. 재현: `python -m backtest.wan71_edge_decomposition`.
원자료: `backtest/reports/wan71_random_entry_current_exit.csv`(무작위 대조군), `backtest/reports/wan71_cost_sensitivity.csv`(비용 민감도), `backtest/reports/wan71_buy_hold.csv`(바이앤홀드).

> **기준값 각주**: 이 리포트는 `min_stop_distance_fraction=0.003`(WAN-79 저장소
> 기본값)으로 산출했다. WAN-68(+0.78% 평균 OOS)·WAN-70(매칭 널 검정)의 기존 수치는
> 하한 0 기준이라 이 리포트의 절대값과 직접 비교할 수 없다.

## 배경 · 세 가설

WAN-70이 매칭 널로 확인한 "진입 타이밍에 엣지 없음"과 WAN-68의 "무게이트 B안 평균 OOS +0.78%"는 모순이 아니라 하나의 질문이다: 플러스가 진입에서 오지 않는다면
어디서 오는가? 가설은 (1) 청산 규칙이 캐리, (2) 시장 베타, (3) 엣지가 실제로 없고
비용에 취약, 세 가지다(자세한 배경은 이 모듈의 docstring과 이슈 WAN-71 본문 참고).

## 무작위 진입 + 현행 청산 (게이트=current, WAN-70 매칭 널 재사용)

| 심볼 | TF | 구간 | 게이트 | 실제수익 | n | 무작위평균 | 95% CI | p |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | current | 0.048 | 144 | 0.297 | [-0.169, 0.826] | 0.725 |
| BTC/USDT:USDT | 15m | OOS | current | -0.072 | 80 | -0.048 | [-0.144, 0.042] | 0.715 |
| BTC/USDT:USDT | 1h | IS | current | 0.131 | 42 | 0.646 | [-0.150, 2.773] | 0.330 |
| BTC/USDT:USDT | 1h | OOS | current | 0.021 | 23 | -0.034 | [-0.101, 0.054] | 0.095 |
| BTC/USDT:USDT | 4h | IS | current | -0.021 | 12 | 0.255 | [-0.051, 1.560] | 0.845 |
| BTC/USDT:USDT | 4h | OOS | current | -0.061 | 8 | -0.061 | [-0.083, -0.031] | 0.595 |
| BTC/USDT:USDT | 1d | IS | current | -0.010 | 1 | -0.010 | [-0.010, -0.010] | 1.000 |
| BTC/USDT:USDT | 1d | OOS | current | -0.010 | 1 | -0.010 | [-0.010, -0.010] | 1.000 |
| ETH/USDT:USDT | 15m | IS | current | -0.136 | 147 | -0.141 | [-0.282, -0.003] | 0.465 |
| ETH/USDT:USDT | 15m | OOS | current | -0.025 | 90 | -0.059 | [-0.180, 0.078] | 0.300 |
| ETH/USDT:USDT | 1h | IS | current | 0.022 | 45 | 0.024 | [-0.126, 0.221] | 0.430 |
| ETH/USDT:USDT | 1h | OOS | current | -0.037 | 31 | -0.072 | [-0.163, 0.020] | 0.255 |
| ETH/USDT:USDT | 4h | IS | current | -0.012 | 14 | 0.044 | [-0.074, 0.169] | 0.755 |
| ETH/USDT:USDT | 4h | OOS | current | 0.017 | 4 | 0.014 | [-0.025, 0.049] | 0.460 |
| ETH/USDT:USDT | 1d | IS | current | -0.010 | 1 | -0.010 | [-0.010, -0.010] | 1.000 |
| ETH/USDT:USDT | 1d | OOS | current | -0.004 | 4 | 0.010 | [-0.004, 0.034] | 0.730 |
| SOL/USDT:USDT | 15m | IS | current | 0.371 | 204 | 3.322 | [-0.222, 7.289] | 0.695 |
| SOL/USDT:USDT | 15m | OOS | current | 0.383 | 89 | -0.002 | [-0.221, 0.396] | 0.035 |
| SOL/USDT:USDT | 1h | IS | current | -0.023 | 48 | 0.115 | [-0.175, 0.842] | 0.440 |
| SOL/USDT:USDT | 1h | OOS | current | 0.013 | 21 | -0.025 | [-0.105, 0.072] | 0.215 |
| SOL/USDT:USDT | 4h | IS | current | 0.135 | 20 | 0.359 | [0.062, 0.760] | 0.825 |
| SOL/USDT:USDT | 4h | OOS | current | -0.021 | 7 | -0.001 | [-0.043, 0.043] | 0.770 |
| SOL/USDT:USDT | 1d | IS | current | 0.001 | 2 | 0.002 | [-0.008, 0.013] | 0.495 |
| SOL/USDT:USDT | 1d | OOS | current | -0.020 | 2 | -0.017 | [-0.020, -0.009] | 0.635 |

## 숏 포함 변형 (게이트=with_short, WAN-64/69 재확인)

| 심볼 | TF | 구간 | 게이트 | 실제수익 | n | 무작위평균 | 95% CI | p |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | with_short | -0.119 | 271 | 0.143 | [-0.275, 0.754] | 0.725 |
| BTC/USDT:USDT | 15m | OOS | with_short | 0.017 | 124 | -0.004 | [-0.139, 0.181] | 0.355 |
| BTC/USDT:USDT | 1h | IS | with_short | 0.350 | 91 | 0.418 | [-0.170, 2.787] | 0.150 |
| BTC/USDT:USDT | 1h | OOS | with_short | 0.090 | 50 | -0.015 | [-0.103, 0.104] | 0.040 |
| BTC/USDT:USDT | 4h | IS | with_short | -0.014 | 26 | 0.195 | [-0.082, 1.587] | 0.765 |
| BTC/USDT:USDT | 4h | OOS | with_short | -0.107 | 13 | -0.024 | [-0.122, 0.119] | 0.955 |
| BTC/USDT:USDT | 1d | IS | with_short | -0.030 | 3 | -0.016 | [-0.016, -0.016] | 1.000 |
| BTC/USDT:USDT | 1d | OOS | with_short | -0.020 | 2 | -0.020 | [-0.020, -0.020] | 0.000 |
| ETH/USDT:USDT | 15m | IS | with_short | -0.162 | 315 | -0.205 | [-0.407, 0.028] | 0.305 |
| ETH/USDT:USDT | 15m | OOS | with_short | -0.101 | 164 | -0.087 | [-0.272, 0.177] | 0.480 |
| ETH/USDT:USDT | 1h | IS | with_short | -0.073 | 98 | -0.079 | [-0.254, 0.128] | 0.435 |
| ETH/USDT:USDT | 1h | OOS | with_short | -0.023 | 64 | -0.059 | [-0.173, 0.084] | 0.260 |
| ETH/USDT:USDT | 4h | IS | with_short | -0.086 | 28 | -0.004 | [-0.133, 0.116] | 0.880 |
| ETH/USDT:USDT | 4h | OOS | with_short | 0.015 | 10 | 0.084 | [-0.011, 0.177] | 0.890 |
| ETH/USDT:USDT | 1d | IS | with_short | -0.041 | 4 | -0.041 | [-0.041, -0.041] | 0.000 |
| ETH/USDT:USDT | 1d | OOS | with_short | -0.014 | 5 | -0.002 | [-0.014, 0.023] | 0.645 |
| SOL/USDT:USDT | 15m | IS | with_short | -0.021 | 387 | 3.024 | [-0.388, 7.451] | 0.655 |
| SOL/USDT:USDT | 15m | OOS | with_short | 0.296 | 181 | 0.206 | [-0.345, 1.319] | 0.395 |
| SOL/USDT:USDT | 1h | IS | with_short | -0.158 | 107 | 0.073 | [-0.244, 0.799] | 0.760 |
| SOL/USDT:USDT | 1h | OOS | with_short | -0.043 | 49 | -0.029 | [-0.167, 0.126] | 0.565 |
| SOL/USDT:USDT | 4h | IS | with_short | 0.138 | 38 | 0.314 | [-0.003, 0.742] | 0.670 |
| SOL/USDT:USDT | 4h | OOS | with_short | -0.092 | 14 | 0.013 | [-0.099, 0.180] | 0.960 |
| SOL/USDT:USDT | 1d | IS | with_short | 0.032 | 4 | 0.004 | [-0.006, 0.014] | 0.000 |
| SOL/USDT:USDT | 1d | OOS | with_short | -0.018 | 5 | -0.016 | [-0.050, 0.003] | 0.520 |

## 비용 민감도 (1.0x/1.5x/2.0x, 동일 후보 재시퀀싱)

| 심볼 | TF | 구간 | 배율 | 총수익률 | 거래수 |
| -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | 1.0x | 0.048 | 144 |
| BTC/USDT:USDT | 15m | IS | 1.5x | -0.044 | 144 |
| BTC/USDT:USDT | 15m | IS | 2.0x | -0.127 | 144 |
| BTC/USDT:USDT | 15m | OOS | 1.0x | -0.072 | 80 |
| BTC/USDT:USDT | 15m | OOS | 1.5x | -0.119 | 80 |
| BTC/USDT:USDT | 15m | OOS | 2.0x | -0.163 | 80 |
| BTC/USDT:USDT | 1h | IS | 1.0x | 0.131 | 42 |
| BTC/USDT:USDT | 1h | IS | 1.5x | 0.104 | 42 |
| BTC/USDT:USDT | 1h | IS | 2.0x | 0.078 | 42 |
| BTC/USDT:USDT | 1h | OOS | 1.0x | 0.021 | 23 |
| BTC/USDT:USDT | 1h | OOS | 1.5x | 0.008 | 23 |
| BTC/USDT:USDT | 1h | OOS | 2.0x | -0.005 | 23 |
| BTC/USDT:USDT | 4h | IS | 1.0x | -0.021 | 12 |
| BTC/USDT:USDT | 4h | IS | 1.5x | -0.025 | 12 |
| BTC/USDT:USDT | 4h | IS | 2.0x | -0.029 | 12 |
| BTC/USDT:USDT | 4h | OOS | 1.0x | -0.061 | 8 |
| BTC/USDT:USDT | 4h | OOS | 1.5x | -0.064 | 8 |
| BTC/USDT:USDT | 4h | OOS | 2.0x | -0.066 | 8 |
| BTC/USDT:USDT | 1d | IS | 1.0x | -0.010 | 1 |
| BTC/USDT:USDT | 1d | IS | 1.5x | -0.010 | 1 |
| BTC/USDT:USDT | 1d | IS | 2.0x | -0.010 | 1 |
| BTC/USDT:USDT | 1d | OOS | 1.0x | -0.010 | 1 |
| BTC/USDT:USDT | 1d | OOS | 1.5x | -0.010 | 1 |
| BTC/USDT:USDT | 1d | OOS | 2.0x | -0.010 | 1 |
| ETH/USDT:USDT | 15m | IS | 1.0x | -0.136 | 147 |
| ETH/USDT:USDT | 15m | IS | 1.5x | -0.209 | 147 |
| ETH/USDT:USDT | 15m | IS | 2.0x | -0.276 | 147 |
| ETH/USDT:USDT | 15m | OOS | 1.0x | -0.025 | 90 |
| ETH/USDT:USDT | 15m | OOS | 1.5x | -0.077 | 90 |
| ETH/USDT:USDT | 15m | OOS | 2.0x | -0.126 | 90 |
| ETH/USDT:USDT | 1h | IS | 1.0x | 0.022 | 45 |
| ETH/USDT:USDT | 1h | IS | 1.5x | 0.001 | 45 |
| ETH/USDT:USDT | 1h | IS | 2.0x | -0.019 | 45 |
| ETH/USDT:USDT | 1h | OOS | 1.0x | -0.037 | 31 |
| ETH/USDT:USDT | 1h | OOS | 1.5x | -0.050 | 31 |
| ETH/USDT:USDT | 1h | OOS | 2.0x | -0.064 | 31 |
| ETH/USDT:USDT | 4h | IS | 1.0x | -0.012 | 14 |
| ETH/USDT:USDT | 4h | IS | 1.5x | -0.016 | 14 |
| ETH/USDT:USDT | 4h | IS | 2.0x | -0.020 | 14 |
| ETH/USDT:USDT | 4h | OOS | 1.0x | 0.017 | 4 |
| ETH/USDT:USDT | 4h | OOS | 1.5x | 0.016 | 4 |
| ETH/USDT:USDT | 4h | OOS | 2.0x | 0.014 | 4 |
| ETH/USDT:USDT | 1d | IS | 1.0x | -0.010 | 1 |
| ETH/USDT:USDT | 1d | IS | 1.5x | -0.011 | 1 |
| ETH/USDT:USDT | 1d | IS | 2.0x | -0.011 | 1 |
| ETH/USDT:USDT | 1d | OOS | 1.0x | -0.004 | 4 |
| ETH/USDT:USDT | 1d | OOS | 1.5x | -0.004 | 4 |
| ETH/USDT:USDT | 1d | OOS | 2.0x | -0.005 | 4 |
| SOL/USDT:USDT | 15m | IS | 1.0x | 0.371 | 204 |
| SOL/USDT:USDT | 15m | IS | 1.5x | 0.221 | 204 |
| SOL/USDT:USDT | 15m | IS | 2.0x | 0.088 | 204 |
| SOL/USDT:USDT | 15m | OOS | 1.0x | 0.383 | 89 |
| SOL/USDT:USDT | 15m | OOS | 1.5x | 0.311 | 89 |
| SOL/USDT:USDT | 15m | OOS | 2.0x | 0.243 | 89 |
| SOL/USDT:USDT | 1h | IS | 1.0x | -0.023 | 48 |
| SOL/USDT:USDT | 1h | IS | 1.5x | -0.041 | 48 |
| SOL/USDT:USDT | 1h | IS | 2.0x | -0.059 | 48 |
| SOL/USDT:USDT | 1h | OOS | 1.0x | 0.013 | 21 |
| SOL/USDT:USDT | 1h | OOS | 1.5x | 0.004 | 21 |
| SOL/USDT:USDT | 1h | OOS | 2.0x | -0.005 | 21 |
| SOL/USDT:USDT | 4h | IS | 1.0x | 0.135 | 20 |
| SOL/USDT:USDT | 4h | IS | 1.5x | 0.131 | 20 |
| SOL/USDT:USDT | 4h | IS | 2.0x | 0.126 | 20 |
| SOL/USDT:USDT | 4h | OOS | 1.0x | -0.021 | 7 |
| SOL/USDT:USDT | 4h | OOS | 1.5x | -0.023 | 7 |
| SOL/USDT:USDT | 4h | OOS | 2.0x | -0.024 | 7 |
| SOL/USDT:USDT | 1d | IS | 1.0x | 0.001 | 2 |
| SOL/USDT:USDT | 1d | IS | 1.5x | 0.001 | 2 |
| SOL/USDT:USDT | 1d | IS | 2.0x | 0.001 | 2 |
| SOL/USDT:USDT | 1d | OOS | 1.0x | -0.020 | 2 |
| SOL/USDT:USDT | 1d | OOS | 1.5x | -0.020 | 2 |
| SOL/USDT:USDT | 1d | OOS | 2.0x | -0.020 | 2 |

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

## 가설별 판정

| 가설 | 근거 수치 | 판정 |
| -- | -- | -- |
| 1. 청산 규칙이 캐리 | 실제 OOS평균=0.0473, 무작위진입+현행청산 OOS평균=-0.0401 | 기각/약함 |
| 2. 시장 베타 | 전략 OOS평균=0.0473, 바이앤홀드 OOS평균=-0.4771 | 기각(전략이 베타 상회) |
| 3. 엣지 없음(비용 취약) | 1.0배=0.0473, 1.5배=0.0129, 2.0배=-0.0200 | 부분 지지(2배에서 마이너스) |

> 집계는 실제 거래 20건 이상인 OOS 셀(게이트=current)만 포함한 단순 평균이다.

## 결론

OOS 유효 셀 6개(거래 20건 이상, 게이트=current) 기준 실제 평균 총수익률은 0.0473, 무작위 진입+현행 청산 매칭 널 평균은 -0.0401, 바이앤홀드 평균은 -0.4771이다. 관측된 플러스는 비용에 취약해 엣지로 보기 어렵다(**가설 3**) — 비용을 1.5~2배로 올리면 평균이 1.5배=0.0129, 2.0배=-0.0200로 마이너스에 가까워지거나 전환한다. 숏을 포함(게이트=with_short)해도 평균 OOS는 0.0394로 현행(롱온리, 0.0473) 대비 개선되지 않아, WAN-64/69의 롱온리 채택 판단과 이 프레임의 결과가 일치한다.
