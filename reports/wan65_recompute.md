# WAN-65 — 리스크 사이징 배선 + 손절 체결가 clamp 재산출

**증상**: 모든 백테스트 진입점이 `BacktestConfig()`를 직접 생성하면서 `risk_sizing`을
넘기지 않아, `config.Settings.effective_risk_sizing`(`risk_sizing_enabled=True`,
`risk_per_trade=0.01`)이 켜져 있어도 실제로는 매 거래가 `position_fraction`(기본
100%, 자본 전액)으로 진입했다. 손절 거리와 무관하게 동일 비율의 자본을 썼으므로,
손절이 걸리면 거래마다 손실이 제멋대로였다(손절 0.4% 거리 → 손실 0.4%, 손절 14.3%
거리 → 손실 14.3%).

**함께 발견된 이상**: 손절(`stop_loss`)로 청산됐는데 수익률이 양수인 거래가 존재했다.
`strategy/confluence.py::_plan_exit`가 무효화(breaker) 봉의 **종가**를 그대로 손절
체결가로 썼는데, 무효화는 저가/고가(wick) 기준으로 판정되므로 그 봉이 반전해
진입가에 유리하게 마감하면 "손절인데 이익"이라는 모순이 생겼다.

## 원인과 수정

1. **배선** — `backtest/sweep.py::default_backtest_config()`를 모든 진입점(스윕·CLI
   리포트·워크포워드·A/B 러너·존-지정가 파이프라인·페이퍼 패리티·대시보드)의 **단일
   설정 소스**로 확정하고, `settings.effective_risk_sizing`을 `BacktestConfig.
   risk_sizing`에 실었다. `BacktestConfig()` 직접 생성은 `BacktestEngine`의
   라이브러리 레벨 기본값(명시적 설정 없이 호출됐을 때의 최종 폴백)과 테스트를
   제외하고 코드베이스에서 사라졌다.
2. **조용한 실패 제거** — `risk_sizing=None`으로 백테스트가 돌면 `BacktestEngine`과
   존-지정가 파이프라인이 `logger.warning`을 낸다(회귀 테스트로 고정). 리포트
   요약·CSV에 `sizing_mode`(`risk_sizing`/`full_position`)와 `risk_per_trade`
   컬럼을 추가해 파일만 봐도 어떤 사이징으로 나온 숫자인지 알 수 있게 했고,
   전액 진입 모드면 `sizing_mode_banner()`가 경고 배너를 띄운다.
3. **손절 체결가 clamp** — `_plan_exit`의 STOP_LOSS `PlannedExit.price`를 무효화
   봉 종가와 오더블록 무효화 경계(`ob.bottom`/`ob.top`) 중 진입가에 더 불리한
   쪽으로 정한다. 경계는 항상 진입가보다 불리하므로(탭 진입은 존 안에서 일어난다)
   손절은 구조적으로 절대 이익을 낼 수 없다.

## 재현 커맨드

```bash
# BEFORE (수정 전 버그 재현): risk_sizing_enabled=false로 강제 — position_fraction(100%) 경로
ALPHABLOCK_RISK_SIZING_ENABLED=false uv run python scripts/backtest_report.py \
    --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT \
    --timeframes 15m,1h,2h,4h,1d --out-dir /tmp/wan65_before

# AFTER (수정 후, 기본값): risk_sizing이 기본으로 켜진 상태
uv run python scripts/backtest_report.py \
    --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT \
    --timeframes 15m,1h,2h,4h,1d --out-dir /tmp/wan65_after
```

"손절인데 이익" clamp 검증(결과 2.5)은 `ConfluenceStrategy._stop_loss_price`를 잠시
`lambda direction_sign, ob, close_price: close_price`로 몽키패치해 clamp 이전 동작을
재현한 뒤, 위와 같은 3심볼 × 5TF 스윕(`run_sweep` → best 조합 `evaluate`)의 거래를
`exits[-1].reason == "stop_loss" and realized_pnl > 0`으로 집계했다.

산출물: `backtest/reports/wan65_sweep_before.csv` / `wan65_sweep_after.csv`(조합별
전체 스윕 행, `sizing_mode`/`risk_per_trade` 포함), `wan65_comparison_best.csv`
((심볼,TF)별 sharpe 최상위 조합의 전/후 비교), `wan65_stop_loss_convergence.csv`
(수정 후 모든 손절 거래의 자본 대비 손실 — 아래 검증 근거).

## 결과 1 — MDD가 15개 조합 전부에서 축소된다

(심볼, TF) 15개 조합 각각 sharpe 최상위 파라미터 기준, 전/후 비교
(`wan65_comparison_best.csv`):

| 지표 | BEFORE(전액 진입) | AFTER(리스크 사이징) |
| --- | --- | --- |
| 평균 MDD | 22.0% | **9.5%** |
| 중앙값 MDD | 19.7% | **9.5%** |
| MDD 축소된 조합 수 | — | **15 / 15** |

한 방에 계좌 14%가 날아가던 손절이 사라졌다 — 손절 거리가 큰 거래의 명목가치가
줄어든 만큼 개별 거래 손실 상한이 낮아진 직접적 결과다. 15개 조합 전체 표는
`wan65_comparison_best.csv` 참고.

## 결과 2 — 손절 거래의 자본 대비 손실이 risk_per_trade(1%)로 수렴한다

