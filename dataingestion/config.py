"""Centralized configuration for AMD eSSVI data ingestion pipeline.

All thresholds, constants, and tunable parameters live here.
Import from this module — never hardcode values in pipeline modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from core_engine.shared.calibration_config import (
    BELLY_BOOST,
    BELLY_K_ABS,
    EXPIRY_IMMINENT_DTE,
    HALF_DAY_SESSION_MINUTES,
    KILL_TOL,
    KILL_TOL_BUTTERFLY,
    KILL_TOL_CALENDAR,
    KILL_TOL_LEE,
    KILL_TOL_ROPER,
    LAMBDA_TEMPORAL_PSI,
    LAMBDA_TEMPORAL_RHO,
    LAMBDA_TEMPORAL_THETA,
    MAX_DELTA_ABS,
    MAX_DTE,
    MAX_REL_SPREAD_BELLY,
    MAX_REL_SPREAD_HARD,
    MIN_DELTA_ABS,
    MIN_DTE,
    MIN_OI,
    MIN_STRIKES_PER_SLICE,
    NO_TRADE_CLOSE_MIN,
    NO_TRADE_OPEN_MIN,
    PARITY_SKEW_TOL,
    RATE_INTERPOLATION_METHOD,
    REGULAR_SESSION_MINUTES,
    SESSION_CLOSE_HOUR,
    SESSION_CLOSE_MIN,
    SESSION_OPEN_HOUR,
    SESSION_OPEN_MIN,
    TEMPORAL_PSI_SCALE,
    TEMPORAL_RHO_SCALE,
    TEMPORAL_THETA_LOG,
    TEMPORAL_THETA_SCALE,
    VEGA_WEIGHT_MODE,
)

# ============================================================================
# THETA API PARAMETERS
# ============================================================================

THETA_INTERVAL: Final[str] = "1m"
THETA_FORMAT: Final[str] = "ndjson"
THETA_ANNUAL_DIVIDEND: Final[int] = 0
THETA_RATE_TYPE: Final[str] = "sofr"
THETA_VERSION: Final[str] = "latest"

# Theta Data local Java Terminal (v3 REST bridge)
THETA_HOST: Final[str] = os.getenv("THETA_HOST", "127.0.0.1")
THETA_PORT: Final[int] = int(os.getenv("THETA_PORT", "25510"))
THETA_TIMEOUT_S: Final[int] = int(os.getenv("THETA_TIMEOUT_S", "30"))
HEARTBEAT_RETRIES: Final[int] = int(os.getenv("HEARTBEAT_RETRIES", "5"))
HEARTBEAT_BACKOFF_S: Final[float] = float(os.getenv("HEARTBEAT_BACKOFF_S", "2.0"))
REQUESTS_PER_SECOND: Final[int] = int(os.getenv("REQUESTS_PER_SECOND", "0"))  # 0 = no RPS cap

@dataclass(frozen=True)
class ThetaConfig:
    """Config dataclass matching the interface expected by AsyncThetaClient and heartbeat()."""
    THETA_HOST: str = THETA_HOST
    THETA_PORT: int = THETA_PORT
    THETA_TIMEOUT_S: int = THETA_TIMEOUT_S
    HEARTBEAT_RETRIES: int = HEARTBEAT_RETRIES
    HEARTBEAT_BACKOFF_S: float = HEARTBEAT_BACKOFF_S
    REQUESTS_PER_SECOND: int = REQUESTS_PER_SECOND

    @property
    def THETA_BASE(self) -> str:
        return f"http://{self.THETA_HOST}:{self.THETA_PORT}"

# Default instance for backward compatibility
THETA_CFG = ThetaConfig()

# ============================================================================
# CLEANING THRESHOLDS (dataingestion.md Sections 4-5)
# ============================================================================

# DTE band, delta band, spread thresholds — from shared calibration_config
# (re-exported at module level for backward compatibility)

# IV
MIN_IV: Final[float] = 0.005

# Subpenny detection
SUBPENNY_EPS: Final[float] = 1e-8

# Quality flag bits
QUALITY_BELLY_SPREAD: Final[int] = 1       # bit 0 — belly spread (>10% rel spread, not hard-rejected)
QUALITY_INTRINSIC_TOL: Final[int] = 2      # bit 1 — intrinsic tolerance (reserved, not yet implemented)
QUALITY_MONOTONICITY_TOL: Final[int] = 4   # bit 2 — monotonicity tolerance (reserved, not yet implemented)
QUALITY_EXPIRY_IMMINENT: Final[int] = 8    # bit 3 — DTE=1 expiry-imminent slice
BELLY_SPREAD_BIT: Final[int] = 1           # deprecated alias — prefer QUALITY_BELLY_SPREAD

# ============================================================================
# BUSINESS TIME (dataingestion.md Section 6)
# ============================================================================

BUSINESS_MINUTES_PER_DAY: Final[int] = 390
TRADING_DAYS_PER_YEAR: Final[int] = 252
BUSINESS_MINUTES_PER_YEAR: Final[int] = BUSINESS_MINUTES_PER_DAY * TRADING_DAYS_PER_YEAR

# Numba guards
NUMBA_SIGMA_EPS: Final[float] = 1e-10
NUMBA_T_EPS: Final[float] = 1e-10

# ============================================================================
# DATABASE (TimescaleDB)
# ============================================================================


@dataclass(frozen=True)
class PGConfig:
    host: str = os.getenv("PGHOST", "127.0.0.1")
    port: int = int(os.getenv("PGPORT", "5432"))
    user: str = os.getenv("PGUSER", "postgres")
    password: str = os.getenv("PGPASSWORD", "postgres")
    database: str = os.getenv("PGDATABASE", "postgres")
    min_size: int = 1
    max_size: int = 10


PG_CONFIG = PGConfig()

# Hypertable
CHUNK_TIME_INTERVAL_DAYS: Final[int] = 7
COMPRESSION_INTERVAL_DAYS: Final[int] = 7

# ============================================================================
# CONCURRENCY (dataingestion.md Section V)
# ============================================================================

OPT_SEM_LIMIT: Final[int] = int(os.getenv("OPT_SEM_LIMIT", "4"))   # Standard tier
STK_SEM_LIMIT: Final[int] = int(os.getenv("STK_SEM_LIMIT", "10"))   # Value tier

# ============================================================================
# ORCHESTRATOR
# ============================================================================

# DTE window for expiration eligibility (same as cleaning pre-filter)
DTE_WINDOW_MIN: Final[int] = MIN_DTE
DTE_WINDOW_MAX: Final[int] = MAX_DTE

# Schedule cache buffer (calendar days before earliest needed date)
# Increased from 5 to 14 for holiday safety (Christmas+New Year period)
SCHEDULE_BUFFER_DAYS: Final[int] = 14

# Chunk size
MAX_CHUNK_DAYS: Final[int] = 31  # ≤1 month (calendar days)
MAX_TRADING_DAYS_PER_CHUNK: Final[int] = 21  # ≈1 month in trading days (21 ≈ 252 / 12)

# ============================================================================
# CACHE CONFIGURATION (EH-06 / EH209)
# ============================================================================

# OHLC cache: max number of chunks to cache (bounds memory for 7-year backfill)
OHLC_CACHE_MAX_CHUNKS: Final[int] = int(os.getenv("OHLC_CACHE_MAX_CHUNKS", "50"))

# OHLC cache TTL in hours (auto-evict stale entries)
OHLC_CACHE_TTL_HOURS: Final[int] = int(os.getenv("OHLC_CACHE_TTL_HOURS", "24"))

# Rates cache TTL in hours (SOFR rates change daily)
RATES_CACHE_TTL_HOURS: Final[int] = int(os.getenv("RATES_CACHE_TTL_HOURS", "6"))

# ============================================================================
# FETCH RETRY CONFIGURATION (EH206)
# ============================================================================

FETCH_MAX_RETRIES: Final[int] = int(os.getenv("FETCH_MAX_RETRIES", "3"))
FETCH_BASE_DELAY: Final[float] = float(os.getenv("FETCH_BASE_DELAY", "1.0"))
FETCH_MAX_DELAY: Final[float] = float(os.getenv("FETCH_MAX_DELAY", "30.0"))
FETCH_RETRYABLE_STATUS: Final[set[int]] = {429, 500, 502, 503, 504}
FETCH_NON_RETRYABLE_STATUS: Final[set[int]] = {400, 401, 403, 404}

# ============================================================================
# OI MODE CONFIG (dataingestion.md Section 12.8)
# ============================================================================

# OI_MODE controls which session's open interest is joined to intraday bars:
#   "strict"   — join prior session's EOD OI (D-1) — no leakage, default
#   "research" — join same-day EOD OI (D) — leaks EOD OI into intraday bars
OI_MODE: Final[str] = "strict"

# ============================================================================
# RATE CONFIGURATION (dataingestion.md Section 7)
# ============================================================================

# Rate symbols by DTE tenor bucket.
# DTE ranges follow the blueprint: short ≤ 30, medium 31-60, long 61-90.
RATE_SYMBOLS_SHORT: Final[tuple[str, ...]] = ("TREASURY_M1",)
RATE_SYMBOLS_MEDIUM: Final[tuple[str, ...]] = ("SOFR", "TREASURY_M1")
RATE_SYMBOLS_LONG: Final[tuple[str, ...]] = ("TREASURY_M1", "TREASURY_M3")

# Compounding switch: True = convert simple→cc via r_cc = ln(1 + r_simple*τ)/τ.
# False = treat rate/100 as continuous compounding (acceptable for short tenors).
SIMPLE_TO_CC: Final[bool] = True

# DTE knots for linear rate interpolation (SOFR≈0d, M1≈30d, M3≈90d)
RATE_DTE_KNOTS: Final[tuple[int, ...]] = (0, 30, 90)
RATE_SYMBOL_KNOTS: Final[tuple[str, ...]] = ("SOFR", "TREASURY_M1", "TREASURY_M3")

# DTE bucket thresholds (inclusive on both sides unless noted)
DTE_BUCKET_SHORT_MAX: Final[int] = 30
DTE_BUCKET_MEDIUM_MAX: Final[int] = 60
DTE_BUCKET_LONG_MAX: Final[int] = 90

# ============================================================================
# DIVIDEND YIELD CONFIGURATION
# ============================================================================

# Dividend provider: "alphavantage" | "polygon" | "none" (for AMD q=0)
DIVIDEND_PROVIDER: Final[str] = os.getenv("DIVIDEND_PROVIDER", "none")

# Alpha Vantage API key (if using alphavantage)
ALPHAVANTAGE_API_KEY: Final[str] = os.getenv("ALPHAVANTAGE_API_KEY", "")

# Polygon API key (if using polygon)
POLYGON_API_KEY: Final[str] = os.getenv("POLYGON_API_KEY", "")

# Dividend yield computation: trailing 12-month cash dividends / spot
# For point-in-time, only use dividends with announcement_date <= bar_date
DIVIDEND_LOOKBACK_DAYS: Final[int] = 365

# For AMD: hardcoded q=0 (no dividends since 1995)
AMD_Q_OVERRIDE: Final[float] = 0.0

# Session tagging, kill tolerances, vega mode — from shared calibration_config

# ============================================================================
# MM BUTTERFLY GRID (for eSSVI calibration)
# ============================================================================

MM_RHO_GRID_POINTS: Final[int] = 200
MM_THETA_GRID_POINTS: Final[int] = 100
MM_THETA_GRID_MIN: Final[float] = 1e-6  # Extended for DTE=1
MM_THETA_GRID_MAX: Final[float] = 10.0
MM_L_MAX: Final[float] = 1000.0
MM_L_GRID_POINTS: Final[int] = 500

# ============================================================================
# VEGA UNITS DOCUMENTATION
# ============================================================================

# Vega is ∂Price/∂σ for a full 1.00 vol move (σ in decimals, e.g. 0.25).
# Downstream consumers should NOT multiply or divide by 100 — the value
# represents the P&L impact of a 1.00 (100 percentage-point) change in vol.
VEGA_UNITS: Final[str] = "per_1.0_vol"