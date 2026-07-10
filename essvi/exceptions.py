"""eSSVI engine exceptions."""

from __future__ import annotations

from typing import Sequence


class EssviError(Exception):
    """Base exception for eSSVI engine."""


class DataNotFoundError(EssviError):
    def __init__(self, timestamp) -> None:
        self.timestamp = timestamp
        super().__init__(f"No data found for timestamp {timestamp}")


class MissingColumnError(EssviError):
    def __init__(self, missing_columns: Sequence[str]) -> None:
        self.missing_columns = list(missing_columns)
        super().__init__(f"Missing required columns: {sorted(self.missing_columns)}")


class AnchorError(EssviError):
    """Raised when anchor extraction cannot find a valid strike."""
