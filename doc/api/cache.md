# Cache (Redis)

Advanced Redis caching for Tortoise ORM models, queries, and functions.

## Overview

The cache module provides:

- **RedisCache** — Singleton connection manager with connection pooling
- **BaseCacheableModel** — Abstract base for model-level caching
- **CachedQuerySet** — QuerySet with automatic caching
- **@cached** — Function-level caching decorator
- **@cached_method** — Method-level caching decorator
- **@invalidate** — Cache invalidation decorator

## Installation

```bash
uv add 'tortoise-extended[redis]'
```

Or manually:

```bash
uv add 'redis[hiredis]'
```

## Quick Start

```python
import tortoise_extended  # noqa: F401 — apply patches
from tortoise import Tortoise, fields
from tortoise_extended.cache import RedisCache, BaseCacheableModel, cached

# Initialize Redis
await RedisCache.init(url="redis://localhost:6379/0")

# Initialize Tortoise
await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended",
    modules={"models": ["myapp.models"]},
)

# Use cached model
class Entity(BaseCacheableModel):
    _cache_ttl = 600
    title = fields.CharField(max_length=512)

# Cached query
entity = await Entity.get_cached(id="uuid-here")

# Cached function
@cached(ttl=300)
async def get_entity(entity_id: str):
    return await Entity.get(id=entity_id)
```

## RedisCache

Singleton connection manager.

### init()

