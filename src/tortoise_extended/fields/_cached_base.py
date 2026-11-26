"""Cache-backed field base that mirrors a wrapped Tortoise column.

``_CachedFieldBase`` wraps another :class:`tortoise.fields.base.Field` and
reproduces its database column contract — source column, nullability, default,
uniqueness, index, constraints and dialect SQL type — while carrying the cache
metadata (TTL, namespace, serializer, backend) a concrete cache field needs.
The wrapper is a :class:`~tortoise.fields.base.Field` itself, so it can be
declared on a model in place of the inner field, and schema generation /
migrations see the inner field's column because
:meth:`_CachedFieldBase.get_for_dialect` and
:meth:`_CachedFieldBase.deconstruct` delegate to the wrapped instance.

Construction refuses — with :class:`ValueError`, before any schema exists — an
inner value that is not a field instance (a field *class* is not an instance),
a relational field, a DB-generated field, a primary key, an ``auto_now`` /
``auto_now_add`` field, and any field the JSON codec cannot round-trip
(:meth:`tortoise_extended.fields._cache_codec.CacheCodec.supports`).

Cache keys are unprefixed and app-qualified::

    CacheKey().add(instance._meta.app or "", type(instance).__name__, str(pk), field_name)

The namespace lives on the backend, never in the key, so a wrapper's keys stay
readable without a ``CacheKey(prefix=...)`` round-trip.

Usage::

    from tortoise.fields import DatetimeField

    from tortoise_extended.fields._cached_base import _CachedFieldBase

    class Session(Model):
        #: Last time the session was touched
        touched_at = _CachedFieldBase(DatetimeField(), ttl=60)

    field = Session._meta.fields_map["touched_at"]
    assert field.get_for_dialect("postgres", "SQL_TYPE") == "TIMESTAMPTZ"
"""

from collections.abc import Callable, Mapping
from typing import Literal, cast, override

from pypika_tortoise.terms import Term
from tortoise.fields.base import Field
from tortoise.fields.relational import RelationalField
from tortoise.models import Model

from tortoise_extended._types import CacheScalar
from tortoise_extended.cache.base import CacheBackend, CacheKey, Serializer
from tortoise_extended.fields._cache_codec import CacheCodec

__all__ = ["_CachedFieldBase"]
"""The cache-field base; concrete cache fields (``CachedField``) build on it."""

#: Fallback TTL, in seconds, used when the wrapper does not set one. ``0`` is a
#: real TTL ("no expiry" in :class:`CacheBackend`), so only ``None`` falls back.
_DEFAULT_TTL: int = 300


def _require_field_instance[F](value: F) -> F:
    """Return *value* unchanged when it is a field instance.

    The check is deliberately runtime-only: the public constructor annotates
    ``inner`` as :class:`Field`, so a direct ``isinstance`` there would be
    flagged as unnecessary, yet callers can still pass a field class or an
    arbitrary object at runtime.

    :param value: The candidate inner field.
    :returns: *value*, narrowed to a field instance.
    :raises ValueError: If *value* is not a :class:`Field` instance.
    """
    if not isinstance(value, Field):
        msg = f"inner must be a Field instance, not {type(value).__name__}"
        raise ValueError(msg)
    return cast(F, value)


