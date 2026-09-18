"""PostgreSQL-optimized character field using ``TEXT`` instead of ``VARCHAR``.

In PostgreSQL, ``TEXT`` and ``VARCHAR(n)`` have identical storage and
performance. The only difference is that ``VARCHAR(n)`` enforces a max
length at the database level — a constraint that PostgreSQL 18 now warns
about as a non-best-practice.

This ``CharField`` replaces ``VARCHAR(N)`` with ``TEXT`` on all backends
while keeping Tortoise's ``MaxLengthValidator`` for Python-side safety.
Application-layer validation remains the primary guard; the DB-level
``VARCHAR(n)`` check is redundant when the ORM already validates.

Usage::

    from tortoise import models
    from tortoise_extended.fields import CharField

    class Article(models.Model):
        title = CharField(max_length=200)
        slug = CharField(max_length=100, unique=True)

        class Meta:
            table = "articles"

.. note::
    ``max_length`` is still required — it feeds the ``MaxLengthValidator``
    that runs on every write. The value is **not** emitted as a SQL type
    constraint (the column is ``TEXT``, not ``VARCHAR(N)``).
"""

from collections.abc import Callable
from typing import Literal, TypeVar, Unpack, override, overload

from tortoise.fields import Field
from tortoise.validators import MaxLengthValidator, Validator

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs

T_STR = TypeVar("T_STR", str, str | None, default=str)


class CharField(Field[T_STR]):
    """Character field backed by PostgreSQL ``TEXT`` instead of ``VARCHAR(N)``.

    Drop-in replacement for ``tortoise.fields.CharField``. Accepts the same
    ``max_length`` parameter (used for Python-side validation via
    ``MaxLengthValidator``) but stores the column as ``TEXT`` on every
    backend. This eliminates the PostgreSQL 18 ``character varying`` warning
    and removes future migration risk when length limits need to change.

    Supports ``unique=True``, ``db_index=True``, and ``primary_key=True``
    (unlike ``TextField``, which blocks all three).

    :param max_length: Maximum character count (enforced in Python, not SQL).
    :param null: Allow NULL values.
    :param default: Default value or callable.
    :param description: Column comment.
    :param unique: Create a unique constraint.
    :param db_index: Create a B-tree index.

    Usage::

        class Article(Model):
            title = CharField(max_length=200)
            slug = CharField(max_length=100, unique=True)
    """

    field_type = str

    @overload
    def __init__(
        self: CharField[str],
        max_length: int,
        *,
        null: Literal[False] = False,
        default: FieldDefaultValue = None,
        description: str | None = None,
        validators: list[Validator | Callable[..., None]] | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None: ...

    @overload
    def __init__(
        self: CharField[str | None],
        max_length: int,
        *,
        null: Literal[True],
        default: FieldDefaultValue = None,
        description: str | None = None,
        validators: list[Validator | Callable[..., None]] | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None: ...

    def __init__(
        self,
        max_length: int,
        *,
        null: bool = False,
        default: FieldDefaultValue = None,
        description: str | None = None,
        validators: list[Validator | Callable[..., None]] | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None:
        if int(max_length) < 1:
            raise ValueError("'max_length' must be >= 1")
        self.max_length = int(max_length)
        # Caller validators are merged AFTER the built-in MaxLengthValidator.
        super().__init__(
            null=null,
            default=default,
            description=description,
            validators=[MaxLengthValidator(self.max_length), *list(validators or [])],
            **kwargs,
        )

    @property
    @override
    def constraints(self) -> dict[str, int]:
        return {"max_length": self.max_length}

    SQL_TYPE = "TEXT"  # class attribute, matching VectorField and upstream convention
