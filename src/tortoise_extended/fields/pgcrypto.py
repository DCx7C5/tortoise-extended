"""Thin ``BYTEA`` storage field for PGP-encrypted ciphertext.

PostgreSQL's ``pgcrypto`` extension performs the actual encryption/decryption
in the database.  This field does **not** encrypt or decrypt anything — it is
merely a storage column that holds ciphertext ``bytes`` that your *application*
produces (typically via the :mod:`tortoise_extended.expressions.pgcrypto`
helpers such as ``PGPSymEncrypt`` / ``PGPSymDecrypt``, or any other method).

Usage::

    from tortoise import fields
    from tortoise_extended.fields.pgcrypto import PGPEncryptedField
    from tortoise_extended.expressions.pgcrypto import (
        PGPSymDecrypt,
        PGPSymEncrypt,
    )

    class SecureNote(Model):
        id = fields.IntField(pk=True)
        body = fields.TextField()
        secret = PGPEncryptedField(null=True)

    # Encrypt a plaintext value into the ``secret`` column via pgp_sym_encrypt.
    await SecureNote.filter(id=1).update(
        secret=PGPSymEncrypt("body", "my-passphrase")
    )

    # Decrypt the stored ciphertext back to plaintext via pgp_sym_decrypt.
    note = await SecureNote.annotate(
        decrypted=PGPSymDecrypt("secret", "my-passphrase")
    ).get(id=1)
    print(note.decrypted)

The ``secret`` column stores raw ``bytes`` (PostgreSQL ``BYTEA`` / SQLite
``BLOB``).  Unique / index / equality filters behave like any normal binary
column.
"""

from tortoise.fields.data import BinaryField
from tortoise.fields.data import T_BINARY


class PGPEncryptedField(BinaryField[T_BINARY]):
    """A thin storage field for pgcrypto ciphertext bytes.

    Subclasses :class:`tortoise.fields.data.BinaryField` unchanged in
    behaviour: the column is ``BYTEA`` on PostgreSQL and ``BLOB`` on SQLite,
    and the Python value type is ``bytes``.  This field only **stores** the
    ciphertext — it performs **no encryption or decryption**.  Encryption
    happens in PostgreSQL via pgcrypto (see
    :mod:`tortoise_extended.expressions.pgcrypto`), invoked by your
    application.

    Any unique / index / query filters on the raw ciphertext column behave
    like a normal binary column.
    """


__all__ = ["PGPEncryptedField"]
