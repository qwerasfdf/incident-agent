package com.example.orderservice.order;

import com.example.orderservice.chaos.ChaosCleanup;
import com.example.orderservice.chaos.ChaosResources;
import com.example.orderservice.chaos.ChaosScenario;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** S1·S2·S9·S12를 OFF 할 때 쌓인 상태(메모리, 미반환 커넥션, 캐시, 재고 스냅샷)를 정리한다. (재현 편의용) */
@Configuration
class OrderChaosCleanups {

    @Bean
    ChaosCleanup memoryLeakCleanup(OrderService service) {
        return new ChaosCleanup() {
            public ChaosScenario scenario() { return ChaosScenario.S1_MEMORY_LEAK; }
            public void cleanup() { OrderService.ORDER_AUDIT_CACHE.clear(); System.gc(); }
        };
    }

    @Bean
    ChaosCleanup connectionLeakCleanup(OrderService service) {
        return new ChaosCleanup() {
            public ChaosScenario scenario() { return ChaosScenario.S2_CONNECTION_LEAK; }
            public void cleanup() { ChaosResources.closeAll(); }
        };
    }

    @Bean
    ChaosCleanup staleCacheCleanup(OrderService service) {
        return new ChaosCleanup() {
            public ChaosScenario scenario() { return ChaosScenario.S9_STALE_CACHE; }
            public void cleanup() { OrderService.CACHED_SUMMARY = null; }
        };
    }

    @Bean
    ChaosCleanup lostUpdateCleanup(OrderService service) {
        return new ChaosCleanup() {
            public ChaosScenario scenario() { return ChaosScenario.S12_LOST_UPDATE; }
            public void cleanup() { OrderService.LIMITED_STOCK = null; }
        };
    }
}
