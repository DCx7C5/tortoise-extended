"""PostgreSQL text field for filesystem-style paths.

Provides PathField for storing paths as ``TEXT``. Accepts ``str`` or
``pathlib.Path`` on write and normalizes to a string, rejecting NUL bytes.

Usage::

    from tortoise import models
    from tortoise_extended.fields.path import PathField

    class Document(models.Model):
        title = models.CharField(max_length=100)
        path = PathField()

        class Meta:
            table = "documents"

    # A pathlib.Path is converted to a string on save
    doc = await Document.create(title="README", path=Path("docs/readme.md"))
"""

from os import fspath
from pathlib import PurePosixPath
from typing import Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class PathField(Field[PurePosixPath]):
    """Text column for storing paths.

    Stores the path as a plain string. ``to_db_value`` accepts ``str`` or
    ``pathlib.Path`` (converted via ``os.fspath``); bytes are rejected at
    the type level and NUL bytes are rejected at runtime. Loaded values
    round-trip through ``to_python_value`` as ``PurePosixPath``.

    :param null: Allow NULL values.
    :param default: Default path value.
    :param description: Column comment.

    Usage::

        class Document(Model):
            path = PathField()
    """

    SQL_TYPE = "TEXT"
    indexable = True

    def __init__(
        self,
        *,
        null: bool = False,
        default: FieldDefaultValue = None,
        description: str | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None:
        super().__init__(
            null=null,
            default=default,
            description=description,
            **kwargs,
        )

    @override
    def to_python_value(self, value: str | PurePosixPath | None) -> PurePosixPath | None:
        """Convert a database value to a :class:`PurePosixPath`.

        The driver returns the column's plain path string; it is wrapped in
        ``PurePosixPath`` so loaded values round-trip to the field's declared
        Python type. ``PurePosixPath`` inputs pass through unchanged.

        :param value: Raw value from the driver, or ``None``.
        :returns: The path, or ``None``.
        """
        if value is None:
            return None
        if isinstance(value, PurePosixPath):
            return value
        return PurePosixPath(value)

    @override
    def to_db_value(
        self, value: str | PurePosixPath | None, instance: type[Model] | Model | None
    ) -> str | None:
        """Convert a Python path to its string form.

        ``pathlib.Path`` values are converted with ``os.fspath``; ``bytes``
        are rejected (the field stores text only).

        :param value: The path value, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The path string, or ``None``.
        :raises ValueError: If the string contains a NUL byte.
        """
        if value is None:
            return None
        path = fspath(str(value))
        if "\x00" in path:
            raise ValueError("PathField value must not contain a NUL byte")
        return path

    @override
    def __repr__(self) -> str:
        return f"PathField(null={self.null})"
