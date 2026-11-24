"""Unit tests for the cache-backed field layer (row 3229).

Pure unit coverage: no Redis socket, no database, no live backend. The cache
side is an injected recording fake (:class:`_RecordingBackend`); the single
namespace-prefix assertion goes through a real :class:`RedisCacheBackend` whose
pool is an in-memory stub, because a plain :class:`CacheBackend` never prefixes
and so cannot prove the prefix contract.

Coverage map (plan.md §J.5): codec round-trip and bounds; ``supports()`` truth
table; cache-key shape; the wrapped column contract; the N1 ``#:``-comment
convergence regression; construction refusals; helper behaviour and the
published failure matrix; partial instances; both write policies; MRO.
"""

import ast
import importlib
import inspect
from datetime import date, datetime, time
from decimal import Decimal
from enum import IntEnum, StrEnum
from pathlib import PurePath, PurePosixPath
from uuid import UUID

import pytest
import tortoise
from tortoise.fields import (
    BinaryField,
    CharEnumField,
    CharField as TortoiseCharField,
    DatetimeField,
    ForeignKeyField,
    IntEnumField,
    IntField,
    JSONField,
    TextField,
    TimeDeltaField,
    UUIDField,
)
from tortoise.migrations.writer import ImportManager, MigrationWriter
from tortoise.models import Model

from tortoise_extended import (
    CachedField,
    CachedFieldsMixin,
    RedisPrimaryField,
    cached_get,
    cached_invalidate,
    cached_set,
    prefetch_cached,
)
from tortoise_extended._types import CacheValue
from tortoise_extended.cache.base import CacheBackend
from tortoise_extended.cache.redis import RedisCache, RedisCacheBackend
from tortoise_extended.exceptions import (
    CacheBackendNotInitializedError,
    CacheSerializationError,
    RedisCacheError,
)
from tortoise_extended.fields._cache_codec import (
    MAX_BYTES_LEAF,
    MAX_DEPTH,
    MAX_ENCODED_BYTES,
    CacheCodec,
)
from tortoise_extended.fields._cached_base import _CachedFieldBase
from tortoise_extended.fields.char import CharField
from tortoise_extended.fields.citext import CitextField
from tortoise_extended.fields.inet import InetField
from tortoise_extended.fields.ipv4 import IPv4Field
from tortoise_extended.fields.ltree import LTreeField
from tortoise_extended.fields.macaddr import MacAddrField
from tortoise_extended.fields.path import PathField
from tortoise_extended.fields.pgcrypto import PGPEncryptedField
from tortoise_extended.fields.url import URLField
from tortoise_extended.fields.uuid import UUID4Field, UUID7Field
from tortoise_extended.fields.vector import VectorField
from tortoise_extended.models.cacheable_model import BaseCacheableModel
from tortoise_extended.models.versioned import VersionMixin

_CODEC = CacheCodec()
"""Stateless codec used by the assertion side of the round-trip tests."""


# ---------------------------------------------------------------------------
# Fixtures, fakes and model declarations
# ---------------------------------------------------------------------------


