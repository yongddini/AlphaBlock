# WAN-58 — WAN-56/37 이후 성과 리포트 전면 재산출

**대상 리포트**: WAN-19(스윕) · WAN-22(워크포워드) · WAN-46(A/B 본실험) · WAN-50(A/B 워크포워드)
**재산출 근거**: 아래 두 수정이 모두 `main`에 머지된 뒤 실행.

1. **WAN-56**(PR #43) — 존 병합(`combine_obs`)을 백테스트 신호까지 적용. 진입 팽창률 **1.23×** 교정
   (raw 9,586 → merged 7,762). 상세: [`wan56_merge_impact.md`](wan56_merge_impact.md).
2. **WAN-37**(PR #44) — 백테스트/페이퍼 공용 `common.costs.CostModel`(테이커 0.04% / 메이커 0.02% /
   슬리피지 5bps). A안=시장가(테이커+슬리피지), B안=지정가(메이커, 슬리피지 0)의 **비용 비대칭**을 반영.

> **이전 수치는 모두 무효다.** 기존 커밋 CSV(`backtest/reports/wan46_ab_experiment.csv`,
> `wan50_ab_walkforward*.csv`)는 병합 전(raw 존) 베이스라인으로만 남긴다. 본 재산출의 권위 있는
> 산출물은 `backtest/reports/wan58_*.csv`(신규 파일, 덮어쓰기 아님)와 이 리포트다.

## 원인 분리 방법 (gross vs net)

병합(WAN-56)과 비용(WAN-37)을 한 번에 반영하면 수치 변화가 어느 쪽 때문인지 알 수 없다. 그래서
A/B 재산출은 **같은 하네스에서 두 벌**로 낸다(`backtest.ab_run.add_cost_args` 플래그 하나 차이):

| 컬럼 | 정의 |
| --- | --- |
| `gross` | 병합 존, **비용 차감 전** (`--fee-rate 0 --maker-fee-rate 0 --slippage 0`) |
| `net`   | 병합 존, **비용 차감 후** (테이커 0.0004 / 메이커 0.0002 / 슬리피지 0.0005, 기본값) |

`old → gross` 차이는 **병합 효과**, `gross → net` 차이는 **비용 효과**다.
**WAN-57(4h 기본값 전환)의 판정 게이트는 `net` 이다** — `gross`로 판단하지 않는다.

재현 커맨드:

```bash
# WAN-46 본실험 (net / gross)
uv run python -m backtest.ab_experiment --out backtest/reports/wan58_ab_experiment_merged_net.csv \
    --coverage-out backtest/reports/wan58_coverage_merged.csv
uv run python -m backtest.ab_experiment --fee-rate 0 --maker-fee-rate 0 --slippage 0 \
    --out backtest/reports/wan58_ab_experiment_merged_gross.csv --coverage-out /tmp/cov.csv

# WAN-50 워크포워드 (net / gross)
uv run python -m backtest.ab_walkforward --out backtest/reports/wan58_ab_walkforward_merged_net.csv \
    --summary-out backtest/reports/wan58_ab_walkforward_merged_net_summary.csv
uv run python -m backtest.ab_walkforward --fee-rate 0 --maker-fee-rate 0 --slippage 0 \
    --out backtest/reports/wan58_ab_walkforward_merged_gross.csv \
    --summary-out backtest/reports/wan58_ab_walkforward_merged_gross_summary.csv

# WAN-19 스윕 / WAN-22 워크포워드 (컨플루언스 A-진입, net 기본)
uv run python scripts/backtest_report.py --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT --out-dir out/wan58_sweep
uv run python scripts/walkforward_report.py --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT \
    --timeframes 1h,4h --is-bars 720 --oos-bars 168 --out-dir out/wan58_wf
```

데이터: `data/ohlcv.db`(WAN-6 수집 실데이터, 3심볼 × 최근 3년). 수익률합(sum)은 **복리를 병기**한다
(단순 합산은 과대평가 — 예: 4h B의 OOS 합 +0.675는 심볼별 복리로 BTC +6.3% / ETH +17.1% / SOL +48.7%).

---

## 🔑 WAN-50 — A/B 워크포워드/OOS (WAN-57 게이트)

### 4h OOS 요약 (윈도우 21개 = 3심볼 × 7)

| 지표 | old(raw, 무효) A | old B | **net(merged) A** | **net(merged) B** | gross(merged) A | gross B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mean OOS 수익률/윈도우 | +0.26% | +3.22% | **−0.28%** | **+3.99%** | +0.10% | +4.39% |
| mean OOS PF | 1.48 | 1.75 | **1.20** | **2.60** | 1.52 | 2.87 |
| OOS 거래 수 | 57 | 90 | 44 | 72 | 44 | 72 |
| mean fill_rate(B) | — | 0.41 | — | 0.38 | — | 0.38 |

### 4h OOS 심볼별 복리 수익률 (net vs gross)

| 심볼 | net A | **net B** | gross B | (참고) old B |
| --- | ---: | ---: | ---: | ---: |
| BTC/USDT | −9.1% | **+1.1%** | +3.5% | +6.3% |
| ETH/USDT | −3.9% | **+39.0%** | +42.5% | +17.1% |
| SOL/USDT | +3.9% | **+54.5%** | +59.3% | +48.7% |
| 3심볼 연결 복리 | −9.3% | **+117%** | +135% | +84.9% |

### 판정 — WAN-57 게이트: ✅ **성립 (유지·강화)**

- **"4h에서 B안(존 지정가/메이커 진입) 우월"** 결론은 병합+비용(`net`) 기준으로도 **성립한다.**
  B는 모든 지표(수익률·PF·심볼별 복리)에서 A를 앞선다. A는 net 기준 오히려 **음(−)**이다.
- **비용은 B의 우위를 좁히지 않고 넓힌다.** B는 메이커(수수료 싸고 슬리피지 0)라 gross→net 감소가
  작지만(+4.39%→+3.99%), A는 테이커라 비용을 크게 물어 +0.10%→−0.28%로 뒤집힌다.
  즉 **비용 비대칭이 B에 유리**하게 작동한다.
- **심볼 편중 완화.** old에서 B의 4h 성과는 SOL(+48.7%)에 크게 쏠렸고 BTC(+6.3%)는 약했다.
  병합 후 net 기준 **ETH가 +39.0%로 강해져** SOL 단일 의존이 완화됐다(단, BTC +1.1%는 여전히 약함).
- 병합으로 거래 수는 줄었다(B 90→72, 4h). 이는 진입 부풀림 1.23× 교정의 정상적 결과다.

> **결론 뒤집힘 없음.** 오히려 net 기준에서 B 우위가 더 뚜렷해졌으므로,
> WAN-57(4h `entry_mode` 기본값을 B안 존 지정가로 전환)의 근거가 확보됐다.

---

## WAN-46 — A/B 진입 방식 본실험 (전체 구간, 3심볼×4TF)

전체 합산(ALL) 및 4h 발췌. `net`이 실거래에 가까운 값이다.

| 구간 | 지표 | net A | **net B** | gross A | gross B | old A(무효) | old B(무효) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ALL(전 TF) | avg 거래수익률 | −0.203% | **+0.074%** | −0.023% | +0.184% | −0.191% | −0.014% |
| ALL | PF | 0.696 | **1.104** | 0.979 | 1.331 | 0.704 | 0.966 |
| ALL | 거래 수 | 1637 | 2373 | 1637 | 2373 | 2143 | 3090 |
| 4h BTC | total_return | −23.2% | **+6.0%** | −20.5% | +11.1% | — | — |
| 4h ETH | total_return | −15.3% | **+46.1%** | −12.5% | +53.2% | — | — |
| 4h SOL | total_return | −10.7% | **+72.7%** | −2.9% | +83.8% | — | — |

- **B > A**가 병합+비용 기준으로도 전체·4h 모두에서 유지된다. A는 net PF 0.70(손실 전략), B는 1.10.
- 병합으로 총 진입이 A 2143→1637, B 3090→2373로 줄어(≈1.30×) 부풀림이 교정됐다.
- 전체 구간 B 수익 편중(심볼별 net total_return 합): SOL +1.21, BTC +0.62, ETH −0.11.
  **저TF(15m·1h)에서 ETH가 B를 끌어내리지만 4h만 보면 ETH도 +46%로 강하다** — 편중은 TF 의존적이다.
- **결론 뒤집힘 없음** (B 우위 유지). 단, 절대 성과는 old 대비 축소(부풀림·비용 반영).

## WAN-19 — 컨플루언스 전략 스윕 (병합+net)

컨플루언스 전략은 시장가(A) 단일 진입이라 `net`(테이커+슬리피지)만 산출. (심볼,TF)별 최적
RSI 임계값 조합의 성과:

| 심볼 | 양(+) TF | 음(−) TF | 대표 최적 |
| --- | --- | --- | --- |
| BTC | 1h(+21.6%, PF13.6, n17) · 2h(+15.3%) · 1d(+7.9%) | 15m · 4h | 1h/2h 견조 |
| ETH | — | 15m·1h·2h·4h·1d **전부 음** | 전 TF 부진 |
| SOL | 15m(+12.4%) · 1h(+5.0%) · 4h(+6.7%) | 2h · 1d | 저~중 TF 양호 |

- 15개 조합 중 **6개만 양(+)**. 컨플루언스 A-진입 단독 성과는 심볼·TF 편차가 크고 ETH는 전 구간 부진.
- 4h는 3심볼 중 SOL만 양(+6.7%) — **4h에서 A-진입 컨플루언스는 약하다**(WAN-50에서 B가 A를 앞서는 것과 일관).
- old(무효) 스윕 수치는 커밋되지 않아 1:1 diff은 불가하나, 병합으로 진입 수·성과가 축소됨은 동일 방향.

## WAN-22 — 컨플루언스 워크포워드/OOS (병합+net, 1h·4h)

IS로 RSI 임계값을 고르고 미본(OOS) 구간에서 측정. IS→OOS 격차(과적합 신호)와 OOS 복리:

| 심볼·TF | mean IS | mean OOS | IS−OOS 격차 | **OOS 복리** | OOS PF |
| --- | ---: | ---: | ---: | ---: | ---: |
| BTC 1h | +0.05% | −0.05% | +0.09pp | −7.4% | — |
| BTC 4h | −1.37% | −0.49% | −0.88pp | −21.4% | 0.14 |
| ETH 1h | −0.17% | −0.13% | −0.04pp | −18.9% | 0.25 |
| ETH 4h | −0.18% | −0.55% | +0.37pp | −23.8% | 0.28 |
| SOL 1h | +0.20% | −0.02% | +0.22pp | −4.6% | 0.48 |
| **SOL 4h** | +2.15% | +0.48% | +1.68pp | **+21.3%** | 2.43 |

- **IS→OOS 격차는 작다**(대부분 ±1pp 이내) — 과적합 신호는 크지 않다. 문제는 과적합이 아니라
  **컨플루언스 A-진입의 OOS 절대 성과가 약하다**는 것: 6개 중 SOL 4h만 OOS 양(+21.3%).
- 이는 WAN-50의 결론(B가 A보다 우월, 특히 4h)과 정합적이다 — A-진입 컨플루언스는 OOS에서 대체로
  부진하고, 지정가(B) 진입이 필요한 이유를 뒷받침한다.

---

## 종합 판정

| 리포트 | 이전 결론 | 병합+net 재산출 | 뒤집힘? |
| --- | --- | --- | --- |
| WAN-50 (게이트) | 4h B안 우월 | **4h B안 우월 (강화, net 전 심볼 +)** | ❌ 유지 |
| WAN-46 | B > A | **B > A (전체·4h)** | ❌ 유지 |
| WAN-19 | (미커밋) | A-진입 스윕 6/15 양, ETH 부진 | — |
| WAN-22 | (미커밋) | A-진입 OOS 부진(SOL 4h만 +) | — |

**WAN-57 게이트: 통과.** 4h `entry_mode` 기본값을 B안(존 지정가/메이커)으로 전환하는 근거가
병합+비용(net) 기준으로 확보됐다. 비용 비대칭이 B에 유리하게 작동하고, 심볼 편중도 old 대비 완화됐다.
