"""Re-implemented VectorField for pgvector.

Self-contained — does NOT import from tortoise-embeddings.
No monkey-patch conflicts.
"""

import struct
from typing import Literal, Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs
from tortoise_extended.exceptions import VectorFieldError

# The live asyncpg codec is TEXT (see ``tortoise_extended.__init__``): the
# ``_encode_binary`` / ``_decode_binary`` helpers below are the field-level
# fallback/test path and a possible future opt-in — no binary codec is
# registered, and there is no configuration knob to enable one.

VectorType = Literal["vector", "halfvec"]
"""Supported pgvector column types.

``halfvec`` stores half-precision (2-byte) floats — ~2x storage savings for
a modest precision trade-off, identical ``list[float]`` Python API.
"""


class VectorField(Field[list[float]]):
    """PostgreSQL vector column for pgvector similarity search.

    Stores fixed-dimension float vectors. Requires the ``vector`` extension
    to be created in the database.

    :param dimensions: Number of dimensions in the vector.
    :param vector_type: ``"vector"`` (full precision, default) or
        ``"halfvec"`` (half-precision 2-byte floats). Both expose the same
        ``list[float]`` Python value; ``halfvec`` halves storage.
    :param null: Allow NULL values.
    :param default: Default vector value.
    :param description: Column comment.

    Usage::

        class Chunk(Model):
            embedding = VectorField(dimensions=1536)

        class CompactChunk(Model):
            embedding = VectorField(dimensions=1536, vector_type="halfvec")
    """

    SQL_TYPE = "vector"
    indexable = True
    vector_type: VectorType = "vector"

    class _db_postgres:
        def __init__(self, field: VectorField) -> None:
            self.field = field

        @property
        def SQL_TYPE(self) -> str:
            if self.field.dimensions:
                return f"{self.field.vector_type}({self.field.dimensions})"
            return self.field.vector_type

    def __init__(
        self,
        dimensions: int | None = None,
        *,
        vector_type: VectorType = "vector",
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
        self.dimensions = dimensions
        self.vector_type = vector_type

    def _field_label(self) -> str:
        """Return the name used for this field in error messages.

        The bound model field name when Tortoise has attached the field to a
        model, otherwise the bare class name.
        """
        return self.model_field_name or "VectorField"

    @override
    def to_db_value(
        self, value: list[float] | None, instance: type[Model] | Model | None
    ) -> list[float] | None:
        """Convert a Python vector to the database representation.

        The vector is passed through as a plain float list; the pgvector
        asyncpg codec serializes it to the wire format at execution time.
        When ``dimensions`` is set, a value of a different length is rejected
        before any SQL is sent; an unconstrained field (``dimensions=None``)
        and ``None`` pass through unchanged.

        :param value: The vector value, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The float list, or ``None``.
        :raises VectorFieldError: If ``dimensions`` is set and the value does
            not have exactly that many elements.
        """
        if value is None:
            return None
        values = list(value)
        if self.dimensions is not None and len(values) != self.dimensions:
            raise VectorFieldError(
                f"{self._field_label()}: expected a vector with "
                f"{self.dimensions} dimensions, got {len(values)}"
            )
        return values

    @staticmethod
    def _encode_binary(values: list[float], vector_type: str = "vector") -> bytes:
        """Encode a float list into the pgvector binary wire layout.

        Mirrors :meth:`_decode_binary` and pgvector's ``vector_send`` /
        ``halfvec_send``: a 4-byte big-endian header (``int16`` dimension
        count, ``int16`` unused) followed by big-endian elements — 4-byte
        floats for ``vector``, 2-byte half-precision floats for ``halfvec``.

        The live asyncpg codec is TEXT (see :mod:`tortoise_extended`): these
        helpers are the field-level fallback/test path and a possible future
        opt-in — no binary codec is registered.

        :param values: The vector values.
        :param vector_type: Column type — ``"vector"`` or ``"halfvec"``.
        :returns: The encoded binary value.
        """
        ndim = len(values)
        if vector_type == "halfvec":
            body = struct.pack(f">{ndim}e", *values)
        else:
            body = struct.pack(f">{ndim}f", *values)
        return struct.pack(">HH", ndim, 0) + body

    @override
    def to_python_value(
        self, value: list[float] | str | bytes | memoryview | tuple[float, ...] | None
    ) -> list[float] | None:
        """Convert a database value to a Python ``list[float]``.

        Accepts every format the supported drivers can produce: asyncpg's
        ``"[0.1,0.2]"`` text form (the live codec), the pgvector binary
        layout (bytes / memoryview, only when a binary codec is registered —
        the shipped codec is text), or a native list/tuple.

        :param value: Raw value from the driver, or ``None``.
        :returns: A float list, or ``None``.
        :raises VectorFieldError: If a string literal cannot be parsed or
            binary data is truncated.
        """
        if value is None:
            return None
        if isinstance(value, list):
            return value
        # asyncpg returns a string like "[0.1,0.2,0.3]" under the text codec
        if isinstance(value, str):
            values: list[float] = []
            for token in value.strip().strip("[]").split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    values.append(float(token))
                except (ValueError, TypeError) as exc:
                    raise VectorFieldError(
                        f"{self._field_label()}: cannot parse vector literal "
                        f"{value!r} (offending token {token!r})"
                    ) from exc
            return values
        if isinstance(value, memoryview):
            # Binary fallback: pgvector's send layout (dim, unused, elements).
            return self._decode_binary(value.tobytes(), self.vector_type)
        if isinstance(value, bytes):
            return self._decode_binary(value, self.vector_type)
        return list(value)

    @staticmethod
    def _decode_binary(data: bytes, vector_type: str = "vector") -> list[float]:
        """Decode the pgvector binary wire layout: header + N elements.

        Mirrors pgvector's ``vector_send`` / ``halfvec_send``: the 4-byte
        big-endian header is ``int16`` dimension count then ``int16`` unused;
        the body is big-endian elements — 4-byte floats for ``vector``,
        2-byte half-precision floats for ``halfvec``.

        The live asyncpg codec is TEXT (see :mod:`tortoise_extended`); this is
        the field-level fallback/test path, not the registered codec.

        :param data: Raw binary value.
        :param vector_type: Column type — ``"vector"`` or ``"halfvec"``.
        :returns: A float list.
        :raises VectorFieldError: If the header declares more elements than
            the payload contains (truncated data).
        """
        if len(data) < 4:
            return []
        # Header: int16 dimensions, int16 unused (both big-endian)
        ndim = struct.unpack_from(">H", data, 0)[0]
        if ndim == 0:
            return []
        if vector_type == "halfvec":
            element_size = 2
            fmt = "e"
        else:
            element_size = 4
            fmt = "f"
        needed = 4 + ndim * element_size
        if len(data) < needed:
            raise VectorFieldError(
                f"Truncated vector binary data: header declares {ndim} "
                f"dimensions ({needed} bytes required) but only {len(data)} "
                "bytes were provided"
            )
        # Data starts at offset 4: ndim * element_size-byte big-endian floats
        return list(struct.unpack_from(f">{ndim}{fmt}", data, 4))

    @override
    def __repr__(self) -> str:
        return (
            f"VectorField(dimensions={self.dimensions}, "
            f"vector_type={self.vector_type!r})"
        )
