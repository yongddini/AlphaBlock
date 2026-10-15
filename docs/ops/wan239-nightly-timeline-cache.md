# 야간 타임라인 캐시 — 은퇴 (WAN-239 → WAN-451 ②)

🚨 **이 크론은 WAN-451 ②(2026-10-15)에서 은퇴했다.** 옛 규칙 페이퍼(셀당 대기 지정가 1개 ·
band 재진입 · 고정 1.5R)가 팔 + 엔진 C로 바뀌면서(WAN-451 ①), 그 장부를 per-cell 백테와
나란히 놓던 비교 도구가 전부 빠졌다 — `alphablock trades`(`--persist-cache`·`--days`·
`--prune-cache` 포함) · 대시보드 「거래 타임라인」 탭(3열 대조) · `live/timeline_cache.py`.
**캐시를 채우는 명령 자체가 없으므로** 서버 crontab에 남은 줄은 매일 밤 실패한다.

## 서버에서 지울 것 (사람이 1회)

```bash
# ① 무엇이 걸려 있는지 확인 — `trades ... --persist-cache` 줄이 대상이다
crontab -l | grep -n -- '--persist-cache'

# ② 지운다 — 손으로: `crontab -e` 를 열어 그 줄(`... cli.main trades ... --persist-cache` 또는
#    `alphablock trades ... --persist-cache`)을 지우고 저장한다.
#    한 줄로(먼저 백업):
crontab -l > ~/crontab.before-wan451.txt
crontab -l | grep -v -- '--persist-cache' | crontab -

# ③ 확인 — 아무것도 안 나와야 한다
crontab -l | grep -n -- '--persist-cache' || echo "야간 타임라인 크론 없음 ✅"
```

- 옛 로그(`/var/log/alphablock/timeline-cache.log` · `~/persist.log` 등)는 지워도 된다 —
  더 이상 쌓이지 않는다.
- DB에 남은 `timeline_cache_cells` · `timeline_cache_rows` 테이블은 **아무 코드도 안 읽는다**
  (무해). 지우려면 러너·수집기를 세운 뒤 `DROP TABLE` — **자동 삭제·자동 VACUUM은 없다**
  (WAN-194 원칙 · 지우는 것은 사람의 결정이다).

## 옛 문서

이 파일의 옛 내용(크론 등록 · 배포 뒤 되채우기 · `--prune-cache`)은 git 이력에 그대로 있다
(`git log -- docs/ops/wan239-nightly-timeline-cache.md`). 결정 기록은
[`docs/decisions/wan451.md`](../decisions/wan451.md) §7.
