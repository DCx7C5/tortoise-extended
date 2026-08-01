# pyright: reportExplicitAny=false
"""Local stub overlay for ``tortoise.fields.base``.

Self-sufficient replacement for the runtime analysis of
``tortoise/fields/base.py``. The runtime ``Field.__init__`` declares
``validators: list[Validator | Callable]`` with a bare ``Callable`` which
makes basedpyright infer ``(...) -> Unknown``; this overlay types the full
public surface concretely so subclass ``super().__init__`` calls and
``to_db_value`` / ``to_python_value`` overrides type-check cleanly.
"""

from collections.abc import Callable, Mapping
from typing import Any, Generic, TypeVar, overload, override

import tortoise.validators

VALUE = TypeVar("VALUE")


class OnDelete:
    """Enum of ON DELETE behaviours (mirrors ``tortoise.fields.base``)."""


CASCADE: OnDelete = ...
RESTRICT: OnDelete = ...
SET_NULL: OnDelete = ...
SET_DEFAULT: OnDelete = ...
NO_ACTION: OnDelete = ...


class DatabaseDefault:
    """A database-computed default value."""

    def __init__(self, field: Field[Any]) -> None: ...


DB_DEFAULT_NOT_SET: Any = ...


class Field(Generic[VALUE]):
    """Base Tortoise field type (generic over the stored Python value)."""

    field_type: type[Any]
    indexable: bool
    has_db_field: bool
    skip_to_python_if_native: bool
    allows_generated: bool
    function_cast: Callable[..., Any] | None
    SQL_TYPE: str
    GENERATED_SQL: str

    model_field_name: str
    source_field: str
    related_name: str | None
    null: bool
    unique: bool
    db_index: bool | None
    description: str | None
    default: Any
    db_default: Any
    pk: bool

    @property
    def constraints(self) -> Mapping[str, object]:
        """DB-level column constraints (covariant Mapping so subclass
        overrides returning ``dict[str, int]`` remain valid)."""

    def __init__(
        self,
        source_field: str | None = None,
        generated: bool = False,
        primary_key: bool | None = None,
        null: bool = False,
        default: Any = None,
        db_default: Any = DB_DEFAULT_NOT_SET,
        unique: bool = False,
        db_index: bool | None = None,
        description: str | None = None,
        model: Any = None,
        validators: list[tortoise.validators.Validator | Callable[..., Any]] | None = None,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __get__(self, instance: None, owner: type[Any]) -> Field[VALUE]: ...
    @overload
    def __get__(self, instance: Any, owner: type[Any]) -> VALUE: ...
    def __get__(self, instance: Any | None, owner: type[Any]) -> Field[VALUE] | VALUE: ...

    def __set__(self, instance: Any, value: VALUE) -> None: ...

    def to_db_value(self, value: Any, instance: Any) -> Any: ...

    def to_python_value(self, value: Any) -> Any: ...

    def validate(self, value: Any) -> None: ...

    @override
    def __repr__(self) -> str: ...
