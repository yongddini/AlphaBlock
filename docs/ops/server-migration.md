# 페이퍼 수집기·러너 리눅스 서버 이전 런북 (WAN-174)

> 🔄 **2026-07-24 정정 — 아래 「ASTx가 막는다」 전제는 폐기됐다. 차단은 없었다.**
> 원인은 수집기가 **옛 엔드포인트 경로**를 쓴 것이고(`/market` 접두사 누락), 그 경로는
> 핸드셰이크만 성공시키고 데이터를 한 건도 안 보낸다. 고친 뒤 **로컬 맥에서 실시간 수신이
> 확인됐다**(ASTx 상주 그대로). 근거·실측: [`docs/decisions/wan174.md`](../decisions/wan174.md).
> **따라서 「데이터를 받으려면 서버로 가야 한다」는 이 문서의 동기는 더 이상 성립하지 않는다.**
> 서버 이전을 계속할 이유는 **상시 가동**(맥은 잠자고 재부팅된다) 하나이며, 그 판단은
> 사용자 몫이다. 아래 절차 자체는 상시 가동을 택할 때 **그대로 유효**하다(집 미니PC 포함).

로컬 맥은 ASTx(AhnLab Safe Transaction)가 바이낸스 **선물 웹소켓**(`wss://fstream.binance.com`)을
막아 실시간 수집이 불가하다(REST `fapi`는 정상 — WAN-174 진단). 리눅스 서버에는 ASTx가
없으므로 **수집기 + 페이퍼 러너 + 대시보드**를 서버로 옮겨 이 문제를 회피한다.

- **서버(사용자 확정)**: 오라클 클라우드 무료 티어 · **춘천 리전** ·
  `VM.Standard.E2.1.Micro`(1 OCPU · 1GB RAM · x86). 지금 단계는 **페이퍼/테스트 전용**이고,
  실매매 전환 시 더 좋은 유료 서버로 재이전한다(사용자 결정 2026-07-23).
- **안전**: 페이퍼 한정. `ALPHABLOCK_LIVE_TRADING=false` 불변 · 실주문 없음 · 바이낸스 API 키
  불필요(시세는 공개 데이터, 페이퍼는 `PaperBroker`).
- **1GB 박스 제약(PM 실측)**: 서버는 **실시간 수집 + 페이퍼 러너 + 대시보드 전용**이다.
  몇 년치 1분봉을 pandas로 올리는 **백테스트 격자는 서버에서 돌리지 않는다**(OOM) — 맥에서
  돌린다. 스왑 2GB는 필수(셋업 스크립트가 만든다).

## 0. 사전 조건

- 오라클 VM 프로비저닝 완료(Ubuntu 22.04+ 또는 동급, systemd 필수) + SSH 키 접속 가능.
- 인바운드 포트는 **SSH(22)만** 열면 된다. 텔레그램 알림은 아웃바운드 HTTPS라 개방 불필요,
  대시보드는 SSH 터널로만 접속한다(공개 노출 금지).

이하 `<서버>`는 `ubuntu@<공인IP>` 형태의 SSH 대상이라고 표기한다.

## 1. 서버 1회 셋업

```bash
ssh <서버>
git clone https://github.com/yongddini/AlphaBlock.git && cd AlphaBlock
./scripts/setup-server.sh   # 스왑 2GB + uv + uv sync (멱등)
```

## 2. .env 배치 (로컬 맥에서)

`.env`는 커밋 금지 파일이라 손으로 옮긴다(텔레그램 토큰 등):

```bash
scp .env <서버>:~/AlphaBlock/.env
```

새 설정을 추가할 일이 있으면 `.env.example`에 예시를 함께 올린다(저장소 규칙).

## 3. DB 이전 (로컬 맥에서)

3년치 재백필보다 복구된 `data/ohlcv.db`를 복사하는 쪽이 훨씬 빠르다.

⚠️ **복사 전 반드시 로컬 수집기를 정지**한다 — SQLite WAL 저널이 열린 채 복사하면
깨진 사본이 나온다(`data/ohlcv.db.corrupt.bak`이 그 흉터다).

