"""
Ollama 접속·성능 확인 스크립트 (1주차 작업 8, 표준 라이브러리만 사용)

확인하는 것
  1) 서버 접속과 설치된 모델 목록
  2) 모델별 응답 시간, 초당 생성 토큰 수
  3) 모델별 도구 호출(tool calling) 지원 여부 → 2주차 Agent 모델 선정 근거

사용 예
  python agent/check_ollama.py --base-url http://<AI서버IP>:11434
  python agent/check_ollama.py --base-url http://<AI서버IP>:11434 --models qwen2.5:7b-instruct exaone3.5:7.8b
  (환경변수 OLLAMA_BASE_URL 을 설정해 두면 --base-url 생략 가능)

결과는 화면 표와 agent/results/ollama_check_<시각>.csv 로 저장된다.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GEN_PROMPT = (
    "다음 서버 로그의 원인을 한 문장으로 추정해줘.\n"
    "ERROR OrderHikariPool - Connection is not available, request timed out after 3000ms "
    "(total=10, active=10, idle=0, waiting=3)"
)

TOOL_PROMPT = "주문 서비스의 현재 DB 커넥션 사용 개수를 확인해줘."
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_metric",
        "description": "Spring Boot 서비스의 지표 값을 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "지표 이름 (예: hikaricp.connections.active, jvm.memory.used)"},
            },
            "required": ["name"],
        },
    },
}]

EMBEDDING_HINTS = ("bge", "embed", "e5-", "minilm")


def post(base, path, payload, timeout):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def get(base, path, timeout):
    with urllib.request.urlopen(base + path, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def http_error_text(e):
    try:
        return json.loads(e.read().decode("utf-8")).get("error", str(e))
    except Exception:  # noqa: BLE001
        return str(e)


def test_generate(base, model, timeout):
    start = time.time()
    r = post(base, "/api/generate", {"model": model, "prompt": GEN_PROMPT, "stream": False,
                                     "options": {"temperature": 0, "num_predict": 120}}, timeout)
    wall = time.time() - start
    tokens = r.get("eval_count", 0)
    eval_sec = r.get("eval_duration", 0) / 1e9
    return {
        "wall_sec": round(wall, 2),
        "load_sec": round(r.get("load_duration", 0) / 1e9, 2),
        "tokens_per_sec": round(tokens / eval_sec, 1) if eval_sec else "",
        "answer": " ".join(r.get("response", "").split())[:120],
    }


def test_tools(base, model, timeout):
    try:
        r = post(base, "/api/chat", {"model": model, "stream": False, "tools": TOOLS,
                                     "messages": [{"role": "user", "content": TOOL_PROMPT}],
                                     "options": {"temperature": 0}}, timeout)
    except urllib.error.HTTPError as e:
        msg = http_error_text(e)
        if "does not support tools" in msg:
            return "미지원", ""
        return "오류", msg[:100]
    calls = r.get("message", {}).get("tool_calls") or []
    if not calls:
        return "호출 안 함", " ".join(r.get("message", {}).get("content", "").split())[:80]
    fn = calls[0].get("function", {})
    args = fn.get("arguments", {})
    ok = fn.get("name") == "get_metric" and "hikaricp" in json.dumps(args)
    return ("정상" if ok else "부정확"), f"{fn.get('name')}({json.dumps(args, ensure_ascii=False)})"


def main():
    p = argparse.ArgumentParser(description="Ollama 접속·성능·도구 호출 확인")
    p.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    p.add_argument("--models", nargs="*", help="확인할 모델 (생략 시 임베딩 모델을 뺀 전체)")
    p.add_argument("--timeout", type=int, default=300, help="요청당 최대 대기 초 (첫 호출은 모델 로딩으로 느림)")
    args = p.parse_args()
    base = args.base_url.rstrip("/")

    print(f"Ollama 서버: {base}")
    try:
        tags = get(base, "/api/tags", timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"[실패] 서버에 연결할 수 없습니다: {e}")
        print(" - 서버 주소/포트(11434), 사내 방화벽, Ollama의 OLLAMA_HOST=0.0.0.0 설정 여부를 확인하세요.")
        sys.exit(1)

    installed = [m["name"] for m in tags.get("models", [])]
    print(f"[성공] 접속 확인. 설치된 모델 {len(installed)}개: {', '.join(installed) or '(없음)'}")

    models = args.models or [m for m in installed if not any(h in m.lower() for h in EMBEDDING_HINTS)]
    missing = [m for m in models if m not in installed]
    if missing:
        print(f"[주의] 설치되지 않은 모델은 건너뜀: {', '.join(missing)}")
    models = [m for m in models if m in installed]

    rows = []
    for m in models:
        print(f"\n▶ {m}")
        row = {"model": m}
        try:
            row.update(test_generate(base, m, args.timeout))
            print(f"  응답 {row['wall_sec']}초 (로딩 {row['load_sec']}초), {row['tokens_per_sec']} 토큰/초")
            print(f"  답변: {row['answer']}")
        except Exception as e:  # noqa: BLE001
            row.update({"wall_sec": "", "load_sec": "", "tokens_per_sec": "", "answer": f"오류: {e}"})
            print(f"  [오류] 생성 실패: {e}")
        try:
            row["tool_call"], row["tool_detail"] = test_tools(base, m, args.timeout)
        except Exception as e:  # noqa: BLE001
            row["tool_call"], row["tool_detail"] = "오류", str(e)[:100]
        print(f"  도구 호출: {row['tool_call']} {row['tool_detail']}")
        rows.append(row)

    if not rows:
        return
    print("\n" + "=" * 72)
    print(f"{'모델':<28}{'응답(초)':>9}{'토큰/초':>9}  도구 호출")
    print("-" * 72)
    for r in rows:
        print(f"{r['model']:<28}{str(r['wall_sec']):>9}{str(r['tokens_per_sec']):>9}  {r['tool_call']}")

    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"ollama_check_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["model", "wall_sec", "load_sec", "tokens_per_sec",
                                          "tool_call", "tool_detail", "answer"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n결과 저장: {out}")
    print("※ 응답 시간은 두 번째 실행부터가 실제 값입니다 (첫 호출은 모델 로딩 시간 포함).")


if __name__ == "__main__":
    main()
