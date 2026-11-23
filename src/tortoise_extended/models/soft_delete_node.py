"""Soft-deleting graph node base: ``deleted_at`` over the adjacency list.

Combines :class:`~tortoise_extended.models.soft_delete.BaseSoftDeleteModel`
(the ``deleted_at`` column plus the auto-filtering queryset) with
:class:`~tortoise_extended.models.node.NodeMixin` (adjacency-list fields,
traversal helpers and the denormalized ``child_count``).

Why a dedicated base: ``BaseSoftDeleteModel.delete()`` sets ``deleted_at`` and
calls ``save(update_fields=["deleted_at"])``.  It never reaches
``NodeMixin.delete()``, so on a plain
``class X(BaseSoftDeleteModel, NodeMixin)`` the parent ``child_count``
decrement in the MRO is bypassed — and a single-row soft delete would leave
the children visible anyway.  This base replaces both halves with one
subtree-aware implementation.

Subtree semantics
-----------------

``delete()`` and ``restore()`` act on the node **and every descendant reached
through live ``parent_id`` links**, in one transaction.  Siblings are never
touched.  The instance path and the queryset path share the same core:

* ``Node.filter(...).delete()`` soft-deletes the matched rows and their
  subtrees — the defect where ``Parent.filter(pk=...).delete()`` left the
  children visible does not apply to this queryset;
* ``Node.filter(...).restore()`` restores the soft-deleted rows it matches and
  their subtrees (the base queryset semantics, widened to the subtree);
* a **physical** delete (``with_deleted().delete()`` / ``hard_delete()``)
  keeps the base behaviour: only the matched rows are removed, children keep a
  dangling ``parent_id`` per the :class:`NodeMixin` orphan policy.

The subtree is materialised with the ORM (one query per tree level) and the
whole subtree is stamped with a **single bulk ``UPDATE``** over ``pk__in=``.
A bulk update fires no per-row ``pre_save``/``post_save`` signals and does not
run ``save()`` — ``deleted_at`` on the instance is updated in memory by the
instance path, the rows themselves only get the column write.

``child_count`` invariant
-------------------------

``child_count`` counts **live** children: for a live node it always equals its
live direct children.  A subtree soft delete zeroes the counters *inside* the
subtree (every live child of a deleted node went with it) and decrements each
**live** parent by the number of its children entering the subtree — exactly
one for the usual single-root delete.  ``restore()`` recomputes every restored
node's counter from its live children and increments each live parent by the
number of children it gets back.  A deleted row's own counter reads ``0``
until it is restored; that is what makes a later restore land on the exact
value, including children created while the row was hidden.

``_block_orphan_delete`` is a **hard**-delete guard: a soft delete hides the
children instead of orphaning them, so the guard never fires here (and
``NodeMixin.delete()`` is not on this path).

Visibility in the CTE builders: ``GraphTraversal``, the pathfinding helpers
and ``GraphVectorSearch`` add ``deleted_at IS NULL`` whenever the node model
declares the column, so a soft-deleted node disappears from
``descendants()`` / ``shortest_path()`` without any extra work.

Caveat: ``rebuild_derived()`` (inherited) is a raw-SQL hard-delete repair that
counts **all** children, deleted ones included — do not use it on a
soft-delete node tree; ``delete()`` / ``restore()`` / ``save()`` maintain the
counters.  The parent-walking helpers (``path_to_root``, ``root``,
``is_ancestor_of``) read through the live-only manager, so they raise
``GraphError`` when a live node's ancestor is soft-deleted — restore the
ancestor (or query with ``with_deleted()``) instead.  The subtree walk is a
point-in-time read: a concurrent insert under a node between the walk and the
``UPDATE`` is not part of that delete — serialise writers per tree when that
race matters.

Requires: PostgreSQL + Tortoise ORM

Usage::

    from tortoise_extended.models.node import BaseNode
    from tortoise_extended.models.soft_delete_node import BaseSoftDeleteNode

    class Category(BaseSoftDeleteNode, BaseNode):
        class Meta:
            table = "categories"
            indexes = (("namespace", "depth"),)

    root = await Category.create(name="Electronics")
    laptops = await root.add_child(name="Laptops")
    macbooks = await laptops.add_child(name="MacBooks")
    phones = await root.add_child(name="Phones")

    await laptops.delete()      # soft-deletes Laptops + MacBooks, keeps Phones
    await laptops.restore()     # both rows live again

    await Category.filter(name="Electronics").delete()  # root + whole subtree
"""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self, cast, override
from uuid import UUID

