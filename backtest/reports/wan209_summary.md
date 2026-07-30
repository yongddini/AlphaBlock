# WAN-209 즉사/손절 부검의 남은 세 축 — 거래량 · 상위TF 장세 · 볼린저 하단 기울기

9종목 × 15m, 1h, 4h, 못 박은 6년 창 **2020-09-15 ~ 2026-07-22**, 오늘의 채택 기본값(핀 없는 `ConfluenceParams()`·`OrderBlockParams()`) · 렌즈 `baseline` 단독. WAN-150 라벨(즉사=손절 & MFE<0.5R · 애매=손절 & 0.5R≤MFE · 승자=1.5R 익절)을 그대로 쓰고 §A(거래량)·§B(상위TF 장세)·§C(볼린저 하단 기울기) 특징만 얹었다. 재현: `python -m backtest.wan209_death_autopsy_axes`. 라벨 원자료: `backtest/reports/wan209_labeled.csv`.

## §0 라벨 재생성 검산 (WAN-150 엔진 재사용)

라벨링된 거래 **10623건** = 즉사 1194 · 애매 3283 · 승자 6146. 시퀀서 거래 10623건. **공유 특징(존폭·손절폭·`volume_pctl`·…)은 `wan150_labeled.csv`와 비트 동일해야 한다**(회귀 테스트 + `--checksum`).

## 축별 판정 — 즉사가 진입 시점에 보이는가

### §A — 거래량

* **§A 15m**: **(b) 즉사가 안 보인다** — 2개 특징 중 어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 못한다.
* **§A 1h**: **(b) 즉사가 안 보인다** — 2개 특징 중 어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 못한다.
* **§A 4h**: **(b) 즉사가 안 보인다** — 2개 특징 중 어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 못한다.

| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `rvol_sma20` | 5280 | 9.6% | +0.011 | 0.3965 | +0.009 | 0.5995 | ✗ |
| 15m | is | `rvol_sma50` | 5280 | 9.6% | +0.009 | 0.4915 | +0.006 | 0.7285 | ✗ |
| 15m | oos | `rvol_sma20` | 2567 | 14.1% | +0.027 | 0.2135 | +0.023 | 0.3725 | ✗ |
| 15m | oos | `rvol_sma50` | 2567 | 14.1% | +0.019 | 0.3925 | +0.010 | 0.6935 | ✗ |
| 1h | is | `rvol_sma20` | 1448 | 10.5% | +0.014 | 0.6085 | +0.006 | 0.8360 | ✗ |
| 1h | is | `rvol_sma50` | 1448 | 10.5% | +0.029 | 0.2660 | +0.021 | 0.5025 | ✗ |
| 1h | oos | `rvol_sma20` | 769 | 14.0% | +0.017 | 0.6415 | -0.006 | 0.8815 | ✗ |
| 1h | oos | `rvol_sma50` | 769 | 14.0% | -0.003 | 0.9230 | -0.024 | 0.5605 | ○ |
| 4h | is | `rvol_sma20` | 343 | 11.1% | -0.046 | 0.3785 | -0.056 | 0.3420 | ○ |
| 4h | is | `rvol_sma50` | 343 | 11.1% | -0.100 | 0.0555 | -0.119 | 0.0500 | ○ |
| 4h | oos | `rvol_sma20` | 215 | 11.6% | -0.065 | 0.3475 | -0.077 | 0.3835 | ○ |
| 4h | oos | `rvol_sma50` | 215 | 11.6% | -0.091 | 0.2000 | -0.112 | 0.1905 | ○ |

### §B — 상위TF 장세

* **§B 15m**: **(b) 즉사가 안 보인다** — 8개 특징 중 어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 못한다.
* **§B 1h**: **(b) 즉사가 안 보인다** — 8개 특징 중 어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 못한다.
* **§B 4h**: **(b) 즉사가 안 보인다** — 4개 특징 중 어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 못한다.

| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `reg_4h_trend` | 5280 | 9.6% | -0.017 | 0.2245 | -0.021 | 0.2095 | ○ |
| 15m | is | `reg_4h_ema_slope` | 5280 | 9.6% | -0.008 | 0.5565 | -0.012 | 0.4925 | ○ |
| 15m | is | `reg_4h_vol_pctl` | 5279 | 9.6% | +0.001 | 0.9625 | -0.006 | 0.7205 | ○ |
| 15m | is | `reg_4h_dev_pctl` | 5280 | 9.6% | -0.029 | 0.0395 | -0.035 | 0.0285 | ✗ |
| 15m | is | `reg_1d_trend` | 5280 | 9.6% | -0.021 | 0.1280 | -0.027 | 0.1070 | ○ |
| 15m | is | `reg_1d_ema_slope` | 5245 | 9.6% | -0.006 | 0.6705 | -0.009 | 0.5905 | ○ |
| 15m | is | `reg_1d_vol_pctl` | 5236 | 9.6% | +0.006 | 0.6485 | +0.013 | 0.4580 | ○ |
| 15m | is | `reg_1d_dev_pctl` | 5280 | 9.6% | +0.003 | 0.8005 | +0.005 | 0.7675 | ○ |
| 15m | oos | `reg_4h_trend` | 2567 | 14.1% | -0.009 | 0.6435 | -0.018 | 0.4640 | ○ |
| 15m | oos | `reg_4h_ema_slope` | 2567 | 14.1% | -0.008 | 0.7215 | -0.018 | 0.4705 | ○ |
| 15m | oos | `reg_4h_vol_pctl` | 2567 | 14.1% | -0.010 | 0.6200 | -0.021 | 0.3740 | ✗ |
| 15m | oos | `reg_4h_dev_pctl` | 2567 | 14.1% | -0.007 | 0.7560 | -0.018 | 0.4595 | ✗ |
| 15m | oos | `reg_1d_trend` | 2567 | 14.1% | +0.021 | 0.5180 | +0.008 | 0.8105 | ✗ |
| 15m | oos | `reg_1d_ema_slope` | 2567 | 14.1% | +0.037 | 0.2975 | +0.029 | 0.5110 | ✗ |
| 15m | oos | `reg_1d_vol_pctl` | 2567 | 14.1% | +0.016 | 0.4575 | +0.021 | 0.4015 | ○ |
| 15m | oos | `reg_1d_dev_pctl` | 2567 | 14.1% | -0.000 | 0.9940 | -0.003 | 0.8880 | ✗ |
| 1h | is | `reg_4h_trend` | 1448 | 10.5% | -0.053 | 0.0440 | -0.063 | 0.0580 | ○ |
| 1h | is | `reg_4h_ema_slope` | 1448 | 10.5% | -0.070 | 0.0135 | -0.084 | 0.0135 | ○ |
| 1h | is | `reg_4h_vol_pctl` | 1448 | 10.5% | +0.043 | 0.1055 | +0.029 | 0.3395 | ○ |
| 1h | is | `reg_4h_dev_pctl` | 1448 | 10.5% | +0.030 | 0.2470 | +0.015 | 0.6475 | ○ |
| 1h | is | `reg_1d_trend` | 1448 | 10.5% | -0.035 | 0.1835 | -0.048 | 0.1225 | ○ |
| 1h | is | `reg_1d_ema_slope` | 1447 | 10.5% | -0.034 | 0.2775 | -0.046 | 0.2345 | ○ |
| 1h | is | `reg_1d_vol_pctl` | 1446 | 10.5% | +0.076 | 0.0020 | +0.084 | 0.0085 | ○ |
| 1h | is | `reg_1d_dev_pctl` | 1448 | 10.5% | +0.023 | 0.3995 | +0.027 | 0.3845 | ○ |
| 1h | oos | `reg_4h_trend` | 769 | 14.0% | -0.009 | 0.8070 | -0.001 | 0.9890 | ○ |
| 1h | oos | `reg_4h_ema_slope` | 769 | 14.0% | -0.008 | 0.8120 | -0.004 | 0.9270 | ○ |
| 1h | oos | `reg_4h_vol_pctl` | 769 | 14.0% | -0.084 | 0.0170 | -0.132 | 0.0000 | ✗ |
| 1h | oos | `reg_4h_dev_pctl` | 769 | 14.0% | -0.087 | 0.0160 | -0.138 | 0.0025 | ✗ |
| 1h | oos | `reg_1d_trend` | 769 | 14.0% | +0.035 | 0.3730 | +0.033 | 0.4695 | ✗ |
| 1h | oos | `reg_1d_ema_slope` | 769 | 14.0% | +0.043 | 0.2770 | +0.034 | 0.4390 | ✗ |
| 1h | oos | `reg_1d_vol_pctl` | 769 | 14.0% | +0.009 | 0.8045 | +0.008 | 0.8545 | ○ |
| 1h | oos | `reg_1d_dev_pctl` | 769 | 14.0% | +0.030 | 0.4085 | +0.028 | 0.5075 | ○ |
| 4h | is | `reg_4h_trend` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | is | `reg_4h_ema_slope` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | is | `reg_4h_vol_pctl` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | is | `reg_4h_dev_pctl` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | is | `reg_1d_trend` | 344 | 11.0% | -0.136 | 0.0085 | -0.161 | 0.0120 | ○ |
| 4h | is | `reg_1d_ema_slope` | 343 | 11.1% | -0.170 | 0.0010 | -0.200 | 0.0025 | ○ |
| 4h | is | `reg_1d_vol_pctl` | 343 | 11.1% | +0.012 | 0.8175 | +0.027 | 0.6585 | ○ |
| 4h | is | `reg_1d_dev_pctl` | 344 | 11.0% | +0.012 | 0.8265 | +0.020 | 0.7635 | ○ |
| 4h | oos | `reg_4h_trend` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | oos | `reg_4h_ema_slope` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | oos | `reg_4h_vol_pctl` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | oos | `reg_4h_dev_pctl` | 0 | 0.0% | — | — | — | — | ✗ |
| 4h | oos | `reg_1d_trend` | 215 | 11.6% | +0.029 | 0.6275 | +0.032 | 0.6575 | ✗ |
| 4h | oos | `reg_1d_ema_slope` | 215 | 11.6% | +0.074 | 0.2115 | +0.095 | 0.1710 | ✗ |
| 4h | oos | `reg_1d_vol_pctl` | 215 | 11.6% | -0.061 | 0.3730 | -0.124 | 0.1095 | ✗ |
| 4h | oos | `reg_1d_dev_pctl` | 215 | 11.6% | -0.084 | 0.2210 | -0.132 | 0.1230 | ✗ |

### §C — 볼린저 하단 기울기

* **§C 15m**: **(a) 즉사 축을 넘고 존폭/손절폭 통제 뒤에도 남는 특징 있음** — `band_lower_slope_3_atr`, `band_lower_slope_3_pct`, `band_lower_slope_5_atr`, `band_lower_slope_5_pct`. ⚠️ 「선별」 대 「가격」은 미분리(후속 이슈·사용자 결정).
* **§C 1h**: **(c) 즉사 축은 넘지만 기하(존폭/손절폭)의 대리변수** — `band_lower_slope_3_atr`, `band_lower_slope_3_pct`, `band_lower_slope_5_atr`, `band_lower_slope_5_pct`가 무작위를 넘되 통제 부분상관이 OOS에서 무너진다(WAN-133/152 계열).
* **§C 4h**: **(b) 즉사가 안 보인다** — 4개 특징 중 어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 못한다.

| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |
| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `band_lower_slope_3_atr` | 5280 | 9.6% | +0.052 | 0.0000 | +0.077 | 0.0000 | ✗ |
| 15m | is | `band_lower_slope_3_pct` | 5280 | 9.6% | +0.051 | 0.0010 | +0.077 | 0.0000 | ✗ |
| 15m | is | `band_lower_slope_5_atr` | 5280 | 9.6% | +0.047 | 0.0010 | +0.069 | 0.0000 | ✗ |
| 15m | is | `band_lower_slope_5_pct` | 5280 | 9.6% | +0.049 | 0.0010 | +0.073 | 0.0000 | ✗ |
| 15m | oos | `band_lower_slope_3_atr` | 2567 | 14.1% | +0.093 | 0.0000 | +0.130 | 0.0000 | ✗ |
| 15m | oos | `band_lower_slope_3_pct` | 2567 | 14.1% | +0.093 | 0.0000 | +0.129 | 0.0000 | ✗ |
| 15m | oos | `band_lower_slope_5_atr` | 2567 | 14.1% | +0.091 | 0.0000 | +0.127 | 0.0000 | ✗ |
| 15m | oos | `band_lower_slope_5_pct` | 2567 | 14.1% | +0.090 | 0.0000 | +0.126 | 0.0000 | ✗ |
| 1h | is | `band_lower_slope_3_atr` | 1448 | 10.5% | +0.060 | 0.0240 | +0.092 | 0.0025 | ✗ |
| 1h | is | `band_lower_slope_3_pct` | 1448 | 10.5% | +0.051 | 0.0590 | +0.079 | 0.0135 | ✗ |
| 1h | is | `band_lower_slope_5_atr` | 1448 | 10.5% | +0.057 | 0.0295 | +0.092 | 0.0030 | ✗ |
| 1h | is | `band_lower_slope_5_pct` | 1448 | 10.5% | +0.048 | 0.0790 | +0.077 | 0.0195 | ✗ |
| 1h | oos | `band_lower_slope_3_atr` | 769 | 14.0% | +0.053 | 0.1270 | +0.082 | 0.0495 | ✗ |
| 1h | oos | `band_lower_slope_3_pct` | 769 | 14.0% | +0.057 | 0.1020 | +0.093 | 0.0285 | ✗ |
| 1h | oos | `band_lower_slope_5_atr` | 769 | 14.0% | +0.060 | 0.0935 | +0.089 | 0.0350 | ✗ |
| 1h | oos | `band_lower_slope_5_pct` | 769 | 14.0% | +0.052 | 0.1415 | +0.087 | 0.0375 | ✗ |
| 4h | is | `band_lower_slope_3_atr` | 344 | 11.0% | +0.116 | 0.0295 | +0.122 | 0.0520 | ✗ |
| 4h | is | `band_lower_slope_3_pct` | 344 | 11.0% | +0.096 | 0.0720 | +0.103 | 0.1000 | ✗ |
| 4h | is | `band_lower_slope_5_atr` | 344 | 11.0% | +0.114 | 0.0365 | +0.122 | 0.0490 | ✗ |
| 4h | is | `band_lower_slope_5_pct` | 344 | 11.0% | +0.093 | 0.0845 | +0.099 | 0.1070 | ✗ |
| 4h | oos | `band_lower_slope_3_atr` | 215 | 11.6% | +0.031 | 0.6600 | +0.029 | 0.7225 | ✗ |
| 4h | oos | `band_lower_slope_3_pct` | 215 | 11.6% | +0.079 | 0.2640 | +0.093 | 0.2660 | ✗ |
| 4h | oos | `band_lower_slope_5_atr` | 215 | 11.6% | +0.011 | 0.8875 | +0.010 | 0.9030 | ✗ |
| 4h | oos | `band_lower_slope_5_pct` | 215 | 11.6% | +0.063 | 0.3765 | +0.076 | 0.3685 | ✗ |

