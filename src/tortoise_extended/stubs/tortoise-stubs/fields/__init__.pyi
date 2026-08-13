# pyright: reportExplicitAny=false
"""Local stub overlay for ``tortoise.fields``.

Overrides the upstream ``tortoise-orm-stubs`` to replace bare ``Callable``
with ``Callable[..., Any]`` and bare ``Validator`` references — the upstream
forms cause basedpyright to infer ``(...) -> Unknown`` which triggers
``reportUnknownVariableType`` on every field that accepts ``validators``.
"""

import datetime
import decimal
import uuid
from collections.abc import Callable
from typing import Any, Literal, overload

import tortoise.validators
from tortoise.fields.base import (
    CASCADE,
    NO_ACTION,
    RESTRICT,
    SET_DEFAULT,
    SET_NULL,
    Field,
    OnDelete,
)
from tortoise.fields.data import CharEnumType, IntEnumType
from tortoise.fields.boolean import BooleanField as BooleanField
from tortoise.fields.relational import (
    BackwardFKRelation,
    BackwardOneToOneRelation,
    ForeignKeyField,
    ForeignKeyNullableRelation,
    ForeignKeyRelation,
    ManyToManyField,
    ManyToManyRelation,
    OneToOneField,
    OneToOneNullableRelation,
    OneToOneRelation,
    ReverseRelation,
)
from tortoise.models import Model
from tortoise_extended.fields.vector_field import VectorField

__all__ = [
    "CASCADE",
    "NO_ACTION",
    "RESTRICT",
    "SET_DEFAULT",
    "SET_NULL",
    "BackwardFKRelation",
    "BackwardOneToOneRelation",
    "BigIntField",
    "BinaryField",
    "BooleanField",
    "CharEnumField",
    "CharEnumType",
    "CharField",
    "DateField",
    "DatetimeField",
    "DecimalField",
    "Field",
    "FloatField",
    "ForeignKeyField",
    "ForeignKeyNullableRelation",
    "ForeignKeyRelation",
    "IntEnumField",
    "IntEnumType",
    "IntField",
    "JSONField",
    "ManyToManyField",
    "ManyToManyRelation",
    "OnDelete",
    "OneToOneField",
    "OneToOneNullableRelation",
    "OneToOneRelation",
    "ReverseRelation",
    "SmallIntField",
    "TextField",
    "TimeDeltaField",
    "TimeField",
    "UUIDField",
    "VectorField",
]

type _ValidatorType = list[tortoise.validators.Validator | Callable[..., Any]]


# ── BigIntField ─────────────────────────────────────────────────────────

class BigIntField(Field[int | None]):
    """Big integer field — a runtime CLASS (``isinstance``-safe).

    Typed nullable because ``null=True`` instances (e.g. a nullable
    ``parent_id``) must keep ``is None`` comparisons type-checking; the
    class-level alternative ``Field[int]`` would flag those as unnecessary.
    """


# ── BinaryField ─────────────────────────────────────────────────────────

@overload
def BinaryField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[False] = False,
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[bytes]: ...

@overload
def BinaryField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[True],
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[bytes | None]: ...


# ── CharEnumField ───────────────────────────────────────────────────────

@overload
def CharEnumField(
    enum_type: type[CharEnumType],
    description: str | None = None,
    max_length: int = 0,
    *,
    null: Literal[False] = False,
    **kwargs: Any,
) -> Field[CharEnumType]:
    """Char Enum Field"""

