"""Tests for InetField, MacAddrField and MacAddr8Field.

The unit classes pin the conversion guards (what fails, and on which side of the
round-trip). The PostgreSQL classes pin the real column types (``inet`` /
``macaddr`` / ``macaddr8``), the round-trip through asyncpg, and the fact that
ordering an ``inet`` column is numeric — a text column could not produce the
same sequence.
"""

import ipaddress
from collections.abc import AsyncGenerator

import pytest
import tortoise_extended  # noqa: F401 — apply patches
from tortoise import Tortoise, fields, models
from tortoise.utils import get_schema_sql

from tortoise_extended.fields import InetField, IPv4Field, MacAddr8Field, MacAddrField
from tests._pg_env import database_url, pg_skip_reason

# ---------------------------------------------------------------------------
# Configuration — a database-backed module, skipped without the Docker PG
# ---------------------------------------------------------------------------

DB_URL = database_url()

HOST_TABLE = "inet_macaddr_hosts"
V4_TABLE = "inet_macaddr_v4_only"
HW_TABLE = "inet_macaddr_hw"
_TABLES = (HOST_TABLE, V4_TABLE, HW_TABLE)

MAC48 = "aa:bb:cc:dd:ee:ff"
MAC64 = "aa:bb:cc:dd:ee:ff:00:11"


_PG_SKIP_REASON = pg_skip_reason()
pytestmark = pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class InetHost(models.Model):
    """Dual-family host address column."""

    id = fields.IntField(primary_key=True)
    label = fields.CharField(max_length=64)
    ip = InetField()

    class Meta:
        table = HOST_TABLE


class V4OnlyHost(models.Model):
    """IPv4-only column, to pin the coexistence contract with InetField."""

    id = fields.IntField(primary_key=True)
    label = fields.CharField(max_length=64)
    ip = IPv4Field()

    class Meta:
        table = V4_TABLE


