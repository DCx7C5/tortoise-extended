# Migration Guide

## From Standard Tortoise ORM

### Step 1: Install Package

```bash
pip install tortoise-extended
```

### Step 2: Import Before Tortoise

```python
import tortoise_extended  # Must be first import
tortoise_extended.patch()  # Explicitly apply monkey-patches (idempotent)
from tortoise import Tortoise
```

### Step 3: Update Models

Replace standard fields with tortoise-extended fields:

```python
# Before
from tortoise import fields, models

class Chunk(models.Model):
    id = fields.UUIDField(pk=True)
    content = fields.TextField()
    embedding = fields.BinaryField(null=True)  # Manual vector handling

# After
from tortoise import fields, models
from tortoise_extended import VectorField, HNSWIndex

class Chunk(models.Model):
    id = fields.UUIDField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536, null=True)

    class Meta:
        table = "chunks"
        indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]
```

### Step 4: Update Queries

```python
# Before (manual vector handling)
import numpy as np

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

chunks = await Chunk.all()
similar = sorted(chunks, key=lambda c: cosine_similarity(query_vec, c.embedding))

# After (pgvector queries)
chunks = await Chunk.filter(
    embedding__cosine_distance=[[query_vec], 0.5]
).order_by("embedding__cosine_distance").limit(10)
```

### Step 5: Update Database URL

```python
# Before
db_url = "postgres://user:pass@localhost:5432/mydb"

# After (dev database from docker-compose.dev.yml)
db_url = "postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended"
```

## From tortoise-embeddings

### Step 1: Remove tortoise-embeddings

```bash
pip uninstall tortoise-embeddings
```

### Step 2: Update Imports

```python
# Before
from tortoise_embeddings import VectorField

# After
from tortoise_extended import VectorField
```

### Step 3: Remove Compatibility Code

```python
# Before (tortoise-embeddings required)
from tortoise_embeddings import VectorField
from tortoise_embeddings.fields import encode_vector, decode_vector

# After (self-contained)
from tortoise_extended import VectorField
# No need for encode_vector/decode_vector
```

## From Raw SQL

### Step 1: Replace Raw Queries

```python
# Before
sql = """
    WITH RECURSIVE ancestors AS (
        SELECT id, title, 0 AS depth
        FROM entities WHERE id = 42
        UNION
        SELECT e.id, e.title, a.depth + 1
        FROM entities e
        JOIN relationships r ON e.id = r.source_entity_id
        JOIN ancestors a ON r.target_entity_id = a.id
        WHERE a.depth < 3
    )
    SELECT * FROM ancestors;
"""
results = await connection.fetch(sql)

# After
from pypika_tortoise import PostgreSQLQuery, Table, RawSQL
from tortoise_extended import RecursiveCTE

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

results = await connections.get("default").execute_query(str(cte), [])
```

### Step 2: Use Graph Functions

```python
# Before
sql = """
    SELECT e.id, e.title, e.type, e.description, 0 AS depth
    FROM entities e
    WHERE e.title ILIKE $1
    
    UNION
    
    SELECT e.id, e.title, e.type, e.description, n.depth + 1
    FROM entities e
    JOIN relationships r ON e.id = r.target_entity_id
    JOIN neighborhood n ON r.source_entity_id = n.id
    WHERE n.depth < $2
"""
results = await connection.fetch(sql, ["%Python%", 2])

# After
from tortoise_extended import GraphTraversal

traversal = GraphTraversal(Entity, Relationship)
rows = await traversal.neighbors(
    node_id=python_entity.id,
    direction="both",
    max_depth=2,
)
```

## From Neo4j

### Step 1: Export Data

```python
# Export from Neo4j
from neo4j import GraphDatabase

driver = GraphDatabase.driver(uri, auth=(user, password))

with driver.session() as session:
    # Export nodes
    nodes = session.run("MATCH (n) RETURN n")

    # Export relationships
    rels = session.run("MATCH (a)-[r]->(b) RETURN a, r, b")
```

### Step 2: Import to PostgreSQL

```python
from myapp.models import Entity, Relationship

# Import nodes as entities
for node in nodes:
    await Entity.create(
        title=node["n"]["name"],
        type=node["n"]["label"],
        description=node["n"]["description"],
    )

# Import relationships
for rel in rels:
    source = await Entity.get(title=rel["a"]["name"])
    target = await Entity.get(title=rel["b"]["name"])
    await Relationship.create(
        source=source,
        target=target,
        type=rel["r"]["type"],
        weight=rel["r"].get("weight", 1.0),
    )
```

