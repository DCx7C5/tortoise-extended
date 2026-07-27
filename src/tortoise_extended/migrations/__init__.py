"""Migration operations for TimescaleDB."""

from tortoise_extended.migrations.operations import (
    CreateContinuousAggregate,
    CreateHypertable,
)

__all__ = ["CreateContinuousAggregate", "CreateHypertable"]
