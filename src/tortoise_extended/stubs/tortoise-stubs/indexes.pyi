# pyright: reportExplicitAny=false
"""Type stubs for ``tortoise.indexes`` (local overlay).

Mirrors the runtime module layout (``tortoise/indexes.py``, not a package).
Declares the upstream ``Index`` base class plus the pgvector/ltree index
types that ``tortoise_extended`` registers at import time (``HNSWIndex``,
``IVFFlatIndex``, ``GiSTIndex``) so the monkey-patch attribute access in
``tortoise_extended`` type-checks against the runtime module.
"""

from typing import Any, override

from tortoise.backends.base.schema_generator import BaseSchemaGenerator
from tortoise.models import Model

class Index:
    """Base class for all index types (mirrors upstream ``tortoise.indexes.Index``)."""

    INDEX_TYPE: str
    fields: list[str]
    name: str | None
    expressions: tuple[Any, ...]
    extra: str

    def __init__(
        self,
        *expressions: Any,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
    ) -> None: ...
    def describe(self) -> dict[str, Any]: ...
    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]: ...
    def index_name(
        self, schema_generator: BaseSchemaGenerator, model: type[Model]
    ) -> str: ...
    def get_sql(
        self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool
    ) -> str: ...
    def resolve_expressions(self, model: type[Model]) -> None: ...
    @property
    def field_names(self) -> list[str]: ...

class HNSWIndex(Index):
    """pgvector HNSW index (registered by ``tortoise_extended``)."""

    INDEX_TYPE: str
    m: int
    ef_construction: int
    dist_metric: str

    def __init__(
        self,
        *args: Any,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
        m: int = 16,
        ef_construction: int = 200,
        dist_metric: str = "vector_l2_ops",
    ) -> None: ...
    @override
    def describe(self) -> dict[str, Any]: ...
    @override
    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]: ...
    @override
    def get_sql(
        self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool
    ) -> str: ...

class IVFFlatIndex(Index):
    """pgvector IVFFlat index (registered by ``tortoise_extended``)."""

    INDEX_TYPE: str
    lists: int
    dist_metric: str

    def __init__(
        self,
        *args: Any,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
        lists: int = 100,
        dist_metric: str = "vector_l2_ops",
    ) -> None: ...
    @override
    def describe(self) -> dict[str, Any]: ...
    @override
    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]: ...
    @override
    def get_sql(
        self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool
    ) -> str: ...

class GiSTIndex(Index):
    """PostgreSQL GiST index for ltree columns (registered by ``tortoise_extended``)."""

    INDEX_TYPE: str

    def __init__(
        self,
        *expressions: Any,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
    ) -> None: ...
    @override
    def get_sql(
        self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool
    ) -> str: ...
