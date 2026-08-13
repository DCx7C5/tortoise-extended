"""Shared cache-value coercion helpers.

JSON-backed cache serializers lose Python types: datetimes become ISO-8601
strings, and without coercion a cache hit exposes ``str`` where a database
hit exposes ``datetime``.  These helpers restore a field's Python type from
the serialized representation, using the model field's ``field_type``.

Used by both
:class:`~tortoise_extended.models.cacheable_model.BaseCacheableModel` and
:class:`~tortoise_extended.cache.queryset.CachedQuerySet` so cache hits and
database hits expose the same value types.
"""

from datetime import date, datetime, time

from tortoise.fields.base import Field

from tortoise_extended._types import CoercedValue, RowValue


def coerce_cache_value(raw: RowValue, field_obj: "Field[RowValue]") -> CoercedValue:
    """Coerce a JSON-deserialized cache value back to the field's Python type.

    Args:
        raw: Value as deserialized from the cache (JSON types + strings).
        field_obj: The Tortoise ``Field`` instance for the target column.

    Returns:
        ``raw`` coerced to ``field_obj.field_type`` when the coercion is
        unambiguous; otherwise ``raw`` unchanged.
    """
    if not isinstance(raw, str):
        return raw
    field_type = getattr(field_obj, "field_type", None)
    if field_type is int:
        try:
            return int(raw)
        except ValueError, TypeError:
            return raw
    if field_type is float:
        try:
            return float(raw)
        except ValueError, TypeError:
            return raw
    if field_type is bool:
        return raw.lower() in ("true", "1", "yes")
    if field_type is datetime:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return raw
    if field_type is date:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return raw
    if field_type is time:
        try:
            return time.fromisoformat(raw)
        except ValueError:
            return raw
    return raw
