"""Reusable Tortoise ORM model primitives.

Ships the Tier-1 base-model family from the project roadmap:

* :class:`~tortoise_extended.models.base.BaseModel` — ``BigIntField`` pk.
* :class:`~tortoise_extended.models.mixins.TimestampMixin` — timestamps.
* :class:`~tortoise_extended.models.soft_delete.SoftDeleteModel` +
  :class:`~tortoise_extended.models.soft_delete.SoftDeleteQuerySet` — soft
  delete with an auto-filtering queryset.

All are opt-in abstract bases — nothing is forced on any model.
"""

from tortoise_extended.models.base import BaseModel
from tortoise_extended.models.mixins import TimestampMixin
from tortoise_extended.models.soft_delete import SoftDeleteModel, SoftDeleteQuerySet

__all__ = [
    "BaseModel",
    "SoftDeleteModel",
    "SoftDeleteQuerySet",
    "TimestampMixin",
]
