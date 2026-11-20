"""
장애 대응 Agent 흐름 (LangGraph)

  기초 조사 ─▶ 증거 수집 ─▶ 추가 조사(LLM) ─┬─▶ 도구 실행 ─▶ 추가 조사 (최대 N회)
   (코드)       (코드)                        └─▶ 결론(LLM, JSON) ─▶ 검증(코드) ─▶ 끝

- 기초 조사·증거 수집은 코드가 항상 수행한다 (작은 모델이 빠뜨리기 쉬운 기본 확인).
- LLM은 '무엇을 더 볼지'와 '원인이 무엇인지'를 판단한다.
- 결론은 정해진 JSON 형식으로 받고, 코드가 근거·위치를 다시 확인한다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from . import runbook
from .config import AgentConfig
from .llm import chat
from .tools import TOOL_SPECS, Tools, run_tool

CAUSE_TYPES = ["코드 결함", "설정 오류", "외부 시스템", "기타"]

EXT_FAILURE_MARKERS = ("timed out", "timeout", "connection refused", "connectexception", "refused",
                       " 503", " 502", " 500 ", " 429", " 401", "too many requests", "rate limit",
                       "parse", "parsing", "malformed", "unexpected character", "jsonparseexception",
                       "deserializ", "jsonmappingexception", "httpmessagenotreadable",
                       "mismatchedinput", "cannot construct",
                       "서명", "signature", "unauthorized", "forbidden", "인증 실패")


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "check_code_defect": {"type": "boolean", "description": "[코드 점검]에서 결함 패턴이 발견되었는가"},
        "check_config_changed": {"type": "boolean", "description": "첫 오류 직전에 관련 설정 변경이 있었는가"},
        "check_external_timeout": {"type": "boolean", "description": "외부 서비스 호출이 시간 초과로 실패했는가"},
        "summary": {"type": "string", "maxLength": 160, "description": "원인 한 문장"},
        "cause_type": {"type": "string", "enum": CAUSE_TYPES},
        "location": {"type": "string", "maxLength": 80, "description": "코드 결함이면 클래스.메서드, 설정 오류면 설정 키, 외부 시스템이면 시스템 이름"},
        "evidence": {"type": "array", "items": {"type": "string", "maxLength": 120}, "minItems": 1, "maxItems": 4},
        "immediate_action": {"type": "string", "maxLength": 160},
        "fix": {"type": "string", "maxLength": 160},
        "confidence": {"type": "string", "enum": ["높음", "보통", "낮음"]},
    },
    "required": ["check_code_defect", "check_config_changed", "check_external_timeout", "summary", "cause_type", "location", "evidence", "immediate_action", "fix", "confidence"],
}

SYSTEM_PROMPT = """너는 Spring Boot 주문 서비스의 장애 원인을 조사하는 SRE Agent다.
조사 결과의 수치·로그·코드만 근거로 판단하고, 보이지 않는 내용은 추측하지 않는다.
원인은 다음 중 하나로 구분한다: 코드 결함, 설정 오류, 외부 시스템, 기타.
- 코드 결함: 우리 코드의 잘못 (자원 미반환, 무한 누적, null 처리 누락, 비효율 반복 등)
- 설정 오류: 코드는 정상인데 설정값이 잘못됨
- 외부 시스템: 우리가 호출하는 외부 서비스의 지연·장애 (우리 코드 수정으로 해결되지 않음)
주의: DB 커넥션 풀(HikariPool), JVM 힙 메모리, 스레드는 외부 시스템이 아니라 우리 서비스 내부 자원이다.
내부 자원이 고갈됐다면 대개 그 자원을 잘못 쓰는 우리 코드(코드 결함)가 원인이다.
모든 답은 한국어로 쓴다."""

INVESTIGATE_PROMPT = """위 기초 조사로 원인을 확정할 수 있는지 판단하라.
- 정보가 부족하면 도구를 호출해 추가로 확인하라 (한 번에 최대 3개).
- 이미 충분하면 도구를 호출하지 말고 '충분'이라고만 답하라."""

CONCLUDE_PROMPT = """지금까지의 조사 결과로 원인 보고서를 JSON으로 작성하라.
먼저 세 가지를 확인해 check_ 항목에 true/false로 답하라:
- check_code_defect: [코드 점검]에 결함 패턴(자원 미반환, 계속 누적, null 확인 없음, 반복문 안 조회 등)이 있는가 ('오류 발생' 줄 표시만 있는 것은 결함 아님)
- check_config_changed: [자동 관찰]에 '첫 오류 직전 설정 변경'이 있는가
- check_external_timeout: 외부 호출이 시간 초과(timed out)로 실패했는가
그 다음 판단 순서:
1. [코드 점검] 결과에 결함이 있고 오류 위치와 일치하면 → 코드 결함 (location은 그 메서드)
2. 오류 직전에 [최근 설정 변경]이 있고 오류가 그 설정과 관련되면 → 설정 오류 (location은 설정 키)
3. 외부 서비스 호출이 시간 초과되었고 우리 코드·설정에 문제가 없으면 → 외부 시스템

