"""
정답률 스코어보드.

Agent가 낸 결론(LLM 1차 결론 / 코드 보정 후 최종 결론)을, 그 분석 시점에 실제로 켜져 있던
chaos 시나리오(=정답)와 대조해서 누적 기록한다.

주의: 이 모듈은 Agent의 조사/추론 과정(agent/incident_agent 패키지)과는 완전히 분리되어 있다.
Agent는 이 모듈도, /chaos API도 전혀 참조하지 않는다 — 정답을 미리 들여다보고 끼워맞추는 것을
막기 위해서다. 여기서는 Agent가 이미 결론을 다 낸 '이후'에, 채점용으로만 /chaos 상태를 읽는다.
"""
from __future__ import annotations

import json
from pathlib import Path

# 시나리오 코드 -> 기대 원인 유형(CAUSE_TYPES 중 하나).
# ChaosScenario.java의 정의를 기준으로, 그 장애가 실제로 어느 범주에 속하는지 분류한 것.
SCENARIO_EXPECTED = {
    "S1": "코드 결함",   # 메모리 누수
    "S2": "코드 결함",   # DB 커넥션 고갈
    "S3": "코드 결함",   # 응답 지연
    "S4": "설정 오류",   # 잘못된 설정값
    "S5": "외부 시스템",  # 외부 API 타임아웃
    "S6": "코드 결함",   # 예외 처리 누락
    "S7": "코드 결함",   # 비동기 처리 오용
    "S8": "설정 오류",   # 타임존 설정 오류
    "S9": "코드 결함",   # 캐시 미반영
    "S10": "코드 결함",  # 결제 예외 삼킴
    "S11": "코드 결함",  # 재시도 폭주
    "S12": "코드 결함",  # 동시성 유실 업데이트

    # --- 코드 결함 추가 (S13~S17) ---
    "S13": "코드 결함",  # 로그에 민감정보 평문 노출
    "S14": "코드 결함",  # 금액 계산에 부동소수점 사용
    "S15": "코드 결함",  # 요청마다 스레드 생성(미반환)
    "S16": "코드 결함",  # 비멱등 재시도로 중복 처리 위험
    "S17": "코드 결함",  # 잘못된 종료조건(무한 루프 위험)

    # --- 설정 오류 추가 (S18~S23) ---
    "S18": "설정 오류",  # DB 커넥션 풀 크기 과소 설정
    "S19": "설정 오류",  # 로그 레벨 설정 오류(TRACE 방치)
    "S20": "설정 오류",  # 쿠폰 기능 플래그 오설정
    "S21": "설정 오류",  # 결제 타임아웃 설정값 과소
    "S22": "설정 오류",  # 검색 모드 설정 오류(레거시 전환)
    "S23": "설정 오류",  # 주문 최대 수량 설정값 오류

    # --- 외부 시스템 추가 (S24~S30) ---
    "S24": "외부 시스템",  # 외부 PG 응답 스키마 깨짐
    "S25": "외부 시스템",  # 외부 PG 서버 오류(500)
    "S26": "외부 시스템",  # 외부 PG 레이트 리밋(429)
    "S27": "외부 시스템",  # 외부 PG 점검중(503)
    "S28": "외부 시스템",  # 외부 PG 게이트웨이 오류(502)
    "S29": "외부 시스템",  # 외부 PG 간헐적 실패
    "S30": "외부 시스템",  # 외부 PG 인증 실패(401)
}

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "scoreboard.jsonl"


def expected_for(active_codes: list[str]) -> str | None:
    """켜져 있는 시나리오가 정확히 하나일 때만 정답을 판단한다. 0개·2개 이상이면 채점 보류(None)."""
    if len(active_codes) != 1:
        return None
    return SCENARIO_EXPECTED.get(active_codes[0])


def record(symptom: str, raw: dict, final: dict, active_codes: list[str], at: str,
           path: Path = DEFAULT_PATH) -> dict:
    """분석 1건을 채점해서 JSONL에 한 줄 추가하고, 그 줄(dict)을 반환한다."""
    expected = expected_for(active_codes)
    llm_ct = (raw or {}).get("cause_type")
    final_ct = (final or {}).get("cause_type")
    row = {
        "at": at,
        "symptom": symptom[:80],
        "active_scenarios": active_codes,
        "expected": expected,
        "llm_cause_type": llm_ct,
        "final_cause_type": final_ct,
        "llm_correct": (llm_ct == expected) if expected else None,
        "final_correct": (final_ct == expected) if expected else None,
        "corrected": bool(raw) and raw != final,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def load_rows(path: Path = DEFAULT_PATH) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summary(rows: list[dict]) -> dict:
    """채점 가능한(=expected가 있는) 기록만으로 LLM 단독 vs Agent 최종 정답률을 계산한다."""
    graded = [r for r in rows if r.get("expected")]
    n = len(graded)
    llm_ok = sum(1 for r in graded if r.get("llm_correct"))
    final_ok = sum(1 for r in graded if r.get("final_correct"))
    return {
        "total": len(rows),
        "graded": n,
        "llm_ok": llm_ok,
        "final_ok": final_ok,
        "llm_rate": (llm_ok / n) if n else None,
        "final_rate": (final_ok / n) if n else None,
    }
