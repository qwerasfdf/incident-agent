from __future__ import annotations
"""order-service(장애 재현 서비스)와 로그 파일을 읽는 함수 모음. 화면(app.py)에서만 사용한다."""
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- HTTP

def call(base, method, path, body=None, timeout=10):
    """(상태코드, 소요초, 응답) 반환. 연결 실패는 상태코드 -1, 시간 초과는 0."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw, status = res.read().decode("utf-8"), res.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", errors="replace"), e.code
    except (TimeoutError, OSError) as e:
        is_timeout = isinstance(e, TimeoutError) or "timed out" in str(e)
        return (0 if is_timeout else -1), time.time() - start, str(e)
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw
    return status, time.time() - start, parsed


def metric(base, name, tag=None):
    """Actuator 지표 값 1개. 없으면 None."""
    path = f"/actuator/metrics/{name}" + (f"?tag={tag}" if tag else "")
    status, _, body = call(base, "GET", path, timeout=5)
    if status != 200 or not isinstance(body, dict):
        return None
    for m in body.get("measurements", []):
        if m.get("statistic") in ("VALUE", "COUNT"):
            return m["value"]
    return body["measurements"][0]["value"] if body.get("measurements") else None


@dataclass
class Snapshot:
    reachable: bool
    health: str = "UNKNOWN"
    db: str = "UNKNOWN"
    heap_used_mb: float | None = None
    heap_max_mb: float | None = None
    conn_active: float | None = None
    conn_max: float | None = None
    conn_timeouts: float | None = None
    server_errors: float | None = None
    taken_at: float = field(default_factory=time.time)


def snapshot(base):
    status, _, health = call(base, "GET", "/actuator/health", timeout=5)
    if status in (-1, 0):
        return Snapshot(reachable=False)
    snap = Snapshot(reachable=True)
    if isinstance(health, dict):
        snap.health = health.get("status", "UNKNOWN")
        snap.db = health.get("components", {}).get("db", {}).get("status", "UNKNOWN")
    used = metric(base, "jvm.memory.used", "area:heap")
    mx = metric(base, "jvm.memory.max", "area:heap")
    snap.heap_used_mb = used / 1048576 if used is not None else None
    snap.heap_max_mb = mx / 1048576 if mx and mx > 0 else None
    snap.conn_active = metric(base, "hikaricp.connections.active")
    snap.conn_max = metric(base, "hikaricp.connections.max")
    snap.conn_timeouts = metric(base, "hikaricp.connections.timeout")
    snap.server_errors = metric(base, "http.server.requests", "outcome:SERVER_ERROR") or 0
    return snap


# ---------------------------------------------------------------- 로그

LOG_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) (?P<level>[A-Z]+)\s+"
    r"\[(?P<thread>[^\]]*)\] \[(?P<rid>[^\]]*)\] (?P<logger>\S+) - (?P<msg>.*)$")


@dataclass
class LogEntry:
    ts: str
    level: str
    request_id: str
    logger: str
    message: str
    detail: list[str] = field(default_factory=list)  # 스택 트레이스 등 이어지는 줄


def read_log_tail(path, max_bytes=2_000_000):
    """로그 파일 끝부분을 읽어 LogEntry 목록으로 (오래된 것부터)."""
    p = Path(path)
    if not p.exists():
        return None
    with p.open("rb") as f:
        size = p.stat().st_size
        f.seek(max(0, size - max_bytes))
        text = f.read().decode("utf-8", errors="replace")
    entries: list[LogEntry] = []
    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if m:
            entries.append(LogEntry(m["ts"], m["level"], m["rid"], m["logger"], m["msg"]))
        elif entries and line.strip():
            entries[-1].detail.append(line)
    return entries
