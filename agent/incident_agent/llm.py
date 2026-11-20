"""LLM 호출 (Ollama 로컬 모델 / Claude API). 표준 라이브러리만 사용."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .config import AgentConfig


class LLMError(RuntimeError):
    pass


def chat(cfg: AgentConfig, messages, tools=None, schema=None):
    """LLM 호출. (응답 message, 소요 초)를 반환한다.
    message는 {"content": str, "tool_calls"?: [{"function": {"name","arguments"}}]} 형태로 통일한다 —
    graph.py는 provider가 Ollama든 Claude든 이 형태만 본다."""
    if cfg.provider == "claude":
        return _claude_chat(cfg, messages, tools=tools, schema=schema)
    return _ollama_chat(cfg, messages, tools=tools, schema=schema)


def _ollama_chat(cfg: AgentConfig, messages, tools=None, schema=None):
    """Ollama /api/chat 호출."""
    payload = {"model": cfg.model, "messages": messages, "stream": False,
               "options": {"temperature": 0, "num_ctx": cfg.num_ctx, "seed": 42}}
    if tools:
        payload["tools"] = tools
    if schema:
        payload["format"] = schema
    req = urllib.request.Request(cfg.ollama_url.rstrip("/") + "/api/chat",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cfg.llm_timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"Ollama 오류 {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}") from e
    except OSError as e:
        raise LLMError(f"Ollama 연결 실패 ({cfg.ollama_url}): {e}") from e
    return body.get("message", {}), time.time() - start


# --------------------------------------------------------------------- Claude

REPORT_TOOL_NAME = "submit_report"


def _to_claude_messages(messages):
    """graph.py가 쓰는 공용 메시지 형식(OpenAI 스타일: role system/user/assistant/tool,
    assistant의 tool_calls, tool의 tool_name+content)을 Claude Messages API 형식으로 바꾼다.

    - system 메시지는 별도로 뽑아 최상위 system 파라미터로 보낸다.
    - assistant의 tool_calls 각각에 순서대로 id를 붙여 tool_use 블록으로 만들고,
      바로 뒤에 오는 role=tool 메시지들을 같은 id의 tool_result 블록(user)으로 짝지운다.
    - Claude는 역할(user/assistant)이 번갈아야 하므로, 같은 역할이 연속되면 한 메시지로 합친다
      (예: 여러 tool_result + 그다음 안내문이 하나의 user 메시지가 됨).
    """
    system_parts = []
    out: list[dict] = []
    pending_ids: list[str] = []
    counter = 0

    def push(role, block):
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
        elif role == "user":
            push("user", {"type": "text", "text": m.get("content") or ""})
        elif role == "assistant":
            if m.get("content"):
                push("assistant", {"type": "text", "text": m["content"]})
            pending_ids = []
            for tc in (m.get("tool_calls") or []):
                counter += 1
                tid = f"toolu_{counter}"
                pending_ids.append(tid)
                fn = tc.get("function", {})
                push("assistant", {"type": "tool_use", "id": tid, "name": fn.get("name"),
                                    "input": fn.get("arguments") or {}})
        elif role == "tool":
            tid = pending_ids.pop(0) if pending_ids else f"toolu_orphan_{counter}"
            push("user", {"type": "tool_result", "tool_use_id": tid, "content": m.get("content") or ""})

    system = "\n\n".join(system_parts) if system_parts else None
    # 빈 text 블록은 Claude가 거부하므로 제거하고, 그 결과 content가 통째로 빈 메시지는 뺀다
    for msg in out:
        msg["content"] = [b for b in msg["content"] if b.get("type") != "text" or b.get("text")]
    out = [m for m in out if m["content"]]
    return system, out


def _tool_specs_to_claude(tools):
    """OpenAI 함수형({"type":"function","function":{...}})을 Claude 도구 형식으로 바꾼다."""
    return [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
             "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}})}
            for t in tools]


def _claude_chat(cfg: AgentConfig, messages, tools=None, schema=None):
    """Anthropic Messages API 호출.
    schema가 있으면(=graph.py가 JSON 보고서를 요구하는 경우) submit_report라는 도구 하나를
    강제로 호출하게 만들어서(tool_choice) 구조화된 JSON을 안정적으로 받는다 — Ollama의
    format=schema와 같은 역할."""
    if not cfg.anthropic_api_key:
        raise LLMError("Claude API 키가 설정되지 않았습니다 (ANTHROPIC_API_KEY).")
    system, claude_messages = _to_claude_messages(messages)
    payload: dict = {"model": cfg.model, "max_tokens": 2048, "messages": claude_messages}
    if system:
        payload["system"] = system
    if schema:
        payload["tools"] = [{"name": REPORT_TOOL_NAME, "description": "원인 보고서를 지정된 JSON 형식으로 제출한다",
                              "input_schema": schema}]
        payload["tool_choice"] = {"type": "tool", "name": REPORT_TOOL_NAME}
    elif tools:
        payload["tools"] = _tool_specs_to_claude(tools)

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": cfg.anthropic_api_key,
                 "anthropic-version": cfg.anthropic_version},
        method="POST")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cfg.llm_timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"Claude API 오류 {e.code}: {detail}") from e
    except OSError as e:
        raise LLMError(f"Claude API 연결 실패: {e}") from e

    blocks = body.get("content", [])
    if schema:
        # tool_choice로 강제했으므로 submit_report의 input만 꺼내 graph.py가 기대하는
        # json.loads(content) 형태로 맞춘다. 사족 텍스트가 섞여도 무시한다.
        report_input = next((b.get("input", {}) for b in blocks
                             if b.get("type") == "tool_use" and b.get("name") == REPORT_TOOL_NAME), {})
        return {"content": json.dumps(report_input, ensure_ascii=False)}, time.time() - start

    text_parts, tool_calls = [], []
    for block in blocks:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append({"function": {"name": block.get("name"), "arguments": block.get("input", {})}})
    message = {"content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message, time.time() - start
