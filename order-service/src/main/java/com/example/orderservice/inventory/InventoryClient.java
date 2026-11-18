package com.example.orderservice.inventory;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Map;

/** 재고 서비스(inventory-service) 호출 클라이언트 — 멀티 서비스 장애 전파 시나리오용 */
@Component
public class InventoryClient {

    private static final Logger log = LoggerFactory.getLogger(InventoryClient.class);

    private final InventoryProperties props;

    public InventoryClient(InventoryProperties props) {
        this.props = props;
    }

    @SuppressWarnings("unchecked")
    public int reserve(String productName, int quantity) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) props.getConnectTimeout().toMillis());
        factory.setReadTimeout((int) props.getReadTimeout().toMillis());
        RestClient client = RestClient.builder().baseUrl(props.getBaseUrl()).requestFactory(factory).build();

        log.info("재고 서비스 예약 요청 product={} qty={} url={}/api/inventory/reserve", productName, quantity, props.getBaseUrl());
        long start = System.currentTimeMillis();
        Map<String, Object> res = client.post().uri("/api/inventory/reserve")
                .contentType(MediaType.APPLICATION_JSON)
                .body(Map.of("productName", productName, "quantity", quantity))
                .retrieve()
                .body(Map.class);
        log.info("재고 서비스 예약 응답 product={} elapsedMs={}", productName, System.currentTimeMillis() - start);
        Object remaining = res == null ? null : res.get("remaining");
        return remaining instanceof Number ? ((Number) remaining).intValue() : 0;
    }
}
