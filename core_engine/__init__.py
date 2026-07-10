"""Theta Data Core Engine — Pure HTTP client library for Theta Terminal v3.

No business logic, no fetchers, no semaphores.  Just the HTTP layer
with retries, parsing, rate limiting, and a blocking heartbeat.

Callers provide their own asyncio.Semaphore for concurrency control
tailored to their Theta Data subscription tier.
"""

from .shared.config import CFG, Config
from .shared.theta_client import AsyncThetaClient, ThetaTerminalDown, heartbeat

__version__ = "1.0.0"
__all__ = [
    "CFG",
    "Config",
    "AsyncThetaClient",
    "ThetaTerminalDown",
    "heartbeat",
]