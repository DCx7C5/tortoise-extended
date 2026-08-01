"""Unit tests for timescale manager helpers with a fake connection.

Exercises the tuple-row and mapping-row result branches of
``HypertableManager.is_hypertable`` and ``CompressionManager.get_compression_stats``
without a live TimescaleDB. Uses monkeypatch to swap the module-level
``connections`` lookup.
"""

import pytest

from tortoise_extended.timescale.compression import CompressionManager
from tortoise_extended.timescale.hypertable import HypertableManager


class FakeConn:
    """Returns a canned ``execute_query`` result."""

    def __init__(self, result: object) -> None:
        self.result = result

    async def execute_query(self, sql: str, *args: object) -> object:
        return self.result


class FakeConnections:
    """Stand-in for the module-level ``connections`` singleton."""

    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def get(self, name: str) -> FakeConn:
        return self.conn


def _patch_connections(
    monkeypatch: pytest.MonkeyPatch, result: object
) -> FakeConn:
    conn = FakeConn(result)
    holder = FakeConnections(conn)
    # Replace the whole module-level `connections` binding — the real proxy
    # delegates through the active TortoiseContext, which we don't have here.
    monkeypatch.setattr("tortoise_extended.timescale.hypertable.connections", holder)
    monkeypatch.setattr("tortoise_extended.timescale.compression.connections", holder)
    return conn


class TestIsHypertable:
    """HypertableManager.is_hypertable() result-shape branches."""

    @pytest.mark.asyncio
    async def test_tuple_row_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, (0, [(True,)]))
        assert await HypertableManager.is_hypertable("events") is True

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, (0, [{"is_hypertable": False}]))
        assert await HypertableManager.is_hypertable("events") is False

    @pytest.mark.asyncio
    async def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, ([], 0))
        assert await HypertableManager.is_hypertable("events") is False


class TestGetCompressionStats:
    """CompressionManager.get_stats() result-shape branches."""

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [{"uncompressed_size": 10, "compressed_size": 5}]),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats["uncompressed_size"] == 10

    @pytest.mark.asyncio
    async def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [(10, 5, 2.0, 4, 2)]),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats == {
            "uncompressed_size": 10,
            "compressed_size": 5,
            "compression_ratio": 2.0,
            "uncompressed_chunks": 4,
            "compressed_chunks": 2,
        }

    @pytest.mark.asyncio
    async def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, ([], 0))
        assert await CompressionManager.get_stats("events") == {}
