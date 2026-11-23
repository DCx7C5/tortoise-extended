"""PostgreSQL ``macaddr`` and ``macaddr8`` fields for hardware addresses.

Provides MacAddrField for 48-bit MAC addresses (``macaddr``) and
MacAddr8Field for 64-bit EUI-64 addresses (``macaddr8``). Accepted input is
normalised to the lowercase, colon-separated form on write and on read.

Usage::

    from tortoise import models
    from tortoise_extended.fields.macaddr import MacAddrField

    class Interface(models.Model):
        name = models.CharField(max_length=100)
        hw = MacAddrField()

        class Meta:
            table = "interfaces"

    # Dash and dotted-Cisco input is normalised to aa:bb:cc:dd:ee:ff
    iface = await Interface.create(name="eth0", hw="AA-BB-CC-DD-EE-FF")
"""

import re
from typing import ClassVar, Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs

_HEX_OCTET = r"[0-9A-Fa-f]{2}"
_HEX_QUAD = r"[0-9A-Fa-f]{4}"


def _compile_pattern(octet_count: int) -> re.Pattern[str]:
    """Compile a pattern accepting colon, dash and dotted-Cisco spellings."""
    colon = rf"(?:{_HEX_OCTET}:){{{octet_count - 1}}}{_HEX_OCTET}"
    dash = rf"(?:{_HEX_OCTET}-){{{octet_count - 1}}}{_HEX_OCTET}"
    quad_count = octet_count // 2
    cisco = rf"{_HEX_QUAD}(?:\.{_HEX_QUAD}){{{quad_count - 1}}}"
    return re.compile(rf"^(?:{colon}|{dash}|{cisco})$")


_MACADDR_PATTERN = _compile_pattern(6)
_MACADDR8_PATTERN = _compile_pattern(8)


class _MacAddressField(Field[str]):
    """Shared parsing and formatting for the PostgreSQL MAC address types."""

    _PATTERN: ClassVar[re.Pattern[str]]
    _OCTET_COUNT: ClassVar[int]
    _TYPE_NAME: ClassVar[str]

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

    def _canonical(self, value: str) -> str:
        """Validate one address and return its lowercase colon form."""
        if not self._PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid {self._TYPE_NAME} value {value!r}: expected "
                f"{self._OCTET_COUNT} hex octets in colon (aa:bb:cc:...), "
                f"dash (aa-bb-cc-...) or dotted-Cisco (aabb.ccdd...) form"
            )
        digits = value.replace(":", "").replace("-", "").replace(".", "")
        return ":".join(
            digits[index : index + 2] for index in range(0, len(digits), 2)
        ).lower()

    @override
    def to_python_value(self, value: str | bytes | None) -> str | None:
        """Normalise a value read from the database.

        :param value: Raw value from the driver (asyncpg returns ``str``),
            or ``None``.
        :returns: The lowercase colon-separated form, or ``None``.
        :raises ValueError: If the value is not a valid MAC address.
        """
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode()
        return self._canonical(value)

    @override
    def to_db_value(
        self, value: str | None, instance: type[Model] | Model | None
    ) -> str | None:
        """Validate a Python value and return its canonical form.

        :param value: A colon, dash or dotted-Cisco address string, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The lowercase colon-separated form, or ``None``.
        :raises ValueError: If the value is not a valid MAC address.
        """
        if value is None:
            return None
        return self._canonical(value)


class MacAddrField(_MacAddressField):
    """PostgreSQL ``macaddr`` column for 48-bit hardware addresses.

    Stores six octets. The accepted spellings — ``aa:bb:cc:dd:ee:ff``,
    ``aa-bb-cc-dd-ee-ff`` and ``aabb.ccdd.eeff`` — are normalised to the
    lowercase colon form on write and on read. Any other shape raises
    ``ValueError`` naming the rejected value and the expected form.

    :param null: Allow NULL values.
    :param default: Default address value.
    :param description: Column comment.

    Usage::

        class Interface(Model):
            hw = MacAddrField()

        iface = await Interface.create(hw="AABB.CCDD.EEFF")
    """

    SQL_TYPE = "macaddr"
    _PATTERN = _MACADDR_PATTERN
    _OCTET_COUNT = 6
    _TYPE_NAME = "macaddr"

    @override
    def __repr__(self) -> str:
        return f"MacAddrField(null={self.null})"


class MacAddr8Field(_MacAddressField):
    """PostgreSQL ``macaddr8`` column for 64-bit EUI-64 addresses.

    Stores eight octets. The accepted spellings —
    ``aa:bb:cc:dd:ee:ff:00:11``, ``aa-bb-cc-dd-ee-ff-00-11`` and
    ``aabb.ccdd.eeff.0011`` — are normalised to the lowercase colon form on
    write and on read. Any other shape raises ``ValueError`` naming the
    rejected value and the expected form.

    :param null: Allow NULL values.
    :param default: Default address value.
    :param description: Column comment.

    Usage::

        class Interface(Model):
            hw = MacAddr8Field()

        iface = await Interface.create(hw="AABB.CCDD.EEFF.0011")
    """

    SQL_TYPE = "macaddr8"
    _PATTERN = _MACADDR8_PATTERN
    _OCTET_COUNT = 8
    _TYPE_NAME = "macaddr8"

    @override
    def __repr__(self) -> str:
        return f"MacAddr8Field(null={self.null})"
