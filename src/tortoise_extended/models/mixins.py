"""Reusable model mixins (timestamping).

Tortoise documents ``auto_now_add``/``auto_now`` but ships no ready-made
mixin — this is the canonical one, timezone-aware.  Stackable with any
abstract base (:class:`~tortoise_extended.models.base.BaseModel` or plain
``tortoise.models.Model``).

Usage::

    from tortoise import fields
    from tortoise_extended.models.base import BaseModel
    from tortoise_extended.models.mixins import TimestampMixin

    class Account(TimestampMixin, BaseModel):
        name = fields.CharField(max_length=64)

        class Meta:
            table = "accounts"
"""

from tortoise import fields


class TimestampMixin:
    """Add ``created_at``/``updated_at`` timestamp columns to a model.

    Attributes:
        created_at: Set automatically on first insert.
        updated_at: Set automatically on every save/update.
    """

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


class TimestampEndMixin:
    """Add ``created_at``/``ended_at`` timestamp columns to a model.

    ``ended_at`` is a **caller-managed** nullable timestamp (e.g. the moment
    a process/workflow finished) — it is deliberately *not* ``auto_now``,
    because ``auto_now`` would silently rewrite the end time on every later
    save, turning it into a misnamed ``updated_at``.  Set it explicitly when
    the entity ends::

        entity.ended_at = datetime.now(UTC)
        await entity.save(update_fields=["ended_at"])

    Attributes:
        created_at: Set automatically on first insert.
        ended_at: ``NULL`` until the caller marks the entity ended.
    """

    created_at = fields.DatetimeField(
        auto_now_add=True,
        use_tz=True,
        description="Creation timestamp (timezone-aware)",
    )
    ended_at = fields.DatetimeField(
        null=True,
        default=None,
        use_tz=True,
        description="Caller-set end timestamp (timezone-aware); NULL while active",
    )

