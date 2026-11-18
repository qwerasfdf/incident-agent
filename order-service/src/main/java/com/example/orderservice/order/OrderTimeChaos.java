package com.example.orderservice.order;

import com.example.orderservice.chaos.ChaosCleanup;
import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import com.example.orderservice.chaos.ChaosScenario;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * [S8] 운영 중 설정 실수로 주문 시각 기록용 타임존이 UTC로 바뀐 상황을 재현한다.
 * ON: zoneId를 UTC로 변경 / OFF: 원래 값(Asia/Seoul)으로 복구
 */
@Component
public class OrderTimeChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");

    private final OrderTimeProperties props;
    private final String originalZoneId;

    public OrderTimeChaos(OrderTimeProperties props) {
        this.props = props;
        this.originalZoneId = props.getZoneId();
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S8_WRONG_TIMEZONE; }

    @Override
    public void activate() {
        String before = props.getZoneId();
        props.setZoneId("UTC");
        log.info("설정 갱신 적용 app.order.zone-id: {} -> {}", before, props.getZoneId());
    }

    @Override
    public void cleanup() {
        if (!originalZoneId.equals(props.getZoneId())) {
            String before = props.getZoneId();
            props.setZoneId(originalZoneId);
            log.info("설정 갱신 적용 app.order.zone-id: {} -> {}", before, props.getZoneId());
        }
    }
}
