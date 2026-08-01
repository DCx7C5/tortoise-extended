"""Clean type stubs for ``tortoise.backends.base.client`` (local overlay).

The runtime types ``execute_query`` with a bare ``dict``
(``dict[Unknown, Unknown]``), which poisons every caller under strict mode.
This overlay declares fully-known signatures; callers narrow the
``object`` values with ``isinstance``.
"""

from collections.abc import Sequence


class BaseDBAsyncClient:
    """Minimal client surface used by ``tortoise_extended``."""

    async def execute_query(
        self,
        query: str,
        values: list[str] | None = None,
    ) -> tuple[int, Sequence[dict[str, object]]]: ...

    async def execute_query_dict(
        self,
        query: str,
        values: list[object] | None = None,
    ) -> list[dict[str, object]]: ...

    async def execute_script(self, query: str) -> None: ...
