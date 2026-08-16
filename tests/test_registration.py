"""Tests for monkey-patch registration and idempotency.

Verifies that importing ``tortoise_extended`` registers the custom field
types, index types, and filters on tortoise-orm, and that re-applying the
patches never double-wraps. No database connection required.
"""

from collections.abc import Callable

import tortoise.filters as filters_mod
import tortoise.indexes as indexes_mod
from tortoise.backends.asyncpg.client import AsyncpgDBClient
from tortoise.migrations.writer import MigrationWriter

from tortoise_extended import (
    GiSTIndex,
    HNSWIndex,
    IVFFlatIndex,
    VectorField,
    _apply_patches,
    _combined_codec_init,
    _decode_vector,
    _encode_vector,
    _pgvector_codec_init,
)
from tortoise_extended._types import AsyncpgConnection
from tortoise_extended.fields.ltree import LTreeField
from tortoise_extended.migrations.operations import _patch_format_operation


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestFieldAndIndexRegistration:
    """Custom types should be importable from the patched tortoise modules."""

    def test_vector_field_registered(self) -> None:
        """tortoise.fields.VectorField should point at our class."""
        import tortoise.fields as fields_mod

        assert fields_mod.VectorField is VectorField

    def test_index_types_registered(self) -> None:
        """tortoise.indexes should expose all three custom index classes."""
        assert indexes_mod.HNSWIndex is HNSWIndex
        assert indexes_mod.IVFFlatIndex is IVFFlatIndex
        assert indexes_mod.GiSTIndex is GiSTIndex


class TestFilterRegistration:
    """get_filters_for_field should dispatch custom fields."""

    def test_vector_field_filters(self) -> None:
        """VectorField filters should include the distance operators."""
        filters = filters_mod.get_filters_for_field(
            "embedding", VectorField(dimensions=3), "embedding"
        )
        assert "embedding__l2_distance" in filters
        assert "embedding__cosine_distance" in filters
        assert "embedding__inner_product" in filters

    def test_ltree_field_filters(self) -> None:
        """LTreeField filters should include the ltree operators."""
        filters = filters_mod.get_filters_for_field(
            "path", LTreeField(max_length=1024), "path"
        )
        assert "path__ancestor_of" in filters
        assert "path__descendant_of" in filters
        assert "path__match" in filters
        assert "path__in" in filters

    def test_regular_field_untouched(self) -> None:
        """Regular fields should still use the standard filter set."""
        from tortoise.fields import CharField

        filters = filters_mod.get_filters_for_field(
            "name", CharField(max_length=50), "name"
        )
        assert "name" in filters
        assert "name__icontains" in filters


# ---------------------------------------------------------------------------
# pgvector codec helpers — every branch of the module-level helpers
# ---------------------------------------------------------------------------


class _CodecConn:
    """In-memory ``AsyncpgConnection`` double that records codec setup calls.

    ``probe_result`` controls the value returned by the schema probe;
    ``probe_error`` makes the probe raise instead. Both knobs keep the
    double protocol-compatible with ``AsyncpgConnection`` while letting the
    tests exercise every branch of ``_pgvector_codec_init``.
    """

    def __init__(
        self,
        *,
        probe_result: str | None = None,
        probe_error: type[Exception] | None = None,
    ) -> None:
        self.calls: list[
            tuple[
                str,
                Callable[[list[float] | str | None], str],
                Callable[[str], list[float]],
                str,
            ]
        ] = []
        self._probe_result = probe_result
        self._probe_error = probe_error

    async def set_type_codec(
        self,
        typename: str,
        *,
        schema: str,
        encoder: Callable[[list[float] | str | None], str],
        decoder: Callable[[str], list[float]],
    ) -> None:
        self.calls.append((typename, encoder, decoder, schema))

    async def fetchval(self, query: str) -> str | None:
        assert "typname = 'vector'" in query
        if self._probe_error is not None:
            raise self._probe_error("probe failed")
        return self._probe_result


class _NoCodec:
    """A protocol-compatible connection double without codec support.

    Mirrors an asyncpg connection that cannot register custom type codecs:
    both codec hooks raise ``AttributeError``, which ``_pgvector_codec_init``
    swallows so registration is skipped silently.
    """

    async def set_type_codec(
        self,
        typename: str,
        *,
        schema: str,
        encoder: Callable[[list[float] | str | None], str],
        decoder: Callable[[str], list[float]],
    ) -> None:
        raise AttributeError(
            f"cannot register codec for {typename} in schema {schema} "
            f"({encoder.__name__}/{decoder.__name__})"
        )

    async def fetchval(self, query: str) -> str | None:
        raise AttributeError(f"probe failed: {query}")


