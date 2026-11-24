"""Tagged, JSON-safe codec for cache-backed field values.

Cache-backed fields persist Python values through a backend whose serializer is
JSON-based (:class:`tortoise_extended.cache.base.JSONSerializer`). JSON knows
``str``, ``int``, ``float``, ``bool``, ``None``, ``list`` and ``dict`` only, and
``pickle`` is not an option — the cache layer's restricted unpickler rejects
``datetime`` and every other non-primitive. This module bridges the gap with a
**tagged** envelope: values JSON cannot represent are wrapped as
``{"__t": <tag>, "v": <payload>}`` and plain scalars pass through untagged.

Encoding table:

- ``str`` / ``int`` / ``float`` / ``bool`` / ``None`` — bare, untagged.
- ``bytes`` → ``{"__t": "bytes", "v": <base64 ASCII>}``.
- ``datetime`` / ``date`` / ``time`` → their own tag, ``v`` is ISO 8601.
- ``Decimal`` → ``{"__t": "decimal", "v": <str>}``.
- ``UUID`` → ``{"__t": "uuid", "v": <canonical str>}``.
- ``pathlib.PurePath`` → ``{"__t": "path", "v": <str>}``.
- ``list`` → a JSON array of encoded elements. Lists stay untagged: a sequence
  cannot collide with the mapping envelope.
- ``dict`` → ``{"__t": "dict", "v": {<str key>: <encoded value>}}``. Every
  mapping is tagged, so a user dict that happens to contain a ``"__t"`` key can
  never be mistaken for an envelope. Non-``str`` keys raise
  :class:`~tortoise_extended.exceptions.CacheSerializationError`.

Two ``isinstance`` orders are pinned and must never be reordered: ``bool`` is
checked before ``int``, and ``datetime`` before ``date``. Both are subclass
pairs (``bool`` subclasses ``int``; ``datetime`` subclasses ``date``), so the
reverse order would route a tagged value through the untagged branch.

Bounds are enforced, not decorative. A value nested deeper than :data:`MAX_DEPTH`
levels, a ``bytes`` leaf larger than :data:`MAX_BYTES_LEAF` decoded, or an
encoded envelope larger than :data:`MAX_ENCODED_BYTES` raises
:class:`~tortoise_extended.exceptions.CacheSerializationError`. The two size
caps are independently reachable because base64 expands a ``bytes`` leaf by
about a third (8 MiB decoded → ~10.7 MiB encoded, under the 16 MiB envelope).

A :class:`CacheCodec` holds no state; one instance (or the shared default) is
safe to use from concurrent tasks and threads.

Usage::

    from datetime import datetime

    from tortoise_extended.fields._cache_codec import CacheCodec

    codec = CacheCodec()
    payload = codec.encode({"at": datetime(2026, 11, 24, 9, 30), "raw": b"\\x00\\x01"})
    # {"__t": "dict", "v": {
    #     "at": {"__t": "datetime", "v": "2026-11-24T09:30:00"},
    #     "raw": {"__t": "bytes", "v": "AAE="},
    # }}
    assert codec.decode(payload) == {
        "at": datetime(2026, 11, 24, 9, 30),
        "raw": b"\\x00\\x01",
    }
"""

import base64
import json
from collections.abc import Callable
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import PurePath
from typing import Final, cast
from uuid import UUID

from tortoise.fields.base import Field
from tortoise.fields.data import (
    BigIntField,
    BinaryField,
    BooleanField,
    CharEnumFieldInstance,
    CharField as TortoiseCharField,
    DateField,
    DatetimeField,
    DecimalField,
    FloatField,
    IntEnumFieldInstance,
    IntField,
    JSONField,
    SmallIntField,
    TextField,
    TimeDeltaField,
    TimeField,
    UUIDField,
)
from tortoise.fields.relational import RelationalField

from tortoise_extended._types import CacheScalar, CacheValue
from tortoise_extended.exceptions import CacheSerializationError
from tortoise_extended.fields.char import CharField
from tortoise_extended.fields.citext import CitextField
from tortoise_extended.fields.inet import InetField
from tortoise_extended.fields.ipv4 import IPv4Field
from tortoise_extended.fields.ltree import LTreeField
from tortoise_extended.fields.macaddr import MacAddr8Field, MacAddrField
from tortoise_extended.fields.path import PathField
from tortoise_extended.fields.pgcrypto import PGPEncryptedField
from tortoise_extended.fields.url import URLField
from tortoise_extended.fields.uuid import UUID4Field, UUID7Field
from tortoise_extended.fields.vector import VectorField