## §A 문턱 스윕 (RVOL < 문턱 대 ≥ 문턱 즉사율)

Δ>0 = 한산한 존(저 RVOL)이 더 자주 즉사(가설 방향). ⚠️ 문턱은 IS에서 고르고 OOS로 검증 — 단독 비율 차이는 채택 근거가 아니다.

| TF | 구간 | 특징 | 문턱 | n(저/고) | 즉사%(저) | 즉사%(고) | Δ(저−고) |
| -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `rvol_sma20` | 0.6 | 621/4659 | 7.9% | 9.9% | -2.0%p |
| 15m | is | `rvol_sma20` | 0.8 | 1474/3806 | 8.9% | 9.9% | -1.0%p |
| 15m | is | `rvol_sma20` | 1.0 | 2347/2933 | 9.2% | 10.0% | -0.9%p |
| 15m | is | `rvol_sma20` | 1.2 | 3169/2111 | 9.5% | 9.9% | -0.4%p |
| 15m | is | `rvol_sma50` | 0.6 | 948/4332 | 6.8% | 10.3% | -3.5%p |
| 15m | is | `rvol_sma50` | 0.8 | 1884/3396 | 8.9% | 10.1% | -1.2%p |
| 15m | is | `rvol_sma50` | 1.0 | 2787/2493 | 9.2% | 10.1% | -1.0%p |
| 15m | is | `rvol_sma50` | 1.2 | 3516/1764 | 9.2% | 10.4% | -1.2%p |
| 15m | oos | `rvol_sma20` | 0.6 | 305/2262 | 13.8% | 14.1% | -0.4%p |
| 15m | oos | `rvol_sma20` | 0.8 | 663/1904 | 11.9% | 14.9% | -2.9%p |
| 15m | oos | `rvol_sma20` | 1.0 | 1018/1549 | 12.7% | 15.0% | -2.4%p |
| 15m | oos | `rvol_sma20` | 1.2 | 1396/1171 | 13.5% | 14.9% | -1.4%p |
| 15m | oos | `rvol_sma50` | 0.6 | 460/2107 | 14.1% | 14.1% | +0.0%p |
| 15m | oos | `rvol_sma50` | 0.8 | 886/1681 | 11.7% | 15.3% | -3.6%p |
| 15m | oos | `rvol_sma50` | 1.0 | 1225/1342 | 13.1% | 15.0% | -1.8%p |
| 15m | oos | `rvol_sma50` | 1.2 | 1530/1037 | 13.9% | 14.4% | -0.4%p |
| 1h | is | `rvol_sma20` | 0.6 | 134/1314 | 6.7% | 10.9% | -4.2%p |
| 1h | is | `rvol_sma20` | 0.8 | 389/1059 | 8.5% | 11.2% | -2.8%p |
| 1h | is | `rvol_sma20` | 1.0 | 644/804 | 9.6% | 11.2% | -1.6%p |
| 1h | is | `rvol_sma20` | 1.2 | 889/559 | 10.8% | 10.0% | +0.8%p |
| 1h | is | `rvol_sma50` | 0.6 | 238/1210 | 6.3% | 11.3% | -5.0%p |
| 1h | is | `rvol_sma50` | 0.8 | 489/959 | 8.4% | 11.6% | -3.2%p |
| 1h | is | `rvol_sma50` | 1.0 | 779/669 | 9.4% | 11.8% | -2.4%p |
| 1h | is | `rvol_sma50` | 1.2 | 979/469 | 10.1% | 11.3% | -1.2%p |
| 1h | oos | `rvol_sma20` | 0.6 | 102/667 | 13.7% | 14.1% | -0.4%p |
| 1h | oos | `rvol_sma20` | 0.8 | 245/524 | 13.9% | 14.1% | -0.2%p |
| 1h | oos | `rvol_sma20` | 1.0 | 350/419 | 13.4% | 14.6% | -1.1%p |
| 1h | oos | `rvol_sma20` | 1.2 | 450/319 | 12.7% | 16.0% | -3.3%p |
| 1h | oos | `rvol_sma50` | 0.6 | 146/623 | 12.3% | 14.4% | -2.1%p |
| 1h | oos | `rvol_sma50` | 0.8 | 268/501 | 13.8% | 14.2% | -0.4%p |
| 1h | oos | `rvol_sma50` | 1.0 | 406/363 | 14.8% | 13.2% | +1.6%p |
| 1h | oos | `rvol_sma50` | 1.2 | 522/247 | 14.2% | 13.8% | +0.4%p |
| 4h | is | `rvol_sma20` | 0.6 | 30/313 | 6.7% | 11.5% | -4.8%p |
| 4h | is | `rvol_sma20` | 0.8 | 78/265 | 10.3% | 11.3% | -1.1%p |
| 4h | is | `rvol_sma20` | 1.0 | 146/197 | 11.0% | 11.2% | -0.2%p |
| 4h | is | `rvol_sma20` | 1.2 | 222/121 | 11.7% | 9.9% | +1.8%p |
| 4h | is | `rvol_sma50` | 0.6 | 40/303 | 10.0% | 11.2% | -1.2%p |
| 4h | is | `rvol_sma50` | 0.8 | 107/236 | 12.1% | 10.6% | +1.6%p |
| 4h | is | `rvol_sma50` | 1.0 | 184/159 | 14.1% | 7.5% | +6.6%p |
| 4h | is | `rvol_sma50` | 1.2 | 239/104 | 13.4% | 5.8% | +7.6%p |
| 4h | oos | `rvol_sma20` | 0.6 | 22/193 | 13.6% | 11.4% | +2.2%p |
| 4h | oos | `rvol_sma20` | 0.8 | 54/161 | 13.0% | 11.2% | +1.8%p |
| 4h | oos | `rvol_sma20` | 1.0 | 94/121 | 10.6% | 12.4% | -1.8%p |
| 4h | oos | `rvol_sma20` | 1.2 | 126/89 | 11.1% | 12.4% | -1.2%p |
| 4h | oos | `rvol_sma50` | 0.6 | 22/193 | 18.2% | 10.9% | +7.3%p |
| 4h | oos | `rvol_sma50` | 0.8 | 61/154 | 14.8% | 10.4% | +4.4%p |
| 4h | oos | `rvol_sma50` | 1.0 | 96/119 | 13.5% | 10.1% | +3.5%p |
| 4h | oos | `rvol_sma50` | 1.2 | 132/83 | 12.9% | 9.6% | +3.2%p |

