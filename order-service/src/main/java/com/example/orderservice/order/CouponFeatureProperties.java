package com.example.orderservice.order;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 쿠폰 할인 표시 기능 플래그 */
@ConfigurationProperties(prefix = "app.order.coupon")
public class CouponFeatureProperties {

    /** false면 쿠폰이 있어도 할인 표시를 숨긴다 */
    private boolean enabled = true;

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
}
