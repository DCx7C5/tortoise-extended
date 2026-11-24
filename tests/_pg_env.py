"""Connection details for the ``postgres-ext`` dev container.

The database-backed modules dial ``TORTOISE_TEST_DB`` when it is set and the dev
container (``postgres://postgres:postgres@localhost:5434/tortoise_test``) when it
is not.  Probing a hardcoded ``localhost:5434`` while the DSN honours the
variable meant a run pointed somewhere else silently skipped every
database-backed module — so :func:`pg_available` probes the host and port of the
DSN that is actually going to be used.

The environment is read at call time (not import time), so the value may be
exported after collection — same contract as :mod:`tests._redis_env`.

An unreachable target SKIPS the database-backed modules rather than failing them
(AGENTS.md §Testing).  That is why :func:`pg_skip_reason` names the host:port
that was actually tried: a mis-pointed ``TORTOISE_TEST_DB`` shows up in the skip
summary instead of looking like a missing container.

Run the suite against the dev container::

    docker compose -f docker-compose.dev.yml up -d
    uv run pytest tests/ -q
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlsplit

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5434
DEFAULT_URL = "postgres://postgres:postgres@localhost:5434/tortoise_test"

PROBE_TIMEOUT = 2.0


def database_url() -> str:
    """The PostgreSQL DSN: ``TORTOISE_TEST_DB`` when set, else the dev default."""
    return os.environ.get("TORTOISE_TEST_DB") or DEFAULT_URL


def pg_host_port() -> tuple[str, int]:
    """``(host, port)`` parsed from :func:`database_url`, with defaults.

    :raises ValueError: When the DSN's authority cannot be parsed — a
        non-numeric port or a malformed IPv6 literal. Failing here names
        ``TORTOISE_TEST_DB`` instead of silently probing the default port,
        which is the silent-wrong-target bug this module exists to prevent.
    """
    url = database_url()
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise ValueError(
            f"TORTOISE_TEST_DB is not a usable DSN: {url!r} ({exc}) — "
            f"unset it to use {DEFAULT_URL}"
        ) from exc
    return host or DEFAULT_HOST, port or DEFAULT_PORT


def pg_available() -> bool:
    """``True`` when the resolved host:port accepts a TCP connection."""
    host, port = pg_host_port()
    try:
        probe = socket.create_connection((host, port), timeout=PROBE_TIMEOUT)
    except OSError:
        return False
    probe.close()
    return True


def pg_skip_reason() -> str | None:
    """``None`` when reachable, else why not — naming the host:port tried."""
    if pg_available():
        return None
    host, port = pg_host_port()
    return (
        f"PostgreSQL not available on {host}:{port} "
        "(docker compose -f docker-compose.dev.yml up -d)"
    )
