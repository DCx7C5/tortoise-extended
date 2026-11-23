"""PostgreSQL ``bytea`` field for application-produced pgcrypto ciphertext.

The column stores the ciphertext ``bytes`` *your application* produces. It does
**no** encryption or decryption and never sees a key — the same scope rule the
expression helpers follow (:mod:`tortoise_extended.expressions.pgcrypto`):
the library only supplies the storage type and the SQL wrappers, so the
application owns the key material.

Why a dedicated type rather than a plain ``BinaryField``:

* it documents intent in the model — a reader (and a reviewer) sees that the
  column holds ciphertext, not an arbitrary blob;
* it validates the boundary: a ``str`` is rejected by name instead of being
  stored as text-encoded bytes, which is the usual way plaintext leaks into a
  column that was meant to hold ciphertext;
* it refuses to be indexed (``indexable = False``), because pgcrypto ciphertext
  is non-deterministic — an index on it can never be used for equality and
  signals a design error.

Requires: nothing beyond ``bytea`` for the column. The ``pgp_sym_*`` helpers
need ``CREATE EXTENSION IF NOT EXISTS pgcrypto;``.

Usage::

    from tortoise_extended import PGPEncryptedField

    class Document(Model):
        id = fields.IntField(primary_key=True)
        secret = PGPEncryptedField(null=True)

    # Encrypt with the application's own encryptor, store the ciphertext ...
    doc = await Document.create(secret=encrypt_locally(plaintext))

    # ... or encrypt inside the database and store the result in the column
    await Document.filter(id=doc.id).update(
        secret=PGPSymEncrypt(
            "secret",
            RawSQL("current_setting('app.encryption_key')"),
        )
    )

    # Read plaintext back without storing it anywhere else
    row = await Document.annotate(
        clear=PGPSymDecrypt("secret", RawSQL("current_setting('app.encryption_key')"))
    ).get(id=doc.id)
"""

from typing import Unpack, cast, override

from tortoise.fields.base import Field
from tortoise.models import Model

from tortoise_extended._types import FieldDefaultValue, FieldInitKwargs


class PGPEncryptedField(Field[bytes]):
    """PostgreSQL ``bytea`` column holding pgcrypto ciphertext.

    Thin storage type: the application supplies the ciphertext bytes. Pair it
    with :class:`~tortoise_extended.expressions.pgcrypto.PGPSymEncrypt` /
    :class:`~tortoise_extended.expressions.pgcrypto.PGPSymDecrypt` (encrypt and
    decrypt inside PostgreSQL) or any application-side encryptor.

    .. note::

        This mirrors ``tortoise.fields.BinaryField`` (``bytea`` storage, not
        indexable) rather than subclassing it: the local typing overlay declares
        ``BinaryField`` as an overloaded factory function, so a subclass would
        fail the strict type gate. The runtime behaviour is the same, plus the
        boundary validation below.

    :param null: Allow NULL values (a row that has no ciphertext yet).
    :param default: Default value, e.g. ``None``.
    :param description: Column comment.

    Usage::

        class Document(Model):
            secret = PGPEncryptedField(null=True)
            # a searchable blind index for the same value
            secret_tag = fields.CharField(max_length=64, null=True)

        # store ciphertext, never plaintext
        await Document.create(secret=encrypt_locally(plaintext))

    :raises TypeError: From :meth:`to_db_value`, when the value is a ``str``
        or another non-bytes object — ciphertext must be ``bytes``.
    """

    SQL_TYPE = "bytea"
    indexable = False

    def __init__(
        self,
        *,
        null: bool = False,
        default: FieldDefaultValue = None,
        description: str | None = None,
        **kwargs: Unpack[FieldInitKwargs],
    ) -> None:
        super().__init__(
            null=null,
            default=default,
            description=description,
            **kwargs,
        )

    @override
    def to_python_value(self, value: bytes | memoryview | None) -> bytes | None:
        """Convert a driver value to ``bytes``.

        asyncpg returns ``bytea`` as ``bytes``; other drivers may hand back a
        ``memoryview``, so both are normalised.

        :param value: Raw value from the driver, or ``None``.
        :returns: The ciphertext bytes, or ``None``.
        """
        if value is None:
            return None
        return bytes(value)

    @override
    def to_db_value(
        self,
        value: bytes | bytearray | memoryview | None,
        instance: type[Model] | Model | None,
    ) -> bytes | None:
        """Validate the ciphertext boundary and normalise it to ``bytes``.

        :param value: The ciphertext to store, or ``None``.
        :param instance: The model instance being saved (unused).
        :returns: The ciphertext as ``bytes``, or ``None``.
        :raises TypeError: If the value is a ``str`` or any other non-bytes
            object. Storing text here is the usual way plaintext ends up in a
            column that was meant to hold ciphertext, so it is refused by name
            instead of being encoded.
        """
        if value is None:
            return None
        # The annotation says ciphertext is bytes, but this is the boundary an
        # untyped caller crosses — the refusal below is the whole point of the
        # field, so widen through a cast rather than trust the annotation.
        raw = cast(bytes | bytearray | memoryview | str, value)
        if isinstance(raw, str):
            raise TypeError(
                "PGPEncryptedField stores ciphertext bytes, not str: encrypt "
                "the value first (PGPSymEncrypt(...) or an application-side "
                "encryptor) and pass the resulting bytes. A str would be "
                "stored as plain text in a column that only looks encrypted."
            )
        # ``memoryview`` rejects anything that is not a bytes-like object, so a
        # value outside the contract (an int, a dict) fails here by name
        # instead of being coerced into something plausible.
        return memoryview(raw).tobytes()

    @override
    def __repr__(self) -> str:
        return "PGPEncryptedField()"
