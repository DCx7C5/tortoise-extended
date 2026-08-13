"""Clean type stubs for ``tortoise.backends.base.client`` (local overlay).

The runtime types ``execute_query`` with a bare ``dict``
(``dict[Unknown, Unknown]``), which poisons every caller under strict mode.
This overlay declares fully-known signatures; callers narrow the
``object`` values with ``isinstance``.
"""

from collections.abc import Sequence
from typing import AsyncContextManager, Protocol

from tortoise_extended._types import RowMapping, RowValue


class RawConnection(Protocol):
    """The raw driver connection yielded by ``acquire_connection``.

    Only the surface used by ``tortoise_extended`` is declared (asyncpg's
    ``copy_records_to_table`` for COPY ingestion).
    """

    async def copy_records_to_table(
        self,
        table_name: str,
        *,
        columns: list[str],
        records: list[list[RowValue]],
        timeout: float | None = None,
    ) -> str: ...


class BaseDBAsyncClient:
    """Minimal client surface used by ``tortoise_extended``."""

    async def execute_query(
        self,
        query: str,
        values: list[str] | None = None,
    ) -> tuple[int, Sequence[RowMapping]]: ...

    async def execute_query_dict(
        self,
        query: str,
        values: list[RowValue] | None = None,
    ) -> list[RowMapping]: ...

    async def execute_script(self, query: str) -> None: ...

    def acquire_connection(self) -> AsyncContextManager[RawConnection]: ...
