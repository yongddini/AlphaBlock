"""지정가 주문 체결률 실측 장부 (WAN-45 — 이 이슈의 1급 산출물).

페이퍼 러너가 건 지정가 주문의 전 생애(예약 → 체결/만료/무효화/취소)를 SQLite에
누적한다. 목적은 집행이 아니라 **측정**이다: 이 저장소의 모든 백테스트 판정은
`baseline`("닿으면 체결") 낙관 가정 위에 서 있는데(WAN-96/128), 그 가정을 실제 시장에서
확인할 유일한 통로가 이 장부다(틱·호가 수집 WAN-98은 Canceled).

## 기록하는 것

* **주문 생애**: 심볼·TF별 걸린/체결된/만료·취소된 주문 수 → 체결률.
* **예약→체결 소요**(`wait_ms`)와 **체결 관통 폭**(`penetration_bps`): 관통 0 근처의
  체결("스치듯 닿음")은 실거래에서 큐 우선순위 때문에 가장 안 될 부류라(WAN-96),
  그 비중이 곧 낙관 가정의 비용이다. `pen_5bp` 렌즈(관통 5bp 요구)와 나란히 읽는다.
* **가동 구간**(`live_runner_sessions`): 러너가 실제로 살아 있던 시간. 로컬 맥에서
  돌므로(사용자 결정 2026-07-21) 재부팅·노트북 닫기로 구멍이 나는데, 체결률의 분모가
  "러너가 살아 있던 시간"임을 표에 명시할 수 있어야 한다 — 이게 없으면 "체결률 60%"가
  진짜 60%인지 러너가 40% 시간 죽어 있던 건지 구분이 안 된다.
* **재시작 폐기**(`discarded_restart`): 러너가 죽었다 살아나면 이전 세션의 대기 주문은
  **버리고 새로 건다**(복원하지 않는다 — 복원하려면 죽어 있던 구간의 가격 경로를
  재구성해야 하는데 그 구간 데이터가 빈 것이 문제의 본질이라 지어내지 않는다). 버린
  주문은 별도 상태로 남겨 체결률 통계를 오염시키지 않는다.
* **체결의 하류 처분**(`entry_status`·`entry_reject_reason`, WAN-194): 체결이 실제로
  페이퍼 포지션으로 **열렸는지**(`entered`) 아니면 집행 계층이 **거부했는지**(`rejected`,
  사유 포함 — `cell_busy`·`notional`·`sizing`). 아래 문단이 이 열이 왜 1급 기록인지 설명한다.
* **주문 걸기 전 미진입 사유**(`skip_reason`, WAN-217): 지정가가 확정되기 **전** 윗단계에서
  걸러진 셋업(`zone_width`·`cell_busy`·`retap`). WAN-194가 체결의 하류를 남겼다면 이건
  깔때기의 **상단**을 남긴다 — 이게 없으면 존폭 필터·슬롯 점유로 사라진 셋업이 아무 데도
  안 남아 "왜 안 들어갔나"를 셀 수 없다. `record_skipped`가 `status='skipped'` 행으로 남기고
  체결률 분모(주문이 걸린 표본)에는 넣지 않는다. **볼린저 규칙 3 기각**(deviation)은 별도
  행이 아니라 `first_rested_ms IS NULL`인 만료로 남는다(주문은 걸렸으나 밴드가 한 번도
  유리하지 않아 주문판에 실린 적 없다) — `SeriesFillStats.unfilled_no_band`가 그걸 센다.

## 체결 ≠ 진입 (WAN-194 — 이 열이 없어서 생긴 사고)

지정가 체결과 페이퍼 진입은 **다른 사건**이다. 체결은 이 장부가 남기지만 그 체결이
포지션이 되는지는 집행 계층(`ExecutionEngine.on_entry`)이 정한다 — 손절 거리가 사이징
가드(`min_stop_distance_fraction`, 0.3% WAN-79)보다 가깝거나, 명목 상한·리스크 한도에
걸리거나, 이미 그 시리즈에 포지션이 있으면 **거부**된다. 백테스트도 같은 자로 거부하므로
(`_to_trade`의 `qty <= 0` → 후보 폐기) 거부 자체는 버그가 아니다.

버그였던 것은 **그 거부가 아무 곳에도 안 남았다는 것**이다. WAN-194 전까지 거부된 체결은
INFO 로그 한 줄이 전부였고 DB에는 `status='filled'`만 남아 포지션·거래가 없었다. 그래서
운영자가 장부를 보면 "체결은 됐는데 포지션이 없다"가 **DB 손상·기록 유실과 구분되지
않았다**(실제로 사용자 질문 → 손상 의심 → WAN-194로 왔다). 두 열이 그 구분을 만든다:

* `entry_status='rejected'` + 사유 → **정상 동작**(가드가 걸렀다). 답이 장부에 있다.
* `entry_status IS NULL` 인데 `status='filled'` → **처분 미기록**. 러너가 체결 기록과
  포지션 쓰기 **사이에서 죽었을** 때 나오는 모양이다(두 쓰기가 원자적이지 않다) — 즉 이
  상태가 곧 "진짜 유실" 신호다. `orphan_fills()`가 그 행만 골라 준다.

⚠️ 옛 DB의 `filled` 행은 열이 없던 시절 기록이라 전부 `NULL`이다 — 그 행들은 "유실"이
아니라 **판별 불가**다(도입 이후만 보려면 `orphan_fills(since_ms=...)`).

수집 DB(`data/ohlcv.db`)를 같이 쓰며 `data.sqlite_util.configure_connection`(WAL +
busy_timeout)으로 동시 프로세스(수집기·대시보드)와의 락 경합을 견딘다.

요약 표는 `python -m live.fill_report`(같은 패키지 `fill_report` 모듈)가 찍는다.
"""

from __future__ import annotations

import sqlite3
import statistics
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from data.integrity import OrphanFill
from data.sqlite_util import configure_connection
from execution.engine import (
    REJECT_CODE_CELL_BUSY,
    REJECT_CODE_NOTIONAL,
    REJECT_CODE_SIZING,
)
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder

#: 처분 미기록 체결의 행 모양은 `data.integrity`가 소유한다(레이어 규칙: `data`는 `live`를
#: 임포트할 수 없어 점검 도구와 장부가 공유하려면 낮은 쪽에 둬야 한다). 여기서 재수출해
#: 장부 사용자가 `data.integrity`를 직접 알 필요는 없게 한다.
__all__ = [
    "ENTRY_STATUS_ENTERED",
    "ENTRY_STATUS_REJECTED",
    "LEDGER_REASON_DEVIATION",
    "LEDGER_REASON_ENTERED",
    "LEDGER_REASON_NO_FILL",
    "LEDGER_REASON_OTHER",
    "LEDGER_REASON_UNRECORDED",
    "MARGINAL_FILL_BPS",
    "REASON_PHRASES",
    "SKIP_REASON_CELL_BUSY",
    "SKIP_REASON_RETAP",
    "SKIP_REASON_ZONE_WIDTH",
    "STATUS_DISCARDED_RESTART",
    "STATUS_SKIPPED",
    "DaySummary",
    "FunnelCounts",
    "LedgerEntry",
    "OrderJournal",
    "OrphanFill",
    "PlacedOrder",
    "SeriesFillStats",
    "SessionSpan",
]

