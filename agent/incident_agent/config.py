"""Agent 설정. 환경변수로 바꿀 수 있다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # incident-agent 폴더


@dataclass
class AgentConfig:
    service_url: str = os.environ.get("SERVICE_URL", "http://localhost:8081")
    ollama_url: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model: str = os.environ.get("AGENT_MODEL", "qwen2.5:3b")
    log_path: Path = Path(os.environ.get("SERVICE_LOG", ROOT / "order-service" / "logs" / "app.log"))
    source_dir: Path = Path(os.environ.get("SERVICE_SOURCE", ROOT / "order-service" / "src" / "main" / "java"))
    # 멀티 서비스 장애 전파 시나리오(M1/M2)용 — 재고 서비스(inventory-service)의 로그·소스도 함께 본다
    log_path_inventory: Path = Path(os.environ.get("INVENTORY_LOG", ROOT / "inventory-service" / "logs" / "app.log"))
    source_dir_inventory: Path = Path(os.environ.get(
        "INVENTORY_SOURCE", ROOT / "inventory-service" / "src" / "main" / "java"))
    log_window_min: int = 10        # 최근 몇 분의 로그를 볼지
    log_since: object = None        # 이 시각 이후 로그만 (평가용, datetime)
    stats_baseline: dict | None = None  # 이 시점 이후 증가분만 보기 (평가용, Tools.snapshot_baseline 결과)
    max_rounds: int = 2             # LLM 추가 조사 반복 횟수
    max_tool_calls_per_round: int = 3
    num_ctx: int = 8192             # 모델 입력 길이 한도
    llm_timeout: int = 600

    # Agent가 볼 수 없는 코드 (장애 재현 장치, 가상의 외부 결제사)
    hidden_paths: tuple = field(default=(
        "/chaos/", "MockExternalPaymentController.java", "PaymentConfigChaos.java", "OrderChaosCleanups.java",
        "OrderTimeChaos.java",
    ))
