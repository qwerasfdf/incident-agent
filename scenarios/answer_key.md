# 에러 시나리오 정답지 (1차)

> 4주차 진단 정확도 평가의 기준. Agent가 결론 낸 원인을 이 표와 비교해 채점한다.
> 로그 문구는 실제 재현 시 `logs/app.log`에 남은 내용이다.
> 평가 시 Agent 도구에서 `chaos` 패키지, `/chaos` API, `/mock-external` 코드는 보이지 않게 한다.

| No | 에러 유형 | 증상 (사용자 관점) | 확인되는 지표 | 로그 근거 | 원인 위치 | 올바른 조치 |
|---|---|---|---|---|---|---|
| S1 | 메모리 누수 | 주문 생성 약 25건 후부터 주문 생성만 500, 조회 API는 정상 | `jvm.memory.used`(heap)가 주문마다 약 8MB씩 증가해 상한(256MB) 근처에서 유지 | `OutOfMemoryError: Java heap space`, 스택에 `OrderService.recordAudit` | `OrderService.recordAudit()` — static `ORDER_AUDIT_CACHE`에 요청마다 8MB 추가, 비우는 코드 없음 | 캐시 제거 또는 크기 제한, 임시로 서비스 재시작 |
| S2 | DB 커넥션 고갈 | 주문 생성 약 10건 후부터 3초 대기 뒤 500 | `hikaricp.connections.active` 9~10 고정, `hikaricp.connections.timeout` 증가 | `OrderHikariPool - Connection is not available, request timed out after 3000ms (total=10, active=10, idle=0 ...)`, 스택에 `OrderService.checkStockDirectly` | `OrderService.checkStockDirectly()` — `dataSource.getConnection()` 후 `close()` 누락 | try-with-resources로 커넥션 반환, 임시로 재시작 |
| S3 | 응답 지연 | 주문 검색(`/api/orders/search`)만 4초 이상 소요, 오류는 없음 | `http.server.requests`에서 search URI 처리 시간 증가 | `WARN access - 느린 요청 GET /api/orders/search status=200 elapsedMs=4515` (에러 로그 없음) | `OrderService.searchSummaryLegacy()` — 주문마다 배송 상태 단건 조회 + 전체 재조회(N+1) | 집계 쿼리 한 번으로 변경(기존 `searchSummary` 로직), 단건 조회 일괄화 |
| S4 | 잘못된 설정값 | 결제(`/api/payments`)만 즉시 500, 주문 API는 정상 | `/actuator/configprops`의 `payment.external.baseUrl` = `.../mock-externl` (오타) | `설정 갱신 적용 payment.external.base-url=http://localhost:8081/mock-externl` 직후 `HttpClientErrorException$NotFound: 404` | 설정값 `payment.external.base-url` 오타 (코드 문제 아님) | 설정값을 `.../mock-external`로 수정 |
| S5 | 외부 API 타임아웃 | 결제만 약 2초 후 500 | 결제 요청 처리 시간 약 2초(=read-timeout) | `SocketTimeoutException: Read timed out`, `느린 요청 POST /api/payments ... elapsedMs=2015`, 직전 `외부 결제 승인 요청` 로그 | 외부 결제 대행사(PG) 응답 지연 (내부 코드 문제 아님) | PG사 상태 확인·문의, 재시도/타임아웃 정책 검토. 내부 코드 수정 불필요 |
| S6 | 예외 처리 누락 | 일부 주문 상세 조회만 500, 쿠폰 있는 주문은 정상 | 특정 id에서만 5xx | `NullPointerException: Cannot invoke "String.toUpperCase()" because the return value of "...Order.getCouponCode()" is null` | `OrderService.couponLabel()` — 쿠폰 없는 주문(null) 처리 누락 | null 체크 추가 (쿠폰 없으면 "쿠폰 없음") |
| S7 | 비동기 처리 오용(동기 대기) | 주문 생성(`/api/orders`)만 매번 약 4초 소요, 오류는 없음 | `http.server.requests`에서 orders(POST) 처리 시간 증가 | `WARN access - 느린 요청 POST /api/orders status=200 elapsedMs=40xx` (에러 로그 없음) | `OrderService.sendOrderNoticeBlocking()` — 백그라운드로 보내야 할 알림 작업을 `Future.get()`으로 동기 대기 | 알림 발송을 `get()` 없이 비동기로 넘기거나 결과를 기다리지 않도록 수정 |
| S8 | 타임존 설정 오류 | 주문은 에러 없이 정상 처리되지만, 생성 시각(`orderedAt`)이 실제보다 9시간 이전으로 기록됨 | `/actuator/configprops`의 `app.order.zoneId` = `UTC` (기본값 `Asia/Seoul`과 다름) | `설정 갱신 적용 app.order.zone-id: Asia/Seoul -> UTC` | `app.order.zone-id` 설정값이 `UTC`로 변경됨 (코드 문제 아님) | 설정값을 `Asia/Seoul`로 복구 |
| S9 | 캐시 미반영 | 주문 생성 직후 전체 카테고리 검색(`/api/orders/search`, 카테고리 미지정)에 새 주문이 반영되지 않음 | 특이 지표 없음 — 응답 바디의 건수·합계가 신규 주문 전후로 그대로 | 에러·경고 로그 없음 | `OrderService.searchSummary()` — 최초 조회 결과를 `CACHED_SUMMARY`에 캐시한 뒤 무효화 로직이 없음 | 주문 생성 시 캐시 무효화 추가, 또는 캐시 제거 |
| S10 | 결제 예외 삼킴 | 결제(`/api/payments`)가 항상 200 "PAID"를 반환하지만 승인 결과(`approval`)가 `{"result":"UNKNOWN"}`으로만 나옴 | 특이 지표 없음 — 응답 바디의 `approval` 필드로만 확인 가능 | `결제 승인 처리 중 예외 발생 (무시하고 진행)` — **DEBUG 레벨**(기본 로그 레벨에서는 안 보임) | `PaymentService.pay()` — 승인 과정 예외를 catch 후 무시하고 가짜 성공으로 처리 | 예외를 삼키지 말고 전파하거나 명확한 실패 응답으로 변경 |
| S11 | 불필요한 재시도 폭주 | 결제 1건 요청 시 외부 결제사(mock-external) 승인 호출이 실제보다 과도하게(약 4배) 발생. 결제 자체는 성공 | 외부 호출 횟수/`http.server.requests`(mock-external) 증가 | 동일 요청 처리 중 `외부 결제 승인 요청` 로그가 짧은 시간 안에 여러 번 반복 | `PaymentService.pay()` — 실패 여부와 무관하게 백오프 없이 승인 요청을 추가로 3회 더 호출 | 불필요한 사전 재시도 제거, 재시도는 실패 시에만 백오프와 함께 수행 |
| S12 | 동시성 유실 업데이트 | 한정 수량 상품을 동시에 여러 건 주문하면, 실제 주문 수량 합만큼 재고가 줄지 않고 일부 차감이 누락됨 | 특이 지표 없음 — `한정 수량 재고 차감` 로그의 "남은수량(추정)" 값이 기대치와 불일치 | `한정 수량 재고 차감 product=... qty=... 남은수량(추정)=...` (겹치는 요청에서 값이 일부 덮어써짐) | `OrderService.decrementLimitedStock()` — 재고를 읽고(read) 대기 후 쓰는(write) 구간이 원자적이지 않은 전형적인 lost-update | 재고 차감을 원자적 연산(DB 단일 UPDATE, 락, `AtomicInteger` 등)으로 변경 |

