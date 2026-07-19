# WAN-126: 다중TF 겹침 캐스케이드 — 선별 대 가격 분리

재현: `uv run python -m backtest.wan126_multi_tf_overlap`

창 **2023-07-14 ~ 2026-07-15** · 공식 렌즈 `baseline` 단독(WAN-128) · 채택 기본값(존 지정가 + 오프셋 2bp + 롱 온리 + 고정 1.5R) 고정, 겹침만 옵트인.

> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결, 큐 우선순위 미모델링) — 수치는 **상한**이다.
> ⚠️ `C−B`는 볼린저가 최종 가격을 덮어써 가격 효과의 **하한**이다(wan126.md §3).
> ⚠️ 사다리 5m 칸: 사용자 결정 (A)로 5m 수집 완료 — 1h→15m→5m→1m · 15m→5m→1m.

## 판정 — 공식 렌즈 OOS `contained`(정본)

- **15m OOS** (contained): 선별 `B−A` -1.13%p · 가격 `C−B` +4.48%p → **(c) 선별 0/음수 · 가격 `C−B`는 플러스지만 거래 수 52% 차이(오염)라 순수 가격 효과로 못 읽음 — 규칙 값 확인 안 됨**
- **1h OOS** (contained): 선별 `B−A` -0.17%p · 가격 `C−B` -3.59%p → **(c) 둘 다 0/음수 — 규칙 값 없음**

## 1. 선별/가격 분해 (`B−A` / `C−B`, 심볼평균 total_return, `contained`)

| timeframe | segment | definition | A | B | C | selection_B_minus_A | price_C_minus_B |
| -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | full | contained | 42.2 | 40.48 | 37.99 | -1.72 | -2.49 |
| 15m | is | contained | 24.5 | 24.85 | 20.65 | 0.35 | -4.19 |
| 15m | oos | contained | 8.63 | 7.5 | 11.97 | -1.13 | 4.48 |
| 1h | full | contained | 32.71 | 32.23 | 23.71 | -0.48 | -8.52 |
| 1h | is | contained | 17.91 | 17.76 | 20.55 | -0.15 | 2.79 |
| 1h | oos | contained | 5.28 | 5.11 | 1.52 | -0.17 | -3.59 |

## 2. 거래 수 오염 — `B` vs `C` (5% 초과면 `C−B` 순수 가격 인용 금지)

| timeframe | segment | B_trades | C_trades | diff_pct | contaminated |
| -- | -- | -- | -- | -- | -- |
| 15m | full | 5066 | 2359 | 53.43 | True |
| 15m | is | 3378 | 1547 | 54.2 | True |
| 15m | oos | 1625 | 782 | 51.88 | True |
| 1h | full | 1550 | 994 | 35.87 | True |
| 1h | is | 995 | 666 | 33.07 | True |
| 1h | oos | 504 | 293 | 41.87 | True |

## 3. 심볼 편중 (leave-one-out, OOS `contained`)

| timeframe | dropped | selection_B_minus_A | price_C_minus_B |
| -- | -- | -- | -- |
| 15m | (none) | -1.13 | 4.48 |
| 15m | BNB | -1.53 | 3.1 |
| 15m | BTC | -1.46 | 6.7 |
| 15m | ETH | 0.64 | 9.04 |
| 15m | SOL | -0.9 | 3.87 |
| 15m | TRX | -1.61 | 2.46 |
| 15m | XRP | -1.93 | 1.7 |
| 1h | (none) | -0.17 | -3.59 |
| 1h | BNB | -0.21 | -6.03 |
| 1h | BTC | -0.21 | -1.43 |
| 1h | ETH | -0.26 | -0.59 |
| 1h | SOL | -0.21 | -3.13 |
| 1h | TRX | -0.21 | -5.13 |
| 1h | XRP | 0.05 | -5.2 |

## 4. 매칭 널 (WAN-70/124식) — `B`가 상위존 풀 `A`의 무작위 진입을 이기나

