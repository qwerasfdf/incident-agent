package com.example.orderservice.payment;

import com.example.orderservice.chaos.ChaosScenario;
import com.example.orderservice.chaos.ChaosState;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

/**
 * 외부 결제 대행사(PG)를 흉내 내는 모의 API.
 * 실제로는 다른 회사 서버라고 가정한다. (Agent는 이 코드를 내부 코드로 보면 안 됨)
 */
@RestController
@RequestMapping("/mock-external")
public class MockExternalPaymentController {

    private final ChaosState chaos;

    public MockExternalPaymentController(ChaosState chaos) {
        this.chaos = chaos;
    }

    @PostMapping("/payments")
    public ResponseEntity<?> approve(@RequestBody Map<String, Object> body) throws InterruptedException {
        if (chaos.isOn(ChaosScenario.S5_EXTERNAL_TIMEOUT)) {
            Thread.sleep(5000); // [S5] 외부 PG 응답 지연
        }
        if (chaos.isOn(ChaosScenario.S24_MALFORMED_RESPONSE)) {
            // [S24] 응답 스키마가 바뀌어 객체 대신 배열을 돌려준다 — 호출 측 역직렬화가 깨진다
            return ResponseEntity.ok(List.of("unexpected-array-response"));
        }
        if (chaos.isOn(ChaosScenario.S25_EXTERNAL_5XX)) {
            return ResponseEntity.status(500).body(Map.of("error", "internal_error"));
        }
        if (chaos.isOn(ChaosScenario.S26_RATE_LIMIT)) {
            return ResponseEntity.status(429).body(Map.of("error", "too_many_requests"));
        }
        if (chaos.isOn(ChaosScenario.S27_SERVICE_UNAVAILABLE)) {
            return ResponseEntity.status(503).body(Map.of("error", "service_unavailable"));
        }
        if (chaos.isOn(ChaosScenario.S28_BAD_GATEWAY)) {
            return ResponseEntity.status(502).body(Map.of("error", "bad_gateway"));
        }
        if (chaos.isOn(ChaosScenario.S29_INTERMITTENT_FAILURE) && ThreadLocalRandom.current().nextInt(2) == 0) {
            return ResponseEntity.status(500).body(Map.of("error", "intermittent_failure"));
        }
        if (chaos.isOn(ChaosScenario.S30_AUTH_REJECT)) {
            return ResponseEntity.status(401).body(Map.of("error", "unauthorized", "message", "서명 검증 실패"));
        }
        return ResponseEntity.ok(Map.of("approvalNo", UUID.randomUUID().toString().substring(0, 8).toUpperCase(),
                "result", "APPROVED", "orderId", body.get("orderId")));
    }
}
