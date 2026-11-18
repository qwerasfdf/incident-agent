package com.example.orderservice.order;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String customerName;
    private String productName;
    private String category;
    private int quantity;
    private long price;

    /** 쿠폰이 없는 주문은 null */
    private String couponCode;

    @Enumerated(EnumType.STRING)
    private OrderStatus status;

    private LocalDateTime orderedAt;

    protected Order() {
    }

    public Order(String customerName, String productName, String category, int quantity,
                 long price, String couponCode, OrderStatus status, LocalDateTime orderedAt) {
        this.customerName = customerName;
        this.productName = productName;
        this.category = category;
        this.quantity = quantity;
        this.price = price;
        this.couponCode = couponCode;
        this.status = status;
        this.orderedAt = orderedAt;
    }

    public Long getId() { return id; }
    public String getCustomerName() { return customerName; }
    public String getProductName() { return productName; }
    public String getCategory() { return category; }
    public int getQuantity() { return quantity; }
    public long getPrice() { return price; }
    public String getCouponCode() { return couponCode; }
    public OrderStatus getStatus() { return status; }
    public LocalDateTime getOrderedAt() { return orderedAt; }

    public long getTotalAmount() { return price * quantity; }

    public void markPaid() { this.status = OrderStatus.PAID; }
}
