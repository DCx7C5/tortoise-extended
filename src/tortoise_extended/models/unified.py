"""Abstract base model with BigInt pk + UUID7 unified identifier.

Extends :class:`~tortoise_extended.models.base.BaseModel` with a ``uid``
column (:class:`~tortoise_extended.fields.uuid.UUID7Field`) — a time-ordered
UUID7 for cross-table and external references.  The BigInt ``id`` remains the
internal FK target; ``uid`` is the globally unique reference used when a
foreign key must point at "any table" without a shared-sequence coordination
burden.

Design rationale (plan.md §F):
    Per-table BigInt auto-increment collides (``FileNode`` and ``User`` can
    both have ``id=42``), so a plain ``source_id``/``ref_id`` column cannot
    safely point at an arbitrary table.  ``UUID7Field`` solves this:

    * Globally unique — no shared-sequence coordination.
    * Time-ordered — B-tree locality, range scans, hypertable-friendly.
    * Zero new deps — Python 3.14 ``uuid.uuid7()`` / PG 18 ``uuidv7()``.

Boundary rule (plan.md §F):
    ``id`` = internal FK target (fast integer JOINs within a single table
    family).  ``uid`` = cross-table / external references (graph edges,
    polymorphic refs, cache keys, API identifiers).  ``uid`` is **opt-in**
    — forcing it on every model adds a unique-index write per insert on hot
    tables and introduces two-ID confusion on internal-only tables.

Usage::

    from tortoise import fields
    from tortoise_extended.models.unified import UnifiedIdModel

    class Document(UnifiedIdModel):
        title = fields.CharField(max_length=200)

        class Meta:
            table = "documents"

    # ``id`` is auto-generated BigInt; ``uid`` is auto-generated UUID7.
    doc = await Document.create(title="Design Doc")
    assert isinstance(doc.id, int)
    assert isinstance(doc.uid, UUID)

    # Cross-table reference uses ``uid``, not ``id``:
    # source_id = doc.uid  (UUID7 — globally unique)
"""

from tortoise_extended.fields.uuid import UUID7Field

from .base import BaseModel


class UnifiedIdModel(BaseModel):
    """Abstract base: BigInt pk + UUID7 unified id for cross-table refs.

    Inherits :class:`~tortoise_extended.models.base.BaseModel`'s
    ``id = BigIntField(primary_key=True)`` and adds a ``uid`` column — a
    time-ordered UUID7 for use as a globally unique cross-table identifier.

    Attributes:
        id: 64-bit auto-increment primary key (inherited from ``BaseModel``).
        uid: Time-ordered UUID7, unique + indexed.  Use this for
            cross-table foreign keys, polymorphic references, cache keys,
            and external/public identifiers.
    """

    uid = UUID7Field(
        unique=True,
        description="Time-ordered UUID7 for cross-table / external references",
    )

    class Meta:
        abstract = True
