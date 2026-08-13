# pyright: reportExplicitAny=false
"""Type stubs for the ``tortoise`` package root (local overlay).

The local ``stubs`` overlay REPLACES the installed upstream stubs
and the runtime analysis entirely (``stubPath`` overrides do not merge).
This root stub types the ``Tortoise`` class surface consumed by
``tortoise_extended`` and re-exports the submodules the package imports from
the package root.

``Tortoise.apps`` and ``Tortoise._inited`` are ``classproperty`` descriptors
in the runtime — attribute access without parentheses. They are declared as
``ClassVar`` so attribute narrowing (``if not Tortoise.apps``) type-checks.

``Any`` is used only where the runtime is untyped/dynamic.
"""

from collections.abc import Iterable
from types import ModuleType
from typing import Any, ClassVar

from tortoise import fields as fields
from tortoise import models as models

from tortoise.apps import Apps
from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.connection import (
    connections as connections,
    get_connection as get_connection,
    get_connections as get_connections,
)
from tortoise.context import TortoiseContext
from tortoise.models import Model as Model, ModelMeta as ModelMeta

class Tortoise:
    """Tortoise ORM entry point (``tortoise_extended`` surface)."""

    # classproperty descriptors — attribute access, no parentheses.
    apps: ClassVar[Apps | None]
    _inited: ClassVar[bool]

    @classmethod
    async def init(
        cls,
        config: dict[str, object] | None = None,
        config_file: str | None = None,
        _create_db: bool = False,
        db_url: str | None = None,
        modules: dict[str, list[str]] | None = None,
        use_tz: bool = True,
        timezone: str = "UTC",
        routers: list[str] | None = None,
        init_connections: bool = True,
    ) -> TortoiseContext: ...
    @classmethod
    def get_connection(cls, connection_name: str) -> BaseDBAsyncClient: ...
    @classmethod
    async def close_connections(cls) -> None: ...
    @classmethod
    async def generate_schemas(cls, safe: bool = True) -> None: ...
    @classmethod
    def is_inited(cls) -> bool: ...
    @classmethod
    def init_models(
        cls,
        models_paths: Iterable[ModuleType | str],
        app_label: str,
        _init_relations: bool = True,
    ) -> None: ...
    @classmethod
    def init_app(
        cls,
        label: str,
        model_paths: Iterable[ModuleType | str],
        _init_relations: bool = True,
    ) -> dict[str, type[Model]]: ...
    @classmethod
    def describe_model(
        cls, model: type[Model], serializable: bool = True
    ) -> dict[str, Any]: ...
    @classmethod
    def describe_models(
        cls,
        models: list[type[Model]] | None = None,
        serializable: bool = True,
    ) -> dict[str, dict[str, Any]]: ...
    @classmethod
    def _drop_database(cls, connection_name: str = "default") -> None: ...
