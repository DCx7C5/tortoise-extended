"""Abstract Tortoise ORM model for ltree-based hierarchy operations.

Provides HierarchyModel base class for tree structures using PostgreSQL ltree
materialized paths combined with adjacency-list fields. Subclasses inherit
ltree path queries, ancestor/descendant traversal, subtree moves, and
hierarchy validation — all backed by declared Tortoise fields.

Requires: PostgreSQL + ``CREATE EXTENSION IF NOT EXISTS ltree;``

Usage::

    from tortoise import fields
    from tortoise_extended.graph.hierarchy_model import HierarchyModel

    class Category(HierarchyModel):
        slug = fields.CharField(max_length=50)

        class Meta:
            table = "categories"
            verbose_name = "Category"
            verbose_name_plural = "Categories"
"""

from typing import Self, override

from tortoise import fields
from tortoise.models import Model
from tortoise.queryset import QuerySet

from tortoise_extended.fields.ltree_field import LTreeField
from tortoise_extended.indexes.ltree_index import GiSTIndex

# ── Internal helpers ────────────────────────────────────────────────────


def _path_to_str(path: list[str] | str | None) -> str:
    """Normalize a ltree path value to a dot-separated string.

    ``LTreeField.to_python_value`` returns ``list[str]`` after a DB round-trip,
    but Tortoise filter encoders accept both ``str`` and ``list[str]``.  Model
    methods that do string surgery (split, replace, startswith) need a plain
    ``str``, which this helper provides.

    Args:
        path: Raw value from the model's ``path`` attribute.

    Returns:
        Dot-separated path string, or an empty string when *path* is falsy.
    """
    if not path:
        return ""
    if isinstance(path, list):
        return ".".join(path)
    return str(path)


# ── Model ───────────────────────────────────────────────────────────────


