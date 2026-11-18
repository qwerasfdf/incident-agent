"""
Agent 도구 모음. 모두 '읽기 전용'이다 (서비스 상태를 바꾸지 않음).

  get_status          서비스·DB 상태, 메모리, DB 커넥션 지표
  get_request_stats   API별 요청 수, 평균/최대 처리 시간, 오류 수
  get_metric          지표 1개 조회 (이름은 정해진 목록 중 선택)
  search_logs         로그 검색 (키워드·레벨)
  error_signatures    최근 경고/오류를 같은 유형끼리 묶은 요약
  config_changes      최근 설정 변경 기록
  get_config          현재 적용된 설정값 (prefix로 필터)
  read_code           클래스/메서드 소스 읽기
  read_endpoint_code  API 주소로 처리 코드 따라가기 (컨트롤러 → 서비스 → 내부 메서드)
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from .config import AgentConfig

METRIC_NAMES = [
    "jvm.memory.used", "jvm.memory.max", "jvm.gc.pause", "jvm.threads.live",
    "hikaricp.connections.active", "hikaricp.connections.idle", "hikaricp.connections.pending",
    "hikaricp.connections.max", "hikaricp.connections.timeout", "http.server.requests",
]
HIDDEN_URI_PREFIXES = ("/actuator", "/chaos", "/mock-external", "/h2-console", "/error")
# 멀티 서비스 장애 전파 시나리오(M1/M2) 추가로 inventory-service 패키지도 "우리 코드"로 인식해야 한다
APP_PACKAGES = ("com.example.orderservice", "com.example.inventoryservice")


class Tools:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg

    # ------------------------------------------------------------ HTTP 공통
    def _get(self, path, timeout=10):
        url = self.cfg.service_url.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, None
        except (OSError, json.JSONDecodeError) as e:
            return -1, str(e)

    def _metric_raw(self, name, tags=()):
        q = "&".join("tag=" + urllib.parse.quote(t, safe=":") for t in tags)
        status, body = self._get(f"/actuator/metrics/{name}" + (f"?{q}" if q else ""), timeout=5)
        if status != 200 or not isinstance(body, dict):
            return None
        return body

    def _metric(self, name, tags=(), stat=None):
        body = self._metric_raw(name, tags)
        if not body:
            return None
        for m in body.get("measurements", []):
            if stat is None or m["statistic"] == stat:
                return m["value"]
        return None

    # ------------------------------------------------------------ 상태·지표
    def get_status(self) -> dict:
        status, health = self._get("/actuator/health", timeout=5)
        if status == -1:
            return {"reachable": False, "error": f"서비스 연결 실패: {health}"}
        mb = 1048576
        used = self._metric("jvm.memory.used", ["area:heap"])
        mx = self._metric("jvm.memory.max", ["area:heap"])
        out = {
            "reachable": True,
            "health": (health or {}).get("status", f"HTTP {status}"),
            "db": (health or {}).get("components", {}).get("db", {}).get("status", "UNKNOWN"),
            "heap_used_mb": round(used / mb) if used is not None else None,
            "heap_max_mb": round(mx / mb) if mx and mx > 0 else None,
            "db_conn_active": self._metric("hikaricp.connections.active"),
            "db_conn_idle": self._metric("hikaricp.connections.idle"),
            "db_conn_pending": self._metric("hikaricp.connections.pending"),
            "db_conn_max": self._metric("hikaricp.connections.max"),
            "db_conn_timeout_total": self._metric("hikaricp.connections.timeout"),
        }
        b = self.cfg.stats_baseline
        if b and out["db_conn_timeout_total"] is not None:
            out["db_conn_timeout_total"] -= b.get("_timeouts", 0)
        if out["heap_used_mb"] and out["heap_max_mb"]:
            out["heap_usage_pct"] = round(out["heap_used_mb"] / out["heap_max_mb"] * 100)
        return out

    def get_request_stats(self) -> list[dict]:
        base = self._metric_raw("http.server.requests")
        if not base:
            return []
        tags = {t["tag"]: t["values"] for t in base.get("availableTags", [])}
        rows = []
        for uri in tags.get("uri", []):
            if uri.startswith(HIDDEN_URI_PREFIXES) or uri in ("/**", "UNKNOWN"):
                continue
            for method in tags.get("method", []):
                count = self._metric("http.server.requests", [f"uri:{uri}", f"method:{method}"], "COUNT")
                if not count:
                    continue
                total = self._metric("http.server.requests", [f"uri:{uri}", f"method:{method}"], "TOTAL_TIME") or 0
                mx = self._metric("http.server.requests", [f"uri:{uri}", f"method:{method}"], "MAX") or 0
                err5 = self._metric("http.server.requests",
                                    [f"uri:{uri}", f"method:{method}", "outcome:SERVER_ERROR"], "COUNT") or 0
                b = (self.cfg.stats_baseline or {}).get(f"{method} {uri}")
                if b:  # 기준 시점 이후 증가분만
                    count, total, err5 = count - b["count"], total - b["total"], err5 - b["err5"]
                    if count <= 0:
                        continue
                rows.append({"api": f"{method} {uri}", "count": int(count), "avg_sec": round(total / count, 3),
                             "max_sec_recent": round(mx, 3), "server_errors": int(err5)})
        rows.sort(key=lambda r: (-r["server_errors"], -r["max_sec_recent"]))
        return rows

    def snapshot_baseline(self) -> dict:
        """현재 누적 통계를 기준점으로 저장 (평가 시 시나리오마다 새로 시작하는 효과)."""
        saved, self.cfg.stats_baseline = self.cfg.stats_baseline, None
        base = {"_timeouts": self._metric("hikaricp.connections.timeout") or 0}
        raw = self._metric_raw("http.server.requests")
        tags = {t["tag"]: t["values"] for t in (raw or {}).get("availableTags", [])}
        for uri in tags.get("uri", []):
            for method in tags.get("method", []):
                t = [f"uri:{uri}", f"method:{method}"]
                c = self._metric("http.server.requests", t, "COUNT")
                if c:
                    base[f"{method} {uri}"] = {
                        "count": c, "total": self._metric("http.server.requests", t, "TOTAL_TIME") or 0,
                        "err5": self._metric("http.server.requests", t + ["outcome:SERVER_ERROR"], "COUNT") or 0}
        self.cfg.stats_baseline = saved
        return base

    def get_metric(self, name: str) -> dict:
        if name not in METRIC_NAMES:
            return {"error": f"지원하지 않는 지표입니다. 선택 가능: {', '.join(METRIC_NAMES)}"}
        tags = ["area:heap"] if name.startswith("jvm.memory") else []
        body = self._metric_raw(name, tags)
        if not body:
            return {"name": name, "error": "값 없음"}
        return {"name": name, "measurements": {m["statistic"]: round(m["value"], 3) for m in body["measurements"]}}

    def get_config(self, prefix: str = "payment.external") -> dict:
        status, body = self._get("/actuator/configprops")
        if status != 200:
            return {"error": "설정 조회 실패"}
        found = {}
        for ctx in body.get("contexts", {}).values():
            for bean in ctx.get("beans", {}).values():
                p = bean.get("prefix", "")
                if p.startswith(prefix) or prefix.startswith(p) and p:
                    found[p] = bean.get("properties", {})
        return found or {"error": f"'{prefix}'로 시작하는 설정이 없습니다."}

    # ------------------------------------------------------------ 로그
    _LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3} ([A-Z]+)\s+\[[^\]]*\] \[([^\]]*)\] (\S+) - (.*)$")

    def _read_log_file(self, path):
        p = Path(path)
        if not p.exists():
            return []
        with p.open("rb") as f:
            f.seek(max(0, p.stat().st_size - 3_000_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        entries = []
        for line in lines:
            m = self._LINE.match(line)
            if m:
                ts = datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S")
                entries.append({"ts": ts, "level": m[2], "rid": m[3], "logger": m[4], "msg": m[5], "detail": []})
            elif entries:
                entries[-1]["detail"].append(line)
        return entries

    def _entries(self, minutes=None):
        # order-service 로그 + (있으면) inventory-service 로그를 시간순으로 합쳐서 본다
        # — M1/M2처럼 재고 서비스 쪽에서 난 오류/설정 변경도 같은 조사 창에서 보여야 한다.
        entries = self._read_log_file(self.cfg.log_path)
        entries += self._read_log_file(self.cfg.log_path_inventory)
        entries.sort(key=lambda e: e["ts"])
        since = datetime.now() - timedelta(minutes=minutes or self.cfg.log_window_min)
        if self.cfg.log_since and self.cfg.log_since > since:
            since = self.cfg.log_since
        return [e for e in entries if e["ts"] >= since and e["logger"] != "chaos"]

    @staticmethod
    def _app_frames(detail, limit=3):
        frames = []
        for d in detail:
            d = d.strip()
            if d.startswith("at ") and any(d.startswith("at " + pkg) for pkg in APP_PACKAGES) \
                    and "$$" not in d and ".chaos." not in d and "RequestIdFilter" not in d:
                frames.append(d[3:])
            if len(frames) >= limit:
                break
        return frames

    @staticmethod
    def _causes(detail):
        return [d.strip()[:220] for d in detail if d.strip().startswith("Caused by")][:2]

    def _brief(self, e):
        out = {"time": e["ts"].strftime("%H:%M:%S"), "level": e["level"], "requestId": e["rid"],
               "logger": e["logger"].split(".")[-1], "message": e["msg"][:260]}
        frames = self._app_frames(e["detail"])
        if frames:
            out["app_frames"] = frames
        causes = self._causes(e["detail"])
        if causes:
            out["caused_by"] = causes
        return out

    def search_logs(self, keyword: str = "", level: str = "", limit: int = 6) -> list[dict]:
        rows = self._entries()
        if level:
            rows = [e for e in rows if e["level"] == level.upper()]
        if keyword:
            k = keyword.lower()
            rows = [e for e in rows if k in e["msg"].lower() or any(k in d.lower() for d in e["detail"])]
        return [self._brief(e) for e in rows[-limit:]]

    def requests_by_id(self, request_id: str) -> list[dict]:
        return [self._brief(e) for e in self._entries() if e["rid"] == request_id][:10]

    @staticmethod
    def _signature(msg):
        s = re.sub(r"\b[0-9a-f]{8}\b", "#", msg)
        s = re.sub(r"\d+", "#", s)
        return s[:150]

    def error_signatures(self) -> list[dict]:
        groups = {}
        for e in self._entries():
            if e["level"] not in ("WARN", "ERROR"):
                continue
            key = (e["level"], self._signature(e["msg"]))
            g = groups.setdefault(key, {"level": e["level"], "count": 0, "first": e["ts"], "last": e["ts"],
                                        "sample": self._brief(e)})
            g["count"] += 1
            g["last"] = e["ts"]
        out = []
        # ERROR 먼저, 그 안에서는 가장 최근에 발생한 순서 (이전 장애 기록보다 지금 장애를 먼저 보도록)
        for g in sorted(groups.values(), key=lambda g: (g["level"] != "ERROR", -g["last"].timestamp())):
            out.append({"level": g["level"], "count": g["count"], "first": g["first"].strftime("%H:%M:%S"),
                        "last": g["last"].strftime("%H:%M:%S"), "sample": g["sample"]})
        return out[:6]

    def config_changes(self) -> list[dict]:
        return [{"time": e["ts"].strftime("%H:%M:%S"), "message": e["msg"]}
                for e in self._entries(minutes=max(60, self.cfg.log_window_min))
                if "설정 갱신" in e["msg"] or e["logger"].startswith("config")][-5:]

    # ------------------------------------------------------------ 코드
    _FLAG_NAMES = {"S1_MEMORY_LEAK": "order-audit-log", "S2_CONNECTION_LEAK": "direct-stock-check",
                   "S3_SLOW_RESPONSE": "legacy-search-summary", "S6_NULL_HANDLING": "coupon-label-v2",
                   "S4_WRONG_CONFIG": "payment-config", "S5_EXTERNAL_TIMEOUT": "external-pg"}

    def _source_roots(self):
        roots = [Path(self.cfg.source_dir)]
        inv = Path(self.cfg.source_dir_inventory)
        if inv.exists():
            roots.append(inv)
        return roots

    def _visible_files(self):
        for root in self._source_roots():
            for p in root.rglob("*.java"):
                posix = p.as_posix()
                if not any(h in posix for h in self.cfg.hidden_paths):
                    yield p

    def _sanitize(self, text):
        """장애 재현 장치를 일반적인 기능 플래그처럼 보이게 바꾸고, 재현 전용 줄은 뺀다."""
        out = []
        for line in text.splitlines():
            if (".chaos." in line and line.strip().startswith("import")) or "ChaosResources" in line:
                out.append("")  # 줄 번호를 실제 파일·스택 트레이스와 맞추기 위해 빈 줄로 남김
                continue
            for k, v in self._FLAG_NAMES.items():
                line = line.replace(f"chaos.isOn(ChaosScenario.{k})", f'features.isOn("{v}")')
            line = line.replace("ChaosState chaos", "FeatureFlags features").replace(
                "this.chaos = chaos", "this.features = features")
            out.append(line)
        return out

    def _find_class(self, cls):
        for p in self._visible_files():
            if p.stem == cls:
                return p
        return None

    @staticmethod
    def _method_range(lines, method):
        decl = re.compile(r"^\s*(?:(?:public|private|protected|static|final|synchronized|default)\s+)*"
                          rf"([\w.]+(?:<.*>)?(?:\[\])*)\s+{re.escape(method)}\s*\(")
        for i, l in enumerate(lines):
            m = decl.search(l)
            if m and m[1] not in ("return", "new", "else", "throw") and not l.strip().startswith(("//", "*")):
                start = i
                while start > 0 and lines[start - 1].strip().startswith(("@", "/**", "*", "//")):
                    start -= 1
                depth, seen = 0, False
                for j in range(i, len(lines)):
                    depth += lines[j].count("{") - lines[j].count("}")
                    seen = seen or "{" in lines[j]
                    if seen and depth <= 0:
                        return start, j
                return start, min(len(lines) - 1, i + 40)
        return None

    def read_code(self, symbol: str, max_lines: int = 60) -> dict:
        """symbol: 'OrderService.checkStockDirectly' 또는 'OrderService' 또는 스택 프레임 문자열"""
        err_line = None
        m = re.search(r"(\w+)\.(\w+)\(\w+\.java:(\d+)\)", symbol)  # 스택 프레임 형식
        if m:
            cls, method, err_line = m[1], m[2], int(m[3])
        elif "." in symbol:
            cls, method = symbol.split(".")[-2], symbol.split(".")[-1]
        else:
            cls, method = symbol, None
        cls = cls.replace(".java", "")
        path = self._find_class(cls)
        if not path:
            return {"error": f"'{cls}' 클래스를 찾을 수 없습니다."}
        lines = self._sanitize(path.read_text(encoding="utf-8"))
        if method:
            rng = self._method_range(lines, method)
            if not rng:
                return {"error": f"{cls}에 '{method}' 메서드가 없습니다.",
                        "methods": self._method_names(lines)}
            s, e = rng
        else:
            s, e = 0, len(lines) - 1
        e = min(e, s + max_lines - 1)
        body = "\n".join(f"{i + 1:4d}| {lines[i]}" for i in range(s, e + 1))
        file_rel = next((path.relative_to(root).as_posix() for root in self._source_roots()
                          if path.is_relative_to(root)), path.name)
        out = {"file": file_rel, "symbol": symbol, "code": body}
        findings = self.check_code(lines, s, e, err_line)
        if findings:
            out["findings"] = findings
        return out

    def check_code(self, lines, s, e, err_line=None):
        """일반적인 결함 패턴 점검 (정적 분석). 메서드 범위 s~e (0부터), err_line은 스택 트레이스의 줄 번호."""
        f = []
        body = lines[s:e + 1]
        whole = "\n".join(lines)
        if err_line and 1 <= err_line <= len(lines):
            f.append(f"{err_line}줄에서 오류 발생 (스택 트레이스 기준): {lines[err_line - 1].strip()}")
            m = re.search(r"(\w+\.get\w+\(\))\.\w+\(", lines[err_line - 1])
            if m:
                f.append(f"{err_line}줄: {m[1]} 결과를 null 확인 없이 바로 사용함")
        # 자원 미반환: getConnection / 스트림을 열고 close·try-with-resources가 없음
        for i, l in enumerate(body):
            if re.search(r"\.getConnection\(|new \w*(InputStream|Reader|Writer)\(", l):
                block = "\n".join(body)
                if ".close()" not in block and "try (" not in block:
                    f.append(f"{s + i + 1}줄: 자원(커넥션/스트림)을 얻은 뒤 close() 또는 try-with-resources로 반환하지 않음")
        # static 컬렉션에 추가만 하고 비우지 않음
        for name in re.findall(r"static\s+final\s+(?:List|Map|Set|Queue|Collection)<[^=]*>\s+(\w+)\s*=", whole):
            adds = [s + i + 1 for i, l in enumerate(body) if re.search(rf"\b{name}\.(add|put|offer)\(", l)]
            if adds and not re.search(rf"\b{name}\.(remove|clear|poll)\(", whole):
                f.append(f"{adds[0]}줄: static 컬렉션 {name}에 추가만 하고, 클래스 어디에도 비우는 코드(remove/clear)가 없음 (계속 누적)")
        # Future.get()으로 비동기 작업을 동기처럼 대기
        for i, l in enumerate(body):
            if re.search(r"\.get\(\)\s*;", l) and re.search(r"Future<", whole):
                f.append(f"{s + i + 1}줄: Future.get()으로 결과를 기다림 — 별도 스레드로 보낸 작업을 동기로 대기해 느려짐")
        # static volatile 필드를 null 체크 후 단 한 번만 채우고, 그 뒤로 다시 계산하거나 비우는 코드가 전혀 없음 (영구 캐시, 미반영)
        for name in re.findall(r"static\s+volatile\s+\w[\w<>,\s]*\s+(\w+)\s*;", whole):
            guard_lines = [s + i + 1 for i, l in enumerate(body) if re.search(rf"if\s*\(\s*{name}\s*==\s*null\s*\)", l)]
            if not guard_lines:
                continue
            total_assigns = len(re.findall(rf"\b{name}\s*=(?!=)", whole))
            resets_to_null = re.search(rf"\b{name}\s*=\s*null\s*;", whole)
            if total_assigns <= 1 and not resets_to_null:
                f.append(f"{guard_lines[0]}줄: {name}이 비어있을 때 딱 한 번만 계산해 캐시하고, 그 뒤로 다시 계산(재검사)하거나 "
                         f"비우는 코드가 클래스 어디에도 없음 — 캐시 생성 이후 생긴 변경 사항이 결과에 반영되지 않음(캐시 미반영)")
        # static volatile 필드를 읽고 대기한 뒤 다시 쓰는 비원자적 갱신 (lost update 가능성)
        for name in re.findall(r"static\s+volatile\s+\w[\w<>,\s]*\s+(\w+)\s*;", whole):
            reads = [s + i + 1 for i, l in enumerate(body) if re.search(rf"=\s*{name}\b", l)]
            writes = [s + i + 1 for i, l in enumerate(body) if re.search(rf"\b{name}\s*=", l)]
            has_wait = any("sleep" in l for l in body)
            if reads and writes and has_wait and not re.search(rf"synchronized|Atomic", whole):
                f.append(f"{reads[0]}줄: {name}을 읽고(read) 대기 후 다시 쓰는(write) 구간이 원자적이지 않음 "
                         f"— 동시 요청 시 변경 사항이 유실될 수 있음(lost update)")
        # 반복문 안의 DB 조회·대기
        depth, loop_depths = 0, []
        for i, l in enumerate(body):
            if re.search(r"^\s*(for|while)\s*\(", l):
                loop_depths.append(depth)
            if loop_depths:
                if re.search(r"[Rr]epository\.\w+\(", l):
                    f.append(f"{s + i + 1}줄: 반복문 안에서 DB 조회 ({l.strip()[:80]}) — 데이터 건수만큼 반복 실행됨")
                if "Thread.sleep" in l:
                    f.append(f"{s + i + 1}줄: 반복문 안에서 Thread.sleep 대기")
                m3 = re.search(r"\b(\w*(?:[Cc]lient|[Gg]ateway)\w*)\.\w+\(.*\)\s*;\s*$", l)
                if m3:
                    f.append(f"{s + i + 1}줄: 불필요한 사전 재시도 — 실패 여부와 무관하게 {m3[1]}를 반복문 안에서 "
                             f"미리 여러 번 호출함 (백오프 없는 재시도 폭주)")
                m2 = re.search(r"^\s*(\w+)\(\w+\);", l)
                if m2 and self._method_range(lines, m2[1]):
                    rs, re_ = self._method_range(lines, m2[1])
                    if any("sleep" in x or "Repository" in x or "repository" in x for x in lines[rs:re_ + 1]):
                        f.append(f"{s + i + 1}줄: 반복문 안에서 {m2[1]}() 호출 — 내부에 대기/조회가 있어 건수만큼 느려짐")
            depth += l.count("{") - l.count("}")
            while loop_depths and depth <= loop_depths[-1]:
                loop_depths.pop()
        # 로그에 민감정보로 보이는 필드명을 그대로 남김 (카드번호/비밀번호 등 평문 노출)
        SENSITIVE_LOG_WORDS = ("cardnumber", "card_no", "cardno", "password", "pwd", "ssn",
                               "카드번호", "비밀번호", "주민등록번호")
        for i, l in enumerate(body):
            if re.search(r"\blog\.\w+\(", l) and any(w in l.lower() for w in SENSITIVE_LOG_WORDS):
                f.append(f"{s + i + 1}줄: 로그에 민감정보로 보이는 필드를 그대로 남김 ({l.strip()[:80]}) — 평문 노출 위험")
        # 금액(돈)을 double/float로 선언 — 부동소수점 반올림 오차 위험
        for i, l in enumerate(body):
            m4 = re.search(r"\b(?:double|float)\s+(\w*(?:amount|price|total|point)\w*)\s*=", l, re.IGNORECASE)
            if m4:
                f.append(f"{s + i + 1}줄: 금액 관련 변수({m4[1]})를 double/float로 선언함 — 부동소수점 반올림 오차 위험 "
                         f"(금액은 long 또는 BigDecimal 사용 권장)")
        # 요청마다 new Thread()로 스레드를 생성 — 풀(ExecutorService) 재사용 없이 계속 쌓임
        for i, l in enumerate(body):
            if re.search(r"\bnew\s+Thread\s*\(", l):
                f.append(f"{s + i + 1}줄: new Thread()로 요청마다 새 스레드를 생성함 — 스레드 풀 재사용 없이 "
                         f"계속 쌓임 (스레드 누수 위험)")
        # catch 블록 안에서 멱등성 확인 없이 같은 외부 호출을 그대로 재시도 (중복 처리 위험)
        depth2, catch_entry_depth = 0, None
        for i, l in enumerate(body):
            closes, opens = l.count("}"), l.count("{")
            depth2 -= closes
            if catch_entry_depth is not None and depth2 <= catch_entry_depth:
                catch_entry_depth = None
            if re.search(r"catch\s*\(", l):
                catch_entry_depth = depth2  # catch 본문이 열리기 '직전' 깊이
            depth2 += opens
            if catch_entry_depth is not None and depth2 > catch_entry_depth:
                m5 = re.search(r"\b(\w*(?:[Cc]lient|[Gg]ateway)\w*)\.\w+\(.*\)\s*;\s*$", l)
                if m5 and not re.search(r"idempot|멱등", l, re.IGNORECASE):
                    f.append(f"{s + i + 1}줄: catch 블록 안에서 {m5[1]} 요청을 멱등성 확인 없이 그대로 재시도함 "
                             f"— 중복 처리(이중 청구) 위험")
        # 불리언 플래그로 도는 반복문인데 본문에서 그 플래그를 갱신하는 코드가 없음 (종료 조건 누락)
        for i, l in enumerate(body):
            m6 = re.search(r"while\s*\(\s*(\w+)\s*\)", l)
            if m6:
                flag = m6[1]
                rest = "\n".join(body[i + 1:])
                if not re.search(rf"\b{flag}\s*=(?!=)", rest):
                    f.append(f"{s + i + 1}줄: while({flag}) 반복문인데 본문에서 {flag}를 다시 대입하는 코드가 없음 "
                             f"— 종료 조건이 바뀌지 않아 사실상 끝나지 않는 반복(무한 루프 위험)")
        return list(dict.fromkeys(f))[:10]

    @staticmethod
    def _method_names(lines):
        names = []
        for l in lines:
            m = re.match(r"^\s*(public|private|protected)[\w<>\[\],.? ]*\s+(\w+)\s*\(", l)
            if m:
                names.append(m[2])
        return names

    def read_endpoint_code(self, api: str) -> dict:
        """api: 'GET /api/orders/search' 처럼 메서드와 주소. 컨트롤러 → 서비스 → 내부 메서드 순서로 코드를 모은다."""
        parts = api.strip().split()
        http_method, uri = (parts[0].upper(), parts[1]) if len(parts) == 2 else ("", parts[0])
        for p in self._visible_files():
            text = p.read_text(encoding="utf-8")
            if "@RestController" not in text:
                continue
            base = re.search(r'@RequestMapping\("([^"]*)"\)', text)
            base = base[1] if base else ""
            lines = self._sanitize(text)
            for i, l in enumerate(lines):
                m = re.search(r"@(Get|Post|Put|Delete|Patch)Mapping(?:\(\"([^\"]*)\"\))?", l)
                if not m:
                    continue
                full = base + (m[2] or "")
                if full != uri or (http_method and m[1].upper() != http_method):
                    continue
                sig = next(lines[j] for j in range(i + 1, min(i + 5, len(lines)))
                           if "(" in lines[j] and not lines[j].strip().startswith("@"))
                ctrl_method = re.search(r"\s(\w+)\s*\(", sig)[1]
                chunks = [self.read_code(f"{p.stem}.{ctrl_method}", 25)]
                # 컨트롤러가 부르는 서비스 메서드
                calls = re.findall(r"(\w+(?:Service|Client))\.(\w+)\(", chunks[0].get("code", ""))
                seen = set()
                for field_name, meth in calls:
                    svc = field_name[0].upper() + field_name[1:]
                    r = self.read_code(f"{svc}.{meth}", 40)
                    chunks.append(r)
                    for cf, cm in re.findall(r"(\w+Client)\.(\w+)\(", r.get("code", "")):
                        chunks.append(self.read_code(f"{cf[0].upper() + cf[1:]}.{cm}", 40))
                    seen.add(meth)
                    # 서비스 내부에서 부르는 같은 클래스의 메서드 (한 단계 더)
                    for inner in re.findall(r"(?<![.\w])(\w+)\(", r.get("code", "")):
                        if inner in seen or inner in ("if", "for", "while", "switch", "catch", "return", "new") \
                                or inner[0].isupper() or len(chunks) >= 10:
                            continue
                        rr = self.read_code(f"{svc}.{inner}", 35)
                        if "code" in rr:
                            chunks.append(rr)
                            seen.add(inner)
                return {"api": api, "chain": [c for c in chunks if "code" in c]}
        return {"error": f"'{api}'를 처리하는 컨트롤러를 찾을 수 없습니다."}

    def symbol_exists(self, symbol: str) -> bool:
        r = self.read_code(symbol, 5)
        return "code" in r


# LLM에 알려줄 도구 정의 (Ollama tool calling 형식)
TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "get_metric", "description": "서비스 지표 1개를 조회한다.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": METRIC_NAMES, "description": "지표 이름"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "search_logs", "description": "최근 로그에서 키워드로 검색한다. 오류 메시지·예외 이름·API 주소 등으로 검색.",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "검색어 (예: Timeout, NullPointer, /api/payments)"},
            "level": {"type": "string", "enum": ["", "ERROR", "WARN", "INFO"]}}, "required": ["keyword"]}}},
    {"type": "function", "function": {
        "name": "read_code", "description": "클래스 또는 메서드의 소스 코드를 읽는다.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "'클래스명.메서드명' 또는 '클래스명' (예: PaymentClient.requestApproval)"}},
            "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "read_endpoint_code", "description": "API 주소를 처리하는 코드를 컨트롤러부터 따라가며 읽는다.",
        "parameters": {"type": "object", "properties": {
            "api": {"type": "string", "description": "HTTP 메서드와 주소 (예: GET /api/orders/search)"}}, "required": ["api"]}}},
    {"type": "function", "function": {
        "name": "get_config", "description": "현재 적용된 설정값을 조회한다.",
        "parameters": {"type": "object", "properties": {
            "prefix": {"type": "string", "enum": ["payment.external", "spring.datasource"]}}, "required": ["prefix"]}}},
]


def run_tool(tools: Tools, name: str, args: dict):
    fn = {"get_metric": tools.get_metric, "search_logs": tools.search_logs, "read_code": tools.read_code,
          "read_endpoint_code": tools.read_endpoint_code, "get_config": tools.get_config}.get(name)
    if not fn:
        return {"error": f"없는 도구: {name}"}
    try:
        allowed = fn.__code__.co_varnames[1:fn.__code__.co_argcount]
        return fn(**{k: v for k, v in (args or {}).items() if k in allowed})
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
