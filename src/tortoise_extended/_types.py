"""Shared type helpers for ``tortoise-extended``.

``LibraryAny`` is the ONLY sanctioned spelling of ``Any`` in this package.
It is reserved for signatures that MUST mirror an upstream library's
``Any``-typed parameter or return — e.g. overriding
``tortoise.fields.Field.to_db_value`` / ``to_python_value`` / ``validate``,
which the base class declares as ``(value: Any) -> Any``.

Using a bare ``Any`` anywhere else is a policy violation: prefer concrete
types, unions, ``object`` + ``cast``, or ``TypeVar``.

NOTE (verified 2026-07-31): ``reportExplicitAny`` fires at EVERY *use site* of
``LibraryAny``, not just the alias definition — the ``# pyright: ignore[...]``
on the alias line does not propagate. Each annotation using ``LibraryAny`` must
carry its own trailing ``# pyright: ignore[reportExplicitAny]`` comment
(see ``cache/base.py``, ``fields/vector_field.py``).

The module also hosts the shared ``ParamSpec`` / ``TypeVar`` instances and
``TypeAlias``es that warning-cleanup tasks import instead of re-declaring
them locally (see the FOUNDATION backlog).

Protocols in this module describe *partial* surfaces of upstream Tortoise
classes. They exist so code can call private upstream methods (e.g. ``_run_sql``)
without ``reportPrivateUsage`` — the patched object is ``cast()`` to the
protocol instead of annotated with the upstream class. Signatures mirror the
installed ``tortoise`` runtime exactly (``tortoise/schema_quoting.py``,
``tortoise/backends/base/schema_generator.py``,
``tortoise/migrations/schema_editor/base.py``, ``tortoise/migrations/writer.py``).

NOTE (verified 2026-07-31): ``cast()`` to a Protocol does NOT silence
``reportPrivateUsage`` — pyright checks protected-member access against the
declaring class, so private-method calls on patched objects must go through
``getattr(obj, "_name")`` instead (see ``migrations/operations.py``).
"""

from typing import Any, ParamSpec, Protocol, TypeAlias, TypeVar

LibraryAny: TypeAlias = Any  # pyright: ignore[reportExplicitAny]

P = ParamSpec("P")
"""Bare ``ParamSpec`` for decorator signatures that forward arbitrary callables."""

R = TypeVar("R")
"""Bare ``TypeVar`` for decorator return types that preserve the wrapped callable."""

T = TypeVar("T")
"""Bare ``TypeVar`` for generic helpers (e.g. ``Model`` subclasses)."""

ModelKwargs: TypeAlias = dict[str, LibraryAny]  # pyright: ignore[reportExplicitAny]
"""Keyword arguments accepted by ``Model.create`` / ``Model.update`` mirrors."""

SerializedRecord: TypeAlias = dict[str, LibraryAny]  # pyright: ignore[reportExplicitAny]
"""A serialized model record as stored in the cache backend."""

RowMapping: TypeAlias = dict[str, LibraryAny]  # pyright: ignore[reportExplicitAny]
"""A raw SQL result row exposed by the ORM cursor / ``QuerySet.values``."""


class Deconstructable(Protocol):
    """Protocol for Tortoise migration operations that implement ``deconstruct()``."""

    def deconstruct(self) -> tuple[str, tuple[()], dict[str, Any]]: ...  # pyright: ignore[reportExplicitAny]
