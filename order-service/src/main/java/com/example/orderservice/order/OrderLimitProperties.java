package com.example.orderservice.order;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 1회 주문 최대 수량 설정 */
@ConfigurationProperties(prefix = "app.order")
public class OrderLimitProperties {

    /** 1회 주문으로 허용되는 최대 수량 */
    private int maxQuantity = 10000;

    public int getMaxQuantity() { return maxQuantity; }
    public void setMaxQuantity(int maxQuantity) { this.maxQuantity = maxQuantity; }
}
