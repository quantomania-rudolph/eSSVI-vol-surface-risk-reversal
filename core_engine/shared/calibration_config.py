"""Shared calibration constants for dataingestion and essvi packages.

Single source of truth for thresholds duplicated across ingestion and
calibration. Engine-specific parameters remain in essvi/config.py;
ingestion-specific parameters remain in dataingestion/config.py.
"""

from __future__ import annotations

from typing import Final

# DTE window (calendar days select contract membership)
MIN_DTE: Final[int] = 7   # Blueprint §4: skip weeklies, start at 7 DTE
MAX_DTE: Final[int] = 90

# Belly / wing partition
MIN_DELTA_ABS: Final[float] = 0.10
MAX_DELTA_ABS: Final[float] = 0.90
MAX_REL_SPREAD_HARD: Final[float] = 0.25
MAX_REL_SPREAD_BELLY: Final[float] = 0.10
MIN_OI: Final[int] = 100
BELLY_BOOST: Final[float] = 3.0
BELLY_K_ABS: Final[float] = 0.15

# Slice requirements
MIN_STRIKES_PER_SLICE: Final[int] = 3

# Minimum implied volatility (filters zero/near-zero IV quotes)
MIN_IV: Final[float] = 0.005

# Vega weighting (eSSVI objective uses variance-space vega² by default)
VEGA_WEIGHT_MODE: Final[str] = "var_vega2"  # var_vega2 | vol_vega1 | vol_vega2

# Put-call parity diagnostic
PARITY_SKEW_TOL: Final[float] = 0.005

# Rate interpolation
RATE_INTERPOLATION_METHOD: Final[str] = "linear"  # linear | bucket

# Kill-switch tolerances (per audit type) - P2-4 unified tolerances
KILL_TOL_BUTTERFLY: Final[float] = 1e-6
KILL_TOL_CALENDAR: Final[float] = 1e-8
KILL_TOL_ROPER: Final[float] = 1e-10
KILL_TOL_LEE: Final[float] = 1e-10
KILL_TOL_VERTICAL: Final[float] = 1e-8
# Legacy single tolerance (deprecated, kept for backward compat)
KILL_TOL: Final[float] = 1e-10

# Session tagging
NO_TRADE_OPEN_MIN: Final[int] = 60
NO_TRADE_CLOSE_MIN: Final[int] = 60
SESSION_OPEN_HOUR: Final[int] = 9
SESSION_OPEN_MIN: Final[int] = 30
SESSION_CLOSE_HOUR: Final[int] = 16
SESSION_CLOSE_MIN: Final[int] = 0
HALF_DAY_SESSION_MINUTES: Final[int] = 210
REGULAR_SESSION_MINUTES: Final[int] = 390

# Temporal regularization scales (ingestion stores; engine consumes)
TEMPORAL_THETA_SCALE: Final[float] = 0.1
TEMPORAL_RHO_SCALE: Final[float] = 0.5
TEMPORAL_PSI_SCALE: Final[float] = 0.5
LAMBDA_TEMPORAL_THETA: Final[float] = 0.01
LAMBDA_TEMPORAL_RHO: Final[float] = 0.01
LAMBDA_TEMPORAL_PSI: Final[float] = 0.01
TEMPORAL_THETA_LOG: Final[bool] = True

# Expiry-imminent handling
EXPIRY_IMMINENT_DTE: Final[int] = 1
