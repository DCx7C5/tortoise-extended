"""Database lifecycle: the PostgreSQL extensions this package depends on.

Every feature in ``tortoise-extended`` sits on one or more PostgreSQL
extensions:

- ``vector`` — pgvector, for :class:`~tortoise_extended.fields.vector.VectorField`
  and the distance filters
- ``ltree`` — for :class:`~tortoise_extended.fields.ltree.LTreeField` and the
  hierarchy models
- ``timescaledb`` — hypertables, continuous aggregates, compression and
  retention policies
- ``pgcrypto`` — the ``digest`` / ``hmac`` query helpers
- ``pg_trgm`` — trigram similarity, used alongside full-text search
- ``uuid-ossp`` — UUID generation functions some deployments rely on

:func:`ensure_extensions` creates them with ``CREATE EXTENSION IF NOT
EXISTS``, so it is safe on an already provisioned database and safe to run
twice. A refusal (typically missing privilege) surfaces as
:class:`~tortoise_extended.exceptions.SetupError` naming the failing
extension — it is never swallowed, because every later step of the setup
depends on the extensions being present.

This module is the extension layer only; the ``setup()`` fan-out
(extensions → streams → schemas) is built on top of it.

Usage (illustrative; needs async context + configured DB)::

    from tortoise_extended.lifecycle import ensure_extensions

    await Tortoise.init(db_url="postgres://...", modules={"models": [...]})
    ensured = await ensure_extensions()
    # ['vector', 'ltree', 'timescaledb', 'pgcrypto', 'pg_trgm', 'uuid-ossp']
"""

import msgspec
from tortoise import connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_ident
from tortoise_extended.exceptions import SetupError

__all__ = ["EXTENSIONS", "SetupReport", "ensure_extensions"]

EXTENSIONS: tuple[str, ...] = (
    "vector",
    "ltree",
    "timescaledb",
    "pgcrypto",
    "pg_trgm",
    "uuid-ossp",
)
"""Extensions :func:`ensure_extensions` creates, in creation order."""


class SetupReport(msgspec.Struct):
    """Result of the database setup fan-out.

    Attributes:
        extensions: Extension names ensured, in creation order.
        streams: Stream tables registered by the setup.
        schemas_generated: Whether Tortoise generated the model schemas.
    """

    extensions: list[str]
    streams: list[str]
    schemas_generated: bool


async def ensure_extensions(conn: BaseDBAsyncClient | None = None) -> list[str]:
    """Create the PostgreSQL extensions this package depends on.

    Runs ``CREATE EXTENSION IF NOT EXISTS`` for each entry of
    :data:`EXTENSIONS` in order. Idempotent: on a database where the
    extensions are already installed every statement is a no-op notice, so
    callers may run it on every application start.

    Args:
        conn: Database connection to use (default: the ``"default"``
            connection). ``Tortoise.init()`` must have run before the default
            connection is requested.

    Returns:
        The extension names ensured, in creation order — the same six names
        on every call.

    Raises:
        SetupError: when the server refuses a ``CREATE EXTENSION`` — most
            commonly insufficient privilege (the statement needs superuser
            for non-trusted extensions such as ``vector`` and
            ``timescaledb``). The message names the failing extension; the
            original server error is chained as the cause.

    Usage (illustrative; needs async context + configured DB)::

        ensured = await ensure_extensions()
        if "vector" not in ensured:
            raise RuntimeError("pgvector is unavailable")
    """
    client = conn if conn is not None else connections.get("default")

    for name in EXTENSIONS:
        try:
            _ = await client.execute_query(
                f"CREATE EXTENSION IF NOT EXISTS {quote_ident(name)}"
            )
        except Exception as exc:
            msg = (
                f"Failed to create PostgreSQL extension {name!r}: {exc}. "
                "If the server refused the statement, the usual cause is "
                "missing privilege: CREATE EXTENSION requires superuser for a "
                "non-trusted extension (vector, timescaledb) and database "
                "CREATE privilege for a trusted one. Grant the privilege, or "
                "have an administrator install the extension before setup "
                "runs."
            )
            raise SetupError(msg) from exc

    return list(EXTENSIONS)
