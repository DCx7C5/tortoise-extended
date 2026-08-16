"""PostgreSQL ltree field for hierarchical data.

Provides LTreeField for storing materialized paths like "root.parent.child".
Requires: CREATE EXTENSION IF NOT EXISTS ltree;

Usage::

    from tortoise import models, fields
    from tortoise_extended.fields.ltree import LTreeField

    class Category(models.Model):
        name = fields.CharField(max_length=100)
        path = LTreeField(max_length=1024)

        class Meta:
            table = "categories"

    # Create root
    root = await Category.create(name="Electronics", path="electronics")

    # Create child
    laptops = await Category.create(name="Laptops", path="electronics.laptops")

    # Create grandchild
    macbook = await Category.create(name="MacBook", path="electronics.laptops.macbook")
"""

from typing import Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class LTreeField(Field[list[str]]):
    """PostgreSQL ltree column for hierarchical data.

    Stores materialized paths like "root.parent.child".
    Requires: CREATE EXTENSION IF NOT EXISTS ltree;

    :param max_length: Maximum path length in characters (default: 256).
        PostgreSQL's ``ltree`` type has no column length modifier, so this
        is enforced as a Python-side guard in :meth:`to_db_value` (paths
        longer than the limit raise ``ValueError``).
    :param separator: Path separator (default: ".")
    :param null: Allow NULL values
    :param default: Default path value
    :param description: Column comment

    Usage::

        class Category(Model):
            path = LTreeField(max_length=1024)

        # Query ancestors
        ancestors = await Category.filter(
            path__ancestor_of="root.parent.child"
        ).order_by("depth")

        # Query descendants
        descendants = await Category.filter(
            path__descendant_of="root"
        ).order_by("depth")
    """

    SQL_TYPE = "ltree"
    indexable = True

    def __init__(
        self,
        max_length: int = 256,
        separator: str = ".",
        *,
        null: bool = False,
        default: FieldDefaultValue = None,
        description: str | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None:
        self.max_length = max_length
        self.separator = separator
        super().__init__(
            null=null,
            default=default,
            description=description,
            **kwargs,
        )

    @override
    def to_python_value(self, value: str | list[str] | None) -> list[str] | None:
        """Convert ltree string to Python list.

        Args:
            value: Raw ltree value from database

        Returns:
            List of path components, or None if value is None
        """
        if value is None:
            return None
        if isinstance(value, list):
            return value
        return value.split(self.separator)

    @override
    def to_db_value(
        self, value: str | list[str] | None, instance: type[Model] | Model | None
    ) -> str | None:
        """Convert Python list to ltree string.

        Args:
            value: List of path components, or a pre-joined ltree string
            instance: Model instance (unused)

        Returns:
            ltree string, or None if value is None

        Raises:
            ValueError: If the joined path exceeds ``max_length``
        """
        if value is None:
            return None
        if isinstance(value, str):
            path = value
        else:
            path = self.separator.join(str(v) for v in value)
        if self.max_length and len(path) > self.max_length:
            raise ValueError(
                f"ltree path exceeds max_length={self.max_length}: "
                f"{path[: self.max_length]}... ({len(path)} chars)"
            )
        return path

    @override
    def __repr__(self) -> str:
        return f"LTreeField(max_length={self.max_length}, separator={self.separator!r})"
