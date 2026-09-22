# WAN-424 §8 스크래치 원본 (보관용 · 재현 참고)

`docs/decisions/wan424.md` §8(운용 탐색)의 숫자를 낸 **세션 스크래치 스크립트와 출력**을 그대로 보관한다.
세션 임시 폴더가 정리되면 사라지므로 「그 숫자를 어떻게 냈나」를 잃지 않으려고 남긴다.

🚨 **저장소 코드가 아니다.**

- 확장자를 `.py.txt`로 바꿔 ruff·mypy·pytest 대상에서 뺐다. 테스트도 품질 게이트도 거치지 않았다.
- 경로가 세션 임시 폴더(`/private/tmp/claude-501/...`)로 하드코딩돼 있다.
- 입력 캐시 `backtest/cache/wan424_iso_work.pkl.gz`(§7 모듈이 만든다)와 `backtest/cache/wan424_payloads`
  (§1 모듈)를 읽는다. 숏(`ls_rsi` · `ls_ts2` · `short_hold`)은 WAN-423 세션 스크래치 캐시
  `hold_ls_payloads`를 읽는데, 그 캐시는 **저장소에 없다**(재현하려면 롱·숏 후보를 다시 만들어야 하고
  약 5시간 걸린다).
- 채택 근거로 쓰려면 `backtest/` 모듈로 옮기고 테스트를 붙여 재현해야 한다.

## 스크립트 → §8 항목

| 스크립트 | 내용 | §8 |
| -- | -- | -- |
| `overlap`, `maxday`, `bookday`, `dayimpact` | 날짜 몰림 · 최대 진입일 · 폭락일 계좌 영향 | 8-1 |
| `breakbar`, `breakbar_all`, `breaktf`, `breaksym` | 몇 번째 봉에서 깨졌나 · TF별 · 종목별 | 8-1 |
| `cap_trades`, `cap_grid`, `cap_show` | 리스크 합계 한도 × 폭비례 크기 격자 (24팔) | 8-2, 8-4 |
| `breaker` | 서킷브레이커 (24h 실현손실 → H시간 금지) | 8-2 |
| `fifth`, `five` | 1/5 명목 · 1/5 × 5배 · 개수 5칸 제한 | 8-2, 8-4 |
| `winbar`, `winbar2`, `fastbreak_ub`, `fastbreak_ub2` | 진입 봉·두 봉 뒤 상승폭 · 빠른 손절 제거 상한 | 8-2 |
| `delayed2`, `delayed2_eval` | **한 봉 대기 · 반반 진입 (실제 북)** | 8-2 |
| `delayed`, `delayed_eval` | 위의 간이 판 (사용자 지적으로 폐기 — 결과 없음) | — |
| `rsi_mix`, `rsi_mix1r`, `rsi_floor` | RSI 추가 × 하한 | 8-3 |
| `ls_rsi`, `short_hold`, `ls_ts2` | 숏 · 롱+숏 (ts2·ts3은 중단) | 8-3 |
| `mdd30` | 목표 MDD 30% 크기 스윕 (실제 북 · 복리) | 8-4 |
| `tf_floor`, `per_tf`, `ts_by_tf`, `by_sym` | TF별 하한 통과율 · TF·종목별 성적 | 8-5 |
| `book_null`, `regime`, `halves` | 계좌 단위 무작위 · 장세 분할 · IS 반분 | 8-5 |
| `levers`, `levers_all`, `lv_sum` | 레버 A1~A5 × 24팔 | (§8 이전 탐색) |
| `book_all`, `book_mdd`, `floor_only_book`, `mdd`, `judge`, `loss` | 실제 북 vs 간이 경로 · 하한만 팔 · 손실 분포 | 8-1, §7 |

`scripts/wan423_deps/`는 숏 스크립트가 import하는 WAN-423 세션 스크래치 모듈 사본이다.
`outputs/`는 파일로 남아 있던 출력만 담았다. 화면에만 찍힌 출력은 결정문 §8의 표가 원본이다.
