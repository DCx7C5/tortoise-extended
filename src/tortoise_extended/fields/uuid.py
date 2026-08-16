"""PostgreSQL UUID fields with auto-generation defaults.

Provides UUID4Field and UUID7Field, which subclass tortoise's ``UUIDField``
and default to ``uuid.uuid4`` / ``uuid.uuid7`` (Python 3.14) when no
``default`` is given. The PostgreSQL SQL type ``UUID`` is inherited via
tortoise's ``UUIDField._db_postgres``.

Usage::

    from tortoise import models
    from tortoise_extended.fields.uuid import UUID4Field, UUID7Field

    class Event(models.Model):
        id = UUID4Field(pk=True)
        trace_id = UUID7Field()

        class Meta:
            table = "events"

    # Both fields auto-generate values on create when no default is set
    event = await Event.create()
"""

from collections.abc import Callable
from typing import Unpack
from uuid import UUID, uuid4, uuid7

from tortoise.fields import UUIDField

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class UUID4Field(UUIDField[UUID]):
    """PostgreSQL ``uuid`` column auto-populated with a random UUID4.

    Inherits tortoise's ``UUIDField`` SQL types: ``UUID`` on PostgreSQL
    (via ``_db_postgres``) and ``CHAR(36)`` on SQLite. When no ``default``
    is supplied, ``uuid4`` is injected — this overrides tortoise's
    pk-only default injection, so non-primary-key columns also
    auto-generate values.

    :param null: Allow NULL values.
    :param default: Default UUID value or callable (defaults to ``uuid4``).
    :param description: Column comment.

    Usage::

        class Event(Model):
            id = UUID4Field(pk=True)

    .. note::
        ``null=True`` intentionally types as ``Field[UUID]`` rather than
        ``Field[UUID | None]``. The stub ``UUIDField`` generic is
        value-restricted to ``UUID`` / ``UUID | None`` and invariant, so a
        subclass parameterized with its own ``TypeVar`` cannot substitute
        it (pyright: "Type parameter ``T_UUID@UUIDField`` is invariant, but
        ... is not the same as ``UUID``"). Re-parameterizing ``UUID4Field``
        generically is therefore not feasible without changing the shared
        tortoise stubs; the base ``UUIDField`` overloads already narrow
        ``null=True`` for direct ``UUIDField`` use.
    """

    def __init__(
        self,
        *,
        null: bool = False,
        default: FieldDefaultValue | UUID | Callable[[], UUID] | None = None,
        description: str | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None:
        if default is None:
            default = uuid4
        super().__init__(
            null=null, default=default, description=description, **kwargs
        )


class UUID7Field(UUIDField[UUID]):
    """PostgreSQL ``uuid`` column auto-populated with a time-ordered UUID7.

    Inherits tortoise's ``UUIDField`` SQL types: ``UUID`` on PostgreSQL
    (via ``_db_postgres``) and ``CHAR(36)`` on SQLite. UUID7 values embed a
    Unix timestamp, which keeps index locality good for insert-heavy
    tables. When no ``default`` is supplied, ``uuid7`` is injected.

    :param null: Allow NULL values.
    :param default: Default UUID value or callable (defaults to ``uuid7``).
    :param description: Column comment.

    Usage::

        class Event(Model):
            id = UUID7Field(pk=True)

    .. note::
        See ``UUID4Field`` — ``null=True`` cannot be re-parameterized to
        ``Field[UUID | None]`` without changing the shared tortoise stubs.
    """

    def __init__(
        self,
        *,
        null: bool = False,
        default: FieldDefaultValue | UUID | Callable[[], UUID] | None = None,
        description: str | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None:
        if default is None:
            default = uuid7
        super().__init__(
            null=null, default=default, description=description, **kwargs
        )