from tortoise.expressions import F
from tortoise.models import Model
from tortoise.transactions import in_transaction

from ..exceptions import GraphError
from .node import NodeMixin
from .soft_delete import BaseSoftDeleteModel, SoftDeleteQuerySet

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient
    from tortoise.queryset import QuerySet


def _pk_of(row: Model) -> UUID | int:
    """Return a node row's primary key.

    The tortoise stubs type ``Model.pk`` as ``Any`` (the pk field is chosen per
    concrete class); this is the boundary where it is narrowed.
    """
    return cast(UUID | int, row.pk)


def _parent_of(row: Model) -> UUID | int | None:
    """Return a node row's ``parent_id``.

    ``NodeMixin`` only *annotates* ``parent_id`` (the concrete class declares
    the column), so it is read off the instance rather than the class.
    """
    return cast(UUID | int | None, getattr(row, "parent_id", None))


async def _subtree_rows[MODELT: Model](
    visible: QuerySet[MODELT], roots: Sequence[MODELT]
) -> tuple[list[MODELT], Counter[UUID | int]]:
    """Materialise the subtree of *roots* and the outside-parent multiplicities.

    Walks ``parent_id`` one level at a time through *visible* — the live rows
    for a delete, the ``only_deleted()`` rows for a restore — so rows already
    hidden are never re-stamped and live rows are never resurrected.  The walk
    is iterative (no Python recursion) and guarded by the visited map, so a
    pre-existing parent cycle terminates instead of looping.

    Args:
        visible: Mode-scoped queryset the walk reads through.
        roots: Already matched rows; they are part of the result.

    Returns:
        The affected rows (roots included, de-duplicated) and, per parent
        **outside** the affected set, how many affected rows hang off it.
    """
    affected: dict[UUID | int, MODELT] = {}
    for root in roots:
        _ = affected.setdefault(_pk_of(root), root)

    frontier = list(affected)
    while frontier:
        children = await visible.filter(parent_id__in=frontier)
        next_frontier: list[UUID | int] = []
        for child in children:
            pk = _pk_of(child)
            if pk in affected:
                continue
            affected[pk] = child
            next_frontier.append(pk)
        frontier = next_frontier

    outside: Counter[UUID | int] = Counter()
    for pk, row in affected.items():
        parent_id = _parent_of(row)
        if parent_id is not None and parent_id not in affected:
            outside[parent_id] += 1
    return list(affected.values()), outside


async def _apply_live_parent_deltas[MODELT: Model](
    model: type[MODELT],
    outside: Counter[UUID | int],
    db: BaseDBAsyncClient,
    *,
    sign: int,
) -> None:
    """Add or subtract ``outside`` counts on each **live** parent.

    The live-only filter is deliberate: a soft-deleted parent's counter was
    zeroed when it was hidden and is recomputed when it is restored, so
    touching it here would double-count (and can go negative).
    """
    grouped: dict[int, list[UUID | int]] = {}
    for parent_id, delta in outside.items():
        grouped.setdefault(delta, []).append(parent_id)
    for delta, parent_ids in grouped.items():
        change = F("child_count") + delta if sign > 0 else F("child_count") - delta
        _ = (
            await model.filter(pk__in=parent_ids)
            .using_db(db)
            .update(child_count=change)
        )


