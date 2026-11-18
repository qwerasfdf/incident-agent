package com.example.orderservice.order;

import com.example.orderservice.chaos.ChaosCleanup;
import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import com.example.orderservice.chaos.ChaosScenario;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * [S22] 운영 중 설정 실수로 카테고리 집계 검색이 구버전(느린) 로직으로 전환된 상황을 재현한다.
 * ON: legacy-mode를 true로 변경 / OFF: 원래 값(false)으로 복구
 */
@Component
public class OrderSearchModeChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");

    private final OrderSearchProperties props;
    private final boolean originalLegacyMode;

    public OrderSearchModeChaos(OrderSearchProperties props) {
        this.props = props;
        this.originalLegacyMode = props.isLegacyMode();
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S22_LEGACY_SEARCH; }

    @Override
    public void activate() {
        boolean before = props.isLegacyMode();
        props.setLegacyMode(true);
        log.info("설정 갱신 적용 app.order.search.legacy-mode: {} -> {}", before, props.isLegacyMode());
    }

    @Override
    public void cleanup() {
        if (props.isLegacyMode() != originalLegacyMode) {
            boolean before = props.isLegacyMode();
            props.setLegacyMode(originalLegacyMode);
            log.info("설정 갱신 적용 app.order.search.legacy-mode: {} -> {}", before, originalLegacyMode);
        }
    }
}
