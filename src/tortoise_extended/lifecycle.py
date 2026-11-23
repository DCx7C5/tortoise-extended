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

This module owns the extension layer and the :func:`setup` fan-out built on
top of it: extensions → schemas → stream DDL.

Usage (illustrative; needs async context + configured DB)::

    from tortoise_extended.lifecycle import ensure_extensions

    await Tortoise.init(db_url="postgres://...", modules={"models": [...]})
    ensured = await ensure_extensions()
    # ['vector', 'ltree', 'timescaledb', 'pgcrypto', 'pg_trgm', 'uuid-ossp']
"""

import msgspec
from tortoise import Tortoise, connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_ident
from tortoise_extended.exceptions import SetupError
from tortoise_extended.models.sample import SampleMixin

__all__ = ["EXTENSIONS", "SetupReport", "ensure_extensions", "setup"]

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


async def setup(
    models: dict[str, list[str]] | None = None,
    *,
    generate_schemas: bool = False,
) -> SetupReport:
    """Run the whole database setup fan-out in dependency order.

    After a precondition check that ``Tortoise.init()`` has run, the database
    steps execute in this order:

    1. :func:`ensure_extensions` — needs no tables, so it runs first.
    2. ``Tortoise.generate_schemas()`` when *generate_schemas* is true.
    3. ``await model.setup()`` for every non-abstract
       :class:`~tortoise_extended.models.sample.SampleMixin` subclass.

    The order of steps 2 and 3 is load-bearing:
    :meth:`SampleMixin.setup <tortoise_extended.models.sample.SampleMixin.setup>`
    converts an existing table into a hypertable immediately, and its
    docstring requires the table to exist. On a fresh database the schemas
    must therefore be generated first; the reverse order fails.

    Idempotent: every step is ``IF NOT EXISTS``-guarded, so a second call is
    a no-op and the function may run on every application start.

    Args:
        models: Optional app-label restriction for the stream scan, in the
            same ``{app_label: [module, ...]}`` shape as ``Tortoise.init()``'s
            ``modules`` — so a caller can pass its configuration unchanged.
            Only the keys (app labels) are used: models registered under any
            other label are skipped, and the module lists are ignored (nothing
            is forwarded to ``Tortoise.init()``). ``None`` (default) considers
            every registered model.
        generate_schemas: Generate the model schemas before the stream DDL.

    Returns:
        A :class:`SetupReport` with the extension names ensured, the stream
        table names whose ``setup()`` ran, and whether schemas were generated.

    Raises:
        SetupError: when ``Tortoise.init()`` has not run (``Tortoise.apps`` is
            ``None``), or when a ``CREATE EXTENSION`` is refused.

    Note:
        The import-order contract — ``import tortoise_extended`` before
        ``Tortoise.init()`` — is satisfied by importing this module at all
        (``_apply_patches()`` runs at package import) and is covered by
        ``tests/test_import_order.py``; this function deliberately does not
        re-check it.

    Usage (illustrative; needs async context + configured DB)::

        await Tortoise.init(db_url="postgres://...", modules={"models": [...]})
        report = await setup(generate_schemas=True)
        if not report.schemas_generated:
            raise RuntimeError("schemas were not generated")
    """
    apps = Tortoise.apps
    if apps is None:
        raise SetupError(
            "Tortoise is not initialized: call Tortoise.init() before "
            "tortoise_extended.lifecycle.setup()."
        )

    app_labels = set(models) if models is not None else None
    stream_models: list[type[SampleMixin]] = []
    for model in apps.get_models_iterable():
        if model._meta.abstract:
            continue
        if app_labels is not None and model._meta.app not in app_labels:
            continue
        if issubclass(model, SampleMixin):
            stream_models.append(model)

    extensions = await ensure_extensions()

    schemas_generated = False
    if generate_schemas:
        await Tortoise.generate_schemas()
        schemas_generated = True

    streams: list[str] = []
    for stream_model in stream_models:
        await stream_model.setup()
        streams.append(stream_model._meta.db_table)

    return SetupReport(
        extensions=extensions,
        streams=streams,
        schemas_generated=schemas_generated,
    )
