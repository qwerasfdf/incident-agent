package com.example.orderservice.payment;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Map;

/** 외부 결제 대행사(PG) 호출 클라이언트 */
@Component
public class PaymentClient {

    private static final Logger log = LoggerFactory.getLogger(PaymentClient.class);

    private final PaymentProperties props;

    public PaymentClient(PaymentProperties props) {
        this.props = props;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> requestApproval(Long orderId, long amount) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) props.getConnectTimeout().toMillis());
        factory.setReadTimeout((int) props.getReadTimeout().toMillis());
        RestClient client = RestClient.builder().baseUrl(props.getBaseUrl()).requestFactory(factory).build();

        log.info("외부 결제 승인 요청 orderId={} amount={} url={}/payments", orderId, amount, props.getBaseUrl());
        long start = System.currentTimeMillis();
        Map<String, Object> res = client.post().uri("/payments")
                .contentType(MediaType.APPLICATION_JSON)
                .body(Map.of("orderId", orderId, "amount", amount))
                .retrieve()
                .body(Map.class);
        log.info("외부 결제 승인 응답 orderId={} elapsedMs={}", orderId, System.currentTimeMillis() - start);
        return res;
    }
}
