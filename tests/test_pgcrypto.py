"""Tests for pgcrypto expression wrappers (hash/digest + symmetric PGP).

Sections:
  1. ``TestPgcryptoSQL`` — SQL rendering (unit, no DB required).
  2. ``TestPgcryptoAlgorithmAllowlist`` — algorithm allowlist (SEC-M03).
  3. ``TestPgcryptoIntegration`` — live PostgreSQL integration (skipped when PG is down).
"""

import hashlib
import hmac as hmac_mod

import pytest
from pypika_tortoise import Field
from pypika_tortoise.context import DEFAULT_SQL_CONTEXT

import tortoise_extended  # noqa: F401 — apply patches
from tortoise import Tortoise, fields
from tortoise.expressions import RawSQL
from tortoise.models import Model
from tortoise.transactions import in_transaction

from tortoise_extended.expressions.pgcrypto import (
    Digest,
    DigestHex,
    HMACDigest,
    PGPSymDecrypt,
    PGPSymEncrypt,
)
from tortoise_extended.exceptions import PgCryptoError
from tortoise_extended.expressions.pgcrypto import (
    _DigestFunction,
    _DigestHexFunction,
    _HMACFunction,
    _PGPSymDecryptFunction,
    _PGPSymEncryptFunction,
)
from tests._pg_env import database_url, pg_skip_reason


# =========================================================================
# Section 1 — SQL rendering (unit, no DB)
# =========================================================================


