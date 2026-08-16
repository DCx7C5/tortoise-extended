"""Soft-delete model + queryset.

Provides ``BaseSoftDeleteModel`` (a ``deleted_at`` column) and
``SoftDeleteQuerySet`` (auto-filters ``deleted_at IS NULL``).  Every
``all()``/``filter()``/``get()``/``count()``/``exists()``/``update()`` call
on the default manager excludes soft-deleted rows; opt out per-query with
``.with_deleted()`` or ``.only_deleted()``.

``delete()`` semantics on ``SoftDeleteQuerySet`` are mode-dependent:

* default (live-only) queryset — ``delete()`` performs a **soft delete**
  (sets ``deleted_at``; the row stays in the DB and is visible through
  ``with_deleted()``);
* ``with_deleted()`` / ``only_deleted()`` querysets — ``delete()`` performs
  a **physical** ``DELETE`` (purge semantics).  ``hard_delete()`` is the
  explicit spelling of the purge and is equivalent to
  ``with_deleted().delete()``.

Verified against Tortoise ORM 1.1.7 internals: model entry points funnel
through ``_db_queryset()`` / ``manager.get_queryset()``, so
``BaseSoftDeleteModel`` overrides the classmethods and ``SoftDeleteQuerySet``
injects the ``deleted_at IS NULL`` condition eagerly at construction time.
This is what makes ``count()``/``exists()``/``update()``/``delete()`` —
which snapshot ``_q_objects`` instead of going through
``QuerySet._execute`` — honor the default filter too.

Usage::

    from tortoise import fields
    from tortoise_extended.models.base import BaseModel
    from tortoise_extended.models.soft_delete import BaseSoftDeleteModel

    class Account(BaseSoftDeleteModel, BaseModel):
        name = fields.CharField(max_length=64)

        class Meta:
            table = "accounts"

    account = await Account.create(name="alice")

    await Account.all()                      # live rows only
    await Account.filter(name="alice")       # live rows only
    await Account.with_deleted()             # everything
    await Account.only_deleted()             # soft-deleted only

    await account.delete()                   # soft delete (sets deleted_at)
    await account.restore()                  # back to live
    await Account.only_deleted().restore()   # restore all deleted rows
    await Account.with_deleted().hard_delete()  # physical delete
"""

from datetime import UTC, datetime
from typing import Self, TypeVar, cast, override

from tortoise import fields
from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.expressions import Q
from tortoise.models import Model
from tortoise.queryset import QuerySet, QuerySetSingle

from tortoise_extended._types import RowValue

MODEL = TypeVar("MODEL", bound=Model)


class SoftDeleteQuerySet(QuerySet[MODEL]):
    """QuerySet that excludes soft-deleted rows unless opted out.

    The default condition is injected into ``_q_objects`` at construction
    time, so every operation that consumes the queryset (``filter``,
    ``count``, ``exists``, ``update``, ``delete``, ``get``) sees it.
    """

    def __init__(self, model: type[MODEL]) -> None:
        super().__init__(model)
        self._sd_index: int | None = None
        self._sd_mode: int = 0
        if "deleted_at" in model._meta.fields_map:
            self._q_objects.append(Q(deleted_at__isnull=True))
            self._sd_index = len(self._q_objects) - 1

    @override
    def _clone(self) -> SoftDeleteQuerySet[MODEL]:
        qs = cast(SoftDeleteQuerySet[MODEL], super()._clone())
        qs._sd_index = self._sd_index
        qs._sd_mode = self._sd_mode
        return qs

    def with_deleted(self) -> SoftDeleteQuerySet[MODEL]:
        """Return a clone that includes soft-deleted rows."""
        clone = self._clone()
        if clone._sd_index is not None:
            del clone._q_objects[clone._sd_index]
            clone._sd_index = None
        clone._sd_mode = 1
        return clone

    def only_deleted(self) -> SoftDeleteQuerySet[MODEL]:
        """Return a clone that matches only soft-deleted rows."""
        clone = self._clone()
        if clone._sd_index is not None:
            clone._q_objects[clone._sd_index] = Q(deleted_at__isnull=False)
        else:
            clone._q_objects.append(Q(deleted_at__isnull=False))
            clone._sd_index = len(clone._q_objects) - 1
        clone._sd_mode = 2
        return clone

    async def restore(self) -> int:
        """Restore every soft-deleted row matched by this queryset.

        Returns:
            Number of rows restored.
        """
        return await self.only_deleted().update(deleted_at=None)

    async def hard_delete(self) -> int:
        """Physically delete every row matched by this queryset.

        Includes soft-deleted rows — ``delete()`` on a default (live-only)
        queryset soft-deletes instead, so use ``hard_delete()`` (or
        ``with_deleted().delete()``) to actually purge rows from the table.

        Returns:
            Number of rows deleted.
        """
        return await self.with_deleted().delete()

    @override
    # The base QuerySet.delete() is a factory returning an unawaited
    # DeleteQuery; the override executes the delete eagerly and returns the
    # row count, which is what every ``await qs.delete()`` call site already
    # expects at runtime.  The signature widening is intentional.
    async def delete(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
    ) -> int:
        """Delete every row matched by this queryset.

        Semantics depend on the queryset mode:

        * default live-only queryset — **soft delete**: rows get
          ``deleted_at`` set (``UPDATE``), stay in the table, and remain
          visible via ``with_deleted()``;
        * ``with_deleted()`` / ``only_deleted()`` querysets — **physical
          delete** (``DELETE``), purging the matched rows.

        Returns:
            Number of rows affected.
        """
        if self._sd_mode == 0:
            return await self.update(deleted_at=datetime.now(UTC))
        return await super().delete()


