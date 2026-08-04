# LTreeField

PostgreSQL ltree column for hierarchical data with materialized paths.

## Import

```python
from tortoise_extended import LTreeField
```

## Constructor

```python
LTreeField(
    max_length: int = 256,
    separator: str = ".",
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_length` | `int` | `256` | Max path length in characters |
| `separator` | `str` | `"."` | Path separator |
| `null` | `bool` | `False` | Allow NULL values |

## Python Type

```python
list[str] | None  # ["root", "parent", "child"]
```

## Usage

### Model Definition

```python
from tortoise import fields, models
from tortoise_extended import LTreeField

class Category(models.Model):
    name = fields.CharField(max_length=100)
    path = LTreeField(max_length=1024)

    class Meta:
        table = "categories"
```

### Query with ltree Operators

```python
# Descendants: paths that live under a given path (path <@ given path)
descendants = await Category.filter(
    path__descendant_of="electronics.laptops"
)
# => rows whose path is a descendant of electronics.laptops, e.g. electronics.laptops.macbook

# Ancestors: paths that contain a given path (path @> given path)
ancestors = await Category.filter(
    path__ancestor_of="electronics.laptops.macbook"
)
# => rows whose path is an ancestor of electronics.laptops.macbook, e.g. electronics

# Pattern match (path ~ lquery)
matches = await Category.filter(
    path__match="electronics.*.macbook"
)
```

## Filter Operators

| Filter | SQL | Description |
|--------|-----|-------------|
| `path__ancestor_of` | `path @> value` | Is ancestor of |
| `path__descendant_of` | `path <@ value` | Is descendant of |
| `path__match` | `path ~ value` | ltree pattern match |
| `path__ancestor_match` | `path ?@> value` | Has ancestor match |
| `path__descendant_match` | `path ?<@ value` | Has descendant match |

## Requirements

```sql
CREATE EXTENSION IF NOT EXISTS ltree;
```

## Notes

- Input accepts `list[str]` or `str` — stored as dot-separated string
- Path components must be ≤256 bytes each
- Use with `HierarchyModel` for tree operations (ancestors, descendants, move_to)