async def _recompute_child_counts[MODELT: Model](
    model: type[MODELT], restored_ids: Sequence[UUID | int], db: BaseDBAsyncClient
) -> None:
    """Rebuild ``child_count`` for every restored node from its live children.

    One fetch plus one ``UPDATE`` per distinct count value (normally two
    statements: the leaves and everything else).  This is exact even when
    children were created while the node was hidden.
    """
    children = await model.filter(parent_id__in=list(restored_ids)).using_db(db)
    counts: Counter[UUID | int] = Counter()
    for child in children:
        parent_id = _parent_of(child)
        if parent_id is not None:
            counts[parent_id] += 1
    grouped: dict[int, list[UUID | int]] = {}
    for pk in restored_ids:
        grouped.setdefault(counts.get(pk, 0), []).append(pk)
    for value, pks in grouped.items():
        _ = await model.filter(pk__in=pks).using_db(db).update(child_count=value)


async def _soft_delete_subtree[MODELT: Model](
    model: type[MODELT],
    roots: Sequence[MODELT],
    db: BaseDBAsyncClient,
    *,
    deleted_at: datetime,
) -> int:
    """Stamp ``deleted_at`` on *roots* and their live subtree; return the count."""
    rows, outside = await _subtree_rows(model.filter().using_db(db), roots)
    ids = [_pk_of(row) for row in rows]
    affected = (
        await model.filter(pk__in=ids)
        .using_db(db)
        .update(deleted_at=deleted_at, child_count=0)
    )
    await _apply_live_parent_deltas(model, outside, db, sign=-1)
    return affected


async def _restore_subtree[MODELT: Model](
    model: type[MODELT], roots: Sequence[MODELT], db: BaseDBAsyncClient
) -> int:
    """Clear ``deleted_at`` on *roots* and their soft-deleted subtree; return the count."""
    deleted = SoftDeleteQuerySet(model).only_deleted().using_db(db)
    rows, outside = await _subtree_rows(deleted, roots)
    ids = [_pk_of(row) for row in rows]
    restored = await deleted.filter(pk__in=ids).update(deleted_at=None)
    await _recompute_child_counts(model, ids, db)
    await _apply_live_parent_deltas(model, outside, db, sign=1)
    return restored


class SoftDeleteNodeQuerySet[MODELT: Model](SoftDeleteQuerySet[MODELT]):
    """Subtree-aware soft-delete queryset, returned by every node manager.

    ``delete()`` on a live queryset soft-deletes every matched row **and its
    subtree**; ``restore()`` restores the soft-deleted rows it matches and
    their subtrees.  ``with_deleted().delete()`` / ``hard_delete()`` keep the
    base physical-delete behaviour (matched rows only — children keep a
    dangling ``parent_id``, see the :class:`NodeMixin` orphan policy).

    Each operation runs in one transaction, so the subtree stamp, the
    ``child_count`` recomputation and the parent deltas land together.
    """

    @override
    def _clone(self) -> SoftDeleteNodeQuerySet[MODELT]:
        return cast(SoftDeleteNodeQuerySet[MODELT], super()._clone())

    @override
    async def delete(
        self,
    ) -> int:
        """Soft-delete the matched rows and their subtrees.

        Returns:
            Number of rows soft-deleted (the physical-delete modes return the
            number of rows removed, as in the base queryset).
        """
        if self._sd_mode != 0:
            return await super().delete()
        async with in_transaction() as raw_conn:
            db: BaseDBAsyncClient = raw_conn
            roots = await self.using_db(db)
            if not roots:
                return 0
            return await _soft_delete_subtree(
                self.model, roots, db, deleted_at=datetime.now(UTC)
            )

    @override
    async def restore(self) -> int:
        """Restore the matched soft-deleted rows and their subtrees.

        Returns:
            Number of rows restored.
        """
        async with in_transaction() as raw_conn:
            db: BaseDBAsyncClient = raw_conn
            roots = await self.only_deleted().using_db(db)
            if not roots:
                return 0
            return await _restore_subtree(self.model, roots, db)


