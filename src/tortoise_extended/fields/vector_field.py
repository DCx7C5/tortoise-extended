"""Re-implemented VectorField for pgvector.

Self-contained — does NOT import from tortoise-embeddings.
No monkey-patch conflicts.
"""

import struct
from typing import Any, override

from tortoise.fields.base import Field


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
        default: Any = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            null=null,
            default=default,
            description=description,
            **kwargs,
        )
        self.dimensions = dimensions

    @override
    def to_db_value(self, value: list[float] | None, instance: Any) -> list[float] | None:
        if value is None:
            return None
        return list(value)

    @override
    def to_python_value(self, value: Any) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, list):
            return value
        # asyncpg returns a string like "[0.1,0.2,0.3]"
        if isinstance(value, str):
            return [float(x) for x in value.strip("[]").split(",") if x]
        if isinstance(value, memoryview):
            return self._decode_binary(bytes(value))
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
