"""PostgreSQL ``inet`` field for IPv4 and IPv6 addresses.

Provides InetField for storing plain IPv4 or IPv6 host addresses using the
PostgreSQL ``inet`` type, which supports indexing. Prefix/interface forms
(``192.168.1.0/24``) are rejected: the field stores host addresses only.

Usage::

    from tortoise import models
    from tortoise_extended.fields.inet import InetField

    class Server(models.Model):
        name = models.CharField(max_length=100)
        ip = InetField()

        class Meta:
            table = "servers"

    # Create with a string; the field converts it to an address object
    server = await Server.create(name="web-01", ip="192.168.1.10")
    dual = await Server.create(name="web-02", ip="2001:db8::1")
"""

import ipaddress
from typing import Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


type _InetInput = (
    ipaddress.IPv4Address
    | ipaddress.IPv6Address
    | ipaddress.IPv4Interface
    | ipaddress.IPv6Interface
    | str
    | bytes
)


def _host_address(value: _InetInput) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Normalise a driver or Python value to a plain host address.

    ``IPv4Interface``/``IPv6Interface`` subclass the matching address class,
    so they are tested first — otherwise the address branch would return the
    interface object unchanged. A full-length prefix is still a host address;
    anything shorter is rejected. Strings carrying a ``/prefix`` are rejected
    by :func:`ipaddress.ip_address`.

    :raises ValueError: If the value is not a plain IPv4/IPv6 host address.
    """
    if isinstance(value, ipaddress.IPv4Interface | ipaddress.IPv6Interface):
        network = value.network
        if network.prefixlen != network.max_prefixlen:
            raise ValueError(
                f"invalid inet value {value!r}: prefix notation is not "
                "supported, only host addresses are stored"
            )
        return value.ip
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return value
    if isinstance(value, bytes):
        value = value.decode()
    return ipaddress.ip_address(value)


class InetField(Field[ipaddress.IPv4Address | ipaddress.IPv6Address]):
    """PostgreSQL ``inet`` column for IPv4 and IPv6 host addresses.

    Stores addresses of either family. Invalid strings raise ``ValueError``
    at save time (``to_db_value``) and at load time (``to_python_value``).
    Interface/prefix notation (``192.168.1.1/24``) is not accepted — this
    field is for plain addresses only. A driver interface object carrying a
    full-length prefix (``/32`` or ``/128``) is normalised to its address; a
    shorter prefix raises ``ValueError``.

    :param null: Allow NULL values.
    :param default: Default address value.
    :param description: Column comment.

    Usage::

        class Server(Model):
            ip = InetField()

        v4 = await Server.create(ip="192.168.1.10")
        v6 = await Server.create(ip="2001:db8::1")
    """

    SQL_TYPE = "inet"
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
    def to_python_value(
        self, value: _InetInput | None
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        """Convert a database value to an ``IPv4Address`` or ``IPv6Address``.

        :param value: Raw value from the driver, a textual address, packed
            bytes, or ``None``. asyncpg returns address objects for host
            addresses and interface objects for values carrying a non-host
            prefix.
        :returns: A plain host address, or ``None``.
        :raises ValueError: If the value is not a plain IPv4/IPv6 host
            address, including textual or driver-supplied prefix forms.
        """
        if value is None:
            return None
        return _host_address(value)

    @override
    def to_db_value(
        self, value: _InetInput | None, instance: type[Model] | Model | None
    ) -> str | None:
        """Validate a Python address and return its string form.

        :param value: An address or interface object, an address string, packed
            bytes, or ``None``. Strings with a ``/prefix`` are rejected; an
            interface object with a full-length prefix is normalised to its
            address.
        :param instance: The model instance being saved (unused).
        :returns: The canonical address string, or ``None``.
        :raises ValueError: If the value is not a plain IPv4/IPv6 host address.
        """
        if value is None:
            return None
        return str(_host_address(value))

    @override
    def __repr__(self) -> str:
        return f"InetField(null={self.null})"
