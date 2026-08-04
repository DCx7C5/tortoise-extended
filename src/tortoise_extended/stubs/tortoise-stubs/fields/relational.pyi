# pyright: reportExplicitAny=false
"""Type stubs for tortoise.fields.relational.

Completes the stubs that tortoise-orm-stubs omits.  The upstream
package only provides ``tortoise-stubs/fields/__init__.pyi`` which
re-exports from ``tortoise.fields.relational`` — but that module
has no .pyi, so pyright falls back to the runtime source where
ModelMeta and _FieldMeta are partially unknown.
"""

from collections.abc import AsyncGenerator, Generator, Iterator
from typing import Any, Generic, Literal, TypeAlias, TypeVar, overload, override

from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.expressions import Q
from tortoise.fields.base import CASCADE, Field, OnDelete
from tortoise.models import Model
from tortoise.queryset import QuerySet

_MODEL = TypeVar("_MODEL", bound=Model)


# ── containers ───────────────────────────────────────────────────────────

class ReverseRelation(Generic[_MODEL]):
    """Relation container for :func:`.ForeignKeyField`."""

    remote_model: type[_MODEL]
    relation_field: str
    instance: Model
    from_field: str
    _fetched: bool
    related_objects: list[_MODEL]

    def __init__(
        self,
        remote_model: type[_MODEL],
        relation_field: str,
        instance: Model,
        from_field: str,
    ) -> None: ...
    def __contains__(self, item: Any) -> bool: ...
    def __iter__(self) -> Iterator[_MODEL]: ...
    def __len__(self) -> int: ...
    def __bool__(self) -> bool: ...
    def __getitem__(self, item: int) -> _MODEL: ...
    def __await__(self) -> Generator[Any, None, list[_MODEL]]: ...
    async def __aiter__(self) -> AsyncGenerator[_MODEL]: ...
    def filter(self, *args: Q, **kwargs: Any) -> QuerySet[_MODEL]: ...
    def all(self) -> QuerySet[_MODEL]: ...
    def order_by(self, *orderings: str) -> QuerySet[_MODEL]: ...
    def limit(self, limit: int) -> QuerySet[_MODEL]: ...
    def offset(self, offset: int) -> QuerySet[_MODEL]: ...
    async def create(
        self, using_db: BaseDBAsyncClient | None = None, **kwargs: Any
    ) -> _MODEL: ...


class ManyToManyRelation(ReverseRelation[_MODEL]):
    """Relation container for :func:`.ManyToManyField`."""

    field: ManyToManyFieldInstance[_MODEL]
    instance: Model

    def __init__(
        self, instance: Model, m2m_field: ManyToManyFieldInstance[_MODEL]
    ) -> None: ...
    async def add(
        self, *instances: _MODEL, using_db: BaseDBAsyncClient | None = None
    ) -> None: ...
    async def clear(self, using_db: BaseDBAsyncClient | None = None) -> None: ...
    async def remove(
        self, *instances: _MODEL, using_db: BaseDBAsyncClient | None = None
    ) -> None: ...


# ── fields ───────────────────────────────────────────────────────────────

class RelationalField(Field[_MODEL]):
    has_db_field: bool
    related_model: type[_MODEL]
    to_field: str
    to_field_instance: Field[Any]
    db_constraint: bool

    def __init__(
        self,
        related_model: type[_MODEL],
        to_field: str | None = None,
        db_constraint: bool = True,
        **kwargs: Any,
    ) -> None: ...

    # noinspection PyMethodOverriding
    # NOTE: PyCharm's builtin checker flags these descriptor overloads as
    # signature-incompatible with Field.__get__; the identical pattern at
    # runtime (tortoise/fields/relational.py:283) is clean only because
    # site-packages files get reduced inspections. basedpyright accepts it.
    @overload
    def __get__(
        self, instance: None, owner: type[Model]
    ) -> RelationalField[_MODEL]: ...
    # noinspection PyMethodOverriding
    @overload
    def __get__(
        self, instance: Model, owner: type[Model]
    ) -> _MODEL: ...
    @override
    def __get__(
        self, instance: Model | None, owner: type[Model]
    ) -> RelationalField[_MODEL] | _MODEL: ...
    @override
    def __set__(self, instance: Model, value: _MODEL) -> None: ...


