"""Cache decorators for functions and methods.

Provides:
- @cached: Function-level caching
- @cached_method: Method-level caching
- @invalidate: Cache invalidation on call
"""

import contextlib
import functools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Concatenate, TypeVar, cast

from tortoise_extended._types import CacheValue, P, R
from tortoise_extended.cache.base import CacheBackend, CacheKey
from tortoise_extended.exceptions import CacheError

logger = logging.getLogger(__name__)

T = TypeVar("T")
"""Type of the bound ``self`` on a :func:`cached_method` wrapper."""


def _build_cache_key(
    func: Callable[..., str],
    args: tuple[CacheValue, ...],
    kwargs: dict[str, CacheValue],
    prefix: str | None = None,
    key_builder: Callable[..., str] | None = None,
) -> str:
    """Build cache key from function signature."""
    if key_builder:
        return key_builder(*args, **kwargs)

    func_name = (
        f"{getattr(func, '__module__', '<unknown>')}."
        f"{getattr(func, '__qualname__', '<unknown>')}"
    )
    key = CacheKey(prefix or func_name)
    _ = key.add(
        CacheKey.hash(
            json.dumps({"args": str(args), "kwargs": str(kwargs)}, default=str)
        )
    )
    return key.build()


def cached(
    ttl: int = 300,
    prefix: str | None = None,
    key_builder: Callable[..., str] | None = None,
    backend: CacheBackend | None = None,
    namespace: str = "decorators",
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Cache function results in Redis.

    Args:
        ttl: Time-to-live in seconds (0 = no expiry)
        prefix: Custom key prefix (default: function name)
        key_builder: Custom key builder function(*args, **kwargs) -> str
        backend: Cache backend (default: RedisCache)
        namespace: Cache namespace

    Usage:

        @cached(ttl=600)
        async def get_entity(entity_id: str):
            return await Entity.get(id=entity_id)

        # Cache is automatically used
        entity = await get_entity("uuid-here")
    """

    def decorator(
        func: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        """Decorate an async function with cache read/write logic."""

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Call ``func``, serving cached results when available."""
            nonlocal backend
            if backend is None:
                from tortoise_extended.cache.redis import RedisCache

                backend = RedisCache.get_backend(namespace=namespace, default_ttl=ttl)

            cache_key = _build_cache_key(
                cast(Callable[..., str], func),
                cast(tuple[CacheValue, ...], args),
                cast(dict[str, CacheValue], kwargs),
                prefix,
                key_builder,
            )

            # Try cache
            try:
                cached_value = await backend.get(cache_key)
                if cached_value is not None:
                    return cast(R, cached_value)
            except CacheError:
                logger.debug("Cache read error for key %s", cache_key, exc_info=True)

            # Execute function
            result = await func(*args, **kwargs)

            # Store in cache
            if result is not None:
                with contextlib.suppress(CacheError):
                    await backend.set(cache_key, cast(CacheValue, result), ttl=ttl)

            return result

        # Expose cache control methods
        def _invalidate(*_a: CacheValue, **_kw: CacheValue) -> Awaitable[None]:
            """Invalidate cache for this decorated function."""
            return _invalidate_cached(
                cast(Callable[..., str], func),
                _a,
                _kw,
                prefix,
                key_builder,
                namespace,
            )

        def _cache_key(*a: CacheValue, **kw: CacheValue) -> str:
            """Get cache key for given arguments."""
            return _build_cache_key(
                cast(Callable[..., str], func), a, kw, prefix, key_builder
            )

        setattr(wrapper, "invalidate", _invalidate)
        setattr(wrapper, "cache_key", _cache_key)

        return wrapper

    return decorator


def cached_method(
    ttl: int = 300,
    prefix: str | None = None,
    namespace: str = "methods",
) -> Callable[
    [Callable[Concatenate[T, P], Awaitable[R]]],
    Callable[Concatenate[T, P], Awaitable[R]],
]:
    """Cache method results in Redis.

    The first argument (self/cls) is excluded from the cache key.

    Usage:

        class EntityService:
            @cached_method(ttl=300)
            async def get_entity(self, entity_id: str):
                return await Entity.get(id=entity_id)
    """

    def decorator(
        func: Callable[Concatenate[T, P], Awaitable[R]],
    ) -> Callable[Concatenate[T, P], Awaitable[R]]:
        """Decorate an async method with cache read/write logic."""

        @functools.wraps(func)
        async def wrapper(self: T, *args: P.args, **kwargs: P.kwargs) -> R:
            """Call ``func``, serving cached results when available."""
            from tortoise_extended.cache.redis import RedisCache

            backend = RedisCache.get_backend(namespace=namespace, default_ttl=ttl)

            # Build key without self
            func_qualname = getattr(func, "__qualname__", "<unknown>")
            func_name = f"{type(self).__name__}.{func_qualname}"
            p = prefix or func_name
            key = CacheKey(p)
            _ = key.add(
                CacheKey.hash(
                    json.dumps({"args": str(args), "kwargs": str(kwargs)}, default=str)
                )
            )
            cache_key = key.build()

            # Try cache
            try:
                cached_value = await backend.get(cache_key)
                if cached_value is not None:
                    return cast(R, cached_value)
            except CacheError:
                logger.debug("Cache read error for method %s", cache_key, exc_info=True)

            # Execute method
            result = await func(self, *args, **kwargs)

            # Store in cache
            if result is not None:
                with contextlib.suppress(CacheError):
                    await backend.set(cache_key, cast(CacheValue, result), ttl=ttl)

            return result

        return wrapper

    return decorator


async def _invalidate_cached(
    func: Callable[..., str],
    args: tuple[CacheValue, ...],
    kwargs: dict[str, CacheValue],
    prefix: str | None,
    key_builder: Callable[..., str] | None,
    namespace: str,
) -> None:
    """Invalidate a cached function call."""
    from tortoise_extended.cache.redis import RedisCache

    backend = RedisCache.get_backend(namespace=namespace)
    cache_key = _build_cache_key(func, args, kwargs, prefix, key_builder)
    _ = await backend.delete(cache_key)


def invalidate(
    *patterns: str,
    namespace: str = "decorators",
    key_func: Callable[..., str] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Invalidate cache entries when decorated function is called.

    Args:
        patterns: Key patterns to invalidate (supports * wildcards)
        namespace: Cache namespace
        key_func: Custom function to generate invalidation keys

    Usage:

        @invalidate("entity:*", namespace="entity")
        async def update_entity(entity_id: str, data: dict):
            await Entity.filter(id=entity_id).update(**data)
    """

    def decorator(
        func: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        """Decorate an async function with post-call cache invalidation."""

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Call ``func``, then invalidate matching cache entries."""
            from tortoise_extended.cache.redis import RedisCache

            backend = RedisCache.get_backend(namespace=namespace)

            # Execute function first
            result = await func(*args, **kwargs)

            # Invalidate cache
            try:
                if key_func:
                    key = key_func(*args, **kwargs)
                    _ = await backend.delete(key)
                else:
                    for pattern in patterns:
                        _ = await backend.delete_pattern(pattern)
            except CacheError:
                logger.debug(
                    "Cache invalidation error for patterns %s", patterns, exc_info=True
                )

            return result

        return wrapper

    return decorator
