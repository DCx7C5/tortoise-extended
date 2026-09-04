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

    @override
    def to_db_value(
        self, value: list[float] | None, instance: type[Model] | Model | None
    ) -> list[float] | None:
        """Convert a Python vector to the database representation.

        The vector is passed through as a plain float list; the pgvector
        asyncpg codec serializes it to the wire format at execution time.

        :param value: The vector value, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The float list, or ``None``.
        """
        if value is None:
            return None
        return list(value)

    @staticmethod
    def _encode_binary(values: list[float], vector_type: str = "vector") -> bytes:
        """Encode a float list into the pgvector binary layout.

        Mirrors :meth:`_decode_binary`: 4-byte big-endian header (2 bytes
        length, 2 bytes dimensions) followed by big-endian elements —
        4-byte floats for ``vector``, 2-byte half-precision floats for
        ``halfvec``.

        :param values: The vector values.
        :param vector_type: Column type — ``"vector"`` or ``"halfvec"``.
        :returns: The encoded binary value.
        """
        ndim = len(values)
        if vector_type == "halfvec":
            body = struct.pack(f">{ndim}e", *values)
        else:
            body = struct.pack(f">{ndim}f", *values)
        return struct.pack(">HH", 4 + len(body), ndim) + body

    @override
    def to_python_value(
        self, value: list[float] | str | bytes | memoryview | tuple[float, ...] | None
    ) -> list[float] | None:
        """Convert a database value to a Python ``list[float]``.

        Accepts every format the supported drivers produce: asyncpg's
        ``"[0.1,0.2]"`` text form, the pgvector binary layout (bytes /
        memoryview, shared by the asyncpg binary codec and the SQLite BLOB
        fallback), or a native list/tuple.

        :param value: Raw value from the driver, or ``None``.
        :returns: A float list, or ``None``.
        """
        if value is None:
            return None
        if isinstance(value, list):
            return value
        # asyncpg returns a string like "[0.1,0.2,0.3]"
        if isinstance(value, str):
            return [float(x) for x in value.strip("[]").split(",") if x]
        if isinstance(value, memoryview):
            # SQLite BLOB fallback and asyncpg binary codec share the
            # pgvector binary layout: 4-byte header + N elements.
            return self._decode_binary(value.tobytes(), self.vector_type)
        if isinstance(value, bytes):
            return self._decode_binary(value, self.vector_type)
        return list(value)

    @staticmethod
    def _decode_binary(data: bytes, vector_type: str = "vector") -> list[float]:
        """Decode pgvector binary format: 4-byte header + N elements.

        ``vector`` columns store 4-byte big-endian floats; ``halfvec``
        columns store 2-byte big-endian half-precision floats (struct ``e``).

        :param data: Raw binary value.
        :param vector_type: Column type — ``"vector"`` or ``"halfvec"``.
        :returns: A float list.
        :raises VectorFieldError: If the header declares more elements than
            the payload contains (truncated data).
        """
        if len(data) < 4:
            return []
        # Header: 2 bytes reserved, 2 bytes dimensions (big-endian)
        ndim = struct.unpack_from(">H", data, 2)[0]
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
