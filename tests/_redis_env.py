"""Connection details for the live ``redis-ext`` dev container.

The container requires AUTH — ``docker/redis-ext/entrypoint.sh`` refuses to
start without ``REDIS_PASSWORD`` — so the live cache tests must know the secret
*and* put it in a URL that survives it: ``@``, ``:``, ``/`` and ``%`` inside a
password have to be percent-encoded, which :func:`redis_url` does.

The environment is read at call time (not import time), so the value may be
exported after collection, and ``REDIS_HOST`` / ``REDIS_PORT`` (documented in
``.env.example``) let the same tests point at a throwaway container.

Run the suite with the dev secrets::

    uv run --env-file .env --extra redis pytest tests/ -q
"""

from __future__ import annotations

import os
from urllib.parse import quote

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6381


def redis_host() -> str:
    """Host of the dev container (``REDIS_HOST``, default ``localhost``)."""
    return os.environ.get("REDIS_HOST") or DEFAULT_HOST


def redis_port() -> int:
    """Published port of the dev container (``REDIS_PORT``, default ``6381``)."""
    raw = os.environ.get("REDIS_PORT", "")
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def redis_password() -> str:
    """The AUTH secret; empty when ``REDIS_PASSWORD`` is unset."""
    return os.environ.get("REDIS_PASSWORD", "")


def redis_url(db: int = 0) -> str:
    """Build a Redis URL, percent-encoding the password when one is set."""
    password = redis_password()
    auth = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{redis_host()}:{redis_port()}/{db}"


def redis_skip_reason() -> str | None:
    """``None`` when a container is reachable and accepts our AUTH, else why not.

    A bare ``socket.connect`` is not enough any more: an unauthenticated probe
    would pass against a protected server and then fail every command with
    ``NOAUTH``.  The probe authenticates and pings.
    """
    if not redis_password():
        return (
            "REDIS_PASSWORD is not set — redis-ext requires AUTH "
            "(docker compose -f docker-compose.dev.yml up -d, then run the "
            "suite with `uv run --env-file .env --extra redis pytest`)"
        )
    try:
        import redis
    except ImportError:
        return "the 'redis' extra is not installed (uv run --extra redis pytest)"

    client = redis.Redis.from_url(
        redis_url(), socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()
    except Exception:  # noqa: BLE001 — any failure means "not testable here"
        return (
            f"Redis not reachable or AUTH rejected on "
            f"{redis_host()}:{redis_port()} "
            "(docker compose -f docker-compose.dev.yml up -d)"
        )
    finally:
        client.connection_pool.disconnect()
    return None
