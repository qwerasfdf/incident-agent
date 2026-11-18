package com.example.inventoryservice.inventory;

import com.example.inventoryservice.chaos.ChaosScenario;
import com.example.inventoryservice.chaos.ChaosState;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class InventoryService {

    private static final Logger log = LoggerFactory.getLogger(InventoryService.class);

    /** 데모 편의상 처음 조회되는 상품은 기본 재고로 자동 생성한다. */
    private static final int DEFAULT_STOCK = 1000;

    private final InventoryRepository repository;
    private final ChaosState chaos;

    public InventoryService(InventoryRepository repository, ChaosState chaos) {
        this.repository = repository;
        this.chaos = chaos;
    }

    @Transactional
    public int getStock(String productName) {
        applyChaos();
        return findOrCreate(productName).getStock();
    }

    @Transactional
    public int reserve(String productName, int quantity) {
        applyChaos();
        InventoryItem item = findOrCreate(productName);
        if (item.getStock() < quantity) {
            throw new IllegalStateException("재고 부족: " + productName + " (남은 수량 " + item.getStock() + ")");
        }
        item.setStock(item.getStock() - quantity);
        log.info("재고 차감 완료 product={} qty={} remaining={}", productName, quantity, item.getStock());
        return item.getStock();
    }

    private InventoryItem findOrCreate(String productName) {
        return repository.findByProductName(productName)
                .orElseGet(() -> repository.save(new InventoryItem(productName, DEFAULT_STOCK)));
    }

    /** [M1]/[M2] 멀티 서비스 장애 전파 시나리오 — order-service → inventory-service 호출에 영향을 준다. */
    private void applyChaos() {
        if (chaos.isOn(ChaosScenario.M1_INVENTORY_SLOW)) {
            try {
                Thread.sleep(5000); // order-service의 inventory.external.read-timeout(기본 2s)보다 길게 지연
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        if (chaos.isOn(ChaosScenario.M2_INVENTORY_ERROR)) {
            throw new RuntimeException("재고 서비스 내부 오류(재고 저장소 조회 실패)");
        }
    }
}
