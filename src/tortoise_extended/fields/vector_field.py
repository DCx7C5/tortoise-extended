"""Re-implemented VectorField for pgvector.

Self-contained — does NOT import from tortoise-embeddings.
No monkey-patch conflicts.
"""

import struct
from typing import Unpack, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class VectorField(Field[list[float]]):
    """PostgreSQL vector column for pgvector similarity search.

    Stores fixed-dimension float vectors. Requires the ``vector`` extension
    to be created in the database.

    :param dimensions: Number of dimensions in the vector.
    :param null: Allow NULL values.
    :param default: Default vector value.
    :param description: Column comment.

    Usage::

        class Chunk(Model):
            embedding = VectorField(dimensions=1536)
    """

    SQL_TYPE = "vector"
    indexable = True

    class _db_postgres:
        def __init__(self, field: VectorField) -> None:
            self.field = field

        @property
        def SQL_TYPE(self) -> str:
            if self.field.dimensions:
                return f"vector({self.field.dimensions})"
            return "vector"

    class _db_sqlite:
        SQL_TYPE = "BLOB"
        skip_to_python_if_native = False

    def __init__(
        self,
        dimensions: int | None = None,
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
        self.dimensions = dimensions

    @override
    def to_db_value(
        self, value: list[float] | None, instance: Model | None
    ) -> list[float] | None:
        """Convert a Python vector to the database representation.

        The vector is stored as a plain float list; pgvector's asyncpg codec
        serializes it to the wire format at execution time.

        :param value: The vector value, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: A copy of the vector, or ``None``.
        """
        if value is None:
            return None
        return list(value)

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
            # pgvector binary layout: 4-byte header + N * 4-byte floats.
            return self._decode_binary(value.tobytes())
        if isinstance(value, bytes):
            return self._decode_binary(value)
        return list(value)

    @staticmethod
    def _decode_binary(data: bytes) -> list[float]:
        """Decode pgvector binary format: 4-byte header + N * 4-byte floats."""
        if len(data) < 4:
            return []
        # Header: 2 bytes reserved, 2 bytes dimensions (big-endian)
        ndim = struct.unpack_from(">H", data, 2)[0]
        if ndim == 0:
            return []
        # Data starts at offset 4: ndim * 4-byte big-endian floats
        return list(struct.unpack_from(f">{ndim}f", data, 4))

    @override
    def __repr__(self) -> str:
        return f"VectorField(dimensions={self.dimensions})"
