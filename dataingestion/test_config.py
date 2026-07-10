"""Tests for dataingestion.config module and orchestrator config integration."""

from __future__ import annotations

import pytest


class TestConfigModule:
    """Tests for dataingestion.config module."""

    def test_config_module_exists(self):
        """Verify config module can be imported."""
        from dataingestion import config
        assert config is not None

    def test_concurrency_constants(self):
        """Verify concurrency constants exist with correct values."""
        from dataingestion.config import OPT_SEM_LIMIT, STK_SEM_LIMIT
        assert OPT_SEM_LIMIT == 4
        assert STK_SEM_LIMIT == 10

    def test_dte_window_constants(self):
        """Verify DTE window constants exist with correct values."""
        from dataingestion.config import DTE_WINDOW_MIN, DTE_WINDOW_MAX
        assert DTE_WINDOW_MIN == 1
        assert DTE_WINDOW_MAX == 90

    def test_chunk_size_constant(self):
        """Verify MAX_CHUNK_DAYS constant exists with correct value."""
        from dataingestion.config import MAX_CHUNK_DAYS
        assert MAX_CHUNK_DAYS == 31

    def test_theta_api_constants(self):
        """Verify Theta API constants exist with correct values."""
        from dataingestion.config import (
            THETA_INTERVAL, THETA_FORMAT, THETA_ANNUAL_DIVIDEND,
            THETA_RATE_TYPE, THETA_VERSION
        )
        assert THETA_INTERVAL == "1m"
        assert THETA_FORMAT == "ndjson"
        assert THETA_ANNUAL_DIVIDEND == 0
        assert THETA_RATE_TYPE == "sofr"
        assert THETA_VERSION == "latest"

    def test_cleaning_constants(self):
        """Verify cleaning constants exist with correct values."""
        from dataingestion.config import (
            MIN_DTE, MAX_DTE,
            MIN_DELTA_ABS, MAX_DELTA_ABS,
            MAX_REL_SPREAD_HARD, MAX_REL_SPREAD_BELLY,
            MIN_IV, MIN_OI,
            SUBPENNY_EPS, BELLY_SPREAD_BIT
        )
        assert MIN_DTE == 1
        assert MAX_DTE == 90
        assert MIN_DELTA_ABS == 0.10
        assert MAX_DELTA_ABS == 0.90
        assert MAX_REL_SPREAD_HARD == 0.25
        assert MAX_REL_SPREAD_BELLY == 0.10
        assert MIN_IV == 0.005
        assert MIN_OI == 100
        assert SUBPENNY_EPS == 1e-8
        assert BELLY_SPREAD_BIT == 1

    def test_business_time_constants(self):
        """Verify business time constants exist with correct values."""
        from dataingestion.config import (
            BUSINESS_MINUTES_PER_DAY, TRADING_DAYS_PER_YEAR,
            BUSINESS_MINUTES_PER_YEAR, NUMBA_SIGMA_EPS, NUMBA_T_EPS
        )
        assert BUSINESS_MINUTES_PER_DAY == 390
        assert TRADING_DAYS_PER_YEAR == 252
        assert BUSINESS_MINUTES_PER_YEAR == 390 * 252
        assert NUMBA_SIGMA_EPS == 1e-10
        assert NUMBA_T_EPS == 1e-10

    def test_database_constants(self):
        """Verify database constants exist with correct values."""
        from dataingestion.config import (
            PG_CONFIG, CHUNK_TIME_INTERVAL_DAYS, COMPRESSION_INTERVAL_DAYS
        )
        assert CHUNK_TIME_INTERVAL_DAYS == 7
        assert COMPRESSION_INTERVAL_DAYS == 7
        assert PG_CONFIG.min_size == 1
        assert PG_CONFIG.max_size == 10

    def test_cache_config_constants(self):
        """Verify cache config constants exist with correct default values."""
        from dataingestion.config import (
            OHLC_CACHE_MAX_CHUNKS, OHLC_CACHE_TTL_HOURS, RATES_CACHE_TTL_HOURS
        )
        assert OHLC_CACHE_MAX_CHUNKS == 50
        assert OHLC_CACHE_TTL_HOURS == 24
        assert RATES_CACHE_TTL_HOURS == 6

    def test_dte_window_uses_cleaning_constants(self):
        """Verify DTE window uses same constants as cleaning."""
        from dataingestion.config import MIN_DTE, MAX_DTE, DTE_WINDOW_MIN, DTE_WINDOW_MAX
        assert DTE_WINDOW_MIN == MIN_DTE
        assert DTE_WINDOW_MAX == MAX_DTE


class TestOrchestratorUsesConfig:
    """Tests verifying orchestrator imports and uses config constants."""

    def test_orchestrator_uses_config(self):
        """Verify orchestrator imports and uses config constants."""
        from dataingestion import orchestrator
        import inspect
        source = inspect.getsource(orchestrator)

        # Check config imports present
        assert "from dataingestion import config as cfg" in source
        assert "cfg.OPT_SEM_LIMIT" in source
        assert "cfg.STK_SEM_LIMIT" in source
        assert "cfg.DTE_WINDOW_MAX" in source

        # Check no hardcoded semaphore values
        assert "Semaphore(4)" not in source
        assert "Semaphore(2)" not in source

        # Check semaphores use config
        assert "OPT_SEM = asyncio.Semaphore(cfg.OPT_SEM_LIMIT)" in source
        assert "STK_SEM = asyncio.Semaphore(cfg.STK_SEM_LIMIT)" in source

    def test_orchestrator_no_hardcoded_thresholds(self):
        """Verify orchestrator has no hardcoded numeric thresholds."""
        from dataingestion import orchestrator
        import inspect
        source = inspect.getsource(orchestrator)

        # Check that defaults are from config, not hardcoded
        # The old hardcoded defaults were: dte_min=7, dte_max=90
        assert "dte_min: int = 7" not in source
        assert "dte_max: int = 90" not in source

        # Check MAX_CHUNK_DAYS is used via cfg
        assert "cfg.OHLC_CACHE_MAX_CHUNKS" in source


class TestEnvironmentOverrides:
    """Test that environment variables can override config values."""

    def test_opt_sem_limit_env_override(self, monkeypatch):
        """Test OPT_SEM_LIMIT can be overridden via environment."""
        monkeypatch.setenv("OPT_SEM_LIMIT", "2")
        # Need to reimport to pick up env var
        import importlib
        from dataingestion import config
        importlib.reload(config)
        assert config.OPT_SEM_LIMIT == 2

    def test_stk_sem_limit_env_override(self, monkeypatch):
        """Test STK_SEM_LIMIT can be overridden via environment."""
        monkeypatch.setenv("STK_SEM_LIMIT", "1")
        import importlib
        from dataingestion import config
        importlib.reload(config)
        assert config.STK_SEM_LIMIT == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])