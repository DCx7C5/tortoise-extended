"""BigInt-keyed graph node base: integer adjacency list + UUID7 cross-table id.

Combines :class:`~tortoise_extended.models.unified.UnifiedIdModel` (a 64-bit
``id`` primary key plus a time-ordered UUID7 ``uid``) with
:class:`~tortoise_extended.models.node.NodeMixin` (the adjacency-list fields
and traversal helpers) — the BigInt-keyed hybrid ``NodeMixin`` documents but
:class:`~tortoise_extended.models.node.BaseNode` does not ship.

What it buys over :class:`~tortoise_extended.models.node.BaseNode`: the
primary key and every ``parent_id`` link stay 64-bit integers, so adjacency
lookups and JOINs are compact and index-friendly.  What it costs: every insert
also writes the ``uid`` unique index, and each row carries two identities —
``id`` is the internal FK target, ``uid`` the cross-table / external reference
(see :class:`~tortoise_extended.models.unified.UnifiedIdModel`).

Requires: PostgreSQL + Tortoise ORM

Usage::

    from tortoise_extended.models.unified_node import BaseUnifiedNode

    class Category(BaseUnifiedNode):
        class Meta:
            table = "categories"
            indexes = (("namespace", "depth"),)

    root = await Category.create(namespace="shop", is_root=True)
    child = await root.add_child()
    assert isinstance(child.parent_id, int)  # BigInt adjacency list
    assert child.uid != root.uid  # UUID7 cross-table identifier
"""

from typing import TYPE_CHECKING

from tortoise import fields

from .node import NodeMixin
from .unified import UnifiedIdModel


class BaseUnifiedNode(UnifiedIdModel, NodeMixin):
    """Abstract base: BigInt pk + UUID7 ``uid`` + adjacency-list traversal.

    Layers :class:`~tortoise_extended.models.node.NodeMixin` (adjacency-list
    fields and helpers) over
    :class:`~tortoise_extended.models.unified.UnifiedIdModel` (BigInt ``id``
    plus UUID7 ``uid``).  This is the BigInt-keyed node the mixin is designed
    for: ``parent_id`` is a ``BigIntField``, so tree links are integer JOINs,
    while ``uid`` still gives every row a globally unique, time-ordered
    identifier for cross-table references.

    Concrete subclasses **must** declare ``Meta.table`` and redeclare
    ``Meta.indexes`` — Tortoise does not propagate ``Meta.indexes`` from
    abstract bases, and ``NodeMixin.__init_subclass__`` raises at class
    creation when it is missing.  ``parent_id`` is declared here, so
    subclasses never repeat it.

    Attributes:
        id: 64-bit auto-increment primary key (inherited from ``BaseModel``).
        uid: Time-ordered UUID7, unique + indexed (inherited from
            ``UnifiedIdModel``); use it for cross-table / external references.
        parent_id: 64-bit parent key (``NULL`` for a root), indexed for
            adjacency-list lookups.
        depth: Hierarchy depth level (root = 0).
        is_root: ``True`` when this node has no parent.
        child_count: Denormalized count of direct children.
        namespace: Multi-tenancy namespace (default ``"default"``).
        metadata_json: Arbitrary JSON metadata for the node.
    """

    if TYPE_CHECKING:
        # The mixin annotates ``parent_id`` as ``UUID | int | None``; narrow it
        # to this base's concrete key type.  The runtime field lives in the
        # ``else`` branch so Tortoise still registers the column.
        parent_id: int | None
    else:
        parent_id = fields.BigIntField(
            description="Parent node ID for adjacency list traversal",
            null=True,
            db_index=True,
        )

    class Meta:
        abstract = True
        # Tortoise does not propagate Meta.indexes from abstract bases, so
        # every concrete subclass must redeclare this pair (NodeMixin's
        # __init_subclass__ guard enforces it on concrete classes).  Repeated
        # here to document the index the base expects.
        indexes = (("namespace", "depth"),)
