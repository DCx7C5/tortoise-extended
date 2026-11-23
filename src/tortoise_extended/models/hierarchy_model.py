"""Abstract Tortoise ORM model for ltree-based hierarchy operations.

Provides BaseHierarchyModel base class for tree structures using PostgreSQL
ltree materialized paths combined with adjacency-list fields. Subclasses
inherit ltree path queries, ancestor/descendant traversal, subtree moves,
and hierarchy validation — all backed by declared Tortoise fields.

Requires: PostgreSQL + ``CREATE EXTENSION IF NOT EXISTS ltree;``

Usage::

    from tortoise import fields
    from tortoise_extended.exceptions import HierarchyError
    from tortoise_extended.models.hierarchy_model import BaseHierarchyModel

    class Category(BaseHierarchyModel):
        slug = fields.CharField(max_length=50)

        class Meta:
            table = "categories"
            verbose_name = "Category"
            verbose_name_plural = "Categories"
            # Tortoise does NOT inherit Meta.indexes from the abstract base —
            # redeclare them on every concrete subclass.
            indexes = (
                GiSTIndex(fields=("path",)),
                ("namespace", "depth"),
                ("parent_id", "depth"),
            )
"""

from collections import deque
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, NoReturn, Self, override

from pypika_tortoise.context import SqlContext
from pypika_tortoise.terms import Term, ValueWrapper
from pypika_tortoise.terms import Function as PypikaFunction
from tortoise import fields
from tortoise.expressions import F, Function
from tortoise.models import Model
from tortoise.queryset import QuerySet
from tortoise.transactions import in_transaction

from tortoise_extended._types import CacheValue
from tortoise_extended.exceptions import HierarchyError
from tortoise_extended.fields import CharField
from tortoise_extended.fields.ltree import LTreeField
from tortoise_extended.indexes.ltree_index import GiSTIndex

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient

# ── Internal helpers ────────────────────────────────────────────────────


class _PrefixReplaceFunction(PypikaFunction):
    """pypika term emitting a prefix-only path replacement.

    Renders ``CONCAT(<new>::text, SUBSTRING(<field>::text FROM <start>)::text)::ltree``
    — a ``substring`` from just past the old prefix, so only the leading
    label sequence is rewritten.  This matters because SQL ``REPLACE()``
    rewrites *every* occurrence: a descendant whose path repeats the moved
    ancestor's label sequence deeper in the tree (e.g. ``a.b.c.a.b`` under
    ``a.b``) would otherwise be corrupted.
    """

    def __init__(self, field: Term, old_prefix: str, new_prefix: str) -> None:
        super().__init__("PREFIX_REPLACE", field)
        self._field = field
        self._new_wrapper = ValueWrapper(new_prefix)
        self._start = len(old_prefix) + 1

    @override
    def get_sql(self, ctx: SqlContext) -> str:
        """Render ``CONCAT(new, SUBSTRING(field FROM start))`` with casts."""
        field_sql = self._field.get_sql(ctx)
        new_sql = self._new_wrapper.get_sql(ctx)
        return (
            f"CONCAT({new_sql}::text, "
            f"SUBSTRING({field_sql}::text FROM {self._start})::text)::ltree"
        )


class _PrefixReplace(Function):
    """Tortoise ``Function`` wrapping :class:`_PrefixReplaceFunction`.

    Usable as a value in ``QuerySet.update(path=_PrefixReplace(...))``.
    """

    database_func = _PrefixReplaceFunction

    def __init__(self, field: str, old_prefix: str, new_prefix: str) -> None:
        super().__init__(field, old_prefix, new_prefix)


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


