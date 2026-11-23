"""Optimistic locking for opt-in tortoise-orm models.

Tortoise ships no optimistic locking: two instances that load the same row can
both ``save()`` and the second write silently wins. This mixin makes that race
visible — every guarded ``save()`` carries the version it loaded, and a stale
write raises :class:`~tortoise_extended.exceptions.VersionMismatchError`
instead of overwriting the other writer.

Usage::

    from tortoise import fields
    from tortoise_extended import BaseModel, VersionMixin

    class Document(BaseModel, VersionMixin):
        title = fields.CharField(max_length=200)
        body = fields.TextField()

        class Meta:
            table = "documents"

    doc = await Document.get(pk=1)      # version 0
    doc.title = "Draft"
    await doc.save()                    # guarded: WHERE version = 0, version -> 1

    stale = await Document.get(pk=1)
    await doc.save()                    # doc still holds version 0 -> mismatch
    # VersionMismatchError: Document pk=1 was loaded at version 0 but the row
    # is at version 1: reload the row and reapply the change

Guarded ``save()`` is one ``UPDATE … WHERE <pk> = $1 AND version = $2`` through
the queryset API (no raw SQL, AGENTS.md rule 3): the rowcount is the answer —
``1`` committed, ``0`` means the version moved. ``version`` is incremented in
the database with ``F("version") + 1``, so two writers cannot both read 0 and
both write 1.

A refused save leaves the instance untouched: the ``UPDATE`` writes nothing,
and the ``auto_now`` values ``_pre_save`` may have stamped are put back before
:class:`~tortoise_extended.exceptions.VersionMismatchError` is raised. An
instance that was never loaded from the database carries no version to check,
so a projected save of one is refused with
:class:`tortoise.exceptions.IncompleteInstanceError` instead of taking the
unguarded ``UPDATE`` upstream ``Model.save()`` would run — a deliberate
divergence.

**Not covered — by design.** ``bulk_create``, ``bulk_update`` and
``QuerySet.update()`` bypass model ``save()`` entirely: they write without the
guard and without bumping ``version``. That is deliberate (they are the bulk
escape hatch, and refusing them would break ordinary queryset use on a model
that happens to inherit this mixin). Anything that must be race-safe uses
``instance.save()``; anything on the bulk paths is last-write-wins, and this
docstring says so instead of pretending otherwise.
"""

from collections.abc import Iterable
from typing import cast, override

from tortoise import fields
from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.exceptions import (
    IncompleteInstanceError,
    IntegrityError,
    OperationalError,
)
from tortoise.expressions import F
from tortoise.fields.base import DatabaseDefault
from tortoise.models import Model
from tortoise.queryset import QuerySet
from tortoise.timezone import localtime

from tortoise_extended.exceptions import VersionMismatchError