```python
await RedisCache.init(
    url="redis://localhost:6379/0",
    max_connections=20,
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | `"redis://localhost:6379/0"` | Redis URL |
| `max_connections` | `int` | `20` | Pool size |

### get_backend()

```python
backend = RedisCache.get_backend(
    namespace="entity",
    default_ttl=300,
    serializer=JSONSerializer(),
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `namespace` | `str` | `"default"` | Key prefix |
| `default_ttl` | `int` | `300` | Default TTL (seconds) |
| `serializer` | `Serializer` | `JSONSerializer()` | Serialization |

### close()

```python
await RedisCache.close()
```

## BaseCacheableModel

Abstract base for model-level caching. Extends `Model` directly — subclass it
and add your own columns.

### Class Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `_cache_ttl` | `int` | `300` | TTL in seconds (0 = disabled) |
| `_cache_fields` | `list[str] \| None` | `None` | Fields to cache (None = all) |
| `_cache_namespace` | `str` | `"model"` | Redis namespace |
| `_cache_backend` | `CacheBackend \| None` | `None` | Custom backend |
| `_cache_serializer` | `Serializer \| None` | `None` | Custom serializer |

### Usage

```python
from tortoise import fields
from tortoise_extended.cache import BaseCacheableModel

class Entity(BaseCacheableModel):
    _cache_ttl = 600
    _cache_fields = ["title", "entity_type"]

    title = fields.CharField(max_length=512)
    entity_type = fields.CharField(max_length=100)
    embedding = VectorField(dimensions=1536, null=True)  # Not cached

    class Meta:
        table = "entities"
```

### Methods

#### get_cached()

```python
entity = await Entity.get_cached(id="uuid-here")
```

Get instance by primary key, using cache.

#### filter_cached()

```python
entities = await Entity.filter_cached(type="TECHNOLOGY")
```

Filter instances, using cache.

#### Automatic Invalidation

```python
# Cache is automatically invalidated on save/delete
await entity.save()  # Invalidates cache
await entity.delete()  # Invalidates cache
```

#### refresh_from_db()

```python
await entity.refresh_from_db()
# Cache is updated with fresh data
```

## CachedQuerySet

QuerySet with automatic caching.

### Usage

```python
from tortoise_extended.cache import CachedQuerySet

# Basic caching
entities = await CachedQuerySet(Entity).filter(type="TECHNOLOGY").cache(ttl=300)

# Custom key
entities = await CachedQuerySet(Entity).filter(type="TECHNOLOGY").cache(
    key="tech_entities",
    ttl=600,
)

# Invalidate specific query
await CachedQuerySet(Entity).filter(type="TECHNOLOGY").cache().invalidate_cache()
```

`CachedQuerySet(Model)` is a drop-in `QuerySet` subclass — construct it
directly (as above) or expose it through a custom manager. Query chaining
(`.filter()`, `.order_by()`, ...) works exactly like a normal QuerySet.

### Methods

#### cache()

```python
qs = qs.cache(  # qs is a CachedQuerySet
    ttl=300,           # TTL in seconds
    key="custom_key",  # Custom cache key (optional)
    backend=None,      # Custom backend (optional)
    namespace="queryset",
)
```

#### invalidate_cache()

```python
count = await qs.invalidate_cache()
# Returns number of cache entries deleted
```

## @cached Decorator

Cache function results in Redis.

```python
from tortoise_extended.cache import cached

@cached(ttl=600)
async def get_entity(entity_id: str):
    return await Entity.get(id=entity_id)

# First call: hits database
entity = await get_entity("uuid-here")

# Second call: hits cache
entity = await get_entity("uuid-here")
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ttl` | `int` | `300` | TTL in seconds |
| `prefix` | `str \| None` | `None` | Custom key prefix |
| `key_builder` | `Callable \| None` | `None` | Custom key function |
| `backend` | `CacheBackend \| None` | `None` | Custom backend |
| `namespace` | `str` | `"decorators"` | Cache namespace |

### Cache Control

```python
# Get cache key
key = get_entity.cache_key("uuid-here")

# Invalidate specific call
await get_entity.invalidate("uuid-here")
```

## @cached_method Decorator

Cache method results (excludes self/cls from key).

```python
from tortoise_extended.cache import cached_method

class EntityService:
    @cached_method(ttl=300)
    async def get_entity(self, entity_id: str):
        return await Entity.get(id=entity_id)
```

## @invalidate Decorator

Invalidate cache entries on function call.

```python
from tortoise_extended.cache import invalidate

@invalidate("entity:*", namespace="entity")
async def update_entity(entity_id: str, data: dict):
    await Entity.filter(id=entity_id).update(**data)
    # Cache invalidated after function completes
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `*patterns` | `str` | Required | Key patterns (supports *) |
| `namespace` | `str` | `"decorators"` | Cache namespace |
| `key_func` | `Callable \| None` | `None` | Custom invalidation key |

## CacheBackend Interface

Abstract base for custom backends.

```python
from tortoise_extended.cache.base import CacheBackend

class MemcachedBackend(CacheBackend):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> bool: ...
    async def exists(self, key: str) -> bool: ...
    async def expire(self, key: str, ttl: int) -> bool: ...
    async def keys(self, pattern: str) -> list[str]: ...
    async def delete_pattern(self, pattern: str) -> int: ...
```

## Serializers

| Serializer | Use Case | Safety |
|------------|----------|--------|
| `JSONSerializer` | Default, human-readable | Safe |
| `PickleSerializer` | Fast, supports more types | Unsafe with untrusted data |
| `NullSerializer` | Raw bytes passthrough | N/A |

## Key Patterns

```python
from tortoise_extended.cache.base import CacheKey, CacheNamespace

# Build secrets
key = CacheKey("entity").add("get", "123").build()
# => "entity:get:123"

key = CacheKey.from_dict("entity", {"id": "123", "type": "TECHNOLOGY"}).build()
# => "entity:id:123:type:TECHNOLOGY"

# Namespaces
ns = CacheNamespace("entity")
key = ns.key("get", "123")
# => "entity:get:123"
```

## Performance

| Operation | Latency | Throughput |
|-----------|---------|------------|
| GET (small value) | <1ms | 100K/sec |
| SET (small value) | <1ms | 80K/sec |
| MGET (100 keys) | <2ms | 50K/sec |
| Pipeline (100 ops) | <5ms | 20K/sec |

## Memory Usage

| Data Type | Size per Entry | 1M Entries |
|-----------|----------------|------------|
| Small dict | ~200 bytes | ~200 MB |
| Large dict | ~2 KB | ~2 GB |
| Model instance | ~1 KB | ~1 GB |

## Best Practices

1. **Set appropriate TTLs** — Short for volatile data, long for static
2. **Use namespaces** — Separate cache regions for different models
3. **Limit cached fields** — Only cache what you need
4. **Monitor memory** — Use `INFO memory` in Redis
5. **Handle failures gracefully** — Cache is optional, DB is source of truth

## Troubleshooting

### Redis Not Connected

```
tortoise_extended.exceptions.CacheBackendNotInitializedError:
Redis cache not initialized
```

**Fix:**
```python
await RedisCache.init(url="redis://localhost:6379/0")
```

### Import Error

```
ImportError: redis package not installed
```

**Fix:**
```bash
uv add 'redis[hiredis]'
```

### Cache Stampede

Use `setnx` or locks for hot keys:

```python
import asyncio

_lock = asyncio.Lock()

@cached(ttl=60)
async def get_hot_data(key: str):
    async with _lock:
        return await fetch_from_db(key)
```
