"""Graph node base class for hierarchical data.

Provides GraphNode base class for graph traversal with adjacency list pattern.
Requires: PostgreSQL + Tortoise ORM

Usage::

    from tortoise import models, fields
    from tortoise_extended.graph.node import GraphNode

    class Category(GraphNode):
        name = fields.CharField(max_length=100)
        parent_id = fields.UUIDField(null=True)
        depth = fields.IntField(default=0)

        class Meta:
            table = "categories"
"""

from collections import deque
from typing import TYPE_CHECKING, Self, override
from uuid import UUID

from tortoise import fields
from tortoise.models import Model

if TYPE_CHECKING:
    from tortoise.queryset import QuerySet


class GraphNode(Model):
    """Base class for graph nodes with adjacency list pattern.

    Features:
    - UUID primary key for global uniqueness
    - parent_id for adjacency list traversal
    - depth for hierarchy levels
    - is_root flag for identifying root nodes
    - child_count for denormalized degree tracking
    - namespace for multi-tenancy

    Usage::

        class Category(GraphNode):
            name = fields.CharField(max_length=100)

            class Meta:
                table = "categories"

        # Create root
        root = await Category.create(name="Electronics")

        # Create child
        child = await Category.create(name="Laptops", parent=root)
    """

    id = fields.UUIDField(
        primary_key=True,
        description="Unique identifier for the node",
    )
    name = fields.CharField(
        max_length=100,
        description="Human-readable node name",
    )
    parent_id = fields.UUIDField(
        null=True,
        description="Parent node ID for adjacency list traversal",
        db_index=True,
    )
    depth = fields.IntField(
        default=0,
        description="Hierarchy depth level (root=0)",
    )
    is_root: fields.Field[bool] = fields.BooleanField(
        default=False,
        description="True if this is a root node",
    )
    child_count = fields.IntField(
        default=0,
        description="Denormalized count of direct children",
    )
    namespace = fields.CharField(
        max_length=100,
        default="default",
        description="Namespace for multi-tenancy",
        db_index=True,
    )
    metadata_json = fields.JSONField(
        default=dict,
        description="Arbitrary metadata for the node",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="Creation timestamp",
    )
    updated_at = fields.DatetimeField(
        auto_now=True,
        description="Last update timestamp",
    )

    class Meta:
        table = "graph_nodes"
        verbose_name = "Graph Node"
        verbose_name_plural = "Graph Nodes"
        abstract = True

    @override
    def __str__(self) -> str:
        return f"{self.name} ({self.id})"

    @override
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.id}, name={self.name!r})>"

    @property
    def is_leaf(self) -> bool:
        """Check if this is a leaf node (no children)."""
        return self.child_count == 0

    def children(self) -> QuerySet[Self]:
        """Get direct children of this node.

        Returns:
            QuerySet of child nodes ordered by name
        """
        return self.__class__.filter(
            parent_id=self.id,
        ).order_by("name")

    def descendants(self, max_depth: int | None = None) -> QuerySet[Self]:
        """Get descendants of this node by depth range in the same namespace.

        This is a depth-range approximation: it returns nodes strictly deeper
        than this node within the same namespace, excluding the node itself.
        For link-exact traversal use :meth:`subtree`.

        Args:
            max_depth: Maximum depth to traverse (None = unlimited)

        Returns:
            QuerySet of descendant nodes ordered by depth then name
        """
        if max_depth is None:
            max_depth = 1000  # Safety limit
        return self.__class__.filter(
            namespace=self.namespace,
            depth__gt=self.depth,
            depth__lte=self.depth + max_depth,
        ).order_by("depth", "name")

    def ancestors(self) -> QuerySet[Self]:
        """Get ancestors of this node by depth range in the same namespace.

        This is a depth-range approximation: it returns nodes strictly
        shallower than this node within the same namespace, excluding the
        node itself. For the exact root path use :meth:`path_to_root`.

        Returns:
            QuerySet of ancestor nodes ordered by depth ascending
        """
        return self.__class__.filter(
            namespace=self.namespace,
            depth__lt=self.depth,
        ).order_by("depth")

    def siblings(self) -> QuerySet[Self]:
        """Get siblings of this node (same parent).

        Returns:
            QuerySet of sibling nodes excluding self
        """
        return self.__class__.filter(
            parent_id=self.parent_id,
        ).exclude(id=self.id).order_by("name")

    async def path_to_root(self) -> list[Self]:
        """Get path from this node to root by walking ``parent_id`` links.

        Returns:
            List of nodes from root to this node
        """
        path: list[Self] = []
        visited: set[UUID] = set()
        current = self
        while True:
            if current.pk in visited:
                break  # defensive cycle guard
            visited.add(current.pk)
            path.append(current)
            if current.parent_id is None:
                break
            current = await self.__class__.get(pk=current.parent_id)
        return sorted(path, key=lambda n: n.depth)

    async def subtree(self, max_depth: int | None = None) -> list[Self]:
        """Get entire subtree rooted at this node.

        Args:
            max_depth: Maximum depth to traverse (None = unlimited)

        Returns:
            List of nodes in breadth-first order
        """
        result: list[Self] = [self]
        queue: deque[Self] = deque([self])
        visited = {self.id}

        while queue:
            current = queue.popleft()
            children = await current.children().all()
            for child in children:
                if child.id not in visited:
                    visited.add(child.id)
                    result.append(child)
                    if max_depth is None or child.depth < self.depth + max_depth:
                        queue.append(child)

        return result