class VersionMixin(Model):
    """Add an optimistic-lock ``version`` column and a guarded ``save()``.

    Abstract: stack it on an identity base (``BaseModel``, ``BaseNode``, …) and
    declare ``Meta.table`` in the concrete model.

    Attributes:
        version: Row version, starting at ``0`` and incremented by every
            guarded save. Never set it by hand; a stale value is what raises
            :class:`~tortoise_extended.exceptions.VersionMismatchError`.

    The instance is only as current as the last committed save: ``version``
    and ``_saved_in_db`` are updated in memory when the guarded ``UPDATE``
    succeeds, inside whatever transaction ``using_db`` belongs to. After a
    rollback (or an external writer) the instance advertises a version the row
    does not have, and the next ``save()`` raises a spurious
    :class:`~tortoise_extended.exceptions.VersionMismatchError`. Call
    ``refresh_from_db()`` before saving again.
    """

    version = fields.BigIntField(
        default=0,
        description="Optimistic-lock version; bumped by every guarded save",
    )

    class Meta:
        abstract = True

    @override
    async def save(
        self,
        using_db: BaseDBAsyncClient | None = None,
        update_fields: Iterable[str] | None = None,
        force_create: bool = False,
        force_update: bool = False,
    ) -> None:
        """Insert or update the instance, guarding updates on ``version``.

        Inserts (``force_create``, an instance with no primary key yet, or an
        unsaved instance saved without ``update_fields``) delegate to the next
        ``save`` in the MRO unchanged, so defaults, generated keys,
        ``auto_now_add`` and other mixins' hooks behave exactly as upstream.
        ``update_fields=[]`` is normalised to ``None`` — a full save — matching
        upstream's ``if self._saved_in_db or update_fields``.

        Updates run as ``UPDATE <table> SET …, version = version + 1 WHERE
        <pk> = $1 AND version = $2``. ``auto_now`` fields are written
        explicitly, because the queryset executor converts values without an
        instance and would otherwise skip them — the same reason ``auto_now``
        never fires through ``QuerySet.update()``. A refused save puts those
        values back, so the instance is left exactly as it was.

        This **deliberately diverges from upstream ``Model.save()``**: an
        instance that carries a primary key but was never loaded from the
        database (``_saved_in_db`` is ``False``) has no version to check, where
        upstream would take an unguarded ``UPDATE`` in that shape. A projected
        save of such an instance raises ``IncompleteInstanceError`` instead.
        Reload the row, or pass ``force_update=True`` if you really mean to
        write it unguarded.

        :param using_db: Connection to use instead of the model's default.
        :param update_fields: Subset of field names to write; an empty iterable
            means "all fields". ``version`` is always written. Naming an
            unknown field, a field without a database column (M2M / backward
            relation), the primary key or a generated column raises
            :class:`tortoise.exceptions.OperationalError`.
        :param force_create: Insert even when the instance looks persisted.
        :param force_update: Update without checking ``_saved_in_db``, which
            also skips the "must have been loaded" rule above.
        :raises VersionMismatchError: When the row's version has moved; the
            instance is left untouched so the caller can reload and retry.
        :raises IncompleteInstanceError: For a partial instance saved without
            ``update_fields``, when the in-memory version is unavailable, or
            for a never-loaded instance saved with ``update_fields``.
        :raises IntegrityError: When a forced update matches no row at all, or
            when the primary key matches more than one.
        """
        names = list(update_fields) if update_fields is not None else None
        if not names:
            names = None

        if force_create or (
            not force_update
            and (self.pk is None or (not self._saved_in_db and names is None))
        ):
            await super().save(
                using_db=using_db,
                update_fields=names,
                force_create=force_create,
                force_update=force_update,
            )
            return

        if not (force_update or self._saved_in_db):
            raise IncompleteInstanceError(
                f"{type(self).__name__} was not loaded from the database, so a "
                f"guarded save has no version to check (_saved_in_db is "
                f"False): reload the row, or pass force_update=True if you "
                f"really mean to write it unguarded"
            )

        self._validate_update_fields(names)
        self._check_partial(names)
        await self._set_async_default_field()
        if not hasattr(self, "version"):
            raise IncompleteInstanceError(
                f"{type(self).__name__} has no in-memory version: a guarded save "
                f"needs the version the instance was loaded with, so reload the "
                f"row"
            )
        expected = self.version
        if expected is None:
            raise IncompleteInstanceError(
                f"{type(self).__name__} holds no version: a guarded save needs "
                f"the version the instance was loaded with"
            )

        db = using_db or self._choose_db(True)
        auto_now_before = self._snapshot_auto_now()
        await self._pre_save(db, names)

        payload = self._update_payload(names)
        payload["version"] = F("version") + 1

        rows = await (
            QuerySet(type(self))
            .using_db(db)
            .filter(**{self._meta.pk_attr: self.pk}, version=expected)
            .update(**payload)
        )
        if rows == 0:
            # A refused save must not keep the auto_now stamps _pre_save (or a
            # listener it ran) applied to the instance.
            self._restore_auto_now(auto_now_before)
            # Error path only: one extra read, so the message can name the
            # version now in the row (and tell a deleted row from a stale one).
            row = (
                await QuerySet(type(self))
                .using_db(db)
                .filter(**{self._meta.pk_attr: self.pk})
                .first()
            )
            if row is None:
                raise IntegrityError(
                    f"Can't update object that doesn't exist. PK: {self.pk}"
                )
            raise VersionMismatchError(
                model=type(self).__name__,
                pk=self.pk,
                expected=expected,
                current=cast("VersionMixin", row).version,
            )
        if rows > 1:
            raise IntegrityError(
                f"{type(self).__name__} pk={self.pk!r} matched {rows} rows: the "
                f"primary key is not unique, so the version guard is meaningless"
            )

        self.version = expected + 1
        for name, value in payload.items():
            if name == "version":
                continue
            field_obj = self._meta.fields_map[name]
            if getattr(field_obj, "auto_now", False):
                # The payload carried an aware instant; the field decides what
                # the instance should hold (naive under ``use_tz=False``).
                setattr(self, name, field_obj.to_python_value(value))
        self._saved_in_db = True
        await self._post_save(db, False, names)

    def _validate_update_fields(self, names: list[str] | None) -> None:
        """Reject ``update_fields`` names that cannot form a guarded ``UPDATE``.

        Upstream indexes ``fields_map`` directly (a bare ``KeyError`` for an
        unknown name) and would hand a manager object to the payload for an M2M
        or backward relation. Both are refused here, before ``_pre_save``
        listeners run, so a bad projection never fires a hook or reaches a
        query.

        :param names: The resolved ``update_fields`` names, or ``None`` for a
            full save (nothing to validate).
        :raises OperationalError: When a name is unknown, names a field without
            a database column, the primary key, or a generated column.
        """
        if names is None:
            return
        valid = ", ".join(sorted(self._meta.fields_db_projection))
        for name in names:
            field_obj = self._meta.fields_map.get(name)
            if field_obj is None:
                raise OperationalError(
                    f"{type(self).__name__} has no field {name!r}: update_fields "
                    f"must name database columns, expected one of {valid}"
                )
            if not field_obj.has_db_field:
                raise OperationalError(
                    f"Can't update {name!r} on {type(self).__name__}: it is a "
                    f"related field with no database column of its own, "
                    f"update_fields must name one of {valid}"
                )
            if field_obj.pk:
                raise OperationalError(
                    f"Can't update pk field, use `{type(self).__name__}.create` "
                    f"instead."
                )
            if field_obj.generated:
                raise OperationalError(f"Can't update generated field {name}")

    def _check_partial(self, names: list[str] | None) -> None:
        """Apply upstream's partial-instance rules before a guarded update.

        :param names: The resolved ``update_fields`` names, or ``None``.
        :raises IncompleteInstanceError: When the instance is partial and cannot
            be saved with the fields that are loaded.
        """
        if not self._partial:
            return
        if names is None:
            raise IncompleteInstanceError(
                f"{type(self).__name__} is a partial model, can only be saved "
                f"with the relevant update_field provided"
            )
        for name in names:
            if not hasattr(self, self._meta.pk_attr):
                raise IncompleteInstanceError(
                    f"{type(self).__name__} is a partial model without primary "
                    f"key fetchd. Partial update not available"
                )
            if not hasattr(self, name):
                raise IncompleteInstanceError(
                    f"{type(self).__name__} is a partial model, field {name!r} is "
                    f"not available"
                )

    def _snapshot_auto_now(self) -> dict[str, object]:
        """Record the ``auto_now`` values a refused save must put back.

        ``_pre_save`` runs before the guarded ``UPDATE`` and can stamp
        ``auto_now`` fields on the instance; a save the guard refuses must
        leave the instance exactly as it was, so the values are captured here
        and restored when no row matches.

        :returns: Field name → current value, for every ``auto_now`` field the
            instance carries (a partial instance may not carry them all).
        """
        values: dict[str, object] = {}
        for name, field_obj in self._meta.fields_map.items():
            if getattr(field_obj, "auto_now", False) and hasattr(self, name):
                values[name] = getattr(self, name)
        return values

    def _restore_auto_now(self, values: dict[str, object]) -> None:
        """Undo the ``auto_now`` stamps of a save that matched no row.

        :param values: The snapshot returned by :meth:`_snapshot_auto_now`.
        """
        for name, value in values.items():
            setattr(self, name, value)

    def _update_payload(self, names: list[str] | None) -> dict[str, object]:
        """Collect the values a guarded ``UPDATE`` should write.

        Mirrors the executor's field selection: without ``update_fields`` every
        projected field is written; primary keys and generated columns are
        skipped (naming either explicitly is refused by
        :meth:`_validate_update_fields`); values still holding a
        :class:`tortoise.fields.base.DatabaseDefault` sentinel are left to the
        database.

        ``auto_now`` columns are replaced with the current time because the
        queryset executor converts values without an instance, so the field
        never sees the save and would keep the old timestamp. The stamp is an
        **aware** instant: tortoise's ``to_db_value`` only adds a timezone when
        ``use_tz`` is on and the value is naive, it never strips one, so an
        aware value reaches asyncpg unchanged and stores the right instant in
        either mode. The in-memory mirror inherits the field conversion
        instead, which is what turns it naive under ``use_tz=False``.

        :param names: The resolved ``update_fields`` names, or ``None``.
        :returns: Field name → Python value, ready for ``QuerySet.update()``.
        """
        source = names if names is not None else list(self._meta.fields_db_projection)
        payload: dict[str, object] = {}
        for name in source:
            field_obj = self._meta.fields_map[name]
            if field_obj.pk or field_obj.generated:
                continue
            value = getattr(self, name)
            if isinstance(value, DatabaseDefault):
                continue
            if getattr(field_obj, "auto_now", False):
                value = localtime()
            payload[name] = value
        return payload


__all__ = ["VersionMixin"]
