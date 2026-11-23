"""Integration tests for the database lifecycle extension layer.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 with
pgvector/TimescaleDB/pgcrypto/pg_trgm/uuid-ossp on ``127.0.0.1:5434``.

Run with: uv run pytest tests/test_lifecycle.py -v
"""

import os
import socket

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.lifecycle import EXTENSIONS, ensure_extensions

# ---------------------------------------------------------------------------
# Config — skip entire module if PG is not available
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)

#: The six extensions ``ensure_extensions`` must ensure, in creation order.
EXPECTED_EXTENSIONS = [
    "vector",
    "ltree",
    "timescaledb",
    "pgcrypto",
    "pg_trgm",
    "uuid-ossp",
]


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5434",
)


# ---------------------------------------------------------------------------
# Test model — Tortoise.init() needs a non-empty models module
# ---------------------------------------------------------------------------


class LifecycleProbe(Model):
    """Minimal model so Tortoise.init has a non-empty module."""

    id = fields.IntField(primary_key=True)
    label = fields.CharField(max_length=32)

    class Meta:
        table = "test_lifecycle_probe"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_lifecycle"]},
    )
    yield
    await Tortoise.close_connections()


# ---------------------------------------------------------------------------
# ensure_extensions
# ---------------------------------------------------------------------------


class TestEnsureExtensions:
    """``ensure_extensions`` against the real PostgreSQL server."""

    def test_extension_list_is_the_expected_six(self) -> None:
        """The module constant is exactly the six documented extensions."""
        assert list(EXTENSIONS) == EXPECTED_EXTENSIONS

    @pytest.mark.asyncio
    async def test_returns_the_six_names_in_order(self) -> None:
        names = await ensure_extensions()
        assert names == EXPECTED_EXTENSIONS

    @pytest.mark.asyncio
    async def test_idempotent_second_call_succeeds(self) -> None:
        """Running it twice must not error — the second run is all no-ops."""
        first = await ensure_extensions()
        second = await ensure_extensions()
        assert first == EXPECTED_EXTENSIONS
        assert second == EXPECTED_EXTENSIONS

    @pytest.mark.asyncio
    async def test_all_six_installed_in_pg_extension(self) -> None:
        """Every ensured extension is present in the server catalog afterwards."""
        await ensure_extensions()
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query("SELECT extname FROM pg_extension")
        installed = {row["extname"] for row in result[1]}
        missing = [name for name in EXPECTED_EXTENSIONS if name not in installed]
        assert not missing, f"extensions not installed: {missing!r}"
