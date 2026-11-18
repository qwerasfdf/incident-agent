package com.example.orderservice.payment;

import com.example.orderservice.chaos.ChaosCleanup;
import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import com.example.orderservice.chaos.ChaosScenario;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Duration;

/**
 * [S21] 운영 중 설정 실수로 결제 외부 연동 read-timeout이 1ms로 과도하게 짧게 바뀐 상황을 재현한다.
 * 외부 PG가 정상이어도 응답을 받기 전에 항상 타임아웃이 난다.
 * ON: read-timeout을 1ms로 변경 / OFF: 원래 값으로 복구
 */
@Component
public class PaymentTimeoutChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");

    private final PaymentProperties props;
    private final Duration originalReadTimeout;

    public PaymentTimeoutChaos(PaymentProperties props) {
        this.props = props;
        this.originalReadTimeout = props.getReadTimeout();
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S21_AGGRESSIVE_TIMEOUT; }

    @Override
    public void activate() {
        Duration before = props.getReadTimeout();
        props.setReadTimeout(Duration.ofMillis(1));
        log.info("설정 갱신 적용 payment.external.read-timeout: {} -> {}", before, props.getReadTimeout());
    }

    @Override
    public void cleanup() {
        if (!props.getReadTimeout().equals(originalReadTimeout)) {
            Duration before = props.getReadTimeout();
            props.setReadTimeout(originalReadTimeout);
            log.info("설정 갱신 적용 payment.external.read-timeout: {} -> {}", before, originalReadTimeout);
        }
    }
}
