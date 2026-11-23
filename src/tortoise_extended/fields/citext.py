"""PostgreSQL ``citext`` field for case-insensitive text.

Provides CitextField for storing text that the database compares
case-insensitively: ``WHERE name = 'Mixed'`` matches ``'MiXeD'``. The
original spelling is preserved on read.

Requires: CREATE EXTENSION IF NOT EXISTS citext;

Usage::

    from tortoise import models
    from tortoise_extended.fields.citext import CitextField

    class Account(models.Model):
        email = CitextField(max_length=320)

        class Meta:
            table = "accounts"

    account = await Account.create(email="Mixed@Example.COM")
    found = await Account.get(email="mixed@example.com")
"""

from typing import Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class CitextField(Field[str]):
    """PostgreSQL ``citext`` column for case-insensitive text.

    Comparisons, uniqueness and ``ORDER BY`` are case-insensitive because
    the database type handles them; the value itself keeps its original
    case. Requires: CREATE EXTENSION IF NOT EXISTS citext;

    :param max_length: Maximum character count (default: 256). PostgreSQL's
        ``citext`` type has no column length modifier, so this is enforced
        as a Python-side guard in :meth:`to_db_value` (longer values raise
        ``ValueError``).
    :param null: Allow NULL values.
    :param default: Default text value.
    :param description: Column comment.

    Usage::

        class Account(Model):
            email = CitextField(max_length=320)

        account = await Account.create(email="Mixed@Example.COM")
        found = await Account.get(email="mixed@example.com")
    """

    SQL_TYPE = "citext"
    indexable = True

    def __init__(
        self,
        max_length: int = 256,
        *,
        null: bool = False,
        default: FieldDefaultValue = None,
        description: str | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None:
        self.max_length = max_length
        super().__init__(
            null=null,
            default=default,
            description=description,
            **kwargs,
        )

    @override
    def to_python_value(self, value: str | bytes | None) -> str | None:
        """Convert a database value to ``str``, preserving its case.

        :param value: Raw value from the driver (asyncpg returns ``str``),
            or ``None``.
        :returns: The stored text, or ``None``.
        """
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode()
        return value

    @override
    def to_db_value(
        self, value: str | None, instance: type[Model] | Model | None
    ) -> str | None:
        """Validate the Python-side length guard and return the text.

        :param value: The text to store, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The unchanged text, or ``None``.
        :raises ValueError: If the value exceeds ``max_length``.
        """
        if value is None:
            return None
        if self.max_length and len(value) > self.max_length:
            raise ValueError(
                f"citext value exceeds max_length={self.max_length}: "
                f"{value[: self.max_length]}... ({len(value)} chars)"
            )
        return value

    @override
    def __repr__(self) -> str:
        return f"CitextField(max_length={self.max_length})"