class _RecordingBackend(CacheBackend):
    """In-memory cache that records every call and can fail on demand.

    A plain :class:`CacheBackend` owns no namespace, so keys are recorded
    unprefixed — that is exactly the field layer's key contract.
    """

    def __init__(
        self,
        *,
        stored: dict[str, CacheValue] | None = None,
        fail_get: bool = False,
        fail_set: bool = False,
        fail_delete: bool = False,
        fail_get_many: bool = False,
        fail_set_many: bool = False,
    ) -> None:
        super().__init__(default_ttl=300)
        self.stored: dict[str, CacheValue] = dict(stored or {})
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.fail_delete = fail_delete
        self.fail_get_many = fail_get_many
        self.fail_set_many = fail_set_many
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, CacheValue, int | None]] = []
        self.delete_calls: list[str] = []
        self.get_many_calls: list[list[str]] = []
        self.set_many_calls: list[tuple[dict[str, CacheValue], int | None]] = []

    @staticmethod
    def _down() -> RedisCacheError:
        return RedisCacheError("fake redis failure")

    async def get(self, key: str) -> CacheValue | None:
        self.get_calls.append(key)
        if self.fail_get:
            raise self._down()
        return self.stored.get(key)

    async def set(self, key: str, value: CacheValue, ttl: int | None = None) -> None:
        self.set_calls.append((key, value, ttl))
        if self.fail_set:
            raise self._down()
        self.stored[key] = value

    async def delete(self, key: str) -> bool:
        self.delete_calls.append(key)
        if self.fail_delete:
            raise self._down()
        return self.stored.pop(key, None) is not None

    async def exists(self, key: str) -> bool:
        return key in self.stored

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.stored

    async def keys(self, pattern: str) -> list[str]:
        return list(self.stored)

    async def delete_pattern(self, pattern: str) -> int:
        return 0

    async def get_many(self, keys: list[str]) -> dict[str, CacheValue]:
        self.get_many_calls.append(list(keys))
        if self.fail_get_many:
            raise self._down()
        return {key: self.stored[key] for key in keys if key in self.stored}

    async def set_many(
        self, mapping: dict[str, CacheValue], ttl: int | None = None
    ) -> None:
        self.set_many_calls.append((dict(mapping), ttl))
        if self.fail_set_many:
            raise self._down()
        self.stored.update(mapping)


class _StubPool:
    """Minimal async stand-in for a redis-asyncio client pool."""

    def __init__(self) -> None:
        self.set_keys: list[str] = []
        self.data: dict[str, bytes] = {}

    async def set(self, key: str, value: bytes, ex: int | None = None) -> bool:
        self.set_keys.append(key)
        self.data[key] = value
        return True

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0

    async def exists(self, key: str) -> bool:
        return key in self.data

    async def expire(self, key: str, ttl: int) -> bool:
        return True


_PREFIX_POOL = _StubPool()
_PREFIX_BACKEND = RedisCacheBackend(pool=_PREFIX_POOL, namespace="cachefield")


class Item(Model):
    """Model carrying one cache-aside field in the ``shop`` app."""

    title = CachedField(CharField(max_length=64), ttl=60)

    class Meta:
        app = "shop"


class Profile(Model):
    """Model carrying one write-through field."""

    nickname = RedisPrimaryField(CharField(max_length=64), ttl=90)

    class Meta:
        app = "shop"


class ZeroTtlItem(Model):
    """Cache-aside field whose TTL is a real ``0`` (no expiry)."""

    title = CachedField(CharField(max_length=64), ttl=0)

    class Meta:
        app = "shop"


class CommentedItem(Model):
    """Model whose wrapped field carries a ``#:`` comment (the N1 regression)."""

    #: Cached title comment
    title = CachedField(CharField(max_length=64))

    class Meta:
        app = "shop"


def _app_item(app_label: str) -> type[Model]:
    """Declare a model named ``Item`` in *app_label* (two same-named models)."""

    class Item(Model):  # noqa: F811 — deliberately shadows the module-level Item
        title = CachedField(CharField(max_length=64))

        class Meta:
            app = app_label

    return Item


class _Colour(StrEnum):
    RED = "red"
    BLUE = "blue"


class _Rank(IntEnum):
    LOW = 1
    HIGH = 2


def _instantiate(cls: type) -> object:
    """Instantiate a field class, supplying ``max_length`` when it is accepted.

    Tortoise's ``CharField`` requires ``max_length``, so a factory that omits it
    raises ``TypeError`` at construction — hence the signature probe.
    """
    if "max_length" in inspect.signature(cls).parameters:
        return cls(max_length=5)
    return cls()


def _nest(value: object, levels: int) -> object:
    for _ in range(levels):
        value = [value]
    return value


