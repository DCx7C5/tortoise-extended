# pyright: reportExplicitAny=false
"""Type stubs for ``tortoise.models``.

The local ``tortoise-stubs`` overlay REPLACES the upstream stubs and the
runtime analysis entirely (``stubPath`` overrides do not merge — verified in
scratch experiments). Therefore this file must be self-sufficient and mirror
the fully-annotated runtime module.

It adds the symbols ``tortoise_extended`` patches or that the Tortoise
metaclass injects dynamically:

- ``get_filters_for_field`` — re-exported into ``tortoise.models`` by
  ``_apply_patches()``.
- ``Model.DoesNotExist`` — set by the metaclass; never declared statically in
  the runtime source.

``Any`` is used only where the runtime is untyped/dynamic (``**kwargs``,
``pk`` accessors). reportExplicitAny is disabled for this file for that reason.
"""

from collections.abc import Callable, Iterable
from typing import Any, Self, override

from pypika_tortoise.queries import Table
from pypika_tortoise.terms import Term
from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.exceptions import DoesNotExist
from tortoise.expressions import Expression, Q
from tortoise.fields.base import Field
from tortoise.manager import Manager
from tortoise.queryset import (
    BulkCreateQuery,
    BulkUpdateQuery,
    ExistsQuery,
    QuerySet,
    QuerySetSingle,
    RawSQLQuery,
)
from tortoise.signals import Signals


def get_filters_for_field(
    field_name: str, field: Field[object] | None, source_field: str
) -> dict[str, object]: ...


class MetaInfo:
    """Metadata container attached to every Tortoise model as ``_meta``."""

    abstract: bool
    db_table: str
    schema: str | None
    app: str | None
    pk_attr: str
    pk: Field[Any]
    fields: set[str]
    db_fields: set[str]
    fk_fields: set[str]
    fields_map: dict[str, Field[Any]]
    filters: dict[str, object]
    manager: Manager


class Model:
    """Base class for all Tortoise ORM models."""

    DoesNotExist: type[DoesNotExist]

    _meta: MetaInfo
    pk: Any

    def __init__(self, **kwargs: Any) -> None: ...

    @override
    def __str__(self) -> str: ...
    @override
    def __repr__(self) -> str: ...

    def update_from_dict(self, data: dict[str, object]) -> Self: ...
    def clone(self, pk: Any = ...) -> Self: ...

    async def save(
        self,
        using_db: BaseDBAsyncClient | None = None,
        update_fields: Iterable[str] | None = None,
        force_create: bool = False,
        force_update: bool = False,
    ) -> None: ...

    async def delete(self, using_db: BaseDBAsyncClient | None = None) -> None: ...

    async def fetch_related(self, *args: Any, using_db: BaseDBAsyncClient | None = None) -> None: ...

    async def refresh_from_db(
        self,
        fields: Iterable[str] | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None: ...

    @classmethod
    def filter(cls, *args: Q, using_db: BaseDBAsyncClient | None = None, **kwargs: Any) -> QuerySet[Self]: ...

    @classmethod
    def exclude(cls, *args: Q, using_db: BaseDBAsyncClient | None = None, **kwargs: Any) -> QuerySet[Self]: ...

    @classmethod
    def annotate(cls, **kwargs: Expression | Term) -> QuerySet[Self]: ...

    @classmethod
    def all(cls, using_db: BaseDBAsyncClient | None = None) -> QuerySet[Self]: ...

    @classmethod
    def get(
        cls, *args: Q, using_db: BaseDBAsyncClient | None = None, **kwargs: Any
    ) -> QuerySetSingle[Self]: ...

    @classmethod
    def get_or_none(
        cls, *args: Q, using_db: BaseDBAsyncClient | None = None, **kwargs: Any
    ) -> QuerySetSingle[Self | None]: ...

    @classmethod
    def exists(
        cls, *args: Q, using_db: BaseDBAsyncClient | None = None, **kwargs: Any
    ) -> ExistsQuery: ...

    @classmethod
    async def create(cls, using_db: BaseDBAsyncClient | None = None, **kwargs: Any) -> Self: ...

    @classmethod
    async def get_or_create(
        cls,
        defaults: dict[str, object] | None = None,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: Any,
    ) -> tuple[Self, bool]: ...

    @classmethod
    async def update_or_create(
        cls,
        defaults: dict[str, object] | None = None,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: Any,
    ) -> tuple[Self, bool]: ...

    @classmethod
    def bulk_create(
        cls,
        objects: Iterable[Self],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        on_conflict: Iterable[str] | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> BulkCreateQuery[Self]: ...

    @classmethod
    def bulk_update(
        cls,
        objects: Iterable[Self],
        fields: Iterable[str],
        batch_size: int | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> BulkUpdateQuery[Self]: ...

    @classmethod
    async def in_bulk(
        cls,
        id_list: Iterable[Any],
        field_name: str = "pk",
        using_db: BaseDBAsyncClient | None = None,
    ) -> dict[Any, Self]: ...

    @classmethod
    def first(cls, using_db: BaseDBAsyncClient | None = None) -> QuerySetSingle[Self | None]: ...

    @classmethod
    def last(cls, using_db: BaseDBAsyncClient | None = None) -> QuerySetSingle[Self | None]: ...

    @classmethod
    def latest(cls, *orderings: str) -> QuerySetSingle[Self | None]: ...

    @classmethod
    def earliest(cls, *orderings: str) -> QuerySetSingle[Self | None]: ...

    @classmethod
    def select_for_update(
        cls,
        nowait: bool = False,
        skip_locked: bool = False,
        of: tuple[str, ...] = (),
        using_db: BaseDBAsyncClient | None = None,
        no_key: bool = False,
    ) -> QuerySet[Self]: ...

    @classmethod
    def raw(
        cls, raw_query: str, using_db: BaseDBAsyncClient | None = None
    ) -> RawSQLQuery: ...

    @classmethod
    async def fetch_for_list(
        cls,
        instance_list: Iterable[Model],
        *args: Any,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None: ...

    @classmethod
    def construct(cls, _saved_in_db: bool = False, **kwargs: Any) -> Self: ...

    @classmethod
    def get_table(cls) -> Table: ...

    @classmethod
    def register_listener(cls, signal: Signals, listener: Callable[..., Any]) -> None: ...

    @classmethod
    def describe(cls, serializable: bool = True) -> dict[str, Any]: ...