#: "스치듯 닿은 체결" 판정 문턱(bp). `pen_5bp` 민감도 렌즈(WAN-96)와 같은 5bp를 써서
#: 백테스트 표와 같은 자로 읽는다 — 이 값 미만 관통의 체결은 실거래에서 큐 우선순위
#: 때문에 가장 안 될 부류다.
MARGINAL_FILL_BPS = 5.0

#: 이전 세션이 남긴 대기 주문의 폐기 상태(재시작 정책 — 모듈 독스트링).
STATUS_DISCARDED_RESTART = "discarded_restart"

#: 체결이 페이퍼 포지션으로 열렸다(`entry_status`, WAN-194).
ENTRY_STATUS_ENTERED = "entered"

#: 체결이 집행 계층에서 거부됐다 — 사유는 `entry_reject_reason`(WAN-194).
ENTRY_STATUS_REJECTED = "rejected"

#: 주문이 걸리기 **전** 진입 깔때기 윗단계에서 걸러진 셋업(WAN-217). 지정가가 확정되기
#: 전에 탈락해 주문 생애(pending→…)를 시작하지 못하므로, `record_placed`가 아니라
#: `record_skipped`로 이 상태 행을 남긴다. 사유는 `skip_reason`. ⚠️ 체결률 분모(주문이
#: 걸린 표본)에는 넣지 않는다 — `discarded_restart`처럼 "결말이 나지 않은" 게 아니라
#: "주문이 걸린 적조차 없는" 표본이라 분자·분모 어디에도 안 들어간다.
STATUS_SKIPPED = "skipped"

#: 미진입 사유 코드(`skip_reason`, WAN-217). 백테스트 레버리지 북의 `SkippedSetup.reason`
#: (`backtest.leverage_book`)과 같은 라벨을 써서 두 경로의 깔때기를 나란히 읽는다.
#: 존폭 필터(`max_zone_width_atr`, WAN-159)에 걸려 넓은 존이 기각됐다.
SKIP_REASON_ZONE_WIDTH = "zone_width"
#: 슬롯이 이미 차 있어(오픈 포지션 또는 대기 주문) 새 지정가를 걸지 못했다. 라이브
#: 단일-대기-주문 규칙에서 칸 점유의 실질 발생 지점이다(체결 후 `cell_busy` 거부는
#: 드물다 — 주문을 걸 때 이미 슬롯을 봤으므로). 백테스트 북의 `cell_busy`와 짝.
SKIP_REASON_CELL_BUSY = "cell_busy"
#: `retap_mode="once"`(옵트인)에서 이미 한 번 진입한 존의 재탭이라 걸렀다. 채택
#: 기본값(`every_tap`)에서는 발생하지 않는다.
SKIP_REASON_RETAP = "retap"

#: 진입 깔때기 행 하나의 「결과 사유」 정규 코드(WAN-219 조회 화면). `funnel_counts`와 **같은
#: 분류 규칙**을 써서, 이 코드로 나열한 목록을 집계하면 `funnel_counts`와 정확히 같은 수가
#: 나온다(회귀 테스트가 고정). 존폭·슬롯참·재탭·명목·사이징은 `SKIP_REASON_*`·`REJECT_CODE_*`
#: 문자열을 그대로 재사용하므로(백테스트 북·라이브 스킵과 같은 어휘) 아래는 나머지 코드만 정의한다.
#: 체결 + 페이퍼 진입 성공.
LEDGER_REASON_ENTERED = "entered"
#: 체결됐으나 하류 처분이 안 남음(orphan, WAN-194) — 진입도 거부도 아니라 결과 미상.
LEDGER_REASON_UNRECORDED = "unrecorded"
#: 걸렸으나 유효기간 내 지정가에 안 닿아 만료(순수 미체결).
LEDGER_REASON_NO_FILL = "no_fill"
#: 걸렸으나 밴드가 한 번도 유리하지 않아 주문판에 실린 적 없이 만료(볼린저 규칙 3 기각).
LEDGER_REASON_DEVIATION = "deviation"
#: 그 밖의 체결 후 거부(리스크 한도 등) · 코드 미기록 거부(WAN-221 이전 행).
LEDGER_REASON_OTHER = "other"

