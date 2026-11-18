package com.example.orderservice.chaos;

import java.util.Arrays;

/**
 * 장애 재현 시나리오 목록.
 * 주의: Agent 평가 시에는 이 패키지(chaos)와 /chaos API를 Agent 도구에서 제외해야 한다.
 * (정답을 미리 보는 것을 막기 위함)
 */
public enum ChaosScenario {
    S1_MEMORY_LEAK("S1", "메모리 누수", "POST /api/orders"),
    S2_CONNECTION_LEAK("S2", "DB 커넥션 고갈", "POST /api/orders"),
    S3_SLOW_RESPONSE("S3", "응답 지연", "GET /api/orders/search"),
    S4_WRONG_CONFIG("S4", "잘못된 설정값", "POST /api/payments"),
    S5_EXTERNAL_TIMEOUT("S5", "외부 API 타임아웃", "POST /api/payments"),
    S6_NULL_HANDLING("S6", "예외 처리 누락", "GET /api/orders/{id}"),
    S7_ASYNC_BLOCKING("S7", "비동기 처리 오용(동기 대기)", "POST /api/orders"),
    S8_WRONG_TIMEZONE("S8", "타임존 설정 오류", "POST /api/orders"),
    S9_STALE_CACHE("S9", "캐시 미반영", "GET /api/orders/search"),
    S10_SILENT_FAILURE("S10", "결제 예외 삼킴", "POST /api/payments"),
    S11_RETRY_STORM("S11", "불필요한 재시도 폭주", "POST /api/payments"),
    S12_LOST_UPDATE("S12", "동시성 유실 업데이트", "POST /api/orders"),

    // --- 코드 결함 추가 (S13~S17) ---
    S13_SENSITIVE_LOG("S13", "로그에 민감정보 평문 노출", "POST /api/payments"),
    S14_FLOAT_MONEY("S14", "금액 계산에 부동소수점 사용", "POST /api/payments"),
    S15_THREAD_LEAK("S15", "요청마다 스레드 생성(미반환)", "POST /api/orders"),
    S16_NON_IDEMPOTENT_RETRY("S16", "비멱등 재시도로 중복 처리 위험", "POST /api/payments"),
    S17_INFINITE_LOOP("S17", "잘못된 종료조건(무한 루프 위험)", "GET /api/orders/search"),

    // --- 설정 오류 추가 (S18~S23) ---
    S18_SMALL_POOL("S18", "DB 커넥션 풀 크기 과소 설정", "POST /api/orders"),
    S19_LOG_LEVEL("S19", "로그 레벨 설정 오류(TRACE 방치)", "POST /api/orders"),
    S20_COUPON_FLAG("S20", "쿠폰 기능 플래그 오설정", "GET /api/orders/{id}"),
    S21_AGGRESSIVE_TIMEOUT("S21", "결제 타임아웃 설정값 과소", "POST /api/payments"),
    S22_LEGACY_SEARCH("S22", "검색 모드 설정 오류(레거시 전환)", "GET /api/orders/search"),
    S23_MAX_QUANTITY("S23", "주문 최대 수량 설정값 오류", "POST /api/orders"),

    // --- 외부 시스템 추가 (S24~S30) ---
    S24_MALFORMED_RESPONSE("S24", "외부 PG 응답 스키마 깨짐", "POST /api/payments"),
    S25_EXTERNAL_5XX("S25", "외부 PG 서버 오류(500)", "POST /api/payments"),
    S26_RATE_LIMIT("S26", "외부 PG 레이트 리밋(429)", "POST /api/payments"),
    S27_SERVICE_UNAVAILABLE("S27", "외부 PG 점검중(503)", "POST /api/payments"),
    S28_BAD_GATEWAY("S28", "외부 PG 게이트웨이 오류(502)", "POST /api/payments"),
    S29_INTERMITTENT_FAILURE("S29", "외부 PG 간헐적 실패", "POST /api/payments"),
    S30_AUTH_REJECT("S30", "외부 PG 인증 실패(401)", "POST /api/payments");

    private final String code;
    private final String title;
    private final String target;

    ChaosScenario(String code, String title, String target) {
        this.code = code;
        this.title = title;
        this.target = target;
    }

    public String code() { return code; }
    public String title() { return title; }
    public String target() { return target; }

    public static ChaosScenario fromCode(String code) {
        return Arrays.stream(values())
                .filter(s -> s.code.equalsIgnoreCase(code) || s.name().equalsIgnoreCase(code))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("알 수 없는 시나리오: " + code));
    }
}
