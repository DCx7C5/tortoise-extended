"""Shared type helpers for ``tortoise-extended``.

``LibraryAny`` is the ONLY sanctioned spelling of ``Any`` in this package.
It is reserved for signatures that MUST mirror an upstream library's
``Any``-typed parameter or return — e.g. overriding
``tortoise.fields.Field.to_db_value`` / ``to_python_value`` / ``validate``,
which the base class declares as ``(value: Any) -> Any``.

Using a bare ``Any`` anywhere else is a policy violation: prefer concrete
types, unions, ``object`` + ``cast``, or ``TypeVar``.
"""

from typing import Any, Protocol, TypeAlias

LibraryAny: TypeAlias = Any  # pyright: ignore[reportExplicitAny]


class Deconstructable(Protocol):
    """Protocol for Tortoise migration operations that implement ``deconstruct()``."""

    def deconstruct(self) -> tuple[str, tuple[()], dict[str, Any]]: ...  # pyright: ignore[reportExplicitAny]
