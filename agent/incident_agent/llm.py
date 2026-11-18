"""Ollama 호출 (표준 라이브러리만 사용)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .config import AgentConfig


class LLMError(RuntimeError):
    pass


def chat(cfg: AgentConfig, messages, tools=None, schema=None):
    """Ollama /api/chat 호출. (응답 message, 소요 초) 반환."""
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