class BaseSoftDeleteModel(Model):
    """Abstract base adding soft-delete behavior via ``deleted_at``.

    Attributes:
        deleted_at: ``NULL`` for live rows; set on soft delete.
    """

    deleted_at = fields.DatetimeField(
        null=True,
        default=None,
        use_tz=True,
        db_index=True,
        description="Soft-delete marker — NULL means the row is live",
    )

    class Meta:
        abstract = True

    @classmethod
    @override
    # QuerySet's MODEL TypeVar is invariant, so a narrower
    # SoftDeleteQuerySet[Self] return cannot be expressed in the base stub;
    # the widening is intentional (runtime override semantics unchanged).
    def _db_queryset(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls,
        using_db: BaseDBAsyncClient | None = None,
        for_write: bool = False,
    ) -> SoftDeleteQuerySet[Self]:
        db = using_db or cls._choose_db(for_write)
        return cast(
            SoftDeleteQuerySet[Self],
            SoftDeleteQuerySet(cls).using_db(db),
        )

    @classmethod
    @override
    def all(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls, using_db: BaseDBAsyncClient | None = None
    ) -> SoftDeleteQuerySet[Self]:
        """Return a queryset of live rows (excludes soft-deleted)."""
        return cls._db_queryset(using_db)

    @classmethod
    @override
    def filter(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls,
        *args: Q,
        **kwargs: RowValue | list[RowValue],
    ) -> SoftDeleteQuerySet[Self]:
        """Filter live rows (excludes soft-deleted)."""
        return cast(
            SoftDeleteQuerySet[Self],
            cls._db_queryset().filter(*args, **kwargs),
        )

    @classmethod
    @override
    def exclude(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls,
        *args: Q,
        **kwargs: RowValue | list[RowValue],
    ) -> SoftDeleteQuerySet[Self]:
        """Exclude matching live rows (excludes soft-deleted)."""
        return cast(
            SoftDeleteQuerySet[Self],
            cls._db_queryset().exclude(*args, **kwargs),
        )

    @classmethod
    @override
    def get(
        cls,
        *args: Q,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: RowValue | list[RowValue],
    ) -> QuerySetSingle[Self]:
        """Get a single live row."""
        return cls._db_queryset(using_db).get(*args, **kwargs)

    @classmethod
    @override
    def get_or_none(
        cls,
        *args: Q,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: RowValue | list[RowValue],
    ) -> QuerySetSingle[Self | None]:
        """Get a single live row or ``None``."""
        return cls._db_queryset(using_db).get_or_none(*args, **kwargs)

    @classmethod
    def with_deleted(
        cls, using_db: BaseDBAsyncClient | None = None
    ) -> SoftDeleteQuerySet[Self]:
        """Return a queryset that includes soft-deleted rows."""
        return cls._db_queryset(using_db).with_deleted()

    @classmethod
    def only_deleted(
        cls, using_db: BaseDBAsyncClient | None = None
    ) -> SoftDeleteQuerySet[Self]:
        """Return a queryset that matches only soft-deleted rows."""
        return cls._db_queryset(using_db).only_deleted()

    @override
    async def delete(self, using_db: BaseDBAsyncClient | None = None) -> None:
        """Soft-delete this row by setting ``deleted_at``.

        Uses ``save(update_fields=...)`` so ``pre_save``/``post_save``
        signals still fire; the row keeps its PK and can be restored with
        :meth:`restore`.
        """
        if self.deleted_at is not None:
            return
        self.deleted_at = datetime.now(UTC)
        await self.save(update_fields=["deleted_at"], using_db=using_db)

    async def restore(self) -> None:
        """Restore this row by clearing ``deleted_at``."""
        if self.deleted_at is None:
            return
        self.deleted_at = None
        await self.save(update_fields=["deleted_at"])