#: Envelope tag key and payload key (``{"__t": <tag>, "v": <payload>}``).
_TAG_KEY: Final[str] = "__t"
_VALUE_KEY: Final[str] = "v"

#: Envelope tags. ``dict`` is handled structurally on decode (its payload is a
#: mapping, not a string); the rest are looked up in :data:`_PARSERS`.
_TAG_BYTES: Final[str] = "bytes"
_TAG_DATETIME: Final[str] = "datetime"
_TAG_DATE: Final[str] = "date"
_TAG_TIME: Final[str] = "time"
_TAG_DECIMAL: Final[str] = "decimal"
_TAG_UUID: Final[str] = "uuid"
_TAG_PATH: Final[str] = "path"
_TAG_DICT: Final[str] = "dict"

#: Maximum nesting depth of a value. The top-level value is depth 0 and every
#: nested ``list`` / ``dict`` increments it; a value at depth > 32 is refused.
MAX_DEPTH: Final[int] = 32

#: Maximum size of a single ``bytes`` leaf, measured on the decoded payload.
MAX_BYTES_LEAF: Final[int] = 8 * 1024 * 1024

#: Maximum size of the encoded envelope, measured as the UTF-8 JSON bytes the
#: backend's ``JSONSerializer`` would produce.
MAX_ENCODED_BYTES: Final[int] = 16 * 1024 * 1024

#: Decoders for the string-payload tags. Unknown tags are rejected before a
#: lookup; every parser raises ``ValueError`` (or ``ArithmeticError`` for
#: :class:`~decimal.Decimal`) on malformed input, which :meth:`CacheCodec.decode`
#: converts to :class:`~tortoise_extended.exceptions.CacheSerializationError`.
_PARSERS: dict[str, Callable[[str], CacheScalar]] = {
    _TAG_BYTES: lambda text: base64.b64decode(text, validate=True),
    _TAG_DATETIME: datetime.fromisoformat,
    _TAG_DATE: date.fromisoformat,
    _TAG_TIME: time.fromisoformat,
    _TAG_DECIMAL: Decimal,
    _TAG_UUID: UUID,
    _TAG_PATH: PurePath,
}

#: Field classes whose Python values the codec round-trips. Keyed on the field
#: **class** (``isinstance``, so subclasses qualify) — never on ``field_type``.
#: ``TortoiseCharField`` and :class:`~tortoise_extended.fields.char.CharField`
#: both appear: the repo field subclasses ``Field`` directly, not the upstream
#: class, so the upstream entry does not cover it.
_CACHEABLE_FIELD_CLASSES = (
    TortoiseCharField,
    TextField,
    IntField,
    BigIntField,
    SmallIntField,
    FloatField,
    BooleanField,
    DecimalField,
    DatetimeField,
    DateField,
    TimeField,
    UUIDField,
    BinaryField,
    JSONField,
    CharField,
    CitextField,
    PathField,
    URLField,
    MacAddrField,
    MacAddr8Field,
    LTreeField,
    UUID4Field,
    UUID7Field,
)

#: Field classes refused even though an allowlist entry might otherwise match.
#: ``CharEnumFieldInstance`` subclasses ``CharField`` and
#: ``IntEnumFieldInstance`` subclasses ``SmallIntField``, so these must be
#: checked **before** the allowlist. Tortoise 1.1.9 exports no ``BaseEnumField``
#: class — the two instance classes are the enum fields of this version, and
#: ``IntEnumField`` / ``CharEnumField`` are factory functions, not classes, so
#: they cannot appear in an ``isinstance`` tuple.
_DENIED_FIELD_CLASSES = (
    RelationalField,
    CharEnumFieldInstance,
    IntEnumFieldInstance,
    IPv4Field,
    InetField,
    TimeDeltaField,
    VectorField,
    PGPEncryptedField,
)


