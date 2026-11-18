package com.example.orderservice.chaos;

/** 시나리오를 끌 때 쌓인 장애 상태(누수 객체, 미반환 커넥션 등)를 정리하는 훅. 재현 편의용. */
public interface ChaosCleanup {
    ChaosScenario scenario();
    void cleanup();
}
