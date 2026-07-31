"""Tests for monkey-patch registration and idempotency.

Verifies that importing ``tortoise_extended`` registers the custom field
types, index types, and filters on tortoise-orm, and that re-applying the
patches never double-wraps. No database connection required.
"""

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
)
from tortoise_extended.fields.ltree_field import LTreeField
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
# Idempotency
# ---------------------------------------------------------------------------


class TestPatchIdempotency:
    """Re-applying patches should never double-wrap."""

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