## 존폭/손절폭 공선성 (대리변수 위험)

corr(특징, 통제 변수). |corr|이 크면 그 축은 `zone_width_atr`/`stop_width_atr`의 대리변수일 수 있다(WAN-150이 그 기하 축 하나만 강건 판정).

| TF | 구간 | 특징 | 통제 | n | corr |
| -- | -- | -- | -- | -- | -- |
| 15m | is | `rvol_sma20` | `zone_width_atr` | 5280 | +0.100 |
| 15m | is | `rvol_sma20` | `stop_width_atr` | 5280 | +0.077 |
| 15m | is | `rvol_sma50` | `zone_width_atr` | 5280 | +0.103 |
| 15m | is | `rvol_sma50` | `stop_width_atr` | 5280 | +0.071 |
| 15m | is | `reg_4h_trend` | `zone_width_atr` | 5280 | +0.048 |
| 15m | is | `reg_4h_trend` | `stop_width_atr` | 5280 | +0.007 |
| 15m | is | `reg_4h_ema_slope` | `zone_width_atr` | 5280 | +0.040 |
| 15m | is | `reg_4h_ema_slope` | `stop_width_atr` | 5280 | +0.028 |
| 15m | is | `reg_4h_vol_pctl` | `zone_width_atr` | 5279 | -0.155 |
| 15m | is | `reg_4h_vol_pctl` | `stop_width_atr` | 5279 | -0.266 |
| 15m | is | `reg_4h_dev_pctl` | `zone_width_atr` | 5280 | -0.103 |
| 15m | is | `reg_4h_dev_pctl` | `stop_width_atr` | 5280 | -0.164 |
| 15m | is | `reg_1d_trend` | `zone_width_atr` | 5280 | -0.008 |
| 15m | is | `reg_1d_trend` | `stop_width_atr` | 5280 | -0.052 |
| 15m | is | `reg_1d_ema_slope` | `zone_width_atr` | 5245 | -0.005 |
| 15m | is | `reg_1d_ema_slope` | `stop_width_atr` | 5245 | -0.025 |
| 15m | is | `reg_1d_vol_pctl` | `zone_width_atr` | 5236 | -0.065 |
| 15m | is | `reg_1d_vol_pctl` | `stop_width_atr` | 5236 | -0.150 |
| 15m | is | `reg_1d_dev_pctl` | `zone_width_atr` | 5280 | -0.061 |
| 15m | is | `reg_1d_dev_pctl` | `stop_width_atr` | 5280 | -0.111 |
| 15m | is | `band_lower_slope_3_atr` | `zone_width_atr` | 5280 | +0.179 |
| 15m | is | `band_lower_slope_3_atr` | `stop_width_atr` | 5280 | +0.073 |
| 15m | is | `band_lower_slope_3_pct` | `zone_width_atr` | 5280 | +0.275 |
| 15m | is | `band_lower_slope_3_pct` | `stop_width_atr` | 5280 | +0.200 |
| 15m | is | `band_lower_slope_5_atr` | `zone_width_atr` | 5280 | +0.167 |
| 15m | is | `band_lower_slope_5_atr` | `stop_width_atr` | 5280 | +0.093 |
| 15m | is | `band_lower_slope_5_pct` | `zone_width_atr` | 5280 | +0.262 |
| 15m | is | `band_lower_slope_5_pct` | `stop_width_atr` | 5280 | +0.204 |
| 15m | oos | `rvol_sma20` | `zone_width_atr` | 2567 | +0.148 |
| 15m | oos | `rvol_sma20` | `stop_width_atr` | 2567 | +0.113 |
| 15m | oos | `rvol_sma50` | `zone_width_atr` | 2567 | +0.149 |
| 15m | oos | `rvol_sma50` | `stop_width_atr` | 2567 | +0.117 |
| 15m | oos | `reg_4h_trend` | `zone_width_atr` | 2567 | +0.071 |
| 15m | oos | `reg_4h_trend` | `stop_width_atr` | 2567 | +0.034 |
| 15m | oos | `reg_4h_ema_slope` | `zone_width_atr` | 2567 | +0.069 |
| 15m | oos | `reg_4h_ema_slope` | `stop_width_atr` | 2567 | +0.050 |
| 15m | oos | `reg_4h_vol_pctl` | `zone_width_atr` | 2567 | -0.193 |
| 15m | oos | `reg_4h_vol_pctl` | `stop_width_atr` | 2567 | -0.287 |
| 15m | oos | `reg_4h_dev_pctl` | `zone_width_atr` | 2567 | -0.077 |
| 15m | oos | `reg_4h_dev_pctl` | `stop_width_atr` | 2567 | -0.150 |
| 15m | oos | `reg_1d_trend` | `zone_width_atr` | 2567 | +0.001 |
| 15m | oos | `reg_1d_trend` | `stop_width_atr` | 2567 | -0.037 |
| 15m | oos | `reg_1d_ema_slope` | `zone_width_atr` | 2567 | -0.018 |
| 15m | oos | `reg_1d_ema_slope` | `stop_width_atr` | 2567 | -0.043 |
| 15m | oos | `reg_1d_vol_pctl` | `zone_width_atr` | 2567 | -0.110 |
| 15m | oos | `reg_1d_vol_pctl` | `stop_width_atr` | 2567 | -0.178 |
| 15m | oos | `reg_1d_dev_pctl` | `zone_width_atr` | 2567 | -0.011 |
| 15m | oos | `reg_1d_dev_pctl` | `stop_width_atr` | 2567 | -0.034 |
| 15m | oos | `band_lower_slope_3_atr` | `zone_width_atr` | 2567 | +0.149 |
| 15m | oos | `band_lower_slope_3_atr` | `stop_width_atr` | 2567 | +0.062 |
| 15m | oos | `band_lower_slope_3_pct` | `zone_width_atr` | 2567 | +0.302 |
| 15m | oos | `band_lower_slope_3_pct` | `stop_width_atr` | 2567 | +0.235 |
| 15m | oos | `band_lower_slope_5_atr` | `zone_width_atr` | 2567 | +0.128 |
| 15m | oos | `band_lower_slope_5_atr` | `stop_width_atr` | 2567 | +0.070 |
| 15m | oos | `band_lower_slope_5_pct` | `zone_width_atr` | 2567 | +0.274 |
| 15m | oos | `band_lower_slope_5_pct` | `stop_width_atr` | 2567 | +0.227 |
| 1h | is | `rvol_sma20` | `zone_width_atr` | 1448 | +0.156 |
| 1h | is | `rvol_sma20` | `stop_width_atr` | 1448 | +0.136 |
| 1h | is | `rvol_sma50` | `zone_width_atr` | 1448 | +0.167 |
| 1h | is | `rvol_sma50` | `stop_width_atr` | 1448 | +0.138 |
| 1h | is | `reg_4h_trend` | `zone_width_atr` | 1448 | +0.117 |
| 1h | is | `reg_4h_trend` | `stop_width_atr` | 1448 | +0.099 |
| 1h | is | `reg_4h_ema_slope` | `zone_width_atr` | 1448 | +0.078 |
| 1h | is | `reg_4h_ema_slope` | `stop_width_atr` | 1448 | +0.090 |
| 1h | is | `reg_4h_vol_pctl` | `zone_width_atr` | 1448 | -0.191 |
| 1h | is | `reg_4h_vol_pctl` | `stop_width_atr` | 1448 | -0.233 |
| 1h | is | `reg_4h_dev_pctl` | `zone_width_atr` | 1448 | -0.090 |
| 1h | is | `reg_4h_dev_pctl` | `stop_width_atr` | 1448 | -0.128 |
| 1h | is | `reg_1d_trend` | `zone_width_atr` | 1448 | -0.000 |
| 1h | is | `reg_1d_trend` | `stop_width_atr` | 1448 | -0.006 |
| 1h | is | `reg_1d_ema_slope` | `zone_width_atr` | 1447 | +0.025 |
| 1h | is | `reg_1d_ema_slope` | `stop_width_atr` | 1447 | +0.022 |
| 1h | is | `reg_1d_vol_pctl` | `zone_width_atr` | 1446 | -0.082 |
| 1h | is | `reg_1d_vol_pctl` | `stop_width_atr` | 1446 | -0.125 |
| 1h | is | `reg_1d_dev_pctl` | `zone_width_atr` | 1448 | -0.035 |
| 1h | is | `reg_1d_dev_pctl` | `stop_width_atr` | 1448 | -0.065 |
| 1h | is | `band_lower_slope_3_atr` | `zone_width_atr` | 1448 | +0.246 |
| 1h | is | `band_lower_slope_3_atr` | `stop_width_atr` | 1448 | +0.105 |
| 1h | is | `band_lower_slope_3_pct` | `zone_width_atr` | 1448 | +0.304 |
| 1h | is | `band_lower_slope_3_pct` | `stop_width_atr` | 1448 | +0.198 |
| 1h | is | `band_lower_slope_5_atr` | `zone_width_atr` | 1448 | +0.230 |
| 1h | is | `band_lower_slope_5_atr` | `stop_width_atr` | 1448 | +0.127 |
| 1h | is | `band_lower_slope_5_pct` | `zone_width_atr` | 1448 | +0.295 |
| 1h | is | `band_lower_slope_5_pct` | `stop_width_atr` | 1448 | +0.210 |
| 1h | oos | `rvol_sma20` | `zone_width_atr` | 769 | +0.190 |
| 1h | oos | `rvol_sma20` | `stop_width_atr` | 769 | +0.159 |
| 1h | oos | `rvol_sma50` | `zone_width_atr` | 769 | +0.207 |
| 1h | oos | `rvol_sma50` | `stop_width_atr` | 769 | +0.174 |
| 1h | oos | `reg_4h_trend` | `zone_width_atr` | 769 | +0.079 |
| 1h | oos | `reg_4h_trend` | `stop_width_atr` | 769 | +0.049 |
| 1h | oos | `reg_4h_ema_slope` | `zone_width_atr` | 769 | +0.036 |
| 1h | oos | `reg_4h_ema_slope` | `stop_width_atr` | 769 | +0.025 |
| 1h | oos | `reg_4h_vol_pctl` | `zone_width_atr` | 769 | -0.113 |
| 1h | oos | `reg_4h_vol_pctl` | `stop_width_atr` | 769 | -0.165 |
| 1h | oos | `reg_4h_dev_pctl` | `zone_width_atr` | 769 | +0.014 |
| 1h | oos | `reg_4h_dev_pctl` | `stop_width_atr` | 769 | +0.006 |
| 1h | oos | `reg_1d_trend` | `zone_width_atr` | 769 | +0.047 |
| 1h | oos | `reg_1d_trend` | `stop_width_atr` | 769 | +0.018 |
| 1h | oos | `reg_1d_ema_slope` | `zone_width_atr` | 769 | +0.011 |
| 1h | oos | `reg_1d_ema_slope` | `stop_width_atr` | 769 | +0.005 |
| 1h | oos | `reg_1d_vol_pctl` | `zone_width_atr` | 769 | +0.029 |
| 1h | oos | `reg_1d_vol_pctl` | `stop_width_atr` | 769 | -0.047 |
| 1h | oos | `reg_1d_dev_pctl` | `zone_width_atr` | 769 | +0.059 |
| 1h | oos | `reg_1d_dev_pctl` | `stop_width_atr` | 769 | +0.042 |
| 1h | oos | `band_lower_slope_3_atr` | `zone_width_atr` | 769 | +0.196 |
| 1h | oos | `band_lower_slope_3_atr` | `stop_width_atr` | 769 | +0.063 |
| 1h | oos | `band_lower_slope_3_pct` | `zone_width_atr` | 769 | +0.239 |
| 1h | oos | `band_lower_slope_3_pct` | `stop_width_atr` | 769 | +0.130 |
| 1h | oos | `band_lower_slope_5_atr` | `zone_width_atr` | 769 | +0.213 |
| 1h | oos | `band_lower_slope_5_atr` | `stop_width_atr` | 769 | +0.094 |
| 1h | oos | `band_lower_slope_5_pct` | `zone_width_atr` | 769 | +0.251 |
| 1h | oos | `band_lower_slope_5_pct` | `stop_width_atr` | 769 | +0.151 |
| 4h | is | `rvol_sma20` | `zone_width_atr` | 343 | +0.153 |
| 4h | is | `rvol_sma20` | `stop_width_atr` | 343 | +0.177 |
| 4h | is | `rvol_sma50` | `zone_width_atr` | 343 | +0.081 |
| 4h | is | `rvol_sma50` | `stop_width_atr` | 343 | +0.093 |
| 4h | is | `reg_4h_trend` | `zone_width_atr` | 0 | — |
| 4h | is | `reg_4h_trend` | `stop_width_atr` | 0 | — |
| 4h | is | `reg_4h_ema_slope` | `zone_width_atr` | 0 | — |
| 4h | is | `reg_4h_ema_slope` | `stop_width_atr` | 0 | — |
| 4h | is | `reg_4h_vol_pctl` | `zone_width_atr` | 0 | — |
| 4h | is | `reg_4h_vol_pctl` | `stop_width_atr` | 0 | — |
| 4h | is | `reg_4h_dev_pctl` | `zone_width_atr` | 0 | — |
| 4h | is | `reg_4h_dev_pctl` | `stop_width_atr` | 0 | — |
| 4h | is | `reg_1d_trend` | `zone_width_atr` | 344 | +0.072 |
| 4h | is | `reg_1d_trend` | `stop_width_atr` | 344 | +0.079 |
| 4h | is | `reg_1d_ema_slope` | `zone_width_atr` | 343 | +0.041 |
| 4h | is | `reg_1d_ema_slope` | `stop_width_atr` | 343 | +0.081 |
| 4h | is | `reg_1d_vol_pctl` | `zone_width_atr` | 343 | -0.178 |
| 4h | is | `reg_1d_vol_pctl` | `stop_width_atr` | 343 | -0.161 |
| 4h | is | `reg_1d_dev_pctl` | `zone_width_atr` | 344 | -0.054 |
| 4h | is | `reg_1d_dev_pctl` | `stop_width_atr` | 344 | -0.048 |
| 4h | is | `band_lower_slope_3_atr` | `zone_width_atr` | 344 | +0.256 |
| 4h | is | `band_lower_slope_3_atr` | `stop_width_atr` | 344 | +0.090 |
| 4h | is | `band_lower_slope_3_pct` | `zone_width_atr` | 344 | +0.361 |
| 4h | is | `band_lower_slope_3_pct` | `stop_width_atr` | 344 | +0.222 |
| 4h | is | `band_lower_slope_5_atr` | `zone_width_atr` | 344 | +0.262 |
| 4h | is | `band_lower_slope_5_atr` | `stop_width_atr` | 344 | +0.127 |
| 4h | is | `band_lower_slope_5_pct` | `zone_width_atr` | 344 | +0.358 |
| 4h | is | `band_lower_slope_5_pct` | `stop_width_atr` | 344 | +0.234 |
| 4h | oos | `rvol_sma20` | `zone_width_atr` | 215 | +0.166 |
| 4h | oos | `rvol_sma20` | `stop_width_atr` | 215 | +0.209 |
| 4h | oos | `rvol_sma50` | `zone_width_atr` | 215 | +0.185 |
| 4h | oos | `rvol_sma50` | `stop_width_atr` | 215 | +0.224 |
| 4h | oos | `reg_4h_trend` | `zone_width_atr` | 0 | — |
| 4h | oos | `reg_4h_trend` | `stop_width_atr` | 0 | — |
| 4h | oos | `reg_4h_ema_slope` | `zone_width_atr` | 0 | — |
| 4h | oos | `reg_4h_ema_slope` | `stop_width_atr` | 0 | — |
| 4h | oos | `reg_4h_vol_pctl` | `zone_width_atr` | 0 | — |
| 4h | oos | `reg_4h_vol_pctl` | `stop_width_atr` | 0 | — |
| 4h | oos | `reg_4h_dev_pctl` | `zone_width_atr` | 0 | — |
| 4h | oos | `reg_4h_dev_pctl` | `stop_width_atr` | 0 | — |
| 4h | oos | `reg_1d_trend` | `zone_width_atr` | 215 | +0.032 |
| 4h | oos | `reg_1d_trend` | `stop_width_atr` | 215 | -0.024 |
| 4h | oos | `reg_1d_ema_slope` | `zone_width_atr` | 215 | +0.034 |
| 4h | oos | `reg_1d_ema_slope` | `stop_width_atr` | 215 | +0.013 |
| 4h | oos | `reg_1d_vol_pctl` | `zone_width_atr` | 215 | -0.109 |
| 4h | oos | `reg_1d_vol_pctl` | `stop_width_atr` | 215 | -0.203 |
| 4h | oos | `reg_1d_dev_pctl` | `zone_width_atr` | 215 | -0.059 |
| 4h | oos | `reg_1d_dev_pctl` | `stop_width_atr` | 215 | -0.125 |
| 4h | oos | `band_lower_slope_3_atr` | `zone_width_atr` | 215 | +0.173 |
| 4h | oos | `band_lower_slope_3_atr` | `stop_width_atr` | 215 | +0.055 |
| 4h | oos | `band_lower_slope_3_pct` | `zone_width_atr` | 215 | +0.217 |
| 4h | oos | `band_lower_slope_3_pct` | `stop_width_atr` | 215 | +0.105 |
| 4h | oos | `band_lower_slope_5_atr` | `zone_width_atr` | 215 | +0.137 |
| 4h | oos | `band_lower_slope_5_atr` | `stop_width_atr` | 215 | +0.049 |
| 4h | oos | `band_lower_slope_5_pct` | `zone_width_atr` | 215 | +0.176 |
| 4h | oos | `band_lower_slope_5_pct` | `stop_width_atr` | 215 | +0.089 |

