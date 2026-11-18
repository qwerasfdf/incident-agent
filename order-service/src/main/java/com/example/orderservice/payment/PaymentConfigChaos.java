package com.example.orderservice.payment;

import com.example.orderservice.chaos.ChaosCleanup;
import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import com.example.orderservice.chaos.ChaosScenario;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * [S4] 운영 중 설정 갱신으로 결제 API 주소가 잘못 바뀐 상황을 재현한다.
 * ON: base-url 오타 값(mock-externl)으로 변경 / OFF: 원래 값으로 복구
 */
@Component
public class PaymentConfigChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");

    private final PaymentProperties props;
    private final String originalBaseUrl;

    public PaymentConfigChaos(PaymentProperties props) {
        this.props = props;
        this.originalBaseUrl = props.getBaseUrl();
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S4_WRONG_CONFIG; }

    @Override
    public void activate() {
        String before = props.getBaseUrl();
        props.setBaseUrl(originalBaseUrl.replace("mock-external", "mock-externl"));
        log.info("설정 갱신 적용 payment.external.base-url: {} -> {}", before, props.getBaseUrl());
    }

    @Override
    public void cleanup() {
        if (!originalBaseUrl.equals(props.getBaseUrl())) {
            String before = props.getBaseUrl();
            props.setBaseUrl(originalBaseUrl);
            log.info("설정 갱신 적용 payment.external.base-url: {} -> {}", before, props.getBaseUrl());
        }
    }
}
