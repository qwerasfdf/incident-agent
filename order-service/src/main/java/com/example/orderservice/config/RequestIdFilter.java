package com.example.orderservice.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

/** 요청마다 requestId를 붙이고, 요청 완료 시 상태 코드와 처리 시간을 남긴다. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestIdFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger("access");

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String requestId = UUID.randomUUID().toString().substring(0, 8);
        MDC.put("requestId", requestId);
        res.setHeader("X-Request-Id", requestId);
        long start = System.currentTimeMillis();
        try {
            chain.doFilter(req, res);
        } finally {
            long elapsed = System.currentTimeMillis() - start;
            String uri = req.getRequestURI();
            if (!uri.startsWith("/actuator") && !uri.startsWith("/chaos") && !uri.startsWith("/mock-ext")) {
                if (elapsed >= 2000) {
                    log.warn("느린 요청 {} {} status={} elapsedMs={}", req.getMethod(), uri, res.getStatus(), elapsed);
                } else {
                    log.info("요청 완료 {} {} status={} elapsedMs={}", req.getMethod(), uri, res.getStatus(), elapsed);
                }
            }
            MDC.remove("requestId");
        }
    }
}