## 평가 시 주의할 함정

- **S4·S5는 내부 코드 결함이 아니다.** Agent가 코드 수정을 제안하면 오답 처리.
- **S3는 에러 로그가 없다.** 에러만 검색하는 Agent는 놓친다. 처리 시간 지표와 WARN 로그를 봐야 한다.
- **S2는 GET 요청은 정상**(남은 커넥션 1개)이라 "DB 장애"로 오진하기 쉽다. 커넥션 지표로 미반환을 짚어야 정답.
- **S1과 S2는 둘 다 `POST /api/orders`에서 발생**한다. 증상이 비슷해도 지표(heap vs 커넥션)로 구분해야 한다.
- **S8·S9·S10은 에러·예외가 전혀 없다.** 응답 바디나 configprops를 직접 비교해야 발견되는, "장애처럼 보이지 않는" 문제다.
- **S10의 단서 로그는 DEBUG 레벨이라 기본 설정에서는 보이지 않는다.** 로그만으로는 못 찾고, 응답 바디의 `approval` 값을 봐야 한다.
- **S12는 동시 요청이 있어야만 재현된다.** 순차적으로 하나씩 호출하면 정상으로 보이므로, 반드시 동시 요청(콘솔의 "동시 주문 8건")으로 확인해야 한다.
- **S7은 S3(응답 지연)와 영향 API가 다르다.** S3는 검색(`GET /api/orders/search`), S7은 주문 생성(`POST /api/orders`)이 느려진다.
