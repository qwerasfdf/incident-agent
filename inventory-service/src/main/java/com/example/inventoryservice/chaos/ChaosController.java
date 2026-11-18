package com.example.inventoryservice.chaos;

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

    public ChaosController(ChaosState state) {
        this.state = state;
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
        log.debug("chaos on {}", s.code());
        return Map.of("code", s.code(), "on", true);
    }

    @PostMapping("/{code}/off")
    public Map<String, Object> off(@PathVariable String code) {
        ChaosScenario s = ChaosScenario.fromCode(code);
        state.off(s);
        log.debug("chaos off {}", s.code());
        return Map.of("code", s.code(), "on", false);
    }

    @PostMapping("/reset")
    public Map<String, Object> reset() {
        for (ChaosScenario s : ChaosScenario.values()) {
            state.off(s);
        }
        return Map.of("reset", true);
    }
}
