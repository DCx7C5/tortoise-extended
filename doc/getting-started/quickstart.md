# Quickstart

This guide walks you through setting up tortoise-extended and performing your first vector and graph queries.

## 1. Start the Database

```bash
docker run -d --name graphrag-db -p 5432:5432 tortoise-extended-pg
```

Or connect to an existing PostgreSQL instance with pgvector + ltree + TimescaleDB enabled.

## 2. Initialize Tortoise ORM

```python
import asyncio
import tortoise_extended  # Must be first import — applies monkey-patches
from tortoise import Tortoise

# Define your models (see doc/architecture/schema.md for the GraphRAG schema)
from myapp.models import Entity, Relationship, TextUnit

async def main():
    await Tortoise.init(
        db_url="asyncpg://postgres:postgres@localhost:5432/graphrag",
        modules={"models": ["myapp.models"]},
    )
    await Tortoise.generate_schemas()

asyncio.run(main())
```

## 3. Insert Data

```python
async def insert_entity():
    entity = await Entity.create(
        title="Python",
        type="TECHNOLOGY",
        description="A high-level programming language",
        embedding=[0.1, 0.2, 0.3, ...],  # 1536-dimensional vector
    )
    return entity
```

## 4. Vector Search

```python
async def vector_search():
    query_embedding = [0.1, 0.2, 0.3, ...]

    # Cosine similarity
    entities = await Entity.filter(
        embedding__cosine_distance=([query_embedding, 0.5])
    ).order_by("embedding__cosine_distance").limit(10)

    # L2 distance
    entities = await Entity.filter(
        embedding__l2_distance=([query_embedding, 0.3])
    ).order_by("embedding__l2_distance").limit(10)

    return entities
```

## 5. Graph Traversal

```python
async def graph_traversal():
    entity = await Entity.get(title="Python")

    # Get outgoing relationships
    outgoing = await entity.outgoing.all()

    # Get incoming relationships
    incoming = await entity.incoming.all()

    # Traverse with filter
    tech_entities = await entity.outgoing.all().filter(
        target__type="TECHNOLOGY"
    )

    return outgoing, incoming
```

## 6. Graph Queries (Python API)

Neighborhood, path, and hybrid search are implemented in Python by the
library — no SQL functions are needed in the database:

```python
from tortoise_extended import GraphTraversal, HybridSearch, shortest_path

async def graph_queries():
    # Local neighborhood search (1-2 hops)
    traversal = GraphTraversal(Entity, Relationship)
    neighbors = await traversal.neighbors(
        node_id=entity.id,
        direction="both",
        max_depth=2,
    )

    # Shortest path between two entities
    path = await shortest_path(
        Entity, Relationship,
        from_id=entity.id,
        to_id=other_entity.id,
        max_hops=5,
    )

    # Hybrid search (vector + text)
    search = HybridSearch(
        model=Entity,
        vector_field="embedding",
        text_field="description",
    )
    results = await search.search(
        query_vector=[0.1, 0.2, ...],
        query_text="machine learning",
        max_results=20,
    )

    return neighbors, path, results
```

## 7. Recursive CTEs

```python
from pypika_tortoise import PostgreSQLQuery, Table, RawSQL
from tortoise_extended import RecursiveCTE

async def find_ancestors():
    entities = Table("entities")
    relationships = Table("relationships")

    cte = (
        RecursiveCTE("ancestors")
        .anchor(
            PostgreSQLQuery.from_(entities)
            .select(entities.id, entities.title, RawSQL("0").as_("depth"))
            .where(entities.id == 42)
        )
        .union(
            PostgreSQLQuery.from_(entities)
            .join(relationships).on(entities.id == relationships.source_entity_id)
            .select(entities.id, entities.title, (RawSQL("ancestors.depth") + 1).as_("depth"))
        )
        .build()
    )

    conn = connections.get("default")
    results = await conn.execute_query(str(cte), [])
    return results
```

## 8. GraphTraversal (CTE-based)

```python
from tortoise_extended import GraphTraversal

async def traverse():
    from myapp.models import Entity, Relationship

    traversal = GraphTraversal(Entity, Relationship)

    # Get all ancestors of a node
    ancestors = await traversal.ancestors(node_id=42, max_depth=5)

    # Get neighbors (outgoing only)
    neighbors = await traversal.neighbors(node_id=42, direction="outgoing")

    return ancestors, neighbors
```

## Next Steps

- Read the [Architecture Guide](../architecture/overview.md) for design details
- See the [API Reference](../api/index.md) for complete documentation
- Check [Performance Tuning](../guides/performance.md) for optimization tips
