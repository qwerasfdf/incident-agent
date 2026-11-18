package com.example.orderservice.order;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 주문 시각을 기록할 때 쓰는 타임존 설정 */
@ConfigurationProperties(prefix = "app.order")
public class OrderTimeProperties {

    /** 주문 시각을 기록할 타임존 (예: Asia/Seoul) */
    private String zoneId = "Asia/Seoul";

    public String getZoneId() { return zoneId; }
    public void setZoneId(String zoneId) { this.zoneId = zoneId; }
}
