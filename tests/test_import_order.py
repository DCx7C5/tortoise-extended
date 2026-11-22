"""Tests for the import-order and patch-safety guards.

AGENTS.md Non-Negotiable Rule #1: ``import tortoise_extended`` MUST happen
before ``Tortoise.init()``. Importing the package applies monkey-patches
(``VectorField``, index types, ``get_filters_for_field``, the asyncpg codec)
via ``_apply_patches``. If ``Tortoise.init()`` runs first, those registrations
are silently missed.

These tests exercise the guards in ``_apply_patches`` directly:

- the RAISE branch (tortoise initialized but patches absent),
- the NO-RAISE branch (correct order — not initialized),
- the version guard: supported, boundary, out-of-range and unparseable
  versions (``_check_tortoise_version`` / ``_parse_version`` /
  ``_installed_tortoise_version``),
- the patch-ownership guard: a foreign object in a patched slot raises
  ``PatchConflictError`` instead of silently skipping the patch.

Because the rest of the suite imports ``tortoise_extended`` at module load
(so ``tortoise.fields.VectorField`` is already registered), the RAISE branch
is simulated by temporarily removing ``VectorField`` from ``tortoise.fields``
while running inside an isolated ``tortoise_test_context``. The attribute is
always restored (try/finally) so the suite stays intact.
"""

import importlib.metadata
import re
from types import ModuleType

import pytest
import tortoise
import tortoise.fields as _fields_mod
import tortoise.indexes as _indexes_mod
from tortoise import Tortoise
from tortoise.context import tortoise_test_context
from tortoise.models import Model

from tortoise_extended import (
    MAX_EXCL,
    MIN_VERSION,
    GiSTIndex,
    HNSWIndex,
    IVFFlatIndex,
    PatchConflictError,
    TortoiseExtendedError,
    TortoiseVersionError,
    VectorField,
    _apply_patches,
    _check_tortoise_version,
    _installed_tortoise_version,
    _parse_version,
)


class _GuardProbe(Model):
    """Minimal model so ``tortoise_test_context`` has an app to initialize."""

    id = _fields_mod.IntField(primary_key=True)

    class Meta:
        table = "import_order_guard_probe"


class TestImportOrderGuard:
    """The import-order guard must fire only on a true order violation."""

    def test_no_raise_on_correct_order(self) -> None:
        """Correct order (patch before init) does not raise.

        ``VectorField`` is already registered from the suite's earlier
        import and Tortoise is not initialized in this module's isolated
        scope, so the guard condition is False and calling ``_apply_patches``
        is a no-op.
        """
        assert Tortoise._inited is False
        _apply_patches()  # must not raise
        assert hasattr(_fields_mod, "VectorField")

    async def test_raises_when_init_precedes_patch(self) -> None:
        """Initializing Tortoise before patching raises the guard error.

        The RAISE branch requires both ``Tortoise._inited`` True AND
        ``tortoise.fields.VectorField`` to be absent. The latter never holds
        naturally in-process (the suite already imported the package), so we
        simulate the genuine first-import failure by temporarily removing
        ``VectorField`` and re-invoking ``_apply_patches`` inside an isolated
        ``tortoise_test_context`` (which marks tortoise as initialized).
        """
        saved = _fields_mod.VectorField
        delattr(_fields_mod, "VectorField")
        try:
            async with tortoise_test_context(["tests.test_import_order"]):
                with pytest.raises(
                    TortoiseExtendedError, match=r"BEFORE `Tortoise\.init\(\)`"
                ):
                    _apply_patches()
        finally:
            # Restore so the rest of the suite sees a registered VectorField.
            setattr(_fields_mod, "VectorField", saved)