## 부분상관 통제 순열 (생존자 전용 — 독립성 관문)

주 검정을 넘은 특징을 존폭·손절폭에 잔차화한 뒤 즉사와의 잔차 상관을 순열 검정. OOS p≥0.05 = 기하의 대리변수(c), p<0.05 = 통제 뒤에도 남음(a).

| TF | 구간 | 특징 | n | raw corr | partial corr | p |
| -- | -- | -- | -- | -- | -- | -- |
| 15m | is | `band_lower_slope_3_atr` | 5280 | +0.052 | +0.032 | 0.0155 |
| 15m | oos | `band_lower_slope_3_atr` | 2567 | +0.093 | +0.073 | 0.0010 |
| 15m | is | `band_lower_slope_3_pct` | 5280 | +0.051 | +0.017 | 0.2260 |
| 15m | oos | `band_lower_slope_3_pct` | 2567 | +0.093 | +0.047 | 0.0125 |
| 15m | is | `band_lower_slope_5_atr` | 5280 | +0.047 | +0.027 | 0.0445 |
| 15m | oos | `band_lower_slope_5_atr` | 2567 | +0.091 | +0.072 | 0.0010 |
| 15m | is | `band_lower_slope_5_pct` | 5280 | +0.049 | +0.016 | 0.2670 |
| 15m | oos | `band_lower_slope_5_pct` | 2567 | +0.090 | +0.047 | 0.0130 |
| 1h | is | `band_lower_slope_3_atr` | 1448 | +0.060 | +0.028 | 0.2650 |
| 1h | oos | `band_lower_slope_3_atr` | 769 | +0.053 | +0.023 | 0.5265 |
| 1h | is | `band_lower_slope_3_pct` | 1448 | +0.051 | +0.007 | 0.7985 |
| 1h | oos | `band_lower_slope_3_pct` | 769 | +0.057 | +0.018 | 0.6110 |
| 1h | is | `band_lower_slope_5_atr` | 1448 | +0.057 | +0.026 | 0.3050 |
| 1h | oos | `band_lower_slope_5_atr` | 769 | +0.060 | +0.027 | 0.4375 |
| 1h | is | `band_lower_slope_5_pct` | 1448 | +0.048 | +0.004 | 0.9015 |
| 1h | oos | `band_lower_slope_5_pct` | 769 | +0.052 | +0.011 | 0.7745 |

