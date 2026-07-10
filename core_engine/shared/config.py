"""Configuration for Theta Data Terminal v3 REST API client (HTTP layer only)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --- Theta Data local Java Terminal (v3 REST bridge) ---
    THETA_HOST: str = os.getenv("THETA_HOST", "127.0.0.1")
    THETA_PORT: int = int(os.getenv("THETA_PORT", "25510"))
    THETA_TIMEOUT_S: int = int(os.getenv("THETA_TIMEOUT_S", "30"))
    HEARTBEAT_RETRIES: int = int(os.getenv("HEARTBEAT_RETRIES", "5"))
    HEARTBEAT_BACKOFF_S: float = float(os.getenv("HEARTBEAT_BACKOFF_S", "2.0"))
    REQUESTS_PER_SECOND: int = int(os.getenv("REQUESTS_PER_SECOND", "0"))  # 0 = no RPS cap

    @property
    def THETA_BASE(self) -> str:
        return f"http://{self.THETA_HOST}:{self.THETA_PORT}"


CFG = Config()