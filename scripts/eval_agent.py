"""
Agent 진단 정확도 평가

시나리오마다: 모든 장애 끄기 → 기준점 저장 → 장애 켜고 증상 발생 → Agent 분석 → 정답지와 비교 → 장애 끄기

  py -3 scripts/eval_agent.py                          # S1~S6 전체, 기본 모델
  py -3 scripts/eval_agent.py --scenarios S2 S3        # 일부만
  py -3 scripts/eval_agent.py --model llama3.2:3b      # 다른 모델로
  py -3 scripts/eval_agent.py --repeat 2               # 시나리오마다 2번씩

결과: agent/results/eval_<모델>_<시각>.json / .csv
"""
import argparse
import contextlib
import csv
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import chaos_demo  # noqa: E402
from incident_agent.config import AgentConfig  # noqa: E402
from incident_agent.graph import run_agent  # noqa: E402
from incident_agent.tools import Tools  # noqa: E402

# 사용자가 실제로 말할 법한 증상 (원인 힌트 없음)
SYMPTOMS = {
    "S1": "주문 생성이 처음에는 잘 되다가 어느 순간부터 계속 실패합니다.",
    "S2": "주문 생성이 몇 건 되다가 갑자기 실패해요. 주문 목록 조회는 됩니다.",
    "S3": "주문 검색이 너무 느려요. 에러는 안 납니다.",
    "S4": "결제 요청이 전부 실패합니다. 주문은 정상이에요.",
    "S5": "결제 버튼을 누르면 한참 기다리다가 실패합니다.",
    "S6": "일부 주문의 상세 조회만 오류가 납니다.",
}
TYPE_MAP = {"code": "코드 결함", "config": "설정 오류", "external": "외부 시스템"}


def score(answer, report):
    exp_type = TYPE_MAP[answer["cause_type"]]
    got_type = report.get("cause_type", "")
    loc = (report.get("location") or "").lower()
    type_ok = got_type == exp_type
    if answer["cause_type"] == "code":
        loc_ok = answer["root_cause_location"].split(".")[-1].lower() in loc
    elif answer["cause_type"] == "config":
        loc_ok = any(k in loc for k in ("base-url", "baseurl", "payment.external"))
    else:
        loc_ok = type_ok
    return {"expected_type": exp_type, "type_ok": type_ok, "location_ok": loc_ok, "correct": type_ok and loc_ok}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenarios", nargs="*", default=list(SYMPTOMS))
    p.add_argument("--model")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--service-url", default="http://localhost:8081")
    a = p.parse_args()

    answers = {x["id"]: x for x in json.loads((ROOT / "scenarios" / "answer_key.json").read_text(encoding="utf-8"))}
    chaos_demo.BASE = a.service_url.rstrip("/")
    cfg = AgentConfig()
    cfg.service_url = a.service_url
    if a.model:
        cfg.model = a.model
    tools = Tools(cfg)

    rows = []
    for code in [c.upper() for c in a.scenarios]:
        for rep in range(1, a.repeat + 1):
            chaos_demo.call("POST", "/chaos/reset")
            time.sleep(1.5)
            cfg.stats_baseline, cfg.log_since = None, None
            cfg.stats_baseline = tools.snapshot_baseline()
            cfg.log_since = datetime.now().replace(microsecond=0)
            print(f"\n[{code}] {answers[code]['title']} (반복 {rep}) — 증상 발생 중...", flush=True)
            chaos_demo.chaos(code, True)
            with contextlib.redirect_stdout(io.StringIO()):
                chaos_demo.SCENARIOS[code][1]()
            print(f"  Agent 분석 중... (증상: {SYMPTOMS[code]})", flush=True)
            try:
                result = run_agent(SYMPTOMS[code], cfg)
                report, err = result["report"] or {}, None
            except Exception as e:  # noqa: BLE001
                result, report, err = {"timings": {}, "warnings": [], "tool_log": []}, {}, str(e)
            chaos_demo.chaos(code, False)
            raw = result.get("raw_report") or {}
            raw_sc = score(answers[code], raw) if raw else {"correct": False}
            sc = score(answers[code], report) if not err else \
                {"expected_type": TYPE_MAP[answers[code]["cause_type"]], "type_ok": False, "location_ok": False,
                 "correct": False}
            mark = "✅" if sc["correct"] else ("🔶" if sc["type_ok"] else "❌")
            print(f"  {mark} 판단: {report.get('cause_type')} / {report.get('location')}  "
                  f"[LLM 단독: {'정답' if raw_sc['correct'] else '오답'}]  "
                  f"(정답: {sc['expected_type']} / {answers[code]['root_cause_location']})  "
                  f"{result['timings'].get('total', '-')}초")
            if err:
                print(f"  오류: {err}")
            rows.append({"scenario": code, "repeat": rep, "model": cfg.model, **sc, "llm_only_correct": raw_sc["correct"],
                         "got_type": report.get("cause_type"), "got_location": report.get("location"),
                         "confidence": report.get("confidence"), "summary": report.get("summary"),
                         "seconds": result["timings"].get("total"), "tool_calls": len(result["tool_log"]),
                         "llm_tool_calls": sum(1 for t in result["tool_log"] if t.get("by") == "LLM"),
                         "warnings": " / ".join(result["warnings"]), "error": err, "report": report})

    n = len(rows)
    correct = sum(r["correct"] for r in rows)
    type_ok = sum(r["type_ok"] for r in rows)
    secs = [r["seconds"] for r in rows if r["seconds"]]
    llm_only = sum(r["llm_only_correct"] for r in rows)
    print("\n" + "=" * 70)
    print(f"모델 {cfg.model} · {n}건 · 정답(유형+위치) {correct}/{n} ({correct / n * 100:.0f}%) · "
          f"유형만 {type_ok}/{n} ({type_ok / n * 100:.0f}%) · LLM 단독 정답 {llm_only}/{n} "
          f"({llm_only / n * 100:.0f}%) · 평균 {sum(secs) / max(1, len(secs)):.0f}초")

    out_dir = ROOT / "agent" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{cfg.model.replace(':', '-')}_{datetime.now():%Y%m%d_%H%M%S}"
    (out_dir / f"{stem}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with (out_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        cols = [k for k in rows[0] if k != "report"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"결과 저장: {out_dir / stem}.json / .csv")


if __name__ == "__main__":
    main()
