package com.example.orderservice.order;

import com.example.orderservice.chaos.ChaosResources;
import com.example.orderservice.chaos.ChaosScenario;
import com.example.orderservice.chaos.ChaosState;
import com.example.orderservice.inventory.InventoryClient;
import com.example.orderservice.order.OrderDtos.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

@Service
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    /** 주문 감사 기록 */
    static final List<byte[]> ORDER_AUDIT_CACHE = new ArrayList<>();

    /** 주문 알림 발송용 스레드 풀 (2개 고정) */
    private static final ExecutorService NOTICE_POOL = Executors.newFixedThreadPool(2);

    /** [S9] 카테고리 집계 캐시 — 무효화(evict) 로직 없음 */
    static volatile List<CategorySummary> CACHED_SUMMARY;

    /** [S12] 한정 수량 재고 스냅샷 (동시성 보호 없이 read-modify-write) */
    static volatile Integer LIMITED_STOCK;

    private final OrderRepository orderRepository;
    private final DataSource dataSource;
    private final ChaosState chaos;
    private final OrderTimeProperties timeProps;
    private final OrderLimitProperties limitProps;
    private final OrderSearchProperties searchProps;
    private final CouponFeatureProperties couponFeature;
    private final InventoryClient inventoryClient;

    public OrderService(OrderRepository orderRepository, DataSource dataSource, ChaosState chaos,
                         OrderTimeProperties timeProps, OrderLimitProperties limitProps,
                         OrderSearchProperties searchProps, CouponFeatureProperties couponFeature,
                         InventoryClient inventoryClient) {
        this.orderRepository = orderRepository;
        this.dataSource = dataSource;
        this.chaos = chaos;
        this.timeProps = timeProps;
        this.limitProps = limitProps;
        this.searchProps = searchProps;
        this.couponFeature = couponFeature;
        this.inventoryClient = inventoryClient;
    }

    @Transactional(readOnly = true)
    public List<OrderResponse> findAll() {
        return orderRepository.findAll().stream().map(OrderResponse::from).toList();
    }

    @Transactional(readOnly = true)
    public OrderDetailResponse findDetail(Long id) {
        Order order = orderRepository.findById(id)
                .orElseThrow(() -> new NoSuchElementException("주문을 찾을 수 없습니다. id=" + id));
        return new OrderDetailResponse(OrderResponse.from(order), couponLabel(order));
    }

    private String couponLabel(Order order) {
        if (chaos.isOn(ChaosScenario.S6_NULL_HANDLING)) {
            return "쿠폰 적용: " + order.getCouponCode().toUpperCase();
        }
        if (!couponFeature.isEnabled()) {
            // 기능 플래그가 꺼져 있으면 쿠폰이 실제로 있어도 할인 표시를 숨긴다
            return "쿠폰 없음";
        }
        return order.getCouponCode() == null ? "쿠폰 없음" : "쿠폰 적용: " + order.getCouponCode().toUpperCase();
    }

    @Transactional
    public OrderResponse create(CreateOrderRequest req) {
        if (req.quantity() > limitProps.getMaxQuantity()) {
            throw new IllegalArgumentException(
                    "주문 수량이 허용 범위(max-quantity)를 초과했습니다: " + req.quantity());
        }
        // 재고 서비스(inventory-service)에 재고를 예약한다 — 멀티 서비스 장애 전파 시나리오(M1/M2)의 진입점
        inventoryClient.reserve(req.productName(), req.quantity());
        if (chaos.isOn(ChaosScenario.S1_MEMORY_LEAK)) {
            recordAudit(req);
        }
        if (chaos.isOn(ChaosScenario.S2_CONNECTION_LEAK)) {
            checkStockDirectly(req.productName());
        }
        if (chaos.isOn(ChaosScenario.S7_ASYNC_BLOCKING)) {
            sendOrderNoticeBlocking(req);
        }
        if (chaos.isOn(ChaosScenario.S12_LOST_UPDATE)) {
            decrementLimitedStock(req);
        }
        if (chaos.isOn(ChaosScenario.S15_THREAD_LEAK)) {
            sendOrderNoticeViaNewThread(req);
        }
        LocalDateTime orderedAt = LocalDateTime.now(ZoneId.of(timeProps.getZoneId()));
        Order saved = orderRepository.save(new Order(req.customerName(), req.productName(), req.category(),
                req.quantity(), req.price(), req.couponCode(), OrderStatus.CREATED, orderedAt));
        log.info("주문 생성 완료 orderId={} customer={} amount={} orderedAt={}", saved.getId(), saved.getCustomerName(),
                saved.getTotalAmount(), saved.getOrderedAt());
        return OrderResponse.from(saved);
    }

    /** 주문 알림을 비동기로 보낸다는 의도였지만, submit() 직후 get()으로 완료를 기다려 사실상 동기 호출이 된다. */
    private void sendOrderNoticeBlocking(CreateOrderRequest req) {
        Future<?> f = NOTICE_POOL.submit(() -> {
            try {
                Thread.sleep(4000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            log.debug("주문 알림 발송 완료 product={}", req.productName());
        });
        try {
            f.get();
        } catch (Exception e) {
            Thread.currentThread().interrupt();
        }
    }

    /** 재고를 읽고(read) 대기한 뒤 그대로 덮어쓴다(write) — 동시에 들어온 다른 요청의 차감분을 잃어버릴 수 있다. */
    private void decrementLimitedStock(CreateOrderRequest req) {
        if (LIMITED_STOCK == null) {
            LIMITED_STOCK = 100;
        }
        int current = LIMITED_STOCK;
        try {
            Thread.sleep(50);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        LIMITED_STOCK = current - req.quantity();
        log.info("한정 수량 재고 차감 product={} qty={} 남은수량(추정)={}", req.productName(), req.quantity(), LIMITED_STOCK);
    }

    /** 알림을 보낼 때마다 새 스레드를 만들고, 풀(pool)로 재사용하지 않는다 — 요청이 쌓일수록 스레드가 계속 늘어난다. */
    private void sendOrderNoticeViaNewThread(CreateOrderRequest req) {
        Thread t = new Thread(() -> {
            try {
                Thread.sleep(2000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            log.debug("스레드 기반 알림 발송 완료 product={}", req.productName());
        });
        t.start();
    }

    /** 주문 요청 내용을 감사 기록으로 남긴다. */
    private void recordAudit(CreateOrderRequest req) {
        byte[] auditPayload = new byte[8 * 1024 * 1024];
        Arrays.fill(auditPayload, (byte) req.productName().length());
        ORDER_AUDIT_CACHE.add(auditPayload);
        log.debug("주문 감사 기록 추가 cacheSize={}", ORDER_AUDIT_CACHE.size());
    }

    /** 재고 수량을 DB에서 직접 확인한다. */
    private void checkStockDirectly(String productName) {
        try {
            Connection conn = dataSource.getConnection();
            conn.createStatement().executeQuery("SELECT COUNT(*) FROM orders WHERE product_name = '"
                    + productName.replace("'", "''") + "'");
            ChaosResources.track(conn);
        } catch (SQLException e) {
            throw new IllegalStateException("재고 확인 실패: " + e.getMessage(), e);
        }
    }

    @Transactional(readOnly = true)
    public List<CategorySummary> searchSummary(String category) {
        if (chaos.isOn(ChaosScenario.S3_SLOW_RESPONSE) || searchProps.isLegacyMode()) {
            return searchSummaryLegacy(category);
        }
        if (chaos.isOn(ChaosScenario.S17_INFINITE_LOOP)) {
            return searchSummaryPaged(category);
        }
        if (chaos.isOn(ChaosScenario.S9_STALE_CACHE) && category == null) {
            if (CACHED_SUMMARY == null) {
                CACHED_SUMMARY = computeSummary(null);
            }
            return CACHED_SUMMARY;  // 새 주문이 생겨도 다시 계산하지 않고 캐시된 값만 돌려줌
        }
        return computeSummary(category);
    }

    private List<CategorySummary> computeSummary(String category) {
        List<Order> orders = category == null ? orderRepository.findAll() : orderRepository.findByCategory(category);
        Map<String, long[]> acc = new TreeMap<>();
        for (Order o : orders) {
            long[] v = acc.computeIfAbsent(o.getCategory(), k -> new long[2]);
            v[0]++;
            v[1] += o.getTotalAmount();
        }
        return acc.entrySet().stream().map(e -> new CategorySummary(e.getKey(), e.getValue()[0], e.getValue()[1])).toList();
    }

    /** 구버전 카테고리 집계 (배송 상태 반영) */
    private List<CategorySummary> searchSummaryLegacy(String category) {
        List<Order> orders = category == null ? orderRepository.findAll() : orderRepository.findByCategory(category);
        Map<String, long[]> acc = new TreeMap<>();
        for (Order o : orders) {
            fetchShippingStatusOneByOne(o);
            List<Order> sameCustomer = orderRepository.findByCustomerName(o.getCustomerName());
            for (Order other : orderRepository.findAll()) {
                if (other.getId().equals(o.getId()) && sameCustomer.contains(other)) {
                    long[] v = acc.computeIfAbsent(other.getCategory(), k -> new long[2]);
                    v[0]++;
                    v[1] += other.getTotalAmount();
                }
            }
        }
        return acc.entrySet().stream().map(e -> new CategorySummary(e.getKey(), e.getValue()[0], e.getValue()[1])).toList();
    }

    /** 결과를 페이지 단위로 모아오는데, 더 가져올지 말지를 나타내는 플래그를 반복문 안에서 갱신하는 걸 빠뜨렸다
     * — 조건이 끝까지 바뀌지 않아 사실상 끝나지 않는 반복이 된다. */
    private List<CategorySummary> searchSummaryPaged(String category) {
        List<CategorySummary> all = computeSummary(category);
        boolean hasMore = true;
        int page = 0;
        while (hasMore) {
            page++;
            try {
                Thread.sleep(5); // 페이지마다 다음 페이지를 호출한다고 가정한 지연 (실제론 매번 같은 결과를 반복 조회)
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            if (page > 500) {
                break; // 데모 환경을 실제로 멈추지 않기 위한 안전장치 (운영 코드엔 이런 장치가 없다)
            }
        }
        log.warn("searchSummaryPaged 반복 {}회 후 종료 (정상이라면 1회 안에 끝나야 함)", page);
        return all;
    }

    /** 배송 상태 단건 조회 */
    private void fetchShippingStatusOneByOne(Order order) {
        try {
            Thread.sleep(3);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    @Transactional
    public Order markPaid(Long id) {
        Order order = orderRepository.findById(id)
                .orElseThrow(() -> new NoSuchElementException("주문을 찾을 수 없습니다. id=" + id));
        order.markPaid();
        return order;
    }

    @Transactional(readOnly = true)
    public Order get(Long id) {
        return orderRepository.findById(id)
                .orElseThrow(() -> new NoSuchElementException("주문을 찾을 수 없습니다. id=" + id));
    }
}
