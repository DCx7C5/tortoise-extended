# pyright: reportPrivateUsage=false
"""Graph node primitives: pk-free traversal mixin + UUID7 identity base.

Splits the former ``BaseGraphNodeModel`` into two reusable layers:

* :class:`NodeMixin` — pk-free abstract mixin carrying the adjacency-list
  fields and traversal helpers.  It is an abstract :class:`~tortoise.models.Model`
  but deliberately declares **no** primary key, so it combines with any
  identity base (a BigInt ``BaseModel``, a custom UUID table, ...).
* :class:`BaseNode` — identity base combining :class:`NodeMixin` with a
  time-ordered ``UUID7Field`` primary key and the timestamp mixin.
* :class:`NodeTree` — :class:`msgspec.Struct` pairing a node with its nested
  children, built from a flat node slice by :meth:`NodeMixin.to_tree`.

``BaseGraphNodeModel`` (``tortoise_extended.models.graph_node``) remains as a
deprecated compatibility alias pinned to UUID4 identity.

Requires: PostgreSQL + Tortoise ORM

Usage::

    from tortoise_extended.models.node import BaseNode

    class Category(BaseNode):
        class Meta:
            table = "categories"
            indexes = (("namespace", "depth"),)

    root = await Category.create(name="Electronics")
    child = await Category.create(name="Laptops", parent_id=root.pk)
"""

from collections import deque
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, ClassVar, Self, override
from uuid import UUID

import msgspec
from tortoise import fields
from tortoise.expressions import F
from tortoise.models import Model
from tortoise.transactions import in_transaction

from tortoise_extended._types import CacheValue
from tortoise_extended.exceptions import GraphError
from tortoise_extended.fields import CharField
from tortoise_extended.fields.uuid import UUID7Field
from tortoise_extended.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient
    from tortoise.queryset import QuerySet


class NodeTree[ModelT: Model](msgspec.Struct):
    """A node and its nested children — one element of a materialized forest.

    Built by :meth:`NodeMixin.to_tree` from a flat node slice.  The struct
    wraps the hydrated model instance instead of copying its fields, so ORM
    identity (``pk``, ``parent_id``) and the queryset helpers stay available
    on every element.

    Attributes:
        node: The hydrated node model instance.
        children: Nested child trees, in input order; empty for a leaf.
    """

    node: ModelT
    children: list[NodeTree[ModelT]] = msgspec.field(default_factory=list)