class _CachedFieldBase[V](Field[V]):
    """Base for cache fields: an inner Tortoise field plus cache metadata.

    Subclasses add the read/write behaviour (``cache_aside`` or
    ``write_through`` per :attr:`_write_policy`); this class owns the column
    contract, the cache key and the backend resolution only.

    :param inner: The wrapped field instance. Its column contract is copied
        verbatim; it must be a non-relational, non-generated, non-primary-key
        field without ``auto_now`` / ``auto_now_add`` that the cache codec
        supports.
    :param ttl: Cache entry lifetime in seconds. ``None`` defers to the backend
        default (300 seconds when the default backend is resolved); ``0`` is a
        real TTL and is passed through.
    :param namespace: Backend namespace the cache keys are written under.
    :param serializer: Serializer override for the resolved backend.
    :param backend: Explicit backend. When omitted, a namespace-scoped
        :class:`~tortoise_extended.cache.redis.RedisCacheBackend` is resolved
        lazily from the class-level Redis pool on every access.
    :raises ValueError: If *inner* violates any construction-time refusal.

    .. note::
        ``field_type`` is a per-instance attribute rather than a property:
        tortoise's ``_FieldMeta`` overwrites any class-body ``field_type`` on a
        PEP 695 generic subclass (it reads the implicit ``Generic`` base and
        writes ``field_type = Generic``), so only an instance attribute
        survives class creation.
    """

    _write_policy: Literal["cache_aside", "write_through"] = "cache_aside"
    """Write strategy used by concrete cache fields (declared on the base only)."""

    def __init__(
        self,
        inner: Field[V],
        *,
        ttl: int | None = None,
        namespace: str = "cachefield",
        serializer: Serializer | None = None,
        backend: CacheBackend | None = None,
    ) -> None:
        inner = _require_field_instance(inner)
        inner_name = type(inner).__name__
        if isinstance(inner, RelationalField):
            msg = (
                f"{inner_name} is a relational field; cache fields wrap a scalar column"
            )
            raise ValueError(msg)
        if inner.generated:
            msg = f"{inner_name} is DB-generated; a cached column cannot be written"
            raise ValueError(msg)
        if inner.pk:
            msg = f"{inner_name} is a primary key; cache fields are non-key columns"
            raise ValueError(msg)
        if getattr(inner, "auto_now", False) or getattr(inner, "auto_now_add", False):
            msg = (
                f"{inner_name} is auto-managed (auto_now/auto_now_add); "
                "its writes would bypass the cache"
            )
            raise ValueError(msg)
        if not CacheCodec().supports(inner):
            msg = f"{inner_name} is not supported by the cache codec"
            raise ValueError(msg)
        # ``indexable`` is read by ``Field.__init__``, so the inner field must
        # be in place before ``super().__init__`` runs.
        self.inner = inner
        # ``field_type`` is an instance attribute, not a property: tortoise's
        # ``_FieldMeta`` overwrites every class-body ``field_type`` on a PEP 695
        # generic subclass (it sees the implicit ``Generic`` base and writes
        # ``field_type = Generic``), so only an instance attribute survives.
        self.field_type = inner.field_type
        self.ttl = ttl
        self.namespace = namespace
        self.serializer = serializer
        self._backend = backend
        super().__init__(
            source_field=inner.source_field,
            generated=False,
            primary_key=False,
            null=inner.null,
            default=inner.default,
            db_default=inner.db_default,
            unique=inner.unique,
            db_index=self.inner.index,
            description=inner.description,
            validators=list(self.inner.validators),
        )

    @property
    @override
    def SQL_TYPE(self) -> str:
        """The inner field's default (dialect-independent) SQL column type."""
        return self.inner.SQL_TYPE

    @property
    @override
    def indexable(self) -> bool:
        """Mirror the inner field's indexability (read by ``Field.__init__``)."""
        return self.inner.indexable

    @property
    @override
    def constraints(self) -> Mapping[str, object]:
        """The inner field's DB-level column constraints."""
        return self.inner.constraints

    @property
    def backend(self) -> CacheBackend:
        """Return the cache backend, resolving the default lazily on every call.

        An explicitly supplied backend wins. Otherwise a namespace-scoped
        backend is built from the class-level Redis pool on each access, so a
        pool re-initialisation between two calls is observed rather than
        hidden behind a cached instance.

        :returns: The cache backend for this field.
        :raises tortoise_extended.exceptions.CacheBackendNotInitializedError:
            If no backend was supplied and ``RedisCache.init`` has not run.
        """
        if self._backend is not None:
            return self._backend
        from tortoise_extended.cache.redis import RedisCache

        return RedisCache.get_backend(
            namespace=self.namespace,
            default_ttl=self.ttl if self.ttl is not None else _DEFAULT_TTL,
            serializer=self.serializer,
        )

    def cached_key(self, instance: Model) -> str:
        """Build the cache key for *instance*'s value of this field.

        The key is unprefixed and app-qualified; the backend's namespace is the
        only prefix, so a raw ``CacheKey`` reproduces the key exactly.

        :param instance: A saved model instance.
        :returns: The cache key string.
        :raises ValueError: If *instance* has no primary key value.
        """
        pk = instance.pk
        if pk is None:
            msg = (
                f"{type(instance).__name__} has no primary key value; "
                "the cache key requires a saved instance"
            )
            raise ValueError(msg)
        return (
            CacheKey()
            .add(
                instance._meta.app or "",
                type(instance).__name__,
                str(pk),
                self.model_field_name,
            )
            .build()
        )

    @override
    def describe(self, serializable: bool) -> dict[str, object]:
        """Describe the field, presenting the inner field's column contract.

        The inner description is copied wholesale — field type, python type,
        nullability, default, constraints and per-dialect ``db_field_types`` —
        and only the identity keys are overwritten with the wrapper's own.
        ``description`` / ``docstring`` are filled from the wrapper when the
        inner field has none: the model metaclass attaches a ``#:`` comment to
        the wrapper attribute, not to the inner instance.

        :param serializable: ``True`` for JSON-safe values, ``False`` for raw
            Python objects (see :meth:`tortoise.fields.base.Field.describe`).
        :returns: The field description dictionary.
        """
        desc = self.inner.describe(serializable)
        desc["name"] = self.model_field_name
        desc["db_column"] = self.source_field or self.model_field_name
        if desc.get("description") is None and self.description is not None:
            desc["description"] = self.description
        if desc.get("docstring") is None and self.docstring is not None:
            desc["docstring"] = self.docstring
        return desc

    @override
    def deconstruct(self) -> tuple[str, list[object], dict[str, object]]:
        """Deconstruct the field into the inner field's migration definition.

        The path and keyword arguments are the inner field's, so a migration
        recreates the underlying column with the original field class. The
        wrapper's ``description`` is carried explicitly when set: the model
        metaclass writes a ``#:`` comment to the wrapper, and
        ``_field_signature`` keeps ``description``, so omitting it here would
        make every ``makemigrations`` see a change that never converges.

        :returns: ``(import_path, args, kwargs)`` tuple.
        """
        path, args, kwargs = self.inner.deconstruct()
        if self.description is not None:
            kwargs["description"] = self.description
        return path, args, kwargs

    @override
    def get_for_dialect(
        self, dialect: str, key: str
    ) -> str | Callable[..., Term] | None:
        """Return the inner field's per-dialect override for *key*.

        Mandatory for the dialect-specific column types (``TIMESTAMPTZ``,
        ``JSONB``, ``UUID``, ``BYTEA``): schema generation and migrations ask
        the wrapper for ``SQL_TYPE`` / ``GENERATED_SQL``, and query expressions
        ask for ``function_cast``.

        :param dialect: The SQL dialect name (``"postgres"``, ...).
        :param key: The attribute or method name to resolve.
        :returns: The dialect override, or the inner field's default value.
        """
        return self.inner.get_for_dialect(dialect, key)

    @override
    def to_db_value(self, value: V, instance: Model | type[Model]) -> CacheScalar:
        """Convert a Python value to the inner field's DB representation.

        :param value: The Python value assigned to the model attribute.
        :param instance: The model instance or class being written.
        :returns: The value the inner field stores.
        """
        return cast(CacheScalar, self.inner.to_db_value(value, instance))

    @override
    def to_python_value(self, value: CacheScalar) -> V:
        """Convert a DB value back to the inner field's Python representation.

        :param value: The value read from the database.
        :returns: The Python value exposed on the model attribute.
        """
        return cast(V, self.inner.to_python_value(value))
