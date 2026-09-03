"""Tests for quote_ident and quote_literal SQL quoting helpers.

Covers basic wrapping, embedded-quote escaping, empty strings, and
edge cases (quotes at boundaries, multiple embedded quotes, unicode).
"""

from tortoise_extended._quote import quote_ident, quote_literal


# --- TestQuoteIdent


class TestQuoteIdent:
    """Test quote_ident — double-quote PostgreSQL identifiers."""

    def test_basic_identifier(self) -> None:
        """Simple identifier should be wrapped in double quotes."""
        assert quote_ident("table") == '"table"'

    def test_schema_qualified(self) -> None:
        """Schema-qualified identifier passes through with dots preserved."""
        assert quote_ident("public.accounts") == '"public.accounts"'

    def test_embedded_double_quote(self) -> None:
        """An embedded double quote should be escaped by doubling."""
        assert quote_ident('weird"name') == '"weird""name"'

    def test_empty_string(self) -> None:
        """Empty string should produce two double-quote characters."""
        assert quote_ident("") == '""'

    def test_double_quote_at_start(self) -> None:
        """Double quote at the start of the name is escaped."""
        assert quote_ident('"start') == '"""start"'

    def test_double_quote_at_end(self) -> None:
        """Double quote at the end of the name is escaped."""
        assert quote_ident('end"') == '"end"""'

    def test_multiple_embedded_quotes(self) -> None:
        """Multiple embedded double quotes are each escaped."""
        assert quote_ident('a"b"c"d') == '"a""b""c""d"'

    def test_only_double_quotes(self) -> None:
        """A name consisting entirely of double-quote characters."""
        assert quote_ident('"') == '""""'

    def test_unicode_preserved(self) -> None:
        """Non-ASCII characters pass through verbatim."""
        assert quote_ident("tâche_ñ") == '"tâche_ñ"'


# --- TestQuoteLiteral


class TestQuoteLiteral:
    """Test quote_literal — single-quote PostgreSQL string literals."""

    def test_basic_literal(self) -> None:
        """Simple string should be wrapped in single quotes."""
        assert quote_literal("abc") == "'abc'"

    def test_embedded_single_quote(self) -> None:
        """An embedded single quote should be escaped by doubling."""
        assert quote_literal("O'Reilly") == "'O''Reilly'"

    def test_single_quote_at_start(self) -> None:
        """Single quote at the start of the value is escaped."""
        assert quote_literal("'start") == "'''start'"

    def test_single_quote_at_end(self) -> None:
        """Single quote at the end of the value is escaped."""
        assert quote_literal("end'") == "'end'''"

    def test_empty_string(self) -> None:
        """Empty string should produce two single-quote characters."""
        assert quote_literal("") == "''"

    def test_multiple_embedded_quotes(self) -> None:
        """Multiple embedded single quotes are each escaped."""
        assert quote_literal("a'b'c'd") == "'a''b''c''d'"

    def test_only_single_quotes(self) -> None:
        """A value consisting entirely of single-quote characters."""
        assert quote_literal("'") == "''''"

    def test_spaces_preserved(self) -> None:
        """Spaces in the value are preserved verbatim."""
        assert quote_literal("hello world") == "'hello world'"

    def test_dollar_sign_preserved(self) -> None:
        """Dollar signs are not escaped and pass through."""
        assert quote_literal("price=$10") == "'price=$10'"

    def test_percent_sign_preserved(self) -> None:
        """Percent signs pass through without escaping."""
        assert quote_literal("100%") == "'100%'"

    def test_backslash_preserved(self) -> None:
        """Backslashes are preserved (not escaped in standard PostgreSQL)."""
        assert quote_literal("path\\to\\file") == "'path\\to\\file'"
