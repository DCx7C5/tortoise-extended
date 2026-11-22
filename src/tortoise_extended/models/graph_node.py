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

import warnings
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
        compatibility and keeps the legacy UUID4 key generation, but
        subclassing it emits a :class:`DeprecationWarning` at class-creation
        time.

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

    def __init_subclass__(cls, **kwargs: str | int | float | bool | None) -> None:
        """Warn that this compatibility alias is deprecated.

        Emitted from subclass creation — never at class-definition time — so
        importing :mod:`tortoise_extended` warns nobody who does not subclass
        the alias.  The warning is a plain :func:`warnings.warn` call, so
        :func:`warnings.simplefilter` applies as usual: a test suite running
        under ``filterwarnings = ["error"]`` must handle it where the subclass
        is declared.

        Args:
            **kwargs: Class keyword arguments forwarded to
                :meth:`NodeMixin.__init_subclass__`, which enforces the
                ``Meta.indexes`` / ``parent_id`` contract.

        Raises:
            NotImplementedError: From :meth:`NodeMixin.__init_subclass__` when
                a concrete subclass omits ``Meta.indexes`` or ``parent_id``.
        """
        super().__init_subclass__(**kwargs)
        warnings.warn(
            "BaseGraphNodeModel is deprecated: subclass "
            "tortoise_extended.models.node.BaseNode (UUID7 identity) or "
            "compose tortoise_extended.models.node.NodeMixin with your own "
            "identity base instead. This alias keeps the legacy UUID4 primary "
            "key and will be removed in a future release.",
            DeprecationWarning,
            # Frames: 1 = this method, 2 = tortoise's ``ModelMeta.__new__``
            # (which calls ``super().__new__``), 3 = the module declaring the
            # subclass — the frame the warning must point at.
            stacklevel=3,
        )

    class Meta:
        abstract = True
