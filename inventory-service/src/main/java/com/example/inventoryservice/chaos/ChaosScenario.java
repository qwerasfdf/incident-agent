package com.example.inventoryservice.chaos;

import java.util.Arrays;

/**
 * inventory-service(재고 서비스) 쪽 장애 시나리오.
 * order-service → inventory-service 호출이 전파되어 주문 생성에 영향을 주는지 보기 위한 멀티 서비스 장애 전파 시나리오.
 * 주의: 이 패키지(chaos)와 /chaos API는 Agent 평가 시 제외해야 한다 (정답을 미리 보는 것을 막기 위함).
 */
public enum ChaosScenario {
    M1_INVENTORY_SLOW("M1", "재고 서비스 응답 지연 전파", "GET /api/inventory/{productName}"),
    M2_INVENTORY_ERROR("M2", "재고 서비스 내부 오류(500) 전파", "GET /api/inventory/{productName}");

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
