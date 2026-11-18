package com.example.orderservice.chaos;

import com.example.orderservice.chaos.ChaosController.ChaosActivation;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.logging.LogLevel;
import org.springframework.boot.logging.LoggingSystem;
import org.springframework.stereotype.Component;

/**
 * [S19] 운영 중 설정 실수로 애플리케이션 로그 레벨이 TRACE로 방치된 상황을 재현한다.
 * ON: com.example.orderservice 로그 레벨을 TRACE로 변경 / OFF: INFO로 복구
 */
@Component
public class LogLevelChaos implements ChaosCleanup, ChaosActivation {

    private static final Logger log = LoggerFactory.getLogger("config.refresh");
    private static final String LOGGER_NAME = "com.example.orderservice";

    private final LoggingSystem loggingSystem;

    public LogLevelChaos(LoggingSystem loggingSystem) {
        this.loggingSystem = loggingSystem;
    }

    @Override
    public ChaosScenario scenario() { return ChaosScenario.S19_LOG_LEVEL; }

    @Override
    public void activate() {
        loggingSystem.setLogLevel(LOGGER_NAME, LogLevel.TRACE);
        log.info("설정 갱신 적용 logging.level.{}: INFO -> TRACE", LOGGER_NAME);
    }

    @Override
    public void cleanup() {
        loggingSystem.setLogLevel(LOGGER_NAME, LogLevel.INFO);
        log.info("설정 갱신 적용 logging.level.{}: TRACE -> INFO", LOGGER_NAME);
    }
}