- summary: 무엇이 어떻게 잘못되어 장애가 났는지 한 문장으로 (코드 결함이면 [코드 점검]에서 찾은 문제를 포함)
- cause_type: 코드 결함 / 설정 오류 / 외부 시스템 / 기타 중 하나
- location: 코드 결함이면 '클래스.메서드' (예: OrderService.create), 설정 오류면 설정 키, 외부 시스템이면 시스템 이름
- evidence: 위 조사 결과에 실제로 있는 수치·로그·코드 줄을 2~4개, 각각 한 줄로 짧게 인용
- immediate_action: 지금 당장 서비스를 정상화할 조치 (fix와 다르게)
- fix: 재발을 막는 근본 해결 (코드 결함이면 어떻게 고칠지 구체적으로)
- confidence: 높음 / 보통 / 낮음"""


class AgentState(TypedDict, total=False):
    raw_report: dict
    defects: list
    defects_multi: list
    frames: list
    symptom: str
    facts: dict
    context: str
    messages: list
    round: int
    pending_calls: list
    tool_log: list
    report: dict
    warnings: list
    timings: dict


def _j(x):
    return json.dumps(x, ensure_ascii=False, default=str)


def _err_content_text(sample):
    """에러 샘플(dict)에서 실제 내용(메시지·스택 프레임·원인)만 뽑아 소문자 텍스트로 합친다.
    _j(sample)처럼 dict를 통째로 JSON 직렬화하면 "level", "time", "logger" 같은 필드 이름
    자체가 텍스트에 섞여 들어가 설정 키와 우연히 겹치는 문제가 있어 (예: logging.level.*),
    매칭용 텍스트는 반드시 이 함수로 만들어야 한다."""
    parts = [sample.get("message") or ""]
    parts.extend(sample.get("app_frames") or [])
    parts.extend(sample.get("caused_by") or [])
    return " ".join(parts).lower()


def _fmt_num(v):
    return "-" if v is None else (f"{v:.0f}" if isinstance(v, float) else str(v))


def build_context(symptom, f):
    """기초 조사 결과를 LLM이 읽기 쉬운 짧은 글로 정리."""
    s = f["status"]
    focus = f.get("focus") or {}
    lines = [f"[증상] {symptom or '(입력 없음)'}"]
    kind_ko = {"memory": "메모리", "connection": "DB 커넥션", "slow": "응답 지연",
               "concurrency": "동시성(경쟁 상태)", "cache": "캐시 미반영",
               "timezone": "시간/타임존"}.get(focus.get("kind"))
    if kind_ko or focus.get("apis"):
        lines.append(f"[증상에서 읽은 관심 영역] {kind_ko or '-'} / 관련 API: {', '.join(focus.get('apis', [])) or '-'}")
    lines.append("")
    if not s.get("reachable"):
        lines.append(f"[서비스 상태] 응답 없음 — {s.get('error')}")
    else:
        lines.append(
            f"[서비스 상태] health {s['health']}, DB {s['db']}, 힙 메모리 {_fmt_num(s['heap_used_mb'])}/"
            f"{_fmt_num(s['heap_max_mb'])}MB ({s.get('heap_usage_pct', '-')}%), DB 커넥션 사용 "
            f"{_fmt_num(s['db_conn_active'])}/{_fmt_num(s['db_conn_max'])} (대기 {_fmt_num(s['db_conn_pending'])}, "
            f"커넥션 획득 실패 누적 {_fmt_num(s['db_conn_timeout_total'])})")
    lines.append("")
    lines.append("[API별 요청 통계] (서비스 시작 이후 누적, 최대값은 최근 기준)")
    for r in f["requests"][:6] or []:
        lines.append(f"- {r['api']}: {r['count']}건, 평균 {r['avg_sec']}초, 최근 최대 {r['max_sec_recent']}초, "
                     f"서버 오류 {r['server_errors']}건")
    if not f["requests"]:
        lines.append("- (요청 기록 없음)")
    lines.append("")
    lines.append(f"[주요 경고/오류 로그] (최근 {f['window_min']}분, 같은 유형끼리 묶음, 오류부터 최근 발생 순)")
    current = [g for g in f["signatures"] if g.get("current", True)]
    stale_n = len(f["signatures"]) - len(current)
    if not current:
        lines.append("- (지금 발생 중인 경고/오류 없음)")
    for i, g in enumerate(current[:4], 1):
        smp = g["sample"]
        tag = ""
        lines.append(f"{i}) {g['level']} {g['count']}건 ({g['first']}~{g['last']}, 마지막 발생 "
                     f"{_age_text(g.get('age_sec', 0))}){tag}: {smp['message'][:220]}")
        if smp.get("app_frames"):
            short = [fr.replace("com.example.orderservice.", "") for fr in smp["app_frames"]]
            lines.append(f"   발생 위치: {' ← '.join(short)}")
        for c in smp.get("caused_by", [])[:1]:
            lines.append(f"   원인 예외: {c}")
    if stale_n:
        lines.append(f"- (90초 넘게 지났고 증상과 무관해 보이는 이전 오류 {stale_n}종류는 제외함)")
    lines.append("")
    lines.append("[최근 설정 변경]")
    lines += [f"- {c['time']} {c['message']}" for c in f["config_changes"]] or ["- (없음)"]
    if f["observations"]:
        lines.append("")
        lines.append("[자동 관찰] (수치 기준으로 눈에 띄는 점, 원인 판단은 아님)")
        lines += [f"- {o}" for o in f["observations"]]
    return "\n".join(lines)


FOCUS_KINDS = [
    ("memory", ("메모리", "힙", "heap", "oom", "outofmemory")),
    ("connection", ("커넥션", "connection", "db ", "디비", "데이터베이스")),
    # S21(결제 타임아웃 설정값 과소 — 실제로는 우리 쪽 read-timeout이 너무 짧은데 증상만 보면
    # "외부 결제사가 응답이 없다"는 외부 시스템 장애처럼 보인다)처럼, "타임아웃" 단어가 들어간
    # 증상은 "slow"(느려요)보다 먼저 이 kind로 분류해야 한다 — 순서가 뒤에 있으면 "느려지거나
    # 타임아웃" 같은 문구에서 "느려"가 먼저 걸려 slow kind로 빠지고, 이 kind는 영영 안 쓰인다.
    ("timeout", ("타임아웃", "time out", "timeout", "시간 초과", "응답이 없", "응답을 못 받",
                "응답을 안 주")),
    ("slow", ("느려", "느림", "느리", "지연", "오래 걸", "늦")),
    ("concurrency", ("동시에", "동시 요청", "동시성", "레이스", "race condition", "race",
                     "수량이 안 맞", "재고가 안 맞", "재고가 이상", "수량이 이상")),
    ("cache", ("캐시", "반영이 안", "반영 안", "반영이 되", "새로고침해도", "새로 고침해도",
              "갱신이 안", "갱신 안", "업데이트가 안", "최신화가 안", "최신화 안")),
    ("timezone", ("시간이 이상", "시각이 이상", "시간대", "타임존", "timezone", "time zone",
                 "시간이 안 맞", "시간이 안맞", "날짜가 이상", "시간이 달라")),
    ("retry", ("재시도", "retry", "여러 번 호출", "여러 번 일어나", "중복 호출", "반복 호출", "백오프", "backoff")),
    # S11(실패와 무관하게 미리 여러 번 호출하는 "재시도 폭주")과는 달리, S16은 "실패했다가 재시도해서
    # 중복 승인/이중 청구가 났다"는 증상이다 — 표현이 겹치지 않게 별도 kind로 둔다 (아래 _defect_kind의
    # "idempotency" 분기와 짝을 이룬다). 안 그러면 "retry" kind 하나로 묶여 S11/S16 결함이 뒤섞인다.
    ("idempotency", ("중복 승인", "이중 청구", "중복 청구", "두 번 나가", "두 번 나간", "중복으로 나가",
                     "멱등성", "비멱등", "중복 처리", "중복 결제", "이중 결제")),
    ("log", ("로그가 많이", "로그가 너무", "로그가 쌓", "로그가 계속", "로그 양이", "로그량",
             "로그 용량", "로그 파일이 커", "디스크가 차", "디스크 용량",
             "트레이스 로그", "log level", "로그 레벨")),
    # "쿠폰"이 들어간 증상 문구는 표현이 다양해서("쿠폰이 없다고 나와요", "쿠폰이 안 보여요",
    # "할인이 적용 안 돼요" 등) 구체적인 문구 목록으로는 계속 빠지는 사례가 나온다 — 이 kind가
    # 쿠폰 관련 증상 전용이라 다른 kind와 겹칠 일이 없으므로, 아예 "쿠폰" 한 단어로 넓게 잡는다.
    ("coupon", ("쿠폰",)),
]

# 에러가 전혀 안 나는(조용한) 설정 오류를 symptom_focus 카테고리별로 잡기 위한 키워드.
# observe()에서 '직전 오류' 없이도 최근 설정 변경이 이 카테고리와 관련 있는지 판단할 때 쓴다.
KIND_CONFIG_HINTS = {
    "timezone": ("zone", "시간", "tz"),
    "log": ("log", "trace", "로그"),
    "coupon": ("coupon", "쿠폰"),
    "slow": ("search", "legacy", "검색", "레거시"),
    # S18(DB 커넥션 풀 크기 과소 설정)처럼 트래픽이 가볍거나 커넥션이 금방 반환되면 실제
    # ERROR 로그(커넥션 타임아웃 예외) 없이도 재현되는 "조용한" 설정 오류가 있다 — 이 경우
    # errors 분기가 아예 안 타므로, connection kind도 이 조용한-설정-오류 경로로 잡아준다.
    "connection": ("hikari", "pool", "커넥션", "connection", "datasource"),
    # S21(결제 read-timeout 과소 설정)도 마찬가지로 로컬 환경에서는 응답이 1ms보다 빨리 와서
    # 실제 타임아웃 예외가 거의 안 난다 — "조용한" 설정 오류 경로로 잡아준다.
    "timeout": ("timeout", "타임아웃", "read-timeout", "payment"),
}
FOCUS_APIS = [  # (증상 단어, API 문자열 일부, 코드/로그에서 찾을 단어)
    (("결제", "결재", "payment"), "/api/payments", "payment"),
    (("검색", "search"), "/api/orders/search", "search"),
    (("상세",), "/api/orders/{id}", "detail"),
    (("주문 생성", "주문이 안", "주문 등록", "주문하기", "주문 생성이", "주문이 동시", "동시에 주문",
      "재고", "수량이 안 맞", "수량이 이상",
      "주문이 몰리", "주문이 많아", "주문 처리가", "주문 처리는", "요청이 몰리"), "POST /api/orders", ".create("),
]


def symptom_focus(symptom):
    t = (symptom or "").lower()
    kind = next((k for k, words in FOCUS_KINDS if any(w in t for w in words)), None)
    apis = [(api, key) for words, api, key in FOCUS_APIS if any(w in t for w in words)]
    return {"kind": kind, "apis": [a for a, _ in apis], "keys": [k for _, k in apis]}


def _relevant(g, focus):
    """오류 묶음이 증상의 관심 영역과 관련 있는지."""
    text = _j(g["sample"]).lower()
    if focus["kind"] == "memory" and "outofmemory" in text:
        return True
    if focus["kind"] == "connection" and ("connection is not available" in text or "hikari" in text):
        return True
    return any(k.lower() in text for k in focus["keys"])


def mark_current(f, focus, now):
    """오류 묶음마다 경과 시간과 '지금 장애' 여부를 표시. 90초 넘게 지났고 증상과도 무관하면 지나간 오류로 본다."""
    for g in f["signatures"]:
        g["age_sec"] = max(0, int((now - datetime.strptime(g["last"], "%H:%M:%S").replace(
            year=now.year, month=now.month, day=now.day)).total_seconds()))
        g["current"] = g["age_sec"] <= 90 or _relevant(g, focus)


def _age_text(sec):
    return f"{sec}초 전" if sec < 60 else f"{sec // 60}분 {sec % 60}초 전"


def observe(f, focus=None, now=None):
    focus = focus or {"kind": None, "apis": [], "keys": []}
    obs = []
    s = f["status"]
    if s.get("reachable"):
        if (s.get("heap_usage_pct") or 0) >= 85:
            obs.append(f"힙 메모리 사용률이 {s['heap_usage_pct']}%로 매우 높음")
        elif (s.get("heap_usage_pct") or 0) >= 40:
            obs.append(f"힙 메모리 사용률이 {s['heap_usage_pct']}%로 높은 편 ({s['heap_used_mb']}/{s['heap_max_mb']}MB)")
        if s.get("db_conn_max") and (s.get("db_conn_active") or 0) >= s["db_conn_max"] - 1:
            obs.append(f"DB 커넥션 {s['db_conn_active']:.0f}/{s['db_conn_max']:.0f}개가 사용 중 (거의 소진)")
    # S23(주문 최대 수량 설정값 오류)처럼 설정값이 너무 공격적으로 바뀌어서 생기는 장애는 요청
    # 검증(validation) 실패로 끝나는 경우가 많다 — 이런 4xx성 실패는 GlobalExceptionHandler가
    # 관례적으로 log.warn(...)으로 남기지(log.error가 아니라) 실제 서버 결함이 아니라 '예상된
    # 거부'이기 때문이다. 그런데 바로 그 WARN 로그 메시지 안에 설정 키가 그대로 찍혀 나온다
    # (예: "잘못된 요청: 주문 수량이 허용 범위(max-quantity)를 초과했습니다"). ERROR만 보면 이
    # 결정적 증거를 통째로 놓치므로 WARN도 같이 본다 — 아래 _config_related가 구체적인 설정
    # 키/값 문자열이 실제로 일치할 때만 관련 있다고 보므로, 무관한 WARN 로그와 우연히 겹쳐
    # 오탐을 일으킬 위험은 낮다.
    errors = [g for g in f["signatures"] if g["level"] in ("ERROR", "WARN") and g.get("current", True)]
    err_text_all = " ".join(_j(g["sample"]).lower() for g in errors)
    if s.get("reachable") and (s.get("db_conn_timeout_total") or 0) > 0 and "connection is not available" in err_text_all:
        obs.append(f"DB 커넥션을 받지 못한 실패가 {s['db_conn_timeout_total']:.0f}건 있음")
    if errors and f["config_changes"]:
        first_err = min(g["first"] for g in errors)
        # 앞서 일어난 설정 변경 중, 오류와 같은 영역(설정 이름 ↔ 오류 위치)이면서 현재 조사 중인 로그 범위
        # (최대 5분) 안에 있는 것만 관련 있다고 본다 — 로그 범위를 좁혀 둔 경우, 그보다 오래된 변경은
        # 다른(이전) 장애의 흔적일 수 있으므로 여기서도 제외해 교란을 줄인다.
        # _j(sample)로 JSON 통째로 직렬화한 문자열을 쓰면 안 된다 — {"level": "error", ...} 처럼
        # 데이터 구조 자체의 필드 이름이 그대로 텍스트에 섞여 들어가서, 예를 들어
        # logging.level.* 키의 leaf인 "level"이 모든 오류의 JSON에 박혀 있는 "level" 필드 이름과
        # 내용과 무관하게 항상 겹쳐버린다 (strict 모드에서도 안 걸러짐). 실제 내용(메시지·스택
        # 프레임·원인)만 모아서 비교 대상으로 삼는다.
        err_text = " ".join(_err_content_text(g["sample"]) for g in errors)
        window_sec = min(300, f["window_min"] * 60)
        before = [c for c in f["config_changes"] if c["time"] <= first_err
                  and _secs_between(c["time"], first_err) <= window_sec
                  and _config_related(c["message"], err_text,
                                      strict=_secs_between(c["time"], first_err) > 90)]
        if before:
            c = before[-1]
            obs.append(f"첫 오류({first_err}) 직전 {c['time']}에 관련 설정 변경이 있었음: {c['message']}")
    elif not errors and f["config_changes"]:
        # 에러 로그가 전혀 없는 '조용한' 설정 오류(예: 타임존이 바뀌어서 값이 틀려지지만 예외는 안 나는 경우)는
        # 위 분기가 아예 작동하지 않으므로, 증상 카테고리와 설정 키가 도메인상 관련 있어 보이면 별도로 잡아준다.
        hints = KIND_CONFIG_HINTS.get(focus.get("kind"))
        if hints:
            window_sec = min(300, f["window_min"] * 60)
            now_str = (now or datetime.now()).strftime("%H:%M:%S")
            recent = [c for c in f["config_changes"]
                      if _secs_between(c["time"], now_str) <= window_sec
                      and any(h in c["message"].lower() for h in hints)]
            if recent:
                c = recent[-1]
                obs.append(f"오류 로그는 없지만 직전 {c['time']}에 관련 설정 변경이 있었음: {c['message']}")
    for r in f["requests"]:
        if r["server_errors"] and errors:  # 누적 오류 수는 지금 오류가 있을 때만 의미 있음
            obs.append(f"{r['api']}에서 서버 오류 {r['server_errors']}건 / 전체 {r['count']}건")
        elif r["max_sec_recent"] >= 2:
            obs.append(f"{r['api']}가 오류 없이 최대 {r['max_sec_recent']}초 걸림 (느림)")
    return obs


def _config_related(msg, err_text, strict=False):
    """설정 변경과 오류가 실제로 관련 있는지.
    - 설정 키의 가장 구체적인 부분(leaf)이나 실제로 바뀐 값(이전/이후)이 오류 내용에 그대로 나타나면 관련 있다고 본다.
    - strict가 아니면, 그런 구체적인 단서가 없어도 설정 키의 도메인 단어가 겹치는 정도의 약한 신호도 받아들인다
      (호출하는 쪽에서 변경 직후처럼 시간상 아주 가까운 경우에만 strict=False로 넘겨야 한다 — 시간이 좀 지난 뒤라면
      도메인 단어만으로는 같은 서비스의 다른(이전) 장애와도 쉽게 겹쳐 오탐을 일으키기 때문)."""
    import re as _re
    m = _re.search(r"적용 ([\w.\-]+):\s*(\S+)\s*->\s*(\S+)", msg)
    if not m:
        return False
    key, before_val, after_val = m[1].lower(), m[2].lower(), m[3].lower()
    # 이 앱의 베이스 패키지(com.example.orderservice)는 거의 모든 로그·스택트레이스 클래스 이름에
    # 그대로 들어있다. 단순히 "orderservice"라는 조각 하나만 제외해서는 부족한 게, 설정 키의 다른
    # 조각("order" 등)도 "orderservice"라는 한 덩어리 문자열 안의 부분 문자열로 우연히 걸릴 수
    # 있기 때문이다 (예: app.order.coupon.enabled의 "order"가 스택트레이스에 찍힌
    # "com.example.orderservice..."의 "order" 부분과 겹쳐버림). 그래서 매칭에 쓰기 전에
    # err_text에서 베이스 패키지 문자열 자체를 통째로 제거해, 그 안에 포함된 부분 문자열들이
    # 무관한 매칭에 쓰이지 않게 한다. (패키지 경로 중 "order"/"payment"처럼 의미 있는 하위
    # 패키지 이름은 베이스 패키지 뒤에 "."로 이어지므로 그대로 남아 실제 관련성 판단에는 영향 없음.)
    BASE_PACKAGE = "com.example.orderservice"
    err_text = err_text.replace(BASE_PACKAGE, "")
    BOILERPLATE_SEGMENTS = {"spring", "com", "example", "orderservice"}
    key_parts = [p for p in key.split(".") if p]
    non_boilerplate = [p for p in key_parts if p not in BOILERPLATE_SEGMENTS]
    leaf = (non_boilerplate or key_parts)[-1]
    specific = [c for c in (leaf, after_val, before_val) if c and len(c) >= 4 and c in err_text]
    if specific or strict:
        return bool(specific)
    # 길이가 짧은 조각("app", "db" 등)은 흔한 단어·식별자 속에 우연히 들어있기 쉽다
    # (예: "app"이 "requestApproval" 안에 그대로 들어있어 결제 승인 코드와 엉뚱하게 겹침) —
    # specific 판정과 같은 기준(len>=4)으로 걸러서 약한 신호가 과도하게 걸리지 않게 한다.
    parts = [p for p in non_boilerplate if len(p) >= 4]
    return any(p in err_text for p in parts)


def build_graph(cfg: AgentConfig, emit: Callable[[dict], Any] = lambda e: None):
    tools = Tools(cfg)

    def step(kind, title, detail=None, **extra):
        emit({"kind": kind, "title": title, "detail": detail, "time": time.strftime("%H:%M:%S"), **extra})

    # 1) 기초 조사 (코드)
    def triage(state: AgentState):
        step("start", "기초 조사: 서비스 상태·API 통계·오류 로그·설정 변경 확인")
        f = {"status": tools.get_status(), "requests": tools.get_request_stats(),
             "signatures": tools.error_signatures(), "config_changes": tools.config_changes(),
             "window_min": cfg.log_window_min, "focus": symptom_focus(state.get("symptom", ""))}
        # 복합 장애 탐지 전용(더 넓은 에러 그룹) — error_signatures()가 최근 6개로 자르면서
        # 밀려날 수 있는 다른 시나리오의 에러 그룹까지 보려는 목적. f["signatures"](메인, 6개 제한)나
        # 아래 mark_current/observe/build_context에는 전혀 쓰이지 않으므로 LLM 컨텍스트는 그대로다.
        f["signatures_multi"] = tools.error_signatures_multi()
        now = datetime.now()
        mark_current(f, f["focus"], now)
        f["observations"] = observe(f, f["focus"], now)
        ctx = build_context(state.get("symptom", ""), f)
        step("triage", "기초 조사 완료", ctx)
        return {"facts": f, "context": ctx, "tool_log": [], "warnings": [], "round": 0, "timings": {}}

    # 2) 증거 수집 (코드): 오류 발생 위치 코드, 느린 API 처리 코드, 외부 호출 오류면 연동 설정
    def gather(state: AgentState):
        f = state["facts"]
        extra, log, defects, call_chain = [], list(state["tool_log"]), [], []
        targets, latest = [], None
        for g in f["signatures"]:  # 가장 최근 오류 순 (지나간 오류는 제외)
            fr = g["sample"].get("app_frames")
            if g["level"] != "ERROR" or not fr or fr[0] in targets or not g.get("current", True):
                continue
            latest = latest or g["last"]
            # 두 번째 오류는 가장 최근 오류와 거의 같은 시각(60초 이내)에 난 경우만 함께 본다
            if not targets or _secs_between(g["last"], latest) <= 60:
                targets.append(fr[0])
        for fr in targets[:2]:
            r = tools.read_code(fr)
            if "code" in r:
                name = (fr.split("(")[0]
                        .replace("com.example.orderservice.", "")
                        .replace("com.example.inventoryservice.", ""))
                extra.append(f"[관련 코드: {name}] (오류 발생 위치)\n{r['code']}" + _findings_text(r))
                defects += _defect_methods(r)
                log.append({"by": "자동", "tool": "read_code", "args": {"symbol": name}, "result": r})
                step("tool", f"코드 확인: {name}", r["code"], tool="read_code")
                if "Client" in name:
                    # 어느 외부/다른 서비스 연동인지에 맞는 설정 prefix를 본다 (멀티 서비스 장애 전파 M1/M2 포함)
                    prefix = "inventory.external" if "Inventory" in name else "payment.external"
                    c = tools.get_config(prefix)
                    extra.append(f"[현재 외부 연동 설정] {_j(c)}")
                    log.append({"by": "자동", "tool": "get_config", "args": {"prefix": prefix}, "result": c})
                    step("tool", f"외부 연동 설정 확인: {prefix}", _j(c), tool="get_config")
        if not targets:
            # 스택 트레이스가 없으면(예: OutOfMemoryError) 오류 난 API → 느린 API 순으로 처리 코드를 따라간다
            focus = f["focus"]
            apis_seen = [r["api"] for r in f["requests"]]
            by_focus = [a for a in apis_seen if any(h in a for h in focus["apis"])]
            memory_suspect = focus["kind"] == "memory" or any("힙 메모리" in o for o in f["observations"])
            writes = sorted([r for r in f["requests"] if r["api"].split()[0] in ("POST", "PUT", "PATCH")],
                            key=lambda r: -r["count"])
            # 누적 통계의 오류 수는 이전 장애 것일 수 있으므로, '지금 발생 중인 오류'가 있을 때만 오류 난 API로 본다
            has_current_error = any(g["level"] == "ERROR" and g.get("current", True) for g in f["signatures"])
            failing = [r["api"] for r in f["requests"] if r["server_errors"]] if has_current_error else []
            slow = [r["api"] for r in sorted([r for r in f["requests"] if r["max_sec_recent"] >= 2],
                                              key=lambda r: -r["max_sec_recent"])]
            candidates = [(a, "증상 관련 API") for a in by_focus]
            if focus["kind"] == "slow":
                candidates += [(a, "느린 API") for a in slow]
            if memory_suspect:  # 오류 전에 메모리만 늘어나는 경우: 요청이 많은 쓰기 API의 코드를 본다
                candidates += [(r["api"], "메모리 증가 의심 API(요청 많은 쓰기 API)") for r in writes]
            candidates += [(a, "오류 난 API") for a in failing] + [(a, "느린 API") for a in slow]
            for api, why in candidates[:1]:
                r = tools.read_endpoint_code(api)
                for c in r.get("chain", []):
                    extra.append(f"[관련 코드: {c['symbol']}] ({why} {api} 처리 경로)\n{c['code']}"
                                 + _findings_text(c, focus["kind"]))
                    defects += _defect_methods(c)
                    call_chain.append(c["symbol"])
                log.append({"by": "자동", "tool": "read_endpoint_code", "args": {"api": api}, "result": r})
                step("tool", f"{why} 처리 코드 확인: {api}",
                     "\n\n".join(c["code"] for c in r.get("chain", [])), tool="read_endpoint_code")
        ctx = state["context"] + ("\n\n" + "\n\n".join(extra) if extra else "")
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": ctx + "\n\n" + INVESTIGATE_PROMPT}]
        frames = [fr for g in f["signatures"] if g.get("current", True)
                  for fr in g["sample"].get("app_frames", [])] + call_chain
        defects_multi = _extra_defects_for_multi(f, tools, defects, log, step)
        return {"context": ctx, "messages": messages, "tool_log": log, "defects": defects,
                "defects_multi": defects_multi, "frames": frames}

    # 3) 추가 조사 (LLM이 도구 선택)
    def investigate(state: AgentState):
        rnd = state["round"] + 1
        step("think", f"추가 조사 판단 ({rnd}/{cfg.max_rounds})")
        msg, sec = chat(cfg, state["messages"], tools=TOOL_SPECS)
        timings = dict(state["timings"]); timings[f"investigate_{rnd}"] = round(sec, 1)
        calls = [c.get("function", {}) for c in (msg.get("tool_calls") or [])]
        from_text = False
        if not calls:  # 작은 모델은 도구 요청을 정식 형식 대신 본문에 JSON 글자로 적기도 한다
            calls = _calls_from_text(msg.get("content") or "")
            from_text = bool(calls)
        done = {(c["tool"], _norm_args(c["args"])) for c in state["tool_log"]}
        uniq, repeated = [], []
        for fn in calls:
            name, args = fn.get("name"), fn.get("arguments") or {}
            key = (name, _norm_args(args))
            if key in done:
                repeated.append(f"{name}({_j(args)})")
            elif key not in [(u["name"], _norm_args(u["args"])) for u in uniq]:
                uniq.append({"name": name, "args": args})
        uniq = uniq[:cfg.max_tool_calls_per_round]
        messages = state["messages"] + [{"role": "assistant", "content": "" if from_text else msg.get("content", ""),
                                          "tool_calls": [{"function": {"name": u["name"], "arguments": u["args"]}}
                                                         for u in uniq]}]
        how = " (본문에 적은 요청을 해석)" if from_text else ""
        if uniq:
            step("think", f"LLM이 추가 확인 요청{how} ({sec:.0f}초)",
                 "\n".join(f"- {u['name']}({_j(u['args'])})" for u in uniq))
        elif repeated:
            step("think", f"LLM이 요청한 자료는 이미 수집되어 있어 추가 조사 없이 진행 ({sec:.0f}초)",
                 "\n".join(f"- {r}" for r in repeated))
        else:
            step("think", f"LLM 판단: 추가 조사 불필요 ({sec:.0f}초)", (msg.get("content") or "").strip()[:300])
        return {"messages": messages, "pending_calls": uniq, "round": rnd, "timings": timings}

    def run_tools(state: AgentState):
        messages, log = list(state["messages"]), list(state["tool_log"])
        for c in state["pending_calls"]:
            result = run_tool(tools, c["name"], c["args"])
            text = result if isinstance(result, str) else _j(result)
            if isinstance(result, dict) and "chain" in result:
                text = "\n\n".join(f"[{x['symbol']}]\n{x['code']}" + _findings_text(x) for x in result["chain"])
            elif isinstance(result, dict) and "code" in result:
                text = f"[{result['symbol']}]\n{result['code']}" + _findings_text(result)
            messages.append({"role": "tool", "tool_name": c["name"], "content": text[:4000]})
            log.append({"by": "LLM", "tool": c["name"], "args": c["args"], "result": result})
            step("tool", f"{c['name']}({_j(c['args'])})", text[:3000], tool=c["name"])
        messages.append({"role": "user", "content": INVESTIGATE_PROMPT})
        return {"messages": messages, "tool_log": log, "pending_calls": []}

    def route(state: AgentState):
        if state.get("pending_calls") and state["round"] <= cfg.max_rounds:
            return "run_tools"
        return "conclude"

    def after_tools(state: AgentState):
        return "investigate" if state["round"] < cfg.max_rounds else "conclude"

    # 4) 결론 (LLM, JSON)
    def conclude(state: AgentState):
        step("think", "원인 결론 작성 중")
        messages = [m for m in state["messages"] if not (m["role"] == "user" and m["content"] == INVESTIGATE_PROMPT
                                                        and m is state["messages"][-1])]
        messages.append({"role": "user", "content": CONCLUDE_PROMPT})
        msg, sec = chat(cfg, messages, schema=REPORT_SCHEMA)
        timings = dict(state["timings"]); timings["conclude"] = round(sec, 1)
        try:
            report = json.loads(msg.get("content", "{}"))
        except json.JSONDecodeError:
            report = {"summary": msg.get("content", "")[:300], "cause_type": "기타", "location": "",
                      "evidence": [], "immediate_action": "", "fix": "", "confidence": "낮음"}
        step("think", f"결론 작성 완료 ({sec:.0f}초)")
        return {"report": report, "timings": timings}

    # 5) 검증 (코드)
    def validate(state: AgentState):
        r, warnings = dict(state["report"]), list(state["warnings"])
        raw = dict(r)
        # evidence를 코드 점검 결과로 직접(프로그램적으로) 고쳤으면 True — 아래 재작성 단계에서 LLM이
        # evidence 필드를 다시 써서 보내도 그걸로 덮어쓰지 않게 막는 플래그. LLM(특히 작은 로컬 모델)은
        # "이 문장만 써라"라고 지시해도 앞서 자기가 봤던 다른 결함 문장을 그대로 다시 쓰는 경우가 있어서,
        # evidence만큼은 지시가 아니라 덮어쓰기 자체를 막아야 확실하다.
        evidence_locked = False
        # 코드가 직접 계산한 사실과 결론이 모순되면 보정
        f = state["facts"]
        defects_found = state.get("defects") or []
        cfg_change = next((o for o in f["observations"] if "직전" in o and "설정 변경" in o), None)
        ext_timeout = any(any(k in _j(g["sample"]).lower() for k in EXT_FAILURE_MARKERS)
                          and any("Client" in fr for fr in g["sample"].get("app_frames", []))
                          for g in f["signatures"] if g["level"] == "ERROR" and g.get("current", True))
        # 결함이 '있다'는 사실만으로 다른 신호(설정 변경/외부 타임아웃)를 덮어쓰지 않는다 — 조사 중에 증상과
        # 무관한 결함(예: 전혀 다른 메모리 이슈)을 우연히 발견했을 수도 있기 때문. 경쟁하는 신호가 없을 때는
        # (예: 순수 코드 결함 시나리오) 예전처럼 결함 발견 자체를 신호로 받아들이되, cfg_change/ext_timeout처럼
        # 더 구체적인 신호와 충돌할 때만 그 결함이 실제로 증상 범주와 맞는지(kind 일치) 확인해서 걸러낸다.
        relevant_defect = _best_defect(defects_found, f["observations"], f["focus"]["kind"]) if defects_found else None
        has_defect = bool(defects_found) if not (cfg_change or ext_timeout) else bool(relevant_defect)
        # cfg_change를 has_defect보다 먼저 확인한다 — 정적 스캐너는 런타임에 그 시나리오가 실제로
        # 켜져 있는지 모르고 소스에 있는 패턴을 전부 찾아내므로(예: S2 커넥션 릭 메서드는 S2가 꺼져
        # 있어도 항상 발견됨), kind가 같다는 이유만으로("커넥션"↔"connection") 코드 결함을 더 강한
        # 신호로 보면 S18(풀 크기 설정 오류)처럼 같은 kind를 공유하는 설정 오류가 늘 가려진다.
        # cfg_change는 '방금 실제로 바뀐 설정값'이라는 더 구체적이고 시점이 정확한 사실이므로,
        # 같은 kind의 정적 코드 패턴보다 우선한다.
        expected = ("설정 오류" if cfg_change else "코드 결함" if has_defect else "외부 시스템" if ext_timeout else None)
        if expected and r.get("cause_type") != expected:
            warnings.append(f"원인 유형을 조사 사실 기준으로 보정: {r.get('cause_type')} → {expected} "
                            f"(코드 결함 패턴 {'있음' if has_defect else '없음'}, 직전 설정 변경 "
                            f"{'있음' if cfg_change else '없음'}, 외부 호출 실패 흔적 {'있음' if ext_timeout else '없음'})")
            r["cause_type"] = expected
            if expected == "설정 오류":
                import re as _re
                m = _re.search(r"적용 ([\w.\-]+)", cfg_change)
                r["location"] = m[1] if m else r.get("location")
                # LLM이 1차 판단 때 집은(코드 결함 쪽) 근거 문장이 그대로 남아있으면 실제 원인인
                # 설정 변경 사실과 뒤섞여 보고서에 같이 뜬다 — 코드 결함 보정 때와 마찬가지로
                # evidence를 설정 변경 사실 하나로 교체하고 잠가서 재작성 LLM이 되돌리지 못하게 한다.
                r["evidence"] = [cfg_change]
                evidence_locked = True
            elif expected == "코드 결함":
                focus_kind = f["focus"]["kind"] or _obs_kind(f["observations"])
                bd = _best_defect(state["defects"], f["observations"], focus_kind) or state["defects"][0]
                r["location"] = bd["method"]
                # location만 고치면 같은 메서드 안에 결함이 여러 개일 때(S11/S16처럼) 구분이 안 된다 —
                # evidence도 실제로 고른 결함의 findings로 바꿔서, 아래 find_section()과 재작성 지시가
                # LLM이 원래 썼던(틀린) 근거 문장이 아니라 실제 고른 결함을 보고 판단하게 한다. 한 메서드
                # 안에 여러 시나리오 패턴이 섞여 있을 수 있으므로(_finding_kind 설명 참고) bd["findings"]
                # 전부가 아니라 이 kind와 맞는 줄만 추려서 쓴다.
                if bd.get("findings"):
                    r["evidence"] = _matching_findings(bd, focus_kind) if focus_kind else list(bd["findings"])
                    evidence_locked = True
            elif expected == "외부 시스템":
                r["location"] = "외부 결제 대행사(PG) API"
            r["confidence"] = "보통"
        elif not expected:
            # 조사 사실 중 어느 것도 결정적이지 않을 때(=이 세 범주로는 설명이 안 되는 결함일 때),
            # 그런데도 LLM이 그중 하나로 단정했다면 — 그 범주를 뒷받침할 사실이 전혀 없다는 뜻이므로
            # (예: '외부 시스템'이라면서 실제 타임아웃 근거가 없음) 확신도만 낮춰 과신을 막는다.
            # expected가 None이면 이 시점에서 세 신호(has_defect/cfg_change/ext_timeout) 모두 False이므로,
            # LLM이 '기타'를 포함해 어떤 cause_type을 주장하든 그걸 뒷받침하는 결정적 사실은 전혀 없다.
            # (과거엔 '코드 결함'/'설정 오류'/'외부 시스템' 세 값만 매핑해두고, '기타'처럼 매핑에 없는 값은
            #  dict.get()이 None을 돌려줘서 'is False' 체크를 피해가는 틈이 있었다 — default=False로 막는다.)
            signal_for_type = {"코드 결함": has_defect, "설정 오류": bool(cfg_change), "외부 시스템": ext_timeout}
            claimed = r.get("cause_type")
            if signal_for_type.get(claimed, False) is False:
                warnings.append(f"'{claimed}'으로 결론 냈지만 이를 뒷받침하는 조사 사실(코드 결함 패턴/직전 설정 변경/"
                                "외부 호출 실패 흔적)이 전혀 없음 — 증거가 부족한데 단정했을 수 있음")
                r["confidence"] = "낮음"
        if r.get("cause_type") not in CAUSE_TYPES:
            warnings.append(f"원인 유형 '{r.get('cause_type')}'이 정해진 값이 아님")
            r["cause_type"] = "기타"
        loc = (r.get("location") or "").strip()
        if r["cause_type"] == "코드 결함" and state.get("defects"):
            loc_method = loc.split("(")[0].split(".")[-1]
            focus_kind = f["focus"]["kind"] or _obs_kind(f["observations"])
            preferred = _best_defect(state["defects"], f["observations"], focus_kind)
            chosen = next((d for d in state["defects"] if d["method"].split(".")[-1] == loc_method), None)
            chain = [fr.split("(")[0].split(".")[-1] for fr in state.get("frames", [])]
            reason = None
            if not chosen:
                reason = "보고한 위치에서는 결함이 발견되지 않음" if loc_method not in chain else \
                    "보고한 위치는 결함 메서드를 호출하는 쪽"
            elif chosen and preferred and chosen is not preferred and \
                    not _defect_matches_kind(chosen, focus_kind):
                reason = "보고한 결함 유형이 관찰된 증상(지표)과 맞지 않음"
            if reason:
                fixed_defect = preferred or state["defects"][0]
                fixed = fixed_defect["method"]
                warnings.append(f"위치를 코드 점검 결과 기준으로 보정 ({reason}): {loc or '(없음)'} → {fixed}")
                loc = r["location"] = fixed
                # location은 같은 메서드 안의 다른 결함(S11/S16 등)과 겹치는 이름일 수 있어 이것만으로는
                # 구분이 안 된다 — evidence를 실제로 고른 결함(fixed_defect)의 findings로 바꿔서, 아래
                # find_section()의 키워드 매칭과 재작성 지시가 LLM이 원래 썼던(틀릴 수 있는) 근거 문장이
                # 아니라 실제로 선택된 결함을 보고 판단하게 한다. 한 메서드 안에 여러 시나리오 패턴이
                # 섞여 있을 수 있으므로(_finding_kind 설명 참고) findings 전부가 아니라 이 kind와
                # 맞는 줄만 추려서 쓴다.
                if fixed_defect.get("findings"):
                    r["evidence"] = (_matching_findings(fixed_defect, focus_kind) if focus_kind
                                      else list(fixed_defect["findings"]))
                    evidence_locked = True
            elif chosen and focus_kind:
                # 위치는 맞았어도(코드 결함이고 보고한 메서드도 실제 결함 메서드와 일치) — 한 메서드 안에
                # 여러 시나리오 패턴이 섞여 있을 수 있어서(S11/S13/S14/S16 등 전부 PaymentService.pay 안에
                # 있는 경우처럼), LLM이 "위치"는 맞게 짚었어도 "근거"는 그 메서드 안의 다른(증상과 무관한)
                # 패턴을 인용했을 수 있다. chosen(=실제 보고된 메서드의 결함)의 findings 중 이 증상 kind와
                # 정확히 맞는 줄이 있으면, LLM이 쓴 근거를 무조건 그걸로 바꿔서 아래 find_section() 매뉴얼
                # 매칭과 재작성 지시가 엉뚱한 줄(예: S16 증상인데 S14 부동소수점 문구)을 보고 판단하지
                # 않게 한다. (맞는 줄이 하나도 없으면 — 애초에 코드 점검에서 이 증상과 맞는 패턴을 못 찾은
                # 경우 — 손대지 않고 LLM의 근거를 그대로 둔다.)
                strict = [x for x in chosen.get("findings", []) if _kind_matches(_finding_kind(x), focus_kind)]
                if strict and strict != (r.get("evidence") or []):
                    warnings.append(f"근거를 코드 점검 결과 기준으로 보정 (증상과 맞는 줄만 사용): {chosen['method']}")
                    r["evidence"] = strict
                    evidence_locked = True
            sym = loc.split("(")[0].replace("com.example.orderservice.", "").split(".")
            sym = ".".join(sym[-2:]) if len(sym) >= 2 else loc
            if not loc or not tools.symbol_exists(sym):
                warnings.append(f"보고서의 코드 위치 '{loc}'를 소스에서 찾지 못함")
                r["confidence"] = "낮음"
        if len(r.get("evidence") or []) < 1:
            warnings.append("근거가 없음")
            r["confidence"] = "낮음"
        ctx = state["context"] + "".join(_j(t["result"]) for t in state["tool_log"])
        quoted = [e for e in r.get("evidence", []) if any(tok in ctx for tok in _key_tokens(e))]
        if r.get("evidence") and not quoted:
            warnings.append("근거 문장이 조사 결과와 일치하는 부분이 없음 (지어낸 근거일 수 있음)")
            r["confidence"] = "낮음"
        # 원인이 확정됐으니, 그에 맞는 매뉴얼 절이 있는지 찾는다 (없으면 억지로 끼워맞추지 않고 None).
        rb_ref = runbook.find_section(r)
        if rb_ref:
            r["runbook_ref"] = {"id": rb_ref["id"], "title": rb_ref["title"]}
        # 보정이 있었거나 매뉴얼이 걸렸으면, 그 내용 기준으로 보고서 문장을 다시 쓰게 한다
        # (요약·근거·조치가 서로 맞도록, 그리고 조치/해결이 지어낸 말이 아니라 매뉴얼에 근거하도록)
        corrected = any(w.startswith(("위치를", "원인 유형을", "근거를")) for w in warnings[len(state["warnings"]):])
        if corrected or rb_ref:
            reason = f"({r['cause_type']} / {r['location']})" if corrected else f"(매뉴얼 {rb_ref['id']} 반영)"
            step("think", f"보정된 원인 기준으로 보고서 다시 작성 {reason}")
            msgs = [m for m in state["messages"] if m["role"] != "assistant" or m.get("content")]
            instruction = (f"검증 결과 원인 유형은 '{r['cause_type']}', 위치는 '{r['location']}'로 확정되었다. "
                           f"이 원인에 맞게 보고서를 다시 작성하라. 다른 메서드·다른 원인은 언급하지 마라.\n")
            if corrected and r.get("evidence"):
                # location만으로는 같은 메서드 안의 다른 결함(예: PaymentService.pay 안의 S11/S16)과
                # 구분이 안 되므로, 실제로 코드 점검에서 고른 결함의 findings 문장을 명시적으로 박아준다 —
                # 안 그러면 LLM이 앞서 자기가 썼던(틀릴 수 있는) 근거를 그대로 반복해서 쓴다.
                instruction += ("\n근거(evidence)는 아래 코드 점검 결과 문장만 사용하라. 이 중에 없는 패턴(예: "
                                "앞서 언급했던 다른 결함)을 근거로 들지 마라:\n" +
                                "\n".join(f"- {e}" for e in r["evidence"]) + "\n")
            if rb_ref:
                instruction += (f"\n사내 대응 매뉴얼 {rb_ref['id']} '{rb_ref['title']}'에 아래 내용이 있다. "
                                f"'지금 할 조치'와 '근본 해결'은 이 내용에 근거해서 작성하고, 매뉴얼에 없는 "
                                f"조치를 지어내지 마라.\n---\n{rb_ref['body']}\n---\n")
            msgs = msgs + [{"role": "user", "content": instruction + CONCLUDE_PROMPT}]
            try:
                msg, sec = chat(cfg, msgs, schema=REPORT_SCHEMA)
                new = json.loads(msg.get("content", "{}"))
                # evidence_locked면 evidence는 LLM이 다시 쓴 걸로 덮어쓰지 않는다 — "이 문장만 써라"라고
                # 지시해도(위 instruction) 작은 모델은 종종 지시를 어기고 앞서 봤던 다른 결함 문장을 그대로
                # 다시 쓰길래, evidence만큼은 지시가 아니라 덮어쓰기 자체를 막아야 확실하다.
                keys = ("summary", "immediate_action", "fix") if evidence_locked else \
                    ("summary", "evidence", "immediate_action", "fix")
                for k in keys:
                    if new.get(k):
                        r[k] = new[k]
                step("think", f"다시 작성 완료 ({sec:.0f}초)")
            except Exception as ex:  # noqa: BLE001
                warnings.append(f"보고서 재작성 실패: {ex}")
        # ------------------------------------------------------------- 복합 장애(원인 여러 개) 탐지
        # 위 보정은 증상 문장에서 뽑은 kind 하나에 대해 "가장 그럴듯한 원인 하나"만 확정한다.
        # 시나리오를 2개 이상 동시에 켜서 서로 다른 신호(힙/DB 커넥션/응답 지연/직전 설정 변경/외부
        # 호출 타임아웃)가 함께 관찰되면, 이미 조사 단계에서 모은 사실(state["defects"], cfg_change,
        # ext_timeout) 안에 주 원인과 무관한 두 번째·세 번째 원인의 증거가 같이 들어있을 수 있다.
        # 여기서는 그 증거를 주 원인과 "다른" kind에 대해서만 다시 검토해, 추가로 확정되는 것만
        # report["additional_causes"]에 보탠다 — 주 원인(r)의 필드·기존 채점 방식은 전혀 바뀌지 않는다.
        # (조사 단계 자체는 안 바꾸므로, 두 원인이 서로 다른 API·다른 시간대라 애초에 그 코드를
        # 들여다보지 않았다면 여기서도 못 잡는다 — 알려진 한계.)
        seen_causes = {(r.get("cause_type"), r.get("location"))}
        additional_causes = []

        def _add(cause):
            if not cause or not cause.get("location"):
                return
            key = (cause["cause_type"], cause["location"])
            if key in seen_causes:
                return
            seen_causes.add(key)
            additional_causes.append(cause)

        # 코드 결함 — 힙/DB 커넥션 등 관찰된 kind별로 다시 확인 (기존 로직 그대로)
        for kind in _obs_kinds(f["observations"]):
            # 단일 원인 판단에 쓰는 state["defects"]뿐 아니라, 복합 탐지 전용으로 추가 읽은
            # state["defects_multi"]도 같이 본다 (존재하면) — 이쪽은 LLM 컨텍스트에는 없던 거라
            # 여기서만 추가 원인 후보로 쓰인다.
            d = _best_defect((state["defects"] + state.get("defects_multi", [])),
                            f["observations"], kind)
            if d:
                ev = _matching_findings(d, kind) if d.get("findings") else list(d.get("findings") or [])
                _add({"cause_type": "코드 결함", "location": d["method"], "evidence": ev,
                      "confidence": "보통"})

        # 설정 오류 — 메인 cfg_change(증상 포커스·'현재' 필터 기준)에 더해, 복합 탐지 전용으로
        # 필터 없이 전체 에러 그룹을 다시 훑은 후보도 같이 본다 (증상 포커스가 다른 원인 쪽으로
        # 잡혀 있어서 '현재'가 아니라고 표시된 에러 그룹 앞의 설정 변경은 메인 cfg_change가 못 본다).
        import re as _re2
        cfg_obs_candidates = ([cfg_change] if cfg_change else []) + _cfg_change_candidates_for_multi(f)
        for obs in dict.fromkeys(cfg_obs_candidates):  # 순서 유지하며 중복 메시지 제거
            m2 = _re2.search(r"적용 ([\w.\-]+)", obs)
            _add({"cause_type": "설정 오류", "location": (m2[1] if m2 else ""),
                  "evidence": [obs], "confidence": "보통"})

        # 외부 시스템 — 메인 ext_timeout(현재' 필터 기준) 또는 복합 탐지 전용(필터 없음) 중
        # 하나라도 신호가 있으면 후보로 본다.
        if ext_timeout or _ext_timeout_any(f):
            ev = _ext_timeout_evidence(f) or _ext_timeout_evidence_any(f)
            _add({"cause_type": "외부 시스템", "location": "외부 결제 대행사(PG) API",
                  "evidence": ev, "confidence": "보통"})

        if additional_causes:
            r["additional_causes"] = additional_causes
            warnings.append(f"주 원인 외에 {len(additional_causes)}개의 추가 원인 신호를 함께 발견함 "
                            "(복합 장애 가능성 — 여러 시나리오가 동시에 켜져 있을 수 있음)")
        step("done", "분석 완료", None, report=r, warnings=warnings)
        return {"report": r, "warnings": warnings, "raw_report": raw}

    g = StateGraph(AgentState)
    for name, fn in [("triage", triage), ("gather", gather), ("investigate", investigate),
                     ("run_tools", run_tools), ("conclude", conclude), ("validate", validate)]:
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_edge("triage", "gather")
    g.add_edge("gather", "investigate")
    g.add_conditional_edges("investigate", route, {"run_tools": "run_tools", "conclude": "conclude"})
    g.add_conditional_edges("run_tools", after_tools, {"investigate": "investigate", "conclude": "conclude"})
    g.add_edge("conclude", "validate")
    g.add_edge("validate", END)
    return g.compile()


def _kind_matches(defect_kind, focus_kind):
    """"slow" 증상 범주는 실제 응답 지연 결함(Future.get() 등)과 스레드 누수 결함을 둘 다 포괄한다
    (둘 다 "서버가 느려진다"는 문구로 들어오므로) — 그 외 kind는 그대로 정확히 일치해야 한다."""
    if focus_kind == "slow":
        return defect_kind in ("slow", "thread_leak")
    return defect_kind == focus_kind


def _defect_kinds(d):
    """defect(보통 메서드 하나) 안에 섞여 있을 수 있는 findings 각각의 kind를 모아 집합으로
    돌려준다 — _finding_kind 설명 참고."""
    return {_finding_kind(x) for x in d.get("findings", [])}


def _defect_matches_kind(d, focus_kind):
    return any(_kind_matches(k, focus_kind) for k in _defect_kinds(d))


def _matching_findings(d, focus_kind):
    """결함(메서드)의 findings 중, 실제로 이 증상 kind와 맞는 줄만 추려서 돌려준다. 한 메서드에
    여러 시나리오의 패턴이 섞여 있을 때(S11/S13/S14/S16 등) evidence·매뉴얼 매칭에 엉뚱한 줄이
    섞여 들어가지 않게 하려는 목적 — 맞는 줄이 하나도 없으면(이론상 없어야 하지만 방어적으로)
    전체 findings를 그대로 돌려준다."""
    matched = [x for x in d.get("findings", []) if _kind_matches(_finding_kind(x), focus_kind)]
    return matched or list(d.get("findings", []))


def _obs_kind(observations):
    t = " ".join(observations)
    if "힙 메모리" in t:
        return "memory"
    if "DB 커넥션" in t:
        return "connection"
    if "느림" in t:
        return "slow"
    return None


def _extra_defects_for_multi(f, tools, existing_defects, log, emit_step):
    """복합 장애(시나리오 여러 개 동시) 탐지 전용 — 주 조사 흐름이 LLM에게 보여주는 맥락(ctx/messages)에는
    전혀 손대지 않고, 힙/DB 커넥션이 독립적으로 높게 관찰되는데 그 코드를 아직 한 번도 안 읽었다면
    (=주 원인 조사가 다른 에러 위치만 보고 끝나서) 추가로 읽어 defects만 보탠다.
    이 결과는 state["defects"](단일 원인 판단에 쓰이는 변수)와는 분리된 state["defects_multi"]로만
    쓰이므로, 기존 단일 시나리오 판단·채점에는 영향이 없다."""
    have_kinds = {k for d in (existing_defects or []) for k in _defect_kinds(d)}
    obs_kinds = _obs_kinds(f["observations"])
    extra = []
    writes = sorted([r for r in f["requests"] if r["api"].split()[0] in ("POST", "PUT", "PATCH")],
                    key=lambda r: -r["count"])
    busiest = sorted(f["requests"], key=lambda r: -r["count"])
    checks = []
    if "memory" in obs_kinds and "memory" not in have_kinds and writes:
        checks.append(("힙 메모리 사용률이 높아 보이는데 아직 안 읽은 쓰기 API 경로", writes[0]["api"]))
    if "connection" in obs_kinds and "connection" not in have_kinds and busiest:
        checks.append(("DB 커넥션 사용률이 높아 보이는데 아직 안 읽은 호출 많은 API 경로", busiest[0]["api"]))
    for why, api in checks:
        r = tools.read_endpoint_code(api)
        for c in r.get("chain", []):
            extra += _defect_methods(c)
        log.append({"by": "자동(복합탐지)", "tool": "read_endpoint_code", "args": {"api": api}, "result": r})
        emit_step("tool", f"[복합 장애 탐지] {why}: {api}",
                  "\n\n".join(c["code"] for c in r.get("chain", [])), tool="read_endpoint_code")
    return extra


def _obs_kinds(observations):
    """_obs_kind()와 같은 신호를 '단일 kind 하나'가 아니라 '관찰된 kind 전부(집합)'로 돌려준다.
    복합 장애(시나리오 여러 개 동시 재현) 탐지에서, 증상 문장이 가리키는 kind 하나만 보지 않고
    실제 수치(힙/DB 커넥션/응답 지연)에 동시에 나타난 다른 신호도 놓치지 않기 위해 쓴다."""
    t = " ".join(observations)
    kinds = set()
    if "힙 메모리" in t:
        kinds.add("memory")
    if "DB 커넥션" in t:
        kinds.add("connection")
    if "느림" in t:
        kinds.add("slow")
    return kinds


def _ext_timeout_evidence(f):
    """ext_timeout 신호(외부 호출 실패 흔적)를 뒷받침하는 오류 샘플에서 근거 문장 한 줄을 뽑는다."""
    for g in f["signatures"]:
        if g["level"] != "ERROR" or not g.get("current", True):
            continue
        text = _j(g["sample"]).lower()
        if (any(k in text for k in EXT_FAILURE_MARKERS)
                and any("Client" in fr for fr in g["sample"].get("app_frames", []))):
            msg = (g["sample"].get("message") or "").strip()
            if msg:
                return [msg[:120]]
    return []


# --------------------------------------------------------------------------- 복합 장애 탐지 전용 (현재' 필터 무시)
# cfg_change/ext_timeout(위)과 _ext_timeout_evidence는 모두 mark_current()가 '현재'로 표시한 오류
# (증상 포커스와 관련 있거나 90초 이내)만 본다. 증상 포커스가 다른 원인(예: 메모리) 쪽으로 잡혀 있으면,
# 그와 무관한 다른 에러 그룹(예: 오래된 설정 오류/외부 타임아웃) 앞의 신호는 '현재'가 아니라는 이유로
# 통째로 안 보인다 — _extra_defects_for_multi가 "코드를 안 읽어서" 놓치는 것과 같은 종류의 문제를
# cfg_change/ext_timeout 쪽에도 그대로 겪는다. 아래 두 함수는 '현재' 여부·증상 포커스와 무관하게
# 전체 에러 그룹을 다시 훑어 신호를 찾되, 결과는 state["observations"]/LLM 컨텍스트에는 전혀 넣지
# 않고 validate()의 복합 원인 후보로만 쓴다 — 주 원인 판단 로직(cfg_change/ext_timeout 그 자체)은
# 그대로 두므로 단일 시나리오 판단에는 영향이 없다.
def _ext_timeout_any(f):
    """ext_timeout 신호를 '현재' 필터 없이, 또한 error_signatures()의 '최근 6개' 제한도 없이
    (signatures_multi가 있으면 그쪽을) 다시 확인한다 (복합 탐지 전용)."""
    sigs = f.get("signatures_multi") or f["signatures"]
    return any(any(k in _j(g["sample"]).lower() for k in EXT_FAILURE_MARKERS)
               and any("Client" in fr for fr in g["sample"].get("app_frames", []))
               for g in sigs if g["level"] == "ERROR")


def _ext_timeout_evidence_any(f):
    """_ext_timeout_evidence와 동일하나 '현재' 필터·"최근 6개" 제한이 없다 (복합 탐지 전용)."""
    sigs = f.get("signatures_multi") or f["signatures"]
    for g in sigs:
        if g["level"] != "ERROR":
            continue
        text = _j(g["sample"]).lower()
        if (any(k in text for k in EXT_FAILURE_MARKERS)
                and any("Client" in fr for fr in g["sample"].get("app_frames", []))):
            msg = (g["sample"].get("message") or "").strip()
            if msg:
                return [msg[:120]]
    return []


def _cfg_change_candidates_for_multi(f):
    """observe()의 cfg_change 판정과 비슷한 매칭(설정 변경 메시지 ↔ 오류 내용)을, '현재'·증상 포커스
    필터 없이, error_signatures()의 '최근 6개' 제한도 없이(signatures_multi가 있으면 그쪽을), 그리고
    *시간 간격 제한도 없이* 에러 그룹(레벨 ERROR/WARN) 하나하나에 대해 다시 해본다 (복합 탐지 전용).

    observe()는 설정 변경과 오류가 90초 넘게 떨어져 있으면 strict 모드(leaf/값이 오류 문구에 그대로
    나와야 함)로 전환하는데, 예를 들어 설정값을 잘못된 URL로 바꿔서 404가 나는 경우 그 404 예외
    메시지에는 URL이 전혀 안 찍히는 형태라 strict 모드에서는 "절대" 못 맞힌다. 그래서 설정을 잘못
    바꿔둔 채로 몇 시간 뒤에야 그 경로로 요청이 들어와서 에러가 나는(=이미 켜져 있던 다른 시나리오
    때문에 늦게 분석을 돌린) 경우를 잡으려는 게 이 함수의 목적인데, observe()와 똑같은 90초/5분
    제한을 그대로 썼다면 애초에 이런 "오래된 설정 + 늦은 요청" 조합을 못 잡는다. 그래서 여기서는
    시간 제한을 두지 않고(설정 변경이 오류보다 먼저이기만 하면 후보로 보고) 항상 느슨한(strict=False)
    도메인 키워드 매칭만 쓴다 — 오탐 가능성은 있지만, 주 원인(r)이 아니라 "추가로 확인된 신호"로만
    보고서에 표시되므로 감내할 만하다 (observe()의 cfg_change 변수·단일 원인 판단은 그대로 둔다)."""
    out = []
    if not f["config_changes"]:
        return out
    sigs = f.get("signatures_multi") or f["signatures"]
    seen_msgs = set()
    for g in sigs:
        if g["level"] not in ("ERROR", "WARN"):
            continue
        err_text = _err_content_text(g["sample"])
        first_err = g["first"]
        before = [c for c in f["config_changes"] if c["time"] <= first_err
                  and _config_related(c["message"], err_text, strict=False)]
        if before:
            c = before[-1]
            if c["message"] not in seen_msgs:
                seen_msgs.add(c["message"])
                out.append(f"첫 오류({first_err}) 직전 {c['time']}에 관련 설정 변경이 있었음: {c['message']}")
    return out


def _best_defect(defects, observations, focus_kind=None):
    """여러 결함 후보 중 증상(관심 영역) 또는 관찰된 지표와 종류가 맞는 것을 고른다."""
    kind = focus_kind or _obs_kind(observations)
    if not kind:
        return None
    if kind == "slow":
        # "slow" 범주에는 실제 응답 지연 결함(Future.get() 등)과 스레드 누수 결함이 둘 다 걸릴 수
        # 있다(_kind_matches 참고). 실제로 관찰된 지연("느림")이 없다면 — 즉 요청은 빠르게 끝났다면 —
        # 응답 지연이 필요 조건인 쪽(Future.get() 등)보다, 응답은 빠르고 백그라운드 스레드만 쌓이는
        # 스레드 누수 쪽이 증상을 더 잘 설명한다. 그래서 관찰된 지연이 없으면 thread_leak을 우선한다.
        observed_latency = any("느림" in o for o in observations)
        if not observed_latency:
            thread_leak = next((d for d in defects if _defect_matches_kind(d, "thread_leak")), None)
            if thread_leak:
                return thread_leak
    return next((d for d in defects if _defect_matches_kind(d, kind)), None)


TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


def _calls_from_text(text):
    """본문에 JSON으로 적힌 도구 요청을 찾아낸다. 예: {"name": "read_code", "arguments": {"symbol": "..."}}"""
    found, dec, i = [], json.JSONDecoder(), 0
    while True:
        start = min([p for p in (text.find("{", i), text.find("[", i)) if p != -1], default=-1)
        if start == -1:
            break
        try:
            obj, end = dec.raw_decode(text, start)
        except json.JSONDecodeError:
            i = start + 1
            continue
        for item in obj if isinstance(obj, list) else [obj]:
            if isinstance(item, dict) and isinstance(item.get("function"), dict):
                item = item["function"]
            if isinstance(item, dict) and item.get("name") in TOOL_NAMES:
                args = item.get("arguments", item.get("parameters", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                found.append({"name": item["name"], "arguments": args if isinstance(args, dict) else {}})
        i = end
    return found


def _norm_args(args):
    """같은 요청인지 비교하기 위한 정규화 (패키지 접두어·파일 줄 번호 표기 차이 무시)."""
    import re as _re
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            v = v.replace("com.example.orderservice.", "")
            v = _re.sub(r"\(\w+\.java:\d+\)$", "", v).strip()
            v = ".".join(v.split(".")[-2:]) if k == "symbol" else v
        out[k] = v
    return _j(out)


def _secs_between(a, b):
    from datetime import datetime as _dt
    fmt = "%H:%M:%S"
    return abs((_dt.strptime(a, fmt) - _dt.strptime(b, fmt)).total_seconds())


def _defect_methods(r):
    """코드 점검에서 결함 패턴(오류 발생 줄 표시 제외)이 나온 메서드 이름."""
    found = [x for x in r.get("findings", []) if "오류 발생 (스택" not in x]
    if not found:
        return []
    sym = r.get("symbol", "").split("(")[0].replace("com.example.orderservice.", "")
    parts = sym.split(".")
    return [{"method": ".".join(parts[-2:]), "findings": found}] if len(parts) >= 2 else []


def _finding_kind(text):
    """findings 줄 하나(문자열 하나)의 kind. 결함 하나(메서드 하나)의 findings 리스트에는
    서로 다른 시나리오의 패턴이 여러 개 같이 들어있을 수 있다 — 예를 들어 PaymentService.pay
    하나에 S11(사전 재시도 폭주)·S13(민감정보 로그)·S14(부동소수점)·S16(비멱등 재시도)이 전부
    if(chaos.isOn(...)) 분기로 들어있어서, 코드를 한 번 훑으면 네 패턴이 다 discovered되어 같은
    defect 딕셔너리의 findings 리스트 하나에 같이 담긴다. 그래서 이 함수는 findings 전체를
    이어붙인 텍스트가 아니라 줄 하나하나에 대해 불려야 한다(_defect_kinds/_matching_findings가
    그렇게 쓴다) — 안 그러면 여러 kind가 하나로 뭉개진다."""
    return ("memory" if "누적" in text else
            "connection" if "커넥션" in text else
            "concurrency" if "원자적이지 않음" in text or "lost update" in text else
            # catch 블록 안에서 멱등성 확인 없이 재시도하는 결함(S16)은 "불필요한 사전 재시도"(S11)와
            # 같은 메서드의 findings에 같이 들어있을 수 있다 — 반드시 "retry"보다 먼저 체크해서, 이
            # 줄 자체는 "idempotency"로 분류되게 한다 (FOCUS_KINDS의 "idempotency"와 짝을 이룬다).
            "idempotency" if "멱등성 확인 없이" in text else
            "retry" if "불필요한 사전 재시도" in text else
            "cache" if "캐시" in text else
            # "스레드 누수"(요청마다 new Thread() 생성, S15)는 "반복문"/"Future.get()"(실제 응답 지연,
            # S7 등)과 증상 문구는 같은 "느려진다"로 겹치지만, 실제로 응답 지연이 관찰되는지 여부가
            # 다르다(스레드 누수는 응답은 빠르고 스레드만 쌓임) — 그래서 별도 kind로 둬서 _best_defect()가
            # 실제 관찰된 지연 유무로 둘을 구분할 수 있게 한다. 같은 "slow"로 묶으면 두 결함이 둘 다
            # 매칭돼 목록 순서(코드에 먼저 등장하는 쪽)로 엉뚱하게 골라진다.
            "thread_leak" if "스레드 누수" in text else
            "slow" if "반복문" in text or "Future.get()" in text else
            "null" if "null" in text else "other")


def _findings_text(r, focus_kind=None):
    if not r.get("findings"):
        return ""
    lines = []
    for x in r["findings"]:
        k = _finding_kind(x)
        mark = "(증상과 다른 종류) " if focus_kind and "오류 발생 (스택" not in x and k != focus_kind else ""
        lines.append(f"- {mark}{x}")
    return "\n[코드 점검]\n" + "\n".join(lines)


def _key_tokens(text):
    """근거 문장에서 조사 결과와 대조할 핵심 토큰 (숫자, 영문 식별자 등)."""
    import re
    return [t for t in re.findall(r"[A-Za-z_][\w.$]{4,}|\d+(?:\.\d+)?", text) if len(t) >= 2][:8]


def run_agent(symptom: str, cfg: AgentConfig | None = None, emit=lambda e: None) -> dict:
    cfg = cfg or AgentConfig()
    start = time.time()
    final = build_graph(cfg, emit).invoke({"symptom": symptom}, {"recursion_limit": 30})
    return {"report": final.get("report"), "raw_report": final.get("raw_report"), "warnings": final.get("warnings", []),
            "tool_log": final.get("tool_log", []), "context": final.get("context"),
            "timings": {**final.get("timings", {}), "total": round(time.time() - start, 1)},
            "model": cfg.model}
