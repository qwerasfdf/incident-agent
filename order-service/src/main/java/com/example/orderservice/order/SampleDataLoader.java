package com.example.orderservice.order;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/** 시작 시 샘플 주문 1000건을 넣는다. (seed 고정으로 매번 같은 데이터) */
@Component
public class SampleDataLoader implements CommandLineRunner {

    private final OrderRepository repo;

    public SampleDataLoader(OrderRepository repo) {
        this.repo = repo;
    }

    @Override
    public void run(String... args) {
        if (repo.count() > 0) return;
        Random r = new Random(42);
        String[] customers = {"김민수", "이서연", "박지훈", "최유진", "정다은", "강현우", "조수빈", "윤도현", "장하린", "임재원"};
        String[][] products = {
                {"전자기기", "무선 이어폰", "79000"}, {"전자기기", "모니터 27인치", "329000"}, {"전자기기", "키보드", "89000"},
                {"가구", "사무용 의자", "189000"}, {"가구", "책상", "259000"},
                {"사무용품", "A4 용지", "4500"}, {"사무용품", "볼펜 세트", "6000"}, {"사무용품", "다이어리", "15000"},
                {"식품", "커피 원두 1kg", "32000"}, {"식품", "견과류 세트", "25000"}};
        String[] coupons = {"WELCOME10", "SALE20", "VIP30"};
        OrderStatus[] statuses = OrderStatus.values();
        List<Order> orders = new ArrayList<>();
        for (int i = 0; i < 1000; i++) {
            String[] p = products[r.nextInt(products.length)];
            String coupon = r.nextInt(3) == 0 ? coupons[r.nextInt(coupons.length)] : null; // 약 2/3는 쿠폰 없음
            orders.add(new Order(customers[r.nextInt(customers.length)], p[1], p[0], 1 + r.nextInt(5),
                    Long.parseLong(p[2]), coupon, statuses[r.nextInt(statuses.length)],
                    LocalDateTime.of(2026, 1, 1, 9, 0).plusHours(r.nextInt(24 * 300))));
        }
        repo.saveAll(orders);
    }
}