## leave-one-out (심볼 편중 진단 — 생존자만)

| TF | 특징 | 구간 | 제외 | n | corr |
| -- | -- | -- | -- | -- | -- |
| 15m | `band_lower_slope_3_atr` | is | BTC | 4796 | +0.045 |
| 15m | `band_lower_slope_3_atr` | is | ETH | 4777 | +0.049 |
| 15m | `band_lower_slope_3_atr` | is | SOL | 4512 | +0.058 |
| 15m | `band_lower_slope_3_atr` | is | BNB | 4698 | +0.049 |
| 15m | `band_lower_slope_3_atr` | is | XRP | 4667 | +0.059 |
| 15m | `band_lower_slope_3_atr` | is | TRX | 4830 | +0.046 |
| 15m | `band_lower_slope_3_atr` | is | DOGE | 4683 | +0.054 |
| 15m | `band_lower_slope_3_atr` | is | LINK | 4615 | +0.054 |
| 15m | `band_lower_slope_3_atr` | is | LTC | 4662 | +0.053 |
| 15m | `band_lower_slope_3_atr` | oos | BTC | 2393 | +0.090 |
| 15m | `band_lower_slope_3_atr` | oos | ETH | 2264 | +0.083 |
| 15m | `band_lower_slope_3_atr` | oos | SOL | 2163 | +0.089 |
| 15m | `band_lower_slope_3_atr` | oos | BNB | 2380 | +0.092 |
| 15m | `band_lower_slope_3_atr` | oos | XRP | 2247 | +0.105 |
| 15m | `band_lower_slope_3_atr` | oos | TRX | 2488 | +0.092 |
| 15m | `band_lower_slope_3_atr` | oos | DOGE | 2181 | +0.095 |
| 15m | `band_lower_slope_3_atr` | oos | LINK | 2195 | +0.104 |
| 15m | `band_lower_slope_3_atr` | oos | LTC | 2225 | +0.090 |
| 15m | `band_lower_slope_3_pct` | is | BTC | 4796 | +0.049 |
| 15m | `band_lower_slope_3_pct` | is | ETH | 4777 | +0.050 |
| 15m | `band_lower_slope_3_pct` | is | SOL | 4512 | +0.057 |
| 15m | `band_lower_slope_3_pct` | is | BNB | 4698 | +0.049 |
| 15m | `band_lower_slope_3_pct` | is | XRP | 4667 | +0.053 |
| 15m | `band_lower_slope_3_pct` | is | TRX | 4830 | +0.047 |
| 15m | `band_lower_slope_3_pct` | is | DOGE | 4683 | +0.054 |
| 15m | `band_lower_slope_3_pct` | is | LINK | 4615 | +0.051 |
| 15m | `band_lower_slope_3_pct` | is | LTC | 4662 | +0.052 |
| 15m | `band_lower_slope_3_pct` | oos | BTC | 2393 | +0.092 |
| 15m | `band_lower_slope_3_pct` | oos | ETH | 2264 | +0.088 |
| 15m | `band_lower_slope_3_pct` | oos | SOL | 2163 | +0.089 |
| 15m | `band_lower_slope_3_pct` | oos | BNB | 2380 | +0.093 |
| 15m | `band_lower_slope_3_pct` | oos | XRP | 2247 | +0.108 |
| 15m | `band_lower_slope_3_pct` | oos | TRX | 2488 | +0.089 |
| 15m | `band_lower_slope_3_pct` | oos | DOGE | 2181 | +0.091 |
| 15m | `band_lower_slope_3_pct` | oos | LINK | 2195 | +0.100 |
| 15m | `band_lower_slope_3_pct` | oos | LTC | 2225 | +0.090 |
| 15m | `band_lower_slope_5_atr` | is | BTC | 4796 | +0.039 |
| 15m | `band_lower_slope_5_atr` | is | ETH | 4777 | +0.045 |
| 15m | `band_lower_slope_5_atr` | is | SOL | 4512 | +0.051 |
| 15m | `band_lower_slope_5_atr` | is | BNB | 4698 | +0.045 |
| 15m | `band_lower_slope_5_atr` | is | XRP | 4667 | +0.053 |
| 15m | `band_lower_slope_5_atr` | is | TRX | 4830 | +0.040 |
| 15m | `band_lower_slope_5_atr` | is | DOGE | 4683 | +0.050 |
| 15m | `band_lower_slope_5_atr` | is | LINK | 4615 | +0.050 |
| 15m | `band_lower_slope_5_atr` | is | LTC | 4662 | +0.047 |
| 15m | `band_lower_slope_5_atr` | oos | BTC | 2393 | +0.086 |
| 15m | `band_lower_slope_5_atr` | oos | ETH | 2264 | +0.083 |
| 15m | `band_lower_slope_5_atr` | oos | SOL | 2163 | +0.086 |
| 15m | `band_lower_slope_5_atr` | oos | BNB | 2380 | +0.087 |
| 15m | `band_lower_slope_5_atr` | oos | XRP | 2247 | +0.105 |
| 15m | `band_lower_slope_5_atr` | oos | TRX | 2488 | +0.094 |
| 15m | `band_lower_slope_5_atr` | oos | DOGE | 2181 | +0.093 |
| 15m | `band_lower_slope_5_atr` | oos | LINK | 2195 | +0.097 |
| 15m | `band_lower_slope_5_atr` | oos | LTC | 2225 | +0.086 |
| 15m | `band_lower_slope_5_pct` | is | BTC | 4796 | +0.046 |
| 15m | `band_lower_slope_5_pct` | is | ETH | 4777 | +0.048 |
| 15m | `band_lower_slope_5_pct` | is | SOL | 4512 | +0.054 |
| 15m | `band_lower_slope_5_pct` | is | BNB | 4698 | +0.048 |
| 15m | `band_lower_slope_5_pct` | is | XRP | 4667 | +0.051 |
| 15m | `band_lower_slope_5_pct` | is | TRX | 4830 | +0.044 |
| 15m | `band_lower_slope_5_pct` | is | DOGE | 4683 | +0.053 |
| 15m | `band_lower_slope_5_pct` | is | LINK | 4615 | +0.049 |
| 15m | `band_lower_slope_5_pct` | is | LTC | 4662 | +0.048 |
| 15m | `band_lower_slope_5_pct` | oos | BTC | 2393 | +0.088 |
| 15m | `band_lower_slope_5_pct` | oos | ETH | 2264 | +0.086 |
| 15m | `band_lower_slope_5_pct` | oos | SOL | 2163 | +0.084 |
| 15m | `band_lower_slope_5_pct` | oos | BNB | 2380 | +0.088 |
| 15m | `band_lower_slope_5_pct` | oos | XRP | 2247 | +0.107 |
| 15m | `band_lower_slope_5_pct` | oos | TRX | 2488 | +0.088 |
| 15m | `band_lower_slope_5_pct` | oos | DOGE | 2181 | +0.089 |
| 15m | `band_lower_slope_5_pct` | oos | LINK | 2195 | +0.093 |
| 15m | `band_lower_slope_5_pct` | oos | LTC | 2225 | +0.085 |
| 1h | `band_lower_slope_3_atr` | is | BTC | 1279 | +0.058 |
| 1h | `band_lower_slope_3_atr` | is | ETH | 1289 | +0.044 |
| 1h | `band_lower_slope_3_atr` | is | SOL | 1261 | +0.058 |
| 1h | `band_lower_slope_3_atr` | is | BNB | 1306 | +0.083 |
| 1h | `band_lower_slope_3_atr` | is | XRP | 1294 | +0.054 |
| 1h | `band_lower_slope_3_atr` | is | TRX | 1286 | +0.059 |
| 1h | `band_lower_slope_3_atr` | is | DOGE | 1305 | +0.054 |
| 1h | `band_lower_slope_3_atr` | is | LINK | 1274 | +0.056 |
| 1h | `band_lower_slope_3_atr` | is | LTC | 1290 | +0.075 |
| 1h | `band_lower_slope_3_atr` | oos | BTC | 684 | +0.058 |
| 1h | `band_lower_slope_3_atr` | oos | ETH | 678 | +0.054 |
| 1h | `band_lower_slope_3_atr` | oos | SOL | 676 | +0.039 |
| 1h | `band_lower_slope_3_atr` | oos | BNB | 691 | +0.027 |
| 1h | `band_lower_slope_3_atr` | oos | XRP | 677 | +0.053 |
| 1h | `band_lower_slope_3_atr` | oos | TRX | 704 | +0.044 |
| 1h | `band_lower_slope_3_atr` | oos | DOGE | 680 | +0.061 |
| 1h | `band_lower_slope_3_atr` | oos | LINK | 677 | +0.052 |
| 1h | `band_lower_slope_3_atr` | oos | LTC | 685 | +0.086 |
| 1h | `band_lower_slope_3_pct` | is | BTC | 1279 | +0.049 |
| 1h | `band_lower_slope_3_pct` | is | ETH | 1289 | +0.040 |
| 1h | `band_lower_slope_3_pct` | is | SOL | 1261 | +0.053 |
| 1h | `band_lower_slope_3_pct` | is | BNB | 1306 | +0.063 |
| 1h | `band_lower_slope_3_pct` | is | XRP | 1294 | +0.046 |
| 1h | `band_lower_slope_3_pct` | is | TRX | 1286 | +0.048 |
| 1h | `band_lower_slope_3_pct` | is | DOGE | 1305 | +0.044 |
| 1h | `band_lower_slope_3_pct` | is | LINK | 1274 | +0.055 |
| 1h | `band_lower_slope_3_pct` | is | LTC | 1290 | +0.064 |
| 1h | `band_lower_slope_3_pct` | oos | BTC | 684 | +0.061 |
| 1h | `band_lower_slope_3_pct` | oos | ETH | 678 | +0.056 |
| 1h | `band_lower_slope_3_pct` | oos | SOL | 676 | +0.041 |
| 1h | `band_lower_slope_3_pct` | oos | BNB | 691 | +0.037 |
| 1h | `band_lower_slope_3_pct` | oos | XRP | 677 | +0.067 |
| 1h | `band_lower_slope_3_pct` | oos | TRX | 704 | +0.044 |
| 1h | `band_lower_slope_3_pct` | oos | DOGE | 680 | +0.054 |
| 1h | `band_lower_slope_3_pct` | oos | LINK | 677 | +0.056 |
| 1h | `band_lower_slope_3_pct` | oos | LTC | 685 | +0.096 |
| 1h | `band_lower_slope_5_atr` | is | BTC | 1279 | +0.050 |
| 1h | `band_lower_slope_5_atr` | is | ETH | 1289 | +0.045 |
| 1h | `band_lower_slope_5_atr` | is | SOL | 1261 | +0.056 |
| 1h | `band_lower_slope_5_atr` | is | BNB | 1306 | +0.079 |
| 1h | `band_lower_slope_5_atr` | is | XRP | 1294 | +0.052 |
| 1h | `band_lower_slope_5_atr` | is | TRX | 1286 | +0.058 |
| 1h | `band_lower_slope_5_atr` | is | DOGE | 1305 | +0.054 |
| 1h | `band_lower_slope_5_atr` | is | LINK | 1274 | +0.054 |
| 1h | `band_lower_slope_5_atr` | is | LTC | 1290 | +0.068 |
| 1h | `band_lower_slope_5_atr` | oos | BTC | 684 | +0.062 |
| 1h | `band_lower_slope_5_atr` | oos | ETH | 678 | +0.062 |
| 1h | `band_lower_slope_5_atr` | oos | SOL | 676 | +0.048 |
| 1h | `band_lower_slope_5_atr` | oos | BNB | 691 | +0.036 |
| 1h | `band_lower_slope_5_atr` | oos | XRP | 677 | +0.062 |
| 1h | `band_lower_slope_5_atr` | oos | TRX | 704 | +0.049 |
| 1h | `band_lower_slope_5_atr` | oos | DOGE | 680 | +0.067 |
| 1h | `band_lower_slope_5_atr` | oos | LINK | 677 | +0.056 |
| 1h | `band_lower_slope_5_atr` | oos | LTC | 685 | +0.103 |
| 1h | `band_lower_slope_5_pct` | is | BTC | 1279 | +0.043 |
| 1h | `band_lower_slope_5_pct` | is | ETH | 1289 | +0.039 |
| 1h | `band_lower_slope_5_pct` | is | SOL | 1261 | +0.048 |
| 1h | `band_lower_slope_5_pct` | is | BNB | 1306 | +0.060 |
| 1h | `band_lower_slope_5_pct` | is | XRP | 1294 | +0.042 |
| 1h | `band_lower_slope_5_pct` | is | TRX | 1286 | +0.045 |
| 1h | `band_lower_slope_5_pct` | is | DOGE | 1305 | +0.043 |
| 1h | `band_lower_slope_5_pct` | is | LINK | 1274 | +0.054 |
| 1h | `band_lower_slope_5_pct` | is | LTC | 1290 | +0.056 |
| 1h | `band_lower_slope_5_pct` | oos | BTC | 684 | +0.054 |
| 1h | `band_lower_slope_5_pct` | oos | ETH | 678 | +0.050 |
| 1h | `band_lower_slope_5_pct` | oos | SOL | 676 | +0.038 |
| 1h | `band_lower_slope_5_pct` | oos | BNB | 691 | +0.032 |
| 1h | `band_lower_slope_5_pct` | oos | XRP | 677 | +0.061 |
| 1h | `band_lower_slope_5_pct` | oos | TRX | 704 | +0.039 |
| 1h | `band_lower_slope_5_pct` | oos | DOGE | 680 | +0.048 |
| 1h | `band_lower_slope_5_pct` | oos | LINK | 677 | +0.047 |
| 1h | `band_lower_slope_5_pct` | oos | LTC | 685 | +0.100 |

## ⚠️ 인용 경고

* **「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지** — 다른 질문(*이미 진입한 손절 중 즉사를 진입 시점에 알아보는가*)이다.
* 전부 `baseline`(낙관) 렌즈 위의 값 · 존폭 축 체결 보수화(`pen_5bp`)는 안 쟀다.
* §C 볼린저는 진입가를 만드는 도구 자체다(WAN-131: 기여의 84%가 선별 아닌 가격) — "선별 축을 찾았다"로 인용 금지.
* 기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지(측정 전용).
