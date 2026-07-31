"""PostgreSQL ltree field for hierarchical data.

Provides LTreeField for storing materialized paths like "root.parent.child".
Requires: CREATE EXTENSION IF NOT EXISTS ltree;

Usage::

    from tortoise import models, fields
    from tortoise_extended.fields.ltree_field import LTreeField

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

from typing import cast, override

from tortoise.fields.base import Field

from tortoise_extended._types import LibraryAny


class LTreeField(Field[str]):
    """PostgreSQL ltree column for hierarchical data.

    Stores materialized paths like "root.parent.child".
    Requires: CREATE EXTENSION IF NOT EXISTS ltree;

    :param max_length: Maximum path length (default: 256)
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
        default: LibraryAny = None,  # pyright: ignore[reportExplicitAny]
        description: str | None = None,
        **kwargs: LibraryAny,  # pyright: ignore[reportExplicitAny]
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
    def to_python_value(self, value: LibraryAny) -> list[str] | None:  # pyright: ignore[reportExplicitAny]
        """Convert ltree string to Python list.

        Args:
            value: Raw ltree value from database

        Returns:
            List of path components, or None if value is None
        """
        if value is None:
            return None
        if isinstance(value, list):
            return cast(list[str], value)
        return value.split(self.separator)

    @override
    def to_db_value(self, value: list[str] | None, instance: LibraryAny) -> str | None:  # pyright: ignore[reportExplicitAny]
        """Convert Python list to ltree string.

        Args:
            value: List of path components
            instance: Model instance (unused)

        Returns:
            ltree string, or None if value is None
        """
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return self.separator.join(str(v) for v in value)

    @override
    def __repr__(self) -> str:
        return f"LTreeField(max_length={self.max_length}, separator={self.separator!r})"