class HardwareAddress(models.Model):
    """Both MAC column widths on one row."""

    id = fields.IntField(primary_key=True)
    label = fields.CharField(max_length=64)
    hw = MacAddrField()
    hw8 = MacAddr8Field()

    class Meta:
        table = HW_TABLE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def _db() -> AsyncGenerator[None]:
    """Initialize Tortoise against the shared test database and own our tables."""
    await Tortoise.init(db_url=DB_URL, modules={"models": [__name__]})
    conn = Tortoise.get_connection("default")
    # DROP before CREATE: generate_schemas() is IF NOT EXISTS, so a stale table
    # left behind by an aborted run would be silently reused.
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture
async def _clean(_db: None) -> AsyncGenerator[None]:
    """Start every test from empty tables with fresh integer ids."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY")
    yield


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------


class TestFieldDefinitions:
    """The SQL types the fields emit, and their indexability."""

    def test_inet_sql_type(self) -> None:
        assert InetField.SQL_TYPE == "inet"
        assert IPv4Field.SQL_TYPE == "inet"

    def test_macaddr_sql_types(self) -> None:
        assert MacAddrField.SQL_TYPE == "macaddr"
        assert MacAddr8Field.SQL_TYPE == "macaddr8"

    def test_indexable(self) -> None:
        assert InetField.indexable is True
        assert MacAddrField.indexable is True
        assert MacAddr8Field.indexable is True


# ---------------------------------------------------------------------------
# InetField — conversion
# ---------------------------------------------------------------------------


class TestInetFieldConversion:
    """``to_python_value`` / ``to_db_value`` for both address families."""

    def test_to_python_value_v4_string(self) -> None:
        result = InetField().to_python_value("192.168.1.10")
        assert isinstance(result, ipaddress.IPv4Address)
        assert str(result) == "192.168.1.10"

    def test_to_python_value_v6_string_is_canonicalised(self) -> None:
        result = InetField().to_python_value("2001:DB8::1")
        assert isinstance(result, ipaddress.IPv6Address)
        assert str(result) == "2001:db8::1"

    def test_to_python_value_none(self) -> None:
        assert InetField().to_python_value(None) is None

    def test_to_db_value_returns_canonical_text(self) -> None:
        field = InetField()
        assert field.to_db_value("192.168.1.10", None) == "192.168.1.10"
        assert field.to_db_value("2001:DB8::1", None) == "2001:db8::1"
        assert field.to_db_value(ipaddress.IPv4Address("10.0.0.1"), None) == "10.0.0.1"
        assert field.to_db_value(None, None) is None


# ---------------------------------------------------------------------------
# InetField — rejection paths
# ---------------------------------------------------------------------------


class TestInetFieldRejection:
    """Host addresses only: interface strings and garbage fail loudly."""

    def test_to_db_value_rejects_interface_prefix(self) -> None:
        """An interface/prefix string never reaches the column."""
        with pytest.raises(ValueError, match=r"192\.168\.1\.0/24"):
            InetField().to_db_value("192.168.1.0/24", None)

    def test_to_db_value_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match="not-an-ip"):
            InetField().to_db_value("not-an-ip", None)

    def test_to_python_value_rejects_garbage(self) -> None:
        """A corrupt database value fails on the read path too."""
        with pytest.raises(ValueError, match="not-an-ip"):
            InetField().to_python_value("not-an-ip")


# ---------------------------------------------------------------------------
# MacAddrField / MacAddr8Field — normalisation and rejection (unit)
# ---------------------------------------------------------------------------


class TestMacAddrNormalisation:
    """Every accepted spelling collapses to lowercase colon form."""

    @pytest.mark.parametrize(
        "spelling",
        ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "AABB.CCDD.EEFF"],
    )
    def test_macaddr_to_db_value_spellings(self, spelling: str) -> None:
        assert MacAddrField().to_db_value(spelling, None) == MAC48

    @pytest.mark.parametrize(
        "spelling",
        ["AA:BB:CC:DD:EE:FF:00:11", "aa-bb-cc-dd-ee-ff-00-11", "AABB.CCDD.EEFF.0011"],
    )
    def test_macaddr8_to_db_value_spellings(self, spelling: str) -> None:
        assert MacAddr8Field().to_db_value(spelling, None) == MAC64

    def test_macaddr_to_python_value_normalises(self) -> None:
        """Read path normalises too (a driver handing back another spelling)."""
        assert MacAddrField().to_python_value("AA-BB-CC-DD-EE-FF") == MAC48
        # bytes input is decoded first
        assert MacAddrField().to_python_value(b"aabb.ccdd.eeff") == MAC48

    def test_none_passes_through(self) -> None:
        assert MacAddrField().to_python_value(None) is None
        assert MacAddrField().to_db_value(None, None) is None
        assert MacAddr8Field().to_python_value(None) is None


class TestMacAddrRejection:
    """Wrong width and bad hex raise ``ValueError`` naming the value."""

    def test_macaddr_rejects_seven_octets(self) -> None:
        value = "aa:bb:cc:dd:ee:ff:00"
        with pytest.raises(ValueError) as excinfo:
            MacAddrField().to_db_value(value, None)
        message = str(excinfo.value)
        assert value in message, message
        assert "invalid macaddr value" in message, message

    def test_macaddr8_rejects_six_octets(self) -> None:
        value = "aa:bb:cc:dd:ee:ff"
        with pytest.raises(ValueError) as excinfo:
            MacAddr8Field().to_db_value(value, None)
        message = str(excinfo.value)
        assert value in message, message
        assert "invalid macaddr8 value" in message, message

    def test_macaddr_rejects_bad_hex(self) -> None:
        value = "gg:bb:cc:dd:ee:ff"
        with pytest.raises(ValueError) as excinfo:
            MacAddrField().to_db_value(value, None)
        assert value in str(excinfo.value)

    def test_macaddr8_rejects_bad_hex(self) -> None:
        value = "aa:bb:cc:dd:ee:ff:00:gg"
        with pytest.raises(ValueError) as excinfo:
            MacAddr8Field().to_db_value(value, None)
        assert value in str(excinfo.value)

    def test_macaddr8_rejects_six_octet_cisco_form(self) -> None:
        value = "aabb.ccdd.eeff"
        with pytest.raises(ValueError) as excinfo:
            MacAddr8Field().to_db_value(value, None)
        assert value in str(excinfo.value)


# ---------------------------------------------------------------------------
# InetField — live PostgreSQL round-trip
# ---------------------------------------------------------------------------


class TestInetFieldPostgres:
    """Reads come back as address objects, never as strings or interfaces."""

    pytestmark = pytest.mark.usefixtures("_clean")

    async def test_v4_round_trip(self) -> None:
        host = await InetHost.create(label="v4", ip="192.168.1.10")
        assert isinstance(host.ip, ipaddress.IPv4Address)

        fetched = await InetHost.get(id=host.id)
        assert isinstance(fetched.ip, ipaddress.IPv4Address)
        assert not isinstance(fetched.ip, (str, ipaddress.IPv4Interface))
        assert str(fetched.ip) == "192.168.1.10"

    async def test_v6_round_trip(self) -> None:
        host = await InetHost.create(label="v6", ip="2001:DB8::1")
        assert isinstance(host.ip, ipaddress.IPv6Address)

        fetched = await InetHost.get(id=host.id)
        assert isinstance(fetched.ip, ipaddress.IPv6Address)
        assert not isinstance(fetched.ip, (str, ipaddress.IPv6Interface))
        assert str(fetched.ip) == "2001:db8::1"

    async def test_prefix_and_garbage_rejected_on_save(self) -> None:
        """``save()`` runs ``to_db_value``; a bad value never overwrites the row."""
        host = await InetHost.create(label="probe", ip="192.168.1.10")

        # setattr() skips to_python_value (model construction), so the
        # ValueError under test comes from the save path — to_db_value.
        setattr(host, "ip", "192.168.1.0/24")
        with pytest.raises(ValueError, match=r"192\.168\.1\.0/24"):
            await host.save()

        setattr(host, "ip", "not-an-ip")
        with pytest.raises(ValueError, match="not-an-ip"):
            await host.save()

        stored = await InetHost.get(id=host.id)
        assert str(stored.ip) == "192.168.1.10"

    async def test_v4_only_field_rejects_v6_while_inet_accepts_it(self) -> None:
        """Coexistence contract: InetField is dual-family, IPv4Field is not."""
        inet_host = await InetHost.create(label="dual", ip="2001:db8::1")
        assert isinstance(inet_host.ip, ipaddress.IPv6Address)

        with pytest.raises(ValueError):
            await V4OnlyHost.create(label="wrong-family", ip="2001:db8::1")

        v4_host = await V4OnlyHost.create(label="v4", ip="10.1.2.3")
        fetched = await V4OnlyHost.get(id=v4_host.id)
        assert isinstance(fetched.ip, ipaddress.IPv4Address)
        assert str(fetched.ip) == "10.1.2.3"

    async def test_short_prefix_is_refused_on_read(self) -> None:
        """A network interface on the read path raises, naming the prefix.

        Catches a field that silently accepts the driver's interface object and
        stores ``192.168.1.0`` as if the caller meant that host address: an ORM
        write cannot produce this value, so it is written with raw SQL.
        """
        host = await InetHost.create(label="short-prefix", ip="192.168.1.10")
        conn = Tortoise.get_connection("default")
        await conn.execute_query(
            f"UPDATE {HOST_TABLE} SET ip = '192.168.1.0/24'::inet WHERE id = {host.id}"
        )

        with pytest.raises(ValueError, match="24"):
            await InetHost.get(id=host.id)

    async def test_full_length_prefix_reads_back_as_address(self) -> None:
        """A ``/32`` is a host address: the guard must not reject it.

        Catches a guard that rejects every prefixed value instead of only a
        prefix shorter than the address length.
        """
        host = await InetHost.create(label="full-prefix", ip="192.168.1.10")
        conn = Tortoise.get_connection("default")
        await conn.execute_query(
            f"UPDATE {HOST_TABLE} SET ip = '192.168.1.10/32'::inet WHERE id = {host.id}"
        )

        fetched = await InetHost.get(id=host.id)
        assert isinstance(fetched.ip, ipaddress.IPv4Address)
        assert not isinstance(fetched.ip, ipaddress.IPv4Interface)
        assert fetched.ip == ipaddress.IPv4Address("192.168.1.10")


# ---------------------------------------------------------------------------
# MacAddrField / MacAddr8Field — live PostgreSQL round-trip
# ---------------------------------------------------------------------------


class TestMacAddrPostgres:
    """Stored values read back in lowercase colon form for every spelling."""

    pytestmark = pytest.mark.usefixtures("_clean")

    @pytest.mark.parametrize(
        "spelling",
        ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "AABB.CCDD.EEFF"],
    )
    async def test_macaddr_round_trip(self, spelling: str) -> None:
        row = await HardwareAddress.create(label=spelling, hw=spelling, hw8=MAC64)
        fetched = await HardwareAddress.get(id=row.id)
        assert fetched.hw == MAC48

    @pytest.mark.parametrize(
        "spelling",
        ["AA:BB:CC:DD:EE:FF:00:11", "aa-bb-cc-dd-ee-ff-00-11", "AABB.CCDD.EEFF.0011"],
    )
    async def test_macaddr8_round_trip(self, spelling: str) -> None:
        row = await HardwareAddress.create(label=spelling, hw=MAC48, hw8=spelling)
        fetched = await HardwareAddress.get(id=row.id)
        assert fetched.hw8 == MAC64

    async def test_orm_rejects_invalid_mac_on_create(self) -> None:
        """A wrong-width MAC raises through ``create()`` naming the value."""
        value = "aa:bb:cc:dd:ee:ff:00"
        with pytest.raises(ValueError) as excinfo:
            await HardwareAddress.create(label="bad", hw=value, hw8=MAC64)
        assert value in str(excinfo.value)


# ---------------------------------------------------------------------------
# Column types and address ordering
# ---------------------------------------------------------------------------


class TestColumnTypesAndOrdering:
    """The emitted DDL and the live catalog agree; ``inet`` sorts by address."""

    pytestmark = pytest.mark.usefixtures("_clean")

    def test_generated_ddl_uses_postgres_types(self) -> None:
        ddl = get_schema_sql(Tortoise.get_connection("default"), safe=True)
        assert f'"{HOST_TABLE}"' in ddl
        assert f'"{V4_TABLE}"' in ddl
        assert f'"{HW_TABLE}"' in ddl
        assert '"ip" inet' in ddl
        assert '"hw" macaddr' in ddl
        assert '"hw8" macaddr8' in ddl

    async def test_information_schema_reports_postgres_types(self) -> None:
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            "SELECT table_name, column_name, udt_name "
            "FROM information_schema.columns "
            f"WHERE table_name IN ('{HOST_TABLE}', '{V4_TABLE}', '{HW_TABLE}')"
        )
        actual = {
            (record["table_name"], record["column_name"]): record["udt_name"]
            for record in result[1]
        }
        assert actual[(HOST_TABLE, "ip")] == "inet"
        assert actual[(V4_TABLE, "ip")] == "inet"
        assert actual[(HW_TABLE, "hw")] == "macaddr"
        assert actual[(HW_TABLE, "hw8")] == "macaddr8"

    async def test_inet_order_by_is_address_order(self) -> None:
        """Two hosts where text order and address order disagree.

        As text: ``"192.168.1.10" < "192.168.1.9"``. As inet: ``.9 < .10``.
        """
        for label, ip in (
            ("ten", "10.0.0.1"),
            ("nine", "192.168.1.9"),
            ("dot-ten", "192.168.1.10"),
        ):
            await InetHost.create(label=label, ip=ip)

        ordered = [str(host.ip) for host in await InetHost.all().order_by("ip")]
        assert ordered == ["10.0.0.1", "192.168.1.9", "192.168.1.10"]

    async def test_inet_filter_matches_by_address_not_text(self) -> None:
        """Equality is address equality: a different spelling finds the row."""
        await InetHost.create(label="v4", ip="192.168.1.10")
        await InetHost.create(label="v6", ip="2001:db8::1")

        v4_hits = await InetHost.filter(ip="192.168.1.10")
        assert [host.label for host in v4_hits] == ["v4"]

        # Uppercase IPv6 text is the same address to PostgreSQL.
        v6_hits = await InetHost.filter(ip="2001:DB8::1")
        assert [host.label for host in v6_hits] == ["v6"]

        assert await InetHost.filter(ip="192.168.1.11").count() == 0
