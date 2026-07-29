# WAN-150 즉사 부검 — 손절을 「즉사」와 「애매 실패」로 갈라 본다 (WAN-117 3분류판)

9종목 × 15m, 1h, 4h, 못 박은 6년 창 **2020-09-15 ~ 2026-07-22**, 오늘의 채택 기본값(`ConfluenceParams()` · `OrderBlockParams()` — 존 지정가 offset 2bp · `intrabar_live` 밴드 · `unconditional` 게이트 · 존폭 필터 1.28 · 분리 존 · 고정 1.5R · 롱 온리) · 공식 렌즈 `baseline`(WAN-128 단독). 3분류: 승자=1.5R 익절 · 즉사=손절 & MFE<0.5R · 애매=손절 & 0.5R≤MFE. END_OF_DATA·MFE 결측 제외. 재현: `python -m backtest.wan150_instant_death_autopsy`. 분위 원자료: `backtest/reports/wan150_quantile.csv`.

## §0 라벨 재생성 검산 (오늘 엔진)

라벨링된 거래 **10623건** = 즉사 1194 · 애매 3283 · 승자 6146. 시퀀서 거래 10623건 = 라벨 10623 + END_OF_DATA 0 + MFE결측 0 + 특징결측 0. **`sequenced`는 채택 엔진(인자 없는 `backtest.run`)의 num_trades와 일치해야 한다**(검산: `--checksum`).

| TF | 구간 | n | 즉사% | 애매% | 승자% |
| -- | -- | -- | -- | -- | -- |
| 15m | is | 5280 | 9.6% | 32.1% | 58.2% |
| 15m | oos | 2567 | 14.1% | 30.7% | 55.2% |
| 1h | is | 1448 | 10.5% | 27.9% | 61.6% |
| 1h | oos | 769 | 14.0% | 31.1% | 54.9% |
| 4h | is | 344 | 11.0% | 25.6% | 63.4% |
| 4h | oos | 215 | 11.6% | 31.2% | 57.2% |

## §1 게이트 판정 — 즉사가 진입 시점에 보이는가

* **15m**: **(a) 즉사 축에서 무작위를 넘는 특징 있음** — 주 검정 Bonferroni 생존 `zone_width_atr`, `freshness_bars`, `approach_mom`, `tap_rsi`; 실무 문턱(즉사 대 승자, OOS 순열 p<0.05) 생존 `trend_dev`, `volume_pctl`, `zone_width_atr`, `freshness_bars`, `approach_mom`, `tap_rsi`. §2/§3의 손익 효과를 재검할 근거가 있다(단 「선별」 대 「기하/가격」은 미분리).
* **1h**: **(a) 즉사 축에서 무작위를 넘는 특징 있음** — 주 검정 Bonferroni 생존 `zone_width_atr`; 실무 문턱(즉사 대 승자, OOS 순열 p<0.05) 생존 `zone_width_atr`, `freshness_bars`, `tap_rsi`. §2/§3의 손익 효과를 재검할 근거가 있다(단 「선별」 대 「기하/가격」은 미분리).
* **4h**: **(a) 즉사 축에서 무작위를 넘는 특징 있음** — 실무 문턱(즉사 대 승자, OOS 순열 p<0.05) 생존 `zone_width_atr`. §2/§3의 손익 효과를 재검할 근거가 있다(단 「선별」 대 「기하/가격」은 미분리).

📌 **일부 TF에서 즉사 축이 무작위를 넘었다(a).** §2·§3에서 손익·기하를 재검할 근거가 있으나, ⚠️ **「선별」과 「기하/가격」은 이 표가 못 가른다**(WAN-117 §1과 같은 자리) — 채택은 후속 이슈(사용자 결정)의 몫이다.

## §1 — 11개 특징 × 즉사 축 두 검정 (심볼 층화 순열, 2000회)

corr(특징, 즉사)>0 = 특징이 클수록 더 즉사. `주검정` = 즉사 대 나머지(Bonferroni 자), `실무` = 즉사 대 승자(부분집합, α=0.05 무작위 초과). 유효 셀 = 거래 20건 이상. 가설방향 ○=일치.

| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `trend_dev` | 5280 | 9.6% | +0.024 | 0.0900 | +0.049 | 0.0035 | ✗ |
| 15m | is | `volume_pctl` | 5280 | 9.6% | -0.042 | 0.0010 | -0.058 | 0.0000 | ○ |
| 15m | is | `vol_balance` | 5280 | 9.6% | +0.022 | 0.1305 | +0.030 | 0.0895 | · |
| 15m | is | `rsi_slope_5_3` | 5280 | 9.6% | +0.023 | 0.0880 | +0.036 | 0.0255 | ✗ |
| 15m | is | `rsi_slope_10_5` | 5280 | 9.6% | +0.034 | 0.0125 | +0.042 | 0.0125 | ✗ |
| 15m | is | `rsi_slope_14_5` | 5280 | 9.6% | +0.017 | 0.2145 | +0.024 | 0.1435 | ✗ |
| 15m | is | `zone_width_atr` | 5280 | 9.6% | +0.131 | 0.0000 | +0.179 | 0.0000 | · |
| 15m | is | `freshness_bars` | 5280 | 9.6% | -0.024 | 0.0685 | -0.036 | 0.0225 | ✗ |
| 15m | is | `prior_taps` | 5280 | 9.6% | +0.039 | 0.0095 | +0.048 | 0.0055 | ○ |
| 15m | is | `approach_mom` | 5280 | 9.6% | +0.045 | 0.0005 | +0.073 | 0.0000 | ✗ |
| 15m | is | `tap_rsi` | 5280 | 9.6% | +0.057 | 0.0000 | +0.090 | 0.0000 | ✗ |
| 15m | oos | `trend_dev` | 2567 | 14.1% | +0.049 | 0.0105 | +0.075 | 0.0020 | ✗ |
| 15m | oos | `volume_pctl` | 2567 | 14.1% | -0.049 | 0.0960 | -0.072 | 0.0385 | ○ |
| 15m | oos | `vol_balance` | 2567 | 14.1% | -0.012 | 0.5720 | +0.001 | 0.9615 | · |
| 15m | oos | `rsi_slope_5_3` | 2567 | 14.1% | +0.016 | 0.4100 | +0.026 | 0.2690 | ✗ |
| 15m | oos | `rsi_slope_10_5` | 2567 | 14.1% | +0.015 | 0.4480 | +0.025 | 0.2805 | ✗ |
| 15m | oos | `rsi_slope_14_5` | 2567 | 14.1% | +0.017 | 0.3815 | +0.026 | 0.2755 | ✗ |
| 15m | oos | `zone_width_atr` | 2567 | 14.1% | +0.166 | 0.0000 | +0.208 | 0.0000 | · |
| 15m | oos | `freshness_bars` | 2567 | 14.1% | -0.053 | 0.0040 | -0.071 | 0.0015 | ✗ |
| 15m | oos | `prior_taps` | 2567 | 14.1% | +0.013 | 0.5215 | +0.010 | 0.6720 | ○ |
| 15m | oos | `approach_mom` | 2567 | 14.1% | +0.078 | 0.0000 | +0.107 | 0.0000 | ✗ |
| 15m | oos | `tap_rsi` | 2567 | 14.1% | +0.102 | 0.0000 | +0.137 | 0.0000 | ✗ |
| 1h | is | `trend_dev` | 1448 | 10.5% | -0.015 | 0.6005 | -0.001 | 0.9690 | ○ |
| 1h | is | `volume_pctl` | 1448 | 10.5% | -0.031 | 0.2465 | -0.042 | 0.1660 | ○ |
| 1h | is | `vol_balance` | 1448 | 10.5% | +0.020 | 0.4640 | +0.022 | 0.5210 | · |
| 1h | is | `rsi_slope_5_3` | 1448 | 10.5% | +0.017 | 0.5360 | +0.019 | 0.5245 | ✗ |
| 1h | is | `rsi_slope_10_5` | 1448 | 10.5% | +0.007 | 0.7915 | -0.002 | 0.9500 | ✗ |
| 1h | is | `rsi_slope_14_5` | 1448 | 10.5% | +0.027 | 0.3085 | +0.028 | 0.3670 | ✗ |
| 1h | is | `zone_width_atr` | 1448 | 10.5% | +0.153 | 0.0000 | +0.211 | 0.0000 | · |
| 1h | is | `freshness_bars` | 1448 | 10.5% | -0.021 | 0.4070 | -0.032 | 0.2965 | ✗ |
| 1h | is | `prior_taps` | 1448 | 10.5% | +0.050 | 0.0595 | +0.056 | 0.0740 | ○ |
| 1h | is | `approach_mom` | 1448 | 10.5% | +0.031 | 0.2485 | +0.046 | 0.1270 | ✗ |
| 1h | is | `tap_rsi` | 1448 | 10.5% | +0.032 | 0.2350 | +0.054 | 0.0695 | ✗ |
| 1h | oos | `trend_dev` | 769 | 14.0% | +0.039 | 0.2865 | +0.081 | 0.0585 | ✗ |
| 1h | oos | `volume_pctl` | 769 | 14.0% | -0.030 | 0.5115 | -0.057 | 0.3460 | ○ |
| 1h | oos | `vol_balance` | 769 | 14.0% | +0.074 | 0.0690 | +0.091 | 0.0650 | · |
| 1h | oos | `rsi_slope_5_3` | 769 | 14.0% | -0.002 | 0.9475 | -0.020 | 0.6275 | ○ |
| 1h | oos | `rsi_slope_10_5` | 769 | 14.0% | -0.008 | 0.8240 | +0.009 | 0.8285 | ○ |
| 1h | oos | `rsi_slope_14_5` | 769 | 14.0% | -0.026 | 0.4610 | -0.013 | 0.7545 | ○ |
| 1h | oos | `zone_width_atr` | 769 | 14.0% | +0.170 | 0.0000 | +0.227 | 0.0000 | · |
| 1h | oos | `freshness_bars` | 769 | 14.0% | -0.063 | 0.0730 | -0.099 | 0.0165 | ✗ |
| 1h | oos | `prior_taps` | 769 | 14.0% | -0.009 | 0.8375 | -0.016 | 0.7495 | ✗ |
| 1h | oos | `approach_mom` | 769 | 14.0% | +0.038 | 0.3110 | +0.053 | 0.2155 | ✗ |
| 1h | oos | `tap_rsi` | 769 | 14.0% | +0.070 | 0.0470 | +0.115 | 0.0045 | ✗ |
| 4h | is | `trend_dev` | 344 | 11.0% | -0.043 | 0.4030 | -0.075 | 0.2400 | ○ |
| 4h | is | `volume_pctl` | 344 | 11.0% | +0.088 | 0.0910 | +0.086 | 0.1630 | ✗ |
| 4h | is | `vol_balance` | 344 | 11.0% | -0.064 | 0.2290 | -0.076 | 0.2125 | · |
| 4h | is | `rsi_slope_5_3` | 344 | 11.0% | +0.116 | 0.0365 | +0.143 | 0.0270 | ✗ |
| 4h | is | `rsi_slope_10_5` | 344 | 11.0% | +0.148 | 0.0065 | +0.177 | 0.0060 | ✗ |
| 4h | is | `rsi_slope_14_5` | 344 | 11.0% | +0.132 | 0.0125 | +0.145 | 0.0225 | ✗ |
| 4h | is | `zone_width_atr` | 344 | 11.0% | +0.092 | 0.0920 | +0.124 | 0.0480 | · |
| 4h | is | `freshness_bars` | 344 | 11.0% | +0.016 | 0.7785 | +0.031 | 0.6310 | ○ |
| 4h | is | `prior_taps` | 344 | 11.0% | -0.018 | 0.7585 | -0.023 | 0.7295 | ✗ |
| 4h | is | `approach_mom` | 344 | 11.0% | +0.097 | 0.0885 | +0.105 | 0.1025 | ✗ |
| 4h | is | `tap_rsi` | 344 | 11.0% | +0.033 | 0.5555 | +0.023 | 0.7115 | ✗ |
| 4h | oos | `trend_dev` | 215 | 11.6% | -0.039 | 0.5595 | -0.030 | 0.6960 | ○ |
| 4h | oos | `volume_pctl` | 215 | 11.6% | +0.051 | 0.3665 | +0.022 | 0.7685 | ✗ |
| 4h | oos | `vol_balance` | 215 | 11.6% | +0.138 | 0.0355 | +0.187 | 0.0220 | · |
| 4h | oos | `rsi_slope_5_3` | 215 | 11.6% | +0.093 | 0.1650 | +0.129 | 0.1215 | ✗ |
| 4h | oos | `rsi_slope_10_5` | 215 | 11.6% | +0.119 | 0.0775 | +0.148 | 0.0740 | ✗ |
| 4h | oos | `rsi_slope_14_5` | 215 | 11.6% | +0.112 | 0.1035 | +0.132 | 0.1145 | ✗ |
| 4h | oos | `zone_width_atr` | 215 | 11.6% | +0.185 | 0.0060 | +0.203 | 0.0140 | · |
| 4h | oos | `freshness_bars` | 215 | 11.6% | -0.068 | 0.3400 | -0.096 | 0.2670 | ✗ |
| 4h | oos | `prior_taps` | 215 | 11.6% | +0.164 | 0.0230 | +0.192 | 0.0380 | ○ |
| 4h | oos | `approach_mom` | 215 | 11.6% | +0.085 | 0.2260 | +0.125 | 0.1315 | ✗ |
| 4h | oos | `tap_rsi` | 215 | 11.6% | +0.006 | 0.9325 | +0.016 | 0.8635 | ✗ |

## §2 — 손절폭(1R) 절대 크기

`stop_width_frac` = |진입−손절|/진입(가격 대비) · `stop_width_atr` = |진입−손절|/ATR. ⚠️ WAN-79 가드(`min_stop_distance_fraction=0.3%`)가 이미 좁은 셋업을 거절하므로 관측 하한이 잘려 있다(아래 하한 참고). ⚠️ 살아남아도 「선별」이 아니라 「기하」일 공산이 크다.

관측 손절폭(가격 대비) 하한 **0.300%** · 가드 0.3% 미만 0건 (0.0%). 가드가 좁은 손절을 이미 잘라 관측 범위가 0.3% 근방에서 절단돼 있음을 전제로 읽을 것.

| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `stop_width_frac` | 5280 | 9.6% | +0.025 | 0.0610 | +0.023 | 0.1470 | ✗ |
| 15m | is | `stop_width_atr` | 5280 | 9.6% | +0.146 | 0.0000 | +0.193 | 0.0000 | ✗ |
| 15m | oos | `stop_width_frac` | 2567 | 14.1% | +0.036 | 0.0930 | +0.035 | 0.1840 | ✗ |
| 15m | oos | `stop_width_atr` | 2567 | 14.1% | +0.174 | 0.0000 | +0.221 | 0.0000 | ✗ |
| 1h | is | `stop_width_frac` | 1448 | 10.5% | +0.099 | 0.0005 | +0.106 | 0.0010 | ✗ |
| 1h | is | `stop_width_atr` | 1448 | 10.5% | +0.174 | 0.0000 | +0.239 | 0.0000 | ✗ |
| 1h | oos | `stop_width_frac` | 769 | 14.0% | +0.021 | 0.5200 | +0.011 | 0.7900 | ✗ |
| 1h | oos | `stop_width_atr` | 769 | 14.0% | +0.164 | 0.0000 | +0.228 | 0.0000 | ✗ |
| 4h | is | `stop_width_frac` | 344 | 11.0% | -0.082 | 0.0970 | -0.069 | 0.2640 | ○ |
| 4h | is | `stop_width_atr` | 344 | 11.0% | +0.107 | 0.0465 | +0.152 | 0.0140 | ✗ |
| 4h | oos | `stop_width_frac` | 215 | 11.6% | -0.018 | 0.7585 | -0.033 | 0.6450 | ○ |
| 4h | oos | `stop_width_atr` | 215 | 11.6% | +0.184 | 0.0045 | +0.227 | 0.0055 | ✗ |

## §3 — RSI-EMA 곡률 (사용자 못박은 후보 · 닫힌 봉 · 룩어헤드 없음)

RSI(14) 위 EMA(span14)의 기울기 `d1`·곡률 `d2`(탭 직전 확정봉). `death_shape=1`은 ∩(d2<0)+하락(d1<0). 가설: 즉사일수록 d1·d2가 음수(롤오버). ⚠️ span 14 하나로 판정(다중 span은 Bonferroni 가족만 키운다). 회귀 테스트가 룩어헤드 없음을 동작으로 고정한다.

| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `rsi_ema_slope` | 5280 | 9.6% | +0.060 | 0.0000 | +0.084 | 0.0000 | ✗ |
| 15m | is | `rsi_ema_curv` | 5280 | 9.6% | +0.038 | 0.0060 | +0.055 | 0.0000 | ✗ |
| 15m | is | `rsi_ema_death_shape` | 5280 | 9.6% | -0.022 | 0.1135 | -0.040 | 0.0160 | ✗ |
| 15m | oos | `rsi_ema_slope` | 2567 | 14.1% | +0.050 | 0.0075 | +0.063 | 0.0085 | ✗ |
| 15m | oos | `rsi_ema_curv` | 2567 | 14.1% | +0.073 | 0.0005 | +0.086 | 0.0005 | ✗ |
| 15m | oos | `rsi_ema_death_shape` | 2567 | 14.1% | -0.053 | 0.0100 | -0.058 | 0.0125 | ✗ |
| 1h | is | `rsi_ema_slope` | 1448 | 10.5% | +0.019 | 0.4805 | +0.018 | 0.5440 | ✗ |
| 1h | is | `rsi_ema_curv` | 1448 | 10.5% | +0.030 | 0.2560 | +0.037 | 0.2375 | ✗ |
| 1h | is | `rsi_ema_death_shape` | 1448 | 10.5% | +0.001 | 1.0000 | -0.003 | 0.9240 | ○ |
| 1h | oos | `rsi_ema_slope` | 769 | 14.0% | +0.005 | 0.9015 | -0.002 | 0.9730 | ✗ |
| 1h | oos | `rsi_ema_curv` | 769 | 14.0% | +0.024 | 0.5150 | +0.027 | 0.5525 | ✗ |
| 1h | oos | `rsi_ema_death_shape` | 769 | 14.0% | -0.046 | 0.2370 | -0.064 | 0.1480 | ✗ |
| 4h | is | `rsi_ema_slope` | 344 | 11.0% | +0.114 | 0.0350 | +0.135 | 0.0350 | ✗ |
| 4h | is | `rsi_ema_curv` | 344 | 11.0% | -0.038 | 0.4820 | -0.038 | 0.5345 | ○ |
| 4h | is | `rsi_ema_death_shape` | 344 | 11.0% | +0.075 | 0.1870 | +0.091 | 0.1995 | ○ |
| 4h | oos | `rsi_ema_slope` | 215 | 11.6% | +0.161 | 0.0230 | +0.205 | 0.0110 | ✗ |
| 4h | oos | `rsi_ema_curv` | 215 | 11.6% | +0.122 | 0.0620 | +0.158 | 0.0555 | ✗ |
| 4h | oos | `rsi_ema_death_shape` | 215 | 11.6% | +0.001 | 1.0000 | -0.002 | 1.0000 | ○ |

## leave-one-out (심볼 편중 진단 — 생존자만)

생존 특징을 심볼 하나씩 빼가며 OOS 상관 부호가 유지되는지. 이 저장소의 플러스는 반복적으로 ETH 하나가 만들었다.

