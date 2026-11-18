package com.example.orderservice.order;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface OrderRepository extends JpaRepository<Order, Long> {
    List<Order> findByCategory(String category);
    List<Order> findByCustomerName(String customerName);
}