#: 미진입 사유 코드 → 사람이 읽는 **문장**(텔레그램 요약·건별 알림·리포트가 공유하는
#: 단일 출처). 코드 상수를 그대로 키로 써서(백테스트 북·라이브 스킵과 같은 어휘) 라벨과
#: 집계가 두 벌로 갈라지지 않게 한다 — 대시보드·타임라인의 **압축 칩**(`슬롯참` 등)은 같은
#: 코드의 짧은 표기라 별도 register지만 뜻은 여기서 온다.
#: ⚠️ `cell_busy`는 오픈 포지션뿐 아니라 **아직 체결 안 된 대기 지정가 주문**이 슬롯을
#: 잡고 있어도 발생한다(단일-대기-주문 규칙, `SKIP_REASON_CELL_BUSY` 문단). 그래서 문장이
#: "포지션 보유 중"만 말하면 "진입한 적 없는데 왜 보유 중?"이라는 혼동을 낳는다(WAN-257) —
#: 대기 주문 점유를 반드시 함께 드러낸다.
REASON_PHRASES: dict[str, str] = {
    LEDGER_REASON_NO_FILL: "지정가에 안 닿아 만료",
    SKIP_REASON_ZONE_WIDTH: "존이 너무 넓어 제외",
    LEDGER_REASON_DEVIATION: "밴드가 불리해 제외",
    SKIP_REASON_CELL_BUSY: "같은 종목·주기에 포지션 보유 또는 대기 주문 있음",
    REJECT_CODE_NOTIONAL: "명목 한도 초과",
    REJECT_CODE_SIZING: "손절이 너무 짧아 제외",
    SKIP_REASON_RETAP: "이미 진입한 존의 재탭이라 제외",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_limit_orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           INTEGER NOT NULL,
    symbol               TEXT    NOT NULL,
    timeframe            TEXT    NOT NULL,
    direction            TEXT    NOT NULL,
    zone_start_time      INTEGER,
    zone_confirmed_time  INTEGER,
    tap_index            INTEGER NOT NULL DEFAULT 0,
    placed_ms            INTEGER NOT NULL,
    status               TEXT    NOT NULL,
    terminal_ms          INTEGER,
    first_rested_ms      INTEGER,
    last_limit_price     REAL,
    fill_ms              INTEGER,
    fill_price           REAL,
    fill_rsi             REAL,
    fill_penetration_bps REAL,
    stop_price           REAL,
    take_profit_price    REAL,
    wait_ms              INTEGER,
    entry_status         TEXT,
    entry_reject_reason  TEXT,
    entry_reject_code    TEXT,
    skip_reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_limit_orders_series
    ON live_limit_orders (symbol, timeframe);
CREATE TABLE IF NOT EXISTS live_runner_sessions (
    session_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ms  INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class SeriesFillStats:
    """한 (symbol, timeframe) 시리즈의 체결률 요약."""

    symbol: str
    timeframe: str
    placed: int
    """유효 표본(재시작 폐기 제외, 아직 대기 중 포함)."""
    pending: int
    filled: int
    cancelled_expired: int
    cancelled_invalidated: int
    cancelled_condition_failed: int
    discarded_restart: int
    median_wait_ms: float | None
    """체결 건의 예약→체결 소요 중앙값(ms)."""
    marginal_fills: int
    """관통 < `MARGINAL_FILL_BPS`(5bp)인 체결 수 — `pen_5bp` 렌즈가 부정할 체결."""
    entered: int = 0
    """체결 중 페이퍼 포지션이 실제로 열린 수(WAN-194)."""
    entry_rejected: int = 0
    """체결 중 집행 계층이 거부한 수(사이징 가드·리스크 한도 등, WAN-194)."""
    entry_unrecorded: int = 0
    """체결 중 처분이 안 남은 수 — 열 도입 전 기록이거나 쓰기 사이의 유실(WAN-194)."""
    skipped_zone_width: int = 0
    """존폭 필터에 걸려 주문을 걸지 않은 셋업 수(WAN-217/159). 주문 생애 밖이라 `placed`·
    체결률에 넣지 않는다."""
    skipped_cell_busy: int = 0
    """슬롯이 차 있어(오픈 포지션/대기 주문) 새 주문을 걸지 못한 셋업 수(WAN-217)."""
    skipped_retap: int = 0
    """`retap_mode="once"`에서 재탭이라 걸른 셋업 수(WAN-217, 옵트인)."""
    unfilled_no_band: int = 0
    """만료(`cancelled_expired`) 중 밴드가 한 번도 유리하지 않아 주문판에 **걸린 적 없이**
    끝난 수(`first_rested_ms IS NULL`) — 볼린저 규칙 3 기각(deviation)의 실측이다(WAN-217).
    나머지 만료(`cancelled_expired - unfilled_no_band`)가 순수 `no_fill`(걸렸으나 안 닿음)."""

    @property
    def skipped(self) -> int:
        """주문이 걸리기 전 걸러진 셋업 총수(WAN-217) — 체결률 분모 밖."""
        return self.skipped_zone_width + self.skipped_cell_busy + self.skipped_retap

    @property
    def no_fill(self) -> int:
        """걸렸으나 유효 기간 내 안 닿아 만료된 수(순수 미체결). 밴드 규칙 3 기각
        (`unfilled_no_band`)은 뺀다 — 그건 주문이 걸린 적 없는 다른 사유다(WAN-217)."""
        return self.cancelled_expired - self.unfilled_no_band

    @property
    def entry_rate(self) -> float | None:
        """체결 → 진입 전환율 = `entered` / (`entered` + `entry_rejected`).

        ⚠️ **체결률과 곱해 읽는 값이다.** 체결률의 분자(체결)는 이 전환율만큼만 거래가
        된다 — 백테스트도 같은 가드로 후보를 버리므로 파리티가 깨진 건 아니지만, "체결률
        81%"를 거래 성립률로 오독하지 않으려면 두 값을 함께 봐야 한다. 처분 미기록
        (`entry_unrecorded`)은 결과를 모르므로 분모에서 뺀다.
        """
        decided = self.entered + self.entry_rejected
        return self.entered / decided if decided else None

    @property
    def resolved(self) -> int:
        """결말이 난 표본 수(체결 + 취소, 대기·폐기 제외) — 체결률의 분모."""
        return (
            self.filled
            + self.cancelled_expired
            + self.cancelled_invalidated
            + self.cancelled_condition_failed
        )

    @property
    def fill_rate(self) -> float | None:
        """체결률 = filled / resolved. 결말 표본이 없으면 None.

        아직 대기 중인 주문은 분모에 넣지 않는다 — 결과가 정해지지 않은 표본을 미체결로
        세면 체결률이 러너를 켠 직후마다 아래로 왜곡된다.
        """
        return self.filled / self.resolved if self.resolved else None

    @property
    def marginal_fill_share(self) -> float | None:
        """체결 중 "스치듯 닿은"(관통 < 5bp) 비중. 체결이 없으면 None."""
        return self.marginal_fills / self.filled if self.filled else None


@dataclass(frozen=True)
class FunnelCounts:
    """한 시간 창(예: KST 하루)의 진입 깔때기 사유 카운트 — 전 시리즈 합산(WAN-221).

    일일 텔레그램 요약이 체결률과 미진입 사유를 **DB 조회로**(재계산 아님) 싣기 위한
    원자료다. 사유 어휘는 백테스트 레버리지 북·라이브 스킵과 같다(`SKIP_REASON_*` ·
    `execution.engine.REJECT_CODE_*`) — 두 경로의 깔때기를 나란히 읽기 위해서다.
    """

    placed: int = 0
    """창 안에 **주문이 걸린**(record_placed → PENDING) 수 = 예약. 창 귀속은 `placed_ms`다
    (사건 시각인 체결·만료와 달리 예약 시각으로 센다 — 러너의 메모리 `note_placed`와 같은
    축). 주문 걸기 전 걸러진 스킵(`STATUS_SKIPPED`)은 걸린 적이 없어 뺀다. 재시작 폐기
    (`discarded_restart`)는 **포함한다** — 사용자가 실제로 주문을 걸었기 때문이다(WAN-230).
    ⚠️ 체결률 분모(filled+no_fill)와는 다른 값이다 — 예약은 폰 요약의 헤드라인 「예약 N」이고,
    재시작에 견디도록 메모리가 아니라 이 DB 카운트에서 낸다(WAN-230)."""
    filled: int = 0
    """창 안에 체결된 지정가 주문 수(체결률 분자). 집행 계층의 하류 처분(진입/거부)과
    무관하다 — '지정가에 닿았나'를 재는 값이라 거부돼도 체결로 센다(거부는 사유로 별도)."""
    no_fill: int = 0
    """걸렸으나 유효기간 내 안 닿아 만료된 수(체결률 분모의 나머지). 밴드 규칙 3 기각은 뺀다."""
    deviation: int = 0
    """걸렸으나 밴드가 한 번도 유리하지 않아 끝난 만료(볼린저 규칙 3 기각, `first_rested_ms
    IS NULL`)."""
    zone_width: int = 0
    """존폭 필터에 걸려 주문을 걸지 않은 셋업 수(WAN-159 · 깔때기 상단)."""
    cell_busy: int = 0
    """슬롯 점유로 주문을 못 걸었거나(상단) 체결 후 이미 오픈이라 거부된 수(하단) 합산."""
    retap: int = 0
    """재탭이라 걸른 셋업 수(옵트인 `retap_mode="once"`에서만 — 채택 기본값은 0)."""
    notional: int = 0
    """체결됐으나 북 명목 상한 소진으로 거부된 수(WAN-171/213)."""
    sizing: int = 0
    """체결됐으나 사이징 수량 0으로 거부된 수(대부분 손절폭 가드 0.3%, WAN-79)."""
    other: int = 0
    """그 밖의 체결 후 거부(리스크 한도 등) · 코드 미기록 거부."""

    @property
    def fill_rate(self) -> float | None:
        """체결률 = 체결 ÷ (체결 + no_fill). 결말 표본이 없으면 None(WAN-221 정의).

        `SeriesFillStats.fill_rate`(체결/결말 — 무효화·조건취소 포함)와 분모가 다르다:
        이 이슈가 폰 요약에 쓰기로 한 정의는 "닿았나 vs 안 닿았나"만 보는 filled/(filled+no_fill)다.
        """
        denom = self.filled + self.no_fill
        return self.filled / denom if denom else None


@dataclass(frozen=True)
class LedgerEntry:
    """진입 깔때기 행 하나(WAN-219 조회 화면). `funnel_counts`와 같은 모집단·분류다.

    `ledger_entries`가 창 안의 깔때기 행(체결·만료·주문 걸기 전 스킵)을 이 형태로 나열한다 —
    화면은 이 목록을 라벨로만 바꿔 체결률·사유 분포·진입/미진입 목록을 그린다(재계산 없음).
    무효화·조건취소·대기·재시작 폐기는 깔때기 밖이라 나열되지 않는다(funnel_counts와 같다).
    """

    symbol: str
    timeframe: str
    direction: str
    event_ms: int
    """창 귀속·표시에 쓰는 사건 시각 — 체결=fill_ms · 만료=terminal_ms · 스킵=placed_ms."""
    filled: bool
    """지정가가 닿았는가(체결). 체결률 분자에 드는 행이면 True."""
    reason: str
    """결과 사유 정규 코드(`LEDGER_REASON_*` · `SKIP_REASON_*` · `REJECT_CODE_*`)."""
    fill_price: float | None
    limit_price: float | None
    penetration_bps: float | None

    @property
    def entered(self) -> bool:
        """체결이 페이퍼 포지션으로 실제 열렸는가(진입 성공)."""
        return self.reason == LEDGER_REASON_ENTERED


@dataclass(frozen=True)
class PlacedOrder:
    """당일 조회의 주문 한 건(WAN-232 `live.fill_report --day`).

    창 귀속이 `placed_ms`(예약 시각)인 것이 `LedgerEntry`(사건 시각 귀속)와 다르다 —
    이 화면의 질문은 "**오늘 예약한** 지정가가 어떻게 됐나"라 코호트를 예약 시각으로 잡는다.
    `status`·`entry_status`·`skip_reason`은 장부 원값 그대로 싣고(가공은 렌더러가 라벨로만),
    무효화·조건취소·대기·재시작 폐기도 숨기지 않고 전부 나열한다(그날 예약한 주문이면 다 보인다).
    """

    symbol: str
    timeframe: str
    direction: str
    placed_ms: int
    status: str
    """`live_limit_orders.status` 원값(FILLED/CANCELLED_*/PENDING/skipped/discarded_restart)."""
    limit_price: float | None
    """마지막 지정가(`last_limit_price`) — 체결가가 아니라 걸었던 가격."""
    fill_ms: int | None
    fill_penetration_bps: float | None
    first_rested_ms: int | None
    """주문판에 처음 걸린 시각. 만료인데 이게 `None`이면 밴드가 한 번도 유리하지 않은
    볼린저 규칙 3 기각(deviation)이라 순수 미체결(no_fill)과 구분한다."""
    entry_status: str | None
    entry_reject_reason: str | None
    skip_reason: str | None
    # -- 아래 다섯은 `live_limit_orders`에 이미 있으나 WAN-232 표는 안 실었다(WAN-234).
    #    거래별 타임라인은 「얼마에 체결했나·손절/익절 목표는·어느 존이었나」까지 봐야 해서
    #    싣는다. 옛 행·아직 안 채워진 값은 `None`이라 렌더러가 `—`로 읽는다.
    fill_price: float | None = None
    """실제 체결가(`fill_price`). 미체결이면 `None`."""
    stop_price: float | None = None
    """진입 근거 오더블록 무효화 경계 = 손절 목표가. 옛 행은 `None`."""
    take_profit_price: float | None = None
    """고정 1.5R 익절 목표가. 옛 행은 `None`."""
    zone_start_time: int | None = None
    """이 셋업이 속한 오더블록 존의 형성 봉 시각(상위TF `open_time`). 백테스트 셋업과
    같은 축이라 라이브↔백테스트 존 대조·차트 점프의 조인 키다(WAN-234)."""
    zone_confirmed_time: int | None = None
    """존 확정 봉 시각(상위TF `open_time`). `zone_start_time`과 한 쌍의 조인 키."""


@dataclass(frozen=True)
class DaySummary:
    """당일(placed_ms 창) 주문 코호트의 한 줄 요약(WAN-232).

    창 귀속이 `placed_ms`인 것 말고는 회계 정의가 `FunnelCounts`/`SeriesFillStats`와 같다:
    체결률 = 체결 / (체결 + 미체결) · 진입률 = 진입 / (진입 + 거부)이고, `discarded_restart`·
    `skipped`는 체결률 분모 밖이다(주문이 결말을 못 냈거나 아예 걸린 적 없다). 모든 사건이 같은
    KST 하루 안에 나면 이 카운트는 `funnel_counts`와 정확히 같다(회귀 테스트가 고정 — CTA #3).
    """

    reserved: int
    """예약 = 주문이 실제로 걸린 수(비-`skipped`). 필터제외는 걸린 적이 없어 여기서 뺀다."""
    filled: int
    no_fill: int
    """걸렸으나 유효기간 내 안 닿아 만료(순수 미체결)."""
    deviation: int
    """걸렸으나 밴드가 한 번도 유리하지 않아 끝난 만료(볼린저 규칙 3 기각)."""
    invalidated: int
    condition_failed: int
    pending: int
    """아직 결말이 나지 않은 대기 주문 — 체결률 분모 밖."""
    discarded_restart: int
    skipped: int
    entered: int
    entry_rejected: int
    entry_unrecorded: int

    @property
    def fill_rate(self) -> float | None:
        """예약→체결(닿음) 전환율 = 체결 / (체결 + 미체결). `FunnelCounts.fill_rate`와 같은 정의."""
        denom = self.filled + self.no_fill
        return self.filled / denom if denom else None

    @property
    def entry_rate(self) -> float | None:
        """체결→진입 전환율 = 진입 / (진입 + 거부). `SeriesFillStats.entry_rate`(WAN-194)와 같다.

        처분 미기록(`entry_unrecorded`)은 결과를 모르므로 분모에서 뺀다.
        """
        decided = self.entered + self.entry_rejected
        return self.entered / decided if decided else None

    @property
    def taps(self) -> int:
        """탭 = 예약(주문 걸림) + 필터제외(주문 걸기 전 걸러짐). 진입 깔때기의 최상단.

        오늘 감지된 존 탭 전이 총수 — `record_placed`로 주문이 걸린 것(`reserved`)과
        `record_skipped`로 주문 걸기 전 걸러진 것(`skipped`)의 합이다. 백테스트 대조에서
        「탭/셋업 감지」 단계의 라이브 값이다(WAN-233)."""
        return self.reserved + self.skipped

    @classmethod
    def from_orders(cls, orders: Sequence[PlacedOrder]) -> DaySummary:
        """주문 목록(한 창의 코호트)을 `funnel_counts`와 같은 분류 규칙으로 센다(WAN-232).

        `OrderJournal.day_summary`(전 시리즈)와 대조 도구의 심볼×TF별 집계(WAN-233)가 같은
        분류를 공유하도록 분류 로직을 여기 한곳에 둔다 — 두 벌로 갈라지면 같은 코호트가 두
        곳에서 다른 수로 보인다. 만료는 `first_rested_ms`로 순수 미체결(no_fill)과 밴드 규칙 3
        기각(deviation)을 가르고, 체결의 하류 처분(진입/거부/미기록)은 `entry_status`로 가른다.
        """
        filled = no_fill = deviation = invalidated = condition_failed = 0
        pending = discarded = skipped = 0
        entered = rejected = unrecorded = 0
        for order in orders:
            if order.status == LimitOrderStatus.FILLED.value:
                filled += 1
                if order.entry_status == ENTRY_STATUS_ENTERED:
                    entered += 1
                elif order.entry_status == ENTRY_STATUS_REJECTED:
                    rejected += 1
                else:
                    unrecorded += 1
            elif order.status == LimitOrderStatus.CANCELLED_EXPIRED.value:
                if order.first_rested_ms is None:
                    deviation += 1
                else:
                    no_fill += 1
            elif order.status == LimitOrderStatus.CANCELLED_INVALIDATED.value:
                invalidated += 1
            elif order.status == LimitOrderStatus.CANCELLED_CONDITION_FAILED.value:
                condition_failed += 1
            elif order.status == LimitOrderStatus.PENDING.value:
                pending += 1
            elif order.status == STATUS_DISCARDED_RESTART:
                discarded += 1
            elif order.status == STATUS_SKIPPED:
                skipped += 1
        return cls(
            reserved=len(orders) - skipped,
            filled=filled,
            no_fill=no_fill,
            deviation=deviation,
            invalidated=invalidated,
            condition_failed=condition_failed,
            pending=pending,
            discarded_restart=discarded,
            skipped=skipped,
            entered=entered,
            entry_rejected=rejected,
            entry_unrecorded=unrecorded,
        )


@dataclass(frozen=True)
class SessionSpan:
    """러너 가동 구간 하나(시작 ~ 마지막 하트비트)."""

    session_id: int
    started_ms: int
    last_seen_ms: int


class OrderJournal:
    """지정가 주문 생애·러너 가동 구간을 SQLite에 기록하는 장부(단일 작성자 = 러너)."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        configure_connection(self._conn)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """옛 DB에 나중에 생긴 열을 덧붙인다(호출부가 이미 락·트랜잭션 안이다).

        `CREATE TABLE IF NOT EXISTS`는 **이미 있는 테이블의 열을 늘려 주지 않는다** —
        서버 DB는 WAN-45 시절 스키마로 만들어졌으므로 마이그레이션 없이 새 열을 쓰면
        `OperationalError`로 러너가 죽는다. 존재 여부를 보고 `ALTER TABLE`을 건다
        (SQLite는 `ADD COLUMN`이 O(1)이고 기존 행은 NULL로 채운다).
        """
        existing = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(live_limit_orders)")
        }
        for column in (
            "entry_status",
            "entry_reject_reason",
            "entry_reject_code",
            "skip_reason",
        ):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE live_limit_orders ADD COLUMN {column} TEXT")

    def close(self) -> None:
        self._conn.close()

    # -- 러너 가동(uptime) ---------------------------------------------------

    def start_session(self, *, now_ms: int) -> int:
        """새 가동 세션을 연다. 세션 id를 반환한다."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO live_runner_sessions (started_ms, last_seen_ms) VALUES (?, ?)",
                (now_ms, now_ms),
            )
        session_id = cur.lastrowid
        assert session_id is not None
        return session_id

    def heartbeat(self, session_id: int, *, now_ms: int) -> None:
        """세션 생존 시각을 갱신한다(가동 구간의 오른쪽 끝)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_runner_sessions SET last_seen_ms = ? WHERE session_id = ?",
                (now_ms, session_id),
            )

    def sessions(self) -> list[SessionSpan]:
        """모든 가동 구간(시작 순). 구간 사이의 틈이 곧 중단(다운타임)이다."""
        rows = self._conn.execute(
            "SELECT session_id, started_ms, last_seen_ms FROM live_runner_sessions "
            "ORDER BY started_ms"
        ).fetchall()
        return [SessionSpan(int(r[0]), int(r[1]), int(r[2])) for r in rows]

    # -- 주문 생애 -----------------------------------------------------------

    def record_placed(
        self,
        order: PendingLimitOrder,
        *,
        session_id: int,
        zone_start_time: int | None,
        zone_confirmed_time: int | None,
    ) -> int:
        """주문 예약을 기록하고 장부 행 id를 반환한다(주문의 `journal_id`로 쓴다)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
                " zone_start_time, zone_confirmed_time, tap_index, placed_ms, status,"
                " first_rested_ms, last_limit_price)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    order.symbol,
                    order.timeframe,
                    order.direction.value,
                    zone_start_time,
                    zone_confirmed_time,
                    order.tap_index,
                    order.placed_ms,
                    LimitOrderStatus.PENDING.value,
                    order.first_rested_ms,
                    order.last_limit_price,
                ),
            )
        row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    def record_skipped(
        self,
        *,
        session_id: int,
        symbol: str,
        timeframe: str,
        direction: str,
        tap_index: int,
        placed_ms: int,
        reason: str,
        zone_start_time: int | None,
        zone_confirmed_time: int | None,
    ) -> int:
        """주문이 걸리기 **전** 걸러진 셋업을 미진입 사유와 함께 기록한다(WAN-217).

        `record_placed`와 달리 대기 주문(`PendingLimitOrder`)이 없다 — `zone_width`·
        `cell_busy`·`retap`은 지정가가 확정되기 전 윗단계에서 탈락해 주문 생애를 시작조차
        못 한다. 그 셋업이 아무 데도 안 남으면 "왜 안 들어갔나"(체결률 실측 깔때기의 상단)를
        사후에 셀 수 없으므로, 같은 `live_limit_orders` 테이블에 `status='skipped'` 행으로
        얹는다(장부를 두 벌로 만들지 않는다 — WAN-45/100 「같은 함수 공유」 원칙). 체결률
        분모에는 넣지 않는다(주문이 걸린 적 없다 — `fill_stats`가 `placed`에서 뺀다).

        `placed_ms`에는 탭이 감지된 1분봉 시각을 넣는다(주문을 걸었다면 예약했을 시각) —
        `record_placed`의 `placed_ms`와 같은 축이라 시간순으로 함께 읽을 수 있다.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
                " zone_start_time, zone_confirmed_time, tap_index, placed_ms, status, skip_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    symbol,
                    timeframe,
                    direction,
                    zone_start_time,
                    zone_confirmed_time,
                    tap_index,
                    placed_ms,
                    STATUS_SKIPPED,
                    reason,
                ),
            )
        row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    def record_progress(self, journal_id: int, order: PendingLimitOrder) -> None:
        """대기 중 주문의 진행 상태(첫 걸림 시각·마지막 지정가)를 갱신한다."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET first_rested_ms = ?, last_limit_price = ?"
                " WHERE id = ?",
                (order.first_rested_ms, order.last_limit_price, journal_id),
            )

    def record_filled(self, journal_id: int, fill: LimitFill) -> None:
        """체결을 기록한다 — 체결가·RSI·관통 폭·대기 시간이 실측의 본체다."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ?, fill_ms = ?,"
                " fill_price = ?, fill_rsi = ?, fill_penetration_bps = ?, stop_price = ?,"
                " take_profit_price = ?, wait_ms = ?, last_limit_price = ? WHERE id = ?",
                (
                    LimitOrderStatus.FILLED.value,
                    fill.time,
                    fill.time,
                    fill.price,
                    fill.rsi,
                    fill.penetration_bps,
                    fill.stop_price,
                    fill.take_profit_price,
                    fill.waited_ms,
                    fill.price,
                    journal_id,
                ),
            )

    def record_entry_result(
        self, journal_id: int, *, entered: bool, reason: str = "", reason_code: str = ""
    ) -> None:
        """체결의 **하류 처분**을 기록한다 — 포지션이 열렸는지, 거부면 사유(WAN-194).

        체결(`record_filled`) 직후에 부른다. 이 호출이 없으면 그 행은 `entry_status
        IS NULL`로 남아 `orphan_fills()`에 잡힌다 — 그게 "체결과 포지션 쓰기 사이에서
        죽었다"의 유일한 신호이므로, **거부일 때도 반드시 기록한다**(조용한 성공/실패
        구분 불가가 이 이슈의 사고 원인이었다).

        `reason`은 사람용 자유 텍스트, `reason_code`(WAN-221 · `REJECT_CODE_*`)는 집계가
        문자열을 파싱하지 않고 사유를 세도록 싣는 안정 코드다 — 일일 요약의 사유 카운트
        (`funnel_counts`)가 이 코드로 명목/사이징/슬롯참을 가른다. 진입 성공이면 둘 다 비운다.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET entry_status = ?, entry_reject_reason = ?,"
                " entry_reject_code = ? WHERE id = ?",
                (
                    ENTRY_STATUS_ENTERED if entered else ENTRY_STATUS_REJECTED,
                    None if entered else (reason or "사유 미기록"),
                    None if entered else (reason_code or None),
                    journal_id,
                ),
            )

    def orphan_fills(self, *, since_ms: int | None = None) -> list[OrphanFill]:
        """처분이 기록되지 않은 체결(= `filled` + `entry_status IS NULL`)을 모은다.

        `since_ms`를 주면 그 이후 체결만 본다 — 열 도입 전 기록은 전부 NULL이라 같은
        모양으로 잡히므로, "진짜 유실"을 보려면 도입 시점 이후로 잘라야 한다.
        """
        sql = (
            "SELECT id, symbol, timeframe, fill_ms, fill_price, stop_price"
            " FROM live_limit_orders WHERE status = ? AND entry_status IS NULL"
        )
        args: list[object] = [LimitOrderStatus.FILLED.value]
        if since_ms is not None:
            sql += " AND fill_ms >= ?"
            args.append(since_ms)
        rows = self._conn.execute(sql + " ORDER BY fill_ms", args).fetchall()
        return [
            OrphanFill(
                journal_id=int(r[0]),
                symbol=str(r[1]),
                timeframe=str(r[2]),
                fill_ms=None if r[3] is None else int(r[3]),
                fill_price=None if r[4] is None else float(r[4]),
                stop_price=None if r[5] is None else float(r[5]),
            )
            for r in rows
        ]

    def record_cancelled(self, journal_id: int, status: LimitOrderStatus, *, now_ms: int) -> None:
        """취소(만료·무효화·조건 미충족)를 기록한다."""
        if not status.is_terminal or status is LimitOrderStatus.FILLED:
            raise ValueError(f"취소 상태가 아닙니다: {status}")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ? WHERE id = ?",
                (status.value, now_ms, journal_id),
            )

    def record_discarded(self, journal_id: int, *, now_ms: int) -> None:
        """개별 주문을 측정 무효로 폐기한다(1분봉 공백 등 — 재시작 폐기와 같은 상태).

        일반 취소와 달리 체결률 분모에서 빠진다 — 러너/데이터가 죽어 있던 구간의 결과를
        지어내지 않기 위해서다.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ? WHERE id = ?",
                (STATUS_DISCARDED_RESTART, now_ms, journal_id),
            )

    def discard_stale_pending(self, *, now_ms: int) -> int:
        """이전 세션이 남긴 대기 주문을 재시작 폐기로 마감한다. 폐기 건수를 반환.

        러너 재시작 시 대기 주문은 복원하지 않고 버린다(모듈 독스트링의 재시작 정책).
        일반 취소와 다른 상태(`discarded_restart`)로 남겨 체결률 분모에서 빠진다.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ? WHERE status = ?",
                (STATUS_DISCARDED_RESTART, now_ms, LimitOrderStatus.PENDING.value),
            )
        return cur.rowcount

    # -- 요약 ----------------------------------------------------------------

    def fill_stats(self) -> list[SeriesFillStats]:
        """심볼·TF별 체결률 요약(백테스트 `baseline` 가정과 나란히 놓는 표의 원자료)."""
        rows = self._conn.execute(
            "SELECT symbol, timeframe, status, wait_ms, fill_penetration_bps, entry_status,"
            " first_rested_ms, skip_reason FROM live_limit_orders ORDER BY symbol, timeframe"
        ).fetchall()
        by_series: dict[
            tuple[str, str],
            list[tuple[str, int | None, float | None, str | None, int | None, str | None]],
        ] = {}
        for symbol, timeframe, status, wait_ms, penetration, entry_status, rested, skip in rows:
            by_series.setdefault((str(symbol), str(timeframe)), []).append(
                (str(status), wait_ms, penetration, entry_status, rested, skip)
            )

        stats: list[SeriesFillStats] = []
        for (symbol, timeframe), entries in sorted(by_series.items()):
            counts: dict[str, int] = {}
            waits: list[int] = []
            marginal = 0
            entered = rejected = unrecorded = 0
            no_band = 0
            skip_counts: dict[str, int] = {}
            for status, wait_ms, penetration, entry_status, rested, skip in entries:
                counts[status] = counts.get(status, 0) + 1
                if status == STATUS_SKIPPED:
                    skip_counts[str(skip)] = skip_counts.get(str(skip), 0) + 1
                elif status == LimitOrderStatus.CANCELLED_EXPIRED.value:
                    # 밴드가 한 번도 유리하지 않아 주문판에 걸린 적조차 없는 만료 =
                    # 볼린저 규칙 3 기각(deviation). 걸렸다 안 닿은 순수 no_fill과 구분한다.
                    if rested is None:
                        no_band += 1
                elif status == LimitOrderStatus.FILLED.value:
                    if wait_ms is not None:
                        waits.append(int(wait_ms))
                    if penetration is not None and penetration < MARGINAL_FILL_BPS:
                        marginal += 1
                    if entry_status == ENTRY_STATUS_ENTERED:
                        entered += 1
                    elif entry_status == ENTRY_STATUS_REJECTED:
                        rejected += 1
                    else:
                        unrecorded += 1
            discarded = counts.get(STATUS_DISCARDED_RESTART, 0)
            skipped = counts.get(STATUS_SKIPPED, 0)
            stats.append(
                SeriesFillStats(
                    symbol=symbol,
                    timeframe=timeframe,
                    placed=len(entries) - discarded - skipped,
                    pending=counts.get(LimitOrderStatus.PENDING.value, 0),
                    filled=counts.get(LimitOrderStatus.FILLED.value, 0),
                    cancelled_expired=counts.get(LimitOrderStatus.CANCELLED_EXPIRED.value, 0),
                    cancelled_invalidated=counts.get(
                        LimitOrderStatus.CANCELLED_INVALIDATED.value, 0
                    ),
                    cancelled_condition_failed=counts.get(
                        LimitOrderStatus.CANCELLED_CONDITION_FAILED.value, 0
                    ),
                    discarded_restart=discarded,
                    median_wait_ms=statistics.median(waits) if waits else None,
                    marginal_fills=marginal,
                    entered=entered,
                    entry_rejected=rejected,
                    entry_unrecorded=unrecorded,
                    skipped_zone_width=skip_counts.get(SKIP_REASON_ZONE_WIDTH, 0),
                    skipped_cell_busy=skip_counts.get(SKIP_REASON_CELL_BUSY, 0),
                    skipped_retap=skip_counts.get(SKIP_REASON_RETAP, 0),
                    unfilled_no_band=no_band,
                )
            )
        return stats

    def funnel_counts(self, *, start_ms: int, end_ms: int) -> FunnelCounts:
        """`[start_ms, end_ms)` 창의 진입 깔때기 사유를 전 시리즈 합산한다(WAN-221 일일 요약).

        사건마다 창 귀속 시각이 다르다 — 체결은 `fill_ms`, 만료는 `terminal_ms`, 주문 걸기 전
        스킵은 `placed_ms`(스킵 행은 종결 시각이 없다). 각 행을 자기 시각으로 창에 넣는다.
        재시작 폐기(`discarded_restart`)·대기 중(`pending`)은 **결말이 없어** 사유 카운트
        어디에도 안 들지만, 헤드라인 `placed`(예약)에는 `placed_ms`로 든다 — 예약은 사건이
        아니라 「주문을 걸었다」는 사실이라 결말과 무관하다(WAN-230). 그래서 이 한 번의 조회가
        일일 요약의 헤드라인(예약·체결·만료)과 미진입 사유를 **한 창·한 출처**로 낸다.
        """
        rows = self._conn.execute(
            "SELECT status, first_rested_ms, entry_status, entry_reject_code, skip_reason,"
            " fill_ms, terminal_ms, placed_ms FROM live_limit_orders"
        ).fetchall()

        def _in_window(ts: int | None) -> bool:
            return ts is not None and start_ms <= int(ts) < end_ms

        placed = 0
        filled = no_fill = deviation = 0
        skip_zone = skip_cell = skip_retap = 0
        rej_cell = rej_notional = rej_sizing = rej_other = 0
        for row in rows:
            status = str(row[0])
            rested, entry_status, reject_code, skip = row[1], row[2], row[3], row[4]
            fill_ms, terminal_ms, placed_ms = row[5], row[6], row[7]
            # 예약(헤드라인 「예약 N」, WAN-230): 주문이 실제로 걸린 행(비-skipped) 중
            # placed_ms가 창 안. 재시작 폐기도 예약이었으니 포함하고, 스킵만 뺀다. 사건
            # 시각(fill/terminal)과 독립이라 아래 상태별 `continue`에 걸리지 않게 먼저 센다.
            if status != STATUS_SKIPPED and _in_window(placed_ms):
                placed += 1
            if status == LimitOrderStatus.FILLED.value:
                if not _in_window(fill_ms):
                    continue
                filled += 1
                if entry_status == ENTRY_STATUS_REJECTED:
                    if reject_code == REJECT_CODE_CELL_BUSY:
                        rej_cell += 1
                    elif reject_code == REJECT_CODE_NOTIONAL:
                        rej_notional += 1
                    elif reject_code == REJECT_CODE_SIZING:
                        rej_sizing += 1
                    else:
                        rej_other += 1  # 리스크 한도·코드 미기록(WAN-221 이전 행) 등.
            elif status == LimitOrderStatus.CANCELLED_EXPIRED.value:
                if not _in_window(terminal_ms):
                    continue
                # 밴드가 한 번도 유리하지 않아 주문판에 실린 적 없는 만료 = deviation(규칙 3).
                if rested is None:
                    deviation += 1
                else:
                    no_fill += 1
            elif status == STATUS_SKIPPED:
                if not _in_window(placed_ms):
                    continue
                if skip == SKIP_REASON_ZONE_WIDTH:
                    skip_zone += 1
                elif skip == SKIP_REASON_CELL_BUSY:
                    skip_cell += 1
                elif skip == SKIP_REASON_RETAP:
                    skip_retap += 1
        return FunnelCounts(
            placed=placed,
            filled=filled,
            no_fill=no_fill,
            deviation=deviation,
            zone_width=skip_zone,
            cell_busy=skip_cell + rej_cell,
            retap=skip_retap,
            notional=rej_notional,
            sizing=rej_sizing,
            other=rej_other,
        )

    def ledger_entries(self, *, start_ms: int, end_ms: int) -> list[LedgerEntry]:
        """`[start_ms, end_ms)` 창의 진입 깔때기 행을 결과 사유와 함께 시간순 나열한다(WAN-219).

        `funnel_counts`와 **같은 모집단·같은 창 귀속·같은 분류 규칙**을 쓴다 — 체결은 `fill_ms`,
        만료는 `terminal_ms`, 주문 걸기 전 스킵은 `placed_ms`로 창에 넣고, 그 밖 상태(무효화·조건
        취소·대기·재시작 폐기)는 깔때기 밖이라 나열하지 않는다. 그래서 이 목록을 집계하면
        `funnel_counts`와 정확히 같은 수가 나온다(회귀 테스트가 고정). 조회 전용이라 쓰지 않는다.
        """
        rows = self._conn.execute(
            "SELECT symbol, timeframe, direction, status, first_rested_ms, entry_status,"
            " entry_reject_code, skip_reason, fill_ms, terminal_ms, placed_ms, fill_price,"
            " last_limit_price, fill_penetration_bps FROM live_limit_orders"
        ).fetchall()

        def _in_window(ts: int | None) -> bool:
            return ts is not None and start_ms <= int(ts) < end_ms

        entries: list[LedgerEntry] = []
        for row in rows:
            symbol, timeframe, direction = str(row[0]), str(row[1]), str(row[2])
            status = str(row[3])
            rested, entry_status, reject_code, skip = row[4], row[5], row[6], row[7]
            fill_ms, terminal_ms, placed_ms = row[8], row[9], row[10]
            fill_price, limit_price, penetration = row[11], row[12], row[13]

            if status == LimitOrderStatus.FILLED.value:
                if not _in_window(fill_ms):
                    continue
                event_ms = int(fill_ms)
                filled = True
                if entry_status == ENTRY_STATUS_ENTERED:
                    reason = LEDGER_REASON_ENTERED
                elif entry_status == ENTRY_STATUS_REJECTED:
                    reason = (
                        str(reject_code)
                        if reject_code
                        in (REJECT_CODE_CELL_BUSY, REJECT_CODE_NOTIONAL, REJECT_CODE_SIZING)
                        else LEDGER_REASON_OTHER
                    )
                else:
                    reason = LEDGER_REASON_UNRECORDED
            elif status == LimitOrderStatus.CANCELLED_EXPIRED.value:
                if not _in_window(terminal_ms):
                    continue
                event_ms = int(terminal_ms)
                filled = False
                # 밴드가 한 번도 유리하지 않아 주문판에 실린 적 없는 만료 = deviation(규칙 3).
                reason = LEDGER_REASON_DEVIATION if rested is None else LEDGER_REASON_NO_FILL
            elif status == STATUS_SKIPPED:
                if not _in_window(placed_ms):
                    continue
                event_ms = int(placed_ms)
                filled = False
                reason = str(skip)
            else:
                continue  # 무효화·조건취소·대기·재시작 폐기는 깔때기 밖.

            entries.append(
                LedgerEntry(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=direction,
                    event_ms=event_ms,
                    filled=filled,
                    reason=reason,
                    fill_price=None if fill_price is None else float(fill_price),
                    limit_price=None if limit_price is None else float(limit_price),
                    penetration_bps=None if penetration is None else float(penetration),
                )
            )
        entries.sort(key=lambda e: e.event_ms)
        return entries

    def orders_placed_between(self, *, start_ms: int, end_ms: int) -> list[PlacedOrder]:
        """`placed_ms ∈ [start_ms, end_ms)`인 주문을 예약 시각순으로 나열한다(WAN-232 당일 조회).

        `funnel_counts`/`ledger_entries`가 사건 시각(체결·만료·스킵)으로 창을 나누는 것과 달리,
        이 조회는 **예약 시각(`placed_ms`)** 하나로 코호트를 잡는다 — "오늘 예약한 지정가가
        어떻게 됐나"가 질문이라 예약된 그 주문을 결말과 무관하게 전부 본다(대기·무효화·재시작
        폐기도 숨기지 않는다). 창 경계는 호출부가 `common.timefmt.kst_day_bounds_for_date`로
        잡아 일일 요약 funnel과 같은 자정 경계를 공유한다(CTA #2)."""
        rows = self._conn.execute(
            "SELECT symbol, timeframe, direction, placed_ms, status, last_limit_price,"
            " fill_ms, fill_penetration_bps, first_rested_ms, entry_status,"
            " entry_reject_reason, skip_reason, fill_price, stop_price, take_profit_price,"
            " zone_start_time, zone_confirmed_time FROM live_limit_orders"
            " WHERE placed_ms >= ? AND placed_ms < ? ORDER BY placed_ms, id",
            (start_ms, end_ms),
        ).fetchall()
        return [
            PlacedOrder(
                symbol=str(r[0]),
                timeframe=str(r[1]),
                direction=str(r[2]),
                placed_ms=int(r[3]),
                status=str(r[4]),
                limit_price=None if r[5] is None else float(r[5]),
                fill_ms=None if r[6] is None else int(r[6]),
                fill_penetration_bps=None if r[7] is None else float(r[7]),
                first_rested_ms=None if r[8] is None else int(r[8]),
                entry_status=None if r[9] is None else str(r[9]),
                entry_reject_reason=None if r[10] is None else str(r[10]),
                skip_reason=None if r[11] is None else str(r[11]),
                fill_price=None if r[12] is None else float(r[12]),
                stop_price=None if r[13] is None else float(r[13]),
                take_profit_price=None if r[14] is None else float(r[14]),
                zone_start_time=None if r[15] is None else int(r[15]),
                zone_confirmed_time=None if r[16] is None else int(r[16]),
            )
            for r in rows
        ]

    def day_summary(self, *, start_ms: int, end_ms: int) -> DaySummary:
        """당일 코호트(`placed_ms` 창)의 한 줄 요약을 낸다(WAN-232).

        `orders_placed_between`이 준 주문을 `funnel_counts`와 **같은 분류 규칙**으로 센다 —
        만료는 `first_rested_ms`로 순수 미체결(no_fill)과 밴드 규칙 3 기각(deviation)을 가르고,
        체결의 하류 처분(진입/거부/미기록)은 `entry_status`로 가른다. 그래서 모든 사건이 같은
        KST 하루에 나면 체결률 분자·분모가 `funnel_counts`와 정확히 일치한다(CTA #3).

        분류 로직은 `DaySummary.from_orders`가 소유한다 — 대조 도구(WAN-233)의 심볼×TF별
        집계가 같은 규칙을 재사용해 두 곳에서 같은 코호트가 다른 수로 보이지 않게 한다.
        """
        return DaySummary.from_orders(self.orders_placed_between(start_ms=start_ms, end_ms=end_ms))
