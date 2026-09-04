"""PostgreSQL ``inet`` field for IPv4 addresses.

Provides IPv4Field for storing IPv4 addresses using the PostgreSQL ``inet``
type, which supports indexing.

Usage::

    from tortoise import models
    from tortoise_extended.fields.ipv4 import IPv4Field

    class Server(models.Model):
        name = models.CharField(max_length=100)
        ip = IPv4Field()

        class Meta:
            table = "servers"

    # Create with a string; the field converts it to IPv4Address
    server = await Server.create(name="web-01", ip="192.168.1.10")
"""

import ipaddress
from typing import Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class IPv4Field(Field[ipaddress.IPv4Address]):
    """PostgreSQL ``inet`` column for IPv4 addresses.

    Stores dotted-quad IPv4 addresses. Invalid strings raise ``ValueError``
    at save time (``to_db_value``) and at load time (``to_python_value``).

    :param null: Allow NULL values.
    :param default: Default address value.
    :param description: Column comment.

    Usage::

        class Server(Model):
            ip = IPv4Field()

        server = await Server.create(name="web-01", ip="192.168.1.10")
    """

    SQL_TYPE = "inet"
    indexable = True

    class _db_sqlite:
        SQL_TYPE = "VARCHAR(15)"
        skip_to_python_if_native = False

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
    def to_python_value(
        self, value: ipaddress.IPv4Address | str | bytes | None
    ) -> ipaddress.IPv4Address | None:
        """Convert a database value to an ``ipaddress.IPv4Address``.

        :param value: Raw value from the driver (asyncpg returns ``str``;
            SQLite returns the text form), or ``None``.
        :returns: An ``IPv4Address``, or ``None``.
        :raises ValueError: If the string is not a valid IPv4 address.
        """
        if value is None:
            return None
        if isinstance(value, ipaddress.IPv4Address):
            return value
        if isinstance(value, bytes):
            value = value.decode()
        return ipaddress.IPv4Address(value)

    @override
    def to_db_value(
        self,
        value: ipaddress.IPv4Address | str | None,
        instance: type[Model] | Model | None,
    ) -> str | None:
        """Convert a Python IPv4 address to its dotted-quad string form.

        :param value: An ``IPv4Address`` or address string, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The dotted-quad string, or ``None``.
        :raises ValueError: If the value is not a valid IPv4 address.
        """
        if value is None:
            return None
        if isinstance(value, ipaddress.IPv4Address):
            return str(value)
        # Validate strings so invalid addresses fail at save time.
        return str(ipaddress.IPv4Address(value))

    @override
    def __repr__(self) -> str:
        return f"IPv4Field(null={self.null})"