class BaseSoftDeleteNode(BaseSoftDeleteModel, NodeMixin):
    """Abstract base: subtree-aware soft delete over an adjacency list.

    Layering: :class:`~tortoise_extended.models.soft_delete.BaseSoftDeleteModel`
    supplies the ``deleted_at`` column, the live-only manager surface and
    ``with_deleted()`` / ``only_deleted()``;
    :class:`~tortoise_extended.models.node.NodeMixin` supplies ``parent_id``,
    ``depth``, ``is_root``, ``child_count``, ``namespace`` and the traversal
    helpers.  The base itself stays pk-free: declare an identity base in front
    of it (``class Category(BaseSoftDeleteNode, BaseNode)``) and redeclare
    ``Meta.indexes`` plus, when not supplied by that base, ``parent_id``.

    Soft delete is **subtree-scoped** and keeps ``child_count`` counting live
    children: the live parent of a deleted subtree loses exactly one child and
    ``restore()`` gives it back; the counters inside the subtree are zeroed on
    delete and recomputed on restore.  ``_block_orphan_delete`` never fires —
    it is a hard-delete guard, and a soft delete hides children instead of
    orphaning them.

    The bulk ``UPDATE`` fires no per-row ``pre_save``/``post_save`` signals.
    ``rebuild_derived()`` (inherited) is a raw-SQL hard-delete repair that
    counts all children, deleted ones included — do not use it to repair a
    soft-delete node tree.

    Usage::

        class Category(BaseSoftDeleteNode, BaseNode):
            class Meta:
                table = "categories"
                indexes = (("namespace", "depth"),)

        await laptops.delete()   # Laptops + every descendant
        await laptops.restore()  # back to live
    """

    class Meta:
        abstract = True
        # Tortoise does not propagate Meta.indexes from abstract bases; the
        # pair is repeated here to document what every concrete subclass must
        # redeclare (NodeMixin.__init_subclass__ enforces it).
        indexes = (("namespace", "depth"),)

    @classmethod
    @override
    # QuerySet's MODEL TypeVar is invariant, so a narrower
    # SoftDeleteNodeQuerySet[Self] return cannot be expressed in the base stub;
    # the widening is intentional (runtime override semantics unchanged) — the
    # same directive BaseSoftDeleteModel._db_queryset carries.
    def _db_queryset(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls,
        using_db: BaseDBAsyncClient | None = None,
        for_write: bool = False,
    ) -> SoftDeleteNodeQuerySet[Self]:
        db = using_db or cls._choose_db(for_write)
        return cast(
            SoftDeleteNodeQuerySet[Self],
            SoftDeleteNodeQuerySet(cls).using_db(db),
        )

    @override
    async def delete(self, using_db: BaseDBAsyncClient | None = None) -> None:
        """Soft-delete this node and its whole live subtree, atomically.

        The subtree is stamped with one bulk ``UPDATE`` inside one
        transaction; its internal counters are zeroed and every live parent
        (normally just this node's own) loses the children it gave up.
        Deleting an already soft-deleted node is a no-op, like the base
        implementation.

        Args:
            using_db: Connection to run the writes on. ``None`` opens a
                transaction on the default connection; a supplied handle is
                used as-is (the caller already scopes it).

        Raises:
            GraphError: If this node has not been saved yet (``pk`` is
                ``None``) — there is no subtree to stamp.
        """
        if self.deleted_at is not None:
            return
        if self.pk is None:
            raise GraphError(
                f"Cannot soft-delete unpersisted {type(self).__name__}: save it first."
            )
        now = datetime.now(UTC)
        if using_db is None:
            async with in_transaction() as raw_conn:
                db: BaseDBAsyncClient = raw_conn
                _ = await _soft_delete_subtree(type(self), [self], db, deleted_at=now)
        else:
            _ = await _soft_delete_subtree(type(self), [self], using_db, deleted_at=now)
        self.deleted_at = now

    @override
    async def restore(self) -> None:
        """Restore this node and its soft-deleted subtree, atomically.

        Every soft-deleted descendant is restored with the node (live rows are
        left alone), each restored node's ``child_count`` is recomputed from
        its live children, and every live parent gains the children it gets
        back.  Restoring a live node is a no-op.
        """
        if self.deleted_at is None:
            return
        async with in_transaction() as raw_conn:
            db: BaseDBAsyncClient = raw_conn
            _ = await _restore_subtree(type(self), [self], db)
        self.deleted_at = None