| TF | 특징 | 축 | 구간 | 제외 | n | corr |
| -- | -- | -- | -- | -- | -- | -- |
| 15m | `approach_mom` | death_vs_rest | is | BTC | 4796 | +0.043 |
| 15m | `approach_mom` | death_vs_rest | is | ETH | 4777 | +0.044 |
| 15m | `approach_mom` | death_vs_rest | is | SOL | 4512 | +0.056 |
| 15m | `approach_mom` | death_vs_rest | is | BNB | 4698 | +0.045 |
| 15m | `approach_mom` | death_vs_rest | is | XRP | 4667 | +0.049 |
| 15m | `approach_mom` | death_vs_rest | is | TRX | 4830 | +0.040 |
| 15m | `approach_mom` | death_vs_rest | is | DOGE | 4683 | +0.042 |
| 15m | `approach_mom` | death_vs_rest | is | LINK | 4615 | +0.045 |
| 15m | `approach_mom` | death_vs_rest | is | LTC | 4662 | +0.043 |
| 15m | `approach_mom` | death_vs_rest | oos | BTC | 2393 | +0.074 |
| 15m | `approach_mom` | death_vs_rest | oos | ETH | 2264 | +0.069 |
| 15m | `approach_mom` | death_vs_rest | oos | SOL | 2163 | +0.084 |
| 15m | `approach_mom` | death_vs_rest | oos | BNB | 2380 | +0.079 |
| 15m | `approach_mom` | death_vs_rest | oos | XRP | 2247 | +0.076 |
| 15m | `approach_mom` | death_vs_rest | oos | TRX | 2488 | +0.078 |
| 15m | `approach_mom` | death_vs_rest | oos | DOGE | 2181 | +0.073 |
| 15m | `approach_mom` | death_vs_rest | oos | LINK | 2195 | +0.092 |
| 15m | `approach_mom` | death_vs_rest | oos | LTC | 2225 | +0.076 |
| 15m | `freshness_bars` | death_vs_rest | is | BTC | 4796 | -0.024 |
| 15m | `freshness_bars` | death_vs_rest | is | ETH | 4777 | -0.026 |
| 15m | `freshness_bars` | death_vs_rest | is | SOL | 4512 | -0.030 |
| 15m | `freshness_bars` | death_vs_rest | is | BNB | 4698 | -0.024 |
| 15m | `freshness_bars` | death_vs_rest | is | XRP | 4667 | -0.025 |
| 15m | `freshness_bars` | death_vs_rest | is | TRX | 4830 | -0.027 |
| 15m | `freshness_bars` | death_vs_rest | is | DOGE | 4683 | -0.026 |
| 15m | `freshness_bars` | death_vs_rest | is | LINK | 4615 | -0.022 |
| 15m | `freshness_bars` | death_vs_rest | is | LTC | 4662 | -0.018 |
| 15m | `freshness_bars` | death_vs_rest | oos | BTC | 2393 | -0.052 |
| 15m | `freshness_bars` | death_vs_rest | oos | ETH | 2264 | -0.054 |
| 15m | `freshness_bars` | death_vs_rest | oos | SOL | 2163 | -0.049 |
| 15m | `freshness_bars` | death_vs_rest | oos | BNB | 2380 | -0.048 |
| 15m | `freshness_bars` | death_vs_rest | oos | XRP | 2247 | -0.056 |
| 15m | `freshness_bars` | death_vs_rest | oos | TRX | 2488 | -0.051 |
| 15m | `freshness_bars` | death_vs_rest | oos | DOGE | 2181 | -0.052 |
| 15m | `freshness_bars` | death_vs_rest | oos | LINK | 2195 | -0.055 |
| 15m | `freshness_bars` | death_vs_rest | oos | LTC | 2225 | -0.054 |
| 15m | `tap_rsi` | death_vs_rest | is | BTC | 4796 | +0.050 |
| 15m | `tap_rsi` | death_vs_rest | is | ETH | 4777 | +0.052 |
| 15m | `tap_rsi` | death_vs_rest | is | SOL | 4512 | +0.065 |
| 15m | `tap_rsi` | death_vs_rest | is | BNB | 4698 | +0.056 |
| 15m | `tap_rsi` | death_vs_rest | is | XRP | 4667 | +0.060 |
| 15m | `tap_rsi` | death_vs_rest | is | TRX | 4830 | +0.058 |
| 15m | `tap_rsi` | death_vs_rest | is | DOGE | 4683 | +0.059 |
| 15m | `tap_rsi` | death_vs_rest | is | LINK | 4615 | +0.062 |
| 15m | `tap_rsi` | death_vs_rest | is | LTC | 4662 | +0.055 |
| 15m | `tap_rsi` | death_vs_rest | oos | BTC | 2393 | +0.098 |
| 15m | `tap_rsi` | death_vs_rest | oos | ETH | 2264 | +0.092 |
| 15m | `tap_rsi` | death_vs_rest | oos | SOL | 2163 | +0.101 |
| 15m | `tap_rsi` | death_vs_rest | oos | BNB | 2380 | +0.100 |
| 15m | `tap_rsi` | death_vs_rest | oos | XRP | 2247 | +0.112 |
| 15m | `tap_rsi` | death_vs_rest | oos | TRX | 2488 | +0.099 |
| 15m | `tap_rsi` | death_vs_rest | oos | DOGE | 2181 | +0.103 |
| 15m | `tap_rsi` | death_vs_rest | oos | LINK | 2195 | +0.113 |
| 15m | `tap_rsi` | death_vs_rest | oos | LTC | 2225 | +0.102 |
| 15m | `zone_width_atr` | death_vs_rest | is | BTC | 4796 | +0.131 |
| 15m | `zone_width_atr` | death_vs_rest | is | ETH | 4777 | +0.139 |
| 15m | `zone_width_atr` | death_vs_rest | is | SOL | 4512 | +0.128 |
| 15m | `zone_width_atr` | death_vs_rest | is | BNB | 4698 | +0.136 |
| 15m | `zone_width_atr` | death_vs_rest | is | XRP | 4667 | +0.126 |
| 15m | `zone_width_atr` | death_vs_rest | is | TRX | 4830 | +0.131 |
| 15m | `zone_width_atr` | death_vs_rest | is | DOGE | 4683 | +0.129 |
| 15m | `zone_width_atr` | death_vs_rest | is | LINK | 4615 | +0.126 |
| 15m | `zone_width_atr` | death_vs_rest | is | LTC | 4662 | +0.136 |
| 15m | `zone_width_atr` | death_vs_rest | oos | BTC | 2393 | +0.171 |
| 15m | `zone_width_atr` | death_vs_rest | oos | ETH | 2264 | +0.167 |
| 15m | `zone_width_atr` | death_vs_rest | oos | SOL | 2163 | +0.166 |
| 15m | `zone_width_atr` | death_vs_rest | oos | BNB | 2380 | +0.163 |
| 15m | `zone_width_atr` | death_vs_rest | oos | XRP | 2247 | +0.165 |
| 15m | `zone_width_atr` | death_vs_rest | oos | TRX | 2488 | +0.163 |
| 15m | `zone_width_atr` | death_vs_rest | oos | DOGE | 2181 | +0.165 |
| 15m | `zone_width_atr` | death_vs_rest | oos | LINK | 2195 | +0.161 |
| 15m | `zone_width_atr` | death_vs_rest | oos | LTC | 2225 | +0.175 |
| 15m | `approach_mom` | death_vs_winner | is | BTC | 3245 | +0.074 |
| 15m | `approach_mom` | death_vs_winner | is | ETH | 3252 | +0.071 |
| 15m | `approach_mom` | death_vs_winner | is | SOL | 3070 | +0.086 |
| 15m | `approach_mom` | death_vs_winner | is | BNB | 3203 | +0.072 |
| 15m | `approach_mom` | death_vs_winner | is | XRP | 3145 | +0.079 |
| 15m | `approach_mom` | death_vs_winner | is | TRX | 3287 | +0.066 |
| 15m | `approach_mom` | death_vs_winner | is | DOGE | 3163 | +0.069 |
| 15m | `approach_mom` | death_vs_winner | is | LINK | 3124 | +0.074 |
| 15m | `approach_mom` | death_vs_winner | is | LTC | 3175 | +0.071 |
| 15m | `approach_mom` | death_vs_winner | oos | BTC | 1660 | +0.103 |
| 15m | `approach_mom` | death_vs_winner | oos | ETH | 1577 | +0.096 |
| 15m | `approach_mom` | death_vs_winner | oos | SOL | 1490 | +0.112 |
| 15m | `approach_mom` | death_vs_winner | oos | BNB | 1652 | +0.109 |
| 15m | `approach_mom` | death_vs_winner | oos | XRP | 1538 | +0.106 |
| 15m | `approach_mom` | death_vs_winner | oos | TRX | 1720 | +0.108 |
| 15m | `approach_mom` | death_vs_winner | oos | DOGE | 1515 | +0.101 |
| 15m | `approach_mom` | death_vs_winner | oos | LINK | 1529 | +0.122 |
| 15m | `approach_mom` | death_vs_winner | oos | LTC | 1551 | +0.103 |
| 15m | `freshness_bars` | death_vs_winner | is | BTC | 3245 | -0.036 |
| 15m | `freshness_bars` | death_vs_winner | is | ETH | 3252 | -0.038 |
| 15m | `freshness_bars` | death_vs_winner | is | SOL | 3070 | -0.041 |
| 15m | `freshness_bars` | death_vs_winner | is | BNB | 3203 | -0.036 |
| 15m | `freshness_bars` | death_vs_winner | is | XRP | 3145 | -0.038 |
| 15m | `freshness_bars` | death_vs_winner | is | TRX | 3287 | -0.038 |
| 15m | `freshness_bars` | death_vs_winner | is | DOGE | 3163 | -0.037 |
| 15m | `freshness_bars` | death_vs_winner | is | LINK | 3124 | -0.034 |
| 15m | `freshness_bars` | death_vs_winner | is | LTC | 3175 | -0.029 |
| 15m | `freshness_bars` | death_vs_winner | oos | BTC | 1660 | -0.070 |
| 15m | `freshness_bars` | death_vs_winner | oos | ETH | 1577 | -0.071 |
| 15m | `freshness_bars` | death_vs_winner | oos | SOL | 1490 | -0.067 |
| 15m | `freshness_bars` | death_vs_winner | oos | BNB | 1652 | -0.065 |
| 15m | `freshness_bars` | death_vs_winner | oos | XRP | 1538 | -0.076 |
| 15m | `freshness_bars` | death_vs_winner | oos | TRX | 1720 | -0.069 |
| 15m | `freshness_bars` | death_vs_winner | oos | DOGE | 1515 | -0.072 |
| 15m | `freshness_bars` | death_vs_winner | oos | LINK | 1529 | -0.073 |
| 15m | `freshness_bars` | death_vs_winner | oos | LTC | 1551 | -0.073 |
| 15m | `tap_rsi` | death_vs_winner | is | BTC | 3245 | +0.083 |
| 15m | `tap_rsi` | death_vs_winner | is | ETH | 3252 | +0.083 |
| 15m | `tap_rsi` | death_vs_winner | is | SOL | 3070 | +0.098 |
| 15m | `tap_rsi` | death_vs_winner | is | BNB | 3203 | +0.087 |
| 15m | `tap_rsi` | death_vs_winner | is | XRP | 3145 | +0.094 |
| 15m | `tap_rsi` | death_vs_winner | is | TRX | 3287 | +0.089 |
| 15m | `tap_rsi` | death_vs_winner | is | DOGE | 3163 | +0.092 |
| 15m | `tap_rsi` | death_vs_winner | is | LINK | 3124 | +0.098 |
| 15m | `tap_rsi` | death_vs_winner | is | LTC | 3175 | +0.087 |
| 15m | `tap_rsi` | death_vs_winner | oos | BTC | 1660 | +0.134 |
| 15m | `tap_rsi` | death_vs_winner | oos | ETH | 1577 | +0.125 |
| 15m | `tap_rsi` | death_vs_winner | oos | SOL | 1490 | +0.138 |
| 15m | `tap_rsi` | death_vs_winner | oos | BNB | 1652 | +0.134 |
| 15m | `tap_rsi` | death_vs_winner | oos | XRP | 1538 | +0.152 |
| 15m | `tap_rsi` | death_vs_winner | oos | TRX | 1720 | +0.134 |
| 15m | `tap_rsi` | death_vs_winner | oos | DOGE | 1515 | +0.139 |
| 15m | `tap_rsi` | death_vs_winner | oos | LINK | 1529 | +0.150 |
| 15m | `tap_rsi` | death_vs_winner | oos | LTC | 1551 | +0.132 |
| 15m | `trend_dev` | death_vs_winner | is | BTC | 3245 | +0.049 |
| 15m | `trend_dev` | death_vs_winner | is | ETH | 3252 | +0.045 |
| 15m | `trend_dev` | death_vs_winner | is | SOL | 3070 | +0.055 |
| 15m | `trend_dev` | death_vs_winner | is | BNB | 3203 | +0.048 |
| 15m | `trend_dev` | death_vs_winner | is | XRP | 3145 | +0.046 |
| 15m | `trend_dev` | death_vs_winner | is | TRX | 3287 | +0.043 |
| 15m | `trend_dev` | death_vs_winner | is | DOGE | 3163 | +0.058 |
| 15m | `trend_dev` | death_vs_winner | is | LINK | 3124 | +0.053 |
| 15m | `trend_dev` | death_vs_winner | is | LTC | 3175 | +0.046 |
| 15m | `trend_dev` | death_vs_winner | oos | BTC | 1660 | +0.074 |
| 15m | `trend_dev` | death_vs_winner | oos | ETH | 1577 | +0.070 |
| 15m | `trend_dev` | death_vs_winner | oos | SOL | 1490 | +0.071 |
| 15m | `trend_dev` | death_vs_winner | oos | BNB | 1652 | +0.065 |
| 15m | `trend_dev` | death_vs_winner | oos | XRP | 1538 | +0.096 |
| 15m | `trend_dev` | death_vs_winner | oos | TRX | 1720 | +0.071 |
| 15m | `trend_dev` | death_vs_winner | oos | DOGE | 1515 | +0.081 |
| 15m | `trend_dev` | death_vs_winner | oos | LINK | 1529 | +0.078 |
| 15m | `trend_dev` | death_vs_winner | oos | LTC | 1551 | +0.075 |
| 15m | `volume_pctl` | death_vs_winner | is | BTC | 3245 | -0.060 |
| 15m | `volume_pctl` | death_vs_winner | is | ETH | 3252 | -0.060 |
| 15m | `volume_pctl` | death_vs_winner | is | SOL | 3070 | -0.040 |
| 15m | `volume_pctl` | death_vs_winner | is | BNB | 3203 | -0.058 |
| 15m | `volume_pctl` | death_vs_winner | is | XRP | 3145 | -0.057 |
| 15m | `volume_pctl` | death_vs_winner | is | TRX | 3287 | -0.059 |
| 15m | `volume_pctl` | death_vs_winner | is | DOGE | 3163 | -0.066 |
| 15m | `volume_pctl` | death_vs_winner | is | LINK | 3124 | -0.063 |
| 15m | `volume_pctl` | death_vs_winner | is | LTC | 3175 | -0.060 |
| 15m | `volume_pctl` | death_vs_winner | oos | BTC | 1660 | -0.070 |
| 15m | `volume_pctl` | death_vs_winner | oos | ETH | 1577 | -0.081 |
| 15m | `volume_pctl` | death_vs_winner | oos | SOL | 1490 | -0.060 |
| 15m | `volume_pctl` | death_vs_winner | oos | BNB | 1652 | -0.074 |
| 15m | `volume_pctl` | death_vs_winner | oos | XRP | 1538 | -0.076 |
| 15m | `volume_pctl` | death_vs_winner | oos | TRX | 1720 | -0.058 |
| 15m | `volume_pctl` | death_vs_winner | oos | DOGE | 1515 | -0.067 |
| 15m | `volume_pctl` | death_vs_winner | oos | LINK | 1529 | -0.075 |
| 15m | `volume_pctl` | death_vs_winner | oos | LTC | 1551 | -0.085 |
| 15m | `zone_width_atr` | death_vs_winner | is | BTC | 3245 | +0.179 |
| 15m | `zone_width_atr` | death_vs_winner | is | ETH | 3252 | +0.187 |
| 15m | `zone_width_atr` | death_vs_winner | is | SOL | 3070 | +0.174 |
| 15m | `zone_width_atr` | death_vs_winner | is | BNB | 3203 | +0.183 |
| 15m | `zone_width_atr` | death_vs_winner | is | XRP | 3145 | +0.172 |
| 15m | `zone_width_atr` | death_vs_winner | is | TRX | 3287 | +0.179 |
| 15m | `zone_width_atr` | death_vs_winner | is | DOGE | 3163 | +0.177 |
| 15m | `zone_width_atr` | death_vs_winner | is | LINK | 3124 | +0.174 |
| 15m | `zone_width_atr` | death_vs_winner | is | LTC | 3175 | +0.183 |
| 15m | `zone_width_atr` | death_vs_winner | oos | BTC | 1660 | +0.214 |
| 15m | `zone_width_atr` | death_vs_winner | oos | ETH | 1577 | +0.209 |
| 15m | `zone_width_atr` | death_vs_winner | oos | SOL | 1490 | +0.207 |
| 15m | `zone_width_atr` | death_vs_winner | oos | BNB | 1652 | +0.204 |
| 15m | `zone_width_atr` | death_vs_winner | oos | XRP | 1538 | +0.210 |
| 15m | `zone_width_atr` | death_vs_winner | oos | TRX | 1720 | +0.203 |
| 15m | `zone_width_atr` | death_vs_winner | oos | DOGE | 1515 | +0.211 |
| 15m | `zone_width_atr` | death_vs_winner | oos | LINK | 1529 | +0.200 |
| 15m | `zone_width_atr` | death_vs_winner | oos | LTC | 1551 | +0.214 |
| 1h | `zone_width_atr` | death_vs_rest | is | BTC | 1279 | +0.150 |
| 1h | `zone_width_atr` | death_vs_rest | is | ETH | 1289 | +0.152 |
| 1h | `zone_width_atr` | death_vs_rest | is | SOL | 1261 | +0.148 |
| 1h | `zone_width_atr` | death_vs_rest | is | BNB | 1306 | +0.157 |
| 1h | `zone_width_atr` | death_vs_rest | is | XRP | 1294 | +0.163 |
| 1h | `zone_width_atr` | death_vs_rest | is | TRX | 1286 | +0.156 |
| 1h | `zone_width_atr` | death_vs_rest | is | DOGE | 1305 | +0.158 |
| 1h | `zone_width_atr` | death_vs_rest | is | LINK | 1274 | +0.136 |
| 1h | `zone_width_atr` | death_vs_rest | is | LTC | 1290 | +0.157 |
| 1h | `zone_width_atr` | death_vs_rest | oos | BTC | 684 | +0.158 |
| 1h | `zone_width_atr` | death_vs_rest | oos | ETH | 678 | +0.192 |
| 1h | `zone_width_atr` | death_vs_rest | oos | SOL | 676 | +0.173 |
| 1h | `zone_width_atr` | death_vs_rest | oos | BNB | 691 | +0.168 |
| 1h | `zone_width_atr` | death_vs_rest | oos | XRP | 677 | +0.172 |
| 1h | `zone_width_atr` | death_vs_rest | oos | TRX | 704 | +0.174 |
| 1h | `zone_width_atr` | death_vs_rest | oos | DOGE | 680 | +0.156 |
| 1h | `zone_width_atr` | death_vs_rest | oos | LINK | 677 | +0.172 |
| 1h | `zone_width_atr` | death_vs_rest | oos | LTC | 685 | +0.170 |
| 1h | `freshness_bars` | death_vs_winner | is | BTC | 919 | -0.017 |
| 1h | `freshness_bars` | death_vs_winner | is | ETH | 936 | -0.020 |
| 1h | `freshness_bars` | death_vs_winner | is | SOL | 916 | -0.028 |
| 1h | `freshness_bars` | death_vs_winner | is | BNB | 937 | -0.037 |
| 1h | `freshness_bars` | death_vs_winner | is | XRP | 930 | -0.033 |
| 1h | `freshness_bars` | death_vs_winner | is | TRX | 918 | -0.032 |
| 1h | `freshness_bars` | death_vs_winner | is | DOGE | 941 | -0.025 |
| 1h | `freshness_bars` | death_vs_winner | is | LINK | 917 | -0.030 |
| 1h | `freshness_bars` | death_vs_winner | is | LTC | 938 | -0.067 |
| 1h | `freshness_bars` | death_vs_winner | oos | BTC | 478 | -0.084 |
| 1h | `freshness_bars` | death_vs_winner | oos | ETH | 461 | -0.108 |
| 1h | `freshness_bars` | death_vs_winner | oos | SOL | 463 | -0.127 |
| 1h | `freshness_bars` | death_vs_winner | oos | BNB | 478 | -0.085 |
| 1h | `freshness_bars` | death_vs_winner | oos | XRP | 463 | -0.094 |
| 1h | `freshness_bars` | death_vs_winner | oos | TRX | 480 | -0.098 |
| 1h | `freshness_bars` | death_vs_winner | oos | DOGE | 472 | -0.102 |
| 1h | `freshness_bars` | death_vs_winner | oos | LINK | 470 | -0.090 |
| 1h | `freshness_bars` | death_vs_winner | oos | LTC | 475 | -0.105 |
| 1h | `tap_rsi` | death_vs_winner | is | BTC | 919 | +0.049 |
| 1h | `tap_rsi` | death_vs_winner | is | ETH | 936 | +0.037 |
| 1h | `tap_rsi` | death_vs_winner | is | SOL | 916 | +0.046 |
| 1h | `tap_rsi` | death_vs_winner | is | BNB | 937 | +0.073 |
| 1h | `tap_rsi` | death_vs_winner | is | XRP | 930 | +0.057 |
| 1h | `tap_rsi` | death_vs_winner | is | TRX | 918 | +0.065 |
| 1h | `tap_rsi` | death_vs_winner | is | DOGE | 941 | +0.045 |
| 1h | `tap_rsi` | death_vs_winner | is | LINK | 917 | +0.059 |
| 1h | `tap_rsi` | death_vs_winner | is | LTC | 938 | +0.061 |
| 1h | `tap_rsi` | death_vs_winner | oos | BTC | 478 | +0.113 |
| 1h | `tap_rsi` | death_vs_winner | oos | ETH | 461 | +0.116 |
| 1h | `tap_rsi` | death_vs_winner | oos | SOL | 463 | +0.108 |
| 1h | `tap_rsi` | death_vs_winner | oos | BNB | 478 | +0.079 |
| 1h | `tap_rsi` | death_vs_winner | oos | XRP | 463 | +0.127 |
| 1h | `tap_rsi` | death_vs_winner | oos | TRX | 480 | +0.112 |
| 1h | `tap_rsi` | death_vs_winner | oos | DOGE | 472 | +0.122 |
| 1h | `tap_rsi` | death_vs_winner | oos | LINK | 470 | +0.120 |
| 1h | `tap_rsi` | death_vs_winner | oos | LTC | 475 | +0.141 |
| 1h | `zone_width_atr` | death_vs_winner | is | BTC | 919 | +0.205 |
| 1h | `zone_width_atr` | death_vs_winner | is | ETH | 936 | +0.209 |
| 1h | `zone_width_atr` | death_vs_winner | is | SOL | 916 | +0.206 |
| 1h | `zone_width_atr` | death_vs_winner | is | BNB | 937 | +0.215 |
| 1h | `zone_width_atr` | death_vs_winner | is | XRP | 930 | +0.223 |
| 1h | `zone_width_atr` | death_vs_winner | is | TRX | 918 | +0.215 |
| 1h | `zone_width_atr` | death_vs_winner | is | DOGE | 941 | +0.215 |
| 1h | `zone_width_atr` | death_vs_winner | is | LINK | 917 | +0.192 |
| 1h | `zone_width_atr` | death_vs_winner | is | LTC | 938 | +0.214 |
| 1h | `zone_width_atr` | death_vs_winner | oos | BTC | 478 | +0.213 |
| 1h | `zone_width_atr` | death_vs_winner | oos | ETH | 461 | +0.261 |
| 1h | `zone_width_atr` | death_vs_winner | oos | SOL | 463 | +0.235 |
| 1h | `zone_width_atr` | death_vs_winner | oos | BNB | 478 | +0.218 |
| 1h | `zone_width_atr` | death_vs_winner | oos | XRP | 463 | +0.226 |
| 1h | `zone_width_atr` | death_vs_winner | oos | TRX | 480 | +0.230 |
| 1h | `zone_width_atr` | death_vs_winner | oos | DOGE | 472 | +0.215 |
| 1h | `zone_width_atr` | death_vs_winner | oos | LINK | 470 | +0.229 |
| 1h | `zone_width_atr` | death_vs_winner | oos | LTC | 475 | +0.222 |
| 4h | `zone_width_atr` | death_vs_winner | is | BTC | 227 | +0.122 |
| 4h | `zone_width_atr` | death_vs_winner | is | ETH | 225 | +0.114 |
| 4h | `zone_width_atr` | death_vs_winner | is | SOL | 228 | +0.106 |
| 4h | `zone_width_atr` | death_vs_winner | is | BNB | 226 | +0.147 |
| 4h | `zone_width_atr` | death_vs_winner | is | XRP | 231 | +0.131 |
| 4h | `zone_width_atr` | death_vs_winner | is | TRX | 218 | +0.121 |
| 4h | `zone_width_atr` | death_vs_winner | is | DOGE | 226 | +0.103 |
| 4h | `zone_width_atr` | death_vs_winner | is | LINK | 233 | +0.122 |
| 4h | `zone_width_atr` | death_vs_winner | is | LTC | 234 | +0.151 |
| 4h | `zone_width_atr` | death_vs_winner | oos | BTC | 138 | +0.203 |
| 4h | `zone_width_atr` | death_vs_winner | oos | ETH | 129 | +0.261 |
| 4h | `zone_width_atr` | death_vs_winner | oos | SOL | 122 | +0.163 |
| 4h | `zone_width_atr` | death_vs_winner | oos | BNB | 131 | +0.236 |
| 4h | `zone_width_atr` | death_vs_winner | oos | XRP | 135 | +0.188 |
| 4h | `zone_width_atr` | death_vs_winner | oos | TRX | 132 | +0.192 |
| 4h | `zone_width_atr` | death_vs_winner | oos | DOGE | 132 | +0.190 |
| 4h | `zone_width_atr` | death_vs_winner | oos | LINK | 132 | +0.183 |
| 4h | `zone_width_atr` | death_vs_winner | oos | LTC | 133 | +0.217 |