class TestPgvectorCodecBranches:
    """Branch coverage for the extracted pgvector codec helpers."""

    def test_encode_vector_empty(self) -> None:
        """None and empty lists encode to the empty-vector literal."""
        assert _encode_vector(None) == "[]"
        assert _encode_vector([]) == "[]"

    def test_encode_vector_values(self) -> None:
        """Non-empty lists encode to the bracketed, comma-joined literal."""
        assert _encode_vector([1.5, -2.0, 3.25]) == "[1.5,-2.0,3.25]"

    def test_encode_vector_passthrough(self) -> None:
        """Pre-encoded strings pass through untouched."""
        assert _encode_vector("[1.5,2.0]") == "[1.5,2.0]"

    def test_decode_vector_empty(self) -> None:
        """The empty-vector literal decodes to an empty list."""
        assert _decode_vector("[]") == []

    def test_decode_vector_values(self) -> None:
        """Non-empty literals decode to a list of floats."""
        assert _decode_vector("[1.5, 2.0, 3.25]") == [1.5, 2.0, 3.25]

    async def test_codec_init_skips_without_set_type_codec(self) -> None:
        """Connections that cannot register codecs are skipped silently."""
        await _pgvector_codec_init(_NoCodec())

    async def test_codec_init_survives_codec_errors(self) -> None:
        """Missing-extension and unsupported-connection errors are swallowed."""

        class _Raises:
            def __init__(self, exc_type: type[Exception]) -> None:
                self._exc_type = exc_type

            async def set_type_codec(
                self,
                typename: str,
                *,
                schema: str,
                encoder: Callable[[list[float] | str | None], str],
                decoder: Callable[[str], list[float]],
            ) -> None:
                raise self._exc_type(
                    f"cannot register codec for {typename} in schema {schema} "
                    f"({encoder.__name__}/{decoder.__name__})"
                )

            async def fetchval(self, query: str) -> str | None:
                raise self._exc_type(f"probe failed: {query}")

        await _pgvector_codec_init(_Raises(ValueError))
        await _pgvector_codec_init(_Raises(AttributeError))

    async def test_codec_init_registers_encoder_and_decoder(self) -> None:
        """Codecs are registered on connections that support them."""

        conn = _CodecConn()
        await _pgvector_codec_init(conn)
        assert len(conn.calls) == 2
        names = [call[0] for call in conn.calls]
        assert names == ["vector", "halfvec"]
        name, encoder, decoder, schema = conn.calls[0]
        assert name == "vector"
        assert schema == "public"
        assert encoder([1.5]) == "[1.5]"
        assert decoder("[2.5]") == [2.5]
        # The halfvec codec shares the same text codec.
        halfvec_encoder = conn.calls[1][1]
        halfvec_decoder = conn.calls[1][2]
        assert halfvec_encoder([1.5]) == "[1.5]"
        assert halfvec_decoder("[2.5]") == [2.5]

    async def test_codec_init_resolves_non_public_schema(self) -> None:
        """G18 — the codec is registered in the extension's actual schema."""

        conn = _CodecConn(probe_result="extensions")
        await _pgvector_codec_init(conn)
        assert conn.calls[0][3] == "extensions"

    async def test_codec_init_falls_back_when_probe_fails(self) -> None:
        """A failing schema probe falls back to public, not to a crash."""

        conn = _CodecConn(probe_error=RuntimeError)
        await _pgvector_codec_init(conn)
        assert conn.calls[0][3] == "public"

    async def test_combined_init_without_original(self) -> None:
        """The combined init runs the codec setup with no original callback."""
        await _combined_codec_init(_NoCodec(), None)

    async def test_combined_init_calls_original(self) -> None:
        """The original init callback still runs after the codec setup."""
        calls: list[AsyncpgConnection] = []

        async def original(conn: AsyncpgConnection) -> None:
            calls.append(conn)

        conn = _CodecConn()
        await _combined_codec_init(conn, original)
        assert calls == [conn]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestPatchIdempotency:
    """Re-applying patches should never double-wrap."""

    def test_public_patch_is_callable_and_idempotent(self) -> None:
        """``patch()`` is the explicit entry point and never double-wraps."""
        from tortoise_extended import patch

        assert callable(patch)
        assert patch() is None
        assert filters_mod.get_filters_for_field is not None

    def test_public_patch_keeps_wrapper_stable(self) -> None:
        """Calling ``patch()`` after import keeps the existing wrappers."""
        from tortoise_extended import patch

        filter_wrapper = filters_mod.get_filters_for_field
        pool_wrapper = AsyncpgDBClient.create_pool
        patch()
        patch()
        assert filters_mod.get_filters_for_field is filter_wrapper
        assert AsyncpgDBClient.create_pool is pool_wrapper

    def test_patch_exported_in_all(self) -> None:
        """``patch`` is part of the public API surface and re-exported."""
        from tortoise_extended import patch

        import tortoise_extended

        assert "patch" in tortoise_extended.__all__
        assert tortoise_extended.patch is patch

    def test_patch_patches_models_local_reference(self) -> None:
        """``patch()`` keeps ``tortoise.models`` aligned with ``tortoise.filters``."""
        import tortoise.models as models_mod
        from tortoise_extended import patch

        patch()
        assert models_mod.get_filters_for_field is filters_mod.get_filters_for_field

    def test_get_filters_patch_applied_once(self) -> None:
        """Re-running _apply_patches keeps the same wrapper object."""
        original = filters_mod.get_filters_for_field
        _apply_patches()
        _apply_patches()
        assert filters_mod.get_filters_for_field is original

    def test_create_pool_patch_applied_once(self) -> None:
        """Re-running _apply_patches keeps the same create_pool wrapper."""
        assert AsyncpgDBClient._tortoise_extended_codec_patched is True
        first = AsyncpgDBClient.create_pool
        _apply_patches()
        assert AsyncpgDBClient.create_pool is first

    def test_format_operation_patch_applied_once(self) -> None:
        """Re-running _patch_format_operation keeps the same wrapper."""
        original = MigrationWriter._format_operation
        _patch_format_operation()
        _patch_format_operation()
        assert MigrationWriter._format_operation is original
        assert MigrationWriter._tortoise_extended_format_patched is True