class CacheCodec:
    """Encode Python values into a tagged JSON envelope and back.

    The envelope is unambiguous without ``pickle``: every non-JSON Python type
    carries a ``{"__t": ..., "v": ...}`` tag, and every mapping is tagged as
    ``"dict"`` so a user dict can never impersonate an envelope.

    :meth:`supports` is a **construction-time** gate owned by the cache-field
    wrapper: a field whose inner type is not supported is refused when the
    wrapper is built. It is not a write-time check — :meth:`encode` never
    consults it, and a value of an unsupported type reaching :meth:`encode`
    raises :class:`~tortoise_extended.exceptions.CacheSerializationError` on
    its own.
    """

    def encode(self, value: CacheScalar) -> CacheValue:
        """Encode *value* into a JSON-safe tagged envelope.

        Args:
            value: A supported Python value. Supported types are ``str``,
                ``int``, ``float``, ``bool``, ``None``, ``bytes``,
                ``datetime``, ``date``, ``time``, ``Decimal``, ``UUID`` and
                ``pathlib.PurePath``, plus ``list`` and ``dict`` recursively.

        Returns:
            The tagged envelope: bare scalars stay bare, every other supported
            value becomes a ``{"__t": ..., "v": ...}`` mapping, and
            ``list`` / ``dict`` recurse.

        Raises:
            CacheSerializationError: When *value* (or a nested element) has an
                unsupported type, a non-``str`` mapping key, a ``bytes`` leaf
                larger than 8 MiB decoded, a nesting depth beyond 32, or when
                the encoded envelope exceeds 16 MiB.
        """
        encoded = self._encode(value, 0)
        size = len(json.dumps(encoded, ensure_ascii=False).encode())
        if size > MAX_ENCODED_BYTES:
            msg = (
                f"encoded cache envelope is {size} bytes, over the "
                f"{MAX_ENCODED_BYTES}-byte cap"
            )
            raise CacheSerializationError(msg)
        return encoded

    def decode(self, value: CacheValue) -> CacheScalar:
        """Decode a tagged envelope back into Python values.

        Bare scalars are returned unchanged. An untagged mapping is accepted as
        plain data and its values are decoded recursively — :meth:`encode`
        always tags mappings, so untagged ones only come from hand-written
        payloads.

        Args:
            value: A value previously produced by :meth:`encode` (or an
                equivalent JSON payload).

        Returns:
            The decoded Python value.

        Raises:
            CacheSerializationError: On an unknown or malformed tag, a
                non-``str`` ``v`` for a string-tagged envelope, a non-mapping
                payload for the ``"dict"`` tag, invalid base64, an unparseable
                ISO / decimal / UUID / path value, raw ``bytes`` (never part of
                a JSON payload), or nesting deeper than 32.
        """
        return self._decode(value, 0)

    def supports[V](self, inner: Field[V]) -> bool:
        """Return True when *inner* is a field class this codec can round-trip.

        The decision is keyed on the inner field **class** (``isinstance``,
        so subclasses qualify), never on ``Field.field_type`` alone: a
        ``field_type`` is a lie for exactly the interesting cases —
        ``CharEnumFieldInstance`` / ``IntEnumFieldInstance`` declare
        ``field_type = str`` / ``int`` while emitting ``Enum`` members,
        ``IPv4Field`` / ``InetField`` emit ``ipaddress`` objects and
        ``TimeDeltaField`` emits ``timedelta``. A ``field_type``-only allowlist
        would accept fields whose values the codec cannot encode.

        Deny-first ordering matters: ``CharEnumFieldInstance`` subclasses
        ``CharField`` and ``IntEnumFieldInstance`` subclasses ``SmallIntField``,
        so the deny list runs before the allowlist. A False result is a
        **construction-time** refusal owned by the cache-field wrapper — it is
        never a write-time error, and :meth:`encode` does not consult it.

        Args:
            inner: The wrapped field instance (never a field class).

        Returns:
            True for the scalar, text, JSON and repo-specific field classes in
            the module allowlist; False for relational, enum, inet, duration,
            vector, encrypted and unknown fields.
        """
        if isinstance(inner, _DENIED_FIELD_CLASSES):
            return False
        return isinstance(inner, _CACHEABLE_FIELD_CLASSES)

    def _encode(self, value: CacheScalar, depth: int) -> CacheValue:
        if depth > MAX_DEPTH:
            msg = f"cache value nesting exceeds the {MAX_DEPTH}-level depth cap"
            raise CacheSerializationError(msg)
        if value is None:
            return None
        # bool is an int subclass — checked first so the value stays a bool.
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            if len(value) > MAX_BYTES_LEAF:
                msg = (
                    f"bytes value is {len(value)} bytes, over the "
                    f"{MAX_BYTES_LEAF}-byte leaf cap"
                )
                raise CacheSerializationError(msg)
            return {
                _TAG_KEY: _TAG_BYTES,
                _VALUE_KEY: base64.b64encode(value).decode("ascii"),
            }
        # datetime is a date subclass — checked first so the tag is not lost.
        if isinstance(value, datetime):
            return {_TAG_KEY: _TAG_DATETIME, _VALUE_KEY: value.isoformat()}
        if isinstance(value, date):
            return {_TAG_KEY: _TAG_DATE, _VALUE_KEY: value.isoformat()}
        if isinstance(value, time):
            return {_TAG_KEY: _TAG_TIME, _VALUE_KEY: value.isoformat()}
        if isinstance(value, Decimal):
            return {_TAG_KEY: _TAG_DECIMAL, _VALUE_KEY: str(value)}
        if isinstance(value, UUID):
            return {_TAG_KEY: _TAG_UUID, _VALUE_KEY: str(value)}
        if isinstance(value, PurePath):
            return {_TAG_KEY: _TAG_PATH, _VALUE_KEY: str(value)}
        if isinstance(value, list):
            return [self._encode(item, depth + 1) for item in value]
        # Only a dict is left in CacheScalar; anything else that violated the
        # type at runtime is rejected by the mapping encoder's own guard.
        return self._encode_mapping(value, depth)

    def _encode_mapping(self, value: CacheScalar, depth: int) -> CacheValue:
        """Encode a mapping; also the runtime fallback for unsupported values."""
        if not isinstance(value, dict):
            msg = f"unsupported cache value type: {type(value).__name__}"
            raise CacheSerializationError(msg)
        # ``CacheScalar`` declares ``dict[str, CacheScalar]``; the widened view
        # keeps the non-str key check expressible (a str-typed key would make
        # ``isinstance(key, str)`` a static no-op).
        widened = cast("dict[CacheValue, CacheScalar]", value)
        encoded: dict[str, CacheValue] = {}
        for key in widened:
            if not isinstance(key, str):
                msg = f"cache mapping keys must be str, got {type(key).__name__}"
                raise CacheSerializationError(msg)
            encoded[key] = self._encode(widened[key], depth + 1)
        return {_TAG_KEY: _TAG_DICT, _VALUE_KEY: encoded}

    def _decode(self, value: CacheValue, depth: int) -> CacheScalar:
        if depth > MAX_DEPTH:
            msg = f"cache payload nesting exceeds the {MAX_DEPTH}-level depth cap"
            raise CacheSerializationError(msg)
        if value is None:
            return None
        # bool is an int subclass — checked first so the value stays a bool.
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            msg = "raw bytes are not a JSON cache payload; expected a tagged envelope"
            raise CacheSerializationError(msg)
        if isinstance(value, list):
            return [self._decode(item, depth + 1) for item in value]
        return self._decode_mapping(value, depth)

    def _decode_mapping(self, value: CacheValue, depth: int) -> CacheScalar:
        """Decode an envelope or a plain (untagged) mapping."""
        if not isinstance(value, dict):
            msg = f"unsupported cache payload type: {type(value).__name__}"
            raise CacheSerializationError(msg)
        if _TAG_KEY not in value:
            return {key: self._decode(item, depth + 1) for key, item in value.items()}
        tag = value[_TAG_KEY]
        if not isinstance(tag, str):
            msg = f"cache envelope tag must be str, got {type(tag).__name__}"
            raise CacheSerializationError(msg)
        if len(value) != 2 or _VALUE_KEY not in value:
            msg = (
                f"malformed cache envelope {tag!r}: expected exactly "
                f"{_TAG_KEY!r} and {_VALUE_KEY!r}"
            )
            raise CacheSerializationError(msg)
        payload = value[_VALUE_KEY]
        if tag == _TAG_DICT:
            if not isinstance(payload, dict):
                msg = (
                    f"cache envelope {tag!r} expects a mapping payload, got "
                    f"{type(payload).__name__}"
                )
                raise CacheSerializationError(msg)
            return {key: self._decode(item, depth + 1) for key, item in payload.items()}
        if not isinstance(payload, str):
            msg = (
                f"cache envelope {tag!r} expects a string payload, got "
                f"{type(payload).__name__}"
            )
            raise CacheSerializationError(msg)
        parser = _PARSERS.get(tag)
        if parser is None:
            msg = f"unknown cache envelope tag {tag!r}"
            raise CacheSerializationError(msg)
        try:
            return parser(payload)
        except (ValueError, ArithmeticError) as exc:
            msg = f"malformed {tag!r} cache payload: {payload!r}"
            raise CacheSerializationError(msg) from exc
