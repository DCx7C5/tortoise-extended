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
