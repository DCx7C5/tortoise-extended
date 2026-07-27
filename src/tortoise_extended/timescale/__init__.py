"""TimescaleDB integration module.

Provides:
- HypertableManager: Convert tables to hypertables
- CompressionManager: Enable and manage compression
- RetentionPolicy: Automatic data retention
- ContinuousAggregateManager: Manage continuous aggregates

Usage::

    from tortoise_extended.timescale import (
        HypertableManager,
        CompressionManager,
        RetentionPolicy,
        ContinuousAggregateManager,
    )

    # Convert table to hypertable
    await HypertableManager.create_hypertable(
        "events",
        time_column="created_at",
        chunk_time_interval="7 days",
    )

    # Enable compression
    await CompressionManager.enable_compression(
        "events",
        compress_after="7 days",
    )

    # Set retention policy
    await RetentionPolicy.set_retention(
        "events",
        drop_after="90 days",
    )

    # Create continuous aggregate
    await ContinuousAggregateManager.create(
        "daily_events",
        "events",
        "SELECT time_bucket('1 day', created_at) AS bucket, "
        "COUNT(*) AS count FROM events GROUP BY bucket",
    )
"""

from tortoise_extended.timescale.compression import CompressionManager
from tortoise_extended.timescale.continuous_aggregate import ContinuousAggregateManager
from tortoise_extended.timescale.hypertable import HypertableManager
from tortoise_extended.timescale.retention import RetentionPolicy

__all__ = [
    "CompressionManager",
    "ContinuousAggregateManager",
    "HypertableManager",
    "RetentionPolicy",
]
