# Field Types

Additional field types for PostgreSQL and common Python value domains.

## CharField

Drop-in replacement for `tortoise.fields.CharField` that stores the column as
PostgreSQL `TEXT` instead of `VARCHAR(n)`. `max_length` is still required and
still enforced in Python by `MaxLengthValidator`, but it is not emitted as a
SQL type constraint — which removes the PostgreSQL 18 `character varying`
warning and makes length changes migration-free.

### Import

```python
from tortoise_extended import CharField
```

### Constructor

```python
CharField(max_length: int, *, null: bool = False, default=None,
          description: str | None = None, validators=None, **kwargs)
```

### Python Type

`str` (or `str | None` with `null=True`)

### SQL Type

`TEXT`.

### Usage

```python
from tortoise import models
from tortoise_extended import CharField


class Article(models.Model):
    title = CharField(max_length=200)
    slug = CharField(max_length=100, unique=True)

    class Meta:
        table = "articles"
```

### Notes

- `max_length` must be `>= 1`; it feeds `MaxLengthValidator`, not the DDL.
- Supports `unique=True`, `db_index=True`, and `primary_key=True` (unlike
  `TextField`, which blocks all three).

---

## IPv4Field

PostgreSQL `inet` column for IPv4 addresses.

### Import

```python
from tortoise_extended import IPv4Field
```

### Constructor

```python
IPv4Field(
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `Any` | `None` | Default address value |
| `description` | `str \| None` | `None` | Column comment |

### Python Type

```python
ipaddress.IPv4Address | None
```

### SQL Type

- **PostgreSQL:** `inet`

### Usage

```python
from tortoise import models
from tortoise_extended import IPv4Field


class Server(models.Model):
    name = models.CharField(max_length=100)
    ip = IPv4Field()

    class Meta:
        table = "servers"


# String input is validated and stored as a dotted-quad
server = await Server.create(name="web-01", ip="192.168.1.10")

# Python IPv4Address works too
from ipaddress import IPv4Address
server = await Server.create(name="db-01", ip=IPv4Address("10.0.0.5"))
```

### Notes

- Accepts `str` or `ipaddress.IPv4Address` on write; always returns `IPv4Address` on read
- Invalid strings raise `ValueError` at save time and at load time
- The `inet` type supports native indexing on PostgreSQL

---

## PathField

Text column for storing filesystem-style paths.

### Import

```python
from tortoise_extended import PathField
```

### Constructor

```python
PathField(
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `Any` | `None` | Default path value |
| `description` | `str \| None` | `None` | Column comment |

### Python Type

```python
pathlib.PurePosixPath | None
```

### SQL Type

- **PostgreSQL:** `TEXT`

### Usage

```python
from pathlib import Path
from tortoise import models
from tortoise_extended import PathField


class Document(models.Model):
    title = models.CharField(max_length=100)
    path = PathField()

    class Meta:
        table = "documents"


# pathlib.Path is converted to a string on save
doc = await Document.create(title="README", path=Path("docs/readme.md"))

# Read back as PurePosixPath
doc = await Document.get(id=doc.id)
doc.path  # PurePosixPath("docs/readme.md")
```

### Notes

- Accepts `str` or `pathlib.Path` on write; always returns `PurePosixPath` on read
- NUL bytes in the path string are rejected with `ValueError`
- Plain `TEXT` column — no ltree semantics; use `LTreeField` for hierarchical path queries

---

## URLField

Text column for storing validated URLs.

### Import

```python
from tortoise_extended import URLField
```

### Constructor

```python
URLField(
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `Any` | `None` | Default URL value |
| `description` | `str \| None` | `None` | Column comment |

### Python Type

```python
str | None
```

### SQL Type

- **PostgreSQL:** `TEXT`

### Usage

```python
from tortoise import models
from tortoise_extended import URLField


class Page(models.Model):
    title = models.CharField(max_length=100)
    url = URLField()

    class Meta:
        table = "pages"


page = await Page.create(title="Home", url="https://example.com/")
```

### Validation Rules

The field validates at save time (`to_db_value`):

- Must include both a **scheme** and a **netloc** (per `urllib.parse.urlsplit`)
- Leading/trailing whitespace is rejected (not silently stripped)
- Whitespace or control characters in the value are rejected

### Error Handling

```python
# Missing scheme — raises ValueError
await Page.create(title="Bad", url="example.com")

# Whitespace — raises ValueError
await Page.create(title="Bad", url=" https://example.com/")
```

---

## UUID4Field

PostgreSQL `uuid` column auto-populated with a random UUID v4.

### Import

```python
from tortoise_extended import UUID4Field
```

### Constructor

```python
UUID4Field(
    null: bool = False,
    default: UUID | Callable[[], UUID] | None = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `UUID \| Callable[[], UUID] \| None` | `uuid4` | Default UUID value or callable (injects `uuid4` when `None`) |
| `description` | `str \| None` | `None` | Column comment |

### Python Type

```python
UUID | None
```

### SQL Type

- **PostgreSQL:** `UUID` (inherited from tortoise `UUIDField._db_postgres`)

### Usage

```python
from tortoise import models
from tortoise_extended import UUID4Field


class Event(models.Model):
    id = UUID4Field(pk=True)
    trace_id = UUID4Field()  # non-PK also auto-generates

    class Meta:
        table = "events"


# Both columns auto-generate UUIDs when no default is given
event = await Event.create()
event.id       # UUID("a1b2c3d4-...")
event.trace_id # UUID("e5f6a7b8-...")
```

### Notes

- Subclasses tortoise's `UUIDField` and defaults to `uuid.uuid4` when no `default` is supplied
- Overrides tortoise's pk-only default injection — non-primary-key columns also auto-generate values
- `null=True` intentionally types as `Field[UUID]` rather than `Field[UUID | None]` due to tortoise stub invariance

---

## UUID7Field

PostgreSQL `uuid` column auto-populated with a time-ordered UUID v7.

### Import

```python
from tortoise_extended import UUID7Field
```

### Constructor

```python
UUID7Field(
    null: bool = False,
    default: UUID | Callable[[], UUID] | None = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `UUID \| Callable[[], UUID] \| None` | `uuid7` | Default UUID value or callable (injects `uuid7` when `None`) |
| `description` | `str \| None` | `None` | Column comment |

### Python Type

```python
UUID | None
```

### SQL Type

- **PostgreSQL:** `UUID` (inherited from tortoise `UUIDField._db_postgres`)

### Usage

```python
from tortoise import models
from tortoise_extended import UUID7Field


class Event(models.Model):
    id = UUID7Field(pk=True)

    class Meta:
        table = "events"


event = await Event.create()
event.id  # UUID with embedded timestamp (time-ordered)
```

### UUID4 vs UUID7

| Feature | UUID4Field | UUID7Field |
|---------|-----------|-----------|
| Randomness | Fully random | Timestamp + random |
| Insert locality | Poor (random pages) | Good (sequential) |
| Best for | General use, non-PK | PK on insert-heavy tables |
| Python | `uuid.uuid4` | `uuid.uuid7` (Python 3.14+) |

### Notes

- UUID7 values embed a Unix timestamp, which keeps index locality good for insert-heavy tables
- Requires Python 3.14+ for `uuid.uuid7`
- Same `null=True` typing caveat as `UUID4Field`
