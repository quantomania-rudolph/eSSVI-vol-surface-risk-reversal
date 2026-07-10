"""Shared pytest fixtures for essvi integration tests.

Production config uses a fine rho grid (step=0.01). Integration tests patch
coarser grids so sequential/solver suites finish in seconds, not minutes.
"""

from __future__ import annotations

import pytest

from essvi import config as cfg


@pytest.fixture
def essvi_fast_calibration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coarser search grids for test runtime only."""
    monkeypatch.setattr(cfg, "RHO_GRID_STEP", 0.1)
    monkeypatch.setattr(cfg, "RHO_GRID_REFINE_FACTOR", 3)
    monkeypatch.setattr(cfg, "MM_L_GRID_POINTS", 80)
    monkeypatch.setattr(cfg, "BRENT_MAX_ITER", 40)
    monkeypatch.setattr(cfg, "U_PSI_GRID_POINTS", 40)
