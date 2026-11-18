package com.example.inventoryservice.inventory;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

public final class InventoryDtos {

    private InventoryDtos() {
    }

    public record StockResponse(String productName, int stock) {
    }

    public record ReserveRequest(
            @NotBlank String productName,
            @Min(1) int quantity) {
    }

    public record ReserveResponse(String productName, int reserved, int remaining) {
    }
}