class NodeMixin(Model):
    """Pk-free adjacency-list node mixin.

    Concrete subclasses **must**:

    * declare their own primary key — use an identity base such as
      :class:`BaseNode` or :class:`~tortoise_extended.models.base.BaseModel`,
      or set one directly;
    * declare a ``parent_id`` column whose type matches the primary key —
      ``parent_id`` is only *annotated* here, never a field, so a hybrid node
      table can key on ``BigInt`` instead of the default ``UUID``;
    * redeclare ``Meta.indexes`` — Tortoise does not propagate
      ``Meta.indexes`` from abstract bases (see ``__init_subclass__``).

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
    """

    if TYPE_CHECKING:
        # The mixin is pk-free: identity lives on the concrete base
        # (``BaseNode`` declares ``UUID7Field``; a hybrid table may use a
        # BigInt primary key).  Declare the value type for the traversal and
        # delete helpers that read ``self.id`` — no runtime field is added.
        id: UUID | int

    _block_orphan_delete: ClassVar[bool] = False
    """When True, ``delete()`` refuses to remove a node that has children."""

    parent_id: UUID | int | None
    """Persisted parent key — declared as a field by the concrete class."""

    ordering_field: ClassVar[str] = "id"
    """Field name used to order :meth:`children` / :meth:`siblings` results."""

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
    namespace = CharField(
        max_length=100,
        default="default",
        description="Namespace for multi-tenancy",
        db_index=True,
    )
    metadata_json = fields.JSONField(
        default=dict,
        description="Arbitrary metadata for the node",
    )

    class Meta:
        abstract = True
        # Tortoise does NOT propagate Meta.indexes from abstract bases to
        # concrete subclasses — every concrete subclass must redeclare them
        # (see __init_subclass__ guard below).
        indexes = (("namespace", "depth"),)

    def __init_subclass__(cls, **kwargs: str | int | float | bool | None) -> None:
        """Guard against silently losing the abstract base index.

        Tortoise only copies base fields/indexes for abstract bases and does
        not propagate ``Meta.indexes`` to concrete subclasses.  A subclass
        that forgets to redeclare ``(namespace, depth)`` would run the common
        multi-tenancy scoped traversal queries without composite index
        support — raise at class-creation time instead.

        A concrete subclass must also declare its own ``parent_id`` column:
        the mixin only annotates the attribute, so the parent key type stays
        open per class.

        Raise:
            NotImplementedError: When a concrete subclass (or one without an
                explicit ``Meta``) does not declare ``Meta.indexes``.  Opt
                out deliberately with ``Meta.indexes = ()``.
            NotImplementedError: When a concrete subclass does not declare a
                ``parent_id`` field.
        """
        super().__init_subclass__(**kwargs)
        meta = cls.__dict__.get("Meta")
        if meta is None:
            raise NotImplementedError(
                f"{cls.__name__} must declare a Meta class with table and indexes. "
                "Tortoise does not propagate Meta.indexes from abstract bases; "
                "redeclare the (namespace, depth) index on every concrete subclass."
            )
        if getattr(meta, "abstract", False):
            return
        if "indexes" not in meta.__dict__:
            raise NotImplementedError(
                f"{cls.__name__}.Meta must redeclare indexes — Tortoise does not "
                "propagate Meta.indexes from abstract bases. Add the "
                "(namespace, depth) index or opt out explicitly with indexes = ()."
            )
        if "parent_id" not in cls._meta.fields_map:
            raise NotImplementedError(
                f"{cls.__name__} must declare a parent_id field. NodeMixin only "
                "annotates parent_id (UUID | int | None) so each concrete node "
                "class chooses its own parent key type; declare e.g. "
                "parent_id = fields.UUIDField(null=True, db_index=True)."
            )

    @override
    async def save(
        self,
        using_db: BaseDBAsyncClient | None = None,
        update_fields: Iterable[str] | None = None,
        force_create: bool = False,
        force_update: bool = False,
    ) -> None:
        """Persist this node and maintain the denormalized ``child_count``.

        ``child_count`` is a denormalized degree counter on the parent, so it
        must be kept in sync on every write:

        * **create** — increment the new parent's ``child_count``;
        * **reparent** (parent_id changed on an update) — decrement the
          previous parent's count and increment the new parent's count.

        The previous ``parent_id`` is read from the database before the write
        because the in-memory instance may already hold the new value.

        A partial update via ``update_fields`` that omits ``parent_id`` does
        **not** persist an in-memory reparent, so the counters are only
        adjusted when ``update_fields`` is ``None`` or explicitly contains
        ``"parent_id"``.

        Args:
            using_db: Specific DB connection to use instead of the default.
            update_fields: Optional subset of fields to update.
            force_create: Force an INSERT even for a previously saved row.
            force_update: Force an UPDATE even for an unsaved row.
        """
        # ``_saved_in_db`` is not declared in the tortoise stubs overlay —
        # read it through getattr (the create-vs-update signal tortoise's own
        # save() uses).
        already_saved = bool(getattr(self, "_saved_in_db", False))
        # Materialize update_fields so the parent_id membership check does
        # not consume a generator before super().save() sees it.
        update_fields_list = list(update_fields) if update_fields is not None else None
        is_create = force_create or (
            not already_saved and update_fields is None and not force_update
        )

        async def _persist_and_sync() -> None:
            old_parent_id: UUID | int | None = None
            if not is_create:
                old_parent_id = await self._previous_parent_id(using_db)

            await super(NodeMixin, self).save(
                using_db=using_db,
                update_fields=update_fields,
                force_create=force_create,
                force_update=force_update,
            )

            if is_create:
                if self.parent_id is not None:
                    _ = await (
                        self.__class__.filter(pk=self.parent_id)
                        .using_db(using_db)
                        .update(child_count=F("child_count") + 1)
                    )
            elif update_fields_list is None or "parent_id" in update_fields_list:
                if old_parent_id is not None:
                    _ = await (
                        self.__class__.filter(pk=old_parent_id)
                        .using_db(using_db)
                        .update(child_count=F("child_count") - 1)
                    )
                if self.parent_id is not None:
                    _ = await (
                        self.__class__.filter(pk=self.parent_id)
                        .using_db(using_db)
                        .update(child_count=F("child_count") + 1)
                    )

        # The node write and the denormalized counter maintenance must be a
        # single atomic unit. When no connection was supplied, run both on the
        # default connection inside one transaction so a partial failure cannot
        # leave ``child_count`` drifted. When ``using_db`` is supplied the
        # caller already scopes the connection (possibly an outer transaction)
        # — never open a nested transaction on a different connection.
        #
        # ``_previous_parent_id`` reads the persisted parent on this same
        # connection and (where the backend supports it) locks the node's row
        # via ``select_for_update``, so a concurrent reparent cannot interleave
        # between the read and the ``child_count`` decrement and drift the
        # counter. See ``_previous_parent_id`` for the SQLite fallback.
        if using_db is None:
            async with in_transaction():
                await _persist_and_sync()
        else:
            await _persist_and_sync()

    async def _previous_parent_id(
        self, using_db: BaseDBAsyncClient | None = None
    ) -> UUID | int | None:
        """Return this node's persisted ``parent_id`` from the database.

        The read runs on the same connection that persists the node
        (``using_db`` when supplied, otherwise the default/transactional
        connection) and, on backends that support row locking (PostgreSQL),
        via ``select_for_update`` so concurrent reparents of this node
        serialize on the node's row before any ``child_count`` adjustment.
        On backends without ``FOR UPDATE`` support (SQLite) the queryset is
        used unmodified, so the call is fully portable.
        """
        row = (
            await self.__class__.select_for_update(using_db=using_db)
            .filter(pk=self.pk)
            .only("parent_id")
            .first()
        )
        return row.parent_id if row is not None else None

    @override
    async def delete(self, using_db: BaseDBAsyncClient | None = None) -> None:
        """Delete this node.

        Performs the optional orphan guard: when ``_block_orphan_delete`` is
        set on the concrete class and this node still has children, raises
        :class:`~tortoise_extended.exceptions.GraphError` instead of
        deleting.  Edges referencing this node are never cascaded — see the
        class-level orphan policy.  On a successful delete the former
        parent's ``child_count`` is decremented.

        Raises:
            GraphError: If ``_block_orphan_delete`` is set and the node has
                children.
        """
        if self._block_orphan_delete:
            remaining = await self.__class__.filter(parent_id=self.id).count()
            if remaining:
                msg = (
                    f"Cannot delete {self.__class__.__name__} {self.id}: "
                    f"{remaining} child node(s) reference it as parent."
                )
                raise GraphError(msg)

        async def _delete_and_sync() -> None:
            await super(NodeMixin, self).delete(using_db=using_db)
            if self.parent_id is not None:
                _ = await (
                    self.__class__.filter(pk=self.parent_id)
                    .using_db(using_db)
                    .update(child_count=F("child_count") - 1)
                )

        # Keep the row delete and the parent ``child_count`` decrement atomic
        # on the default connection, mirroring save(); see there for the
        # ``using_db`` rationale.
        if using_db is None:
            async with in_transaction():
                await _delete_and_sync()
        else:
            await _delete_and_sync()

    @property
    def is_leaf(self) -> bool:
        """Check if this is a leaf node (no children)."""
        return self.child_count == 0

    def children(self) -> QuerySet[Self]:
        """Get direct children of this node.

        Returns:
            QuerySet of child nodes ordered by ``ordering_field``
        """
        return self.__class__.filter(
            parent_id=self.id,
        ).order_by(self.ordering_field)

    async def add_child(self, **kwargs: CacheValue) -> Self:
        """Create and persist a direct child of this node.

        The child gets ``parent_id=self.pk``, ``depth=self.depth + 1``,
        ``is_root=False`` and this node's ``namespace``; any of those can be
        overridden by passing the field explicitly in *kwargs*.

        ``child_count`` is maintained by :meth:`save` on create, so this
        method never issues a second ``child_count`` UPDATE — it only syncs
        the in-memory counter on this instance so it does not read stale.

        Args:
            **kwargs: Additional field values for the new child node. Values
                use :data:`~tortoise_extended._types.CacheValue`, so a
                JSON-serializable ``dict`` / ``list`` is accepted for
                ``JSONField`` columns such as ``metadata_json``.

        Returns:
            The persisted child node.

        Raises:
            GraphError: If this node has not been saved yet (``pk`` is
                ``None``), so the child would have no parent link.
        """
        if self.pk is None:
            msg = (
                f"Cannot add a child to unpersisted {self.__class__.__name__}: "
                "save the parent node first."
            )
            raise GraphError(msg)
        child_fields: dict[str, CacheValue] = {
            "parent_id": self.pk,
            "depth": self.depth + 1,
            "is_root": False,
            "namespace": self.namespace,
        }
        child_fields.update(kwargs)
        # ``using_db`` is bound explicitly: the helper takes field values only
        # (no connection parameter), and it keeps the ``CacheValue`` splat from
        # being matched against ``create()``'s connection argument.
        child = await self.__class__.create(using_db=None, **child_fields)
        # save() already bumped the parent's DB counter on create; bumping it
        # here too would double-count. Keep only the local copy in sync.
        self.child_count += 1
        return child

    def descendants(self, max_depth: int | None = None) -> QuerySet[Self]:
        """Get descendants of this node by depth range in the same namespace.

        This is a depth-range approximation: it returns nodes strictly deeper
        than this node within the same namespace, excluding the node itself.
        For link-exact traversal use :meth:`subtree`.

        Args:
            max_depth: Maximum depth to traverse (None = unlimited)

        Returns:
            QuerySet of descendant nodes ordered by depth then
            ``ordering_field``
        """
        if max_depth is None:
            max_depth = 1000  # Safety limit
        return self.__class__.filter(
            namespace=self.namespace,
            depth__gt=self.depth,
            depth__lte=self.depth + max_depth,
        ).order_by("depth", self.ordering_field)

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
            QuerySet of sibling nodes excluding self, ordered by
            ``ordering_field``
        """
        return (
            self.__class__.filter(
                parent_id=self.parent_id,
            )
            .exclude(id=self.id)
            .order_by(self.ordering_field)
        )

    async def path_to_root(self) -> list[Self]:
        """Get path from this node to root by walking ``parent_id`` links.

        The orphan policy permits dangling ``parent_id`` values (no cascade,
        no FK), so the parent may be missing from the table.  When that
        happens the walk cannot complete and a
        :class:`~tortoise_extended.exceptions.GraphError` is raised instead
        of a ``DoesNotExist``.

        Returns:
            List of nodes from root to this node

        Raises:
            GraphError: If a parent link points at a row that does not exist.
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
            parent = await self.__class__.get_or_none(pk=current.parent_id)
            if parent is None:
                raise GraphError(
                    f"Parent {current.parent_id} of {self.__class__.__name__} "
                    f"{current.pk} no longer exists — dangling parent_id"
                )
            current = parent
        return sorted(path, key=lambda n: n.depth)

    async def root(self) -> Self:
        """Return the root of this node's parent chain.

        :meth:`path_to_root` returns the chain sorted by ascending ``depth``,
        so index ``0`` is the root and index ``-1`` is this node itself.

        Returns:
            The topmost node reachable through ``parent_id`` links.

        Raises:
            GraphError: If a parent link points at a row that no longer
                exists (see :meth:`path_to_root`).
        """
        return (await self.path_to_root())[0]

    async def is_ancestor_of(self, other: Self) -> bool:
        """Return whether this node is a strict ancestor of *other*.

        Walks *other*'s ``parent_id`` chain (link-exact, unlike the
        depth-range approximation in :meth:`ancestors`) and checks whether
        this node appears in it.  A node is not its own ancestor.

        Args:
            other: The node whose ancestry to test.

        Returns:
            ``True`` when this node is on *other*'s parent chain.

        Raises:
            GraphError: If a parent link on *other*'s chain is dangling.
        """
        if self.pk is None or self.pk == other.pk:
            return False
        return any(node.pk == self.pk for node in await other.path_to_root())

    async def is_descendant_of(self, other: Self) -> bool:
        """Return whether this node is a strict descendant of *other*.

        The mirror of :meth:`is_ancestor_of`: this node's own parent chain is
        walked and *other* must appear in it.  A node is not its own
        descendant.

        Args:
            other: The node whose descendant to test.

        Returns:
            ``True`` when *other* is on this node's parent chain.

        Raises:
            GraphError: If a parent link on this node's chain is dangling.
        """
        return await other.is_ancestor_of(self)

    async def subtree(self, max_depth: int | None = None) -> list[Self]:
        """Get entire subtree rooted at this node.

        This is :meth:`descendants`' link-exact counterpart: it walks the
        ``parent_id`` adjacency list directly (BFS) rather than approximating
        via a depth range, so it returns exactly the nodes reachable from this
        one through live parent links.

        .. note::

            The BFS issues one ``children()`` query per visited node — an
            ``N + 1`` query pattern.  For deep or wide trees prefer
            :meth:`descendants` (a single scoped query) when a depth-range
            approximation is acceptable.

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

    @classmethod
    def to_tree(cls, nodes: Sequence[Self]) -> list[NodeTree[Self]]:
        """Build a nested forest from a flat slice of nodes.

        Every node is wrapped in a :class:`NodeTree` and attached to its
        parent in one O(n) pass keyed by ``parent_id`` — no per-node query and
        no recursion.

        A node whose ``parent_id`` is ``None`` **or** points outside *nodes*
        becomes a root of the returned forest.  Promotion is the normal case
        for a slice such as ``descendants()``, whose parents live outside the
        slice.  A node without a primary key (an unsaved instance) has no
        identity to link against and also surfaces as a root.

        Args:
            nodes: The nodes of one forest slice, each appearing once.

        Returns:
            Root :class:`NodeTree` instances in input order, with children
            nested under their parents in input order.

        Raises:
            GraphError: If the input contains a ``parent_id`` cycle, leaving
                nodes unreachable from any root.
        """
        entries: list[tuple[Self, NodeTree[Self]]] = [
            (node, NodeTree(node=node)) for node in nodes
        ]
        trees: dict[UUID | int, NodeTree[Self]] = {
            node.pk: tree for node, tree in entries if node.pk is not None
        }
        roots: list[NodeTree[Self]] = []
        for node, tree in entries:
            parent_id = node.parent_id
            if node.pk is None or parent_id is None or parent_id not in trees:
                roots.append(tree)
            else:
                trees[parent_id].children.append(tree)

        # Walk down from the roots: anything still unvisited is part of a
        # parent_id cycle (every cycle member's parent is inside the input, so
        # none of them was promoted to a root).
        reachable = 0
        pending = list(roots)
        while pending:
            current = pending.pop()
            reachable += 1
            pending.extend(current.children)
        if reachable != len(entries):
            msg = (
                f"{len(entries) - reachable} {cls.__name__} node(s) are not "
                "reachable from any root — the input contains a parent_id cycle."
            )
            raise GraphError(msg)
        return roots


class BaseNode(NodeMixin, TimestampMixin):
    """Graph node identity base: UUID7 primary key + timestamps + traversal.

    Layers :class:`NodeMixin` (adjacency-list fields and helpers) over a
    time-ordered ``UUID7Field`` primary key and ``created_at``/``updated_at``
    timestamps from :class:`~tortoise_extended.models.mixins.TimestampMixin`.

    Features:
    - UUID7 primary key for globally unique, index-friendly identity
    - parent_id for adjacency list traversal
    - depth for hierarchy levels
    - is_root flag for identifying root nodes
    - child_count for denormalized degree tracking
    - namespace for multi-tenancy

    Usage::

        class Category(BaseNode):
            class Meta:
                table = "categories"
                indexes = (("namespace", "depth"),)

        # Create root
        root = await Category.create(name="Electronics")

        # Create child (parent is a bare ``parent_id`` column — no relation)
        child = await Category.create(name="Laptops", parent_id=root.pk)
    """

    if TYPE_CHECKING:
        # Narrow the pk-free mixin's generic identity value types for this
        # UUID-identity base. The concrete field objects are assigned in the
        # ``else`` branch so Tortoise still registers them on the class body;
        # annotating them in the same scope would hide the field descriptors.
        id: UUID
        parent_id: UUID | None
    else:
        id = UUID7Field(
            primary_key=True,
            description="Unique identifier for the node",
        )
        parent_id = fields.UUIDField(
            null=True,
            description="Parent node ID for adjacency list traversal",
            db_index=True,
        )
    name = CharField(
        max_length=100,
        description="Human-readable node name",
    )
    ordering_field: ClassVar[str] = "name"

    class Meta:
        abstract = True

    @override
    def __str__(self) -> str:
        return f"{self.name} ({self.id})"

    @override
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.id}, name={self.name!r})>"
