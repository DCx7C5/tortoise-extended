"""Tests for :mod:`tests._pg_env` — DSN resolution, parsing and the live probe."""

from __future__ import annotations

import pytest

from tests._pg_env import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_URL,
    database_url,
    pg_available,
    pg_host_port,
    pg_skip_reason,
)

CLOSED_URL = "postgres://postgres:postgres@127.0.0.1:1/tortoise_test"


# --- DSN resolution -------------------------------------------------------


class TestDatabaseUrl:
    """``database_url`` reads the environment at call time."""

    def test_honours_tortoise_test_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TORTOISE_TEST_DB", "postgres://u:p@db.example:6000/x")
        assert database_url() == "postgres://u:p@db.example:6000/x"

    def test_falls_back_to_dev_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TORTOISE_TEST_DB", raising=False)
        assert database_url() == DEFAULT_URL


# --- host/port parsing ----------------------------------------------------


class TestPgHostPort:
    """``pg_host_port`` parses the DSN instead of assuming the dev container."""

    def test_parses_non_default_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TORTOISE_TEST_DB", "postgres://u:p@db.example:6000/x")
        assert pg_host_port() == ("db.example", 6000)

    def test_falls_back_when_dsn_has_no_host_or_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TORTOISE_TEST_DB", "postgres:///tortoise_test")
        assert pg_host_port() == (DEFAULT_HOST, DEFAULT_PORT)


# --- reachability ---------------------------------------------------------


class TestPgAvailable:
    """``pg_available`` probes the parsed target."""

    def test_closed_port_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TORTOISE_TEST_DB", CLOSED_URL)
        assert pg_available() is False


# --- skip reason ----------------------------------------------------------


class TestPgSkipReason:
    """``pg_skip_reason`` names the host:port it tried, or nothing on success."""

    def test_names_the_tried_host_and_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TORTOISE_TEST_DB", CLOSED_URL)
        reason = pg_skip_reason()
        assert reason is not None
        assert "127.0.0.1:1" in reason

    @pytest.mark.skipif(
        pg_skip_reason() is not None,
        reason=f"PostgreSQL not available ({pg_skip_reason()})",
    )
    def test_none_against_live_stack(self) -> None:
        assert pg_skip_reason() is None
