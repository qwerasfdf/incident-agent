package com.example.orderservice.order;

import com.example.orderservice.chaos.ChaosCleanup;
import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import com.example.orderservice.chaos.ChaosScenario;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * [S23] 운영 중 설정 실수로 1회 주문 최대 수량이 0으로 바뀐 상황을 재현한다.
 * ON: max-quantity를 0으로 변경 / OFF: 원래 값으로 복구
 */
@Component
public class OrderLimitChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");

    private final OrderLimitProperties props;
    private final int originalMax;

    public OrderLimitChaos(OrderLimitProperties props) {
        this.props = props;
        this.originalMax = props.getMaxQuantity();
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S23_MAX_QUANTITY; }

    @Override
    public void activate() {
        int before = props.getMaxQuantity();
        props.setMaxQuantity(0);
        log.info("설정 갱신 적용 app.order.max-quantity: {} -> {}", before, props.getMaxQuantity());
    }

    @Override
    public void cleanup() {
        if (props.getMaxQuantity() != originalMax) {
            int before = props.getMaxQuantity();
            props.setMaxQuantity(originalMax);
            log.info("설정 갱신 적용 app.order.max-quantity: {} -> {}", before, originalMax);
        }
    }
}