class TestPgcryptoSQL:
    """Verify the pypika-level SQL rendering for each pgcrypto expression."""

    def test_digest_renders_function_name_and_quoted_algorithm(self) -> None:
        """Digest('body', 'sha256') -> DIGEST('body', 'sha256')."""
        sql = _DigestFunction(Field("body"), "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        upper = sql.upper()
        assert "DIGEST(" in upper
        assert "'sha256'" in sql
        # data column reference is quoted identifier, not a string literal
        assert '"body"' in sql or "body" in sql

    def test_digest_hex_renders_encode_wrapper(self) -> None:
        """DigestHex('body', 'sha256') -> ENCODE(DIGEST(...), 'hex')."""
        sql = _DigestHexFunction(Field("body"), "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        upper = sql.upper()
        assert "ENCODE(" in upper
        assert "DIGEST(" in upper
        assert "'hex'" in sql
        assert "'sha256'" in sql

    def test_hmac_renders_all_three_args(self) -> None:
        """HMACDigest('body', 'key', 'sha256') -> HMAC(..., 'key', 'sha256')."""
        sql = _HMACFunction(Field("body"), "my-secret", "sha256").get_sql(
            DEFAULT_SQL_CONTEXT
        )
        upper = sql.upper()
        assert "HMAC(" in upper
        assert "'my-secret'" in sql
        assert "'sha256'" in sql

    def test_algorithm_and_key_are_string_literals(self) -> None:
        """Algorithm must render as a quoted string literal, never a bare column ref."""
        # Field("body") is the data column; "md5" is the algorithm.
        sql = _DigestFunction(Field("body"), "md5").get_sql(DEFAULT_SQL_CONTEXT)
        # 'md5' rendered as a single-quoted string literal ...
        assert "'md5'" in sql
        # ... and the column name "body" must NOT appear as a string literal too.
        assert "'body'" not in sql

    def test_data_field_is_column_reference(self) -> None:
        """The data argument must be a column reference, not a string literal."""
        sql = _DigestFunction(Field("my_column"), "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        # data column appears as a bare identifier ...
        assert "my_column" in sql
        assert "'my_column'" not in sql
        # ... while the algorithm stays a string literal.
        assert "'sha256'" in sql
        assert "sha256" in sql

    def test_hmac_key_and_algo_are_string_literals(self) -> None:
        """Both key and algorithm in HMAC are SQL string literals; data is a column ref."""
        sql = _HMACFunction(Field("data_col"), "secret", "sha512").get_sql(
            DEFAULT_SQL_CONTEXT
        )
        assert "'secret'" in sql
        assert "'sha512'" in sql
        # data column is a bare identifier, not a literal
        assert "'data_col'" not in sql
        assert "data_col" in sql

    def test_hmac_string_key_escapes_embedded_quote(self) -> None:
        """A ``str`` key is a SQL literal: pypika doubles an embedded quote."""
        sql = _HMACFunction(Field("body"), "a'b", "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        assert "'a''b'" in sql

    def test_hmac_key_expression_renders_verbatim(self) -> None:
        """A term key is not quoted — the secret can be resolved server-side."""
        key = RawSQL("current_setting('app.encryption_key')")
        sql = _HMACFunction(Field("body"), key, "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        assert "current_setting('app.encryption_key')" in sql
        assert "'current_setting" not in sql

    def test_outer_function_classes_also_render(self) -> None:
        """Tortoise Function subclasses (Digest, etc.) produce valid SQL when
        directly instantiated and resolved via pypika.

        This tests the full Function->database_func chain.
        """
        # Digest wraps _DigestFunction — build and render directly
        digest = Digest("body", "sha256")
        # Access the underlying pypika function via the database_func mechanism
        func = digest._get_function_field(Field("body"), "sha256")
        sql = func.get_sql(DEFAULT_SQL_CONTEXT)
        assert "DIGEST(" in sql.upper()
        assert "'sha256'" in sql


    def test_pgp_sym_encrypt_renders_function_and_arguments(self) -> None:
        """PGP_SYM_ENCRYPT(<data>, 'key') with the data as a column reference."""
        sql = _PGPSymEncryptFunction(Field("body"), "passphrase", None).get_sql(
            DEFAULT_SQL_CONTEXT
        )
        assert "PGP_SYM_ENCRYPT(" in sql.upper()
        assert "'passphrase'" in sql
        assert "'body'" not in sql
        assert "body" in sql

    def test_pgp_sym_encrypt_omits_options_when_none(self) -> None:
        """No third argument unless options are given, so pgcrypto applies its defaults."""
        with_none = _PGPSymEncryptFunction(Field("body"), "k", None).get_sql(
            DEFAULT_SQL_CONTEXT
        )
        with_opts = _PGPSymEncryptFunction(
            Field("body"), "k", "cipher-algo=aes256"
        ).get_sql(DEFAULT_SQL_CONTEXT)
        assert with_none.count(",") == 1
        assert "'cipher-algo=aes256'" in with_opts
        assert with_opts.count(",") == 2

    def test_pgp_sym_decrypt_renders_function(self) -> None:
        """PGP_SYM_DECRYPT(<data>, 'key')."""
        sql = _PGPSymDecryptFunction(Field("cipher"), "passphrase", None).get_sql(
            DEFAULT_SQL_CONTEXT
        )
        assert "PGP_SYM_DECRYPT(" in sql.upper()
        assert "'passphrase'" in sql

    def test_pgp_sym_key_expression_renders_verbatim(self) -> None:
        """A term key is never quoted — the passphrase can stay out of the statement."""
        key = RawSQL("current_setting('app.encryption_key')")
        sql = _PGPSymEncryptFunction(Field("body"), key, None).get_sql(
            DEFAULT_SQL_CONTEXT
        )
        assert "current_setting('app.encryption_key')" in sql
        assert "'current_setting" not in sql

    def test_pgp_sym_string_key_escapes_embedded_quote(self) -> None:
        """A ``str`` passphrase is a SQL literal: pypika doubles an embedded quote."""
        sql = _PGPSymEncryptFunction(Field("body"), "a'b", None).get_sql(
            DEFAULT_SQL_CONTEXT
        )
        assert "'a''b'" in sql

    def test_pgp_sym_outer_classes_render_through_function_chain(self) -> None:
        """The public Function subclasses resolve to their pypika terms."""
        enc = PGPSymEncrypt("body", "passphrase")
        dec = PGPSymDecrypt("cipher", "passphrase", "cipher-algo=aes256")
        enc_sql = enc._get_function_field("body", "passphrase", None).get_sql(
            DEFAULT_SQL_CONTEXT
        )
        dec_sql = dec._get_function_field(
            "cipher", "passphrase", "cipher-algo=aes256"
        ).get_sql(DEFAULT_SQL_CONTEXT)
        assert "PGP_SYM_ENCRYPT(" in enc_sql.upper()
        assert "PGP_SYM_DECRYPT(" in dec_sql.upper()


# =========================================================================
# Section 1b — Algorithm allowlist (SEC-M03)
# =========================================================================


class TestPgcryptoAlgorithmAllowlist:
    """SEC-M03: only built-in pgcrypto algorithms are accepted."""

    def test_allows_all_six_algorithms(self) -> None:
        for algo in ("md5", "sha1", "sha256", "sha224", "sha384", "sha512"):
            assert Digest("body", algo) is not None

    def test_digest_rejects_unknown_algorithm(self) -> None:
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            Digest("body", "nope")

    def test_digest_hex_rejects_unknown_algorithm(self) -> None:
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            DigestHex("body", "nope")

    def test_hmac_digest_rejects_unknown_algorithm(self) -> None:
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            HMACDigest("body", "key", "nope")

    def test_algorithm_allowlist_rejects_sql_shaped_payload(self) -> None:
        """A quote in the *algorithm* never reaches SQL — it fails the allowlist.

        This is the allowlist control, not the escaping control (see
        ``test_hmac_string_key_escapes_embedded_quote`` and
        ``test_sql_shaped_key_cannot_break_out`` for that one).
        """
        payload = "sha256'; DROP TABLE foo; --"
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            Digest("body", payload)


# =========================================================================
# Section 2 — Live PostgreSQL integration
# =========================================================================

# ---------------------------------------------------------------------------
# Gating — skip only the live-PG integration section when PG is unavailable
# ---------------------------------------------------------------------------

_DB_URL = database_url()


# ---------------------------------------------------------------------------
# Test models — defined fresh for integration tests
# ---------------------------------------------------------------------------


class SimpleDocument(Model):
    """Minimal document model for testing hash/digest functions on PostgreSQL."""

    id = fields.IntField(primary_key=True)
    plain_text = fields.TextField()

    class Meta:
        table = "test_simple_documents"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


_PG_SKIP_REASON = pg_skip_reason()


class TestPgcryptoIntegration:
    """Live PostgreSQL tests for pgcrypto hash/digest functions.

    Skipped (entire class) when PostgreSQL is not available on
    ``localhost:5434``, so the pure unit tests still run on machines without
    PostgreSQL.

    The ``_init_pg_db`` fixture is ``function``-scoped so every test starts
    from an empty ``test_simple_documents`` table: it creates the schema and
    drops it again on teardown.
    """

    pytestmark = pytest.mark.skipif(
        _PG_SKIP_REASON is not None,
        reason=_PG_SKIP_REASON or "",
    )

    @pytest.fixture(autouse=True)
    async def _init_pg_db(self):
        """Re-initialize Tortoise ORM against live PostgreSQL per test."""
        await Tortoise.init(
            db_url=_DB_URL,
            modules={"models": ["tests.test_pgcrypto"]},
        )
        await Tortoise.generate_schemas()
        yield
        conn = Tortoise.get_connection("default")
        await conn.execute_query("DROP TABLE IF EXISTS test_simple_documents CASCADE")
        await Tortoise.close_connections()

    async def test_digest_hex_roundtrip(self) -> None:
        """DigestHex produces a 64-char hex string matching hashlib.sha256."""
        plaintext = "hello, pgcrypto!"
        expected_hex = hashlib.sha256(plaintext.encode()).hexdigest()
        assert len(expected_hex) == 64

        doc = await SimpleDocument.create(plain_text=plaintext)
        result = await SimpleDocument.annotate(h=DigestHex("plain_text", "sha256")).get(
            id=doc.id
        )

        assert result.h is not None
        assert len(str(result.h)) == 64
        assert str(result.h) == expected_hex

    async def test_digest_raw_binary(self) -> None:
        """Digest returns a bytea value (raw binary hash)."""
        plaintext = "binary-test"
        doc = await SimpleDocument.create(plain_text=plaintext)
        result = await SimpleDocument.annotate(raw=Digest("plain_text", "sha256")).get(
            id=doc.id
        )

        # Raw digest is bytes-like; sha256 produces 32 bytes
        assert result.raw is not None
        raw_bytes = (
            bytes(result.raw) if not isinstance(result.raw, bytes) else result.raw
        )
        assert len(raw_bytes) == 32

    async def test_hmac_digest(self) -> None:
        """HMACDigest produces a bytea value (keyed hash)."""
        plaintext = "hmac-test"
        key = "shared-secret"
        doc = await SimpleDocument.create(plain_text=plaintext)
        result = await SimpleDocument.annotate(
            mac=HMACDigest("plain_text", key, "sha256")
        ).get(id=doc.id)

        assert result.mac is not None
        mac_bytes = (
            bytes(result.mac) if not isinstance(result.mac, bytes) else result.mac
        )
        assert len(mac_bytes) == 32  # SHA-256 HMAC is 32 bytes

    async def test_hmac_digest_with_expression_key(self) -> None:
        """A ``RawSQL`` key is resolved server-side and matches Python's HMAC.

        The GUC is set with ``SET LOCAL`` inside the transaction so the
        annotate query runs on the same connection and the key never appears
        in the statement text.
        """
        plaintext = "expression-key-test"
        key = "shared-secret"
        doc = await SimpleDocument.create(plain_text=plaintext)

        async with in_transaction() as conn:
            await conn.execute_query(
                "SELECT set_config('app.encryption_key', 'shared-secret', true)"
            )
            result = await SimpleDocument.annotate(
                mac=HMACDigest(
                    "plain_text",
                    RawSQL("current_setting('app.encryption_key')"),
                    "sha256",
                )
            ).get(id=doc.id)

        expected = hmac_mod.new(key.encode(), plaintext.encode(), "sha256").digest()
        assert bytes(result.mac) == expected

    async def test_hmac_string_key_with_quote_roundtrips(self) -> None:
        """A ``str`` key containing ``'`` is doubled and reaches PostgreSQL intact.

        The rendered literal must double the quote (level 1), and the value the
        database sees must be the original key — asserted against Python's
        ``hmac`` rather than against the SQL text alone.
        """
        plaintext = "quote-in-key"
        key = "a'b"
        sql = _HMACFunction(Field("plain_text"), key, "sha256").get_sql(
            DEFAULT_SQL_CONTEXT
        )
        assert "'a''b'" in sql

        doc = await SimpleDocument.create(plain_text=plaintext)
        result = await SimpleDocument.annotate(
            mac=HMACDigest("plain_text", key, "sha256")
        ).get(id=doc.id)

        expected = hmac_mod.new(key.encode(), plaintext.encode(), "sha256").digest()
        assert bytes(result.mac) == expected

    async def test_sql_shaped_key_cannot_break_out(self) -> None:
        """A key shaped like an injection payload is data, never SQL.

        The annotate runs, the table survives, and the MAC equals Python's for
        the literal payload — so the doubled quote is the only escape level
        that matters here.
        """
        plaintext = "injection-shaped-key"
        key = "x'); DROP TABLE test_simple_documents; --"
        doc = await SimpleDocument.create(plain_text=plaintext)

        result = await SimpleDocument.annotate(
            mac=HMACDigest("plain_text", key, "sha256")
        ).get(id=doc.id)

        expected = hmac_mod.new(key.encode(), plaintext.encode(), "sha256").digest()
        assert bytes(result.mac) == expected
        # The table is still there and still holds the row.
        assert await SimpleDocument.filter(id=doc.id).count() == 1

    async def test_pgp_sym_encrypt_decrypt_roundtrip(self) -> None:
        """pgp_sym_encrypt produces bytes; nesting decrypt returns the plaintext."""
        doc = await SimpleDocument.create(plain_text="top secret")
        encrypted = await SimpleDocument.annotate(
            cipher=PGPSymEncrypt("plain_text", "test-passphrase"),
        ).get(id=doc.id)
        assert isinstance(encrypted.cipher, bytes)
        assert encrypted.cipher != b"top secret"

        decrypted = await SimpleDocument.annotate(
            clear=PGPSymDecrypt(
                PGPSymEncrypt("plain_text", "test-passphrase"),
                "test-passphrase",
            ),
        ).get(id=doc.id)
        assert decrypted.clear == "top secret"

    async def test_pgp_sym_decrypt_wrong_key_raises(self) -> None:
        """A wrong passphrase is a database error, not silent garbage."""
        doc = await SimpleDocument.create(plain_text="top secret")
        with pytest.raises(Exception, match="Wrong key or corrupt data"):
            await SimpleDocument.annotate(
                clear=PGPSymDecrypt(
                    PGPSymEncrypt("plain_text", "right"),
                    "wrong",
                ),
            ).get(id=doc.id)
