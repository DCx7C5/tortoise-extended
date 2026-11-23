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

## InetField

PostgreSQL `inet` column for IPv4 **and** IPv6 host addresses.

`InetField` is the general-purpose companion to `IPv4Field`: same column
type, but either address family is accepted and returned. Use `IPv4Field`
when the schema should refuse IPv6 values.

### Import

```python
from tortoise_extended import InetField
```

### Constructor

```python
InetField(
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
ipaddress.IPv4Address | ipaddress.IPv6Address | None
```

### SQL Type

- **PostgreSQL:** `inet`

### Usage

```python
from tortoise import models
from tortoise_extended import InetField


class Server(models.Model):
    name = models.CharField(max_length=100)
    ip = InetField()

    class Meta:
        table = "servers"


# Either family is accepted and normalised by the field
v4 = await Server.create(name="web-01", ip="192.168.1.10")
v6 = await Server.create(name="web-02", ip="2001:db8::1")
```

### Notes

- Accepts `str`, `ipaddress.IPv4Address` or `ipaddress.IPv6Address` on write;
  always returns an address object on read
- Invalid strings raise `ValueError` at save time and at load time
- Interface/prefix notation (`192.168.1.0/24`) is **not** accepted — this
  field stores plain host addresses only
- The `inet` type supports native indexing on PostgreSQL

---

## MacAddrField / MacAddr8Field

PostgreSQL `macaddr` (48-bit) and `macaddr8` (64-bit EUI-64) columns for
hardware addresses.

### Import

```python
from tortoise_extended import MacAddr8Field, MacAddrField
```

### Constructor

```python
MacAddrField(
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)

MacAddr8Field(
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
str | None
```

### SQL Type

- **`MacAddrField` — PostgreSQL:** `macaddr` (six octets)
- **`MacAddr8Field` — PostgreSQL:** `macaddr8` (eight octets)

### Usage

```python
from tortoise import models
from tortoise_extended import MacAddr8Field, MacAddrField


class Interface(models.Model):
    name = models.CharField(max_length=100)
    hw = MacAddrField()        # aa:bb:cc:dd:ee:ff
    eui = MacAddr8Field()      # aa:bb:cc:dd:ee:ff:00:11

    class Meta:
        table = "interfaces"


# Colon, dash and dotted-Cisco spellings are all accepted
iface = await Interface.create(name="eth0", hw="AA-BB-CC-DD-EE-FF")
```

### Notes

- Accepted input: colon-separated (`aa:bb:cc:dd:ee:ff`), dash-separated
  (`aa-bb-cc-dd-ee-ff`) and dotted-Cisco (`aabb.ccdd.eeff`, or
  `aabb.ccdd.eeff.0011` for `macaddr8`)
- Every form is normalised to lowercase, colon-separated on write **and** on
  read
- A wrong octet count or non-hex text raises `ValueError` naming the received
  value and the expected form
- `MacAddrField` expects six octets; `MacAddr8Field` expects eight

---

## CitextField

PostgreSQL `citext` column for case-insensitive text.

Comparisons, `UNIQUE` constraints and `ORDER BY` are case-insensitive because
the database type handles them; the original spelling is preserved on read.

**:warning: Prerequisite:** the `citext` extension must exist in the database:

```sql
CREATE EXTENSION IF NOT EXISTS citext;
```

### Import

```python
from tortoise_extended import CitextField
```

### Constructor

```python
CitextField(
    max_length: int = 256,
    *,
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_length` | `int` | `256` | Python-side length guard (see notes) |
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `Any` | `None` | Default text value |
| `description` | `str \| None` | `None` | Column comment |

### Python Type

```python
str | None
```

### SQL Type

- **PostgreSQL:** `citext`

### Usage

```python
from tortoise import models
from tortoise_extended import CitextField


class Account(models.Model):
    email = CitextField(max_length=320)

    class Meta:
        table = "accounts"


account = await Account.create(email="Mixed@Example.COM")

# Case-insensitive lookup — matches the row above
found = await Account.get(email="mixed@example.com")
```

### Notes

- `citext` has no column length modifier, so `max_length` is enforced as a
  Python-side guard in `to_db_value`; a longer value raises `ValueError`
  naming the limit
- The stored value keeps its original case (unlike a lowercase-normalising
  field)
- `LIKE` / `icontains` queries inherit the type's case-insensitivity

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

## PGPEncryptedField

`bytea` column for ciphertext produced by the application (or by the
`pgp_sym_*` helpers). The field performs **no** encryption: it stores the bytes
you pass and never sees a key.

### Import

```python
from tortoise_extended import PGPEncryptedField
```

### Constructor

```python
PGPEncryptedField(*, null: bool = False, default=None,
                  description: str | None = None, **kwargs)
```

### Python Type

`bytes` (or `bytes | None` with `null=True`)

### SQL Type

`bytea`.

### Behaviour

- **Not indexable** (`indexable = False`) — pgcrypto ciphertext is
  non-deterministic, so an index on it can never serve an equality lookup.
  Derive a blind index in a companion column with `HMACDigest` / `DigestHex`
  when you need to search.
- **`str` is refused** with `TypeError` naming the field: storing text here is
  the usual way plaintext ends up in a column that only looks encrypted.
  `bytes`, `bytearray` and `memoryview` are accepted and normalised to `bytes`.
- Reads normalise `memoryview` (some drivers) back to `bytes`.

### Usage

```python
from tortoise import fields, models
from tortoise_extended import PGPEncryptedField
from tortoise_extended.expressions.pgcrypto import PGPSymDecrypt, PGPSymEncrypt


class Document(models.Model):
    id = fields.IntField(primary_key=True)
    body_text = fields.TextField(null=True)
    secret = PGPEncryptedField(null=True)

    class Meta:
        table = "documents"


# encrypt a text column into the bytea column, inside the database
await Document.filter(id=doc_id).update(
    secret=PGPSymEncrypt("body_text", "passphrase")
)

# read the plaintext back without storing it anywhere else
doc = await Document.annotate(
    clear=PGPSymDecrypt("secret", "passphrase")
).get(id=doc_id)
```

Requires nothing beyond `bytea` for the column itself; the `pgp_sym_*` helpers
need `CREATE EXTENSION IF NOT EXISTS pgcrypto;` (see [pgcrypto](pgcrypto.md)).

