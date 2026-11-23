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
from typing import TYPE_CHECKING, ClassVar, Literal, NoReturn, Self, cast, override
from uuid import UUID

import msgspec
from tortoise import connections, fields
from tortoise.expressions import F, Q
from tortoise.models import Model
from tortoise.transactions import in_transaction

from tortoise_extended._quote import quote_ident
from tortoise_extended._types import CacheValue, RowValue
from tortoise_extended.exceptions import GraphError
from tortoise_extended.fields import CharField
from tortoise_extended.fields.uuid import UUID7Field
from tortoise_extended.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient
    from tortoise.queryset import QuerySet


async def _descendant_ids(
    model: type[Model],
    root_pk: UUID | int,
    using_db: BaseDBAsyncClient,
) -> list[UUID | int]:
    """Materialise the primary keys of every descendant of *root_pk*.

    A single recursive CTE walks the ``parent_id`` adjacency list, so the
    result is link-exact (unlike the depth-range :meth:`NodeMixin.descendants`
    approximation) and needs no per-level round trip.  ``UNION`` rather than
    ``UNION ALL`` also terminates on a pre-existing parent cycle in the data.

    The query reads **unlocked** rows: a caller that needs the set to be
    consistent with its own writes must hold the relevant locks itself.

    Args:
        model: Concrete node model whose table is traversed.
        root_pk: Primary key of the subtree root (excluded from the result).
        using_db: Connection — pass the transaction handle so the read joins
            the caller's transaction.

    Returns:
        Primary keys of all nodes reachable through live ``parent_id`` links,
        in no particular order.
    """
    table = quote_ident(model._meta.db_table)
    pk_column = quote_ident(model._meta.db_pk_column)
    # ``source_field`` is None unless the concrete class renamed the column;
    # Tortoise falls back to the model-field name for the DB column.
    parent_field = model._meta.fields_map["parent_id"]
    parent_column = quote_ident(
        parent_field.source_field or parent_field.model_field_name
    )
    sql = (
        f"WITH RECURSIVE subtree(nid) AS ("
        f"SELECT {pk_column} FROM {table} WHERE {parent_column} = $1 "
        "UNION "
        f"SELECT child.{pk_column} FROM {table} AS child "
        f"JOIN subtree ON child.{parent_column} = subtree.nid"
        ") SELECT nid FROM subtree"
    )
    # ``RowValue`` does not model UUID — an asyncpg wire-type gap in the stub
    # overlay; the cast is confined to this DB boundary.
    rows = await using_db.execute_query_dict(sql, cast(list[RowValue], [root_pk]))
    return [cast(UUID | int, row["nid"]) for row in rows]


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

    edge_model: ClassVar[type[Model] | None] = None
    """Optional edge model enabling :meth:`connect` / :meth:`disconnect` /
    :meth:`neighbours`.

    Set it on a concrete node class to the edge model that links nodes of
    this type, e.g. ``edge_model = Relationship``.  It is duck-typed: the
    edge model only has to declare ``source_id`` and ``target_id`` (plus,
    optionally, ``namespace``, ``edge_type`` and ``weight``).  It is never
    imported here, because the edge model imports this module's bases.
    Declare a plain ``allow_self_loops = True`` attribute on the edge model
    to permit self-links.
    """

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
        # counter.
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
        connection) and uses ``select_for_update`` so concurrent reparents of
        this node serialize on the node's row before any ``child_count``
        adjustment.
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

    async def add_child(
        self, *, using_db: BaseDBAsyncClient | None = None, **kwargs: CacheValue
    ) -> Self:
        """Create and persist a direct child of this node.

        The child gets ``parent_id=self.pk``, ``depth=self.depth + 1``,
        ``is_root=False`` and this node's ``namespace``; any of those can be
        overridden by passing the field explicitly in *kwargs*.

        ``child_count`` is maintained by :meth:`save` on create, so this
        method never issues a second ``child_count`` UPDATE — it only syncs
        the in-memory counter on this instance so it does not read stale.

        Args:
            using_db: Connection to run the child INSERT on. ``None`` uses the
                default connection, which tortoise routes to the ambient
                ``in_transaction()`` connection when one is active — so atomic
                parent + children creation already works without this
                argument. Pass a handle explicitly to target a specific
                connection (for example a transaction opened on another
                connection name). The in-memory ``child_count`` bump on this
                instance happens either way.
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
        # ``using_db`` is forwarded so the INSERT joins the caller's
        # transaction when one is supplied; ``None`` falls back to the default
        # connection. It is an explicit keyword-only parameter (not a field
        # value) so it can never collide with the ``CacheValue`` splat.
        child = await self.__class__.create(using_db=using_db, **child_fields)
        # save() already bumped the parent's DB counter on create; bumping it
        # here too would double-count. Keep only the local copy in sync.
        self.child_count += 1
        return child

    @classmethod
    async def bulk_add_children(
        cls,
        parent: Self | None,
        rows: Sequence[dict[str, CacheValue]],
        *,
        using_db: BaseDBAsyncClient | None = None,
    ) -> list[Self]:
        """Insert a batch of siblings with every derived column computed.

        The supported way to seed a level of a tree: every row goes through
        Tortoise's own INSERT path (so a generated primary key, ``auto_now``
        timestamps and field defaults all come out right), the whole batch
        shares one transaction, and the parent's ``child_count`` is bumped
        once.  Derived columns are the point — a plain ``bulk_create`` skips
        them (see :meth:`bulk_create`), which is why that method raises here.

        It is **not** a single multi-row ``INSERT``: rows are inserted one at
        a time inside the transaction.  Tortoise's ``bulk_create`` cannot
        return database-generated keys, and a seeded level is useless without
        them (the next level needs the parents' ``pk``).  For a raw
        ``COPY``-speed load, insert outside the library and repair with
        :meth:`rebuild_derived`.

        Each entry of *rows* is a mapping of ordinary field values (like
        :meth:`add_child` keyword arguments) for one node:

        * ``parent_id``, ``depth``, ``is_root`` and ``child_count`` are
          **derived** and must not appear in the row — a row that sets one
          raises :class:`~tortoise_extended.exceptions.GraphError`;
        * ``namespace`` defaults to the parent's and must not name another
          tenant;
        * pass ``parent=None`` to seed roots: ``parent_id=None``, ``depth=0``,
          ``is_root=True``.

        The INSERT and the parent ``child_count`` update share one transaction
        when *using_db* is ``None``; a supplied handle (for example a
        transaction already open on the caller's connection) is used as-is, so
        a caller can nest this in its own atomic unit.

        Args:
            parent: The parent node, or ``None`` to create roots.
            rows: One mapping of field values per new node, in insert order.
            using_db: Connection to run the write on; ``None`` uses a
                freshly opened transaction on the default connection.

        Returns:
            The unsaved-then-persisted instances, in *rows* order.

        Raises:
            GraphError: If *parent* is unpersisted, a row sets a derived
                column, or a row names another namespace.
        """
        if not rows:
            return []
        if parent is not None and parent.pk is None:
            raise GraphError(
                f"Cannot bulk-add children to an unpersisted {cls.__name__}: "
                "save the parent node first."
            )

        parent_id: UUID | int | None = parent.pk if parent is not None else None
        depth = 0 if parent is None else parent.depth + 1
        parent_namespace = None if parent is None else parent.namespace

        prepared: list[dict[str, CacheValue]] = []
        for index, row in enumerate(rows):
            values = dict(row)
            for derived in ("parent_id", "depth", "is_root", "child_count"):
                if derived in values:
                    raise GraphError(
                        f"bulk_add_children row {index} sets {derived!r}, which is "
                        f"derived from the parent; remove it from the row."
                    )
            if parent_namespace is not None:
                row_namespace = values.get("namespace", parent_namespace)
                if row_namespace != parent_namespace:
                    raise GraphError(
                        f"bulk_add_children row {index} targets namespace "
                        f"{row_namespace!r} but the parent is in "
                        f"{parent_namespace!r}: a child cannot cross namespaces."
                    )
                values["namespace"] = parent_namespace
            # ``CacheValue`` has no UUID member (a wire-type alias for the
            # cache backend, not a model column); the value is passed to
            # ``cls(**values)``, which accepts it verbatim.
            values["parent_id"] = cast(CacheValue, parent_id)
            values["depth"] = depth
            values["is_root"] = parent is None
            values["child_count"] = 0
            prepared.append(values)

        async def _insert(db: BaseDBAsyncClient | None) -> list[Self]:
            objects: list[Self] = []
            for values in prepared:
                instance = cls(**values)
                # Insert through Tortoise's own save() rather than NodeMixin's:
                # the per-row child_count maintenance below replaces the
                # per-row hook with a single bump, and Model.save() still
                # generates keys, timestamps and field defaults.
                await super(NodeMixin, instance).save(using_db=db)
                objects.append(instance)
            if parent_id is not None:
                _ = await (
                    cls.filter(pk=parent_id)
                    .using_db(db)
                    .update(child_count=F("child_count") + len(objects))
                )
                if parent is not None:
                    parent.child_count += len(objects)
            return objects

        # The INSERTs and the counter bump must land together, mirroring
        # save(); a caller-supplied connection is already scoped.
        if using_db is None:
            async with in_transaction() as raw_conn:
                db: BaseDBAsyncClient = raw_conn
                return await _insert(db)
        return await _insert(using_db)

    @classmethod
    @override
    def bulk_create(
        cls,
        objects: Iterable[Model],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        on_conflict: Iterable[str] | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> NoReturn:
        """Refuse ``bulk_create`` on node models.

        ``bulk_create`` inserts rows without running :meth:`save`, so no
        derived column is maintained: a bulk-inserted child keeps
        ``depth=0``, ``is_root=False`` and the parent's ``child_count`` stays
        at zero.  Nothing raises, and every ordering and traversal query then
        works on a tree that is silently wrong — the failure mode
        django-mptt documents as its own undoing.

        Use :meth:`bulk_add_children` to seed a level (it derives
        ``parent_id`` / ``depth`` / ``is_root`` / ``child_count`` and bumps the
        parent's counter), or, after a raw ``COPY`` / SQL load, call
        :meth:`rebuild_derived` to repair the derived columns.

        Raises:
            GraphError: Always — this method never inserts.
        """
        raise GraphError(
            f"{cls.__name__}.bulk_create() is not supported: a bulk INSERT does "
            "not run save(), so depth/is_root/child_count would be silently "
            f"wrong. Use `await {cls.__name__}.bulk_add_children(parent, rows)` "
            "to seed a level, or `rebuild_derived()` after a raw load."
        )

    @classmethod
    @override
    def bulk_update(
        cls,
        objects: Iterable[Model],
        fields: Iterable[str],
        batch_size: int | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> NoReturn:
        """Refuse ``bulk_update`` on node models.

        ``bulk_update`` writes columns without running :meth:`save`, so it can
        persist a changed ``parent_id`` while ``is_root`` / ``child_count``
        (and every descendant's ``depth``) keep their old values.  Reparenting
        is :meth:`move_to`'s job — it locks, guards against cycles and shifts
        the whole subtree inside one transaction.

        Raises:
            GraphError: Always — this method never writes.
        """
        raise GraphError(
            f"{cls.__name__}.bulk_update() is not supported: it does not run "
            "save(), so is_root/child_count and descendant depth would be "
            "silently wrong. Use move_to() to reparent, or rebuild_derived() "
            "to repair derived columns."
        )

    @classmethod
    def _require_edge_model(cls) -> type[Model]:
        """Return the configured edge model or raise a clear :class:`GraphError`.

        Validates the duck-typed contract the edge helpers rely on: a
        ``source_id`` and ``target_id`` column must exist on the edge model.
        """
        edge_model = cls.edge_model
        if edge_model is None:
            raise GraphError(
                f"{cls.__name__} has no edge_model configured: set "
                "`edge_model = <edge model>` on the node class to use "
                "connect(), disconnect() and neighbours()."
            )
        for field_name in ("source_id", "target_id"):
            if field_name not in edge_model._meta.fields_map:
                raise GraphError(
                    f"{edge_model.__name__} is not usable as an edge model: "
                    f"missing the {field_name!r} field."
                )
        return edge_model

    async def connect(
        self,
        other: Self,
        *,
        edge_type: str | None = None,
        weight: float | None = None,
        **attrs: CacheValue,
    ) -> Model:
        """Create an edge from this node to *other*.

        The edge row is written through the configured :attr:`edge_model`;
        only fields that model declares are passed, so an edge model without
        ``edge_type`` / ``weight`` is usable as long as the caller does not
        ask for them.

        Args:
            other: Target node — must be persisted and in the same namespace.
            edge_type: Optional relationship type (requires the edge model to
                declare an ``edge_type`` field).
            weight: Optional edge weight (requires a ``weight`` field).
            **attrs: Extra edge-model fields, written verbatim.

        Returns:
            The created edge instance.

        Raises:
            GraphError: If either node is unpersisted, the link crosses
                namespaces, a self-link is attempted without the edge model
                opting in via ``allow_self_loops = True``, or a requested
                field does not exist on the edge model.
        """
        edge_model = type(self)._require_edge_model()
        if self.pk is None or other.pk is None:
            raise GraphError("Cannot connect unpersisted nodes: save them first.")
        if self.pk == other.pk and not getattr(edge_model, "allow_self_loops", False):
            raise GraphError(
                "Cannot connect a node to itself: the edge model does not "
                "declare allow_self_loops = True."
            )
        if other.namespace != self.namespace:
            raise GraphError(
                "Cannot connect nodes across namespaces: "
                f"source namespace {self.namespace!r}, "
                f"target namespace {other.namespace!r}"
            )

        edge_fields = edge_model._meta.fields_map
        payload: dict[str, CacheValue] = {}
        if "namespace" in edge_fields:
            payload["namespace"] = self.namespace
        for optional_name, optional_value in (
            ("edge_type", edge_type),
            ("weight", weight),
        ):
            if optional_value is None:
                continue
            if optional_name not in edge_fields:
                raise GraphError(
                    f"{edge_model.__name__} has no {optional_name!r} field; "
                    "omit it or use an edge model that declares it."
                )
            payload[optional_name] = optional_value

        unknown = set(attrs) - set(edge_fields)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise GraphError(
                f"{edge_model.__name__} has no field(s) {names}; "
                "pass only fields the edge model declares."
            )
        payload.update(attrs)
        payload["source_id"] = self.pk
        payload["target_id"] = other.pk
        return await edge_model.create(using_db=None, **payload)

    async def disconnect(self, other: Self, *, edge_type: str | None = None) -> int:
        """Delete edges from this node to *other*; return the number removed.

        Directed by design: only rows whose ``source_id`` is this node and
        ``target_id`` is *other* are deleted.  Pass ``edge_type`` to narrow
        the deletion to one relationship type.

        Raises:
            GraphError: If either node is unpersisted or a requested
                ``edge_type`` field does not exist on the edge model.
        """
        edge_model = type(self)._require_edge_model()
        if self.pk is None or other.pk is None:
            raise GraphError("Cannot disconnect unpersisted nodes: save them first.")

        edge_fields = edge_model._meta.fields_map
        filters: dict[str, CacheValue] = {}
        if "namespace" in edge_fields:
            filters["namespace"] = self.namespace
        if edge_type is not None:
            if "edge_type" not in edge_fields:
                raise GraphError(
                    f"{edge_model.__name__} has no 'edge_type' field; "
                    "omit it or use an edge model that declares it."
                )
            filters["edge_type"] = edge_type
        filters["source_id"] = self.pk
        filters["target_id"] = other.pk
        deleted = await edge_model.filter(**filters).delete()
        return int(deleted)

    async def neighbours(
        self,
        *,
        edge_type: str | None = None,
        direction: Literal["out", "in", "both"] = "both",
        namespace: str | None = None,
    ) -> list[Self]:
        """Return the nodes linked to this one through :attr:`edge_model`.

        Filter shapes mirror :meth:`BaseGraphEdgeModel.outgoing`,
        :meth:`~BaseGraphEdgeModel.incoming` and
        :meth:`~BaseGraphEdgeModel.between_any`: ``out`` reads edges whose
        ``source_id`` is this node, ``in`` those whose ``target_id`` is, and
        ``both`` the union of the two (so a bidirectional edge is seen from
        either end).

        Args:
            edge_type: Only follow edges of this type.
            direction: ``"out"``, ``"in"`` or ``"both"``.
            namespace: Namespace to read edges from; defaults to this node's
                own namespace.

        Returns:
            Deduplicated peer nodes (this node is never included), ordered by
            :attr:`ordering_field`.

        Raises:
            GraphError: If the node is unpersisted or a requested field is
                absent from the edge model.
        """
        edge_model = type(self)._require_edge_model()
        if self.pk is None:
            raise GraphError("Cannot read neighbours of an unpersisted node.")

        edge_fields = edge_model._meta.fields_map
        filters: Q
        if direction == "out":
            filters = Q(source_id=self.pk)
        elif direction == "in":
            filters = Q(target_id=self.pk)
        else:
            filters = Q(source_id=self.pk) | Q(target_id=self.pk)
        if "namespace" in edge_fields:
            filters &= Q(namespace=self.namespace if namespace is None else namespace)
        if edge_type is not None:
            if "edge_type" not in edge_fields:
                raise GraphError(
                    f"{edge_model.__name__} has no 'edge_type' field; "
                    "omit it or use an edge model that declares it."
                )
            filters &= Q(edge_type=edge_type)

        rows = await edge_model.filter(filters).values("source_id", "target_id")
        self_pk = cast(UUID | int, self.pk)
        peer_ids: list[UUID | int] = []
        seen: set[UUID | int] = set()
        for row in rows:
            source = cast(UUID | int, row["source_id"])
            target = cast(UUID | int, row["target_id"])
            peer = target if source == self_pk else source
            if peer != self_pk and peer not in seen:
                seen.add(peer)
                peer_ids.append(peer)
        if not peer_ids:
            return []
        return await (
            type(self).filter(pk__in=peer_ids).order_by(type(self).ordering_field)
        )

    async def move_to(self, new_parent: Self | None) -> None:
        """Move this node and its whole subtree under *new_parent*.

        Reparents the node in place and shifts every descendant's ``depth`` by
        the same delta in one bulk ``UPDATE`` — no per-node writes::

            await laptops.move_to(kitchen)  # kitchen.depth == 1
            # laptops.depth: 1 -> 2, macbook.depth: 2 -> 3

        Passing ``None`` detaches the node to a root: ``parent_id = None``,
        ``depth = 0``, ``is_root = True``, with descendants shifted relative to
        it.  The previous parent's ``child_count`` is decremented and the new
        parent's incremented; a move to the node's current parent (or a
        detach that changes nothing) leaves both counters untouched.

        The source and target rows are re-read with ``SELECT ... FOR UPDATE``
        in a deterministic, sorted-primary-key order at the start of the
        transaction, and every guard runs against those locked, fresh rows.
        This closes the TOCTOU window on this node's and the new parent's own
        columns, and makes concurrent ``A -> B`` / ``B -> A`` moves contend on
        the same first row instead of deadlocking.

        .. warning::

            The cycle guard is a point-in-time check.  The descendant set is
            materialised with a recursive CTE over **unlocked** rows and the
            bulk ``UPDATE`` does not re-check it: a concurrent write that
            moves a node into or out of this subtree between that read and the
            update can defeat the guard (a cycle) or leave the moved-in node
            with a stale ``depth``.  Serialise concurrent writers per tree
            (for example with a PostgreSQL advisory lock on the namespace)
            when that race matters.

        Args:
            new_parent: The target parent node, or ``None`` to detach this
                node and make it a root.

        Raises:
            GraphError: If either node is unpersisted, the move crosses
                namespaces, targets this node itself, or targets one of this
                node's own descendants.
        """
        if self.pk is None:
            raise GraphError(
                f"Cannot move unpersisted {type(self).__name__}: save it first."
            )
        if new_parent is not None and new_parent.pk is None:
            raise GraphError(
                "Cannot move a node under an unpersisted parent: save the parent first."
            )
        self_pk = cast(UUID | int, self.pk)
        target_pk = cast(UUID | int, new_parent.pk) if new_parent is not None else None

        async with in_transaction() as raw_conn:
            # The stub types the yielded connection as ``Any``; annotate it as
            # the concrete transactional client (no runtime cast needed).
            db: BaseDBAsyncClient = raw_conn

            # Deterministic lock order: both moves of an A <-> B pair take the
            # sorted-first row first, so they serialise instead of deadlocking
            # on opposite acquisition orders.  A self-move locks one row.
            lock_pks: list[UUID | int] = [self_pk]
            if target_pk is not None and target_pk != self_pk:
                lock_pks.append(target_pk)
            lock_pks.sort(key=str)
            locked: dict[UUID | int, Self] = {}
            for pk in lock_pks:
                locked[pk] = await type(self).select_for_update(using_db=db).get(pk=pk)
            locked_self = locked[self_pk]
            locked_parent = locked[target_pk] if target_pk is not None else None

            if locked_parent is not None:
                # Namespace guard — a parent from another tenant would move
                # this subtree into that tenant's tree.
                if locked_parent.namespace != locked_self.namespace:
                    raise GraphError(
                        "Cannot move a node across namespaces: "
                        f"source namespace {locked_self.namespace!r}, "
                        f"target namespace {locked_parent.namespace!r}"
                    )
                # Self-move — a cycle by definition, and the descendant check
                # below cannot see it (the subtree read excludes self).
                if locked_parent.pk == self_pk:
                    raise GraphError("Cannot move a node under itself")

            # Cycle guard: adjacency lists carry no path column, so the
            # descendant set has to be materialised and tested explicitly.
            descendant_ids = await _descendant_ids(type(self), self_pk, db)
            if locked_parent is not None and locked_parent.pk in descendant_ids:
                raise GraphError("Cannot move a node into its own descendant")

            new_parent_id: UUID | int | None = (
                cast(UUID | int, locked_parent.pk)
                if locked_parent is not None
                else None
            )
            new_depth = locked_parent.depth + 1 if locked_parent is not None else 0
            old_parent_id = locked_self.parent_id
            depth_delta = new_depth - locked_self.depth

            # Update this node (an ORM UPDATE bypasses save(), so the
            # child_count hook below is the only counter maintenance).
            _ = (
                await type(self)
                .filter(pk=self_pk)
                .using_db(db)
                .update(
                    parent_id=new_parent_id,
                    depth=new_depth,
                    is_root=new_parent_id is None,
                )
            )

            # One bulk UPDATE shifts the whole subtree: every descendant's
            # depth moves by the same delta as the moved node's.
            if descendant_ids and depth_delta != 0:
                _ = (
                    await type(self)
                    .filter(pk__in=descendant_ids)
                    .exclude(pk=self_pk)
                    .using_db(db)
                    .update(depth=F("depth") + depth_delta)
                )

            # Denormalized degree counters: move one child from the old
            # parent to the new one.  Atomic F() arithmetic means the counters
            # stay correct without reading them first; equal old/new parent
            # (or both ``None``) needs no adjustment at all.
            if old_parent_id != new_parent_id:
                if old_parent_id is not None:
                    _ = (
                        await type(self)
                        .filter(pk=old_parent_id)
                        .using_db(db)
                        .update(child_count=F("child_count") - 1)
                    )
                if new_parent_id is not None:
                    _ = (
                        await type(self)
                        .filter(pk=new_parent_id)
                        .using_db(db)
                        .update(child_count=F("child_count") + 1)
                    )

            # This instance still holds the pre-move parent/depth/is_root —
            # refresh it (on the transaction connection) so callers see the
            # node's new tree position.
            await self.refresh_from_db(using_db=db)

    async def validate_node(self) -> list[str]:
        """Check this node's own invariants and return any error strings.

        Reads the persisted row (not the in-memory copy) and validates:

        * ``is_root`` agrees with ``parent_id is None``;
        * ``depth`` equals the parent's ``depth + 1`` (``0`` for a root);
        * ``child_count`` equals the number of live children;
        * the parent exists in the same namespace;
        * walking ``parent_id`` from here terminates — no cycle.

        This is deliberately **one node**, not a whole-subtree audit: call it
        in a loop to survey a tree, or use :meth:`rebuild_derived` to repair
        rows written outside the library.

        Returns:
            List of human-readable error descriptions — empty when the node
            is valid.
        """
        errors: list[str] = []
        if self.pk is None:
            return [f"Unpersisted {type(self).__name__}: nothing to validate"]

        pk = cast(UUID | int, self.pk)
        stored = await type(self).get_or_none(pk=pk)
        if stored is None:
            return [f"Node {pk} does not exist"]

        if (stored.parent_id is None) != bool(stored.is_root):
            errors.append(
                f"is_root mismatch: is_root={stored.is_root!r} but "
                f"parent_id={stored.parent_id!r}"
            )

        if stored.parent_id is None:
            if stored.depth != 0:
                errors.append(
                    f"Depth mismatch: root node has depth {stored.depth}, expected 0"
                )
        else:
            parent = await type(self).get_or_none(
                pk=stored.parent_id, namespace=stored.namespace
            )
            if parent is None:
                errors.append(f"Parent {stored.parent_id} does not exist")
            else:
                expected_depth = parent.depth + 1
                if stored.depth != expected_depth:
                    errors.append(
                        f"Depth mismatch: expected {expected_depth} "
                        f"(parent depth {parent.depth} + 1) but got {stored.depth}"
                    )

        child_count = (
            await type(self).filter(parent_id=pk, namespace=stored.namespace).count()
        )
        if stored.child_count != child_count:
            errors.append(
                f"child_count mismatch: stored {stored.child_count} but "
                f"{child_count} child row(s) exist"
            )

        # Cycle detection: follow parent_id upwards and stop on a repeat.
        seen: set[UUID | int] = {pk}
        cursor = stored.parent_id
        while cursor is not None:
            if cursor in seen:
                errors.append(f"Parent chain contains a cycle at {cursor}")
                break
            seen.add(cursor)
            step = await type(self).get_or_none(pk=cursor)
            if step is None:
                break
            cursor = step.parent_id

        return errors

    @classmethod
    async def rebuild_derived(cls, *, namespace: str | None = None) -> int:
        """Recompute ``depth``, ``is_root`` and ``child_count`` from ``parent_id``.

        The repair path for rows written outside the library (raw SQL, a bulk
        load, a migration): one recursive-CTE ``UPDATE`` walks ``parent_id``
        from the roots and rewrites the derived columns.  Only rows that
        actually differ are written, so a second call returns ``0``.

        Nodes unreachable from a root are left untouched: an orphan (its
        ``parent_id`` names no row) or a row inside a parent cycle has no
        depth to derive.  :meth:`validate_node` reports both cases.

        Args:
            namespace: Restrict the rebuild to one tenant; ``None`` rebuilds
                every tree in the table.

        Returns:
            Number of rows whose derived columns changed.
        """
        table = quote_ident(cls._meta.db_table)
        pk_column = quote_ident(cls._meta.db_pk_column)
        parent_field = cls._meta.fields_map["parent_id"]
        parent_column = quote_ident(
            parent_field.source_field or parent_field.model_field_name
        )

        params: list[RowValue] = []
        root_filter = ""
        if namespace is not None:
            params.append(namespace)
            root_filter = f" AND {quote_ident('namespace')} = $1"

        # ``RETURNING`` makes this a query, so the client reports the count
        # from the fetched rows; the ``IS DISTINCT FROM`` filter keeps that
        # count honest (a blind UPDATE would count every row).
        sql = (
            f"WITH RECURSIVE tree(nid, depth) AS ("
            f"SELECT {pk_column}, 0 FROM {table} "
            f"WHERE {parent_column} IS NULL{root_filter} "
            "UNION "
            f"SELECT child.{pk_column}, tree.depth + 1 FROM {table} AS child "
            f"JOIN tree ON child.{parent_column} = tree.nid"
            "), counted AS ("
            "SELECT tree.nid, tree.depth, (tree.depth = 0) AS is_root, "
            f"(SELECT count(*) FROM {table} AS c "
            f"WHERE c.{parent_column} = tree.nid) AS child_count "
            "FROM tree"
            ") "
            f"UPDATE {table} AS node SET "
            "depth = counted.depth, "
            "is_root = counted.is_root, "
            "child_count = counted.child_count "
            f"FROM counted WHERE node.{pk_column} = counted.nid "
            "AND (node.depth IS DISTINCT FROM counted.depth "
            "OR node.is_root IS DISTINCT FROM counted.is_root "
            "OR node.child_count IS DISTINCT FROM counted.child_count) "
            f"RETURNING node.{pk_column}"
        )
        affected, _rows = await connections.get("default").execute_query(
            sql, params or None
        )
        return affected

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
