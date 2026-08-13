"""Abstract base model with a ``BigInt`` primary key.

Tortoise auto-creates ``id = IntField(primary_key=True)`` when a model
declares no primary key; this base exists for models that want a 64-bit
primary key instead (JOIN-fast ints, large tables).  It is deliberately
minimal — combine it with :class:`~tortoise_extended.models.mixins.TimestampMixin`
when timestamps are needed too.

Usage::

    from tortoise import fields
    from tortoise_extended.models.base import BaseModel

    class Account(BaseModel):
        name = fields.CharField(max_length=64)

        class Meta:
            table = "accounts"
"""

from tortoise import fields
from tortoise.models import Model


class BaseModel(Model):
    """Abstract base with a ``BigIntField`` primary key.

    Attributes:
        id: 64-bit auto-increment primary key.
    """

    id = fields.BigIntField(
        primary_key=True,
        description="64-bit auto-increment primary key",
    )

    class Meta:
        abstract = True