class HierarchyModel(Model):
    """Abstract base for ltree-path hierarchy models.

    Combines PostgreSQL ltree materialized paths with adjacency-list columns
    (``parent_id``, ``depth``) so subclasses get both efficient path queries
    *and* simple parent/child lookups.  Every field is declared — no
    ``getattr`` guessing required.

    Subclasses **must** set ``class Meta: table = "..."`` to an explicit name.
    """

    # ── Fields ───────────────────────────────────────────────────────────

    id = fields.BigIntField(
        primary_key=True,
        description="Unique identifier for the hierarchy node",
    )
    path = LTreeField(
        max_length=1024,
        description="Materialized ltree path from root to this node "
        "(e.g. 'root.parent.child')",
    )
    name = fields.CharField(
        max_length=255,
        description="Human-readable node name (must match the last path component)",
    )
    parent_id = fields.BigIntField(
        null=True,
        description="Parent node primary key for adjacency-list traversal "
        "(NULL for root nodes)",
        db_index=True,
    )
    depth = fields.IntField(
        default=0,
        description="Denormalized hierarchy depth — root is 0",
    )
    namespace = fields.CharField(
        max_length=100,
        default="default",
        description="Multi-tenancy partition key",
        db_index=True,
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        use_tz=True,
        description="Creation timestamp (timezone-aware)",
    )
    updated_at = fields.DatetimeField(
        auto_now=True,
        use_tz=True,
        description="Last modification timestamp (timezone-aware)",
    )

    class Meta:
        abstract = True
        table = "hierarchy_nodes"
        verbose_name = "Hierarchy Node"
        verbose_name_plural = "Hierarchy Nodes"
        indexes = (
            GiSTIndex(fields=("path",)),
            ("namespace", "depth"),
            ("parent_id", "depth"),
        )

    # ── Dunder helpers ───────────────────────────────────────────────────

    @override
    def __str__(self) -> str:
        return f"{self.name} ({_path_to_str(self.path)})"

    @override
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.id}, path={_path_to_str(self.path)!r})>"

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_root(self) -> bool:
        """True when this node is a tree root (depth 0, no parent)."""
        return self.depth == 0 and self.parent_id is None

    @property
    def path_str(self) -> str:
        """The ltree path as a dot-separated string.

        Convenience accessor for code that needs a plain string rather than
        the ``list[str]`` that ``LTreeField.to_python_value`` returns.
        """
        return _path_to_str(self.path)

    # ── Tree Queries (sync — return lazy QuerySets) ──────────────────────

    def get_ancestors(self, *, include_self: bool = False) -> QuerySet[HierarchyModel] | QuerySet[Self]:
        """Return all ancestor nodes from root down to this node's parent.

        Uses the ltree ``@>`` (ancestor-of) operator so PostgreSQL can walk
        the GiST index.  Results are ordered by ``path`` ascending (root
        first).

        Args:
            include_self: When *True*, include this node in the result.

        Returns:
            Lazy QuerySet of ancestor nodes.
        """
        path_str = _path_to_str(self.path)
        if not path_str:
            return type(self).filter(pk__in=[])

        q: QuerySet[Self] | QuerySet[HierarchyModel] = type(self).filter(
            path__ancestor_of=path_str,
        ).order_by("path")

        if not include_self:
            q = q.exclude(pk=self.pk)

        return q

    def get_descendants(self, *, include_self: bool = False) -> QuerySet[HierarchyModel] | QuerySet[Self]:
        """Return all descendant nodes below this node.

        Uses the ltree ``<@`` (descendant-of) operator so PostgreSQL can walk
        the GiST index.  Results are ordered by ``path`` ascending (shallowest
        first).

        Args:
            include_self: When *True*, include this node in the result.

        Returns:
            Lazy QuerySet of descendant nodes.
        """
        path_str = _path_to_str(self.path)
        if not path_str:
            return type(self).filter(pk__in=[])

        q: QuerySet[Self] | QuerySet[HierarchyModel] = type(self).filter(
            path__descendant_of=path_str,
        ).order_by("path")

        if not include_self:
            q = q.exclude(pk=self.pk)

        return q

    def get_children(self) -> QuerySet[Self] | QuerySet[HierarchyModel]:
        """Return direct children — nodes exactly one depth level below.

        Uses the adjacency-list ``parent_id`` for a precise one-hop query
        instead of counting ltree labels.  Results are ordered by ``name``
        ascending.

        Returns:
            Lazy QuerySet of child nodes.
        """
        return type(self).filter(
            parent_id=self.pk,
        ).order_by("name")

    def get_siblings(self, *, include_self: bool = False) -> QuerySet[Self] | QuerySet[HierarchyModel]:
        """Return sibling nodes that share the same parent and depth.

        Uses the adjacency-list ``parent_id`` for matching.  Results are
        ordered by ``name`` ascending.

        Args:
            include_self: When *True*, include this node in the result.

        Returns:
            Lazy QuerySet of sibling nodes.
        """
        if self.parent_id is None:
            return type(self).filter(pk__in=[])

        q: QuerySet[Self] | QuerySet[HierarchyModel] = type(self).filter(
            parent_id=self.parent_id,
            depth=self.depth,
        ).order_by("name")

        if not include_self:
            q = q.exclude(pk=self.pk)

        return q

    # ── Tree Queries (async — execute immediately) ───────────────────────

    async def get_root(self) -> HierarchyModel | Self | None:
        """Fetch the root node of this node's tree.

        Extracts the first ltree label and looks up the node with that exact
        path within the same namespace.

        Returns:
            The root node, or *None* if the path is empty or the root is
            not found.
        """
        path_str = _path_to_str(self.path)
        if not path_str:
            return None

        # Single-label path means this node IS the root.
        if "." not in path_str:
            return self

        root_label = path_str.split(".")[0]
        root = await type(self).get_or_none(
            path=root_label,
            namespace=self.namespace,
        )
        return root

    async def get_path_to_root(self) -> list[Self] | list[HierarchyModel]:
        """Fetch every node on the path from this node up to the root.

        Builds intermediate ltree labels from the path string, bulk-fetches
        them in a single query, then appends ``self`` if it was not returned
        (e.g. an unsaved instance).

        Returns:
            List of nodes ordered by depth ascending (root first).
        """
        path_str = _path_to_str(self.path)
        if not path_str:
            return [self]

        components = path_str.split(".")
        ancestor_paths = [
            ".".join(components[: i + 1]) for i in range(len(components))
        ]

        nodes = await type(self).filter(
            path__in=ancestor_paths,
            namespace=self.namespace,
        ).order_by("path")

        result = list(nodes)

        # Ensure self is present (maybe an unsaved / detached instance).
        if not any(n.pk == self.pk for n in result):
            result.append(self)  # type: ignore[arg-type]

        return sorted(result, key=lambda n: n.depth)

    # ── Mutations ────────────────────────────────────────────────────────

    async def move_to(self, new_parent: Self) -> None:
        """Move this node and all its descendants under *new_parent*.

        Validates that the move does not create a cycle, then updates this
        node and cascades path/depth changes to every descendant.  Each
        row is updated individually — wrap the caller in ``async with
        in_transaction()`` for atomicity if needed.

        Args:
            new_parent: The target parent node.

        Raises:
            ValueError: If either node lacks a path, or the move would
                create a cycle (moving a node into its own descendant).
        """
        self_path_str = _path_to_str(self.path)
        new_parent_path_str = _path_to_str(new_parent.path)

        if not self_path_str or not new_parent_path_str:
            raise ValueError("Both source and target must have paths")

        # Cycle guard — new_parent must not sit inside this node's subtree.
        if new_parent_path_str.startswith(self_path_str + "."):
            raise ValueError("Cannot move a node into its own descendant")

        old_path = self_path_str
        new_path = f"{new_parent_path_str}.{self.name}"
        depth_delta = new_parent.depth - self.depth + 1

        # Update this node.
        _ = await type(self).filter(pk=self.pk).update(
            path=new_path,
            parent_id=new_parent.pk,
            depth=new_parent.depth + 1,
        )

        # Cascade path prefix replacement to every descendant.
        async for desc in self.get_descendants():
            old_desc_path = _path_to_str(desc.path)
            new_desc_path = old_desc_path.replace(old_path, new_path, 1)

            _ = await type(self).filter(pk=desc.pk).update(
                path=new_desc_path,
                depth=desc.depth + depth_delta,
            )

    # ── Validation ───────────────────────────────────────────────────────

    async def validate_hierarchy(self) -> list[str]:
        """Check this node's invariants and return any error strings.

        Validates:

        * Path is not empty.
        * Last path component matches :attr:`name`.
        * ``depth`` equals the number of ``"."`` separators (root depth 0).
        * ``parent_id`` references an existing node whose path is a prefix
          of this node's path.

        Returns:
            List of human-readable error descriptions — empty when the node
            is valid.
        """
        errors: list[str] = []
        path_str = _path_to_str(self.path)

        if not path_str:
            errors.append(f"Node {self.pk} has no path")
            return errors

        components = path_str.split(".")

        # Name must match the last path label.
        if components[-1] != self.name:
            errors.append(
                f"Path mismatch: path ends with {components[-1]!r} "
                f"but name is {self.name!r}"
            )

        # Depth must equal label count minus one (root = 0).
        expected_depth = len(components) - 1
        if self.depth != expected_depth:
            errors.append(
                f"Depth mismatch: expected {expected_depth} but got {self.depth}"
            )

        # Parent must exist and its path must be a proper prefix.
        if self.parent_id is not None:
            parent = await type(self).get_or_none(pk=self.parent_id)
            if parent is None:
                errors.append(f"Parent {self.parent_id} does not exist")
            else:
                parent_path_str = _path_to_str(parent.path)
                if parent_path_str and not path_str.startswith(
                    parent_path_str + "."
                ):
                    errors.append(
                        f"Parent path {parent_path_str!r} is not a prefix "
                        f"of {path_str!r}"
                    )

        return errors
