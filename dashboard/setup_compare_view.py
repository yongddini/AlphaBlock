"""페이퍼↔백테 셋업 3열 대조의 표시 계층 (WAN-295).

`live.setup_compare`가 셋업 단위로 조인한 `SetupCompareResult`를 사용자 승인 목업
(`docs/mockups/wan290_timeline_compare_mockup.html`)의 **3열 대조 카드**로 옮긴다 —
좌(페이퍼) | 가운데(Δ 막대) | 우(백테). Streamlit에 의존하지 않는 **순수 함수**만 둔다
(`dashboard/trade_timeline_view.py`와 같은 규칙): 화면 없이 페이로드·HTML을 테스트한다.

목업의 시각 문법(팔레트·Δ 막대·판정갈림 빨강·가격벗어남 주황)을 그대로 잇는다 — 색은
`dashboard/lightweight_chart.py` 다크 테마와 같은 값이다(차트와 한 화면에서 톤이 맞게).
행클릭 15열 상세·차트 점프는 아래의 선택 가능한 표(WAN-234/290)가 계승하므로, 이 카드는
**읽기 전용 개요**다(칩 필터는 iframe 안에서 클라이언트가 처리 — 서버 재실행 없음).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from common.timefmt import format_kst
from live.setup_compare import SetupCompareResult, SetupComparison
from live.trade_timeline import TimelineRow

__all__ = ["compare_rows_payload", "setup_compare_html"]


def _short(symbol: str) -> str:
    """표시용 짧은 심볼(`BTC/USDT:USDT` → `BTC`)."""
    return symbol.split("/", 1)[0]


def _hhmm(ms: int | None) -> str:
    """KST 시각의 HH:MM(WAN-172 — 공용 포맷터 사용). 없으면 빈 문자열."""
    if ms is None:
        return ""
    return format_kst(ms).split(" ", 1)[-1]


def _mmdd_hhmm(ms: int | None) -> str:
    """KST `MM-DD HH:MM`(WAN-172 공용 포맷터에서 연도만 뗀다). 없으면 빈 문자열."""
    if ms is None:
        return ""
    stamp = format_kst(ms)  # `YYYY-MM-DD HH:MM`
    return stamp[5:] if len(stamp) > 5 else stamp


def _px(value: float | None) -> str:
    return "—" if value is None else f"{value:.8g}"


def _side_payload(row: TimelineRow | None, *, entered: bool) -> dict[str, object]:
    """한 쪽(페이퍼 또는 백테)의 표시 페이로드 — 상태·손익률·체결가·진입여부."""
    if row is None:
        return {"s": "—", "v": None, "px": "—", "entered": False}
    return {
        "s": row.status,
        "v": row.pnl_pct,
        "px": _px(row.fill_price),
        "entered": entered,
    }


def _zone_tag(comp: SetupComparison) -> str:
    """이 줄이 **어느 존의 몇 번째 탭**인지 (WAN-333 §1). 존 정체성이 없으면 빈 문자열.

    존 병합 폐지(`combine_obs=False`, WAN-149) 이후 겹치는 오더블록은 각자 남으므로, 같은
    시각에 두 존을 동시에 탭하면 **서로 다른 셋업 2건**이 나온다. 심볼·TF·방향·시각만 찍던
    옛 라벨에서는 그 두 줄이 **복사된 것처럼** 보였다(사용자가 「같은 게 두 번 나온다 =
    버그 아닌가」로 읽은 자리 — 데이터는 맞고 표현이 틀렸다).

    존 정체성은 `(존 시작, 존 확정)`이다(`live.limit_engine.ZoneId`와 같은 정의) — 둘이
    같으면 같은 존이므로, 두 시각을 `MM-DD HH:MM`으로 함께 찍으면 서로 다른 존은 반드시
    다른 라벨을 얻는다. 여기에 탭 순번을 더해 **같은 존의 여러 탭**까지 가른다.
    """
    start = _mmdd_hhmm(comp.zone_start_time)
    confirmed = _mmdd_hhmm(comp.zone_confirmed_time)
    parts: list[str] = []
    if start and confirmed:
        parts.append(f"존 {start}→{confirmed}")
    elif confirmed:
        parts.append(f"존 확정 {confirmed}")
    elif start:
        parts.append(f"존 시작 {start}")
    if comp.tap_index is not None:
        parts.append(f"탭 {comp.tap_index}")
    return " · ".join(parts)


def _setup_label(comp: SetupComparison) -> str:
    """셋업 한 줄 라벨: `BTC·1h·롱·09:00 · 존 07-19 15:00→07-20 08:00 · 탭 1`.

    앞은 심볼·TF·방향·탭/체결 KST 시각, 뒤는 **존 정체성 + 탭 순번**(WAN-333 §1) — 뒤가
    없으면 서로 다른 존이 같은 라벨로 겹쳐 보인다(`_zone_tag` 독스트링).
    """
    side = "롱" if comp.is_long else "숏"
    when = _hhmm(comp.focus_ms)
    parts = [_short(comp.symbol), comp.timeframe, side]
    if when:
        parts.append(when)
    head = "·".join(parts)
    zone = _zone_tag(comp)
    return f"{head} · {zone}" if zone else head


def compare_rows_payload(result: SetupCompareResult) -> list[dict[str, object]]:
    """`SetupCompareResult`를 목업 JS가 읽는 행 배열로 (순수 — 화면 없이 테스트).

    각 행: `sym`(라벨) · `p`(페이퍼) · `b`(백테) · `diverge`(판정갈림) · `flag`(가격벗어남) ·
    `bps`(진입가 차이, 옵션 병기용) · `unpaired`(짝 없음, WAN-333). 정렬은
    `build_setup_comparisons`가 이미 시각순으로 했다.
    """
    payload: list[dict[str, object]] = []
    for comp in result.comparisons:
        payload.append(
            {
                "sym": _setup_label(comp),
                "p": _side_payload(comp.live, entered=comp.live_entered),
                "b": _side_payload(comp.backtest, entered=comp.backtest_entered),
                "diverge": comp.verdict_differs,
                "flag": comp.price_off,
                "bps": None if comp.entry_delta_bps is None else round(comp.entry_delta_bps, 2),
                # 🚨 짝이 없으면 「둘 다 미진입 · 매칭」으로 찍지 않는다(WAN-333) — 한쪽이
                # 아예 없는 것을 두 쪽이 같은 판정을 낸 것으로 읽히게 하면 안 된다.
                "unpaired": not comp.paired,
            }
        )
    return payload


def _bar_scale(payload: Sequence[dict[str, object]]) -> float:
    """Δ 막대 길이 기준(%p) — 데이터 최대 |Δ|와 0.5 중 큰 값(막대가 넘치지 않게)."""
    biggest = 0.5
    for row in payload:
        p = row["p"]
        b = row["b"]
        assert isinstance(p, dict) and isinstance(b, dict)
        pv, bv = p.get("v"), b.get("v")
        if isinstance(pv, (int, float)) and isinstance(bv, (int, float)):
            biggest = max(biggest, abs(float(pv) - float(bv)))
    return biggest


# 목업 팔레트(=`dashboard/lightweight_chart.py` 다크 테마). iframe 안 독립 HTML이라 CSS 변수로 둔다.
_CSS = """
:root{--bg:#131722;--panel:#1a2130;--panel-2:#20293b;--border:#2a3346;--text:#d1d4dc;
--muted:#787b86;--teal:#26a69a;--red:#ef5350;--amber:#f9a825;--accent:#42a5f5;
--bg-red:rgba(239,83,80,0.14);--bg-amber:rgba(249,168,37,0.12);--radius:8px;}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
font-size:14px;line-height:1.5;}
.wrap{padding:4px 2px 12px;}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;}
.card{background:var(--panel);border:0.5px solid var(--border);border-radius:var(--radius);
padding:10px 14px;min-width:110px;}
.card.alert{background:var(--bg-red);border-color:rgba(239,83,80,0.4);}
.card .lab{font-size:12px;color:var(--muted);}
.card.alert .lab{color:var(--red);}
.card.warn{background:var(--bg-amber);border-color:rgba(249,168,37,0.4);}
.card.warn .lab,.card.warn .num,.card.warn .hint{color:var(--amber);}
.card .num{font-size:22px;font-weight:500;}
.card.alert .num{color:var(--red);}
.card .hint{font-size:11px;color:var(--red);}
.chips{display:flex;gap:8px;margin-bottom:12px;}
.chip{font-size:13px;padding:6px 13px;border-radius:var(--radius);border:0.5px solid var(--border);
background:transparent;color:var(--muted);cursor:pointer;}
.chip.on{background:var(--panel-2);color:var(--text);border-color:#3a4560;}
.colhead{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:10px;padding:0 6px 8px;
font-size:12px;color:var(--muted);}
.colhead div{text-align:center;}
.srow{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:10px;align-items:center;
padding:12px 6px;border-bottom:0.5px solid var(--border);}
.srow.diverge{background:var(--bg-red);border-left:3px solid var(--red);}
.srow.unpaired{background:var(--bg-amber);border-left:3px solid var(--amber);}
.srow.diverge.unpaired{background:var(--bg-red);border-left:3px solid var(--red);}
.setup{grid-column:1 / -1;font-size:11px;color:var(--muted);padding:0 6px 4px;}
.side{background:var(--panel);border:0.5px solid var(--border);border-radius:var(--radius);
padding:7px 11px;}
.side .st{font-size:12px;color:var(--muted);}
.side .pn{font-size:15px;font-weight:500;}
.side .px{font-size:11px;color:var(--muted);}
.win{color:var(--teal);}.loss{color:var(--red);}
.bar-wrap{position:relative;height:28px;}
.bar-mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--border);}
.bar-seg{position:absolute;top:5px;height:9px;border-radius:3px;}
.bar-lab{position:absolute;top:16px;font-size:11px;white-space:nowrap;}
.tag{display:inline-block;font-size:12px;padding:3px 10px;border-radius:var(--radius);}
.tag.diverge{background:var(--bg-red);color:var(--red);}
.tag.unpaired{background:var(--bg-amber);color:var(--amber);}
.tag.match{color:var(--muted);}
.legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:14px;font-size:12px;color:var(--muted);}
.empty{color:var(--muted);font-size:13px;padding:16px 6px;}
"""

# 목업 JS(칩 필터·Δ 막대·세 신호)를 데이터 주입형으로 옮긴 것. `ROWS`/`MAX`는 파이썬이 채운다.
_JS = """
const cls=v=>v>=0?"win":"loss", sgn=v=>(v>=0?"+":"")+v.toFixed(2);
function side(o){
  if(o.v===null){
    const st = o.entered ? o.s+" · 손익 대기" : o.s;
    return '<div class="side"><span class="st">'+st+'</span></div>';
  }
  return '<div class="side"><span class="st">'+o.s+'</span><br>'
    +'<span class="pn '+cls(o.v)+'">'+sgn(o.v)+'%</span> <span class="px">@'+o.px+'</span></div>';
}
function center(r){
  if(r.diverge) return '<div style="text-align:center;">'
    +'<span class="tag diverge">⚠ 판정 갈림'+(r.unpaired?' · 짝 없음':'')+'</span></div>';
  // 🚨 짝이 없으면 「매칭」이라 말하지 않는다(WAN-333) — 한쪽이 아예 없는 것이지
  // 두 쪽이 같은 판정을 낸 게 아니다.
  if(r.unpaired) return '<div style="text-align:center;">'
    +'<span class="tag unpaired">짝 없음 · 대조 불가</span></div>';
  if(!r.p.entered && !r.b.entered)
    return '<div style="text-align:center;">'
      +'<span class="tag match">둘 다 미진입 · 매칭</span></div>';
  if(r.p.v===null || r.b.v===null)
    return '<div style="text-align:center;"><span class="tag match">진입 · 손익 대기</span></div>';
  const d=+(r.p.v-r.b.v).toFixed(2);
  const w=Math.min(Math.abs(d)/MAX,1)*46;
  const seg=d>=0?('left:50%; width:'+w+'%;'):('right:50%; width:'+w+'%;');
  const lab=d>=0?'left:52%;':'right:52%; text-align:right;';
  const who=d>=0?'페이퍼':'백테';
  const c=r.flag?'var(--amber)':'var(--accent)';
  let extra=r.flag?' · 확인':'';
  if(r.flag && r.bps!==null) extra+=' (진입가 Δ'+r.bps+'bp)';
  return '<div class="bar-wrap"><div class="bar-mid"></div>'
    +'<div class="bar-seg" style="'+seg+'background:'+c+';"></div>'
    +'<div class="bar-lab" style="'+lab+'color:'+c+';">'+(d>=0?'▲':'▼')+' '+who+' +'
    +Math.abs(d).toFixed(2)+'%p'+extra+'</div></div>';
}
function render(f){
  const rows=ROWS.filter(r=>f=="all"||(f=="diverge"?r.diverge
    :(f=="unpaired"?r.unpaired:(!r.diverge&&!r.unpaired))));
  const host=document.getElementById("rows");
  if(rows.length===0){
    host.innerHTML='<div class="empty">해당하는 셋업이 없습니다.</div>'; return;
  }
  host.innerHTML=rows.map(r=>'<div class="srow '+(r.diverge?'diverge ':'')
    +(r.unpaired?'unpaired':'')+'">'
    +'<div class="setup">'+r.sym+'</div>'
    +side(r.p)+center(r)+side(r.b)+'</div>').join('');
}
render("all");
document.querySelectorAll(".chip").forEach(c=>c.onclick=()=>{
  document.querySelectorAll(".chip").forEach(x=>x.classList.remove("on"));
  c.classList.add("on"); render(c.dataset.f);
});
"""


def setup_compare_html(result: SetupCompareResult, *, day_key: str) -> str:
    """셋업 대조 결과를 목업 정본의 3열 대조 HTML로 (iframe 임베드용, WAN-295).

    상단 요약 카드 + 필터 칩(전체/불일치만/일치/짝없음, 클라이언트 필터) + 셋업당 3열 행 +
    범례. 행이 없으면 안내 문구를 낸다. `day_key`는 제목에만 쓴다.

    📌 **「짝 없음」 카드·칩은 WAN-333이 넣었다** — 조인이 실패한 줄은 양쪽 다 「미진입」으로
    보여 옛 셈법에서 **「일치」로 집계**됐다(파리티가 깨진 것을 측정기가 「일치합니다」로
    보고하는 상태). 이제 그 줄은 주황 테두리 + 「짝 없음 · 대조 불가」로 따로 선다.

    ⚠️ 범례의 「per-cell 단일 포지션」 주의는 **알려진 근사**를 보이게 하는 것이다 — 페이퍼는
    레버리지 북(칸을 가로지르는 공유 자본, WAN-213)인데 대조 백테는 셀별 단일 포지션이라
    (WAN-234 규약: 셀별 화면이 북을 표현할 수 없다) **페이퍼가 「자리가 없어서」 못 들어간
    셋업을 백테는 그냥 들어간다**. 그 차이는 집행 차이가 아니라 회계 모델의 몫이다.
    """
    payload = compare_rows_payload(result)
    summary = result.summary
    rows_json = json.dumps(payload, ensure_ascii=False)
    max_scale = _bar_scale(payload)
    diverge_hint = (
        f"백테 진입·라이브 끊김 {summary.backtest_only_entered} · 반대 {summary.live_only_entered}"
    )
    price_off_note = f" · 🟠 가격 벗어남 {summary.price_off}" if summary.price_off else ""
    unpaired_hint = (
        f"페이퍼만 {summary.unpaired_live_only} · 백테만 {summary.unpaired_backtest_only}"
    )
    unpaired_card = (
        '<div class="card warn"><div class="lab">짝 없음 (대조 불가)</div>'
        f'<div class="num">{summary.unpaired}</div>'
        f'<div class="hint">{unpaired_hint}</div></div>'
        if summary.unpaired
        else ""
    )

    body = f"""
