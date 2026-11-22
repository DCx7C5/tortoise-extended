"""Graph node base model for hierarchical data.

Provides BaseGraphNodeModel base class for graph traversal with adjacency list
pattern.
Requires: PostgreSQL + Tortoise ORM

Usage::

    from tortoise import models, fields
    from tortoise_extended.models.graph_node import BaseGraphNodeModel

    class Category(BaseGraphNodeModel):
        name = fields.CharField(max_length=100)
        parent_id = fields.UUIDField(null=True)
        depth = fields.IntField(default=0)

        class Meta:
            table = "categories"
            indexes = (("namespace", "depth"),)
"""

from typing import TYPE_CHECKING
from uuid import UUID

from tortoise_extended.fields.uuid import UUID4Field
from tortoise_extended.models.node import BaseNode


class BaseGraphNodeModel(BaseNode):
    """Deprecated compatibility alias for :class:`BaseNode` pinned to UUID4.

    .. deprecated::
        Subclass :class:`~tortoise_extended.models.node.BaseNode` (UUID7
        identity) or compose :class:`~tortoise_extended.models.node.NodeMixin`
        with your own identity base instead.  This alias stays for backwards
        compatibility, emits no runtime warning, and keeps the legacy UUID4
        key generation.

    Base class for graph nodes with adjacency list pattern.

    Features:
    - UUID primary key for global uniqueness
    - parent_id for adjacency list traversal
    - depth for hierarchy levels
    - is_root flag for identifying root nodes
    - child_count for denormalized degree tracking
    - namespace for multi-tenancy

    Orphan policy:
    ``parent_id`` is a bare column with no ``ON DELETE`` clause.  Deleting a
    node leaves its children (and any edges in ``BaseGraphEdgeModel``
    subclasses that reference it) in place with a dangling ``parent_id``.
    This is deliberate for polymorphic graphs where the parent and child
    types may differ, so no automatic cascade is performed.  Set
    ``_block_orphan_delete = True`` on a concrete subclass to make
    ``delete()`` raise
    :class:`~tortoise_extended.exceptions.GraphError` instead when the node
    still has children.

    Usage::

        class Category(BaseGraphNodeModel):
            name = fields.CharField(max_length=100)

            class Meta:
                table = "categories"
                indexes = (("namespace", "depth"),)

        # Create root
        root = await Category.create(name="Electronics")

        # Create child (parent is a bare ``parent_id`` column — no relation)
        child = await Category.create(name="Laptops", parent_id=root.pk)
    """

    if TYPE_CHECKING:
        # Match BaseNode's value-typed ``id`` declaration; the legacy UUID4
        # field object is still assigned at runtime in the ``else`` branch.
        id: UUID
    else:
        id = UUID4Field(
            primary_key=True,
            description="Unique identifier for the node",
        )

    class Meta:
        abstract = True