class ForeignKeyFieldInstance(RelationalField[_MODEL]):
    model_name: type[Model] | str
    related_name: str | None | Literal[False]
    on_delete: OnDelete

    def __init__(
        self,
        model_name: type[Model] | str,
        related_name: str | None | Literal[False] = None,
        on_delete: OnDelete = CASCADE,
        **kwargs: Any,
    ) -> None: ...



class BackwardFKRelation(RelationalField[_MODEL]):
    relation_field: str
    relation_source_field: str
    description: str | None

    def __init__(
        self,
        field_type: type[_MODEL],
        relation_field: str,
        relation_source_field: str,
        null: bool,
        description: str | None,
        **kwargs: Any,
    ) -> None: ...


class OneToOneFieldInstance(ForeignKeyFieldInstance[_MODEL]): ...


class BackwardOneToOneRelation(BackwardFKRelation[_MODEL]): ...


class ManyToManyFieldInstance(RelationalField[_MODEL]):
    model_name: type[Model] | str
    related_name: str
    forward_key: str
    backward_key: str
    through: str
    through_schema: str | None
    _generated: bool
    on_delete: OnDelete

    def __init__(
        self,
        model_name: type[Model] | str,
        through: str | None = None,
        forward_key: str | None = None,
        backward_key: str = "",
        related_name: str = "",
        on_delete: OnDelete = CASCADE,
        field_type: type[_MODEL] | None = None,
        unique: bool = True,
        **kwargs: Any,
    ) -> None: ...


# ── factory functions (with proper _MODEL inference) ───────────────────────

@overload
def ForeignKeyField(
    to: type[_MODEL],
    related_name: str | None | Literal[False] = None,
    on_delete: OnDelete = CASCADE,
    db_constraint: bool = True,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> ForeignKeyFieldInstance[_MODEL] | None: ...

@overload
def ForeignKeyField(
    to: type[_MODEL],
    related_name: str | None | Literal[False] = None,
    on_delete: OnDelete = CASCADE,
    db_constraint: bool = True,
    null: Literal[False] = False,
    **kwargs: Any,
) -> ForeignKeyFieldInstance[_MODEL]: ...

@overload
def ForeignKeyField(
    to: str,
    related_name: str | None | Literal[False] = None,
    on_delete: OnDelete = CASCADE,
    db_constraint: bool = True,
    null: bool = False,
    **kwargs: Any,
) -> ForeignKeyFieldInstance[Any]: ...


@overload
def OneToOneField(
    to: type[_MODEL],
    related_name: str | None | Literal[False] = None,
    on_delete: OnDelete = CASCADE,
    db_constraint: bool = True,
    *,
    null: Literal[True],
    **kwargs: Any,
) -> OneToOneFieldInstance[_MODEL] | None: ...

@overload
def OneToOneField(
    to: type[_MODEL],
    related_name: str | None | Literal[False] = None,
    on_delete: OnDelete = CASCADE,
    db_constraint: bool = True,
    null: Literal[False] = False,
    **kwargs: Any,
) -> OneToOneFieldInstance[_MODEL]: ...

@overload
def OneToOneField(
    to: str,
    related_name: str | None | Literal[False] = None,
    on_delete: OnDelete = CASCADE,
    db_constraint: bool = True,
    null: bool = False,
    **kwargs: Any,
) -> OneToOneFieldInstance[Any]: ...


def ManyToManyField(
    to: type[_MODEL] | str,
    through: str | None = None,
    forward_key: str | None = None,
    backward_key: str = "",
    related_name: str = "",
    on_delete: OnDelete = CASCADE,
    db_constraint: bool = True,
    unique: bool = True,
    **kwargs: Any,
) -> ManyToManyRelation[_MODEL]: ...


# ── type aliases ──────────────────────────────────────────────────────────

OneToOneRelation: TypeAlias = OneToOneFieldInstance[_MODEL]
OneToOneNullableRelation: TypeAlias = OneToOneFieldInstance[_MODEL] | None
ForeignKeyRelation: TypeAlias = ForeignKeyFieldInstance[_MODEL]
ForeignKeyNullableRelation: TypeAlias = ForeignKeyFieldInstance[_MODEL] | None