class BaseHierarchyModel(Model):
    """Abstract base for ltree-path hierarchy models.

    Combines PostgreSQL ltree materialized paths with adjacency-list columns
    (``parent_id``, ``depth``) so subclasses get both efficient path queries
    *and* simple parent/child lookups.  Every field is declared — no
    ``getattr`` guessing required.

    Subclasses **must** set ``class Meta: table = "..."`` to an explicit name
    and **must redeclare the base indexes** in ``Meta.indexes`` — Tortoise
    does not propagate ``Meta.indexes`` from abstract bases to concrete
    subclasses (confirmed empirically: ``Child._meta.indexes == ()``).  A
    concrete subclass that omits ``Meta.indexes`` fails at import time via
    :meth:`__init_subclass__` instead of silently losing its ltree/GiST and
    adjacency indexes.  Abstract intermediate subclasses are exempt.
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
    name = CharField(
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
    namespace = CharField(
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

    def __init_subclass__(cls, **kwargs: str | int | float | bool | None) -> None:
        """Guard against silently losing the abstract base indexes.

        Tortoise only copies base fields/indexes for abstract bases and does
        not propagate ``Meta.indexes`` to concrete subclasses.  A subclass
        that forgets to redeclare them would run every ltree/adjacency query
        without its indexes — raise at class-creation time instead.

        Raise:
            NotImplementedError: When a concrete subclass (or one without an
                explicit ``Meta``) does not declare ``Meta.indexes``.  Opt
                out deliberately with ``Meta.indexes = ()``.
        """
        super().__init_subclass__(**kwargs)
        meta = cls.__dict__.get("Meta")
        if meta is None:
            raise NotImplementedError(
                f"{cls.__name__} must declare a Meta class with table and indexes. "
                "Tortoise does not propagate Meta.indexes from abstract bases; "
                "redeclare the hierarchy indexes (GiST(path), (namespace, depth), "
                "(parent_id, depth)) on every concrete subclass."
            )
        if getattr(meta, "abstract", False):
            return
        if "indexes" not in meta.__dict__:
            raise NotImplementedError(
                f"{cls.__name__}.Meta must redeclare indexes — Tortoise does not "
                "propagate Meta.indexes from abstract bases. Add the hierarchy "
                "indexes (GiST(path), (namespace, depth), (parent_id, depth)) or "
                "opt out explicitly with indexes = ()."
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

    def get_ancestors(
        self, *, include_self: bool = False, namespace: str | None = None
    ) -> QuerySet[BaseHierarchyModel] | QuerySet[Self]:
        """Return all ancestor nodes from root down to this node's parent.

        Uses the ltree ``@>`` (ancestor-of) operator so PostgreSQL can walk
        the GiST index.  Results are ordered by ``path`` ascending (root
        first).

        Args:
            include_self: When *True*, include this node in the result.
            namespace: Namespace to scope the query to.  Defaults to this
                instance's :attr:`namespace` — required because ltree paths
                are only unique *within* a namespace, so an unscoped path
                prefix match would leak nodes from other tenants.

        Returns:
            Lazy QuerySet of ancestor nodes.
        """
        path_str = _path_to_str(self.path)
        if not path_str:
            return type(self).filter(pk__in=[])

        q: QuerySet[Self] | QuerySet[BaseHierarchyModel] = (
            type(self)
            .filter(
                path__ancestor_of=path_str,
                namespace=namespace if namespace is not None else self.namespace,
            )
            .order_by("path")
        )

        if not include_self:
            q = q.exclude(pk=self.pk)

        return q

    def get_descendants(
        self, *, include_self: bool = False, namespace: str | None = None
    ) -> QuerySet[BaseHierarchyModel] | QuerySet[Self]:
        """Return all descendant nodes below this node.

        Uses the ltree ``<@`` (descendant-of) operator so PostgreSQL can walk
        the GiST index.  Results are ordered by ``path`` ascending (shallowest
        first).

        Args:
            include_self: When *True*, include this node in the result.
            namespace: Namespace to scope the query to.  Defaults to this
                instance's :attr:`namespace` — required because ltree paths
                are only unique *within* a namespace, so an unscoped path
                prefix match would leak nodes from other tenants.

        Returns:
            Lazy QuerySet of descendant nodes.
        """
        path_str = _path_to_str(self.path)
        if not path_str:
            return type(self).filter(pk__in=[])

        q: QuerySet[Self] | QuerySet[BaseHierarchyModel] = (
            type(self)
            .filter(
                path__descendant_of=path_str,
                namespace=namespace if namespace is not None else self.namespace,
            )
            .order_by("path")
        )

        if not include_self:
            q = q.exclude(pk=self.pk)

        return q

    def get_children(self) -> QuerySet[Self] | QuerySet[BaseHierarchyModel]:
        """Return direct children — nodes exactly one depth level below.

        Uses the adjacency-list ``parent_id`` for a precise one-hop query
        instead of counting ltree labels.  Results are ordered by ``name``
        ascending.

        Returns:
            Lazy QuerySet of child nodes.
        """
        return (
            type(self)
            .filter(
                parent_id=self.pk,
            )
            .order_by("name")
        )

    def get_siblings(
        self, *, include_self: bool = False
    ) -> QuerySet[Self] | QuerySet[BaseHierarchyModel]:
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

        q: QuerySet[Self] | QuerySet[BaseHierarchyModel] = (
            type(self)
            .filter(
                parent_id=self.parent_id,
                depth=self.depth,
            )
            .order_by("name")
        )

        if not include_self:
            q = q.exclude(pk=self.pk)

        return q

    # ── Tree Queries (async — execute immediately) ───────────────────────

    async def get_root(self) -> BaseHierarchyModel | Self | None:
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

    async def get_path_to_root(self) -> list[Self] | list[BaseHierarchyModel]:
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
        ancestor_paths = [".".join(components[: i + 1]) for i in range(len(components))]

        nodes = (
            await type(self)
            .filter(
                path__in=ancestor_paths,
                namespace=self.namespace,
            )
            .order_by("path")
        )

        result = list(nodes)

        # Ensure self is present (maybe an unsaved / detached instance).
        if not any(n.pk == self.pk for n in result):
            result.append(self)

        return sorted(result, key=lambda n: n.depth)

    # ── Mutations ────────────────────────────────────────────────────────

    @override
    async def save(
        self,
        using_db: BaseDBAsyncClient | None = None,
        update_fields: Iterable[str] | None = None,
        force_create: bool = False,
        force_update: bool = False,
    ) -> None:
        """Persist this node; refuse to reparent it.

        ``path`` and ``depth`` are a materialized path: a new parent means a
        new path for this row **and** for every descendant, which a single-row
        save cannot write.  A persisted row whose in-memory ``parent_id``
        differs from the stored one therefore raises
        :class:`~tortoise_extended.exceptions.HierarchyError`; :meth:`move_to`
        is the only supported reparent path.

        The persisted parent is read on the same connection before the write
        (with ``SELECT ... FOR UPDATE``) because the in-memory instance may
        already hold the new value.  A partial update via ``update_fields``
        that omits ``parent_id`` writes no parent change, so it is allowed.

        Args:
            using_db: Specific DB connection to use instead of the default.
            update_fields: Optional subset of fields to update.
            force_create: Force an INSERT even for a previously saved row.
            force_update: Force an UPDATE even for an unsaved row.

        Raises:
            HierarchyError: When an update would persist a different
                ``parent_id`` — reparent with :meth:`move_to` instead.
        """
        already_saved = bool(getattr(self, "_saved_in_db", False))
        update_fields_list = list(update_fields) if update_fields is not None else None
        is_create = force_create or (
            not already_saved and update_fields is None and not force_update
        )

        if not is_create and (
            update_fields_list is None or "parent_id" in update_fields_list
        ):
            persisted = await (
                type(self)
                .select_for_update(using_db=using_db)
                .filter(pk=self.pk)
                .only("parent_id")
                .first()
            )
            if persisted is not None and persisted.parent_id != self.parent_id:
                raise HierarchyError(
                    f"Cannot reparent {type(self).__name__} {self.pk} by saving: "
                    f"parent_id {persisted.parent_id!r} -> {self.parent_id!r} "
                    "would leave path, depth and every descendant's path stale. "
                    "Use move_to() to reparent."
                )

        await super().save(
            using_db=using_db,
            update_fields=update_fields,
            force_create=force_create,
            force_update=force_update,
        )

    async def move_to(self, new_parent: Self) -> None:
        """Move this node and all its descendants under *new_parent*.

        This is the **only** supported reparent path — :meth:`save` refuses a
        persisted ``parent_id`` change, because one row cannot rewrite a
        subtree's paths.

        Validates that the move does not create a cycle, then updates this
        node and cascades path/depth changes to every descendant.  The whole
        move runs inside a single transaction, and the descendant cascade is
        a single bulk ``UPDATE`` (no N+1 row-by-row writes) using a
        prefix-only path rewrite so descendant paths that repeat the moved
        ancestor's label sequence deeper in the tree are not corrupted.

        Both the source and target rows are re-read with ``FOR UPDATE`` row
        locks at the start of the transaction, and the namespace / cycle /
        self-move validation runs against those locked, fresh values.  This
        closes a TOCTOU race: without the locks the pre-move checks would
        validate against possibly-stale in-memory nodes while a concurrent
        writer moved them, letting a cycle or cross-namespace move slip
        through.

        Args:
            new_parent: The target parent node.

        Raises:
            HierarchyError: If either node lacks a path, the move targets
                this node itself, or the move would create a cycle (moving a
                node into its own descendant).
        """
        # Fast path: a missing/empty path is checkable without DB access, so
        # surface it before locking rows.  This also covers detached (unpersisted)
        # nodes, which have no row to lock.
        if not _path_to_str(self.path) or not _path_to_str(new_parent.path):
            raise HierarchyError("Both source and target must have paths")

        async with in_transaction():
            # Lock the source and target rows so no concurrent move of either
            # node can interleave between our validation and our writes.
            locked_self = await type(self).select_for_update().get(pk=self.pk)
            locked_parent = (
                await type(new_parent).select_for_update().get(pk=new_parent.pk)
            )

            self_path_str = _path_to_str(locked_self.path)
            new_parent_path_str = _path_to_str(locked_parent.path)

            # Namespace guard — ltree paths are only unique within a namespace,
            # so adopting a parent from another tenant would corrupt the moved
            # subtree's paths (and leak into the other tenant's tree).
            if locked_parent.namespace != locked_self.namespace:
                raise HierarchyError(
                    "Cannot move a node across namespaces: "
                    f"source namespace {locked_self.namespace!r}, "
                    f"target namespace {locked_parent.namespace!r}"
                )

            # Self-move — moving a node under itself is a cycle by definition
            # and is not caught by the descendant prefix check below (paths
            # are equal).
            if locked_parent.pk == locked_self.pk:
                raise HierarchyError("Cannot move a node under itself")

            # Cycle guard — new_parent must not sit inside this node's subtree.
            if new_parent_path_str.startswith(self_path_str + "."):
                raise HierarchyError("Cannot move a node into its own descendant")

            old_path = self_path_str
            new_path = f"{new_parent_path_str}.{locked_self.name}"
            depth_delta = locked_parent.depth - locked_self.depth + 1

            # Update this node.
            _ = (
                await type(self)
                .filter(pk=locked_self.pk)
                .update(
                    path=new_path,
                    parent_id=locked_parent.pk,
                    depth=locked_parent.depth + 1,
                )
            )

            # Cascade path prefix replacement + depth shift to every
            # descendant in a single bulk UPDATE.  The ltree ``@>`` filter is
            # label-boundary precise, so only true descendants match; the
            # namespace filter keeps the move scoped to this tenant.
            _ = (
                await type(self)
                .filter(
                    path__descendant_of=old_path,
                    namespace=locked_self.namespace,
                )
                .exclude(pk=locked_self.pk)
                .update(
                    path=_PrefixReplace("path", old_path, new_path),
                    depth=F("depth") + depth_delta,
                )
            )

            # This instance still holds the pre-move path/depth/parent_id —
            # refresh it so callers see the new tree position.
            await self.refresh_from_db()

    # ── Bulk writes ──────────────────────────────────────────────────────

    @classmethod
    async def bulk_add_children(
        cls,
        parent: Self | None,
        rows: Sequence[dict[str, CacheValue]],
        *,
        using_db: BaseDBAsyncClient | None = None,
    ) -> list[Self]:
        """Insert a batch of siblings with ``path`` and ``depth`` derived.

        Seeds one level of a materialized-path tree: each row's ``path`` is
        the parent's path plus its own ``name``, and ``depth`` the parent's
        depth ``+ 1``.  A plain ``bulk_create`` skips both — which is why it
        raises on this base — and leaves a tree that ``validate_hierarchy()``
        reports but nothing notices at write time.

        Rows are inserted one at a time inside one transaction (Tortoise's
        ``bulk_create`` cannot return database-generated keys, and the next
        level needs them).  For a raw ``COPY``-speed load, insert outside the
        library and repair with :meth:`rebuild_paths`.

        Each entry of *rows* is a mapping of ordinary field values.  ``name``
        is required and must be a single ltree label (non-empty, no ``"."``);
        ``path``, ``parent_id`` and ``depth`` are **derived** and must not
        appear in the row.  ``namespace`` defaults to the parent's and must
        not name another tenant.  Pass ``parent=None`` to seed roots: their
        ``path`` is the ``name`` and their ``depth`` is ``0``.

        Args:
            parent: The parent node, or ``None`` to create roots.
            rows: One mapping of field values per new node, in insert order.
            using_db: Connection to run the INSERT on; ``None`` uses the
                default connection (inside an ambient transaction when one is
                open).

        Returns:
            The created instances, in *rows* order.

        Raises:
            HierarchyError: If *parent* is unpersisted, a row omits or
                malforms ``name``, a row sets a derived column, or a row names
                another namespace.
        """
        if not rows:
            return []
        if parent is not None and parent.pk is None:
            raise HierarchyError(
                f"Cannot bulk-add children to an unpersisted {cls.__name__}: "
                "save the parent node first."
            )

        parent_id = None if parent is None else parent.pk
        depth = 0 if parent is None else parent.depth + 1
        parent_path = "" if parent is None else parent.path_str
        parent_namespace = None if parent is None else parent.namespace

        prepared: list[dict[str, CacheValue]] = []
        for index, row in enumerate(rows):
            values = dict(row)
            for derived in ("path", "parent_id", "depth"):
                if derived in values:
                    raise HierarchyError(
                        f"bulk_add_children row {index} sets {derived!r}, which is "
                        "derived from the parent; remove it from the row."
                    )
            name = values.get("name")
            if not isinstance(name, str) or not name:
                raise HierarchyError(
                    f"bulk_add_children row {index} needs a non-empty 'name': it "
                    "becomes the last label of the ltree path."
                )
            if "." in name:
                raise HierarchyError(
                    f"bulk_add_children row {index} has name {name!r}: '.' is the "
                    "ltree label separator and cannot appear in a single label."
                )
            if parent_namespace is not None:
                row_namespace = values.get("namespace", parent_namespace)
                if row_namespace != parent_namespace:
                    raise HierarchyError(
                        f"bulk_add_children row {index} targets namespace "
                        f"{row_namespace!r} but the parent is in "
                        f"{parent_namespace!r}: a child cannot cross namespaces."
                    )
                values["namespace"] = parent_namespace
            values["path"] = f"{parent_path}.{name}" if parent_path else name
            values["parent_id"] = parent_id
            values["depth"] = depth
            prepared.append(values)

        async def _insert(db: BaseDBAsyncClient | None) -> list[Self]:
            objects: list[Self] = []
            # ``create()`` runs Tortoise's INSERT path (generated key, auto_now
            # timestamps, field defaults) instead of ``bulk_create``, which
            # returns no keys; ``path``/``depth`` are already derived above.
            for values in prepared:
                objects.append(await cls.create(using_db=db, **values))
            return objects

        # A level is one unit: a failure halfway through must not leave it
        # half-inserted.  A caller-supplied connection is already scoped.
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
        """Refuse ``bulk_create`` on hierarchy models.

        The INSERT bypasses :meth:`save`, so ``path`` and ``depth`` are never
        derived: a bulk-inserted child inherits whatever the constructor
        defaults gave it, and ``validate_hierarchy()`` (or an ltree prefix
        query) is the only thing that ever notices.

        Use :meth:`bulk_add_children` to seed a level, or
        :meth:`rebuild_paths` after a raw ``COPY`` / SQL load.

        Raises:
            HierarchyError: Always — this method never inserts.
        """
        raise HierarchyError(
            f"{cls.__name__}.bulk_create() is not supported: it does not run "
            "save(), so path/depth would be silently wrong. Use "
            f"`await {cls.__name__}.bulk_add_children(parent, rows)` to seed a "
            "level, or `rebuild_paths()` after a raw load."
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
        """Refuse ``bulk_update`` on hierarchy models.

        A bulk UPDATE can persist a new ``parent_id`` while ``path``,
        ``depth`` and every descendant's path keep their old values.
        Reparenting is :meth:`move_to`'s job — it locks both rows, guards
        against cycles and rewrites the whole subtree's paths in one
        transaction.  :meth:`rebuild_paths` is the repair path (it writes
        through the unguarded ORM call internally).

        Raises:
            HierarchyError: Always — this method never writes.
        """
        raise HierarchyError(
            f"{cls.__name__}.bulk_update() is not supported: it does not run "
            "save(), so path/depth and descendant paths would be silently "
            "wrong. Use move_to() to reparent, or rebuild_paths() to repair."
        )

    # ── Validation ───────────────────────────────────────────────────────

    async def validate_hierarchy(self) -> list[str]:
        """Check this node's invariants and return any error strings.

        Validates:

        * Path is not empty.
        * Last path component matches :attr:`name`.
        * ``depth`` equals the number of ``"."`` separators (root depth 0).
        * ``parent_id`` references an existing node **in the same namespace**
          whose path is a prefix of this node's path.

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

        # Parent must exist in this node's namespace and its path must be a
        # proper prefix.  The namespace filter matters because ``parent_id``
        # is a bare BigInt column with no cross-namespace uniqueness.
        if self.parent_id is not None:
            parent = await type(self).get_or_none(
                pk=self.parent_id, namespace=self.namespace
            )
            if parent is None:
                errors.append(f"Parent {self.parent_id} does not exist")
            else:
                parent_path_str = _path_to_str(parent.path)
                if parent_path_str and not path_str.startswith(parent_path_str + "."):
                    errors.append(
                        f"Parent path {parent_path_str!r} is not a prefix "
                        f"of {path_str!r}"
                    )

        return errors

    @classmethod
    async def rebuild_paths(cls, *, namespace: str | None = None) -> int:
        """Recompute ``path`` and ``depth`` for every reachable node.

        The repair path for rows written outside the library — raw SQL, a
        migration, a bulk load — mirroring django-mptt's ``rebuild()``.  Roots
        are processed first and children inherit their parent's path, so the
        rewrite happens in dependency order without recursion limits.

        Nodes unreachable from a root (an orphan whose ``parent_id`` names no
        row, or a row inside a parent cycle) are skipped; their ``parent_id``
        gives no path to derive.  ``validate_hierarchy()`` reports them.

        Args:
            namespace: Restrict the rebuild to one tenant; ``None`` rebuilds
                every tree in the table.

        Returns:
            Number of rows whose ``path`` or ``depth`` changed.
        """
        query = cls.all()
        if namespace is not None:
            query = query.filter(namespace=namespace)
        nodes = list(await query)

        children: dict[int, list[Self]] = {}
        roots: list[Self] = []
        for node in nodes:
            parent_id = node.parent_id
            if parent_id is None:
                roots.append(node)
            else:
                children.setdefault(parent_id, []).append(node)

        changed: list[Self] = []
        seen: set[int] = set()
        queue: deque[tuple[Self, str, int]] = deque(
            (root, root.name, 0) for root in roots
        )
        while queue:
            node, path_str, depth = queue.popleft()
            node_pk = node.pk
            if node_pk is None or node_pk in seen:
                # ``seen`` also terminates a parent cycle that happens to be
                # reachable through a duplicated key.
                continue
            seen.add(node_pk)
            if node.path_str != path_str or node.depth != depth:
                node.path = path_str
                node.depth = depth
                changed.append(node)
            for child in children.get(node_pk, ()):
                queue.append((child, f"{path_str}.{child.name}", depth + 1))

        if changed:
            # Bypass this class's own guard: rebuild_paths IS the repair
            # path that guard points at.
            _ = await super(BaseHierarchyModel, cls).bulk_update(
                changed, fields=["path", "depth"]
            )
        return len(changed)
