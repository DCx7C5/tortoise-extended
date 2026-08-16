"""PostgreSQL text field for validated URLs.

Provides URLField for storing URLs as ``TEXT``. Values are validated with
``urllib.parse.urlsplit`` — both a scheme and a netloc are required —
before being saved.

Usage::

    from tortoise import models
    from tortoise_extended.fields.url import URLField

    class Page(models.Model):
        title = models.CharField(max_length=100)
        url = URLField()

        class Meta:
            table = "pages"

    # Valid URLs pass through unchanged
    page = await Page.create(title="Home", url="https://example.com/")
"""

from typing import Unpack, override
from urllib.parse import urlsplit

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class URLField(Field[str]):
    """Text column for storing validated URLs.

    The stored string must include both a scheme and a netloc (e.g.
    ``https://example.com``); anything else raises ``ValueError`` at save
    time (``to_db_value``).

    :param null: Allow NULL values.
    :param default: Default URL value.
    :param description: Column comment.

    Usage::

        class Page(Model):
            url = URLField()
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
    def to_python_value(self, value: str | None) -> str | None:
        """Convert a database value to a plain URL string.

        :param value: Raw value from the driver, or ``None``.
        :returns: The URL string, or ``None``.
        """
        if value is None:
            return None
        return value

    @override
    def to_db_value(
        self, value: str | None, instance: type[Model] | Model | None
    ) -> str | None:
        """Convert a Python URL string to its database form.

        The value must include both a scheme and a netloc (per
        ``urllib.parse.urlsplit``); otherwise ``ValueError`` is raised.
        Leading/trailing whitespace is rejected rather than silently
        stripped, and any whitespace or control character in the value
        is rejected.

        :param value: The URL string, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The URL string, or ``None``.
        :raises ValueError: If the URL lacks a scheme or netloc, or
            contains whitespace/control characters.
        """
        if value is None:
            return None
        if value.strip() != value:
            raise ValueError(f"Invalid URL: {value!r}")
        parts = urlsplit(value)
        if not (parts.scheme and parts.netloc):
            raise ValueError(f"Invalid URL: {value!r}")
        if any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError(f"Invalid URL: {value!r}")
        return value

    @override
    def __repr__(self) -> str:
        return f"URLField(null={self.null})"
