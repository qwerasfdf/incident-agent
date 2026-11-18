"""
장애 시나리오 재현·시연 스크립트 (표준 라이브러리만 사용, Windows/Mac/Linux 공통)

사용 예:
  python scripts/chaos_demo.py --scenario all          # 6종 순서대로 재현 후 자동 복구
  python scripts/chaos_demo.py --scenario S3           # S3만 재현
  python scripts/chaos_demo.py --scenario S2 --keep-on # 장애를 켠 채로 두기 (Agent 테스트용)
  python scripts/chaos_demo.py --reset                 # 모든 장애 끄기
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8081"
ORDER_BODY = {"customerName": "시연고객", "productName": "키보드", "category": "전자기기",
              "quantity": 1, "price": 89000}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def call(method, path, body=None, timeout=30):
    """(상태 코드, 소요 초, 응답 JSON 또는 텍스트) 반환"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            status = res.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        status = e.code
    except (TimeoutError, urllib.error.URLError) as e:
        if isinstance(e, urllib.error.URLError) and not isinstance(e.reason, TimeoutError):
            raise
        return 0, time.time() - start, "timeout"  # 0 = 응답 없음(시간 초과)
    elapsed = time.time() - start
    try:
        return status, elapsed, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, elapsed, raw


def metric(name, tag=None):
    path = f"/actuator/metrics/{name}" + (f"?tag={tag}" if tag else "")
    status, _, body = call("GET", path, timeout=15)
    if status != 200 or not isinstance(body, dict):
        return None
    return body["measurements"][0]["value"]


def chaos(code, on):
    call("POST", f"/chaos/{code}/{'on' if on else 'off'}")


def summarize(results):
    counts = {}
    for s, _ in results:
        counts[s] = counts.get(s, 0) + 1
    avg = sum(t for _, t in results) / len(results)
    return f"상태코드 {counts}, 평균 {avg:.2f}초"


# ---------------- 시나리오별 재현 ----------------

def s1():
    heap_max = metric("jvm.memory.max", "area:heap")
    print(f"  힙 사용량(시작): {metric('jvm.memory.used', 'area:heap') / 1048576:.0f} MB "
          f"/ 최대 {heap_max / 1048576:.0f} MB")
    results, errors = [], 0
    # 오류가 2번 나거나 응답이 없으면 멈춘다 (계속 보내면 서버 전체가 멈출 수 있음)
    for i in range(1, 201):
        s, t, _ = call("POST", "/api/orders", ORDER_BODY, timeout=15)
        results.append((s, t))
        errors += s != 201
        used = metric("jvm.memory.used", "area:heap") if i % 5 == 0 or s != 201 else None
        if i % 5 == 0 and used:
            print(f"  주문 {i}건 후 힙: {used / 1048576:.0f} MB")
        if errors >= 2 or s == 0:
            break
    print(f"  POST /api/orders {len(results)}회 → {summarize(results)}")


def s2():
    results = [call("POST", "/api/orders", ORDER_BODY)[:2] for _ in range(13)]
    print(f"  POST /api/orders 13회 → {summarize(results)}")
    print(f"  hikaricp.connections.active = {metric('hikaricp.connections.active'):.0f}, "
          f"timeout 누적 = {metric('hikaricp.connections.timeout'):.0f}")
    s, t, _ = call("GET", "/api/orders")
    print(f"  (참고) GET /api/orders → {s}, {t:.2f}초  ← 조회는 정상이라 오진하기 쉬움")


def s3():
    for _ in range(2):
        s, t, _ = call("GET", "/api/orders/search")
        print(f"  GET /api/orders/search → {s}, {t:.2f}초")


def s4():
    s, t, body = call("POST", "/api/payments", {"orderId": 2})
    print(f"  POST /api/payments → {s}, {t:.2f}초")
    _, _, props = call("GET", "/actuator/configprops")
    for ctx in props["contexts"].values():
        for bean in ctx["beans"].values():
            if bean.get("prefix") == "payment.external":
                print(f"  configprops payment.external.baseUrl = {bean['properties']['baseUrl']}")


def s5():
    s, t, _ = call("POST", "/api/payments", {"orderId": 3})
    print(f"  POST /api/payments → {s}, {t:.2f}초 (read-timeout 2초)")


def s6():
    _, _, orders = call("GET", "/api/orders")
    no_coupon = next(o["id"] for o in orders if o["couponCode"] is None)
    with_coupon = next(o["id"] for o in orders if o["couponCode"] is not None)
    s1_, _, _ = call("GET", f"/api/orders/{no_coupon}")
    s2_, _, _ = call("GET", f"/api/orders/{with_coupon}")
    print(f"  쿠폰 없는 주문 id={no_coupon} → {s1_} / 쿠폰 있는 주문 id={with_coupon} → {s2_}")


SCENARIOS = {"S1": ("메모리 누수", s1), "S2": ("DB 커넥션 고갈", s2), "S3": ("응답 지연", s3),
             "S4": ("잘못된 설정값", s4), "S5": ("외부 API 타임아웃", s5), "S6": ("예외 처리 누락", s6)}


def main():
    global BASE
    p = argparse.ArgumentParser(description="장애 시나리오 재현·시연")
    p.add_argument("--scenario", default="all", help="S1~S6 또는 all")
    p.add_argument("--keep-on", action="store_true", help="재현 후 장애를 끄지 않음")
    p.add_argument("--reset", action="store_true", help="모든 장애 끄기")
    p.add_argument("--base-url", default=BASE)
    args = p.parse_args()
    BASE = args.base_url.rstrip("/")

    try:
        call("GET", "/actuator/health", timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"서버에 연결할 수 없습니다 ({BASE}). 먼저 order-service를 실행하세요. [{e}]")
        sys.exit(1)

    if args.reset:
        call("POST", "/chaos/reset")
        print("모든 장애를 껐습니다.")
        return

    codes = list(SCENARIOS) if args.scenario.lower() == "all" else [args.scenario.upper()]
    for code in codes:
        if code not in SCENARIOS:
            print(f"알 수 없는 시나리오: {code}")
            sys.exit(1)
        title, fn = SCENARIOS[code]
        print(f"\n[{code}] {title} 재현")
        chaos(code, True)
        fn()
        if not args.keep_on:
            chaos(code, False)
            print("  → 장애 OFF (정상 복구)")
    print("\n자세한 근거는 order-service/logs/app.log 와 /actuator/metrics 에서 확인하세요.")


if __name__ == "__main__":
    main()
