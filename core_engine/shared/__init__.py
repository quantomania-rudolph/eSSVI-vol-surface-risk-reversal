"""Core Engine shared modules — HTTP client, config, parsing, common constants.

This is a pure HTTP layer.  No endpoint-specific fetchers live here.
The caller constructs HTTP requests via AsyncThetaClient.get() and
manages its own concurrency (asyncio.Semaphore).

Usage:
    from core_engine.shared.config import CFG
    from core_engine.shared.theta_client import AsyncThetaClient, heartbeat
    from core_engine.shared.parse import parse_response_body, to_dataframe

    heartbeat()
    async with AsyncThetaClient(CFG) as client:
        status, payload = await client.get("/v3/option/list/expirations",
                                            {"symbol": "AMD"})
"""
