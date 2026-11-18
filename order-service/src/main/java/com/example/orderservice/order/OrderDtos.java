package com.example.orderservice.order;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

import java.time.LocalDateTime;

public final class OrderDtos {

    private OrderDtos() {
    }

    public record CreateOrderRequest(
            @NotBlank String customerName,
            @NotBlank String productName,
            @NotBlank String category,
            @Min(1) int quantity,
            @Min(0) long price,
            String couponCode) {
    }

    public record OrderResponse(Long id, String customerName, String productName, String category,
                                int quantity, long price, long totalAmount, String couponCode,
                                OrderStatus status, LocalDateTime orderedAt) {
        static OrderResponse from(Order o) {
            return new OrderResponse(o.getId(), o.getCustomerName(), o.getProductName(), o.getCategory(),
                    o.getQuantity(), o.getPrice(), o.getTotalAmount(), o.getCouponCode(),
                    o.getStatus(), o.getOrderedAt());
        }
    }

    public record OrderDetailResponse(OrderResponse order, String couponLabel) {
    }

    public record CategorySummary(String category, long orderCount, long totalAmount) {
    }
}
