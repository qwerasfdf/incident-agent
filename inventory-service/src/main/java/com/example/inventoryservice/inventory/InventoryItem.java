package com.example.inventoryservice.inventory;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;

@Entity
public class InventoryItem {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String productName;
    private int stock;

    protected InventoryItem() {
    }

    public InventoryItem(String productName, int stock) {
        this.productName = productName;
        this.stock = stock;
    }

    public Long getId() { return id; }
    public String getProductName() { return productName; }
    public int getStock() { return stock; }
    public void setStock(int stock) { this.stock = stock; }
}