@overload
def CharEnumField(
    enum_type: type[CharEnumType],
    description: str | None = None,
    max_length: int = 0,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> Field[CharEnumType | None]: ...


# ── CharField ───────────────────────────────────────────────────────────

@overload
def CharField(max_length: int, *, null: Literal[False] = False, **kwargs: Any) -> Field[str]:
    """Character field."""

@overload
def CharField(max_length: int, *, null: Literal[True], **kwargs: Any) -> Field[str | None]: ...


# ── DateField ───────────────────────────────────────────────────────────

@overload
def DateField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[False] = False,
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[datetime.date]: ...

@overload
def DateField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[True],
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[datetime.date | None]: ...


# ── DatetimeField ───────────────────────────────────────────────────────

@overload
def DatetimeField(
    auto_now: bool = False,
    auto_now_add: bool = False,
    *,
    null: Literal[False] = False,
    **kwargs: Any,
) -> Field[datetime.datetime]: ...

@overload
def DatetimeField(
    auto_now: bool = False,
    auto_now_add: bool = False,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> Field[datetime.datetime | None]: ...


# ── DecimalField ────────────────────────────────────────────────────────

@overload
def DecimalField(
    max_digits: int, decimal_places: int, *, null: Literal[False] = False, **kwargs: Any
) -> Field[decimal.Decimal]: ...

@overload
def DecimalField(
    max_digits: int, decimal_places: int, *, null: Literal[True], **kwargs: Any
) -> Field[decimal.Decimal | None]: ...


# ── FloatField ──────────────────────────────────────────────────────────

@overload
def FloatField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[False] = False,
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[float]: ...

@overload
def FloatField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[True],
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[float | None]: ...


# ── IntEnumField ────────────────────────────────────────────────────────

@overload
def IntEnumField(
    enum_type: type[IntEnumType],
    description: str | None = None,
    *,
    null: Literal[False] = False,
    **kwargs: Any,
) -> Field[IntEnumType]: ...

@overload
def IntEnumField(
    enum_type: type[IntEnumType],
    description: str | None = None,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> Field[IntEnumType | None]: ...


# ── IntField ────────────────────────────────────────────────────────────

class IntField(Field[int]):
    """Integer field — a runtime CLASS (``isinstance``-safe)."""


# ── JSONField ───────────────────────────────────────────────────────────

@overload
def JSONField(
    encoder: Callable[[Any], str] = ...,
    decoder: Callable[[str | bytes], Any] = ...,
    *,
    null: Literal[False] = False,
    **kwargs: Any,
) -> Field[Any]: ...

@overload
def JSONField(
    encoder: Callable[[Any], str] = ...,
    decoder: Callable[[str | bytes], Any] = ...,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> Field[Any | None]: ...


# ── SmallIntField ───────────────────────────────────────────────────────

class SmallIntField(Field[int]):
    """Small integer field — a runtime CLASS (``isinstance``-safe)."""


# ── TextField ───────────────────────────────────────────────────────────

@overload
def TextField(
    pk: bool = False,
    unique: bool = False,
    index: bool = False,
    *,
    null: Literal[False] = False,
    **kwargs: Any,
) -> Field[str]: ...

@overload
def TextField(
    pk: bool = False,
    unique: bool = False,
    index: bool = False,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> Field[str | None]: ...


# ── TimeDeltaField ──────────────────────────────────────────────────────

@overload
def TimeDeltaField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[False] = False,
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[datetime.timedelta]: ...

@overload
def TimeDeltaField(
    source_field: str | None = None,
    generated: bool = False,
    pk: bool = False,
    *,
    null: Literal[True],
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    description: str | None = None,
    model: Model | None = None,
    validators: _ValidatorType | None = None,
    **kwargs: Any,
) -> Field[datetime.timedelta | None]: ...


# ── TimeField ───────────────────────────────────────────────────────────

@overload
def TimeField(
    auto_now: bool = False,
    auto_now_add: bool = False,
    *,
    null: Literal[False] = False,
    **kwargs: Any,
) -> Field[datetime.time]: ...

@overload
def TimeField(
    auto_now: bool = False,
    auto_now_add: bool = False,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> Field[datetime.time | None]: ...


# ── UUIDField ───────────────────────────────────────────────────────────

@overload
def UUIDField(*, null: Literal[False] = False, **kwargs: Any) -> Field[uuid.UUID]: ...

@overload
def UUIDField(*, null: Literal[True], **kwargs: Any) -> Field[uuid.UUID | None]: ...