⚠️ **퇴화 가드**: `overlap_fraction`(= `B`거래/`A`거래)이 95% 이상이면 겹침 필터가 풀을 거의 그대로 통과시킨 것이라 `B`가 `A`와 사실상 같아진다 — 부트스트랩이 자기 자신을 검정하므로(WAN-124) **돌리지 않고 퇴화로 표시**한다. 그 자체가 「필터가 아무것도 안 걸러낸다」는 발견이다.

| timeframe | pool_trades_A | real_trades_B | overlap_fraction | degenerate | real_return_B | pool_return_A | effect_B_minus_A |
| -- | -- | -- | -- | -- | -- | -- | -- |
| 15m | 1681 | 1625 | 96.67 | True | 7.5 | 8.63 | -1.13 |
| 1h | 507 | 504 | 99.41 | True | 5.11 | 5.28 | -0.17 |

## 5. 표본 게이트 (셀당 최소 거래 수, 20 미만 판정 불가)

| timeframe | segment | arm | definition | min_trades | mean_trades | ok |
| -- | -- | -- | -- | -- | -- | -- |
| 15m | full | A | none | 496 | 877.1666666666666 | True |
| 15m | full | B | contained | 481 | 844.3333333333334 | True |
| 15m | full | B | proximal_in | 485 | 852.3333333333334 | True |
| 15m | full | B | touch | 490 | 866.0 | True |
| 15m | full | C | contained | 116 | 393.1666666666667 | True |
| 15m | full | C | proximal_in | 124 | 411.8333333333333 | True |
| 15m | full | C | touch | 134 | 438.5 | True |
| 15m | is | A | none | 367 | 585.5 | True |
| 15m | is | B | contained | 354 | 563.0 | True |
| 15m | is | B | proximal_in | 357 | 568.8333333333334 | True |
| 15m | is | B | touch | 362 | 578.8333333333334 | True |
| 15m | is | C | contained | 91 | 257.8333333333333 | True |
| 15m | is | C | proximal_in | 99 | 270.5 | True |
| 15m | is | C | touch | 107 | 292.0 | True |
| 15m | oos | A | none | 125 | 280.1666666666667 | True |
| 15m | oos | B | contained | 123 | 270.8333333333333 | True |
| 15m | oos | B | proximal_in | 124 | 272.3333333333333 | True |
| 15m | oos | B | touch | 124 | 275.8333333333333 | True |
| 15m | oos | C | contained | 24 | 130.33333333333334 | True |
| 15m | oos | C | proximal_in | 24 | 136.33333333333334 | True |
| 15m | oos | C | touch | 26 | 141.0 | True |
| 1h | full | A | none | 209 | 260.6666666666667 | True |
| 1h | full | B | contained | 207 | 258.3333333333333 | True |
| 1h | full | B | proximal_in | 207 | 259.1666666666667 | True |
| 1h | full | B | touch | 209 | 260.0 | True |
| 1h | full | C | contained | 75 | 165.66666666666666 | True |
| 1h | full | C | proximal_in | 83 | 173.16666666666666 | True |
| 1h | full | C | touch | 95 | 186.83333333333334 | True |
| 1h | is | A | none | 138 | 167.33333333333334 | True |
| 1h | is | B | contained | 136 | 165.83333333333334 | True |
| 1h | is | B | proximal_in | 136 | 166.33333333333334 | True |
| 1h | is | B | touch | 138 | 166.83333333333334 | True |
| 1h | is | C | contained | 59 | 111.0 | True |
| 1h | is | C | proximal_in | 62 | 115.16666666666667 | True |
| 1h | is | C | touch | 73 | 124.83333333333333 | True |
| 1h | oos | A | none | 69 | 84.5 | True |
| 1h | oos | B | contained | 69 | 84.0 | True |
| 1h | oos | B | proximal_in | 69 | 84.33333333333333 | True |
| 1h | oos | B | touch | 69 | 84.33333333333333 | True |
| 1h | oos | C | contained | 16 | 48.833333333333336 | False |
| 1h | oos | C | proximal_in | 21 | 52.166666666666664 | True |
| 1h | oos | C | touch | 22 | 53.666666666666664 | True |