class TestVersionGuard:
    """The version guard refuses untested tortoise-orm releases."""

    def test_range_constants(self) -> None:
        """The tested range is pinned explicitly, matching pyproject.toml."""
        assert MIN_VERSION == (1, 1, 7)
        assert MAX_EXCL == (1, 2)

    def test_installed_version_prefers_tortoise_attribute(self) -> None:
        """``tortoise.__version__`` exists on this install and is preferred."""
        assert tortoise.__version__ == _installed_tortoise_version()

    def test_falls_back_to_distribution_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A build without ``__version__`` falls back to importlib.metadata."""
        monkeypatch.delattr(tortoise, "__version__")
        assert _installed_tortoise_version() == importlib.metadata.version(
            "tortoise-orm"
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.1.9", (1, 1, 9)),
            ("1.1.7", (1, 1, 7)),
            ("1.1", (1, 1)),
            ("1.2.0rc1", (1, 2, 0)),
            ("1.1.9+local.1", (1, 1, 9)),
            ("1.1.9.dev0", (1, 1, 9)),
            ("unparseable", ()),
            ("", ()),
        ],
    )
    def test_parse_version(self, raw: str, expected: tuple[int, ...]) -> None:
        """Only the leading numeric components are kept; suffixes are ignored."""
        assert _parse_version(raw) == expected

    @pytest.mark.parametrize("raw", ["1.1.7", "1.1.9", "1.1.7rc1", "1.1.9+local"])
    def test_supported_version_passes(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Versions inside the range — including the lower boundary — pass."""
        monkeypatch.setattr(tortoise, "__version__", raw)
        _check_tortoise_version()  # must not raise

    @pytest.mark.parametrize(
        "raw", ["1.0.0", "1.1.6", "2.0.0", "1.2", "1.2.0", "1.2.0rc1"]
    )
    def test_unsupported_version_raises(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Versions outside the range raise with the version in the message."""
        monkeypatch.setattr(tortoise, "__version__", raw)
        with pytest.raises(TortoiseVersionError, match=re.escape(raw)):
            _check_tortoise_version()

    def test_error_names_version_range_and_remedy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message carries the installed version, tested range and remedy."""
        monkeypatch.setattr(tortoise, "__version__", "1.0.0")
        with pytest.raises(TortoiseVersionError) as excinfo:
            _check_tortoise_version()
        message = str(excinfo.value)
        assert "1.0.0" in message
        assert ">=1.1.7,<1.2" in message
        assert "Install a supported tortoise-orm" in message
        assert "upgrade tortoise-extended" in message


class TestPatchOwnershipGuard:
    """A foreign object in a patched slot fails loudly, never silently."""

    def test_reapply_is_idempotent(self) -> None:
        """Re-running the patches on our own objects stays a no-op."""
        _apply_patches()
        _apply_patches()
        assert _fields_mod.VectorField is VectorField
        assert _indexes_mod.HNSWIndex is HNSWIndex
        assert _indexes_mod.IVFFlatIndex is IVFFlatIndex
        assert _indexes_mod.GiSTIndex is GiSTIndex

    @pytest.mark.parametrize(
        ("module", "attribute"),
        [
            (_fields_mod, "VectorField"),
            (_indexes_mod, "HNSWIndex"),
            (_indexes_mod, "IVFFlatIndex"),
            (_indexes_mod, "GiSTIndex"),
        ],
    )
    def test_foreign_definition_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        module: ModuleType,
        attribute: str,
    ) -> None:
        """A sentinel in a patched slot raises instead of silently skipping.

        This calls ``_apply_patches`` directly against the real modules with a
        ``monkeypatch``-owned sentinel — no package reload and no subprocess.
        ``monkeypatch`` restores the real class even when the assertion fails,
        so no later test can observe the sentinel, and the sentinel never
        escapes into ``tortoise.fields`` / ``tortoise.indexes`` state.
        """

        class _ForeignExtension:
            """Stand-in for another extension's class."""

        monkeypatch.setattr(module, attribute, _ForeignExtension)
        with pytest.raises(PatchConflictError, match=attribute):
            _apply_patches()
