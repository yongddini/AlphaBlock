"""존-지정가 페이퍼 러너 — 스토캐스틱 팔 + 엔진 C의 실시간 배선 (WAN-45 · WAN-451).

📌 **이 러너가 돌리는 것은 둘뿐이다**(WAN-451 ⑦ · 옛 규칙 정리 2026-10-15): 오더블록 시리즈
위의 **스토캐스틱 팔**(`live.arm_rules` — 탭마다 독립 지정가 · 팔 규칙 · 몰림 규칙 · 매주 종목
목록 · 손절과 시간 청산)과 **엔진 C**(`live.tsmom_runner`, 하루 1회 리밸런싱). 옛 오더블록
페이퍼(칸당 대기 주문 1개 · 익절 후 재진입 · 웹소켓 틱 피드 · 고정 R 익절 · 일 손실
서킷브레이커 알림 · 고정 시리즈 목록)는 없어졌다 — 오더블록 시리즈를 켜면 팔이 반드시 켜져
있어야 하고, 아니면 러너가 기동을 거부한다(`run_zone_limit_runner`).

옛 오더블록 페이퍼가 세운 목적 두 가지는 팔 위에서 그대로 유효하다:

1. **체결률 실측(1순위)** — 이 저장소의 모든 백테스트 판정은 `baseline`("닿으면 체결")
   낙관 가정 위에 서 있는데, 그 가정을 풀 예정이던 틱·호가 수집(WAN-98)이 Canceled라
   실측이 유일한 통로다. 존에 지정가를 걸어두면 체결되는지 안 되는지 그냥 알게 된다 —
   걸린/체결된/만료·취소된 주문을 `live.order_journal`에 누적하고, 요약은
   `python -m live.fill_report`가 백테스트 가정과 나란히 찍는다.
2. **백테스트↔라이브 간극 닫기** — 옛 러너(`live.runner.SignalRunner`)는 A안(봉 마감
   종가) 시그널 생성기라, WAN-95 이후 채택 기본값이 지정가로 바뀐 뒤에도 사용자가 하지
   않는 매매를 알리고 있었다.

## 틱 소스 — 웹소켓 → 수집기 → 저장소 → 러너 (1분봉 해상도)

수집 데몬(`alphablock collect`)이 바이낸스 웹소켓 kline 스트림(`data.stream`)으로 확정
1분봉을 저장소에 쌓고, 이 러너는 그 1분봉을 **백테스트 서브스텝과 같은 자**로 소비한다
(`ZoneLimitLiveEngine.on_substep` — 터치 판정 `low <= 지정가`, 밴드·RSI 표본 = 1분봉
종가). 러너가 웹소켓을 직접 구독하지 않는 이유:

* **측정 대상이 백테스트의 가정 그 자체다** — 백테스트가 1분봉 저가로 체결을 판정하므로
  같은 자로 재야 "라이브에서 그 가정대로 체결되는가"에 답이 된다. 더 촘촘한 틱은 체결
  판정을 바꾸지 않으면서(판정은 봉 범위 기준) 비교 축만 흐린다.
* PM 실측(이슈 코멘트 §1)대로 밴드는 봉 안에서 중앙값 1.6~4.1bp밖에 안 움직인다 —
  분 단위 재산정이면 충분하고, 페이퍼에는 주문 정정 비용도 없다.
* 수집기가 이미 웹소켓·재접속·하트비트를 소유한다 — 러너가 제 소켓을 열면 같은 걱정을
  두 벌 관리하게 된다.

그 대가로 체결 감지가 1분봉 확정 + 폴링 간격만큼 늦지만, 체결 시각·대기 시간은 벽시계가
아니라 **봉 시각**으로 기록하므로 측정값은 지연과 무관하다.

## 로컬 맥 운영의 한계 처리 (사용자 결정 2026-07-21 · 🔁 재시작 정책은 WAN-306이 개정)

* **가동 시간 기록** — 러너 세션(시작·마지막 하트비트)을 장부에 남긴다. 체결률의 분모가
  "러너가 살아 있던 시간"임을 표에서 명시할 수 있다.
* 🔁 **재시작 정책 = 복원 + 따라잡기 (WAN-306, 사용자 결정 2026-08-14)** — 옛 「버리고
  새로 걸기」는 재시작마다 존 기억(전이 맥락·탭 이력·대기 주문·24봉 만료
  시계)을 리셋해 라이브↔백테 판단을 갈랐다(08-13 실사례: 재시작 직후 즉시 체결, WAN-305
  픽스처). 이제 러너는 매 서브스텝 엔진 상태를 영속화(`live.engine_state`)하고, 기동 시
  복원한 뒤 죽어 있던 구간의 확정 1분봉을 **순서대로 재생**해 그 사이 일어났어야 할
  체결·청산·만료를 페이퍼로 집행한다(뒷북이 아니라 따라잡기 — 복원된 주문이 실제로
  걸려 있었으므로 그 구간 체결은 정당하다). 오픈 포지션은 예전처럼 `PaperExecutor`가
  저장소에서 복구한다(WAN-34).
  ⚠️ **`discarded_restart`는 이제 예외 경로다**: 공백이 따라잡기 한도
  (`live_catchup_max_hours`, 기본 48h)를 넘거나 스냅샷이 없거나/복원 불가일 때만 남는다
  (조용한 재구성 금지 — 한도 초과는 명시적으로 초기화 + 로그). 만료 기한을 이미 넘긴
  복원 불가 주문은 예전처럼 정상 만료(`cancelled_expired`)로 먼저 정리한다
  (`expire_stale_pending`, WAN-298). 가동 중에는 벽시계 백스톱
  (`ZoneLimitLiveEngine.expire_overdue`)이 같은 기한을 매 폴링 집행한다.
* **중단 구간은 데이터로 남는다** — 세션 행 사이의 틈이 곧 다운타임이다.

## 따라잡기 재생과 창 공급 순서 (WAN-306)

`poll_series`는 소비할 1분봉마다 「그 봉이 닫힌 시점에 확정돼 있던 상위TF 창」을
`on_htf_bars`에 먼저 먹인다 — 정상 운영(폴링당 1분봉 ~1개)에서는 오늘까지의 동작과
같고, 공백 재생처럼 여러 상위TF 봉을 한 번에 소비할 때만 창이 봉 경계마다 전진해
연속 가동과 같은 호출 순서를 재현한다(재시작 불변 테스트가 동작으로 고정).

페이퍼 한정: 실주문 API는 부르지 않는다(`build_execution_engine`이 `live_trading=false`에서
`PaperBroker`를 보장). `ALPHABLOCK_LIVE_TRADING` 기본값 `false` 불변.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from backtest.sweep import timeframe_to_ms
from common.timefmt import format_kst_zoned
from config.settings import Settings
from data.freshness import format_window_gaps, window_gap_summary
from data.gaps import Gap, find_gaps
from data.storage import OhlcvStore
from data.universe import ARM_TIMEFRAMES
from data.universe_pool import UniverseStore
from execution.engine import REJECT_CODE_CELL_BUSY, EntryIntent, ExecutionOutcome
from live.arm_rules import (
    ARM_REJECT_CODES,
    REJECT_CODE_ARM_STOCH,
    REJECT_CODE_ARM_STOCH_UNKNOWN,
    REJECT_CODE_ARM_STOP_FLOOR,
    ArmDecision,
    ArmParams,
    btc_lookback_return,
    crowd_decision,
    evaluate_arm_fill,
    is_time_exit_minute,
    rotation_decision,
)
from live.arm_signals import ArmSignal, ArmSignalStore
from live.engine_state import EngineStateStore, SeriesStateSnapshot
from live.executor import PaperExecutor, TradeReport
from live.limit_engine import EngineEvent, ZoneLimitLiveEngine
from live.limit_orders import LimitFill, LimitOrderStatus, expiry_deadline_ms
from live.order_journal import SKIP_REASON_ZONE_WIDTH, OrderJournal
from live.paper import PaperPosition
from live.runtime_state import DataGapSkip, PendingOrderSnapshot, RuntimeStateStore
from live.tsmom_runner import TsmomRebalancer
from live.zone_limit_notifier import ZoneLimitNotifier
from paper.tsmom_ledger import TsmomLedgerStore
from strategy import stoch, tsmom
from strategy.models import ConfluenceParams, OrderBlockDirection, SignalExitReason

if TYPE_CHECKING:  # 무거운 임포트를 런타임 경로에 올리지 않는다(진입점이 지연 임포트한다).
    from data.funding import FundingRateStore
    from paper.store import PaperTradeRecorder, PaperTradeStore

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
Series = tuple[str, str]

_MINUTE_MS = 60_000

#: 공백 따라잡기 재생의 기본 한도(ms) — 설정 `live_catchup_max_hours`(기본 48h)가 정본이고
#: 이 상수는 러너를 직접 조립하는 테스트/도구의 기본값이다.
DEFAULT_CATCHUP_MAX_MS = 48 * 3_600_000
#: 팔 %K를 계산할 확정봉 꼬리 길이 — %K 선이 의존하는 29봉 + 여유(WAN-451).
_ARM_K_BARS = 80
#: 팔 폭락 판정의 기준 종목(정본 `wan439` BTC 선물).
_ARM_BTC_SYMBOL = "BTC/USDT:USDT"


class ZoneLimitPaperRunner:
    """저장소를 폴링해 존-지정가 주문을 상시 관리하는 페이퍼 러너."""

    def __init__(
        self,
        *,
        store: OhlcvStore,
        engine: ZoneLimitLiveEngine,
        journal: OrderJournal,
        session_id: int,
        executor: PaperExecutor,
        params: ConfluenceParams,
        series: list[Series],
        lookback_bars: int,
        poll_interval_seconds: float,
        runtime_state: RuntimeStateStore | None = None,
        notifier: ZoneLimitNotifier | None = None,
        engine_state: EngineStateStore | None = None,
        catchup_max_ms: int = DEFAULT_CATCHUP_MAX_MS,
        tsmom: TsmomRebalancer | None = None,
        arm: ArmParams | None = None,
        universe: UniverseStore | None = None,
        signals: ArmSignalStore | None = None,
        bootstrap_days: int = 0,
        reuse_loads: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        if arm is None and (series or universe is not None):
            # 옛 오더블록 페이퍼(칸당 대기 주문 · 고정 R 익절 · 재진입)는 WAN-451에서 없어졌다 —
            # 팔 규칙 없이 체결을 진입으로 바꾸는 경로가 이제 없다.
            raise ValueError(
                "오더블록 시리즈는 스토캐스틱 팔 규칙(arm)이 있어야 돈다 — 옛 오더블록 페이퍼는"
                " WAN-451에서 없어졌다"
            )
        self._store = store
        self._engine = engine
        self._journal = journal
        self._session_id = session_id
        self._executor = executor
        self._params = params
        self._series = series
        self._lookback_bars = lookback_bars
        self._poll_interval = poll_interval_seconds
        self._runtime_state = runtime_state
        #: 재시작 영속화 저장소(WAN-306). None이면 저장/복원 없음(옛 동작 그대로).
        self._engine_state = engine_state
        self._catchup_max_ms = catchup_max_ms
        #: 엔진 C 일 리밸런싱(WAN-451 ⑥). None이면 엔진 C를 안 돌린다.
        self._tsmom = tsmom
        #: 스토캐스틱 팔 진입·청산 규칙(WAN-451 ③). 오더블록 시리즈가 있으면 반드시 있다(위
        #: 가드). None은 엔진 C 전용 러너(시리즈 0개)뿐이다.
        self._arm = arm
        #: 팔의 주간 종목 목록(WAN-451 ③). 주어지면 감시 시리즈를 매 폴링 그 주 목록 × 팔 TF로
        #: 맞추고, 체결 순간 목록 밖 종목은 진입을 거부한다. 운영 진입점(`run_zone_limit_runner`)은
        #: 언제나 준다 — None은 목록 게이트를 빼고 한 규칙만 재는 단위 테스트·재생 대조용이다.
        self._universe = universe
        #: 팔 신호 장부(WAN-451 몰림 규칙). 주어지면 체결마다 신호를 기록하고, 진입 판정 때 직전
        #: 24h 신호 수로 몰림 규칙을 건다. 운영 진입점은 언제나 준다 — None은 몰림 규칙을 빼는
        #: 단위 테스트·재생 대조용이다.
        self._signals = signals
        #: 오래된 존 기억의 시작(WAN-451): 시리즈를 **처음** 먹일 때만 이 일수만큼의 확정봉으로
        #: 탐지해 몇 년 된 존까지 세운다(그 뒤는 `lookback_bars` 창 + 엔진 기억). 0이면 예전 그대로.
        self._bootstrap_days = bootstrap_days
        self._notifier = notifier
        self._sleep = sleep
        self._now_ms = now_ms
        #: 시리즈별 마지막으로 반영한 확정 상위TF 봉 시각.
        self._last_htf: dict[Series, int] = {}
        #: 시리즈별 마지막으로 소비한 1분봉 시각(그 이후만 새로 읽는다).
        self._last_substep: dict[Series, int] = {}
        #: 읽기 재사용(WAN-451 ④ — 팔 310 시리즈에서 매분 폴링 50초의 59%가 같은 상위TF 창
        #: 재로드였다). False면 매번 저장소에서 다시 읽는다(옛 동작 — 동등성 테스트용).
        self._reuse_loads = reuse_loads
        #: 시리즈 → (창 지문, 마지막으로 읽은 상위TF 꼬리). 지문이 같으면 다시 안 읽는다.
        self._htf_tail_cache: dict[Series, tuple[tuple[object, ...], pd.DataFrame]] = {}
        #: 이번 폴링의 심볼별 1분봉 — (읽기 시작 ms, 확정 1분봉). 폴링마다 비운다.
        self._minute_cache: dict[str, tuple[int, pd.DataFrame]] = {}
        #: 시리즈별 결측 건너뜀 기록(WAN-314) — 상태 파일에서 이어받아 재시작을 넘어
        #: 보존한다(해소된 기록도 남는다: 나중에 성적을 읽을 때 그 구간의 빈칸이
        #: 「기회 없음」이 아니라 「데이터 결측」이었음을 알 수 있어야 한다).
        self._gap_skips: dict[Series, DataGapSkip] = {}
        #: 시리즈별 지금 소비 중인 1분봉 (open_time, 저가, 고가) — 같은 1분 가격 순서 판정용.
        self._minute_bar: dict[Series, tuple[int, float, float]] = {}
        if runtime_state is not None:
            for skip in runtime_state.load().data_gap_skips:
                self._gap_skips[(skip.symbol, skip.timeframe)] = skip

    # -- 폴링 ---------------------------------------------------------------

    def poll_series(self, symbol: str, timeframe: str) -> list[EngineEvent]:
        """한 시리즈를 한 번 폴링한다: 확정 상위TF 봉 반영 → 새 1분봉을 서브스텝으로 소비.

        상위TF 창은 1분봉마다 「그 봉이 닫힌 시점에 확정돼 있던 창」을 먹인다(WAN-306,
        모듈 독스트링 §따라잡기) — 정상 운영(새 1분봉 ~1개)에서는 예전과 같은 한 번의
        갱신이고, 공백 재생처럼 여러 상위TF 봉을 한 번에 소비할 때만 창이 경계마다
        전진해 연속 가동과 같은 호출 순서를 재현한다.
        """
        events: list[EngineEvent] = []
        htf_ms = self._store_htf_ms(timeframe)
        key = (symbol, timeframe)
        since = self._last_substep.get(key)

        df = self._load_htf_tail(symbol, timeframe, since=since, htf_ms=htf_ms)
        if df.empty:
            return []
        closed_all = df[df["closed"].astype(bool)]
        if closed_all.empty:
            return []
        last_closed_time = int(closed_all["open_time"].iloc[-1])

        # 1분봉 서브스텝. 첫 관측(프라이밍)은 현재 형성 중인 상위TF 봉의 시작부터다 —
        # 과거를 재생하면 이미 지나간 탭에 뒷북 주문을 걸게 된다(측정 오염). 복원된
        # 시리즈는 `restore_state`가 심은 커서부터 이어져 공백 구간을 따라잡는다(WAN-306).
        if since is None:
            since = last_closed_time + htf_ms - _MINUTE_MS
        df_1m = self._minute_bars(symbol, since + 1)
        # 열 배열을 직접 돈다(WAN-451 ④ — `itertuples`는 시리즈마다 반복자 생성 비용이 크다).
        # 값은 같다: 아래에서 `int`/`float`로 바꾸는 것도 예전 그대로다.
        for t_raw, low_raw, high_raw, close_raw in zip(
            df_1m["open_time"].to_numpy(),
            df_1m["low"].to_numpy(),
            df_1m["high"].to_numpy(),
            df_1m["close"].to_numpy(),
            strict=True,
        ):
            t = int(t_raw)
            # 이 1분봉이 **시작하기 전에**(t) 닫힌 상위TF 봉만 창에 담는다(WAN-451). 예전에는
            # 「이 1분봉이 닫힌 시각(t+1분)까지」였는데, 그러면 상위TF 봉의 **마지막 1분**을
            # 판정하기 전에 그 봉을 이미 닫힌 봉으로 먹여 — 그 봉에서 존이 깨지면 마지막 1분의
            # 체결 기회를 지웠다(정본 `invalidation_cancel="bar_close"`는 그 봉 끝까지 주문을
            # 살린다 · 실데이터 대조에서 백테스트에만 있는 체결로 드러났다). 그 봉은 폴링 끝의
            # 「최신 창」 공급이나 다음 1분봉에서 먹는다 — 연속 가동 ≡ 재생은 그대로다.
            fed = self._feed_htf_window(symbol, timeframe, closed_all, cutoff_open_time=t - htf_ms)
            if fed is None:
                return events  # 창 불연속 — 이 시리즈 평가 중단(기존 거부와 같은 처리).
            if fed:
                self._handle_events(fed)
                events += fed
            low, high, close = float(low_raw), float(high_raw), float(close_raw)
            # 같은 1분 가격 순서(WAN-451)가 이 1분의 범위를 읽는다(`_stop_first`).
            self._minute_bar[key] = (t, low, high)
            step_events = self._engine.on_substep(
                symbol, timeframe, time_ms=t, low=low, high=high, close=close
            )
            self._handle_events(step_events)
            events += step_events
            self._check_exits(symbol, timeframe, low=low, high=high, close=close, time_ms=t)
            self._last_substep[key] = t
            # 서브스텝마다 저장한다 — 성기게 저장하면 크래시 후 따라잡기가 이미 집행한
            # 구간을 다시 집행해 장부·거래가 중복된다(`live.engine_state`).
            self._save_state(symbol, timeframe)
        # 1분봉이 상위TF 저장보다 뒤처져도 존 대장·무효화 취소는 최신 창으로 유지한다
        # (예전의 「폴링 서두 on_htf_bars」와 같은 역할 — 창은 앞으로만 간다).
        fed = self._feed_htf_window(symbol, timeframe, closed_all, cutoff_open_time=None)
        if fed:
            self._handle_events(fed)
            events += fed
        return events

    def _load_htf_tail(
        self, symbol: str, timeframe: str, *, since: int | None, htf_ms: int
    ) -> pd.DataFrame:
        """이번 폴링이 실제로 쓸 확정봉 꼬리만 로드한다(WAN-313 — 전체 이력 로드 금지).

        예전에는 매 폴링 `store.load(symbol, timeframe)`로 6년 전체를 읽었다 — 15m
        21만 행, 파생 2h는 1h 전체 로드 + 리샘플까지 얹혀 48시리즈 한 바퀴가 15분봉
        주기를 넘겼다(시리즈당 실측은 wan313.md §0). 창은 `_feed_htf_window`가
        `closed` 필터 → `open_time <= cutoff` → `tail(lookback)`로 만들므로, 이번
        폴링의 **가장 이른 cutoff** 이하에서 확정봉 `lookback`개를 덮는 정확한
        앵커(`tail_window_start`)부터 로드하면 창 내용이 전체 로드와 글자 그대로 같다.

        가장 이른 cutoff: 프라이밍(since=None)은 최신 확정봉 이후만 보므로 상한이
        필요 없고, 이어 소비하는 경우 첫 1분봉 t는 `since + 1` 이상이라 cutoff
        (= t − htf_ms, WAN-451)가 `since + 1 − htf_ms`보다 이를 수 없다.
        """
        end_ms = None if since is None else since + 1 - htf_ms
        bars = self._window_bars(symbol, timeframe)
        start = self._store.tail_window_start(symbol, timeframe, bars=bars, end_ms=end_ms)
        if bars > self._lookback_bars:
            # 처음 한 번의 긴 창(오래된 존 기억 시작) — 캐시에 남기지 않는다(310 시리즈의 2년치가
            # 동시에 메모리에 남으면 서버 RAM을 넘는다). 다음 폴링부터는 짧은 창이다.
            if start is None:
                return self._store.load(symbol, timeframe)
            return self._store.load(symbol, timeframe, start_ms=start)
        if not self._reuse_loads:
            if start is None:
                return self._store.load(symbol, timeframe)
            return self._store.load(symbol, timeframe, start_ms=start)
        # 재사용(WAN-451 ④): 창 시작 · 그 뒤 저장 행의 (최신 시각 · 개수 · 확정 수)가 지난번과
        # 같으면 저장 내용도 같다 — 다시 읽지 않는다. 같은 시각 행의 **값만** 바뀐 경우는 이
        # 지문이 못 잡지만, 엔진에 창을 먹이는 것은 새 봉이 들어올 때(= 최신 시각이 바뀔 때)
        # 뿐이라 그때 다시 읽고 결과가 같다(`_feed_htf_window`가 마지막 봉이 안 바뀌면 안
        # 먹인다). 동등성은 `reuse_loads=False` 대조 테스트가 동작으로 고정한다.
        key = (symbol, timeframe)
        fingerprint = (start, *self._store.window_fingerprint(symbol, timeframe, start_ms=start))
        cached = self._htf_tail_cache.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        if start is None:
            df = self._store.load(symbol, timeframe)
        else:
            df = self._store.load(symbol, timeframe, start_ms=start)
        self._htf_tail_cache[key] = (fingerprint, df)
        return df

    def _minute_bars(self, symbol: str, start_ms: int) -> pd.DataFrame:
        """`start_ms` 이후 확정 1분봉 — 한 폴링 안에서는 **심볼당 한 번만** 읽는다(WAN-451 ④).

        한 종목을 여러 TF가 감시하면(팔은 10개) 예전에는 같은 1분봉을 TF마다 다시 읽었다.
        이번 폴링에 이미 더 이른 시각부터 읽어 둔 게 있으면 잘라 쓴다. 폴링 사이에는
        `poll_once`가 비운다. ⚠️ 그래서 한 폴링 안의 시리즈들은 **같은 스냅샷**을 본다(옛
        동작은 폴링 도중 수집기가 넣은 봉을 뒤 시리즈만 볼 수 있었다 — 다음 폴링에 반영된다).
        """
        if not self._reuse_loads:
            return self._load_minutes(symbol, start_ms)
        cached = self._minute_cache.get(symbol)
        if cached is None or cached[0] > start_ms:
            cached = (start_ms, self._load_minutes(symbol, start_ms))
            self._minute_cache[symbol] = cached
        frame = cached[1]
        if cached[0] == start_ms:
            return frame
        return frame[frame["open_time"] >= start_ms]

    def _load_minutes(self, symbol: str, start_ms: int) -> pd.DataFrame:
        df_1m = self._store.load(symbol, "1m", start_ms=start_ms)
        if "closed" in df_1m.columns and not df_1m.empty:
            df_1m = df_1m[df_1m["closed"].astype(bool)]
        return df_1m

    def _feed_htf_window(
        self,
        symbol: str,
        timeframe: str,
        closed_all: pd.DataFrame,
        *,
        cutoff_open_time: int | None,
    ) -> list[EngineEvent] | None:
        """`cutoff_open_time` 이하의 확정 상위TF 봉 창을 엔진에 먹인다(None = 전체).

        창은 **앞으로만** 간다 — 이미 먹인 마지막 봉보다 새 봉이 없으면 아무것도 하지
        않는다. 창에 구멍이 있으면(볼린저·RSI가 조용히 틀린 값을 내는 조건, WAN-156 §3)
        로그를 남기고 None을 반환해 호출부가 시리즈 평가를 중단하게 한다.
        """
        key = (symbol, timeframe)
        prev = self._last_htf.get(key)
        if self._reuse_loads:
            # 먼저 「창의 마지막 봉」만 이진 탐색으로 본다(WAN-451 ④) — 정상 운영에서는 매분
            # 「새 봉 없음」으로 끝나는데, 그걸 알려고 1500행 창을 매번 걸러 만들 필요가 없다.
            # 아래 옛 경로와 같은 조건(창이 비었나 · 마지막 봉이 이미 먹인 봉 이하인가)이다.
            times = closed_all["open_time"].to_numpy()
            end = (
                len(times)
                if cutoff_open_time is None
                else int(np.searchsorted(times, cutoff_open_time, side="right"))
            )
            if end == 0:
                return []
            if prev is not None and int(times[end - 1]) <= prev:
                return []
        window = closed_all
        if cutoff_open_time is not None:
            window = closed_all[closed_all["open_time"] <= cutoff_open_time]
        window = window.tail(self._window_bars(symbol, timeframe))
        if window.empty:
            return []
        last_time = int(window["open_time"].iloc[-1])
        if prev is not None and last_time <= prev:
            return []
        gaps = find_gaps([int(t) for t in window["open_time"].tolist()], timeframe)
        if gaps:
            summary = format_window_gaps(gaps)
            _logger.error(
                "%s %s: %s — 평가를 건너뜁니다. `alphablock backfill`로 갭을 메우세요.",
                symbol,
                timeframe,
                summary,
            )
            # 건너뜀을 상태로도 남긴다(WAN-314) — ERROR 로그 한 줄만으로는 나중에
            # 성적표에서 이 공백이 보이지 않는다.
            self._note_gap_skip(symbol, timeframe, gaps, summary)
            return None
        self._resolve_gap_skip(key)
        events = self._engine.on_htf_bars(symbol, timeframe, window)
        self._last_htf[key] = last_time
        return events

    def _window_bars(self, symbol: str, timeframe: str) -> int:
        """이 시리즈에 먹일 창 길이 — 처음이면 오래된 존 기억 시작 창, 아니면 `lookback_bars`."""
        if self._bootstrap_days <= 0 or (symbol, timeframe) in self._last_htf:
            return self._lookback_bars
        days_bars = -(-self._bootstrap_days * 86_400_000 // timeframe_to_ms(timeframe))
        return max(self._lookback_bars, days_bars)

    def _note_gap_skip(self, symbol: str, timeframe: str, gaps: list[Gap], summary: str) -> None:
        """결측 건너뜀을 기록한다 — 같은 구멍이면 횟수·마지막 관측만 갱신(WAN-314)."""
        key: Series = (symbol, timeframe)
        now = self._now_ms()
        prev = self._gap_skips.get(key)
        first = gaps[0]
        if prev is not None and prev.resolved_ms is None and prev.gap_start_ms == first.start_ms:
            self._gap_skips[key] = prev.model_copy(
                update={
                    "summary": summary,
                    "gap_end_ms": first.end_ms,
                    "last_seen_ms": now,
                    "skip_count": prev.skip_count + 1,
                }
            )
            return
        self._gap_skips[key] = DataGapSkip(
            symbol=symbol,
            timeframe=timeframe,
            summary=summary,
            gap_start_ms=first.start_ms,
            gap_end_ms=first.end_ms,
            first_seen_ms=now,
            last_seen_ms=now,
        )

    def _resolve_gap_skip(self, key: Series) -> None:
        """구멍이 사라져 평가가 재개되면 기록을 해소로 표시한다(지우지 않는다)."""
        prev = self._gap_skips.get(key)
        if prev is not None and prev.resolved_ms is None:
            self._gap_skips[key] = prev.model_copy(update={"resolved_ms": self._now_ms()})

    def poll_once(self) -> list[EngineEvent]:
        """모든 시리즈를 한 번씩 폴링하고 하트비트·상태 스냅샷을 남긴다."""
        cycle_started = self._now_ms()
        emitted: list[EngineEvent] = []
        if self._universe is not None:
            self.sync_series(cycle_started)
        self._minute_cache.clear()  # 1분봉 재사용은 한 폴링 안에서만(WAN-451 ④)
        for symbol, timeframe in self._series:
            try:
                emitted += self.poll_series(symbol, timeframe)
            except Exception:  # noqa: BLE001 — 한 시리즈 오류가 루프 전체를 멈추지 않도록.
                _logger.exception("시리즈 폴링 실패: %s %s", symbol, timeframe)
            # 시리즈 단위 생존 신호(WAN-313) — 하트비트가 「한 바퀴 완주」에 묶여 있으면
            # 시리즈 수가 늘 때마다 간격이 길어져 「멈춤」 판정이 상시 오작동한다. 완주
            # 여부는 아래 record의 별도 지표(last_cycle_completed_at)가 담당한다.
            if self._runtime_state is not None:
                self._runtime_state.touch(self._now_ms())
        now = self._now_ms()
        # 벽시계 만료 백스톱(WAN-298): 수집이 멈춰 서브스텝이 오지 않아도 지정가 만료
        # (`limit_valid_bars`, 24봉 WAN-222)를 집행한다 — 만료 이벤트는 1분봉 경로와 같은
        # `_handle_events`로 기록·알림한다.
        overdue = self._engine.expire_overdue(now_ms=now)
        if overdue:
            self._handle_events(overdue)
            emitted += overdue
        # 벽시계 만료는 서브스텝 밖에서 상태를 바꾼다 — 폴링 끝에 한 번 더
        # 저장해 스냅샷이 그 변화를 놓치지 않게 한다(WAN-306).
        if self._engine_state is not None:
            for symbol, timeframe in self._series:
                self._save_state(symbol, timeframe)
        if self._tsmom is not None:
            try:
                self._tsmom.tick(now)
            except Exception:  # noqa: BLE001 — 엔진 C 오류가 팔 루프를 멈추지 않도록.
                _logger.exception("엔진 C 리밸런싱 실패(WAN-451)")
        self._journal.heartbeat(self._session_id, now_ms=now)
        if self._notifier is not None:
            # KST 날짜가 바뀌면 전날 예약·체결·만료 일일 요약을 보낸다(만료 실시간 대체).
            self._notifier.tick(self._executor.equity, now_ms=now)
        if self._runtime_state is not None:
            self._runtime_state.record(
                now_ms=now,
                open_positions=self._paper_positions(),
                new_events=[],
                pending_orders=self._pending_snapshots(),
                # 완주 지표(WAN-313): 하트비트만으로는 「돌고는 있는데 한 바퀴를 못
                # 끝내는」 상태가 초록불이 된다 — 완주 시각·소요를 따로 남겨 Health가
                # 최단 TF 주기 예산과 비교한다.
                cycle_completed_ms=now,
                cycle_duration_ms=max(0, now - cycle_started),
                # 결측 건너뜀 기록(WAN-314) — 로그 밖(상태 파일·Health 탭)에서도 보인다.
                data_gap_skips=sorted(
                    self._gap_skips.values(), key=lambda s: (s.symbol, s.timeframe)
                ),
            )
        return emitted

    # -- 감시 시리즈 교체 (WAN-451 ③) -----------------------------------------

    def desired_series(self, now_ms: int) -> list[Series]:
        """지금 감시해야 할 시리즈 = 그 주 목록 × 팔 TF + 포지션·대기 주문이 남은 칸.

        목록에서 빠진 종목도 포지션(손절·시간 청산)이나 대기 주문(체결되면 목록 게이트가 거부한다)이
        끝날 때까지는 계속 본다 — 정본 `force_close=False`. 그 주 목록이 아직 저장 전이면 가장 최근
        목록으로 감시한다(진입은 `rotation_decision`이 막는다 — 감시와 진입 판정을 섞지 않는다).
        """
        assert self._universe is not None
        snap = self._universe.at(now_ms)
        members = sorted(snap.members) if snap is not None else []
        keep: set[Series] = {(s, tf) for s in members for tf in ARM_TIMEFRAMES}
        keep |= {(p.symbol, p.timeframe) for p in self._executor.open_positions}
        keep |= {(o.symbol, o.timeframe) for o in self._engine.open_orders()}
        return sorted(keep)

    def sync_series(self, now_ms: int) -> tuple[list[Series], list[Series]]:
        """감시 시리즈를 `desired_series`로 맞춘다 → (추가, 제거). 바뀐 게 없으면 둘 다 빈 목록."""
        want = self.desired_series(now_ms)
        current = set(self._series)
        added = [k for k in want if k not in current]
        removed = [k for k in self._series if k not in set(want)]
        if not added and not removed:
            return [], []
        kept_removed: list[Series] = []
        for key in removed:
            if not self._engine.drop_series(*key):
                kept_removed.append(key)  # 대기 주문이 걸려 있다 — 다음 폴링에 다시 본다
                continue
            self._last_htf.pop(key, None)
            self._last_substep.pop(key, None)
            self._htf_tail_cache.pop(key, None)
            if self._engine_state is not None:
                self._engine_state.delete(*key)
        removed = [k for k in removed if k not in kept_removed]
        self._series = sorted((set(self._series) - set(removed)) | set(added))
        _logger.info(
            "감시 시리즈 교체(WAN-451): +%d · −%d → %d개 (%d종목)",
            len(added),
            len(removed),
            len(self._series),
            len({sym for sym, _tf in self._series}),
        )
        return added, removed

    def _rotation_gate(self, symbol: str, entry_ms: int) -> ArmDecision | None:
        """체결 순간 목록 게이트 — 목록이 없으면(목록 게이트를 뺀 테스트·재생 대조) None."""
        if self._universe is None or self._arm is None:
            return None
        snap = self._universe.at(entry_ms)
        if snap is None:
            return rotation_decision(None, frozenset(), symbol, entry_ms)
        return rotation_decision(snap.rebalance_ms, snap.members, symbol, entry_ms)

    def run(self, *, max_polls: int | None = None) -> None:
        """폴링 루프. `max_polls`가 None이면 무한 반복(테스트에서는 유한 지정)."""
        poll = 0
        while max_polls is None or poll < max_polls:
            self.poll_once()
            poll += 1
            if max_polls is not None and poll >= max_polls:
                break
            self._sleep(self._poll_interval)

    # -- 집행·청산 -----------------------------------------------------------

    def _handle_events(self, events: list[EngineEvent]) -> None:
        for event in events:
            if event.kind == "placed":
                order = event.order
                if self._notifier is not None:
                    self._notifier.note_placed()  # 예약은 실시간 알림 없음 — 일일 요약에만.
                # 시각은 KST로 찍는다(WAN-172) — 로그·텔레그램·`status`가 같은 자다.
                # 판정·저장에 쓰는 값은 UTC epoch ms 그대로다.
                _logger.info(
                    "지정가 예약: %s %s %s %s 지정가=%s 손절=%.6g (탭 #%d)",
                    event.symbol,
                    event.timeframe,
                    order.direction.value,
                    format_kst_zoned(order.placed_ms),
                    "봉내 재산정" if order.live_limit is not None else f"{order.limit_price:.6g}",
                    order.stop_price,
                    order.tap_index,
                )
            elif event.kind == "filled" and event.fill is not None:
                # 탭마다 독립 주문의 체결 — 몰림 규칙의 신호로 남기고, 같은 1분 가격 순서대로
                # 앞 포지션 손절을 먼저 정산한 뒤, 칸에 포지션이 없으면 진입 판정을 탄다(정본
                # 북과 같은 규칙, WAN-451).
                self._record_signal(event)
                self._stop_first(event.fill)
                self._handle_fill(event)
            else:
                if self._notifier is not None and event.kind == "cancelled_expired":
                    self._notifier.note_expired()  # 만료는 실시간 없음 — 일일 요약에만.
                    # 밴드가 한 번도 유리하지 않아 주문판에 실린 적 없는 만료 = 볼린저 규칙 3
                    # 기각(deviation). 옵트인 건별 알림에만 넘긴다(no_fill은 넘기지 않는다 —
                    # WAN-221: 걸렸다 안 닿은 순수 미체결은 건별로 보내지 않는다).
                    if event.order.first_rested_ms is None:
                        self._notifier.note_filter_skip(
                            "deviation",
                            symbol=event.symbol,
                            timeframe=event.timeframe,
                            time_ms=event.time_ms,
                        )
                _logger.info(
                    "지정가 주문 종결: %s %s %s", event.symbol, event.timeframe, event.kind
                )

    def _handle_fill(self, event: EngineEvent) -> None:
        """체결 이벤트를 집행·처분 기록·알림한다 — 셋 중 하나가 끊겨도 나머지를 유실하지 않는다.

        체결(장부 `filled` 기록됨) 직후 진입을 집행하는데, WAN-207 이전에는 세 가지가
        연쇄로 묶여 하나만 어긋나도 **처분 기록과 진입 알림이 함께 조용히 유실**됐다:

        * ⚠️ **근본 원인(WAN-207)**: 처분 기록이 `order.journal_id`를 읽었는데 `order`는
          위쪽 `placed` 분기에서만 대입되는 지역 변수였다. 체결이 예약과 **다른 폴링
          배치**에서 나면(주문이 걸린 뒤 몇 분 후 가격이 닿는 흔한 경우) 이 배치엔
          `placed`가 없어 `order`가 **미대입** → `UnboundLocalError`가 `enter()`로
          포지션을 연 **직후** 터졌다. 그래서 `entry_status`는 NULL로 남고(WAN-194가
          "쓰기 사이의 유실"로 부르던 그 모양) 진입 텔레그램도 건너뛰어졌는데, 러너는
          죽지 않고(폴링 루프의 시리즈별 `except`가 삼킴) 이후 청산은 정상 처리돼
          "진입 알림 없이 익절 알림만 온" 거래가 됐다. 이제 체결 이벤트가 실어 온
          `event.order`(= 그 주문 자신)를 쓴다.
        * **belt-and-suspenders**: 처분은 `try/finally`로 **반드시** 남기고(예외가 나면
          최소 사유를 붙여 `rejected`로), 알림 실패는 삼키지 않고 로그로 드러낸다. 그래야
          `filled` + `entry_status IS NULL`이 도입 이후에는 진짜 "쓰기 사이 유실"만
          가리키고(WAN-194의 고아 판정), 진입 알림 누락이 조용히 넘어가지 않는다.
        """
        fill = event.fill
        assert fill is not None
        journal_id = event.order.journal_id
        report = None
        disposition_done = False
        try:
            # 목록 게이트가 먼저다(정본은 목록 밖 후보를 처음부터 없는 것으로 친다, WAN-444).
            decision = self._rotation_gate(fill.symbol, fill.time) or self._arm_decision(
                fill.symbol, fill.timeframe, fill.price, fill.stop_price, fill.time
            )
            if decision.accepted:
                decision = self._crowd_gate(event, decision)
            if not decision.accepted:
                # 팔 규칙 거부(WAN-451 ③) — 체결은 됐지만 진입하지 않는다. 다른 거부와 같은 길로
                # 장부·알림에 남긴다(조용히 버리지 않는다, WAN-194).
                report = TradeReport(
                    outcome=ExecutionOutcome.rejected(decision.reason, decision.reason_code),
                    open_positions=self._executor.open_positions,
                    equity=self._executor.equity,
                )
            else:
                report = self._executor.enter(
                    EntryIntent(
                        symbol=fill.symbol,
                        timeframe=fill.timeframe,
                        direction=fill.direction,
                        entry_price=fill.price,
                        entry_time=fill.time,
                        stop_price=decision.stop_price,
                        # 팔은 익절이 없다(시간 청산 · 손절만, WAN-436).
                        take_profit_price=None,
                        risk_scale=decision.size_scale,
                    ),
                    now_ms=fill.time,
                )
            if journal_id is not None:
                self._journal.record_entry_result(
                    journal_id,
                    entered=report.accepted,
                    reason=report.outcome.reason,
                    reason_code=report.outcome.reason_code,
                    size_scale=decision.size_scale if decision is not None else None,
                )
            disposition_done = True
            # 거부는 WARNING이다 — 체결이 거래가 되지 않은 것이라 조용히 흘리면
            # 장부와 성과가 어긋난 채로 운영이 계속된다.
            log = _logger.info if report.accepted else _logger.warning
            log(
                "지정가 체결: %s %s %s @%.6g 관통=%.2fbp 대기=%s → 페이퍼 진입 %s",
                fill.symbol,
                fill.timeframe,
                format_kst_zoned(fill.time),
                fill.price,
                fill.penetration_bps,
                f"{fill.waited_ms}ms" if fill.waited_ms is not None else "?",
                "성공" if report.accepted else f"거부({report.outcome.reason})",
            )
            if self._notifier is not None:
                try:
                    self._notifier.handle_fill(fill, report)
                except Exception:  # noqa: BLE001 — 알림 실패가 처분·루프를 막지 않도록(로그로 드러낸다).
                    _logger.exception("진입 알림 실패: %s %s", fill.symbol, fill.timeframe)
        finally:
            if not disposition_done and journal_id is not None:
                # 진입 처리가 예외로 끊겼다 — 처분을 조용히 유실하지 않도록 남긴다(WAN-207).
                # 포지션이 실제로 열렸으면(enter 성공 후 기록 단계 실패) entered로, 그 전에
                # 끊겼으면 사유를 붙여 rejected로 남긴다. NULL로 두는 것보다 항상 낫다.
                entered = report is not None and report.accepted
                reason = (
                    ""
                    if entered
                    else (
                        report.outcome.reason
                        if report is not None
                        else "진입 처리 중 예외(처분 유실 방지, WAN-207)"
                    )
                )
                reason_code = report.outcome.reason_code if report is not None else ""
                try:
                    self._journal.record_entry_result(
                        journal_id, entered=entered, reason=reason, reason_code=reason_code
                    )
                except Exception:  # noqa: BLE001 — 폴백 처분마저 실패하면 로그만 남긴다.
                    _logger.exception("처분 기록 폴백 실패: journal_id=%d", journal_id)

    def _stop_first(self, fill: LimitFill) -> None:
        """같은 1분 가격 순서(WAN-451 · 정본 `w436.ADOPTED_SAME_MINUTE`).

        칸의 포지션이 이 1분에 손절되고 새 체결가가 그 손절가 **이하**(숏은 이상)면 — 가격이
        손절가를 먼저 지나 더 가서 체결된 것이라 손절이 먼저다. 그때만 손절을 먼저 정산해 칸을
        비운다. 그 밖에는 체결이 먼저라 포지션이 열린 채로 판정한다(`cell_busy`). 시간 청산은 그
        1분 종가라 언제나 체결보다 뒤다(아래 `_check_exits`가 그대로 처리한다).
        """
        bar = self._minute_bar.get((fill.symbol, fill.timeframe))
        if bar is None or bar[0] != fill.time:
            return
        position = next(
            (
                p
                for p in self._executor.open_positions
                if p.symbol == fill.symbol and p.timeframe == fill.timeframe
            ),
            None,
        )
        if position is None or position.stop_price is None:
            return
        stop = position.stop_price
        _t, low, high = bar
        if position.direction is OrderBlockDirection.BULLISH:
            first = low <= stop and fill.price <= stop
        else:
            first = high >= stop and fill.price >= stop
        if first:
            self._exit(fill.symbol, fill.timeframe, stop, fill.time, SignalExitReason.STOP_LOSS)

    def _record_signal(self, event: EngineEvent) -> None:
        """체결 하나를 신호 장부에 남긴다 — 몰림 규칙이 세는 모집단인지 함께 적는다."""
        if self._signals is None or event.fill is None or event.zone is None:
            return
        fill = event.fill
        reason = self._population_reason(
            fill.symbol, fill.timeframe, fill.price, fill.stop_price, fill.time
        )
        self._signals.record(
            ArmSignal(
                symbol=fill.symbol,
                timeframe=fill.timeframe,
                zone_start=int(event.zone[1]),
                zone_confirmed=int(event.zone[2]),
                fill_ms=fill.time,
                price=fill.price,
                ob_stop=float(fill.stop_price) if fill.stop_price is not None else 0.0,
                counted=reason == "",
                reason_code=reason,
            )
        )

    def _population_reason(
        self, symbol: str, timeframe: str, price: float, ob_stop: float | None, fill_ms: int
    ) -> str:
        """신호 모집단에서 빠지는 이유(들면 빈 문자열) — 목록 안 · 손절폭 ≥ 하한 · %K < 문턱.

        정본 모집단(`wan436.build_entries` + `wan442.apply_rotation`)과 같은 셋이다. 손절 2배 ≤ 0
        거부와 폭락 크기는 모집단을 바꾸지 않는다(백테스트도 세는 쪽에서 안 뺐다).
        """
        assert self._arm is not None
        gate = self._rotation_gate(symbol, fill_ms)
        if gate is not None:
            return gate.reason_code
        if ob_stop is None or (price - ob_stop) / price < self._arm.min_stop_width:
            return REJECT_CODE_ARM_STOP_FLOOR
        times, ks = self._closed_k(symbol, timeframe, fill_ms)
        k = stoch.k_before_live(times, ks, fill_ms, timeframe_to_ms(timeframe))
        if k is None:
            return REJECT_CODE_ARM_STOCH_UNKNOWN
        if not k < self._arm.k_threshold:
            return REJECT_CODE_ARM_STOCH
        return ""

    def _crowd_gate(self, event: EngineEvent, decision: ArmDecision) -> ArmDecision:
        """몰림 규칙(WAN-436/438) — 건너뛰면 거부 판정, 아니면 크기 배율을 곱한 판정."""
        if self._signals is None or self._arm is None or event.fill is None:
            return decision
        fill = event.fill
        t = fill.time - fill.time % _MINUTE_MS  # 백테스트 후보 시각과 같은 자(그 1분봉 시작)
        exclude = (
            (fill.symbol, fill.timeframe, int(event.zone[1]), int(event.zone[2]))
            if event.zone is not None
            else None
        )
        count = self._signals.count(t - self._arm.crowd_window_ms, t, exclude=exclude)
        lookback = self._arm.crowd_btc_lookback_minutes
        btc = self._store.load(
            _ARM_BTC_SYMBOL, "1m", start_ms=t - (lookback + 10) * _MINUTE_MS, end_ms=t
        )
        if not btc.empty and "closed" in btc.columns:
            btc = btc[btc["closed"].astype(bool)]
        btc_4h = btc_lookback_return(
            [int(x) for x in btc["open_time"]], [float(c) for c in btc["close"]], t, lookback
        )
        crowd = crowd_decision(self._arm, count, btc_4h)
        if crowd.size == 0.0:
            return ArmDecision(
                False,
                crowd.reason_code,
                crowd.reason,
                stop_width=decision.stop_width,
                k_value=decision.k_value,
                btc_24h=decision.btc_24h,
            )
        _logger.info(
            "몰림 규칙 통과: %s %s 직전 24h 신호 %d개 · 크기 ×%.3f (폭락 ×%.1f)",
            fill.symbol,
            fill.timeframe,
            count,
            crowd.size,
            decision.size_scale,
        )
        return dataclasses.replace(decision, size_scale=decision.size_scale * crowd.size)

    def _closed_k(
        self, symbol: str, timeframe: str, entry_ms: int
    ) -> tuple[list[int], list[float | None]]:
        """그 칸 TF의 확정봉 꼬리 → (시각, %K 선). %K 선은 최근 29봉에만 의존한다."""
        tf_ms = timeframe_to_ms(timeframe)
        bars = self._store.load(
            symbol, timeframe, start_ms=entry_ms - _ARM_K_BARS * tf_ms, end_ms=entry_ms
        )
        if not bars.empty and "closed" in bars.columns:
            bars = bars[bars["closed"].astype(bool)]
        bars = bars[bars["open_time"].astype("int64") + tf_ms <= entry_ms]
        k = stoch.stoch_k_line(
            [float(v) for v in bars["high"]],
            [float(v) for v in bars["low"]],
            [float(v) for v in bars["close"]],
        )
        return [int(t) for t in bars["open_time"]], k

    def _arm_decision(
        self,
        symbol: str,
        timeframe: str,
        entry_price: float,
        ob_stop: float | None,
        entry_ms: int,
    ) -> ArmDecision:
        """팔 규칙 판정(WAN-451 ③).

        %K는 그 칸 TF의 **확정봉**으로, BTC 24h는 확정 1분봉으로 낸다(둘 다 체결 시각 이전만 —
        인과). %K 선은 최근 29봉에만 의존하므로 짧은 꼬리(`_ARM_K_BARS`)로 계산해도 값이 같다.
        """
        assert self._arm is not None  # 체결은 오더블록 시리즈에서만 오고 그 시리즈는 팔이 있다
        if ob_stop is None:
            return ArmDecision(False, "sizing", "손절 참조가 없음")
        tf_ms = timeframe_to_ms(timeframe)
        k_times, k = self._closed_k(symbol, timeframe, entry_ms)
        lookback = self._arm.crash_lookback_minutes
        btc = self._store.load(
            _ARM_BTC_SYMBOL, "1m", start_ms=entry_ms - (lookback + 10) * _MINUTE_MS, end_ms=entry_ms
        )
        if not btc.empty and "closed" in btc.columns:
            btc = btc[btc["closed"].astype(bool)]
        return evaluate_arm_fill(
            self._arm,
            entry_price=entry_price,
            ob_stop=ob_stop,
            entry_ms=entry_ms,
            tf_ms=tf_ms,
            htf_closed_times=k_times,
            htf_closed_k=k,
            btc_minute_times=[int(t) for t in btc["open_time"]],
            btc_minute_closes=[float(c) for c in btc["close"]],
        )

    def _check_exits(
        self,
        symbol: str,
        timeframe: str,
        *,
        low: float,
        high: float,
        time_ms: int,
        close: float | None = None,
    ) -> None:
        """오픈 페이퍼 포지션의 손절·시간 청산을 이 1분봉으로 판정한다(팔은 익절이 없다, WAN-436).

        손절이 먼저다 — 손절가는 참조가 그대로 청산가다. 손절이 안 났고 이 1분이 보유 봉수의
        경계 직전이면 그 1분 종가에 시간 청산한다(정본 ts4)."""
        position = next(
            (
                p
                for p in self._executor.open_positions
                if p.symbol == symbol and p.timeframe == timeframe
            ),
            None,
        )
        if position is None:
            return
        is_long = position.direction is OrderBlockDirection.BULLISH
        stop = position.stop_price
        stop_hit = stop is not None and (low <= stop if is_long else high >= stop)
        if stop_hit:
            assert stop is not None
            self._exit(symbol, timeframe, stop, time_ms, SignalExitReason.STOP_LOSS)
        elif (
            self._arm is not None
            and close is not None
            and is_time_exit_minute(
                time_ms, position.entry_time, timeframe_to_ms(timeframe), self._arm.hold_bars
            )
        ):
            # 시간 청산(WAN-451 팔 ts4) — 손절을 먼저 봤고, 경계 직전 1분봉 종가에 시장가로 나간다.
            self._exit(symbol, timeframe, close, time_ms, SignalExitReason.TIME_EXIT)

    def _exit(
        self, symbol: str, timeframe: str, price: float, time_ms: int, reason: SignalExitReason
    ) -> None:
        report = self._executor.exit(
            symbol, timeframe, exit_price=price, exit_time=time_ms, reason=reason, now_ms=time_ms
        )
        _logger.info(
            "페이퍼 청산(%s): %s %s %s @%.6g %s",
            reason.value,
            symbol,
            timeframe,
            format_kst_zoned(time_ms),
            price,
            "정산" if report.accepted else f"거부({report.outcome.reason})",
        )
        if self._notifier is not None:
            self._notifier.handle_exit(report, exit_price=price, reason=reason, exit_time=time_ms)

    # -- 재시작 영속화 (WAN-306) ----------------------------------------------

    def restore_state(self, *, now_ms: int) -> set[int]:
        """기동 시 이전 세션의 엔진 상태를 복원한다. 복원된 대기 주문의 장부 행 id 집합 반환.

        시리즈마다: 스냅샷을 읽어 → 공백이 따라잡기 한도(`catchup_max_ms`)를 넘으면
        **명시적으로 초기화**(스냅샷 삭제 + 경고 로그 — 대기 주문은 기존 재시작 폐기
        경로로 남는다, 조용한 재구성 금지) → 아니면 스냅샷 시점의 확정 창을 엔진에 먹여
        존 대장·시딩·ATR을 재구성한 뒤 세션 상태·대기 주문을 되살린다. 따라잡기 재생
        자체는 첫 `poll_series`가 한다(복원된 1분봉 커서부터 이어 소비).

        복원된 주문의 장부 행은 기동 시 재시작 폐기/만료 재평가에서 **제외**해야 한다 —
        호출부(`run_zone_limit_runner`)가 반환 집합을 `expire_stale_pending`·
        `discard_stale_pending`에 넘긴다.
        """
        restored: set[int] = set()
        if self._engine_state is None:
            return restored
        for symbol, timeframe in self._series:
            snap = self._engine_state.load(symbol, timeframe)
            if snap is None:
                continue
            key = (symbol, timeframe)
            gap_ms = now_ms - snap.last_substep_time
            if gap_ms > self._catchup_max_ms:
                _logger.warning(
                    "%s %s: 공백 %.1f시간이 따라잡기 한도(%.1f시간)를 넘어 상태를 명시적으로"
                    " 초기화합니다 — 대기 주문은 재시작 폐기로 남습니다(WAN-306)",
                    symbol,
                    timeframe,
                    gap_ms / 3_600_000,
                    self._catchup_max_ms / 3_600_000,
                )
                self._engine_state.delete(symbol, timeframe)
                continue
            # 복원 창도 꼬리만 로드한다(WAN-313) — 앵커는 스냅샷 시점(end_ms) 기준이라
            # 아래 `<= last_htf_time` 필터 + tail(lookback)이 전체 로드와 같은 창을 낸다.
            start = self._store.tail_window_start(
                symbol, timeframe, bars=self._lookback_bars, end_ms=snap.last_htf_time
            )
            if start is None:
                df = self._store.load(symbol, timeframe)
            else:
                df = self._store.load(symbol, timeframe, start_ms=start)
            closed_all = df[df["closed"].astype(bool)] if not df.empty else df
            window = closed_all
            if not closed_all.empty:
                window = closed_all[closed_all["open_time"] <= snap.last_htf_time].tail(
                    self._lookback_bars
                )
            if window.empty or int(window["open_time"].iloc[-1]) != snap.last_htf_time:
                _logger.warning(
                    "%s %s: 저장소에 스냅샷 시점의 확정봉이 없어 복원하지 않습니다"
                    "(대기 주문은 재시작 폐기로 남음, WAN-306)",
                    symbol,
                    timeframe,
                )
                self._engine_state.delete(symbol, timeframe)
                continue
            discontinuity = window_gap_summary(
                [int(t) for t in window["open_time"].tolist()], timeframe
            )
            if discontinuity is not None:
                _logger.warning(
                    "%s %s: 복원 창에 구멍이 있어 복원하지 않습니다(%s — 지표가 조용히"
                    " 틀린 값을 내는 조건, WAN-156 §3)",
                    symbol,
                    timeframe,
                    discontinuity,
                )
                self._engine_state.delete(symbol, timeframe)
                continue
            self._engine.on_htf_bars(symbol, timeframe, window)
            self._last_htf[key] = snap.last_htf_time
            snap = self._reconcile_with_journal(symbol, timeframe, snap)
            orders = self._engine.restore_series(symbol, timeframe, snap)
            self._last_substep[key] = snap.last_substep_time
            restored |= self._engine.journal_ids(symbol, timeframe)
            _logger.info(
                "%s %s: 엔진 상태 복원 — 공백 %.1f분 따라잡기 재생 예정 (대기 주문 %d개, WAN-306)",
                symbol,
                timeframe,
                gap_ms / 60_000,
                len(orders),
            )
        return restored

    def _reconcile_with_journal(
        self, symbol: str, timeframe: str, snap: SeriesStateSnapshot
    ) -> SeriesStateSnapshot:
        """마지막 저장과 종료 사이에 장부가 이미 종결한 주문은 되걸지 않는다.

        저장은 서브스텝마다지만 장부 쓰기와 원자적이지 않다 — 그 밀리초 창에서 크래시가
        나면 스냅샷은 「대기 중」인데 장부는 이미 체결/취소다. 장부가 정본이다: 체결이면 진입
        판정까지 끝난 주문이라 다시 걸면 같은 체결을 두 번 집행한다(포지션 자체는
        `PaperExecutor`가 저장소에서 복구한다). 취소면 그냥 버린다.
        """
        kept = [
            s
            for s in snap.shadows
            if s.journal_id is None
            or self._journal.order_status(s.journal_id) == LimitOrderStatus.PENDING.value
        ]
        if len(kept) == len(snap.shadows):
            return snap
        _logger.warning(
            "%s %s: 스냅샷의 대기 주문 %d개가 장부에서 이미 종결돼 되걸지 않습니다",
            symbol,
            timeframe,
            len(snap.shadows) - len(kept),
        )
        return snap.model_copy(update={"shadows": kept})

    def _save_state(self, symbol: str, timeframe: str) -> None:
        """이 시리즈의 엔진 상태를 스냅샷으로 저장한다(복원 앵커인 확정 창이 서기 전엔 무시)."""
        if self._engine_state is None:
            return
        last_htf = self._last_htf.get((symbol, timeframe))
        if last_htf is None:
            return
        snap = self._engine.snapshot_series(
            symbol, timeframe, last_htf_time=last_htf, saved_ms=self._now_ms()
        )
        if snap is not None:
            self._engine_state.save(symbol, timeframe, snap)

    # -- 스냅샷 --------------------------------------------------------------

    def _paper_positions(self) -> list[PaperPosition]:
        return [
            PaperPosition(
                symbol=p.symbol,
                timeframe=p.timeframe,
                direction=p.direction,
                entry_time=p.entry_time,
                entry_price=p.entry_price,
                stop_price=p.stop_price,
                take_profit_price=p.take_profit_price,
            )
            for p in self._executor.open_positions
        ]

    def _pending_snapshots(self) -> list[PendingOrderSnapshot]:
        return [
            PendingOrderSnapshot(
                symbol=o.symbol,
                timeframe=o.timeframe,
                direction=o.direction,
                limit_price=o.last_limit_price,
                stop_price=o.stop_price,
                placed_ms=o.placed_ms,
                bars_elapsed=o.bars_elapsed,
                limit_valid_bars=o.limit_valid_bars,
                tap_index=o.tap_index,
            )
            for o in self._engine.open_orders()
        ]

    @staticmethod
    def _store_htf_ms(timeframe: str) -> int:
        return timeframe_to_ms(timeframe)


def expire_stale_pending(
    journal: OrderJournal, *, now_ms: int, exclude_ids: frozenset[int] | set[int] = frozenset()
) -> int:
    """기동/재시작 시 이전 세션 대기 주문을 만료 기한으로 재평가한다(WAN-298). 만료 건수 반환.

    러너가 죽어 있던 구간에는 봉 계수도 벽시계 백스톱도 돌지 않으므로, 기한을 넘긴 채
    `pending`으로 남은 주문이 재시작 폐기(`discarded_restart`)로만 쓸려 나갔다 — 그러면
    「만료 기준 초과 + fill 없음 + discarded_restart」가 장부에 남아 정상 만료와 구분되지
    않는다(WAN-298 재현 쿼리의 🔴 조합). 이 함수는 `discard_stale_pending` **전에** 불려
    기한 초과분을 `cancelled_expired`로 먼저 정리한다. 종결 시각은 벽시계가 아니라 **기한
    시각**이다(서브스텝 경로가 기록했을 시각과 같다 — `live.limit_orders.expiry_deadline_ms`
    단일 출처). 무기한 · 열 도입 전 행은 기한을 모르므로 건드리지 않는다
    (이후 `discard_stale_pending`이 재시작 폐기로 마감한다).

    `exclude_ids`(WAN-306): 상태 복원으로 되살린 주문의 장부 행 — 따라잡기 재생이 봉
    계수로 정확한 봉에서 만료시키므로 벽시계 재평가에서 뺀다.
    """
    expired = 0
    for row in journal.stale_pending_orders():
        if row.journal_id in exclude_ids:
            continue  # 복원된 주문(WAN-306) — 따라잡기 재생이 봉 계수로 처리한다.
        if row.limit_valid_bars is None:
            continue  # 무기한 또는 열 도입 전 행 — 기한 불명.
        deadline = expiry_deadline_ms(
            row.placed_ms, row.limit_valid_bars, timeframe_to_ms(row.timeframe)
        )
        if deadline <= now_ms:
            journal.record_cancelled(
                row.journal_id, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=deadline
            )
            expired += 1
    return expired


def engine_settings_for(settings: Settings) -> Settings:
    """실행 엔진에 넘길 설정 — 팔이나 엔진 C를 켜면 일 손실 서킷브레이커를 끈다(WAN-451 ⑧).

    서킷브레이커는 정본(WAN-449 팔 · 엔진 C 백테스트)에 **없는 규칙**이다. 켜 두면 페이퍼만
    다른 규칙으로 돈다(WAN-305). 게다가 그 판정은 `paper_trades`만 합산해 엔진 C 손익을 못
    본다. 둘 다 꺼져 있으면(러너가 아무것도 안 돈다) 설정을 그대로 돌려준다. 러너의
    발동/해제 알림(옛 오더블록 페이퍼 시절)은 없어졌다 — 끈 규칙을 알릴 일이 없다.
    """
    if not (settings.live_tsmom_enabled or settings.live_arm_enabled):
        return settings
    limits = settings.risk_limits.model_copy(update={"daily_loss_limit_fraction": None})
    return settings.model_copy(update={"risk_limits": limits})


#: 팔 거래당 리스크 = 맞춤 크기 2.8172% × λ 0.906(WAN-449 재산출 2026-10-15 — 같은 1분 가격 순서
#: 규약 · 옛 값 2.8533% × 0.885).
ARM_RISK_PER_TRADE = 0.028172 * 0.906
#: 팔이 요구하는 관통 체결(bp, 정본 `pen_5bp`).
ARM_FILL_PENETRATION_BPS = 5.0


def arm_settings_problems(settings: Settings) -> list[str]:
    """팔을 켰을 때 함께 맞아야 하는 설정 중 어긋난 것 — 비면 통과(WAN-451 ③).

    러너가 이 값들을 조용히 덮어쓰지 않는 이유: 덮어쓰면 `.env`에 적힌 값과 실제로 도는 값이
    달라진다(「라벨과 동작이 어긋남」, WAN-91/95/112/123/159). 그래서 어긋나면 기동을 거부하고
    무엇을 넣어야 하는지 알려 준다.
    """
    problems: list[str] = []
    if settings.confluence.retap_mode != "once":
        problems.append(
            f"첫 탭만이어야 한다: ALPHABLOCK_CONFLUENCE__RETAP_MODE=once"
            f" (지금 {settings.confluence.retap_mode})"
        )
    if settings.confluence.fill_penetration_bps != ARM_FILL_PENETRATION_BPS:
        problems.append(
            "5bp 관통 체결이어야 한다: ALPHABLOCK_CONFLUENCE__FILL_PENETRATION_BPS=5"
            f" (지금 {settings.confluence.fill_penetration_bps})"
        )
    if abs(settings.risk_sizing.risk_per_trade - ARM_RISK_PER_TRADE) > 1e-6:
        problems.append(
            f"거래당 리스크가 {ARM_RISK_PER_TRADE:.9f}이어야 한다:"
            f" ALPHABLOCK_RISK_SIZING__RISK_PER_TRADE={ARM_RISK_PER_TRADE:.9f}"
            f" (지금 {settings.risk_sizing.risk_per_trade})"
        )
    if not settings.live_zone_limit_enabled:
        problems.append("팔은 오더블록 시리즈 위에서 돈다: ALPHABLOCK_LIVE_ZONE_LIMIT_ENABLED=true")
    if not settings.universe_enabled:
        problems.append(
            "팔은 매주 종목 목록으로 감시한다: ALPHABLOCK_UNIVERSE_ENABLED=true"
            " (수집기가 목록을 만들고 그 종목을 모은다)"
        )
    return problems


def build_paper_recorder(
    paper_store: PaperTradeStore,
    settings: Settings,
    *,
    funding_store: FundingRateStore | None = None,
) -> PaperTradeRecorder:
    """페이퍼 장부 기록기를 **채택 비용 회계**로 만든다(WAN-371).

    🚨 **유동성 구분을 명시적으로 넘기는 것이 이 함수의 존재 이유다.** 기본값(테이커)에
    맡기면 지정가로 도는 러너의 진입을 시장가로 계산하고(WAN-45 배선 · WAN-256 틱 승격 ·
    채택 진입 방식은 존 근단 지정가 WAN-95/112), 익절도 백테스트(메이커, WAN-370)와
    갈린다 — 같은 셋업·같은 가격인데 **다른 경기의 기록**이 된다(WAN-305).

    ⚠️ WAN-392 이후 이 값은 **표시 층이 아니라 지갑 잔고를 직접 움직인다** — 지갑이 장부
    행의 `net_pct`로 정산하기 때문이다. 그래서 자본곡선·MDD·다음 베팅 크기까지 따라간다.

    청산 유동성은 고정값이 아니라 **사유별 함수**(`adopted_exit_liquidity`)를 넘긴다.
    고정 `Liquidity` 하나를 주면 「청산」을 한 덩어리로 취급하게 되고, 그것이 WAN-370이
    백테스트 쪽에서 명시적으로 금지한 실패다.
    """
    from paper.store import ADOPTED_ENTRY_LIQUIDITY, PaperTradeRecorder, adopted_exit_liquidity

    return PaperTradeRecorder(
        paper_store,
        cost_model=settings.costs,
        entry_liquidity=ADOPTED_ENTRY_LIQUIDITY,
        exit_liquidity=adopted_exit_liquidity,
        funding_store=funding_store,
    )


def run_zone_limit_runner(settings: Settings, *, once: bool = False) -> None:
    """페이퍼 러너를 실행한다 — 스토캐스틱 팔 + 엔진 C(`live.runner`가 위임하는 진입점).

    오더블록 시리즈(`live_zone_limit_enabled`)는 팔(`live_arm_enabled`)이 켜져 있어야만 돈다.
    옛 오더블록 페이퍼(칸당 대기 주문 · 고정 R 익절 · 재진입 · 틱 피드)는 WAN-451에서
    없어졌으므로, 오더블록을 켜고 팔을 끈 설정은 기동을 거부한다 — 조용히 다른 규칙으로 돌면
    라벨과 동작이 어긋난다(WAN-91/95/112/123/159). 엔진 C만 돌리려면 오더블록을 끈다.
    """
    from common.telegram import build_telegram_client
    from data.funding import FundingRateStore
    from execution.engine import build_execution_engine
    from execution.leverage import book_per_trade_sizing
    from paper.store import PaperTradeStore

    if settings.live_zone_limit_enabled and not settings.live_arm_enabled:
        raise SystemExit(
            "옛 오더블록 페이퍼는 WAN-451에서 없어졌다 — 스토캐스틱 팔을 켜거나"
            " (ALPHABLOCK_LIVE_ARM_ENABLED=true, `docs/ops/wan451-arm-paper.md`)"
            " 오더블록 시리즈를 끈다(ALPHABLOCK_LIVE_ZONE_LIMIT_ENABLED=false — 엔진 C만 돈다)."
        )
    arm_params: ArmParams | None = None
    if settings.live_arm_enabled:
        problems = arm_settings_problems(settings)
        if problems:
            raise SystemExit(
                "스토캐스틱 팔(ALPHABLOCK_LIVE_ARM_ENABLED=true)을 켜려면 다음을 맞춰야 한다"
                " (WAN-451 ③):\n- " + "\n- ".join(problems)
            )
        arm_params = ArmParams()

    store = OhlcvStore(settings.db_path)
    journal = OrderJournal(settings.db_path)
    paper_store = PaperTradeStore(settings.db_path)
    funding_store = FundingRateStore(settings.db_path) if settings.funding_enabled else None
    recorder = build_paper_recorder(paper_store, settings, funding_store=funding_store)
    # 레버리지 북(WAN-171 = WAN-45의 2단계): 칸=(종목,TF)마다 1포지션 · 여러 칸 동시 · 한
    # 지갑(공유 자본) · 배수 N. 기본값 = 채택 북(cap_only 5배, WAN-213). 엔진이 백테스트와
    # **같은** `resolve_book_sizing`으로 사이징하고, 거래당 리스크 금액(장부)은 북의 거래당
    # 사이징(cap_only=1배·combined=N배)으로 재야 라벨과 실제가 어긋나지 않는다.
    book = settings.live_leverage_book
    # 엔진 C(WAN-451): 원장을 지갑에 물리고, 일 손실 서킷브레이커를 끈다(⑧ — 정본에 없는 규칙).
    tsmom_ledger = TsmomLedgerStore(settings.db_path) if settings.live_tsmom_enabled else None
    engine_settings = engine_settings_for(settings)
    executor = PaperExecutor(
        engine=build_execution_engine(engine_settings, leverage_book=book),
        store=paper_store,
        recorder=recorder,
        sizing=book_per_trade_sizing(settings.risk_sizing, book),
        tsmom_ledger=tsmom_ledger,
    )
    # 재시작 영속화 저장소(WAN-306) — 장부와 같은 DB. 러너가 매 서브스텝 저장하고
    # 기동 시 복원 + 공백 따라잡기 재생한다(모듈 독스트링 §재시작 정책).
    engine_state = EngineStateStore(settings.db_path)
    universe: UniverseStore | None = None
    signals: ArmSignalStore | None = None
    try:
        now = int(time.time() * 1000)
        session_id = journal.start_session(now_ms=now)

        series: list[Series] = []
        if arm_params is not None:
            # 팔(WAN-451 ③): 그 주 목록 × 팔 TF. 기동 때는 스냅샷이 남은 칸도 넣어 목록 밖 칸의
            # 대기 주문까지 복원한다 — 첫 폴링의 `sync_series`가 정리한다.
            universe = UniverseStore(settings.db_path)
            signals = ArmSignalStore(settings.db_path)
            snap = universe.at(now)
            members = sorted(snap.members) if snap is not None else []
            series = sorted(
                {(sym, tf) for sym in members for tf in ARM_TIMEFRAMES}
                | set(engine_state.series_keys())
                | {(p.symbol, p.timeframe) for p in executor.open_positions}
            )
            if snap is None:
                _logger.warning(
                    "팔 종목 목록이 아직 없습니다 — 수집기(ALPHABLOCK_UNIVERSE_ENABLED)가 목록을"
                    " 만들면 다음 폴링부터 감시합니다"
                )
            _logger.info(
                "스토캐스틱 팔 켜짐(WAN-451): 하한 %.0f%% · %%K < %.0f · 손절 ×%.0f · ts%d · "
                "폭락 ×%.1f · 서킷브레이커 꺼짐 · 매주 종목 목록 · 몰림 규칙 · 탭마다 독립 주문",
                arm_params.min_stop_width * 100,
                arm_params.k_threshold,
                arm_params.stop_multiple,
                arm_params.hold_bars,
                arm_params.crash_multiplier,
            )
        else:
            _logger.info("오더블록 시리즈 꺼짐(WAN-451 ⑦) — 시리즈 0개, 대기 주문 없음")
        # 매매 이벤트 알림(WAN-189, 페이퍼 한정). 텔레그램 미설정이면 build_telegram_client가
        # None을 주고 알림기는 드라이런(로그만)으로 돈다 — `notifier`의 기존 관행 그대로다.
        # 엔진보다 **먼저** 만든다 — 엔진의 스킵 훅이 알림기를 참조하기 때문이다(WAN-221).
        notifier: ZoneLimitNotifier | None = None
        if settings.paper_trade_notify_enabled:
            notifier = ZoneLimitNotifier(
                build_telegram_client(settings),
                events=frozenset(settings.paper_trade_notify_events),
                # 일일 요약의 체결률·사유 카운트는 DB 장부 창 조회로 낸다(재계산 아님, WAN-221).
                funnel_provider=lambda s, e: journal.funnel_counts(start_ms=s, end_ms=e),
                # 팔 규칙 거부는 체결마다 생길 수 있어 건별로 안 보낸다(WAN-451 ⑩ — 일일 요약).
                # 팔은 탭마다 독립 주문이라 「칸에 이미 포지션」도 흔하다 — 같이 요약으로 모은다.
                quiet_reject_codes=frozenset({*ARM_REJECT_CODES, REJECT_CODE_CELL_BUSY}),
            )

        # 존폭 필터 기각의 건별 알림(옵트인, WAN-221). deviation은 만료 이벤트에서 따로 넘기고,
        # retap은 no_fill처럼 시끄러워 넘기지 않는다(존폭만 전달).
        skip_listener: Callable[[str, str, str, int], None] | None = None
        if notifier is not None:

            def _forward_skip(reason: str, symbol: str, timeframe: str, time_ms: int) -> None:
                if reason == SKIP_REASON_ZONE_WIDTH:
                    assert notifier is not None
                    notifier.note_filter_skip(
                        reason, symbol=symbol, timeframe=timeframe, time_ms=time_ms
                    )

            skip_listener = _forward_skip

        engine = ZoneLimitLiveEngine(
            params=settings.confluence,
            journal=journal,
            session_id=session_id,
            skip_listener=skip_listener,
        )

        tsmom_rebalancer: TsmomRebalancer | None = None
        if tsmom_ledger is not None:
            telegram = build_telegram_client(settings)
            tsmom_rebalancer = TsmomRebalancer(
                store=store,
                ledger=tsmom_ledger,
                wallet=executor,
                funding_store=funding_store,
                notify=(
                    (lambda text: telegram.send_message(text, parse_mode=None))
                    if telegram is not None and settings.paper_trade_notify_enabled
                    else None
                ),
            )
            _logger.info(
                "엔진 C 켜짐(WAN-451): %s · 하루 1회 00:00 UTC(KST 09:00) 리밸런싱 · "
                "k=%.3f λ=%.3f · 서킷브레이커 꺼짐",
                ", ".join(tsmom.ASSETS),
                tsmom.K,
                tsmom.LAMBDA,
            )

        runner = ZoneLimitPaperRunner(
            store=store,
            engine=engine,
            journal=journal,
            session_id=session_id,
            executor=executor,
            params=settings.confluence,
            series=series,
            lookback_bars=settings.live_signal_lookback_bars,
            poll_interval_seconds=settings.live_poll_interval_seconds,
            runtime_state=RuntimeStateStore(settings.live_runtime_state_path),
            notifier=notifier,
            engine_state=engine_state,
            catchup_max_ms=int(settings.live_catchup_max_hours * 3_600_000),
            tsmom=tsmom_rebalancer,
            arm=arm_params,
            universe=universe,
            signals=signals,
            bootstrap_days=settings.live_zone_memory_days if arm_params is not None else 0,
        )
        # 재시작 복원(WAN-306): 이전 세션의 엔진 상태(전이 맥락·탭 표식·기억하던 존·대기
        # 주문)를 되살린다 — 복원된 주문은 아래 재평가/폐기에서 제외한다(따라잡기 재생이 봉
        # 계수로 정확한 봉에서 체결/만료를 낸다).
        restored_ids = runner.restore_state(now_ms=now)
        if restored_ids:
            _logger.info(
                "이전 세션 대기 주문 %d건 복원(따라잡기 재생 대상, WAN-306)", len(restored_ids)
            )
        # 재시작 재평가(WAN-298): 복원되지 못한 대기 주문 중 만료 기한(24봉, WAN-222)을
        # 이미 넘긴 것은 재시작 폐기가 아니라 정상 만료로 정리한다(종결 시각 = 기한 시각).
        expired_stale = expire_stale_pending(journal, now_ms=now, exclude_ids=restored_ids)
        if expired_stale:
            _logger.info(
                "이전 세션 대기 주문 %d건 만료 처리(cancelled_expired — 기한 초과, WAN-298)",
                expired_stale,
            )
        # 복원도 만료도 아닌 나머지(스냅샷 없음·한도 초과·복원 불가)만 폐기로 마감한다 —
        # `discarded_restart`는 이제 예외 경로다(WAN-306 완료 기준 4).
        discarded = journal.discard_stale_pending(now_ms=now, exclude_ids=restored_ids)
        if discarded:
            _logger.info("이전 세션 대기 주문 %d건 폐기(discarded_restart)", discarded)
        _logger.info(
            "페이퍼 러너 시작(WAN-45 · WAN-451): %d 시리즈, 폴링 %ds, 세션 #%d"
            " · 레버리지 북 %s×%.4g (WAN-171, 공유 자본)"
            " (live_trading=%s — 페이퍼 한정)",
            len(series),
            settings.live_poll_interval_seconds,
            session_id,
            book.leverage_mode,
            book.leverage_multiple,
            settings.live_trading,
        )
        if universe is not None:
            runner.sync_series(now)  # 복원 뒤 목록 밖 칸 정리
        runner.run(max_polls=1 if once else None)
    finally:
        if universe is not None:
            universe.close()
        if signals is not None:
            signals.close()
        engine_state.close()
        store.close()
        journal.close()
        paper_store.close()
        if tsmom_ledger is not None:
            tsmom_ledger.close()
        if funding_store is not None:
            funding_store.close()
