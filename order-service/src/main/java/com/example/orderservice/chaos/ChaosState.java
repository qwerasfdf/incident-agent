package com.example.orderservice.chaos;

import org.springframework.stereotype.Component;

import java.util.EnumSet;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class ChaosState {

    private final Set<ChaosScenario> active = ConcurrentHashMap.newKeySet();

    public boolean isOn(ChaosScenario s) { return active.contains(s); }
    public void on(ChaosScenario s) { active.add(s); }
    public void off(ChaosScenario s) { active.remove(s); }

    public Set<ChaosScenario> active() {
        return active.isEmpty() ? EnumSet.noneOf(ChaosScenario.class) : EnumSet.copyOf(active);
    }
}
