# Project ↔ File Tree Wiring

How to connect a `ProjectModel` entity to its `ProjectFileTree` using the
ltree-based `BaseBaseHierarchyModel` base class.

> **Why not `BaseBaseGraphNodeModelModel`?** A file tree is a *tree* — it never forks and
> rejoins. `BaseBaseHierarchyModel` gives you materialized ltree paths backed by a
> GiST index (`path__ancestor_of` / `path__descendant_of`), `move_to()`,
> and `validate_hierarchy()`. Use `BaseBaseGraphNodeModelModel`/`BaseGraphEdgeModel` only when files
> must participate in arbitrary, cyclic, or cross-project links.

## Models

```python
import tortoise_extended  # noqa: F401 — patches must apply first
from tortoise import fields, models
from tortoise_extended.models.hierarchy_model import BaseHierarchyModel
from tortoise_extended.indexes.ltree_index import GiSTIndex


class ProjectModel(models.Model):
    id = fields.UUIDField(pk=True)
    slug = fields.CharField(max_length=100, unique=True)

    class Meta:
        table = "projects"


class ProjectFileTree(BaseHierarchyModel):
    """One ltree tree per project."""

    project = fields.ForeignKeyField(
        "models.ProjectModel",
        related_name="file_tree",
        on_delete=fields.CASCADE,
    )
    is_directory = fields.BooleanField(default=True)
    size_bytes = fields.BigIntField(null=True)

    class Meta:
        table = "project_files"
        # Tortoise does NOT inherit Meta.indexes from the abstract base —
        # redeclare them on every concrete subclass.
        indexes = (
            GiSTIndex(fields=("path",)),
            ("namespace", "depth"),
            ("parent_id", "depth"),
        )
```

The `namespace` column (inherited from `BaseBaseHierarchyModel`) mirrors
`project.id` so every tree query stays partition-safe **without a join** —
all inherited helpers (`get_ancestors`, `get_descendants`, `get_root`)
filter on it automatically.

## Wiring

```python
# On project creation — root node (single ltree label)
root = await ProjectFileTree.create(
    project=project,
    name=project.slug,
    path=project.slug,
    parent_id=None,
    depth=0,
    namespace=str(project.pk),
)

# Add a file / directory
parent = await ProjectFileTree.get(pk=parent_id)
child = await ProjectFileTree.create(
    project=project,
    name="main.py",
    path=f"{parent.path_str}.main.py",
    parent_id=parent.pk,
    depth=parent.depth + 1,
    namespace=str(project.pk),
)
```

> `name` must equal the last `path` component — `validate_hierarchy()`
> enforces this before you commit your write logic.

## Queries

```python
# Whole tree of a project (two equivalent forms)
await ProjectFileTree.filter(project=project, path__descendant_of=root.path_str)
await root.get_descendants(include_self=True)

# Path from a file to the project root
await node.get_ancestors()
await node.get_path_to_root()

# Move a directory — validates cycles, cascades path+depth to descendants
async with in_transaction():
    await directory.move_to(new_parent)

# Integrity check before writes are released
errors = await node.validate_hierarchy()
```

## Zero-coupling alternative

Drop the FK and use only `namespace=str(project.id)`. `ProjectFileTree`
stays fully generic (no `project` import), you lose only the
`project.file_tree` reverse accessor. Everything else — including
multi-project lookup via `ProjectFileTree.filter(namespace__in=ids)` —
works identically.

## Requirements

- PostgreSQL + `CREATE EXTENSION IF NOT EXISTS ltree;` (already in
  `docker/postgres-ext/scripts/00-extensions.sql`).
- `import tortoise_extended` before `Tortoise.init()`.
