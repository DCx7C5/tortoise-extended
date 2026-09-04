"""Tests for the import-order safety guard.

AGENTS.md Non-Negotiable Rule #1: ``import tortoise_extended`` MUST happen
before ``Tortoise.init()``. Importing the package applies monkey-patches
(``VectorField``, index types, ``get_filters_for_field``, the asyncpg codec)
via ``_apply_patches``. If ``Tortoise.init()`` runs first, those registrations
are silently missed.

These tests exercise the guard in ``_apply_patches`` directly:

- the RAISE branch (tortoise initialized but patches absent),
- the NO-RAISE branch (correct order — not initialized).

Because the rest of the suite imports ``tortoise_extended`` at module load
(so ``tortoise.fields.VectorField`` is already registered), the RAISE branch
is simulated by temporarily removing ``VectorField`` from ``tortoise.fields``
while running inside an isolated ``tortoise_test_context``. The attribute is
always restored (try/finally) so the suite stays intact.
"""

import pytest
import tortoise.fields as _fields_mod
from tortoise import Tortoise
from tortoise.context import tortoise_test_context
from tortoise.models import Model

from tortoise_extended import TortoiseExtendedError, _apply_patches


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
