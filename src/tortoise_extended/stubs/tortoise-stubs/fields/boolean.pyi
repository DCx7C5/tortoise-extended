# pyright: reportExplicitAny=false
"""Clean type stubs for ``tortoise.fields.boolean`` (local overlay).

The runtime class ``tortoise.fields.data.BooleanField`` (re-exported as
``tortoise.fields.BooleanField``) is generic over an unbound ``TypeVar``,
which makes bare usage resolve to ``Unknown`` member types under strict
mode. This overlay types the class concretely: the ``null`` literal
overloads narrow the element type to ``bool`` / ``bool | None`` so model
declarations like ``fields.BooleanField(default=False)`` are fully known.
"""

from typing import ClassVar, Literal, TypeVar, overload

from tortoise.fields.base import Field

T_BOOL = TypeVar("T_BOOL")


class BooleanField(Field[T_BOOL]):
    """Boolean Tortoise field (stored as 0/1, exposed as ``bool``)."""

    field_type: ClassVar[type] = bool
    SQL_TYPE: ClassVar[str] = "BOOL"

    class _db_sqlite:
        SQL_TYPE: ClassVar[str] = "INT"

    @overload
    def __init__(
        self: BooleanField[bool], *, null: Literal[False] = False, **kwargs: object
    ) -> None: ...

    @overload
    def __init__(
        self: BooleanField[bool | None], *, null: Literal[True], **kwargs: object
    ) -> None: ...

    def __init__(self, **kwargs: object) -> None: ...
