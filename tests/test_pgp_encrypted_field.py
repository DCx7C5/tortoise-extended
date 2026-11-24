"""``PGPEncryptedField`` — bytea storage, boundary validation, pgcrypto pairing.

Sections:
  1. ``TestPGPEncryptedFieldUnit`` — value conversion and refusals (no DB).
  2. ``TestPGPEncryptedFieldPG`` — schema, roundtrip and decryption on PostgreSQL.
"""

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended import PGPEncryptedField
from tortoise_extended.expressions.pgcrypto import PGPSymDecrypt, PGPSymEncrypt
from tests._pg_env import database_url, pg_skip_reason

_DB_URL = database_url()


# =========================================================================
# Section 1 — unit (no DB)
# =========================================================================


class TestPGPEncryptedFieldUnit:
    """Conversion and boundary validation happen without a database."""

    def test_declares_bytea_and_refuses_indexing(self) -> None:
        field = PGPEncryptedField()
        assert field.SQL_TYPE == "bytea"
        assert field.indexable is False

    def test_accepts_and_normalises_bytes_like(self) -> None:
        field = PGPEncryptedField(null=True)
        assert field.to_db_value(b"cipher", None) == b"cipher"
        assert field.to_db_value(bytearray(b"cipher"), None) == b"cipher"
        assert field.to_db_value(memoryview(b"cipher"), None) == b"cipher"

    def test_none_round_trips_when_nullable(self) -> None:
        field = PGPEncryptedField(null=True)
        assert field.to_db_value(None, None) is None
        assert field.to_python_value(None) is None

    def test_str_is_refused_by_name(self) -> None:
        """A str is plaintext in disguise — the field refuses it explicitly."""
        field = PGPEncryptedField(null=True)
        with pytest.raises(TypeError, match="stores ciphertext bytes, not str"):
            field.to_db_value("plaintext", None)  # type: ignore[arg-type]

    def test_non_bytes_object_is_refused(self) -> None:
        field = PGPEncryptedField(null=True)
        with pytest.raises(TypeError):
            field.to_db_value(1234, None)  # type: ignore[arg-type]

    def test_python_value_normalises_memoryview(self) -> None:
        field = PGPEncryptedField()
        assert field.to_python_value(memoryview(b"cipher")) == b"cipher"

    def test_repr_names_the_field(self) -> None:
        assert repr(PGPEncryptedField()) == "PGPEncryptedField()"


# =========================================================================
# Section 2 — PostgreSQL
# =========================================================================


class EncryptedDocument(Model):
    """Document with an encrypted column and a blind index beside it."""

    id = fields.IntField(primary_key=True)
    plain_text = fields.CharField(max_length=200, null=True)
    secret = PGPEncryptedField(null=True)
    secret_tag = fields.CharField(max_length=64, null=True)

    class Meta:
        table = "test_encrypted_documents"


_PG_SKIP_REASON = pg_skip_reason()


class TestPGPEncryptedFieldPG:
    """The column is really ``bytea`` and pairs with the pgp_sym_* helpers."""

    pytestmark = pytest.mark.skipif(
        _PG_SKIP_REASON is not None,
        reason=_PG_SKIP_REASON or "",
    )

    @pytest.fixture(autouse=True)
    async def _init_pg_db(self):
        await Tortoise.init(
            db_url=_DB_URL,
            modules={"models": ["tests.test_pgp_encrypted_field"]},
        )
        conn = Tortoise.get_connection("default")
        await conn.execute_query(
            "DROP TABLE IF EXISTS test_encrypted_documents CASCADE"
        )
        await Tortoise.generate_schemas()
        yield
        await conn.execute_query(
            "DROP TABLE IF EXISTS test_encrypted_documents CASCADE"
        )
        await Tortoise.close_connections()

    async def test_column_is_bytea(self) -> None:
        conn = Tortoise.get_connection("default")
        _, rows = await conn.execute_query(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'test_encrypted_documents' AND column_name = 'secret'"
        )
        assert rows[0]["data_type"] == "bytea"

    async def test_bytes_round_trip(self) -> None:
        doc = await EncryptedDocument.create(secret=b"\x00\x01cipher")
        reloaded = await EncryptedDocument.get(id=doc.id)
        assert reloaded.secret == b"\x00\x01cipher"

    async def test_null_ciphertext_round_trips(self) -> None:
        doc = await EncryptedDocument.create()
        reloaded = await EncryptedDocument.get(id=doc.id)
        assert reloaded.secret is None

    async def test_pairs_with_pgp_sym_helpers(self) -> None:
        """Ciphertext produced by pgcrypto is stored as bytes and decrypts in SQL."""
        conn = Tortoise.get_connection("default")
        _, rows = await conn.execute_query(
            "SELECT pgp_sym_encrypt('top secret', 'test-passphrase') AS cipher"
        )
        cipher = rows[0]["cipher"]
        assert isinstance(cipher, bytes)

        doc = await EncryptedDocument.create(secret=cipher)
        row = await EncryptedDocument.annotate(
            clear=PGPSymDecrypt("secret", "test-passphrase"),
        ).get(id=doc.id)
        assert row.clear == "top secret"

    async def test_ciphertext_is_not_reversible_by_comparison(self) -> None:
        """Non-deterministic ciphertext: two rows never share a value for one plaintext."""
        conn = Tortoise.get_connection("default")
        _, rows = await conn.execute_query(
            "SELECT pgp_sym_encrypt('same', 'pass') AS c1, "
            "pgp_sym_encrypt('same', 'pass') AS c2"
        )
        assert rows[0]["c1"] != rows[0]["c2"]

        first = await EncryptedDocument.create(secret=rows[0]["c1"])
        second = await EncryptedDocument.create(secret=rows[0]["c2"])
        assert first.secret != second.secret

    async def test_encrypt_into_the_column_then_decrypt(self) -> None:
        """Encrypt a text column into the bytea column, then read it back."""
        doc = await EncryptedDocument.create(plain_text="top secret")
        await EncryptedDocument.filter(id=doc.id).update(
            secret=PGPSymEncrypt("plain_text", "test-passphrase"),
        )
        reloaded = await EncryptedDocument.get(id=doc.id)
        assert isinstance(reloaded.secret, bytes)
        assert reloaded.secret != b"top secret"

        row = await EncryptedDocument.annotate(
            clear=PGPSymDecrypt("secret", "test-passphrase"),
        ).get(id=doc.id)
        assert row.clear == "top secret"
