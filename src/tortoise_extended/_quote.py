"""Shared PostgreSQL SQL quoting helpers.

Used by TimescaleDB managers and the custom migration operations to build
parameter-safe DDL.  Identifiers are double-quoted (``"schema"."table"``) and
literals are single-quoted with embedded quotes escaped — never interpolate
user-supplied table/column names into SQL without them.
"""


def quote_ident(name: str) -> str:
    """Double-quote a PostgreSQL identifier, escaping embedded double quotes."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Single-quote a PostgreSQL string literal, escaping embedded quotes."""
    return "'" + value.replace("'", "''") + "'"
