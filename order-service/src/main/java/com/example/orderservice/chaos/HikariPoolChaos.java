package com.example.orderservice.chaos;

import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import com.zaxxer.hikari.HikariDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;

/**
 * [S18] 운영 중 설정 실수로 DB 커넥션 풀 크기가 1로 줄어든 상황을 재현한다.
 * ON: maximum-pool-size를 1로 변경 / OFF: 원래 값으로 복구
 */
@Component
public class HikariPoolChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");

    private final HikariDataSource dataSource;
    private final int originalMax;

    public HikariPoolChaos(DataSource dataSource) {
        this.dataSource = (HikariDataSource) dataSource;
        this.originalMax = this.dataSource.getMaximumPoolSize();
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S18_SMALL_POOL; }

    @Override
    public void activate() {
        int before = dataSource.getMaximumPoolSize();
        dataSource.setMaximumPoolSize(1);
        log.info("설정 갱신 적용 spring.datasource.hikari.maximum-pool-size: {} -> {}", before,
                dataSource.getMaximumPoolSize());
    }

    @Override
    public void cleanup() {
        if (dataSource.getMaximumPoolSize() != originalMax) {
            int before = dataSource.getMaximumPoolSize();
            dataSource.setMaximumPoolSize(originalMax);
            log.info("설정 갱신 적용 spring.datasource.hikari.maximum-pool-size: {} -> {}", before, originalMax);
        }
    }
}
