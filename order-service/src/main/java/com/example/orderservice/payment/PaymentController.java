package com.example.orderservice.payment;

import jakarta.validation.constraints.NotNull;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/payments")
public class PaymentController {

    private final PaymentService paymentService;

    public PaymentController(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    public record PayRequest(@NotNull Long orderId) {
    }

    @PostMapping
    public Map<String, Object> pay(@RequestBody PayRequest req) {
        return paymentService.pay(req.orderId());
    }
}
