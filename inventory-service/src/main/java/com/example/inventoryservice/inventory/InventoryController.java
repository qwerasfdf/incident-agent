package com.example.inventoryservice.inventory;

import com.example.inventoryservice.inventory.InventoryDtos.*;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/inventory")
public class InventoryController {

    private final InventoryService service;

    public InventoryController(InventoryService service) {
        this.service = service;
    }

    @GetMapping("/{productName}")
    public StockResponse stock(@PathVariable String productName) {
        return new StockResponse(productName, service.getStock(productName));
    }

    @PostMapping("/reserve")
    public ReserveResponse reserve(@Valid @RequestBody ReserveRequest req) {
        int remaining = service.reserve(req.productName(), req.quantity());
        return new ReserveResponse(req.productName(), req.quantity(), remaining);
    }
}