AFTER(사이징 적용) 손절 거래 239건(`wan65_stop_loss_convergence.csv`)의 자본 대비
손실 분포:

| 통계 | 값 |
| --- | --- |
| 평균 | **0.98%** |
| 중앙값 | **0.98%** |
| 25% ~ 75% 분위 | 0.70% ~ 1.10% |
| [0.5%, 1.5%] 밴드 내 비율 | **77.4%** |
| 최소 / 최대 | 0.20% / 5.33% |
| **이익을 낸 손절 거래 수** | **0** |

목표(`risk_per_trade=0.01`)에 평균·중앙값이 거의 정확히 수렴한다. 꼬리(최대 5.33%)는
버그가 아니라 무효화 봉이 사이징의 손절 참조가(`ob.bottom`/`ob.top`)를 훨씬 지나쳐
종가로 마감한 케이스(빠른 변동성 구간의 갭성 이탈) — 봉 종가로 정산하는 백테스트의
구조적 한계이며, 실거래에서도 슬리피지로 나타날 수 있는 실제 리스크다. `_stop_loss_
price`의 clamp 덕분에 최소 0.20%로, 절대 음수(이익)가 되는 사례는 없다.

## 결과 2.5 — "손절인데 이익" 버그를 실데이터로 재현·검증

`_stop_loss_price`의 clamp를 잠시 비활성화(체결가 = 무효화 봉 종가 그대로, 수정 전
동작)하고 3심볼 × 5TF 전체를 재실행한 결과:

| 항목 | 값 |
| --- | --- |
| 손절(`stop_loss`) 거래 총수 | 276 |
| 그중 **이익을 낸 손절** | **24건 (8.7%)** |

첫 사례가 이슈에 보고된 수치와 정확히 일치한다 — BTC/USDT:USDT 15m, 진입가
29,451.97, 손절 체결가(종가) 28,246.22, **수익률 +4.02%**. 무효화 봉이 저가/고가
(wick)로 오더블록 경계를 찍었지만 진입가에 유리하게 반전 마감한 전형적 케이스다.
clamp를 되돌려 놓은(현재 코드) 상태로 동일 3심볼 × 5TF를 재실행하면(결과 2의
`wan65_stop_loss_convergence.csv`, 239건) **이익을 낸 손절은 0건**이다.

## 결과 3 — 진입 명목가치가 실제로 손절 거리에 반비례해 달라진다

| 통계 | BEFORE(자본 대비 명목 %) | AFTER |
| --- | --- | --- |
| 평균 | 90.9% | **63.2%** |
| 중앙값 | 93.6% | **68.8%** |
| 표준편차 | 12.7%p | **29.3%p**(사이징이 실제로 거래마다 달라짐) |
| 최소 / 최대 | 62.1% / 119.8% | **3.5%** / 112.1% |

BEFORE의 분포(중앙값 93.6%, 최대 119.8%)는 이슈에 보고된 실측치(중앙값 98%, 최대
122%)와 같은 자릿수로 일치한다 — 같은 버그를 다른 데이터 창에서 재확인한 셈이다.
AFTER는 표준편차가 2배 이상으로 커져, 손절 거리가 좁은 셋업엔 명목가치를 늘리고
(레버리지 상한 1× 이내) 넓은 셋업엔 줄이는 사이징이 실제로 작동함을 보여준다.

## 결과 4 — 손익비·손익분기 승률(정규화 관점)

`return_pct`(진입 노셔널 대비 %)는 노셔널 스케일에 거의 불변이라 BEFORE/AFTER가
비슷하다(payoff 1.03 → 1.07). 반면 **금액(R) 기준**은 사이징으로 거래 간 리스크가
일정해지며 비교 가능해진다 — 이전에는 "1R 손실"이 거래마다 0.4%~14.3%로 제멋대로였던
것이, 이제는 위 결과 2처럼 ~1%로 수렴한다. 승률·payoff 자체의 절대 결론(전략이
이기는지)은 이 PR의 범위가 아니다 — 이 재산출의 목적은 **사이징이 설정대로
작동함을 검증**하는 것이다.

## 스킵된 진입(min_stop_distance_fraction)

기본 설정(`PositionSizingParams.min_stop_distance_fraction=0.0`)에서는 스킵이
발생하지 않는다 — 동일 RSI 파라미터에서 BEFORE/AFTER 거래 수를 15개 조합 전부
대조한 결과 차이가 0건이었다(`wan65_sweep_before.csv` vs `wan65_sweep_after.csv`,
동일 `rsi_oversold`/`rsi_overbought` 행 비교). 최소 손절 거리 하한을 두려면
`ALPHABLOCK_RISK_SIZING__MIN_STOP_DISTANCE_FRACTION`으로 설정해야 하며, 이 경우
스킵 건수는 이 리포트가 다시 재현할 때 0이 아니게 된다.

## 결론

**WAN-65 이전(사이징 미배선 상태)에 산출된 모든 백테스트·스윕·워크포워드·A/B 성과
수치(WAN-19/22/46/50/58 포함)는 무효다.** 손절 거래의 손실 크기가 거래마다 제멋대로였고,
MDD·payoff·R 배수가 리스크 정규화되지 않은 채로 산출됐다. 이 수정 이후
`scripts/backtest_report.py`(및 이를 감싸는 워크포워드·A/B 러너) 재실행분만
신뢰할 수 있다.