def _rendered_signature(rendered: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Return the callee and sorted keyword/value pairs of a rendered call."""
    node = ast.parse(rendered, mode="eval").body
    assert isinstance(node, ast.Call)
    keywords = tuple(
        sorted((kw.arg or "", ast.unparse(kw.value)) for kw in node.keywords)
    )
    return ast.unparse(node.func), keywords


def _render_field(field: object) -> str:
    """Render *field* exactly as the migration writer renders it into a file."""
    writer = MigrationWriter("0001_round_trip", "models", [])
    return writer._render_field(field, ImportManager())  # noqa: SLF001 — the only field renderer


def _round_trip_field(field: object) -> object:
    """Serialize *field* the way a migration does and re-import the definition."""
    rendered = _render_field(field)
    path, _, name = field.deconstruct()[0].rpartition(".")  # type: ignore[attr-defined]
    module = importlib.import_module(path)
    namespace = {"fields": tortoise.fields, name: getattr(module, name)}
    return eval(rendered, dict(namespace))  # noqa: S307 — trusted writer output


# ---------------------------------------------------------------------------
# CacheCodec — round-trip, ordering, supports(), bounds, malformed input
# ---------------------------------------------------------------------------

_CODEC_VALUES = [
    pytest.param("text", id="str"),
    pytest.param(7, id="int"),
    pytest.param(-3, id="negative-int"),
    pytest.param(2.5, id="float"),
    pytest.param(True, id="true"),
    pytest.param(False, id="false"),
    pytest.param(None, id="none"),
    pytest.param(b"\x00\x01\xff", id="bytes"),
    pytest.param(datetime(2026, 11, 24, 9, 30, 15, 123456), id="datetime"),
    pytest.param(date(2026, 11, 24), id="date"),
    pytest.param(time(9, 30, 15), id="time"),
    pytest.param(Decimal("12.50"), id="decimal"),
    pytest.param(UUID("12345678-1234-5678-1234-567812345678"), id="uuid"),
    pytest.param(PurePosixPath("/tmp/x.txt"), id="path"),
]


class TestCodecRoundTrip:
    @pytest.mark.parametrize("value", _CODEC_VALUES)
    def test_supported_scalar_round_trips_with_its_type(self, value: object) -> None:
        decoded = _CODEC.decode(_CODEC.encode(value))
        assert decoded == value
        assert type(decoded) is type(value)

    def test_nested_structures_round_trip(self) -> None:
        value = {
            "numbers": [1, 2.5, {"deep": [True, None]}],
            "when": datetime(2026, 1, 2, 3, 4),
            "path": PurePosixPath("/a/b"),
        }
        decoded = _CODEC.decode(_CODEC.encode(value))
        assert decoded == value
        assert isinstance(decoded, dict)
        assert isinstance(decoded["numbers"], list)

    def test_dict_with_literal_tag_key_stays_a_dict(self) -> None:
        value = {"__t": "datetime", "v": "not-a-payload"}
        encoded = _CODEC.encode(value)
        assert encoded["__t"] == "dict"
        decoded = _CODEC.decode(encoded)
        assert isinstance(decoded, dict)
        assert decoded == value

    def test_plain_path_is_tagged(self) -> None:
        encoded = _CODEC.encode(PurePosixPath("/srv/data"))
        assert encoded == {"__t": "path", "v": "/srv/data"}
        assert isinstance(_CODEC.decode(encoded), PurePath)


class TestCodecOrderingTags:
    def test_bool_is_checked_before_int(self) -> None:
        encoded = _CODEC.encode(True)
        assert type(encoded) is bool
        assert encoded is True
        decoded = _CODEC.decode(True)
        assert type(decoded) is bool

    def test_datetime_keeps_its_own_tag(self) -> None:
        moment = datetime(2026, 11, 24, 9, 30)
        encoded = _CODEC.encode(moment)
        assert encoded == {"__t": "datetime", "v": "2026-11-24T09:30:00"}
        decoded = _CODEC.decode(encoded)
        assert type(decoded) is datetime
        assert decoded == moment

    def test_date_stays_date(self) -> None:
        encoded = _CODEC.encode(date(2026, 11, 24))
        assert encoded == {"__t": "date", "v": "2026-11-24"}
        assert type(_CODEC.decode(encoded)) is date


class TestCodecSupports:
    @pytest.mark.parametrize(
        ("name", "factory"),
        [
            ("tortoise_char", lambda: TortoiseCharField(max_length=5)),
            ("tortoise_text", lambda: TextField()),
            ("tortoise_int", lambda: IntField()),
            ("tortoise_datetime", lambda: DatetimeField()),
            ("tortoise_json", lambda: JSONField()),
            ("repo_char", lambda: _instantiate(CharField)),
            ("citext", lambda: _instantiate(CitextField)),
            ("path", lambda: _instantiate(PathField)),
            ("url", lambda: _instantiate(URLField)),
            ("macaddr", lambda: _instantiate(MacAddrField)),
            ("ltree", lambda: _instantiate(LTreeField)),
            ("uuid4", lambda: _instantiate(UUID4Field)),
            ("uuid7", lambda: _instantiate(UUID7Field)),
        ],
    )
    def test_supported_field_classes(self, name: str, factory: object) -> None:
        assert _CODEC.supports(factory()) is True, name

    @pytest.mark.parametrize(
        ("name", "factory"),
        [
            ("ipv4", lambda: IPv4Field()),
            ("inet", lambda: InetField()),
            ("timedelta", lambda: TimeDeltaField()),
            ("char_enum", lambda: CharEnumField(_Colour, max_length=8)),
            ("int_enum", lambda: IntEnumField(_Rank)),
            ("pgp_encrypted", lambda: PGPEncryptedField()),
            ("vector", lambda: VectorField(dimensions=3)),
            (
                "relational",
                lambda: ForeignKeyField("models.Other", related_name="supports_probe"),
            ),
        ],
    )
    def test_denied_field_classes(self, name: str, factory: object) -> None:
        assert _CODEC.supports(factory()) is False, name


class TestCodecBounds:
    def test_depth_at_the_cap_is_accepted(self) -> None:
        value = _nest("leaf", MAX_DEPTH)
        assert _CODEC.decode(_CODEC.encode(value)) == value

    def test_depth_past_the_cap_is_refused(self) -> None:
        with pytest.raises(CacheSerializationError, match="depth"):
            _CODEC.encode(_nest("leaf", MAX_DEPTH + 1))

    def test_single_bytes_leaf_over_the_cap_is_refused(self) -> None:
        with pytest.raises(CacheSerializationError, match="leaf cap"):
            _CODEC.encode(b"x" * (MAX_BYTES_LEAF + 1))

    def test_sub_cap_leaves_over_the_envelope_cap_are_refused(self) -> None:
        leaves = [b"x" * (7 * 1024 * 1024) for _ in range(2)]
        # Each leaf is under the leaf cap, so only the envelope check can fire.
        assert all(len(leaf) < MAX_BYTES_LEAF for leaf in leaves)
        with pytest.raises(CacheSerializationError, match="envelope"):
            _CODEC.encode(leaves)

    def test_caps_are_independently_sized(self) -> None:
        assert MAX_BYTES_LEAF < MAX_ENCODED_BYTES


class TestCodecMalformedDecode:
    @pytest.mark.parametrize(
        ("name", "payload"),
        [
            ("unknown-tag", {"__t": "bogus", "v": "x"}),
            ("non-str-tag", {"__t": 5, "v": "x"}),
            ("non-str-v", {"__t": "decimal", "v": 5}),
            ("bad-base64", {"__t": "bytes", "v": "not base64!"}),
            ("dict-payload-not-mapping", {"__t": "dict", "v": "x"}),
            ("trailing-key", {"__t": "date", "v": "2026-01-01", "extra": 1}),
        ],
    )
    def test_malformed_envelope_is_refused(
        self, name: str, payload: dict[str, object]
    ) -> None:
        with pytest.raises(CacheSerializationError):
            _CODEC.decode(payload)

    def test_raw_bytes_are_refused(self) -> None:
        with pytest.raises(CacheSerializationError, match="raw bytes"):
            _CODEC.decode(b"raw")


# ---------------------------------------------------------------------------
# Cache keys
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_unprefixed_key_shape(self) -> None:
        item = Item(id=42, title="hello")
        field = Item._meta.fields_map["title"]
        assert isinstance(field, _CachedFieldBase)
        assert field.cached_key(item) == "4:shop:4:Item:2:42:5:title"

    def test_two_same_named_models_in_different_apps_differ(self) -> None:
        shop_model = _app_item("shop")
        catalog_model = _app_item("catalog")
        shop_key = shop_model._meta.fields_map["title"].cached_key(shop_model(id=42))
        catalog_key = catalog_model._meta.fields_map["title"].cached_key(
            catalog_model(id=42)
        )
        assert shop_key == "4:shop:4:Item:2:42:5:title"
        assert catalog_key == "7:catalog:4:Item:2:42:5:title"
        assert shop_key != catalog_key

    def test_unsaved_instance_is_refused(self) -> None:
        field = Item._meta.fields_map["title"]
        with pytest.raises(ValueError, match="primary key"):
            field.cached_key(Item.construct(id=None, title="x"))

    async def test_namespace_prefix_comes_from_the_backend(self) -> None:
        item = Item(id=42, title="hello")
        field = Item._meta.fields_map["title"]
        key = field.cached_key(item)
        assert not key.startswith("cachefield:")
        await _PREFIX_BACKEND.set(key, {"probe": 1})
        assert _PREFIX_POOL.set_keys == ["cachefield:4:shop:4:Item:2:42:5:title"]


# ---------------------------------------------------------------------------
# Wrapped column contract
# ---------------------------------------------------------------------------


class TestColumnContract:
    def test_describe_reflects_nullability(self) -> None:
        wrapped = CachedField(CharField(max_length=8, null=True))
        assert wrapped.describe(True)["nullable"] is True

    def test_describe_reflects_not_null(self) -> None:
        wrapped = CachedField(CharField(max_length=8))
        assert wrapped.describe(True)["nullable"] is False

    def test_deconstruct_matches_the_inner_field(self) -> None:
        inner = TortoiseCharField(max_length=8)
        assert CachedField(inner).deconstruct() == inner.deconstruct()

    def test_migration_writer_round_trip_reimports(self) -> None:
        inner = TortoiseCharField(max_length=8)
        wrapped = CachedField(inner)
        # The wrapper must be invisible to migrations: the emitted call site is
        # the inner field's, not CachedField(...).
        assert _rendered_signature(_render_field(wrapped)) == _rendered_signature(
            _render_field(inner)
        )
        # Negative control: the comparison above must be able to fail.
        other = TortoiseCharField(max_length=9)
        assert _rendered_signature(_render_field(other)) != _rendered_signature(
            _render_field(inner)
        )
        # And the emitted code really is importable.
        rebuilt = _round_trip_field(wrapped)
        assert rebuilt.deconstruct() == inner.deconstruct()

    def test_constraints_and_validators_are_delegated(self) -> None:
        inner = CharField(max_length=8, null=True, unique=True)
        wrapped = CachedField(inner)
        assert wrapped.constraints == inner.constraints
        assert wrapped.validators == inner.validators

    @pytest.mark.parametrize(
        ("name", "inner", "sql_type"),
        [
            ("datetime", DatetimeField(), "TIMESTAMPTZ"),
            ("json", JSONField(), "JSONB"),
            ("uuid", UUIDField(), "UUID"),
            ("binary", BinaryField(), "BYTEA"),
        ],
    )
    def test_postgres_sql_type_is_the_inner_type(
        self, name: str, inner: object, sql_type: str
    ) -> None:
        assert CachedField(inner).get_for_dialect("postgres", "SQL_TYPE") == sql_type


# ---------------------------------------------------------------------------
# N1 — the ``#:`` comment must survive describe() and deconstruct() alike
# ---------------------------------------------------------------------------


class TestCommentConvergence:
    def test_description_agrees_between_describe_and_deconstruct(self) -> None:
        field = CommentedItem._meta.fields_map["title"]
        expected = "Cached title comment"
        assert field.describe(True)["description"] == expected
        assert field.deconstruct()[2].get("description") == expected

    def test_two_migration_generations_converge(self) -> None:
        field = CommentedItem._meta.fields_map["title"]
        first = _render_field(field)
        rebuilt = _round_trip_field(field)
        second = _render_field(rebuilt)
        assert _rendered_signature(second) == _rendered_signature(first)
        assert rebuilt.deconstruct() == field.deconstruct()


# ---------------------------------------------------------------------------
# Construction refusals
# ---------------------------------------------------------------------------


class TestConstructionRefusals:
    def test_relational_field_is_refused(self) -> None:
        inner = ForeignKeyField("models.Other", related_name="cache_refusal")
        with pytest.raises(ValueError, match="relational field"):
            CachedField(inner)

    def test_generated_field_is_refused(self) -> None:
        with pytest.raises(ValueError, match="DB-generated"):
            CachedField(IntField(generated=True))

    def test_primary_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="primary key"):
            CachedField(IntField(primary_key=True, generated=False))

    @pytest.mark.parametrize("kwargs", [{"auto_now": True}, {"auto_now_add": True}])
    def test_auto_managed_field_is_refused(self, kwargs: dict[str, bool]) -> None:
        with pytest.raises(ValueError, match="auto_now"):
            CachedField(DatetimeField(**kwargs))

    def test_field_class_instead_of_instance_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Field instance"):
            CachedField(CharField)

    def test_unsupported_inner_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not supported by the cache codec"):
            CachedField(TimeDeltaField())

    def test_unexpected_column_kwarg_is_a_type_error(self) -> None:
        with pytest.raises(TypeError):
            CachedField(CharField(max_length=8), null=True)

    def test_redis_primary_field_refuses_a_primary_key(self) -> None:
        with pytest.raises(ValueError, match="primary key"):
            RedisPrimaryField(IntField(primary_key=True, generated=False))


# ---------------------------------------------------------------------------
# Helpers against an injected fake backend
# ---------------------------------------------------------------------------


def _field(model: type[Model], name: str) -> _CachedFieldBase[CacheValue]:
    field = model._meta.fields_map[name]
    assert isinstance(field, _CachedFieldBase)
    return field


class TestCachedGet:
    async def test_miss_reads_attribute_and_writes_back(self) -> None:
        backend = _RecordingBackend()
        item = Item(id=42, title="hello")
        field = _field(Item, "title")
        value = await cached_get(item, "title", backend=backend)
        assert value == "hello"
        assert backend.get_calls == [field.cached_key(item)]
        assert len(backend.set_calls) == 1
        key, payload, ttl = backend.set_calls[0]
        assert key == field.cached_key(item)
        assert ttl == 60
        assert _CODEC.decode(payload) == "hello"

    async def test_hit_skips_the_attribute(self) -> None:
        item = Item(id=42, title="from-db")
        field = _field(Item, "title")
        backend = _RecordingBackend(
            stored={field.cached_key(item): _CODEC.encode("from-cache")}
        )
        value = await cached_get(item, "title", backend=backend)
        assert value == "from-cache"
        assert backend.set_calls == []

    async def test_zero_ttl_is_forwarded_as_zero(self) -> None:
        backend = _RecordingBackend()
        item = ZeroTtlItem(id=7, title="never")
        field = _field(ZeroTtlItem, "title")
        assert field.ttl == 0
        await cached_get(item, "title", backend=backend)
        assert backend.set_calls[0][2] == 0


class TestCachedSet:
    async def test_cache_aside_deletes_and_writes_nothing(self) -> None:
        backend = _RecordingBackend()
        item = Item(id=42, title="old")
        field = _field(Item, "title")
        await cached_set(item, "title", "new", backend=backend)
        assert item.title == "new"
        assert backend.delete_calls == [field.cached_key(item)]
        assert backend.set_calls == []

    async def test_get_after_set_returns_the_set_value(self) -> None:
        backend = _RecordingBackend()
        item = Item(id=42, title="old")
        await cached_set(item, "title", "new", backend=backend)
        assert await cached_get(item, "title", backend=backend) == "new"

    async def test_write_through_writes_one_key(self) -> None:
        backend = _RecordingBackend()
        profile = Profile(id=7, nickname="old")
        field = _field(Profile, "nickname")
        await cached_set(profile, "nickname", "Ada", backend=backend)
        assert profile.nickname == "Ada"
        assert backend.delete_calls == []
        assert len(backend.set_calls) == 1
        key, payload, ttl = backend.set_calls[0]
        assert key == field.cached_key(profile)
        assert ttl == 90
        assert _CODEC.decode(payload) == "Ada"

    async def test_invalidate_returns_the_backend_result(self) -> None:
        backend = _RecordingBackend()
        item = Item(id=42, title="hello")
        key = _field(Item, "title").cached_key(item)
        backend.stored[key] = _CODEC.encode("hello")
        assert await cached_invalidate(item, "title", backend=backend) is True
        assert backend.delete_calls == [key]
        assert await cached_invalidate(item, "title", backend=backend) is False


class TestPrefetchCached:
    async def test_one_get_many_and_one_set_many(self) -> None:
        backend = _RecordingBackend()
        items = [Item(id=1, title="a"), Item(id=2, title="b")]
        field = _field(Item, "title")
        await prefetch_cached(items, "title", backend=backend)
        assert len(backend.get_many_calls) == 1
        assert len(backend.set_many_calls) == 1
        mapping, ttl = backend.set_many_calls[0]
        assert ttl == 60
        assert set(mapping) == {field.cached_key(item) for item in items}
        assert [_CODEC.decode(mapping[field.cached_key(item)]) for item in items] == [
            "a",
            "b",
        ]

    async def test_empty_sequence_touches_nothing(self) -> None:
        backend = _RecordingBackend()
        await prefetch_cached([], "title", backend=backend)
        assert backend.get_many_calls == []
        assert backend.set_many_calls == []

    async def test_unsaved_instances_are_skipped(self) -> None:
        backend = _RecordingBackend()
        saved = Item(id=1, title="a")
        unsaved = Item.construct(id=None, title="draft")
        await prefetch_cached([saved, unsaved], "title", backend=backend)
        mapping, _ttl = backend.set_many_calls[0]
        assert set(mapping) == {_field(Item, "title").cached_key(saved)}

    async def test_valid_hits_are_left_alone(self) -> None:
        saved = Item(id=1, title="a")
        other = Item(id=2, title="b")
        field = _field(Item, "title")
        backend = _RecordingBackend(
            stored={field.cached_key(saved): _CODEC.encode("a")}
        )
        await prefetch_cached([saved, other], "title", backend=backend)
        mapping, _ttl = backend.set_many_calls[0]
        assert set(mapping) == {field.cached_key(other)}

    async def test_zero_ttl_is_forwarded_on_the_prefetch_path(self) -> None:
        backend = _RecordingBackend()
        item = ZeroTtlItem(id=1, title="a")
        assert _field(ZeroTtlItem, "title").ttl == 0
        await prefetch_cached([item], "title", backend=backend)
        assert backend.set_many_calls[0][1] == 0


# ---------------------------------------------------------------------------
# Failure matrix
# ---------------------------------------------------------------------------


class TestFailureMatrix:
    @pytest.fixture()
    def uninitialized_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RedisCache, "_pool", None)

    async def test_not_initialized_propagates_from_get(
        self, uninitialized_redis: None
    ) -> None:
        with pytest.raises(CacheBackendNotInitializedError):
            await cached_get(Item(id=1, title="x"), "title")

    async def test_not_initialized_propagates_from_set(
        self, uninitialized_redis: None
    ) -> None:
        with pytest.raises(CacheBackendNotInitializedError):
            await cached_set(Item(id=1, title="x"), "title", "y")

    async def test_not_initialized_propagates_from_invalidate_and_prefetch(
        self, uninitialized_redis: None
    ) -> None:
        with pytest.raises(CacheBackendNotInitializedError):
            await cached_invalidate(Item(id=1, title="x"), "title")
        with pytest.raises(CacheBackendNotInitializedError):
            await prefetch_cached([Item(id=1, title="x")], "title")

    async def test_redis_read_error_fails_open_in_get(self) -> None:
        backend = _RecordingBackend(fail_get=True)
        item = Item(id=1, title="db-value")
        assert await cached_get(item, "title", backend=backend) == "db-value"
        assert len(backend.set_calls) == 1

    async def test_redis_write_error_is_swallowed_in_get(self) -> None:
        backend = _RecordingBackend(fail_set=True)
        item = Item(id=1, title="db-value")
        assert await cached_get(item, "title", backend=backend) == "db-value"

    async def test_redis_error_propagates_from_cache_aside_set(self) -> None:
        backend = _RecordingBackend(fail_delete=True)
        with pytest.raises(RedisCacheError):
            await cached_set(Item(id=1, title="x"), "title", "y", backend=backend)

    async def test_redis_error_propagates_from_write_through_set(self) -> None:
        backend = _RecordingBackend(fail_set=True)
        with pytest.raises(RedisCacheError):
            await cached_set(
                Profile(id=1, nickname="x"), "nickname", "y", backend=backend
            )

    async def test_redis_error_propagates_from_invalidate(self) -> None:
        backend = _RecordingBackend(fail_delete=True)
        with pytest.raises(RedisCacheError):
            await cached_invalidate(Item(id=1, title="x"), "title", backend=backend)

    async def test_corrupt_payload_is_a_miss_and_is_evicted(self) -> None:
        item = Item(id=1, title="db")
        field = _field(Item, "title")
        key = field.cached_key(item)
        backend = _RecordingBackend(stored={key: {"__t": "bogus", "v": "x"}})
        assert await cached_get(item, "title", backend=backend) == "db"
        assert backend.delete_calls == [key]
        assert _CODEC.decode(backend.set_calls[0][1]) == "db"

    async def test_uncacheable_attribute_is_served_without_caching(self) -> None:
        backend = _RecordingBackend()
        item = Item(id=1)
        sentinel = object()
        item.title = sentinel
        assert await cached_get(item, "title", backend=backend) is sentinel
        assert backend.set_calls == []

    async def test_prefetch_read_failure_is_a_no_op(self) -> None:
        backend = _RecordingBackend(fail_get_many=True)
        await prefetch_cached([Item(id=1, title="a")], "title", backend=backend)
        assert backend.set_many_calls == []

    async def test_prefetch_write_failure_is_swallowed(self) -> None:
        backend = _RecordingBackend(fail_set_many=True)
        await prefetch_cached([Item(id=1, title="a")], "title", backend=backend)


# ---------------------------------------------------------------------------
# Partial instances (N2)
# ---------------------------------------------------------------------------


class TestPartialInstances:
    async def test_cached_get_raises_value_error_naming_the_field(self) -> None:
        partial = Item.construct(id=42)
        object.__setattr__(partial, "_partial", True)
        partial.__dict__.pop("title", None)
        assert not hasattr(partial, "title")
        with pytest.raises(ValueError, match=r"Item\.title") as excinfo:
            await cached_get(partial, "title", backend=_RecordingBackend())
        assert "not loaded" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Both field kinds and the MRO contract
# ---------------------------------------------------------------------------


class TestFieldKinds:
    def test_write_policies(self) -> None:
        assert CachedField._write_policy == "cache_aside"
        assert RedisPrimaryField._write_policy == "write_through"

    @pytest.mark.parametrize("field_cls", [CachedField, RedisPrimaryField])
    def test_no_read_priority_knob(self, field_cls: type) -> None:
        assert not hasattr(field_cls, "read_priority")
        assert not hasattr(field_cls(CharField(max_length=8)), "read_priority")

    async def test_both_kinds_share_the_read_helper(self) -> None:
        item_backend = _RecordingBackend()
        assert (
            await cached_get(Item(id=1, title="a"), "title", backend=item_backend)
            == "a"
        )
        profile_backend = _RecordingBackend()
        assert (
            await cached_get(
                Profile(id=1, nickname="b"), "nickname", backend=profile_backend
            )
            == "b"
        )
        assert len(item_backend.set_calls) == 1
        assert len(profile_backend.set_calls) == 1


class _VersionedCached(CachedFieldsMixin, VersionMixin, Model):
    class Meta:
        app = "shop"


class _CacheableCached(CachedFieldsMixin, BaseCacheableModel, Model):
    class Meta:
        app = "shop"


class TestMro:
    def test_version_mixin_combination_builds(self) -> None:
        mro = _VersionedCached.mro()
        assert issubclass(_VersionedCached, CachedFieldsMixin)
        assert mro.index(CachedFieldsMixin) < mro.index(VersionMixin)

    def test_cacheable_base_combination_builds(self) -> None:
        mro = _CacheableCached.mro()
        assert issubclass(_CacheableCached, CachedFieldsMixin)
        assert mro.index(CachedFieldsMixin) < mro.index(BaseCacheableModel)

    def test_save_and_delete_shapes_match_tortoise(self) -> None:
        def shape(fn: object) -> list[tuple[str, object, object]]:
            return [
                (param.name, param.kind, param.default)
                for param in inspect.signature(fn).parameters.values()
            ]

        assert shape(CachedFieldsMixin.save) == shape(Model.save)
        assert shape(CachedFieldsMixin.delete) == shape(Model.delete)
