"""
에러 대응 콘솔 (Streamlit)

실행:  py -3 -m streamlit run ui/app.py      (incident-agent 폴더에서)
화면:  http://localhost:8501

  상단 상태 띠 : 서비스 전체 상태 (정상 / 주의 / 에러) — 5초마다 갱신
  서비스 상태  : 힙 메모리, DB 커넥션 풀, 오류 수, 추이 그래프, 최근 경고·오류 로그
  에러 재현    : 시나리오 S1~S12 켜기/끄기, 증상 일으키기 (시연·평가 준비용)
  원인 분석    : 증상 입력 → Agent 조사 과정(실시간) → 원인 보고서
"""
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st

import scoreboard as sb
from service_client import call, read_log_tail, snapshot

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
DEFAULT_LOG = ROOT / "order-service" / "logs" / "app.log"
ORDER_BODY = {"customerName": "콘솔테스트", "productName": "키보드", "category": "전자기기",
              "quantity": 1, "price": 89000}
INV_TEST_PRODUCT = ORDER_BODY["productName"]

# ---------------------------------------------------------------- 디자인 토큰
INK, MUTED, LINE, PAPER, PANEL = "#1D2433", "#5B6475", "#DDE1E8", "#F5F6F8", "#FFFFFF"
OK, WARN, FAULT, INFO = "#1E8E5A", "#C77A12", "#C8362F", "#2F5BD3"

st.set_page_config(page_title="에러 대응 콘솔", page_icon="🛠️", layout="wide")
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
html, body, [class*="css"], .stApp, button, input, textarea, select {{
  font-family: 'IBM Plex Sans KR', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif !important;
}}
.stApp {{ background: {PAPER}; color: {INK}; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.2rem; max-width: 1280px; }}
code, pre, .mono {{ font-family: 'IBM Plex Mono', Consolas, monospace !important; }}
.num {{ font-variant-numeric: tabular-nums; }}