### Step 3: Replace Cypher Queries

```python
# Before (Cypher)
# MATCH (a)-[:USES]->(b) WHERE a.name = 'Python' RETURN b

# After (SQL)
result = await connections.get("default").execute_query("""
    SELECT b.id, b.title, b.type
    FROM entities a
    JOIN relationships r ON a.id = r.source_entity_id
    JOIN entities b ON r.target_entity_id = b.id
    WHERE a.title = $1 AND r.type = 'USES'
""", ["Python"])
```

## From Apache AGE

> **Note:** tortoise-extended deliberately does **not** integrate Apache AGE —
> the graph layer is built on plain PostgreSQL tables + recursive CTEs
> (see `doc/architecture/design-decisions.md`). There is nothing to uninstall;
> you simply keep using PostgreSQL and migrate your Cypher queries to the
> QuerySet / `GraphTraversal` API.

### Step 1: Export from AGE

```python
# Export AGE graph
sql = """
    SELECT * FROM cypher('graphrag', $$
        MATCH (n)
        RETURN n
    $$) AS (n agtype);
"""
results = await connection.fetch(sql)
```

### Step 2: Import to PostgreSQL

```python
# Same as Neo4j import above
```

### Step 3: Replace AGE Queries

```python
# Before (AGE Cypher)
sql = """
    SELECT * FROM cypher('graphrag', $$
        MATCH (a)-[:USES]->(b)
        WHERE a.name = 'Python'
        RETURN b
    $$) AS (b agtype);
"""

# After (PostgreSQL)
result = await connections.get("default").execute_query("""
    SELECT b.id, b.title, b.type
    FROM entities a
    JOIN relationships r ON a.id = r.source_entity_id
    JOIN entities b ON r.target_entity_id = b.id
    WHERE a.title = $1 AND r.type = 'USES'
""", ["Python"])
```

## Database Schema Migration

### Using Aerich

```bash
# Install aerich
pip install aerich

# Initialize
aerich init --db-url postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended

# Generate migration
aerich migrate --name add_vector_fields

# Apply migration
aerich upgrade
```

### Manual Migration

```sql
-- Add pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Add vector columns
ALTER TABLE entities ADD COLUMN embedding vector(1536);
ALTER TABLE text_units ADD COLUMN embedding vector(1536);

-- Add HNSW indexes
CREATE INDEX hnsw_entities_embedding ON entities
USING hnsw (embedding vector_cosine_ops)
WITH (m = 32, ef_construction = 400);

-- Add TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

## Testing

### Unit Tests

```python
import pytest
import tortoise_extended
from tortoise import Tortoise
from myapp.models import Entity

@pytest.fixture(autouse=True)
async def setup_db():
    await Tortoise.init(
        db_url="postgres://user:pass@localhost:5432/test",
        modules={"models": ["myapp.models"]},
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()

@pytest.mark.asyncio
async def test_entity_creation():
    entity = await Entity.create(
        title="Python",
        type="TECHNOLOGY",
        embedding=[0.1, 0.2, 0.3],
    )
    assert entity.id is not None
```

### Integration Tests

```python
import pytest
import tortoise_extended
from tortoise import Tortoise, connections
from myapp.models import Entity

@pytest.mark.asyncio
async def test_local_neighborhood():
    # Create test data
    entity = await Entity.create(
        title="Python",
        type="TECHNOLOGY",
        embedding=[0.1, 0.2, 0.3],
    )

    # Execute search via the library API
    from tortoise_extended import GraphTraversal

    traversal = GraphTraversal(Entity, Relationship)
    rows = await traversal.neighbors(
        node_id=entity.id,
        direction="both",
        max_depth=1,
    )

    assert isinstance(rows, list)
```

## Troubleshooting

### Import Order

```python
# Wrong
from tortoise import Tortoise
import tortoise_extended  # Too late!

# Right
import tortoise_extended  # First!
from tortoise import Tortoise
```

### Missing Extension

```sql
-- Error: type "vector" does not exist
CREATE EXTENSION IF NOT EXISTS vector;
```

### Port Conflict

```python
# Error: Connection refused on port 5433
# The dev database listens on 127.0.0.1:5433
db_url = "postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended"
```

### Dimension Mismatch

```python
# Error: vector dimension mismatch
# Ensure embedding dimensions match VectorField(dimensions=...)
await Entity.create(embedding=[0.1, 0.2])  # Wrong: expects 1536
```
