"""WAN-451 — 같은 1분의 「앞 포지션 청산」과 「다음 체결」: 옛 규약(청산 먼저) vs 가격 순서.

정본 팔(WAN-449: 스토캐스틱 팔 · 거래대금 상위 31 · WAN-448 손절 체결 + 엔진 C · 합성 MDD 35%)은
채택 북에서 칸당 포지션 하나를 들고, 같은 1분에 앞 포지션이 청산되면 **청산을 먼저** 정산했다
(반개구간 `exit_time == now`). 1분봉 안의 순서를 모르니 고른 규약이다.

실제 순서는 가격이 정한다(롱 · 같은 칸):

* 앞 포지션이 **손절**이고 다음 체결가가 그 손절가 **이하**면 — 가격이 손절가를 먼저 지나
  더 내려가 체결된다. 청산이 먼저다(옛 규약과 같다).
* 다음 체결가가 손절가 **위**면 — 내려오는 가격이 지정가에 먼저 닿는다. 체결 순간 앞 포지션은
  아직 열려 있으니 그 체결은 「칸에 포지션 있음」으로 **못 들어간다**.
* 앞 포지션이 **시간 청산**이면 — 그 1분 종가에 나간다. 그 1분의 체결은 언제나 청산보다 먼저다.

사용자 결정(2026-10-15 「그래 바꿔」)으로 가격 순서가 정본 규약이 됐다(`w436.ADOPTED_SAME_MINUTE`).
이 모듈은 그 결정의 근거 표다 — 옛 규약 팔과 가격 순서 팔을 같은 파이프라인으로 돌려 맞댄다.
진입을 빼면 그 칸이 비어 다른 후보가 들어갈 수 있으므로, 배치를 다시 하고 새로 생긴 같은 1분
충돌이 없을 때까지 되풀이한다. 몰림 신호 수(가상 체결)는 그대로다 — 못 들어간 체결도 신호로
센다(정본 정의). 검산: 이 모듈의 판 ≡ 정본 배치 함수(`w.place(same_minute="price_order")`).

비교는 두 자로 한다: (1) **크기 그대로**(옛 규약 팔 리스크 · k·λ) — 순수한 손익 차이,
(2) **다시 맞춤**(팔 리스크를 MDD 35%로 · k·λ도 다시) — 정본 헤드라인과 같은 자.

재현: `uv run python -m backtest.wan451_same_minute_order`(시장 WAN-442 피클 · 펀딩 WAN-447 캐시 ·
손절 α WAN-448 CSV). 측정 전용 — 엔진·기본값·토대·페이퍼 불변 · 실거래 보류
유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import pickle
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan424_stoch_ob_arm as a424  # noqa: F401 — 3h 주기 등록
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq
from backtest import wan448_stop_fill_ticks as st
from backtest import wan449_tsmom_second_engine as e449
from common.timefmt import format_kst

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
SUMMARY_PATH = REPORT_DIR / "wan451_same_minute_order_summary.md"
DROPPED_CSV = REPORT_DIR / "wan451_same_minute_order_dropped.csv"
MAX_ROUNDS = 50

Key = tuple[str, str, int, float]


def conflicts(trades: Sequence[w.PlacedTrade]) -> tuple[set[Key], int]:
    """가격 순서로는 못 들어가는 진입의 키(정본 `w.same_minute_conflicts`) · 같은 1분 사례 수."""
    by_cell: dict[tuple[str, str], list[w.PlacedTrade]] = {}
    for t in trades:
        by_cell.setdefault((t.symbol, t.timeframe), []).append(t)
    same = 0
    for cell in by_cell.values():
        cell.sort(key=lambda t: (t.entry_time, t.exit_time))
        same += sum(
            1 for prev, nxt in zip(cell, cell[1:], strict=False) if nxt.entry_time == prev.exit_time
        )
    return w.same_minute_conflicts(trades), same


def without(mk: c.Market, keys: set[Key]) -> c.Market:
    """그 진입을 뺀 시장 — 몰림 신호 수는 **다시 세지 않는다**(못 들어간 체결도 신호다)."""
    keep = [i for i, e in enumerate(mk.entries) if e.key not in keys]
    return c.Market(
        mk.name,
        mk.payloads,
        [mk.entries[i] for i in keep],
        [mk.counts[i] for i in keep],
        [mk.btc_24h[i] for i in keep],
        [mk.vol_ratio[i] for i in keep],
    )


@dataclass(frozen=True)
class Realistic:
    market: c.Market
    dropped: list[w.PlacedTrade]
    """빠진 진입 — 처음 걸린 배치에서의 그 거래(손익은 정본 배치 기준)."""
    rounds: int
    same_minute: int
    """정본 배치의 같은 1분 사례 수(청산 먼저 포함)."""


def realistic_market(mk: c.Market) -> Realistic:
    """충돌이 없을 때까지 「못 들어가는 진입」을 빼고 다시 배치한다."""
    dropped: dict[Key, w.PlacedTrade] = {}
    cur = mk
    same0 = -1
    for rounds in range(MAX_ROUNDS):
        trades = c.place_arm(
            cur, c.ARM_B, "stop", in_book=True, same_minute=w.SAME_MINUTE_EXIT_FIRST
        )
        bad, same = conflicts(trades)
        if same0 < 0:
            same0 = same
        if not bad:
            return Realistic(cur, list(dropped.values()), rounds, same0)
        for t in trades:
            key = (t.symbol, t.timeframe, t.entry_time, t.entry_price)
            if key in bad:
                dropped[key] = t
        cur = without(mk, set(dropped))
    raise RuntimeError(f"{MAX_ROUNDS}회 안에 같은 1분 충돌이 사라지지 않았다")


def arm_trades(
    built: u.Markets, *, realistic: bool
) -> tuple[list[w.PlacedTrade], list[w.PlacedTrade], float, list[Realistic]]:
    """`e449.arm_trades`와 같은 사슬 — `realistic`이면 회전 뒤 시장에서 충돌 진입을 뺀다."""
    s = m.setting(built)
    top = s.rotations(lambda d, t: liq.select_liquidity(d, t, top=True))
    smk, _ = m.apply_rotation(s.spot_pool, top[0], force_close=False)
    fmk, _ = m.apply_rotation(s.fut_pool, top[1], force_close=False)
    info: list[Realistic] = []
    if realistic:
        rs, rf = realistic_market(smk), realistic_market(fmk)
        smk, fmk = rs.market, rf.market
        info = [rs, rf]
    pl = {
        f: (
            c.place_arm(smk, c.ARM_B, f, in_book=True, same_minute=w.SAME_MINUTE_EXIT_FIRST),
            c.place_arm(fmk, c.ARM_B, f, in_book=True, same_minute=w.SAME_MINUTE_EXIT_FIRST),
        )
        for f in c.FILLS
    }
    tk = pd.read_csv(st.TICKS_CSV)
    tk = tk[tk["ok"]]
    al = {k: float(tk[tk["stratum"] == k]["alpha"].mean()) for k in (st.CRASH, st.NORMAL)}
    sp, fu = (
        st.blended_trades(pl["stop"][k], pl["bar_low"][k], st.alphas_for(pl["stop"][k], mk, al))
        for k, mk in ((0, smk), (1, fmk))
    )
    risk = c.fit_risk(lambda x: c.chain(sp, fu, x)[1], target=c.MDD_TARGET)
    return sp, fu, risk, info


@dataclass(frozen=True)
class ArmNums:
    trades: int
    net_r_sum: float
    cagr: float
    mdd: float


def arm_nums(sp: Sequence[w.PlacedTrade], fu: Sequence[w.PlacedTrade], risk: float) -> ArmNums:
    total, mdd = c.chain(sp, fu, risk)
    yrs = c._years(u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END))
    trades = [*sp, *fu]
    return ArmNums(len(trades), float(sum(t.net_r for t in trades)), c.cagr(total, yrs), mdd)


@dataclass(frozen=True)
class ComboNums:
    cagr: float
    mdd: float
    lam: float
    k: float
    median: float
    negative: int
    yearly: pd.Series


def combo_nums(built: u.Markets, daily: e449.ArmDaily) -> ComboNums:
    _frame, variants, _notes, nums = e449.run(built, arm=daily)
    main = variants[0]
    _ok, med, neg = e449.verdict(main.yearly)
    return ComboNums(nums["combo_cagr"], main.mdd, main.lam, main.k, med, neg, main.yearly)


def fixed_combo(
    built: u.Markets, base: e449.ArmDaily, alt: e449.ArmDaily, k: float, lam: float
) -> tuple[float, float]:
    """정본 k·λ 그대로 두고 팔 일 수익만 바꾼 합성(CAGR, MDD) — 순수한 손익 차이."""
    frame, _v, _n, _nums = e449.run(built, arm=base)
    rc, lc = frame["c_ret"], frame["c_low"]
    idx = frame.index
    ra = alt.ret.reindex(idx).fillna(0.0)
    la = alt.low.reindex(idx).fillna(0.0)
    eq, low = e449.combine(ra, la, rc, lc, k, lam)
    yrs = c._years(u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END))
    return c.cagr(float(eq.iloc[-1]) - 1.0, yrs), e449.mdd_of(eq, low)


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def render(
    base_arm: ArmNums,
    real_arm_fixed: ArmNums,
    real_arm_fit: ArmNums,
    base_risk: float,
    real_risk: float,
    base_combo: ComboNums,
    real_combo: ComboNums,
    fixed: tuple[float, float],
    info: Sequence[Realistic],
    elapsed: float,
) -> str:
    dropped = [t for r in info for t in r.dropped]
    same = sum(r.same_minute for r in info)
    lines = [
        "# WAN-451 — 같은 1분 순서: 옛 규약(청산 먼저) vs 가격 순서(채택)",
        "",
        "정본 팔(WAN-449)을 옛 규약으로 돌린 것과, 같은 1분에 「체결이 청산보다 먼저」인 진입을",
        "뺀 것(가격 순서 · 2026-10-15 채택)을 같은 파이프라인(회전 · 몰림 · 폭락 ×0.5 ·",
        "손절 α · MDD 35% 맞춤)으로 맞댔다.",
        "",
        "## 몇 건인가",
        "",
        f"- 옛 규약 배치의 같은 1분 사례(앞 포지션이 닫힌 바로 그 1분의 진입): **{same}건**"
        f" (현물 구간 {info[0].same_minute} · 선물 구간 {info[1].same_minute})",
        f"- 그중 가격 순서로는 못 들어가는 진입(체결이 먼저): **{len(dropped)}건**"
        f" (재배치 {max(r.rounds for r in info)}회 안에 충돌 0)",
        f"- 팔 거래 {base_arm.trades}건 → 가격 순서 {real_arm_fixed.trades}건"
        f" (빈 칸에 다른 후보가 들어가 순감은 {base_arm.trades - real_arm_fixed.trades}건)",
        "",
        "## 팔 단독",
        "",
        "| | 거래 | 순 R 합 | 연환산(8.6년) | MDD | 거래당 리스크 |",
        "| -- | --: | --: | --: | --: | --: |",
        f"| 옛 규약 | {base_arm.trades} | {base_arm.net_r_sum:+.1f} | {_pct(base_arm.cagr)} |"
        f" {base_arm.mdd:.1%} | {base_risk:.4%} |",
        f"| 가격 순서 · 크기 그대로 | {real_arm_fixed.trades} | {real_arm_fixed.net_r_sum:+.1f} |"
        f" {_pct(real_arm_fixed.cagr)} | {real_arm_fixed.mdd:.1%} | {base_risk:.4%} |",
        f"| 가격 순서 · MDD 35% 다시 맞춤 | {real_arm_fit.trades} | {real_arm_fit.net_r_sum:+.1f} |"
        f" {_pct(real_arm_fit.cagr)} | {real_arm_fit.mdd:.1%} | {real_risk:.4%} |",
        "",
        "## 합성(팔 + 엔진 C) — 정본 헤드라인과 같은 자",
        "",
        "| | 연환산(8.6년) | MDD | 해마다 중앙 | 마이너스 해 | k | λ |",
        "| -- | --: | --: | --: | --: | --: | --: |",
        f"| 옛 규약 | {_pct(base_combo.cagr)} | {base_combo.mdd:.1%} | {_pct(base_combo.median)} |"
        f" {base_combo.negative} | {base_combo.k:.3f} | {base_combo.lam:.3f} |",
        f"| 가격 순서 · 크기 그대로 | {_pct(fixed[0])} | {fixed[1]:.1%} | — | — | — | — |",
        f"| 가격 순서 · 다시 맞춤 | {_pct(real_combo.cagr)} | {real_combo.mdd:.1%} |"
        f" {_pct(real_combo.median)} | {real_combo.negative} | {real_combo.k:.3f} |"
        f" {real_combo.lam:.3f} |",
        "",
        "## 해마다(합성 · 다시 맞춤)",
        "",
        "| 연도 | 옛 규약 | 가격 순서 | 차 |",
        "| -- | --: | --: | --: |",
    ]
    for year in base_combo.yearly.index:
        a = float(base_combo.yearly[year])
        b = float(real_combo.yearly.get(year, float("nan")))
        lines.append(f"| {year} | {_pct(a)} | {_pct(b)} | {(b - a) * 100:+.1f}%p |")
    lines += [
        "",
        "## 빠진 진입",
        "",
        "| 진입(KST) | 칸 | 순 R(옛 규약 배치) |",
        "| -- | -- | --: |",
    ]
    for t in sorted(dropped, key=lambda t: t.entry_time):
        lines.append(
            f"| {format_kst(t.entry_time)} | {t.symbol.split('/')[0]} {t.timeframe} |"
            f" {t.net_r:+.2f} |"
        )
    if dropped:
        r = np.array([t.net_r for t in dropped])
        lines += [
            "",
            f"빠진 진입의 순 R: 합 {r.sum():+.2f} · 평균 {r.mean():+.3f}"
            f" · 이긴 거래 {int((r > 0).sum())}/{len(r)}",
        ]
    lines += [
        "",
        "## 읽는 법",
        "",
        "- 「크기 그대로」가 순수한 손익 차이다. 「다시 맞춤」은 정본 헤드라인과 같은 자다.",
        "- 몰림 신호 수는 그대로다(못 들어간 체결도 신호로 센다 — 정본 정의).",
        "- 1분봉 해상도의 판정이다: 체결가가 손절가 **위**면 체결이 먼저라고 본다(롱 · 내려오는"
        " 가격). 같으면 청산이 먼저로 둔다.",
        f"- 측정 {elapsed:.0f}초 · 측정 전용(엔진·기본값·토대·페이퍼 불변).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--markets", type=Path, default=m.MARKETS_PATH)
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    with args.markets.open("rb") as fh:
        built = pickle.load(fh)  # noqa: S301 — WAN-442가 만든 로컬 캐시

    sp0, fu0, risk0, _ = arm_trades(built, realistic=False)
    ref = e449.arm_trades(built, same_minute=w.SAME_MINUTE_EXIT_FIRST)
    if (len(sp0), len(fu0), risk0) != (len(ref[0]), len(ref[1]), ref[2]):
        raise RuntimeError("검산 실패 — 옛 규약 판이 `e449.arm_trades(exit_first)`와 다르다")
    sp1, fu1, risk1, info = arm_trades(built, realistic=True)
    # 검산 — 이 모듈의 「빼고 다시 배치」 ≡ 정본 배치 함수의 가격 순서 규약(`w.place`).
    adopted = e449.arm_trades(built, same_minute=w.SAME_MINUTE_PRICE_ORDER)
    mine = sorted((t.symbol, t.timeframe, t.entry_time, t.exit_time) for t in [*sp1, *fu1])
    canon = sorted(
        (t.symbol, t.timeframe, t.entry_time, t.exit_time) for t in [*adopted[0], *adopted[1]]
    )
    if mine != canon or risk1 != adopted[2]:
        raise RuntimeError("검산 실패 — 가격 순서 판이 정본 배치 함수와 다르다")

    base_arm = arm_nums(sp0, fu0, risk0)
    real_fixed = arm_nums(sp1, fu1, risk0)
    real_fit = arm_nums(sp1, fu1, risk1)
    base_daily = e449.arm_daily_from(sp0, fu0, risk0)
    real_daily_fixed = e449.arm_daily_from(sp1, fu1, risk0)
    real_daily_fit = e449.arm_daily_from(sp1, fu1, risk1)
    base_combo = combo_nums(built, base_daily)
    real_combo = combo_nums(built, real_daily_fit)
    fixed = fixed_combo(built, base_daily, real_daily_fixed, base_combo.k, base_combo.lam)

    text = render(
        base_arm,
        real_fixed,
        real_fit,
        risk0,
        risk1,
        base_combo,
        real_combo,
        fixed,
        info,
        time.monotonic() - t0,
    )
    SUMMARY_PATH.write_text(text, encoding="utf-8")
    rows = [
        {
            "segment": seg,
            "symbol": t.symbol,
            "timeframe": t.timeframe,
            "entry_time": t.entry_time,
            "entry_kst": format_kst(t.entry_time),
            "entry_price": t.entry_price,
            "net_r": t.net_r,
        }
        for seg, r in zip(("spot", "futures"), info, strict=True)
        for t in r.dropped
    ]
    pd.DataFrame(rows).to_csv(DROPPED_CSV, index=False)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