<div class="wrap">
  <div class="cards">
    <div class="card"><div class="lab">오늘 셋업</div><div class="num">{summary.total}</div></div>
    <div class="card"><div class="lab">일치 (짝지어짐 {summary.paired} 중)</div>
      <div class="num" style="color:var(--muted);">{summary.matched}</div></div>
    <div class="card alert"><div class="lab">불일치 (핵심 신호)</div>
      <div class="num">{summary.diverged}</div><div class="hint">{diverge_hint}</div></div>
    {unpaired_card}
  </div>
  <div class="chips">
    <button class="chip on" data-f="all">전체 {summary.total}</button>
    <button class="chip" data-f="diverge">불일치만 {summary.diverged}</button>
    <button class="chip" data-f="match">일치 {summary.matched}</button>
    <button class="chip" data-f="unpaired">짝 없음 {summary.unpaired}</button>
  </div>
  <div class="colhead"><div>페이퍼 (라이브)</div>
    <div>차이 (페이퍼 − 백테, %p)</div><div>백테스트</div></div>
  <div id="rows"></div>
  <div class="legend">
    <span><b style="color:var(--accent);">▬</b> 막대 = 높은 쪽으로 뻗음 (길수록 격차 큼)</span>
    <span style="color:var(--red);">● 판정 갈림 = 한쪽만 진입</span>
    <span style="color:var(--amber);">● 가격 벗어남 = 틱 오차 초과{price_off_note}</span>
    <span style="color:var(--amber);">● 짝 없음 = 한쪽에만 있는 셋업 (일치로 세지 않는다)</span>
    <span>대조 백테는 <b>per-cell 단일 포지션</b> — 북(공유 자본)의 용량 제약이 없다</span>
  </div>
</div>
"""
    # MAX를 먼저 치환하고 ROWS(주입 JSON)를 나중에 — JSON에 우연히 "MAX"가 있어도 안전.
    script = _JS.replace("MAX", f"{max_scale:.4f}", 1).replace("ROWS", rows_json, 1)
    return (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
        f"<title>셋업 대조 · {day_key}</title><style>{_CSS}</style></head>"
        f"<body>{body}<script>{script}</script></body></html>"
    )
