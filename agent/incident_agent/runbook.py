"""
사내 대응 매뉴얼(runbook.md)에서, 확정된 원인(cause_type/location/evidence)과 가장 관련 있는
절을 찾아준다. "지금 할 조치"/"근본 해결"을 LLM이 지어내지 않고, 이 문서에 실제로 있는 내용에
근거해서 쓰게 하는 데 쓴다.

의도적으로 임베딩/벡터 검색을 쓰지 않았다 — 매뉴얼 절이 10개 남짓뿐이고, cause_type이라는
확실한 1차 필터가 이미 있어서, 키워드 매칭만으로 충분히 정확하고 디버깅하기도 훨씬 쉽다.
"""
from __future__ import annotations

import re
from pathlib import Path

RUNBOOK_PATH = Path(__file__).resolve().parent / "runbook.md"

# 절 번호 -> (연결된 cause_type, 매칭에 쓸 키워드들).
# 키워드는 전부 소문자로 비교한다 (영문 키워드 대응).
SECTION_META = {
    "4.1": ("코드 결함", ("메모리", "memory", "heap", "audit", "cache", "캐시",
                           "누수", "leak", "outofmemory")),
    "4.2": ("코드 결함", ("커넥션", "connection", "hikari", "pool", "db")),
    "4.3": ("코드 결함", ("동시", "재고", "수량", "lost update", "race", "동시성")),
    "4.4": ("코드 결함", ("캐시", "cache", "반영", "invalidate")),
    "4.5": ("코드 결함", ("null", "예외", "exception", "nullpointer")),
    "4.6": ("코드 결함", ("재시도", "retry", "백오프", "backoff", "폭주", "사전 재시도")),
    "4.7": ("코드 결함", ("카드번호", "비밀번호", "cardnumber", "password", "로그", "민감", "평문")),
    "4.8": ("코드 결함", ("double", "float", "points", "포인트", "부동소수점", "반올림")),
    "4.9": ("코드 결함", ("thread", "스레드", "new thread")),
    "4.10": ("코드 결함", ("멱등", "idempot", "중복", "재시도")),
    "4.11": ("코드 결함", ("while", "무한", "infinite", "종료 조건", "반복")),
    "5.1": ("설정 오류", ("zone", "timezone", "config", "설정", "타임존", "tz")),
    "5.2": ("설정 오류", ("커넥션", "connection", "hikari", "pool", "maximum-pool-size", "db")),
    "5.3": ("설정 오류", ("log", "trace", "로그", "로그 레벨")),
    "5.4": ("설정 오류", ("coupon", "쿠폰")),
    "5.5": ("설정 오류", ("read-timeout", "timeout", "타임아웃", "payment")),
    "5.6": ("설정 오류", ("search", "legacy", "검색", "레거시")),
    "5.7": ("설정 오류", ("max-quantity", "수량", "최대")),
    # "pg"/"외부"/"결제 대행사" 등 공통 단어는 외부 시스템 전체 케이스에 늘 나오므로(위치 문구가
    # "외부 결제 대행사(PG) API"로 고정) 6.1절만 과도하게 유리해져 제외한다 — timeout 특유 단어만 남긴다.
    "6.1": ("외부 시스템", ("timeout", "타임아웃", "지연", "응답 지연")),
    "6.2": ("외부 시스템", ("deserializ", "parse", "malformed", "스키마", "역직렬화")),
    "6.3": ("외부 시스템", ("500", "502", "503", "internal_error", "bad_gateway", "service_unavailable", "게이트웨이")),
    "6.4": ("외부 시스템", ("429", "rate limit", "too many requests", "레이트")),
    "6.5": ("외부 시스템", ("401", "unauthorized", "서명", "signature", "인증")),
    "6.6": ("외부 시스템", ("intermittent", "간헐적")),
}


def _parse_sections(text: str) -> dict[str, dict]:
    sections = {}
    blocks = re.split(r"(?m)^## ", text)[1:]  # 첫 블록은 헤더 앞부분이라 버림
    for b in blocks:
        head, _, body = b.partition("\n")
        m = re.match(r"([\d.]+)\s+(.*)", head.strip())
        if not m:
            continue
        sid, title = m.group(1), m.group(2)
        title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()  # 끝의 "(코드 결함)" 등 괄호 제거
        sections[sid] = {"id": sid, "title": title, "body": body.strip()}
    return sections


_cache: dict | None = None


def _sections() -> dict:
    global _cache
    if _cache is None:
        text = RUNBOOK_PATH.read_text(encoding="utf-8") if RUNBOOK_PATH.exists() else ""
        _cache = _parse_sections(text)
    return _cache


def find_section(report: dict) -> dict | None:
    """report(cause_type/location/evidence/summary)와 가장 관련 있는 매뉴얼 절을 찾는다.
    cause_type이 같은 후보들 중, 위치·근거 텍스트에 키워드가 하나라도 걸리는 것만 반환한다.
    하나도 안 걸리면 지어낸 근거로 이어질 수 있으므로 None을 반환한다 (억지로 끼워맞추지 않음)."""
    sections = _sections()
    if not sections:
        return None
    cause_type = report.get("cause_type")
    text = " ".join([
        str(report.get("location") or ""),
        " ".join(str(e) for e in (report.get("evidence") or [])),
        str(report.get("summary") or ""),
    ]).lower()

    best_id, best_score = None, 0
    for sid, (ct, keywords) in SECTION_META.items():
        if ct != cause_type or sid not in sections:
            continue
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_id, best_score = sid, score

    if not best_id:
        return None
    sec = sections[best_id]
    return {"id": sec["id"], "title": sec["title"], "body": sec["body"]}