## 1단계: 특징 분위별 3분류 비율 (심볼 풀링)

각 TF·구간·특징을 3분위로 나눈 즉사%/애매%/승자% (Q1<Q2<Q3).

| TF | 구간 | 특징 | Q1 즉사/애매/승 | Q2 | Q3 |
| -- | -- | -- | -- | -- | -- |
| 15m | is | `trend_dev` | 9/29/63 | 10/33/56 | 10/34/56 |
| 15m | is | `volume_pctl` | 11/33/56 | 10/34/56 | 8/30/62 |
| 15m | is | `vol_balance` | 9/30/61 | 9/34/56 | 11/32/57 |
| 15m | is | `rsi_slope_5_3` | 9/30/62 | 10/33/56 | 10/33/56 |
| 15m | is | `rsi_slope_10_5` | 8/32/59 | 10/31/59 | 10/33/56 |
| 15m | is | `rsi_slope_14_5` | 9/31/60 | 10/32/58 | 10/34/56 |
| 15m | is | `zone_width_atr` | 5/28/67 | 10/34/56 | 14/34/52 |
| 15m | is | `freshness_bars` | 11/36/52 | 10/33/57 | 8/27/65 |
| 15m | is | `prior_taps` | 9/32/59 | 12/34/54 | — |
| 15m | is | `approach_mom` | 8/29/64 | 11/33/56 | 10/35/55 |
| 15m | is | `tap_rsi` | 7/28/65 | 11/32/57 | 11/36/53 |
| 15m | is | `stop_width_frac` | 8/33/59 | 10/33/57 | 11/31/58 |
| 15m | is | `stop_width_atr` | 5/29/67 | 10/34/56 | 14/33/52 |
| 15m | is | `rsi_ema_slope` | 7/29/63 | 11/32/57 | 11/35/54 |
| 15m | is | `rsi_ema_curv` | 8/31/62 | 11/31/58 | 11/35/55 |
| 15m | is | `rsi_ema_death_shape` | 10/32/58 | — | — |
| 15m | oos | `trend_dev` | 11/30/59 | 17/29/54 | 15/33/52 |
| 15m | oos | `volume_pctl` | 16/32/52 | 13/34/53 | 13/27/60 |
| 15m | oos | `vol_balance` | 12/27/61 | 17/31/51 | 13/34/54 |
| 15m | oos | `rsi_slope_5_3` | 14/30/56 | 14/31/55 | 14/31/55 |
| 15m | oos | `rsi_slope_10_5` | 14/29/57 | 15/32/53 | 13/31/55 |
| 15m | oos | `rsi_slope_14_5` | 13/31/57 | 17/29/54 | 13/32/55 |
| 15m | oos | `zone_width_atr` | 7/29/64 | 14/33/53 | 21/30/49 |
| 15m | oos | `freshness_bars` | 17/32/51 | 15/31/54 | 10/29/61 |
| 15m | oos | `prior_taps` | 14/31/55 | 15/30/55 | — |
| 15m | oos | `approach_mom` | 11/29/61 | 16/33/51 | 16/30/54 |
| 15m | oos | `tap_rsi` | 9/27/64 | 15/33/52 | 18/32/50 |
| 15m | oos | `stop_width_frac` | 13/31/56 | 14/32/53 | 15/28/57 |
| 15m | oos | `stop_width_atr` | 7/29/64 | 15/33/53 | 21/30/49 |
| 15m | oos | `rsi_ema_slope` | 12/29/58 | 15/31/54 | 15/31/53 |
| 15m | oos | `rsi_ema_curv` | 12/31/57 | 13/32/55 | 17/30/53 |
| 15m | oos | `rsi_ema_death_shape` | 14/31/55 | — | — |
| 1h | is | `trend_dev` | 12/23/65 | 10/32/58 | 10/29/62 |
| 1h | is | `volume_pctl` | 12/29/59 | 9/28/63 | 10/27/63 |
| 1h | is | `vol_balance` | 9/28/63 | 12/27/60 | 10/28/61 |
| 1h | is | `rsi_slope_5_3` | 9/29/62 | 12/27/60 | 10/28/62 |
| 1h | is | `rsi_slope_10_5` | 10/29/60 | 11/31/59 | 11/24/66 |
| 1h | is | `rsi_slope_14_5` | 10/27/63 | 10/33/57 | 11/24/65 |
| 1h | is | `zone_width_atr` | 4/21/75 | 13/32/55 | 15/31/54 |
| 1h | is | `freshness_bars` | 12/34/55 | 12/27/61 | 8/23/69 |
| 1h | is | `prior_taps` | 10/28/62 | — | — |
| 1h | is | `approach_mom` | 9/26/64 | 11/29/60 | 11/29/60 |
| 1h | is | `tap_rsi` | 9/22/69 | 11/31/58 | 11/31/58 |
| 1h | is | `stop_width_frac` | 5/28/67 | 12/26/62 | 14/29/56 |
| 1h | is | `stop_width_atr` | 4/20/77 | 12/32/57 | 16/32/52 |
| 1h | is | `rsi_ema_slope` | 10/29/62 | 13/26/61 | 9/29/62 |
| 1h | is | `rsi_ema_curv` | 10/27/63 | 10/28/61 | 11/28/60 |
| 1h | is | `rsi_ema_death_shape` | 10/28/62 | — | — |
| 1h | oos | `trend_dev` | 11/25/65 | 19/32/49 | 12/36/51 |
| 1h | oos | `volume_pctl` | 14/33/53 | 15/32/54 | 14/28/58 |
| 1h | oos | `vol_balance` | 11/30/60 | 15/32/53 | 16/32/52 |
| 1h | oos | `rsi_slope_5_3` | 15/32/53 | 13/34/53 | 14/27/59 |
| 1h | oos | `rsi_slope_10_5` | 15/26/59 | 14/36/50 | 12/31/56 |
| 1h | oos | `rsi_slope_14_5` | 14/27/59 | 17/32/50 | 11/34/55 |
| 1h | oos | `zone_width_atr` | 7/25/68 | 16/38/47 | 20/31/49 |
| 1h | oos | `freshness_bars` | 18/37/46 | 16/32/52 | 9/24/67 |
| 1h | oos | `prior_taps` | 14/31/55 | 15/30/55 | — |
| 1h | oos | `approach_mom` | 12/31/56 | 12/32/55 | 17/30/53 |
| 1h | oos | `tap_rsi` | 10/26/64 | 16/30/54 | 16/37/47 |
| 1h | oos | `stop_width_frac` | 13/31/56 | 17/33/50 | 12/29/59 |
| 1h | oos | `stop_width_atr` | 6/27/67 | 17/30/53 | 19/36/45 |
| 1h | oos | `rsi_ema_slope` | 14/34/51 | 13/29/58 | 15/30/55 |
| 1h | oos | `rsi_ema_curv` | 13/32/55 | 13/27/60 | 16/35/49 |
| 1h | oos | `rsi_ema_death_shape` | 14/31/55 | — | — |
| 4h | is | `trend_dev` | 11/31/57 | 13/22/65 | 9/23/68 |
| 4h | is | `volume_pctl` | 9/30/62 | 11/25/65 | 14/23/63 |
| 4h | is | `vol_balance` | 16/29/56 | 9/24/68 | 9/24/67 |
| 4h | is | `rsi_slope_5_3` | 4/24/71 | 13/25/61 | 16/27/57 |
| 4h | is | `rsi_slope_10_5` | 8/26/66 | 8/26/66 | 17/24/58 |
| 4h | is | `rsi_slope_14_5` | 8/26/66 | 10/26/64 | 16/24/60 |
| 4h | is | `zone_width_atr` | 7/21/72 | 13/28/59 | 13/28/59 |
| 4h | is | `freshness_bars` | 14/24/62 | 8/26/66 | 11/27/62 |
| 4h | is | `prior_taps` | 11/26/63 | — | — |
| 4h | is | `approach_mom` | 8/28/64 | 12/28/60 | 13/21/66 |
| 4h | is | `tap_rsi` | 6/28/66 | 20/25/54 | 7/23/70 |
| 4h | is | `stop_width_frac` | 10/23/68 | 17/20/63 | 7/34/59 |
| 4h | is | `stop_width_atr` | 8/20/72 | 11/26/63 | 15/30/55 |
| 4h | is | `rsi_ema_slope` | 6/27/67 | 11/24/65 | 16/26/58 |
| 4h | is | `rsi_ema_curv` | 10/26/63 | 13/27/60 | 10/23/67 |
| 4h | is | `rsi_ema_death_shape` | 11/26/63 | — | — |
| 4h | oos | `trend_dev` | 10/28/62 | 15/34/51 | 10/32/58 |
| 4h | oos | `volume_pctl` | 11/39/50 | 8/27/65 | 15/28/57 |
| 4h | oos | `vol_balance` | 8/26/66 | 11/28/61 | 15/39/45 |
| 4h | oos | `rsi_slope_5_3` | 10/28/62 | 13/27/61 | 12/39/49 |
| 4h | oos | `rsi_slope_10_5` | 6/29/65 | 14/31/55 | 15/33/51 |
| 4h | oos | `rsi_slope_14_5` | 7/31/62 | 14/31/55 | 14/32/54 |
| 4h | oos | `zone_width_atr` | 4/33/62 | 10/37/54 | 21/24/56 |
| 4h | oos | `freshness_bars` | 7/29/64 | 14/41/45 | 14/24/62 |
| 4h | oos | `prior_taps` | 12/31/57 | — | — |
| 4h | oos | `approach_mom` | 10/24/67 | 14/32/54 | 11/38/51 |
| 4h | oos | `tap_rsi` | 8/26/65 | 20/37/44 | 7/31/62 |
| 4h | oos | `stop_width_frac` | 12/33/54 | 10/34/56 | 12/26/61 |
| 4h | oos | `stop_width_atr` | 7/24/69 | 10/38/52 | 18/32/50 |
| 4h | oos | `rsi_ema_slope` | 6/29/65 | 14/30/56 | 15/35/50 |
| 4h | oos | `rsi_ema_curv` | 6/31/64 | 17/28/55 | 12/35/53 |
| 4h | oos | `rsi_ema_death_shape` | 12/31/57 | — | — |

## ⚠️ 인용 경고

* **「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지** — 다른 질문(*이미 진입한 손절 중 즉사를 진입 시점에 알아보는가*)이다.
* 전부 `baseline`(낙관) 렌즈 위의 값 · 존폭 축 체결 보수화(`pen_5bp`)는 안 쟀다.
* §2/§3이 무작위를 넘어도 「선별」이 아니라 「기하/가격」일 공산이 크다(WAN-96/114/115/120/124/117).
* 기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지(측정 전용).
