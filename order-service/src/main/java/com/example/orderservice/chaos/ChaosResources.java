package com.example.orderservice.chaos;

import java.sql.Connection;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** 장애 재현 중 생긴 자원(미반환 커넥션)을 OFF 시점에 정리하기 위한 보관소. 재현 편의용. */
public final class ChaosResources {

    private static final List<Connection> CONNECTIONS = Collections.synchronizedList(new ArrayList<>());

    private ChaosResources() {
    }

    public static void track(Connection conn) {
        CONNECTIONS.add(conn);
    }

    public static void closeAll() {
        synchronized (CONNECTIONS) {
            for (Connection c : CONNECTIONS) {
                try {
                    c.close();
                } catch (SQLException ignored) {
                    // 정리 실패는 무시
                }
            }
            CONNECTIONS.clear();
        }
    }
}
