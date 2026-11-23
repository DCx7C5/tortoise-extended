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

        Inserts (``force_create``, an unsaved instance, or no primary key yet)
        delegate to the next ``save`` in the MRO unchanged, so defaults,
        generated keys, ``auto_now_add`` and other mixins' hooks behave exactly
        as upstream.

        Updates run as ``UPDATE <table> SET …, version = version + 1 WHERE
        <pk> = $1 AND version = $2``. ``auto_now`` fields are written
        explicitly, because the queryset executor converts values without an
        instance and would otherwise skip them — the same reason ``auto_now``
        never fires through ``QuerySet.update()``.

        :param using_db: Connection to use instead of the model's default.
        :param update_fields: Subset of field names to write. ``version`` is
            always written; naming the primary key or a generated column raises
            :class:`tortoise.exceptions.OperationalError`, as upstream does.
        :param force_create: Insert even when the instance looks persisted.
        :param force_update: Update without checking ``_saved_in_db``.
        :raises VersionMismatchError: When the row's version has moved; the
            instance is left untouched so the caller can reload and retry.
        :raises IncompleteInstanceError: For a partial instance saved without
            ``update_fields``, or when the in-memory version is unavailable.
        :raises IntegrityError: When a forced update matches no row at all.
        """
        if (
            force_create
            or (not force_update and self.pk is None)
            or (not force_update and not self._saved_in_db and not update_fields)
        ):
            await super().save(
                using_db=using_db,
                update_fields=update_fields,
                force_create=force_create,
                force_update=force_update,
            )
            return

        await self._set_async_default_field()
        names = list(update_fields) if update_fields is not None else None
        self._check_partial(names)
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
        await self._pre_save(db, update_fields)

        payload = self._update_payload(names)
        payload["version"] = F("version") + 1

        rows = await (
            QuerySet(type(self))
            .using_db(db)
            .filter(**{self._meta.pk_attr: self.pk}, version=expected)
            .update(**payload)
        )
        if rows == 0:
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
                setattr(self, name, field_obj.to_python_value(value))
        self._saved_in_db = True
        await self._post_save(db, False, update_fields)

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

    def _update_payload(self, names: list[str] | None) -> dict[str, object]:
        """Collect the values a guarded ``UPDATE`` should write.

        Mirrors the executor's field selection: without ``update_fields`` every
        projected field is written; primary keys and generated columns are
        skipped (naming either explicitly raises, as upstream does); values
        still holding a :class:`tortoise.fields.base.DatabaseDefault` sentinel
        are left to the database.

        ``auto_now`` columns are replaced with the current time because the
        queryset executor converts values without an instance, so the field
        never sees the save and would keep the old timestamp.

        :param names: The resolved ``update_fields`` names, or ``None``.
        :returns: Field name → Python value, ready for ``QuerySet.update()``.
        :raises OperationalError: When ``update_fields`` names the primary key
            or a generated column.
        """
        user_specified = names is not None
        source = names if names is not None else list(self._meta.fields_db_projection)
        payload: dict[str, object] = {}
        for name in source:
            field_obj = self._meta.fields_map[name]
            if field_obj.pk:
                if user_specified:
                    raise OperationalError(
                        f"Can't update pk field, use `{type(self).__name__}.create` "
                        f"instead."
                    )
                continue
            if field_obj.generated:
                if user_specified:
                    raise OperationalError(f"Can't update generated field {name}")
                continue
            value = getattr(self, name)
            if isinstance(value, DatabaseDefault):
                continue
            if getattr(field_obj, "auto_now", False):
                value = localtime()
            payload[name] = value
        return payload


__all__ = ["VersionMixin"]
