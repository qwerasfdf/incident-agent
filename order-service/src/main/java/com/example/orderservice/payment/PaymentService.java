package com.example.orderservice.payment;

import com.example.orderservice.chaos.ChaosScenario;
import com.example.orderservice.chaos.ChaosState;
import com.example.orderservice.order.Order;
import com.example.orderservice.order.OrderService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;

@Service
public class PaymentService {

    private static final Logger log = LoggerFactory.getLogger(PaymentService.class);

    private final OrderService orderService;
    private final PaymentClient paymentClient;
    private final ChaosState chaos;

    public PaymentService(OrderService orderService, PaymentClient paymentClient, ChaosState chaos) {
        this.orderService = orderService;
        this.paymentClient = paymentClient;
        this.chaos = chaos;
    }

    public Map<String, Object> pay(Long orderId) {
        Order order = orderService.get(orderId);
        if (chaos.isOn(ChaosScenario.S11_RETRY_STORM)) {
            // 실패도 안 했는데 백오프 없이 미리 여러 번 더 호출해버린다 (불필요한 재시도)
            for (int i = 0; i < 3; i++) {
                paymentClient.requestApproval(order.getId(), order.getTotalAmount());
            }
        }
        if (chaos.isOn(ChaosScenario.S13_SENSITIVE_LOG)) {
            // 디버깅 편의로 추가했다가 그대로 운영에 남은 코드 — 카드번호를 평문으로 로그에 남긴다
            log.info("결제 승인 요청 상세 orderId={} amount={} cardNumber={}", orderId, order.getTotalAmount(),
                    "4111-1111-1111-1111");
        }
        if (chaos.isOn(ChaosScenario.S14_FLOAT_MONEY)) {
            // 금액(돈)을 double로 계산한다 — 이진 부동소수점 특성상 특정 금액에서 반올림 오차가 생길 수 있다
            double points = order.getTotalAmount() * 0.01;
            log.info("적립 포인트 계산 orderId={} points={}", orderId, points);
        }
        Map<String, Object> approval;
        try {
            approval = paymentClient.requestApproval(order.getId(), order.getTotalAmount());
            if (chaos.isOn(ChaosScenario.S10_SILENT_FAILURE)) {
                throw new IllegalStateException("승인 검증 실패");
            }
        } catch (Exception e) {
            if (chaos.isOn(ChaosScenario.S16_NON_IDEMPOTENT_RETRY)) {
                // 멱등성 키 확인 없이 같은(비멱등) 승인 요청을 그대로 한 번 더 보낸다 — 중복 승인/이중 청구 위험
                log.warn("결제 승인 실패 후 재시도(멱등성 확인 없음) orderId={}", orderId);
                approval = paymentClient.requestApproval(order.getId(), order.getTotalAmount());
            } else if (chaos.isOn(ChaosScenario.S10_SILENT_FAILURE)) {
                // 예외를 삼키고 가짜 성공으로 처리한다 — 호출자에게는 아무 문제도 보이지 않는다
                log.debug("결제 승인 처리 중 예외 발생 (무시하고 진행)");
                approval = Map.of("result", "UNKNOWN");
            } else {
                throw e;
            }
        }
        orderService.markPaid(orderId);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("orderId", orderId);
        out.put("amount", order.getTotalAmount());
        out.put("approval", approval);
        out.put("status", "PAID");
        return out;
    }
}
