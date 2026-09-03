# pgcrypto

PostgreSQL pgcrypto query-expression helpers and a thin PGP ciphertext storage field.

## Overview

This module provides thin pypika/Tortoise `Function` wrappers that render
[pgcrypto](https://www.postgresql.org/docs/current/pgcrypto.html) SQL
(`digest`, `hmac`, `pgp_sym_encrypt`, `pgp_sym_decrypt`, …) and a
`PGPEncryptedField` storage column for the ciphertext those expressions
produce.

> **Important:** The library performs **no encryption itself**.  The helpers
> only make it easy to call pgcrypto from inside Tortoise queries.  Your
> application supplies the keys — the library never sees or stores them.

## Import

```python
# Expressions (query helpers)
from tortoise_extended.expressions.pgcrypto import (
    Digest,
    DigestHex,
    HMACDigest,
    PGPSymEncrypt,
    PGPSymDecrypt,
)

# Field (storage)
from tortoise_extended.fields.pgcrypto import PGPEncryptedField
```

## PGPEncryptedField

A thin `BYTEA` / `BLOB` storage field that holds ciphertext bytes your
application produces.  It subclasses `tortoise.fields.data.BinaryField`
unchanged — the column is `BYTEA` on PostgreSQL and `BLOB` on SQLite, and
the Python value type is `bytes`.

```python
from tortoise import fields
from tortoise_extended.fields.pgcrypto import PGPEncryptedField

class SecureNote(Model):
    id = fields.IntField(pk=True)
    body = fields.TextField()
    secret = PGPEncryptedField(null=True)
```

**Parameters:** inherited from `BinaryField` (`null`, `default`, `description`,
`pk`, `unique`, `index`, …).

The field **does not** encrypt or decrypt — it only stores `bytes`.  Pair it
with `PGPSymEncrypt` / `PGPSymDecrypt` (below) to encrypt on write and
decrypt on read.

### Encrypt into a column

```python
from tortoise_extended.expressions.pgcrypto import PGPSymEncrypt

await SecureNote.filter(id=1).update(
    secret=PGPSymEncrypt("body", "my-passphrase")
)
```

### Decrypt from a column

```python
from tortoise_extended.expressions.pgcrypto import PGPSymDecrypt

note = await SecureNote.annotate(
    decrypted=PGPSymDecrypt("secret", "my-passphrase")
).get(id=1)
print(note.decrypted)
```

---

## Digest

`digest(data, algorithm)` — raw binary hash (`bytea`).

```python
from tortoise_extended.expressions.pgcrypto import Digest

# Binary digest as an annotation
rows = await Document.annotate(hash=Digest("body", "sha256")).all()
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `str \| F \| Function \| Term` | Required | Value to hash (field name, `F()`, or expression) |
| `algorithm` | `str` | `"sha256"` | Hash algorithm (`"sha256"`, `"sha512"`, `"md5"`, …) |

**Returns:** `bytea`.

> Note: `digest` is the raw cryptographic hash — it is not HMAC and is
> vulnerable to length-extension attacks.  Use `HMACDigest` when a secret
> key is involved.

---

## DigestHex

`digest(data, algorithm)` wrapped in `encode(…, 'hex')` — hex string digest.

```python
from tortoise_extended.expressions.pgcrypto import DigestHex

# Hex-encoded digest
rows = await Document.annotate(hash=DigestHex("body", "sha256")).all()
```

**Parameters:** same as `Digest`.

**Returns:** `str` — a lowercase hex string (e.g. `"a1b2c3…"`) suitable for
storing or transmitting a digest as text.

---

## HMACDigest

`hmac(data, key, algorithm)` — keyed hash (HMAC).

```python
from tortoise_extended.expressions.pgcrypto import HMACDigest

# HMAC tag as an annotation
rows = await Document.annotate(
    tag=HMACDigest("body", "shared-key", "sha256")
).all()
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `str \| F \| Function \| Term` | Required | Value to hash |
| `key` | `str` | Required | Secret key (rendered as a SQL literal) |
| `algorithm` | `str` | `"sha256"` | Hash algorithm |

**Returns:** `bytea`.

---

## PGPSymEncrypt

`pgp_sym_encrypt(data, key[, options])` — symmetric PGP encryption.

```python
from tortoise_extended.expressions.pgcrypto import PGPSymEncrypt

# Encrypt and store
await Document.filter(id=1).update(
    secret=PGPSymEncrypt("body", "my-passphrase")
)

# Annotate with encrypted value (for inspecting ciphertext)
rows = await Document.annotate(
    enc=PGPSymEncrypt("body", "my-passphrase")
).all()
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `str \| F \| Function \| Term` | Required | Plaintext to encrypt |
| `key` | `str` | Required | Symmetric passphrase (SQL literal) |
| `options` | `str \| None` | `None` | pgcrypto options, e.g. `"cipher-algo=aes256, compress-algo=0"` |

**Returns:** `bytea` — PGP-encrypted packet, suitable for storage in
`PGPEncryptedField`.

---

## PGPSymDecrypt

`pgp_sym_decrypt(data, key[, options])` — symmetric PGP decryption.

```python
from tortoise_extended.expressions.pgcrypto import PGPSymDecrypt

# Decrypt stored ciphertext
note = await SecureNote.annotate(
    decrypted=PGPSymDecrypt("secret", "my-passphrase")
).get(id=1)
print(note.decrypted)
```

**Parameters:** same as `PGPSymEncrypt`.

**Returns:** `str` — plaintext.

---

## Requirements

- PostgreSQL with the `pgcrypto` extension enabled:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

- The Docker image at `docker/postgres-ext/` does **not** include `pgcrypto`
  by default — add it to `docker/postgres-ext/scripts/00-extensions.sql` or
  run the `CREATE EXTENSION` statement manually.

## Security Notes

- **The library never sees or stores keys.**  Keys are rendered as SQL string
  literals in the query — they travel to PostgreSQL but are not logged or
  cached by tortoise-extended.
- **Use application-managed keys.**  Store passphrases/secrets in environment
  variables or a secrets manager; do not hardcode them in model definitions.
- **`PGPEncryptedField` does not encrypt.**  It is a plain `BYTEA` storage
  column.  If rows are leaked, the ciphertext is only as safe as the key
  protecting it.
- **`Digest` is not HMAC.**  For keyed hashing, prefer `HMACDigest` which is
  not vulnerable to length-extension attacks.
