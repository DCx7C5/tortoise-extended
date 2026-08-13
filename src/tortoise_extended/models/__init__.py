"""Reusable Tortoise ORM model primitives.

Ships the base-model family from the project roadmap.  Every Tortoise
``Model`` subclass in this package follows the ``Base*Model`` naming
convention and lives under ``tortoise_extended.models``:

* :class:`~tortoise_extended.models.base.BaseModel` — ``BigIntField`` pk.
* :class:`~tortoise_extended.models.user.BaseUserModel` — Django-style
  email/password auth (argon2id hashing via argon2-cffi).
* :class:`~tortoise_extended.models.soft_delete.BaseSoftDeleteModel` +
  :class:`~tortoise_extended.models.soft_delete.SoftDeleteQuerySet` — soft
  delete with an auto-filtering queryset.
* :class:`~tortoise_extended.models.graph_node.BaseGraphNodeModel` —
  adjacency-list graph nodes (UUID pk).
* :class:`~tortoise_extended.models.graph_edge.BaseGraphEdgeModel` — typed
  relationships between graph nodes (UUID pks).
* :class:`~tortoise_extended.models.hierarchy_model.BaseHierarchyModel` —
  ltree-path hierarchy models (BigInt pk).
* :class:`~tortoise_extended.models.cacheable_model.BaseCacheableModel` —
  model-level Redis caching.
* :class:`~tortoise_extended.models.event_stream.BaseEventStreamModel` —
  TimescaleDB multi-stream hypertable model.
* :class:`~tortoise_extended.models.mixins.TimestampMixin` — timestamps.

All are opt-in abstract bases — nothing is forced on any model.
"""

from .base import BaseModel
from .cacheable_model import BaseCacheableModel
from .event_stream import BaseEventStreamModel
from .graph_edge import BaseGraphEdgeModel
from .graph_node import BaseGraphNodeModel
from .hierarchy_model import BaseHierarchyModel
from .mixins import TimestampEndMixin, TimestampMixin
from .soft_delete import BaseSoftDeleteModel, SoftDeleteQuerySet
from .user import BaseUserModel

__all__ = [
    "BaseCacheableModel",
    "BaseEventStreamModel",
    "BaseGraphEdgeModel",
    "BaseGraphNodeModel",
    "BaseHierarchyModel",
    "BaseModel",
    "BaseSoftDeleteModel",
    "BaseUserModel",
    "SoftDeleteQuerySet",
    "TimestampEndMixin",
    "TimestampMixin",
]
