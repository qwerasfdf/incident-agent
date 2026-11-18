package com.example.orderservice.order;

import com.example.orderservice.chaos.ChaosCleanup;
import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import com.example.orderservice.chaos.ChaosScenario;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * [S20] 운영 중 설정 실수로 쿠폰 할인 표시 기능이 꺼진 상황을 재현한다.
 * ON: enabled를 false로 변경 / OFF: 원래 값(true)으로 복구
 */
@Component
public class CouponFeatureChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");

    private final CouponFeatureProperties props;
    private final boolean originalEnabled;

    public CouponFeatureChaos(CouponFeatureProperties props) {
        this.props = props;
        this.originalEnabled = props.isEnabled();
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S20_COUPON_FLAG; }

    @Override
    public void activate() {
        boolean before = props.isEnabled();
        props.setEnabled(false);
        log.info("설정 갱신 적용 app.order.coupon.enabled: {} -> {}", before, props.isEnabled());
    }

    @Override
    public void cleanup() {
        if (props.isEnabled() != originalEnabled) {
            boolean before = props.isEnabled();
            props.setEnabled(originalEnabled);
            log.info("설정 갱신 적용 app.order.coupon.enabled: {} -> {}", before, originalEnabled);
        }
    }
}
