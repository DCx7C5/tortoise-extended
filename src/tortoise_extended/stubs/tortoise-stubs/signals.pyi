"""Clean type stubs for ``tortoise.signals`` (local overlay).

The inline package types the decorators with an unbound module-level
``TypeVar`` (``FuncType = Callable[[T], T]``), which basedpyright resolves
to ``(*senders: Unknown) -> ((Unknown) -> Unknown)``. This overlay types
the decorators precisely: ``senders`` are opaque, and the returned
decorator preserves the decorated callable's type.
"""

from collections.abc import Callable
from enum import Enum
from typing import TypeVar

_T = TypeVar("_T")


class Signals(Enum):
    """Signal event types (mirrors the runtime ``Enum`` declaration)."""

    pre_save = "pre_save"
    post_save = "post_save"
    pre_delete = "pre_delete"
    post_delete = "post_delete"


def post_save(*senders: object) -> Callable[[_T], _T]: ...


def pre_save(*senders: object) -> Callable[[_T], _T]: ...


def pre_delete(*senders: object) -> Callable[[_T], _T]: ...


def post_delete(*senders: object) -> Callable[[_T], _T]: ...