/* 상단 상태 띠 */
.band {{ display:flex; align-items:center; gap:18px; padding:14px 22px; border-radius:6px; color:#fff; margin-bottom:18px; }}
.band .state {{ font-size:1.35rem; font-weight:700; letter-spacing:-0.01em; }}
.band .why {{ font-size:0.95rem; opacity:0.92; flex:1; }}
.band .meta {{ font-size:0.85rem; opacity:0.85; white-space:nowrap; }}
.band .pulse {{ width:12px; height:12px; border-radius:50%; background:#fff; box-shadow:0 0 0 0 rgba(255,255,255,.7); animation:pulse 2s infinite; }}
@keyframes pulse {{ 0% {{ box-shadow:0 0 0 0 rgba(255,255,255,.6); }} 70% {{ box-shadow:0 0 0 10px rgba(255,255,255,0); }} 100% {{ box-shadow:0 0 0 0 rgba(255,255,255,0); }} }}
@media (prefers-reduced-motion: reduce) {{ .band .pulse {{ animation:none; }} }}

.title {{ font-size:1.75rem; font-weight:700; color:{INK}; margin:4px 0 0; letter-spacing:-0.02em; line-height:1.3; }}
.subtitle {{ color:{MUTED}; font-size:0.95rem; margin:2px 0 6px; }}

/* 탭 */
button[data-baseweb="tab"] p {{ font-size:1rem; font-weight:600; }}
div[data-baseweb="tab-highlight"] {{ background:{INK}; }}

/* 지표 띠: 카드 대신 세로 구분선 */
.kpis {{ display:grid; grid-template-columns: 1.3fr 1.3fr 1fr 1fr; background:{PANEL}; border:1px solid {LINE}; border-radius:6px; }}
.kpi {{ padding:16px 20px; border-right:1px solid {LINE}; }}
.kpi:last-child {{ border-right:none; }}
.kpi .label {{ color:{MUTED}; font-size:0.85rem; margin-bottom:6px; }}
.kpi .value {{ font-size:1.9rem; font-weight:600; line-height:1.1; }}
.kpi .value small {{ font-size:0.95rem; color:{MUTED}; font-weight:500; }}
.kpi .hint {{ color:{MUTED}; font-size:0.8rem; margin-top:8px; }}
.bar {{ height:8px; background:#E9ECF1; border-radius:4px; margin-top:10px; overflow:hidden; }}
.bar > div {{ height:100%; border-radius:4px; }}
.slots {{ display:flex; gap:4px; margin-top:10px; }}
.slots span {{ flex:1; height:14px; border-radius:2px; background:#E9ECF1; }}
.slots span.on {{ background:{INK}; }}
.slots span.hot {{ background:{FAULT}; }}

[data-testid="stVegaLiteChart"] {{ border:1px solid {LINE}; border-radius:6px; overflow:hidden; background:{PANEL}; }}
.section {{ font-size:1.05rem; font-weight:700; color:{INK}; margin:26px 0 10px; }}
.section span {{ color:{MUTED}; font-weight:400; font-size:0.85rem; margin-left:8px; }}
.panel {{ background:{PANEL}; border:1px solid {LINE}; border-radius:6px; padding:6px 0; }}

/* 로그 */
.log {{ display:grid; grid-template-columns: 74px 64px 86px 1fr; gap:12px; padding:9px 16px; border-bottom:1px solid #EEF0F4; font-size:0.9rem; align-items:start; }}
.log:last-child {{ border-bottom:none; }}
.log .t, .log .rid {{ color:{MUTED}; font-variant-numeric: tabular-nums; }}
.log .rid {{ font-family:'IBM Plex Mono', Consolas, monospace; font-size:0.82rem; }}
.chip {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.75rem; font-weight:600; }}
.chip.ERROR {{ background:#FBE9E8; color:{FAULT}; }}
.chip.WARN {{ background:#FCF1E0; color:{WARN}; }}
.chip.INFO {{ background:#EDF0F5; color:{MUTED}; }}
.log details summary {{ cursor:pointer; list-style:none; }}
.log details summary::-webkit-details-marker {{ display:none; }}
.log details summary::after {{ content:" 스택 보기"; color:{INFO}; font-size:0.8rem; margin-left:6px; }}
.log details[open] summary::after {{ content:" 접기"; }}
.log pre {{ background:#F7F8FA; border:1px solid #EEF0F4; padding:10px; border-radius:4px; font-size:0.78rem; white-space:pre-wrap; margin:8px 0 0; max-height:260px; overflow:auto; }}
.empty {{ color:{MUTED}; padding:18px 16px; font-size:0.92rem; }}

/* 에러 재현 */
.scn-code {{ font-weight:700; color:{INK}; font-size:1rem; padding-top:6px; }}
.scn-title {{ font-weight:600; padding-top:6px; }}
.scn-hint {{ font-size:0.8rem; color:{MUTED}; margin-top:2px; }}
.scn-api {{ font-family:'IBM Plex Mono', Consolas, monospace; font-size:0.85rem; color:{MUTED}; padding-top:8px; }}
.scn-state {{ font-size:0.85rem; padding-top:8px; }}
.symptom-card {{ margin:2px 0 10px; padding:8px 12px; border-radius:6px; font-size:0.88rem; line-height:1.5; }}
.symptom-card.warn {{ background:#FBE9E8; color:{FAULT}; }}
.symptom-card.ok {{ background:#E8F3EC; color:{OK}; }}
.symptom-card.unknown {{ background:#EDF0F5; color:{MUTED}; }}
hr.row {{ border:none; border-top:1px solid {LINE}; margin:4px 0; }}

/* 원인 분석 */
.step {{ display:flex; gap:10px; padding:7px 0; font-size:0.92rem; border-bottom:1px dashed #E6E9EE; }}
.step .dot {{ width:9px; height:9px; border-radius:50%; margin-top:7px; flex:none; }}
.step .t {{ color:{MUTED}; font-variant-numeric: tabular-nums; width:62px; flex:none; }}
.step .d {{ color:{MUTED}; font-size:0.85rem; margin-top:2px; }}
.report {{ background:{PANEL}; border:1px solid {LINE}; border-left:6px solid var(--c); border-radius:6px; padding:22px 26px; }}
.report .head {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
.report .type {{ background:var(--c); color:#fff; padding:3px 12px; border-radius:12px; font-weight:600; font-size:0.85rem; }}
.report .loc {{ font-family:'IBM Plex Mono', Consolas, monospace; font-size:0.95rem; color:{INK}; background:#F1F3F7; padding:2px 8px; border-radius:4px; }}
.report .conf {{ color:{MUTED}; font-size:0.85rem; margin-left:auto; }}
.report .summary {{ font-size:1.25rem; font-weight:600; line-height:1.5; margin:16px 0 18px; max-width:70ch; }}
.report .cols {{ display:grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap:24px; margin-top:6px; }}
.report .evidence {{ margin-bottom:18px; }}
.report li, .report p {{ overflow-wrap:anywhere; }}
.report h4 {{ font-size:0.85rem; color:{MUTED}; font-weight:600; margin:0 0 8px; }}
.rb-badge {{ display:inline-block; background:#EAF1FB; color:{INFO}; border-radius:10px; padding:1px 9px;
  font-size:0.76rem; font-weight:600; margin-left:4px; vertical-align:middle; }}
.report ul {{ margin:0; padding-left:18px; }}
.report li {{ margin-bottom:8px; line-height:1.5; font-size:0.93rem; }}
.report p {{ margin:0 0 14px; line-height:1.55; font-size:0.93rem; }}
.note {{ background:#FCF6EA; border:1px solid #F0DDB5; color:#6B4A0E; border-radius:6px; padding:10px 14px; font-size:0.88rem; margin-top:14px; }}
.foot {{ color:{MUTED}; font-size:0.82rem; margin-top:14px; }}

/* LLM 원본 vs 보정 후 비교 */
.compare {{ margin-top:16px; }}
.compare h4 {{ font-size:0.85rem; color:{MUTED}; font-weight:600; margin:0 0 8px; }}
.compare .row {{ display:grid; grid-template-columns: 96px minmax(0,1fr) 28px minmax(0,1fr); gap:0 10px; align-items:center;
  font-size:0.88rem; padding:5px 0; border-bottom:1px dashed #E6E9EE; }}
.compare .row:last-child {{ border-bottom:none; }}
.compare .lbl {{ color:{MUTED}; font-size:0.82rem; }}
.compare .arrow {{ color:{MUTED}; text-align:center; }}
.compare .raw {{ color:{MUTED}; }}
.compare .fin {{ color:{INK}; font-weight:600; }}
.compare .fin.changed {{ color:{FAULT}; }}
.compare .cols2 {{ display:grid; grid-template-columns: minmax(0,1fr) 28px minmax(0,1fr); gap:0 10px; font-size:0.78rem;
  color:{MUTED}; margin-bottom:4px; }}
.compare .badge {{ display:inline-block; border-radius:10px; padding:2px 9px; font-size:0.78rem; font-weight:600; margin-left:8px; }}
.compare .badge.changed {{ background:#FBE9E8; color:{FAULT}; }}
.compare .badge.same {{ background:#E8F3EC; color:{OK}; }}

/* 정답률 스코어보드 */
.score {{ background:{PANEL}; border:1px solid {LINE}; border-radius:6px; padding:18px 22px; margin-top:16px; }}
.score .pair {{ display:grid; grid-template-columns: 1fr 1fr; gap:18px; }}
.score .card {{ padding:4px 0; }}
.score .card .label {{ color:{MUTED}; font-size:0.85rem; margin-bottom:4px; }}
.score .card .value {{ font-size:1.9rem; font-weight:700; line-height:1.1; }}
.score .card .value small {{ font-size:0.95rem; color:{MUTED}; font-weight:500; }}
.score .arrow2 {{ color:{MUTED}; font-size:1.3rem; align-self:center; text-align:center; }}
.score .bar {{ height:6px; border-radius:3px; background:#EEF0F4; margin-top:8px; overflow:hidden; }}
.score .bar > div {{ height:100%; }}
.score .rows {{ margin-top:14px; border-top:1px solid {LINE}; padding-top:10px; }}
.score .r {{ display:flex; gap:8px; font-size:0.82rem; padding:3px 0; color:{MUTED}; align-items:center; }}
.score .r .c {{ width:16px; text-align:center; flex:none; }}
.score .r .s {{ font-family:'IBM Plex Mono', Consolas, monospace; flex:none; width:28px; color:{INK}; }}
.score .hint {{ color:{MUTED}; font-size:0.78rem; margin-top:10px; }}

@media (max-width: 900px) {{
  .kpis {{ grid-template-columns: 1fr 1fr; }}
  .kpi:nth-child(2) {{ border-right:none; }}
  .report .cols {{ grid-template-columns: 1fr; }}
  .log {{ grid-template-columns: 64px 1fr; }}
  .log .rid, .log .lv {{ display:none; }}
}}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.markdown("**연결 설정**")
    base = st.text_input("서비스 주소", value=st.session_state.get("base", os.environ.get("SERVICE_URL", "http://localhost:8081")), key="base")
    inv_base = st.text_input("재고 서비스 주소", value=st.session_state.get("inv_base", os.environ.get("INVENTORY_BASE_URL", "http://localhost:8082")),
                              key="inv_base")
    log_path = st.text_input("로그 파일", value=str(DEFAULT_LOG), key="log_path")
    auto = st.toggle("5초마다 새로고침", value=True)
    if st.button("그래프 기록 지우기", use_container_width=True):
        st.session_state.history = []
    st.caption("서비스가 꺼져 있으면 start_all.bat 으로 한 번에 켤 수 있습니다.")

st.session_state.setdefault("history", [])
st.session_state.setdefault("traffic", [])
st.session_state.setdefault("agent_history", [])
st.session_state.setdefault("symptom_result", {})
st.session_state.setdefault("symptom_baseline", {})

esc = html.escape


# ---------------------------------------------------------------- 공통 계산
def recent_log_counts(seconds=60):
    entries = read_log_tail(log_path) or []
    since = datetime.now() - timedelta(seconds=seconds)
    err = warn = 0
    for e in entries:
        try:
            ts = datetime.strptime(e.ts[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts >= since and e.logger != "chaos":
            err += e.level == "ERROR"
            warn += e.level == "WARN"
    return err, warn


def overall_state(snap, err60, warn60):
    """상태 띠: 에러 / 주의 / 정상 과 그 이유."""
    if not snap.reachable:
        return "에러", FAULT, ["서비스에 연결할 수 없습니다"]
    reasons_fault, reasons_warn = [], []
    if snap.health != "UP":
        reasons_fault.append(f"서비스 상태 {snap.health}")
    if snap.conn_max and (snap.conn_active or 0) >= snap.conn_max - 1:
        reasons_fault.append(f"DB 커넥션 {snap.conn_active:.0f}/{snap.conn_max:.0f} 사용 중")
    heap_pct = (snap.heap_used_mb or 0) / snap.heap_max_mb * 100 if snap.heap_max_mb else 0
    if heap_pct >= 85:
        reasons_fault.append(f"힙 메모리 {heap_pct:.0f}%")
    elif heap_pct >= 40:
        reasons_warn.append(f"힙 메모리 {heap_pct:.0f}%")
    if err60:
        reasons_fault.append(f"최근 1분 오류 {err60}건")
    if warn60:
        reasons_warn.append(f"최근 1분 경고 {warn60}건")
    if reasons_fault:
        return "에러", FAULT, reasons_fault + reasons_warn
    if reasons_warn:
        return "주의", WARN, reasons_warn
    return "정상", OK, ["주요 지표와 로그에 이상 없음"]


# ---------------------------------------------------------------- 상단: 상태 띠 + 제목
@st.fragment(run_every=5 if auto else None)
def status_band():
    snap = snapshot(base)
    err60, warn60 = recent_log_counts()
    state, color, reasons = overall_state(snap, err60, warn60)
    st.session_state["last_snap"] = snap
    st.markdown(
        f"<div class='band' style='background:{color}'><div class='pulse'></div>"
        f"<div class='state'>{state}</div><div class='why'>{esc(' · '.join(reasons))}</div>"
        f"<div class='meta num'>order-service {esc(base.replace('http://', ''))} · {datetime.now():%H:%M:%S} 확인</div></div>",
        unsafe_allow_html=True)


status_band()
st.markdown("<div class='title'>에러 대응 콘솔</div>"
            "<div class='subtitle'>주문 서비스의 상태를 보고, 에러를 재현하고, AI Agent로 원인을 분석합니다.</div>",
            unsafe_allow_html=True)

tab_status, tab_chaos, tab_agent = st.tabs(["서비스 상태", "에러 재현", "원인 분석"])


# ---------------------------------------------------------------- 1) 서비스 상태
def fmt(v, d=0):
    return "-" if v is None else f"{v:,.{d}f}"


def kpi_block(snap):
    heap_pct = (snap.heap_used_mb or 0) / snap.heap_max_mb * 100 if snap.heap_max_mb else 0
    heap_color = FAULT if heap_pct >= 85 else WARN if heap_pct >= 40 else OK
    active, mx = int(snap.conn_active or 0), int(snap.conn_max or 10)
    hot = active >= mx - 1
    slots = "".join(f"<span class='{'hot' if hot else 'on'}'></span>" if i < active else "<span></span>"
                    for i in range(mx))
    return f"""
<div class='kpis num'>
  <div class='kpi'><div class='label'>힙 메모리</div>
    <div class='value'>{fmt(snap.heap_used_mb)}<small> / {fmt(snap.heap_max_mb)} MB</small></div>
    <div class='bar'><div style='width:{min(heap_pct, 100):.0f}%;background:{heap_color}'></div></div>
    <div class='hint'>사용률 {heap_pct:.0f}%</div></div>
  <div class='kpi'><div class='label'>DB 커넥션 풀</div>
    <div class='value'>{active}<small> / {mx} 사용 중</small></div>
    <div class='slots'>{slots}</div>
    <div class='hint'>{'거의 모두 사용 중 — 새 요청이 대기합니다' if hot else '여유 있음'}</div></div>
  <div class='kpi'><div class='label'>커넥션 대기 실패</div>
    <div class='value'>{fmt(snap.conn_timeouts)}</div>
    <div class='hint'>서비스 시작 후 누적</div></div>
  <div class='kpi'><div class='label'>서버 오류 (5xx)</div>
    <div class='value' style='color:{FAULT if (snap.server_errors or 0) else INK}'>{fmt(snap.server_errors)}</div>
    <div class='hint'>서비스 시작 후 누적</div></div>
</div>"""


def trend_chart(df, field, title, color, max_value=None, step=False):
    base_chart = alt.Chart(df).encode(
        x=alt.X("시각:T", axis=alt.Axis(title=None, format="%H:%M:%S", labelColor=MUTED, grid=False,
                                       domainColor=LINE, tickColor=LINE, labelFontSize=11)),
        y=alt.Y(f"{field}:Q", axis=alt.Axis(title=None, labelColor=MUTED, gridColor="#EEF0F4",
                                           domain=False, ticks=False, labelFontSize=11),
                scale=alt.Scale(domain=[0, max_value]) if max_value else alt.Undefined),
    )
    interp = "step-after" if step else "monotone"
    area = base_chart.mark_area(color=color, opacity=0.12, interpolate=interp)
    line = base_chart.mark_line(color=color, strokeWidth=2, interpolate=interp)
    layers = [area, line]
    if max_value:
        rule = alt.Chart(pd.DataFrame({"y": [max_value]})).mark_rule(
            color=FAULT, strokeDash=[4, 4], opacity=0.6).encode(y="y:Q")
        layers.append(rule)
    return alt.layer(*layers).properties(height=180, title=alt.TitleParams(
        title, anchor="start", color=INK, fontSize=13, fontWeight=600, offset=12)).configure_view(
        stroke=None).configure(background=PANEL, padding={"left": 14, "right": 18, "top": 14, "bottom": 10})


@st.fragment(run_every=5 if auto else None)
def status_panel():
    snap = snapshot(base)
    if not snap.reachable:
        st.markdown(f"<div class='panel'><div class='empty'>서비스({esc(base)})에 연결할 수 없습니다. "
                    "start_all.bat 으로 서비스를 켜거나, 왼쪽 설정의 주소와 포트를 확인하세요.</div></div>",
                    unsafe_allow_html=True)
        return
    hist = st.session_state.history
    hist.append({"시각": datetime.fromtimestamp(snap.taken_at), "힙": snap.heap_used_mb or 0,
                 "커넥션": snap.conn_active or 0})
    del hist[:-120]
    st.markdown(kpi_block(snap), unsafe_allow_html=True)
    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
    if len(hist) >= 2:
        df = pd.DataFrame(hist)
        c1, c2 = st.columns(2, gap="medium")
        c1.altair_chart(trend_chart(df, "힙", "힙 메모리 (MB)", INFO, snap.heap_max_mb), use_container_width=True)
        c2.altair_chart(trend_chart(df, "커넥션", "DB 커넥션 사용 수", INK, snap.conn_max, step=True),
                        use_container_width=True)
    else:
        st.caption("그래프는 몇 초 뒤부터 그려집니다.")


def render_logs(rows):
    if not rows:
        return "<div class='panel'><div class='empty'>조건에 맞는 로그가 없습니다.</div></div>"
    out = ["<div class='panel'>"]
    for e in rows:
        msg = esc(e.message[:220])
        if e.detail:
            body = f"<details><summary>{msg}</summary><pre>{esc(chr(10).join([e.message] + e.detail[:30]))}</pre></details>"
        else:
            body = msg
        out.append(f"<div class='log'><div class='t'>{e.ts[11:19]}</div>"
                   f"<div class='lv'><span class='chip {e.level}'>{e.level}</span></div>"
                   f"<div class='rid'>{esc(e.request_id)}</div><div>{body}</div></div>")
    out.append("</div>")
    return "".join(out)


with tab_status:
    status_panel()
    st.markdown("<div class='section'>최근 경고 · 오류 로그<span>요청 ID로 같은 요청의 로그를 이어 볼 수 있습니다</span></div>",
                unsafe_allow_html=True)
    f1, f2, f3 = st.columns([1.3, 2.4, 0.8])
    levels = f1.multiselect("레벨", ["ERROR", "WARN", "INFO"], default=["ERROR", "WARN"])
    keyword = f2.text_input("검색", placeholder="예외 이름, API 주소, 요청 ID")
    limit = f3.number_input("개수", min_value=10, max_value=300, value=30, step=10)
    entries = read_log_tail(log_path)
    if entries is None:
        st.markdown(f"<div class='panel'><div class='empty'>로그 파일이 아직 없습니다: {esc(log_path)}</div></div>",
                    unsafe_allow_html=True)
    else:
        rows = [e for e in entries if e.level in levels and e.logger != "chaos"]
        if keyword:
            k = keyword.lower()
            rows = [e for e in rows if k in e.message.lower() or k in e.request_id.lower()
                    or any(k in d.lower() for d in e.detail)]
        st.markdown(render_logs(rows[-int(limit):][::-1]), unsafe_allow_html=True)


# ---------------------------------------------------------------- 2) 에러 재현
LOG_PATTERNS = {
    "S1": ["OutOfMemoryError"],
    "S2": ["Connection is not available"],
    "S3": ["느린 요청 GET /api/orders/search"],
    "S4": ["mock-externl", "NotFound"],
    "S5": ["SocketTimeoutException"],
    "S6": ["NullPointerException"],
    "S7": ["느린 요청 POST /api/orders"],
    "S8": ["설정 갱신 적용 app.order.zone-id"],
    "S9": [],
    "S10": ["결제 승인 처리 중 예외 발생"],
    "S11": ["외부 결제 승인 요청"],
    "S12": ["한정 수량 재고 차감"],
    "S13": ["cardNumber="],
    "S14": ["적립 포인트 계산"],
    "S15": ["스레드 기반 알림 발송 완료"],
    "S16": ["외부 결제 승인 요청", "비멱등 확인 없음", "멱등성 확인 없음"],
    "S17": ["searchSummaryPaged"],
    "S18": ["Connection is not available", "OrderHikariPool"],
    "S19": [],
    "S20": [],
    "S21": ["SocketTimeoutException", "read timed out"],
    "S22": [],
    "S23": ["max-quantity"],
    "S24": ["Cannot deserialize", "JsonMappingException", "HttpMessageNotReadableException",
            "MismatchedInputException"],
    "S25": ["500 Internal Server Error", "mock-external"],
    "S26": ["429"],
    "S27": ["503"],
    "S28": ["502"],
    "S29": ["intermittent_failure"],
    "S30": ["401", "서명 검증 실패"],
}


def _recent_log_lines(code, since_dt, limit=4):
    """요청을 보낸 시점(since_dt) 이후에 남은, 이 시나리오와 관련된 로그 줄만 골라 돌려준다."""
    patterns = LOG_PATTERNS.get(code) or []
    if not patterns:
        return []
    entries = read_log_tail(log_path)
    if not entries:
        return []
    out = []
    for e in entries:
        try:
            ts = datetime.strptime(e.ts, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            continue
        if ts < since_dt:
            continue
        if any(p in e.message for p in patterns):
            out.append(f"{e.ts[11:19]} {e.level:5s} {e.message}")
    return out[-limit:]


def _analyze_symptom(code, calls):
    """calls: [(label, status, elapsed_sec, response), ...]. 사람이 읽을 증상 판정을 돌려준다.
    verdict: 'warn'(증상 확인됨) / 'ok'(정상) / 'unknown'(판단 불가, 로그 확인 필요)"""
    if any(c[1] in (-1, 0) for c in calls):
        return "unknown", "서비스에 연결할 수 없었습니다. order-service가 실행 중인지 확인해주세요."

    if code in ("S1", "S2"):
        fails = [c for c in calls if isinstance(c[1], int) and c[1] >= 500]
        if fails:
            metric_name = "jvm.memory.used" if code == "S1" else "hikaricp.connections.active"
            return "warn", (f"{len(fails)}/{len(calls)}건이 실패(5xx)했습니다 — "
                             f"{'메모리 누수' if code == 'S1' else 'DB 커넥션 고갈'} 증상입니다. "
                             f"'서비스 상태' 탭에서 {metric_name} 추이를 확인해보세요.")
        return "unknown", f"{len(calls)}건 모두 정상 응답입니다. 아직 임계치에 도달하지 않았을 수 있어요 — 버튼을 몇 번 더 눌러보세요."

    if code == "S3":
        elapsed = calls[0][2]
        if elapsed >= 2:
            return "warn", f"응답 시간 {elapsed:.1f}초 (정상은 0.1초 이내) — 응답 지연 증상이 나타났습니다."
        return "ok", f"응답 시간 {elapsed:.2f}초로 정상 범위입니다."

    if code == "S4":
        status = calls[0][1]
        if isinstance(status, int) and status >= 400:
            return "warn", f"상태 코드 {status} — 설정값 오타로 결제 API가 즉시 실패했습니다."
        return "ok", f"상태 코드 {status} — 정상 응답입니다."

    if code == "S5":
        status, elapsed = calls[0][1], calls[0][2]
        if isinstance(status, int) and status >= 500 and elapsed >= 1.5:
            return "warn", f"{elapsed:.1f}초 대기 후 {status} 응답 — 외부 API 타임아웃 증상입니다."
        return "unknown", f"상태 코드 {status}, {elapsed:.1f}초 — 이번엔 증상이 안 보일 수 있습니다. 다시 시도해보세요."

    if code == "S6":
        status = calls[0][1]
        if isinstance(status, int) and status >= 500:
            return "warn", f"상태 코드 {status} — 쿠폰 없는 주문에서 예외 처리 누락(NPE) 증상이 나타났습니다."
        return "unknown", f"상태 코드 {status} — 이 주문에서는 증상이 안 보였습니다. 다시 시도해보세요."

    if code == "S7":
        elapsed = calls[0][2]
        if elapsed >= 2:
            return "warn", f"주문 생성에 {elapsed:.1f}초 소요 (정상은 0.1초 이내) — 비동기 작업을 동기로 기다리는 증상입니다."
        return "ok", f"응답 시간 {elapsed:.2f}초로 정상 범위입니다."

    if code == "S8":
        body = calls[0][3]
        ordered_at = body.get("orderedAt") if isinstance(body, dict) else None
        if ordered_at:
            try:
                dt = datetime.fromisoformat(ordered_at)
                diff_hours = (datetime.now() - dt).total_seconds() / 3600
            except ValueError:
                return "unknown", "응답에서 생성 시각을 읽지 못했습니다."
            if abs(diff_hours) >= 5:
                return "warn", (f"생성 시각이 {ordered_at[11:19]}로, 현재 시각과 약 {abs(diff_hours):.0f}시간 "
                                 f"차이가 납니다 — 타임존 설정 오류 증상입니다.")
            return "ok", f"생성 시각 {ordered_at[11:19]} — 정상 범위입니다."
        return "unknown", "응답에서 생성 시각을 읽지 못했습니다."

    if code == "S9":
        first_body, second_body = calls[0][3], calls[2][3]
        if isinstance(first_body, list) and isinstance(second_body, list):
            first_n = sum(c.get("orderCount", 0) for c in first_body)
            second_n = sum(c.get("orderCount", 0) for c in second_body)
            if first_n == second_n:
                return "warn", f"신규 주문 후에도 검색 결과 건수가 {first_n}건으로 그대로입니다 — 캐시 미반영 증상입니다."
            return "ok", f"검색 결과가 {first_n}건 → {second_n}건으로 정상 반영되었습니다."
        return "unknown", "검색 결과를 비교하지 못했습니다."

    if code == "S10":
        body = calls[0][3]
        approval = body.get("approval") if isinstance(body, dict) else None
        if isinstance(approval, dict) and approval.get("result") == "UNKNOWN":
            return "warn", "결제는 'PAID'로 성공했지만 승인 결과가 UNKNOWN입니다 — 예외를 삼키고 가짜 성공 처리한 증상입니다."
        return "ok", "결제 승인이 정상적으로 완료되었습니다 (approval.result=APPROVED)."

    if code == "S11":
        body = calls[0][3]
        approval = body.get("approval") if isinstance(body, dict) else None
        if isinstance(approval, dict) and approval.get("result") == "APPROVED":
            return "unknown", ("결제는 정상 승인됐습니다(응답만으로는 증상이 안 보임). 내부적으로 승인 요청이 "
                                "최대 4배 더 발생했을 수 있어요 — '서비스 상태' 탭 로그에서 "
                                "'외부 결제 승인 요청' 반복 횟수를 확인해보세요.")
        return "unknown", "결제 응답을 확인하지 못했습니다."

    if code == "S12":
        entries = read_log_tail(log_path)
        vals = []
        if entries:
            pattern = re.compile(r"남은수량\(추정\)=(-?\d+)")
            matches = [e.message for e in entries if "한정 수량 재고 차감" in e.message]
            for m in matches[-len(calls):]:
                mo = pattern.search(m)
                if mo:
                    vals.append(int(mo.group(1)))
        if vals:
            if len(vals) != len(set(vals)):
                return "warn", f"동시 주문 {len(vals)}건의 재고 차감 로그 값이 중복됩니다 ({vals}) — 동시성 유실(lost update) 증상입니다."
            return "unknown", f"재고 차감 로그 값이 모두 달랐습니다 ({vals}). 다시 시도해보세요."
        return "unknown", "로그에서 재고 차감 기록을 찾지 못했습니다. '서비스 상태' 탭에서 직접 확인해보세요."

    if code == "S13":
        status = calls[0][1]
        if isinstance(status, int) and status < 400:
            return "warn", "결제는 정상 처리됐지만, 로그에 카드번호가 평문으로 그대로 남았을 수 있습니다 — 아래 로그를 확인하세요."
        return "unknown", f"상태 코드 {status} — 응답만으로는 판단할 수 없습니다. 아래 로그를 확인하세요."

    if code == "S14":
        status = calls[0][1]
        if isinstance(status, int) and status < 400:
            return "warn", ("결제는 정상 처리됐지만, 적립 포인트를 double로 계산해 반올림 오차가 생겼을 수 있습니다 — "
                            "아래 로그의 points 값을 확인하세요.")
        return "unknown", f"상태 코드 {status} — 응답만으로는 판단할 수 없습니다."

    if code == "S15":
        fails = [c for c in calls if isinstance(c[1], int) and c[1] >= 500]
        if fails:
            return "warn", f"{len(fails)}/{len(calls)}건이 실패(5xx)했습니다 — 스레드 누적으로 인한 영향일 수 있습니다."
        return "unknown", (f"{len(calls)}건 모두 정상 응답입니다. 스레드 누수는 응답에 바로 드러나지 않습니다 — "
                           f"운영체제에서 프로세스의 스레드 수(jstack 등)를 확인해보세요.")

    if code == "S16":
        status = calls[0][1]
        if isinstance(status, int) and status < 400:
            return "warn", ("결제는 정상 승인됐지만, 멱등성 확인 없이 같은 승인 요청이 한 번 더 나갔을 수 있습니다 — "
                            "아래 로그에서 '외부 결제 승인 요청'이 중복으로 찍혔는지 확인하세요.")
        return "unknown", f"상태 코드 {status} — 응답만으로는 판단할 수 없습니다."

    if code == "S17":
        elapsed = calls[0][2]
        if elapsed >= 1.5:
            return "warn", f"응답 시간 {elapsed:.1f}초 (정상은 0.1초 이내) — 종료 조건 누락으로 반복이 길어지는 증상입니다."
        return "ok", f"응답 시간 {elapsed:.2f}초로 정상 범위입니다."

    if code == "S18":
        fails = [c for c in calls if isinstance(c[1], int) and c[1] >= 500]
        slow = [c for c in calls if isinstance(c[2], (int, float)) and c[2] >= 1.5]
        if fails:
            return "warn", f"{len(fails)}/{len(calls)}건이 실패(5xx)했습니다 — DB 커넥션 풀 고갈 증상입니다."
        if slow:
            return "warn", (f"{len(slow)}/{len(calls)}건이 느리게 응답했습니다(최대 {max(c[2] for c in slow):.1f}초) — "
                            f"커넥션 풀이 작아 대기한 것으로 보입니다.")
        return "unknown", f"{len(calls)}건 모두 빠르게 정상 처리됐습니다. 다시 시도해보세요."

    if code == "S19":
        return "unknown", ("응답 자체에는 증상이 나타나지 않습니다. '서비스 상태' 탭에서 로그 레벨이 TRACE로 바뀐 뒤 "
                           "로그 줄 수가 평소보다 크게 늘었는지 확인해보세요.")

    if code == "S20":
        body = calls[0][3]
        if isinstance(body, dict):
            order = body.get("order") or {}
            coupon_code, label = order.get("couponCode"), body.get("couponLabel")
            if coupon_code and label == "쿠폰 없음":
                return "warn", f"주문에 쿠폰 코드({coupon_code})가 있는데도 '쿠폰 없음'으로 표시됩니다 — 기능 플래그 오설정 증상입니다."
            if coupon_code:
                return "ok", f"쿠폰이 정상적으로 표시됩니다 ({label})."
            return "unknown", "이 주문에는 쿠폰 코드가 없어 증상을 확인할 수 없습니다. 다시 시도해보세요."
        return "unknown", "응답을 확인하지 못했습니다."

    if code == "S21":
        status, elapsed = calls[0][1], calls[0][2]
        if isinstance(status, int) and status >= 500:
            return "warn", f"상태 코드 {status} — 결제 타임아웃 설정값이 너무 짧아 즉시 실패하는 증상입니다."
        return "unknown", f"상태 코드 {status}, {elapsed:.1f}초 — 증상이 안 보일 수 있습니다. 다시 시도해보세요."

    if code == "S22":
        elapsed = calls[0][2]
        if elapsed >= 1.5:
            return "warn", f"응답 시간 {elapsed:.1f}초 (정상은 0.1초 이내) — 레거시 검색 경로로 전환된 증상입니다."
        return "ok", f"응답 시간 {elapsed:.2f}초로 정상 범위입니다."

    if code == "S23":
        status = calls[0][1]
        if isinstance(status, int) and status >= 500:
            return "warn", f"상태 코드 {status} — 허용 수량(max-quantity) 설정값이 비정상적으로 작아 정상 주문도 거부되는 증상입니다."
        return "ok", f"상태 코드 {status} — 정상 처리되었습니다."

    if code == "S24":
        status = calls[0][1]
        if isinstance(status, int) and status >= 500:
            return "warn", f"상태 코드 {status} — 외부 PG 응답 스키마가 바뀌어(배열 응답) 역직렬화에 실패하는 증상입니다."
        return "unknown", f"상태 코드 {status} — 증상이 안 보일 수 있습니다. 다시 시도해보세요."

    if code in ("S25", "S27", "S28", "S30"):
        status = calls[0][1]
        label = {"S25": "500(서버 오류)", "S27": "503(점검중)", "S28": "502(게이트웨이 오류)",
                 "S30": "401(인증 실패)"}[code]
        if isinstance(status, int) and status >= 500:
            return "warn", (f"상태 코드 {status} — 외부 PG가 {label} 응답을 돌려주는 증상입니다. "
                            f"아래 로그에서 실제 외부 응답 코드를 확인하세요.")
        return "unknown", f"상태 코드 {status} — 증상이 안 보일 수 있습니다. 다시 시도해보세요."

    if code == "S26":
        status = calls[0][1]
        if isinstance(status, int) and status >= 500:
            return "warn", "상태 코드 500 — 외부 PG 레이트 리밋(429)에 걸리는 증상입니다. 아래 로그에서 429 응답을 확인하세요."
        return "unknown", f"상태 코드 {status} — 증상이 안 보일 수 있습니다. 다시 시도해보세요."

    if code == "S29":
        fails = [c for c in calls if isinstance(c[1], int) and c[1] >= 500]
        if fails:
            return "warn", f"{len(fails)}/{len(calls)}건이 간헐적으로 실패했습니다 — 외부 PG 간헐적 실패 증상입니다."
        return "unknown", f"{len(calls)}건 모두 정상입니다 (확률적으로 실패 안 할 수도 있음). 다시 시도해보세요."

    return "unknown", "이 시나리오는 증상 자동 판정을 지원하지 않습니다."


def generate(code):
    start_dt = datetime.now()
    calls = []
    if code in ("S1", "S2"):
        for _ in range(10 if code == "S1" else 5):
            calls.append(("POST /api/orders",) + call(base, "POST", "/api/orders", ORDER_BODY, timeout=15))
    elif code == "S3":
        calls.append(("GET /api/orders/search",) + call(base, "GET", "/api/orders/search", timeout=60))
    elif code in ("S4", "S5"):
        calls.append(("POST /api/payments",) + call(base, "POST", "/api/payments", {"orderId": 3}, timeout=15))
    elif code == "S6":
        _, _, orders = call(base, "GET", "/api/orders")
        target = next((o["id"] for o in orders or [] if o.get("couponCode") is None), 2)
        calls.append((f"GET /api/orders/{target}",) + call(base, "GET", f"/api/orders/{target}"))
    elif code in ("S7", "S8"):
        calls.append(("POST /api/orders",) + call(base, "POST", "/api/orders", ORDER_BODY, timeout=15))
    elif code == "S9":
        calls.append(("GET /api/orders/search (최초 조회)",)
                      + call(base, "GET", "/api/orders/search", timeout=30))
        calls.append(("POST /api/orders (신규 주문)",)
                      + call(base, "POST", "/api/orders", ORDER_BODY, timeout=15))
        calls.append(("GET /api/orders/search (재조회)",)
                      + call(base, "GET", "/api/orders/search", timeout=30))
    elif code in ("S10", "S11"):
        calls.append(("POST /api/payments",) + call(base, "POST", "/api/payments", {"orderId": 3}, timeout=15))
    elif code == "S12":
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(call, base, "POST", "/api/orders",
                               {**ORDER_BODY, "quantity": 5}, 15) for _ in range(8)]
            for i, f in enumerate(futs):
                calls.append((f"POST /api/orders (동시 요청 #{i + 1})",) + f.result())
    elif code in ("S13", "S14", "S16", "S21"):
        calls.append(("POST /api/payments",) + call(base, "POST", "/api/payments", {"orderId": 3}, timeout=15))
    elif code == "S15":
        for _ in range(10):
            calls.append(("POST /api/orders",) + call(base, "POST", "/api/orders", ORDER_BODY, timeout=15))
    elif code in ("S17", "S22"):
        calls.append(("GET /api/orders/search",) + call(base, "GET", "/api/orders/search", timeout=60))
    elif code == "S18":
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(call, base, "POST", "/api/orders", ORDER_BODY, 15) for _ in range(6)]
            for i, f in enumerate(futs):
                calls.append((f"POST /api/orders (동시 요청 #{i + 1})",) + f.result())
    elif code == "S19":
        calls.append(("POST /api/orders",) + call(base, "POST", "/api/orders", ORDER_BODY, timeout=15))
    elif code == "S20":
        _, _, orders = call(base, "GET", "/api/orders")
        target = next((o["id"] for o in orders or [] if o.get("couponCode")), 2)
        calls.append((f"GET /api/orders/{target}",) + call(base, "GET", f"/api/orders/{target}"))
    elif code == "S23":
        calls.append(("POST /api/orders",) + call(base, "POST", "/api/orders", ORDER_BODY, timeout=15))
    elif code in ("S24", "S25", "S26", "S27", "S28", "S30"):
        calls.append(("POST /api/payments",) + call(base, "POST", "/api/payments", {"orderId": 3}, timeout=15))
    elif code == "S29":
        for _ in range(4):
            calls.append(("POST /api/payments",) + call(base, "POST", "/api/payments", {"orderId": 3}, timeout=15))
    now = datetime.now().strftime("%H:%M:%S")
    st.session_state.traffic = [{"시각": now, "시나리오": code, "요청": r[0], "상태 코드": r[1],
                                 "걸린 시간(초)": round(r[2], 2)} for r in calls] + st.session_state.traffic
    del st.session_state.traffic[50:]
    verdict, text = _analyze_symptom(code, calls)
    log_lines = _recent_log_lines(code, start_dt)
    baseline = st.session_state.symptom_baseline.get(code) if verdict == "warn" else None
    if verdict == "ok":
        st.session_state.symptom_baseline[code] = text
    st.session_state.symptom_result[code] = {
        "time": now, "verdict": verdict, "text": text, "log_lines": log_lines, "baseline": baseline,
    }


def set_chaos(code):
    on = st.session_state[f"chaos_{code}"]
    call(base, "POST", f"/chaos/{code}/{'on' if on else 'off'}")


def set_chaos_inv(code):
    """order-service의 /chaos와 별개로, 재고 서비스(inventory-service) 자신의 M1/M2를 켜고 끈다."""
    on = st.session_state[f"inv_chaos_{code}"]
    call(inv_base, "POST", f"/chaos/{code}/{'on' if on else 'off'}")


SCN_HINT = {"S1": "주문 10건", "S2": "주문 5건", "S3": "검색 1회", "S4": "결제 1회", "S5": "결제 1회", "S6": "상세 조회 1회",
            "S7": "주문 1건", "S8": "주문 1건", "S9": "검색→주문→재검색", "S10": "결제 1회", "S11": "결제 1회",
            "S12": "동시 주문 8건",
            "S13": "결제 1회", "S14": "결제 1회", "S15": "주문 10건", "S16": "결제 1회", "S17": "검색 1회",
            "S18": "동시 주문 6건", "S19": "주문 1건", "S20": "상세 조회 1회", "S21": "결제 1회", "S22": "검색 1회",
            "S23": "주문 1건", "S24": "결제 1회", "S25": "결제 1회", "S26": "결제 1회", "S27": "결제 1회",
            "S28": "결제 1회", "S29": "결제 4회", "S30": "결제 1회"}

SCN_SYMPTOM = {
    "S1": "👀 켜고 10건 넘게 보내면 일부가 500으로 실패해야 정상",
    "S2": "👀 켜고 5건 보내면 일부가 3초 대기 후 500으로 실패해야 정상",
    "S3": "👀 켜면 응답이 4초 이상 걸려야 정상 (에러는 안 남)",
    "S4": "👀 켜면 즉시 404/500으로 실패해야 정상",
    "S5": "👀 켜면 약 2초 대기 후 500으로 실패해야 정상",
    "S6": "👀 켜면 쿠폰 없는 주문 조회가 500으로 실패해야 정상",
    "S7": "👀 켜면 주문 생성이 4초 이상 걸려야 정상 (에러는 안 남)",
    "S8": "👀 켜면 생성 시각이 실제보다 9시간 앞서야 정상 (에러는 안 남)",
    "S9": "👀 켜면 신규 주문 후에도 검색 건수가 그대로여야 정상 (에러는 안 남)",
    "S10": "👀 켜면 결제는 성공(PAID)하지만 승인 결과가 UNKNOWN이어야 정상 (에러는 안 남)",
    "S11": "👀 켜면 결제는 정상 승인되지만 로그상 승인 호출이 여러 번 찍혀야 정상",
    "S12": "👀 켜고 동시에 보내면 재고 차감 로그 값이 중복되어야 정상(유실)",
    "S13": "👀 켜면 결제는 정상 처리되지만 로그에 카드번호가 평문으로 남아야 정상 (응답은 정상)",
    "S14": "👀 켜면 결제는 정상 처리되지만 적립 포인트 계산에 반올림 오차가 생겨야 정상 (응답은 정상)",
    "S15": "👀 켜고 10건 보내면 응답은 정상이지만 스레드가 누적되어야 정상 (jstack으로 확인)",
    "S16": "👀 켜면 결제는 정상 승인되지만 로그상 승인 호출이 중복으로 찍혀야 정상",
    "S17": "👀 켜면 검색 응답이 비정상적으로 오래 걸려야 정상 (에러는 안 남)",
    "S18": "👀 켜고 동시에 보내면 일부가 느려지거나 500으로 실패해야 정상",
    "S19": "👀 켜면 응답은 정상이지만 로그 줄 수가 평소보다 크게 늘어야 정상 (에러는 안 남)",
    "S20": "👀 켜면 쿠폰이 있는 주문도 '쿠폰 없음'으로 표시돼야 정상 (에러는 안 남)",
    "S21": "👀 켜면 결제가 거의 즉시 타임아웃으로 실패해야 정상",
    "S22": "👀 켜면 검색 응답이 느려져야 정상 (레거시 경로, 에러는 안 남)",
    "S23": "👀 켜면 정상 수량 주문도 500으로 실패해야 정상",
    "S24": "👀 켜면 결제가 응답 스키마 오류로 실패해야 정상",
    "S25": "👀 켜면 결제가 외부 PG 500 오류로 실패해야 정상",
    "S26": "👀 켜면 결제가 외부 PG 429(레이트 리밋)로 실패해야 정상",
    "S27": "👀 켜면 결제가 외부 PG 503(점검중)로 실패해야 정상",
    "S28": "👀 켜면 결제가 외부 PG 502(게이트웨이 오류)로 실패해야 정상",
    "S29": "👀 켜면 결제가 간헐적으로(확률적으로) 실패해야 정상",
    "S30": "👀 켜면 결제가 외부 PG 인증 실패(401)로 실패해야 정상",
}

with tab_chaos:
    st.markdown("<p class='subtitle'>시연과 평가 준비용 화면입니다. 에러를 켠 뒤 요청을 보내면 증상이 나타납니다. "
                "Agent는 이 화면과 에러 스위치를 볼 수 없습니다.</p>", unsafe_allow_html=True)

    # 실시간 지표 — 별도 fragment가 아니라 매번 다시 그려지므로, 아래에서 "요청 보내기"를 누르면
    # (st.rerun으로 전체 스크립트가 다시 실행되면서) 여기 숫자·그래프가 바로 그 변화를 반영한다.
    chaos_snap = snapshot(base)
    if chaos_snap.reachable:
        st.markdown("<div class='section'>실시간 지표<span>요청을 보내면 여기 숫자·그래프가 바로 바뀝니다</span></div>",
                    unsafe_allow_html=True)
        st.markdown(kpi_block(chaos_snap), unsafe_allow_html=True)
        chaos_hist = st.session_state.history
        if len(chaos_hist) >= 2:
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            cdf = pd.DataFrame(chaos_hist)
            cc1, cc2 = st.columns(2, gap="medium")
            cc1.altair_chart(trend_chart(cdf, "힙", "힙 메모리 (MB)", INFO, chaos_snap.heap_max_mb),
                              use_container_width=True)
            cc2.altair_chart(trend_chart(cdf, "커넥션", "DB 커넥션 사용 수", INK, chaos_snap.conn_max, step=True),
                              use_container_width=True)
        st.markdown("<hr class='row'>", unsafe_allow_html=True)

    status, _, scenarios = call(base, "GET", "/chaos")
    if status != 200:
        st.markdown("<div class='panel'><div class='empty'>서비스에 연결할 수 없어 에러 목록을 불러오지 못했습니다.</div></div>",
                    unsafe_allow_html=True)
    else:
        widths = [0.5, 1.5, 2.1, 0.9, 1.6]
        h = st.columns(widths)
        for col, name in zip(h, ["", "에러 유형", "영향받는 API", "켜기", "증상 일으키기"]):
            col.markdown(f"<span style='color:{MUTED};font-size:0.85rem'>{name}</span>", unsafe_allow_html=True)
        for s in scenarios:
            st.markdown("<hr class='row'>", unsafe_allow_html=True)
            try:
                c = st.columns(widths, vertical_alignment="center")
            except TypeError:  # 오래된 Streamlit
                c = st.columns(widths)
            c[0].markdown(f"<div class='scn-code'>{s['code']}</div>", unsafe_allow_html=True)
            state_txt = f"<span style='color:{FAULT};font-weight:600'>켜짐</span>" if s["on"] else \
                f"<span style='color:{MUTED}'>꺼짐</span>"
            c[1].markdown(f"<div class='scn-title'>{esc(s['title'])} &nbsp;<span class='scn-state'>{state_txt}</span></div>"
                          f"<div class='scn-hint'>{esc(SCN_SYMPTOM.get(s['code'], ''))}</div>",
                          unsafe_allow_html=True)
            c[2].markdown(f"<div class='scn-api'>{esc(s['target'])}</div>", unsafe_allow_html=True)
            st.session_state[f"chaos_{s['code']}"] = s["on"]
            c[3].toggle("켜기", key=f"chaos_{s['code']}", on_change=set_chaos, args=(s["code"],),
                        label_visibility="collapsed")
            if c[4].button(f"요청 보내기 ({SCN_HINT[s['code']]})", key=f"gen_{s['code']}", use_container_width=True):
                with st.spinner(f"{s['code']} 요청을 보내는 중입니다..."):
                    generate(s["code"])
                st.rerun()
            r = st.session_state.symptom_result.get(s["code"])
            if r:
                icon = {"warn": "⚠️ 증상 확인됨", "ok": "✅ 정상(증상 없음)", "unknown": "❓ 판단 보류"}[r["verdict"]]
                body = f"<b>{icon}</b> ({r['time']}) — {esc(r['text'])}"
                if r.get("baseline"):
                    body += f"<br><span style='opacity:0.75'>↳ 꺼져 있을 때 기준: {esc(r['baseline'])}</span>"
                st.markdown(f"<div class='symptom-card {r['verdict']}'>{body}</div>", unsafe_allow_html=True)
                if r.get("log_lines"):
                    st.code("\n".join(r["log_lines"]), language=None)
        st.markdown("<hr class='row'>", unsafe_allow_html=True)
        b1, b2, _ = st.columns([1.2, 1.2, 3])
        if b1.button("모든 에러 끄기", type="primary", use_container_width=True):
            call(base, "POST", "/chaos/reset")
            st.rerun()
        if b2.button("요청 기록 지우기", use_container_width=True):
            st.session_state.traffic = []
            st.rerun()

    st.markdown("<div class='section'>재고 서비스(inventory-service)"
                "<span>order-service가 주문 생성 시 호출하는 외부 서비스입니다 — 여기서 켜면 재고 조회·주문 생성 양쪽에 전파됩니다</span></div>",
                unsafe_allow_html=True)
    inv_status, _, inv_scenarios = call(inv_base, "GET", "/chaos")
    if inv_status != 200:
        st.markdown("<div class='panel'><div class='empty'>재고 서비스에 연결할 수 없어 목록을 불러오지 못했습니다. "
                    "재고 서비스 주소(사이드바)와 실행 상태를 확인해주세요.</div></div>", unsafe_allow_html=True)
    else:
        iw = [0.5, 2.3, 2.3, 0.9, 1.6]
        ih = st.columns(iw)
        for col, name in zip(ih, ["", "에러 유형", "영향받는 API", "켜기", "증상 일으키기"]):
            col.markdown(f"<span style='color:{MUTED};font-size:0.85rem'>{name}</span>", unsafe_allow_html=True)
        for s in inv_scenarios:
            st.markdown("<hr class='row'>", unsafe_allow_html=True)
            try:
                c = st.columns(iw, vertical_alignment="center")
            except TypeError:  # 오래된 Streamlit
                c = st.columns(iw)
            c[0].markdown(f"<div class='scn-code'>{s['code']}</div>", unsafe_allow_html=True)
            state_txt = f"<span style='color:{FAULT};font-weight:600'>켜짐</span>" if s["on"] else \
                f"<span style='color:{MUTED}'>꺼짐</span>"
            c[1].markdown(f"<div class='scn-title'>{esc(s['title'])} &nbsp;<span class='scn-state'>{state_txt}</span></div>",
                          unsafe_allow_html=True)
            c[2].markdown(f"<div class='scn-api'>{esc(s['target'])}</div>", unsafe_allow_html=True)
            st.session_state[f"inv_chaos_{s['code']}"] = s["on"]
            c[3].toggle("켜기", key=f"inv_chaos_{s['code']}", on_change=set_chaos_inv, args=(s["code"],),
                        label_visibility="collapsed")
            if c[4].button(f"재고 조회 보내기 ({INV_TEST_PRODUCT})", key=f"inv_gen_{s['code']}", use_container_width=True):
                with st.spinner("재고 조회 요청을 보내는 중입니다..."):
                    # 상품명에 한글이 들어있으므로 URL 경로에 넣기 전에 퍼센트 인코딩해야 한다.
                    # (주문 본문은 JSON이라 그대로 utf-8로 보내지지만, 경로에 그대로 넣으면
                    # Windows에서 urllib이 요청 라인을 만들 때 UnicodeEncodeError가 난다.)
                    inv_rstatus, inv_elapsed, _ = call(inv_base, "GET",
                                                        f"/api/inventory/{quote(INV_TEST_PRODUCT)}")
                if inv_rstatus in (-1, 0):
                    icon, verdict = "❓ 판단 보류", "unknown"
                    text = "재고 서비스에 연결할 수 없었습니다." if inv_rstatus == -1 else "응답 시간이 초과되었습니다."
                elif isinstance(inv_rstatus, int) and inv_rstatus >= 500:
                    icon, verdict = "⚠️ 증상 확인됨", "warn"
                    text = f"상태 코드 {inv_rstatus} — 응답 시간 {inv_elapsed:.2f}초로 재고 서비스 쪽 오류가 전파되었습니다."
                else:
                    icon, verdict = "✅ 정상(증상 없음)", "ok"
                    text = f"상태 코드 {inv_rstatus} — 응답 시간 {inv_elapsed:.2f}초로 정상 범위입니다."
                st.session_state[f"inv_symptom_{s['code']}"] = {
                    "time": datetime.now().strftime("%H:%M:%S"), "icon": icon, "verdict": verdict, "text": text}
                st.rerun()
            r = st.session_state.get(f"inv_symptom_{s['code']}")
            if r:
                st.markdown(f"<div class='symptom-card {r['verdict']}'><b>{r['icon']}</b> ({r['time']}) — "
                            f"{esc(r['text'])}</div>", unsafe_allow_html=True)
        st.markdown("<hr class='row'>", unsafe_allow_html=True)
        if st.button("재고 서비스 에러 모두 끄기", key="inv_reset", use_container_width=True):
            call(inv_base, "POST", "/chaos/reset")
            st.rerun()

    if st.session_state.traffic:
        st.markdown("<div class='section'>보낸 요청 결과</div>", unsafe_allow_html=True)
        df = pd.DataFrame(st.session_state.traffic)
        st.dataframe(
            df.style.map(lambda v: f"color:{FAULT};font-weight:600" if isinstance(v, int) and v >= 500 else "",
                         subset=["상태 코드"]).format({"걸린 시간(초)": "{:.2f}"}),
            hide_index=True, use_container_width=True)


# ---------------------------------------------------------------- 3) 원인 분석
CAUSE_COLOR = {"코드 결함": FAULT, "설정 오류": WARN, "외부 시스템": INFO, "기타": MUTED}
STEP_COLOR = {"start": MUTED, "triage": INK, "tool": INFO, "think": "#7A5AF8", "done": OK}


def ollama_models(url):
    try:
        import urllib.request as _ur
        with _ur.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.loads(r.read().decode("utf-8")).get("models", [])]
        return [n for n in names if not any(k in n.lower() for k in ("bge", "embed"))]
    except Exception:  # noqa: BLE001
        return []


def _compare_html(raw, final):
    """LLM이 처음에 낸 결론과, 코드 검증을 거쳐 최종 확정된 결론을 나란히 보여준다."""
    if not raw:
        return ""
    fields = [("cause_type", "원인 유형"), ("location", "위치"), ("confidence", "확신도")]
    changed_any = False
    rows = []
    for key, label in fields:
        rv, fv = str(raw.get(key) or "-"), str(final.get(key) or "-")
        changed = rv != fv
        changed_any = changed_any or changed
        fin_cls = "fin changed" if changed else "fin"
        rows.append(f"<div class='row'><div class='lbl'>{esc(label)}</div><div class='raw'>{esc(rv)}</div>"
                    f"<div class='arrow'>→</div><div class='{fin_cls}'>{esc(fv)}</div></div>")
    badge = (f"<span class='badge changed'>코드가 보정함</span>" if changed_any
             else f"<span class='badge same'>보정 없음 (LLM 결론 그대로)</span>")
    return f"""
<div class='compare'>
  <h4>LLM 원본 판단 → 최종 확정 {badge}</h4>
  <div class='cols2'><div>LLM 단독 (1차 결론)</div><div></div><div>Agent 최종 (코드 보정 후)</div></div>
  {''.join(rows)}
</div>"""


def report_html(res, symptom, at):
    r = res.get("report") or {}
    raw = res.get("raw_report") or {}
    ct = r.get("cause_type", "기타")
    ev = "".join(f"<li>{esc(str(x))}</li>" for x in r.get("evidence", [])) or "<li>근거 없음</li>"
    notes = [w for w in res.get("warnings", [])]
    note_html = "".join(f"<div class='note'>{esc(w)}</div>" for w in notes)
    t = res.get("timings", {})
    llm_calls = sum(1 for x in res.get("tool_log", []) if x.get("by") == "LLM")
    fix = r.get("fix", "-")
    act = r.get("immediate_action", "-")
    fix_html = f"<div><h4>근본 해결</h4><p>{esc(fix)}</p></div>" if fix and fix != act else ""
    rb_ref = r.get("runbook_ref")
    rb_badge = (f"<span class='rb-badge'>📘 사내 매뉴얼 {esc(rb_ref['id'])} · {esc(rb_ref['title'])}</span>"
                if rb_ref else "")
    return f"""
<div class='report' style='--c:{CAUSE_COLOR.get(ct, MUTED)}'>
  <div class='head'><span class='type'>{esc(ct)}</span><span class='loc'>{esc(r.get('location', '-'))}</span>
    <span class='conf'>확신도 {esc(r.get('confidence', '-'))}</span></div>
  <div class='summary'>{esc(r.get('summary', '결론을 내지 못했습니다.'))}</div>
  <div class='evidence'><h4>근거</h4><ul>{ev}</ul></div>
  <div class='cols'>
    <div><h4>지금 할 조치 {rb_badge}</h4><p>{esc(act)}</p></div>
    {fix_html}
  </div>
  {_compare_html(raw, r)}
  {note_html}
  <div class='foot num'>{at} 분석 · 증상 “{esc(symptom)}” · 모델 {esc(str(res.get('model')))} ·
    {t.get('total', '-')}초 · 자동 수집 {len(res.get('tool_log', [])) - llm_calls}회, Agent 추가 조사 {llm_calls}회</div>
</div>"""


def scoreboard_html(rows):
    """에러 재현 화면에서 '켜져 있던 시나리오가 정확히 1개'였던 분석들만 모아 LLM 단독 vs
    Agent 최종 정답률을 비교한다. (Agent 코드는 이 채점 결과를 전혀 보지 못한다 — scoreboard.py 참고)"""
    s = sb.summary(rows)
    if not s["graded"]:
        return ("<div class='score'><div class='hint'>아직 채점 가능한 기록이 없습니다. "
                "'에러 재현' 탭에서 시나리오를 하나만 켠 상태로 '원인 분석'을 돌리면 여기에 누적됩니다.</div></div>")

    def card(label, ok, n, color):
        pct = round(100 * ok / n) if n else 0
        return f"""<div class='card'>
  <div class='label'>{esc(label)}</div>
  <div class='value'>{pct}%<small>&nbsp;({ok}/{n})</small></div>
  <div class='bar'><div style='width:{pct}%;background:{color}'></div></div>
</div>"""

    recent = rows[:8]
    row_html = "".join(
        f"<div class='r'><div class='c'>{'✅' if r.get('final_correct') else ('❌' if r.get('expected') else '–')}</div>"
        f"<div class='s'>{esc(','.join(r.get('active_scenarios') or ['-']))}</div>"
        f"<div>{esc(r.get('llm_cause_type') or '-')} → {esc(r.get('final_cause_type') or '-')}</div>"
        f"<div style='margin-left:auto;opacity:0.8'>{esc(r.get('at',''))}</div></div>"
        for r in recent)
    return f"""
<div class='score'>
  <div class='pair'>
    {card("LLM 단독 정답률", s["llm_ok"], s["graded"], FAULT)}
    {card("Agent 최종 정답률", s["final_ok"], s["graded"], OK)}
  </div>
  <div class='rows'>{row_html}</div>
  <div class='hint'>시나리오 1개만 켜둔 상태로 돌린 분석 {s["graded"]}건 기준 (전체 기록 {s["total"]}건 중) ·
    채점은 결론이 다 나온 뒤 /chaos 상태를 조회해 별도로 매기며, Agent는 이 결과를 보지 못합니다.</div>
</div>"""


with tab_agent:
    from incident_agent.config import AgentConfig
    from incident_agent.graph import run_agent

    left, right = st.columns([1, 1.5], gap="large")
    with left:
        st.markdown("<div class='section' style='margin-top:6px'>증상</div>", unsafe_allow_html=True)
        symptom = st.text_area("증상", label_visibility="collapsed", height=110,
                               placeholder="사용자가 겪는 문제를 그대로 적어 주세요.\n예: 주문 검색이 너무 느려요. 에러는 안 납니다.")
        o1, o2 = st.columns(2)
        window = o1.slider("로그 범위(분)", 1, 60, 5,
                           help="이전 에러 기록이 섞이지 않도록, 지금 에러가 시작된 시점을 포함하는 정도로 짧게 잡으세요.")
        CLAUDE_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
        with st.expander("모델 설정"):
            use_claude = st.radio("LLM", ["Ollama (로컬)", "Claude API"], horizontal=True) == "Claude API"
            if use_claude:
                ollama_url, models = "", []
                model = st.selectbox("Claude 모델", CLAUDE_MODELS)
                claude_key = st.text_input(
                    "Anthropic API 키", value=os.environ.get("ANTHROPIC_API_KEY", ""), type="password",
                    help="api.anthropic.com으로 직접 호출합니다 (사내망에서 외부 HTTPS가 막혀 있으면 호출이 실패할 수 있습니다). "
                         "키는 이 화면 세션에만 쓰이고 저장되지 않습니다.")
            else:
                claude_key = ""
                ollama_url = st.text_input("Ollama 주소", value="http://localhost:11434")
                models = ollama_models(ollama_url)
                model = st.selectbox("모델", models or ["qwen2.5:3b"],
                                     index=(models.index("qwen2.5:3b") if "qwen2.5:3b" in models else 0))
        if not use_claude and not models:
            st.markdown(f"<div class='note'>Ollama({esc(ollama_url)})에 연결할 수 없습니다. Ollama를 실행한 뒤 새로고침하세요.</div>",
                        unsafe_allow_html=True)
        if use_claude and not claude_key:
            st.markdown("<div class='note'>Anthropic API 키를 입력하세요.</div>", unsafe_allow_html=True)
        ready = (bool(claude_key) if use_claude else bool(models))
        run = st.button("원인 분석 시작", type="primary", use_container_width=True,
                        disabled=not ready or not symptom.strip())
        st.caption("CPU에서는 1~3분 걸립니다. 분석 중에는 다른 탭을 누르지 마세요.")
        steps_box = st.container()

    with right:
        st.markdown("<div class='section' style='margin-top:6px'>원인 보고서</div>", unsafe_allow_html=True)
        report_box = st.empty()

    if run:
        cfg = AgentConfig()
        cfg.service_url, cfg.log_window_min = base, window
        if use_claude:
            cfg.provider, cfg.model, cfg.anthropic_api_key = "claude", model, claude_key
        else:
            cfg.provider, cfg.ollama_url, cfg.model = "ollama", ollama_url, model
        cfg.log_path = Path(log_path)
        with steps_box:
            st.markdown("<div class='section'>조사 과정</div>", unsafe_allow_html=True)

            def emit(e):
                d = ""
                if e.get("detail") and e["kind"] == "think":
                    d = f"<div class='d'>{esc(str(e['detail'])[:200])}</div>"
                st.markdown(f"<div class='step'><div class='dot' style='background:{STEP_COLOR.get(e['kind'], MUTED)}'></div>"
                            f"<div class='t'>{e['time']}</div><div>{esc(e['title'])}{d}</div></div>",
                            unsafe_allow_html=True)
                if e.get("detail") and e["kind"] in ("tool", "triage"):
                    with st.expander("Agent가 본 자료"):
                        st.code(str(e["detail"])[:4000], language=None)

            report_box.markdown("<div class='panel'><div class='empty'>Agent가 조사 중입니다...</div></div>",
                                unsafe_allow_html=True)
            try:
                res = run_agent(symptom.strip(), cfg, emit)
                at = datetime.now().strftime("%H:%M:%S")
                # 채점용: 결론이 다 나온 '뒤에' 지금 켜져 있는 시나리오를 조회한다 (Agent는 이 호출을 모른다)
                _, _, chaos_list = call(base, "GET", "/chaos")
                active_codes = [s["code"] for s in chaos_list if s.get("on")] if isinstance(chaos_list, list) else []
                sb.record(symptom.strip(), res.get("raw_report") or {}, res.get("report") or {}, active_codes, at)
                st.session_state.agent_history.insert(0, {"at": at, "symptom": symptom.strip(), "result": res})
            except Exception as ex:  # noqa: BLE001
                report_box.markdown(f"<div class='note'>분석에 실패했습니다: {esc(type(ex).__name__)}: {esc(str(ex))}</div>",
                                    unsafe_allow_html=True)

    with right:
        if st.session_state.agent_history:
            h0 = st.session_state.agent_history[0]
            report_box.markdown(report_html(h0["result"], h0["symptom"], h0["at"]), unsafe_allow_html=True)
            with st.expander("Agent가 본 전체 자료"):
                st.code(h0["result"].get("context", ""), language=None)
            if len(st.session_state.agent_history) > 1:
                st.markdown("<div class='section'>이전 분석</div>", unsafe_allow_html=True)
                for h in st.session_state.agent_history[1:6]:
                    r = h["result"].get("report") or {}
                    with st.expander(f"{h['at']}  {r.get('cause_type', '')}  {r.get('location', '')}  —  {h['symptom'][:30]}"):
                        st.markdown(report_html(h["result"], h["symptom"], h["at"]), unsafe_allow_html=True)
        elif not run:
            report_box.markdown("<div class='panel'><div class='empty'>왼쪽에 증상을 적고 <b>원인 분석 시작</b>을 누르면 "
                                "Agent가 지표·로그·코드를 조사해 여기에 원인과 조치를 정리합니다.</div></div>",
                                unsafe_allow_html=True)
        st.markdown("<div class='section' style='margin-top:18px'>정답률 누적 기록</div>", unsafe_allow_html=True)
        st.markdown(scoreboard_html(list(reversed(sb.load_rows()))), unsafe_allow_html=True)