```bash
# ① 로컬 수집기·러너 정지 (launchd 데몬을 쓰고 있었다면)
./scripts/uninstall-daemons.sh collector
./scripts/uninstall-daemons.sh live

# ② WAL 체크포인트로 -wal/-shm 을 본 파일에 흡수
sqlite3 data/ohlcv.db "PRAGMA wal_checkpoint(TRUNCATE);"

# ③ 복사 (수 GB — 수 분 걸린다)
scp data/ohlcv.db <서버>:~/AlphaBlock/data/ohlcv.db
```

무결성 확인(서버에서): `sqlite3 ~/AlphaBlock/data/ohlcv.db "PRAGMA integrity_check;"` → `ok`.

## 4. 상시 구동 등록 (서버에서)

```bash
cd ~/AlphaBlock
./scripts/install-systemd.sh            # 수집기 + 러너 + 대시보드 셋 다
# 또는 개별: ./scripts/install-systemd.sh collector|live|dashboard
```

systemd 시스템 서비스로 등록되어 **부팅 시 자동 시작 + 크래시 시 10초 후 자동 재시작**된다
(launchd 판 WAN-31/48과 대칭). 로그는 `~/AlphaBlock/logs/{collector,live,dashboard}.log`.

## 5. 검증 — WAN-174 완료 기준

1. **웹소켓 수신(핵심)**: 로컬에서 막히던 그 스트림이 서버에서 뚫리는지.

   ```bash
   uv run -- alphablock status        # 초록불 + 시리즈 신선도
   tail -f logs/collector.log         # 1분봉이 실시간으로 붙는지
   ```

2. **러너**: `logs/live.log`에 시그널 평가 루프가 돌고, 페이퍼 체결/알림이 기록되는지.
3. **자동 복구**: `sudo reboot` 후 셋 다 자동으로 올라오는지 —
   `systemctl status alphablock-collector alphablock-live alphablock-dashboard`.
4. **가동 커버리지**: 며칠 가동 후 `alphablock status`의 신선도/갭으로 "구멍 없이 돈다" 확인.
   갭이 보이면 `uv run -- alphablock backfill`로 1회 복구(WAN-35). 조용히 멈춘 스트림의
   자동 재접속은 WAN-173(워치독) 소관 — 서버에 함께 얹으면 좋다.

## 6. 대시보드 접속 (로컬 맥에서)

대시보드는 서버의 `127.0.0.1`에만 바인딩된다. SSH 터널로 접속:

```bash
ssh -N -L 8501:127.0.0.1:8501 <서버>
# 브라우저에서 http://localhost:8501
```

## 7. DB 배치 설계 — 수집=서버 / 백테스트=로컬

PM 제약 4번("DB를 어디 두고 동기화할지")에 대한 채택 설계:

- **서버 DB = 운영 정본**. 수집기가 쌓고 러너·대시보드가 읽는다. 백테스트는 여기서 안 돌린다.
- **로컬 맥 DB = 백테스트용 사본이며, 동기화가 필요 없다.** ASTx가 막는 것은 **웹소켓뿐**이고
  REST(`fapi`) 백필은 로컬에서 정상 동작한다(WAN-174 진단). 백테스트 전에 로컬에서
  `uv run -- alphablock backfill`(또는 `history`)로 REST 백필해 최신화하면 된다.
- 두 DB가 완전히 같아야 할 일(예: 서버 수집분과 로컬 백필분의 패리티 감사)이 생기면 그때만
  스냅숏을 내린다: 서버에서 §3과 같은 체크포인트 후 `scp` **역방향** 복사. 상시 동기화
  파이프라인은 만들지 않는다(1GB 박스에 부담 + 필요 근거 없음).

## 8. 역할 요약

| 위치 | 역할 | 근거 |
| --- | --- | --- |
| 리눅스 서버 (오라클 무료) | 실시간 수집 · 페이퍼 러너 · 대시보드 (상시) | ASTx 없음 → 웹소켓 정상 |
| 로컬 맥 | 백테스트 격자 · 개발 · REST 백필 | RAM 여유 · REST는 로컬에서도 정상 |

## 범위 밖

- ASTx를 로컬에서 제거·우회하는 방안(서버로 회피가 이 이슈의 결정).
- 실매매 전환·유료 서버 재이전(별도 결정), KST 표시 통일(WAN-172), 스트림 워치독(WAN-173).
