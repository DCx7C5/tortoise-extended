# pyright: reportExplicitAny=false
"""Clean type stubs for ``tortoise.transactions`` (local overlay).

The runtime module imports ``TransactionContext`` under ``TYPE_CHECKING``
from ``tortoise.backends.base.client``, whose ``_in_transaction`` constructor
return is untyped — basedpyright flags ``in_transaction`` as partially
unknown in strict mode. This overlay types the two public helpers precisely.
"""

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

_FT = TypeVar("_FT", bound=Callable[..., Any])

class TransactionContext(Protocol):
    """Async context manager returned by ``in_transaction``.

    Mirrors the runtime ``tortoise.backends.base.client.TransactionContext``
    surface used by library code: it can be awaited for an ``async with``
    block. On entry it yields the underlying connection (typed ``Any`` — the
    runtime context is generic over the connection type ``T_conn``, and the
    concrete ``__aenter__`` implementations return ``TransactionalDBClient``).
    """

    async def __aenter__(self) -> Any: ...
    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

def in_transaction(connection_name: str | None = None) -> TransactionContext: ...
def atomic(connection_name: str | None = None) -> Callable[[_FT], _FT]: ...
