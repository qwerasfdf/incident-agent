"""
터미널에서 Agent 실행

  py -3 agent/run_agent.py "주문 생성이 자꾸 실패해요"
  py -3 agent/run_agent.py "결제가 안 돼요" --model llama3.2:3b
  py -3 agent/run_agent.py "..." --json          # 결과를 JSON으로만 출력 (평가 스크립트용)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from incident_agent.config import AgentConfig  # noqa: E402
from incident_agent.graph import run_agent  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="장애 대응 Agent")
    p.add_argument("symptom", help="증상 설명")
    p.add_argument("--model", help="Ollama 모델 (기본 qwen2.5:3b)")
    p.add_argument("--service-url", help="서비스 주소 (기본 http://localhost:8081)")
    p.add_argument("--ollama-url", help="Ollama 주소 (기본 http://localhost:11434)")
    p.add_argument("--window", type=int, help="최근 몇 분 로그를 볼지 (기본 10)")
    p.add_argument("--json", action="store_true", help="결과만 JSON으로 출력")
    p.add_argument("--verbose", action="store_true", help="조사 단계의 상세 내용까지 출력")
    a = p.parse_args()

    cfg = AgentConfig()
    if a.model:
        cfg.model = a.model
    if a.service_url:
        cfg.service_url = a.service_url
    if a.ollama_url:
        cfg.ollama_url = a.ollama_url
    if a.window:
        cfg.log_window_min = a.window

    def emit(e):
        if a.json:
            return
        print(f"[{e['time']}] {e['title']}")
        if a.verbose and e.get("detail"):
            print("    " + str(e["detail"]).replace("\n", "\n    ")[:3000])

    result = run_agent(a.symptom, cfg, emit)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
        return
    r = result["report"]
    print("\n" + "=" * 70)
    print(f"원인      : {r.get('summary')}")
    print(f"유형      : {r.get('cause_type')}   위치: {r.get('location')}   확신도: {r.get('confidence')}")
    print("근거      :")
    for ev in r.get("evidence", []):
        print(f"  - {ev}")
    print(f"즉시 조치 : {r.get('immediate_action')}")
    print(f"근본 해결 : {r.get('fix')}")
    for w in result["warnings"]:
        print(f"[검증 경고] {w}")
    print(f"\n모델 {result['model']} · 총 {result['timings']['total']}초 · 도구 {len(result['tool_log'])}회 "
          f"· 단계별 {result['timings']}")


if __name__ == "__main__":
    main()
