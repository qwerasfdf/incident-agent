package com.example.orderservice.order;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 카테고리 집계 검색 모드 설정 */
@ConfigurationProperties(prefix = "app.order.search")
public class OrderSearchProperties {

    /** true면 구버전(느린) 집계 로직을 사용한다 */
    private boolean legacyMode = false;

    public boolean isLegacyMode() { return legacyMode; }
    public void setLegacyMode(boolean legacyMode) { this.legacyMode = legacyMode; }
}
