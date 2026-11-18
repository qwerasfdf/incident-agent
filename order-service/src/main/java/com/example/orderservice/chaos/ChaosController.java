package com.example.orderservice.chaos;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.*;

import java.util.*;

/** 장애 시나리오 ON/OFF 관리용 API (시연·평가 준비용, Agent 도구에서는 제외) */
@RestController
@RequestMapping("/chaos")
public class ChaosController {

    private static final Logger log = LoggerFactory.getLogger("chaos");

    private final ChaosState state;
    private final List<ChaosCleanup> cleanups;

    public ChaosController(ChaosState state, List<ChaosCleanup> cleanups) {
        this.state = state;
        this.cleanups = cleanups;
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (ChaosScenario s : ChaosScenario.values()) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("code", s.code());
            m.put("title", s.title());
            m.put("target", s.target());
            m.put("on", state.isOn(s));
            out.add(m);
        }
        return out;
    }

    @PostMapping("/{code}/on")
    public Map<String, Object> on(@PathVariable String code) {
        ChaosScenario s = ChaosScenario.fromCode(code);
        state.on(s);
        cleanups.stream().filter(c -> c.scenario() == s && c instanceof ChaosActivation)
                .forEach(c -> ((ChaosActivation) c).activate());
        log.debug("chaos on {}", s.code());
        return Map.of("code", s.code(), "on", true);
    }

    @PostMapping("/{code}/off")
    public Map<String, Object> off(@PathVariable String code) {
        ChaosScenario s = ChaosScenario.fromCode(code);
        state.off(s);
        cleanups.stream().filter(c -> c.scenario() == s).forEach(ChaosCleanup::cleanup);
        log.debug("chaos off {}", s.code());
        return Map.of("code", s.code(), "on", false);
    }

    @PostMapping("/reset")
    public Map<String, Object> reset() {
        // 실제로 켜져 있던 시나리오만 off + cleanup한다. 모든 ChaosCleanup을 무조건 cleanup()하면
        // 꺼져 있던 설정 시나리오(S19 로그 레벨 등)까지 "복원" 로그를 새로 남기게 되어,
        // 그 직후 다른 시나리오를 테스트할 때 "방금 설정이 바뀌었다"는 가짜 신호로 잡혀
        // Agent의 설정 변경 감지 로직과 충돌한다 (예: 리셋 직후 S26을 테스트하면 방금 찍힌
        // logging.level 복원 로그가 전혀 무관한 결제 실패와 엮여버림).
        for (ChaosScenario s : ChaosScenario.values()) {
            if (state.isOn(s)) {
                state.off(s);
                cleanups.stream().filter(c -> c.scenario() == s).forEach(ChaosCleanup::cleanup);
            }
        }
        return Map.of("reset", true);
    }

    /** ON 시점에 상태를 바꿔야 하는 시나리오(S4 설정 변경 등)용 */
    public interface ChaosActivation {
        void activate();
    }
}
