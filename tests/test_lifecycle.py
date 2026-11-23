"""Integration tests for the database lifecycle extension layer.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 with
pgvector/TimescaleDB/pgcrypto/pg_trgm/uuid-ossp on ``127.0.0.1:5434``.

Run with: uv run pytest tests/test_lifecycle.py -v
"""

import os
import socket
from collections.abc import AsyncGenerator

import pytest
from tortoise import Tortoise, fields
from tortoise.context import _current_context
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.exceptions import SetupError
from tortoise_extended.lifecycle import (
    EXTENSIONS,
    SetupReport,
    ensure_extensions,
    setup,
)
from tortoise_extended.models.sample import SampleMixin
from tortoise_extended.timescale.hypertable import HypertableManager

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


#: Table ``LifecycleStream.setup()`` converts into a hypertable.
STREAM_TABLE = "test_lifecycle_stream"


class LifecycleStream(SampleMixin):
    """Stream table whose schema only ``generate_schemas`` can create.

    ``created_at`` is the primary key and the space dimension is disabled:
    Tortoise can express only a single-column primary key, while TimescaleDB
    requires every unique index to cover all partitioning columns. The table
    therefore has to be created by ``Tortoise.generate_schemas()`` before
    :meth:`SampleMixin.setup` converts it into a hypertable — which is exactly
    the ordering :func:`~tortoise_extended.lifecycle.setup` must guarantee.
    """

    number_partitions = None

    created_at = fields.DatetimeField(primary_key=True, use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()

    class Meta:
        table = STREAM_TABLE


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


async def _drop_stream_table() -> None:
    """Drop the stream table (and its hypertable chunks) if present."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"DROP TABLE IF EXISTS {STREAM_TABLE} CASCADE")


@pytest.fixture(autouse=True)
async def _fresh_stream_table() -> AsyncGenerator[None, None]:
    """Start and end every test with no stream table in the database."""
    await _drop_stream_table()
    yield
    await _drop_stream_table()


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


# ---------------------------------------------------------------------------
# setup — the full fan-out
# ---------------------------------------------------------------------------


class TestSetup:
    """``setup`` fan-out against the real PostgreSQL server."""

    @pytest.mark.asyncio
    async def test_generate_schemas_precedes_stream_setup(self) -> None:
        """On a fresh database the hypertable exists only if schemas ran first.

        ``LifecycleStream``'s table can only be created by
        ``Tortoise.generate_schemas()``; ``SampleMixin.setup()`` requires it to
        exist already. Reversing the two steps makes this test fail on a
        missing table — that is the ordering proof.
        """
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            "SELECT to_regclass('test_lifecycle_stream') AS oid"
        )
        assert result[1][0]["oid"] is None, "fixture must start from a fresh schema"

        report = await setup(generate_schemas=True)

        assert isinstance(report, SetupReport)
        assert report.extensions == EXPECTED_EXTENSIONS
        assert report.schemas_generated is True
        assert report.streams == [STREAM_TABLE]
        assert await HypertableManager.is_hypertable(STREAM_TABLE)

    @pytest.mark.asyncio
    async def test_second_call_is_a_noop(self) -> None:
        """Every step is IF NOT EXISTS-guarded, so a repeat call changes nothing."""
        first = await setup(generate_schemas=True)
        second = await setup(generate_schemas=True)
        assert second == first
        assert await HypertableManager.is_hypertable(STREAM_TABLE)

    @pytest.mark.asyncio
    async def test_raises_setup_error_before_tortoise_init(self) -> None:
        """Without ``Tortoise.init()`` the fan-out refuses with ``SetupError``.

        The context is detached for the duration of the call (and restored in
        ``finally``) so ``Tortoise.apps`` is ``None`` even though the module
        fixture has initialized Tortoise for the other tests.
        """
        token = _current_context.set(None)
        try:
            with pytest.raises(SetupError, match=r"Tortoise\.init\(\)"):
                await setup()
        finally:
            _current_context.reset(token)

    @pytest.mark.asyncio
    async def test_models_restricts_the_stream_scan(self) -> None:
        """An unrelated app label leaves the stream table untouched."""
        report = await setup(models={"not_registered": ["nowhere.models"]})

        assert report.extensions == EXPECTED_EXTENSIONS
        assert report.schemas_generated is False
        assert report.streams == []
        assert not await HypertableManager.is_hypertable(STREAM_TABLE)

    @pytest.mark.asyncio
    async def test_models_naming_the_app_keeps_its_streams(self) -> None:
        """Naming the registering app label includes its stream models."""
        report = await setup(
            models={"models": ["tests.test_lifecycle"]},
            generate_schemas=True,
        )

        assert report.streams == [STREAM_TABLE]
        assert await HypertableManager.is_hypertable(STREAM_TABLE)
