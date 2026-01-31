# NOVA Database Migration: SQLite → TimescaleDB

**Purpose**: Migrate NOVA from SQLite to TimescaleDB for 100x scale (500 devices, 20 concurrent users)  
**Status**: 📋 Planning Document  
**Target Scale**: 50,000 events/sec write, 20+ concurrent streams, 1B+ events stored

---

## Executive Summary

### Current State (SQLite)
- **Capacity**: ~50 devices @ 10Hz, 5 concurrent users
- **Write throughput**: ~5,000 events/sec (single-writer limit)
- **Query performance**: 2-10ms (with correct indexes)
- **Scalability**: Excellent for current load, cannot support 100x scale

### Target State (TimescaleDB)
- **Capacity**: 500+ devices @ 10Hz, 20+ concurrent users
- **Write throughput**: 50,000+ events/sec (multi-writer, partitioned)
- **Query performance**: <10ms (with hypertable chunks and indexes)
- **Scalability**: Horizontal via read replicas, vertical via connection pooling

### Why TimescaleDB (Not Generic PostgreSQL)

1. **Time-series optimized**: Automatic partitioning by time (chunks)
2. **Compression**: 10-20x storage reduction for historical data
3. **Query optimization**: Time-bucket aggregations, continuous aggregates
4. **PostgreSQL foundation**: Full SQL support, ACID guarantees, mature ecosystem
5. **Production features**: Replication, high availability, backup/restore

---

## Architecture Impact Analysis

### What Changes

1. **database.py**: Complete async rewrite (SQLite sync → asyncpg async)
2. **Connection pooling**: asyncpg connection pool (10-50 connections)
3. **Schema**: TEXT timestamps → TIMESTAMPTZ, hypertable per lane
4. **Indexes**: Similar structure, adjusted for PostgreSQL optimizer
5. **Transactions**: Async context managers (`async with conn.transaction()`)

### What Stays the Same

✅ **Event model**: RawFrame, ParsedMessage, UiUpdate, CommandRequest, MetadataEvent unchanged  
✅ **Ordering contract**: Same deterministic rules, ORDER BY clauses  
✅ **Dedupe logic**: eventId SHA256 hash, atomic insert to eventIndex + lane table  
✅ **Query API**: Same function signatures (startTime, stopTime, scopeId, timebase)  
✅ **Streaming logic**: streaming.py unchanged (calls same database.py functions)  
✅ **Lane structure**: 5 lanes (Raw, Parsed, UI, Command, Metadata) preserved  

### API Compatibility

**Goal**: Zero changes to calling code (ingest.py, query.py, streaming.py)

**Strategy**: database.py functions remain async, same parameters, same return types

```python
# Before (SQLite):
async def queryRawEvents(scopeId, startTime, stopTime, timebase):
    # ... asyncio.to_thread(sqlite_query) ...
    return events

# After (TimescaleDB):
async def queryRawEvents(scopeId, startTime, stopTime, timebase):
    # ... await asyncpg_query ...
    return events
```

**Result**: streaming.py, query.py, ingest.py require ZERO modifications

---

## Schema Design

### Global Dedupe Table

**SQLite (Current)**:
```sql
CREATE TABLE eventIndex (
    eventId TEXT PRIMARY KEY
);
```

**TimescaleDB (Target)**:
```sql
CREATE TABLE eventIndex (
    eventId TEXT PRIMARY KEY,
    firstSeen TIMESTAMPTZ DEFAULT NOW()  -- For analytics
);

-- No hypertable (dedupe table doesn't need partitioning)
```

### Lane Tables (Example: Raw Lane)

**SQLite (Current)**:
```sql
CREATE TABLE rawEvents (
    eventId TEXT PRIMARY KEY,
    scopeId TEXT NOT NULL,
    sourceTruthTime TEXT NOT NULL,
    canonicalTruthTime TEXT NOT NULL,
    connectionId TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    bytes BLOB NOT NULL,
    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
);

CREATE INDEX idx_rawEvents_source_order 
    ON rawEvents (sourceTruthTime, connectionId, sequence, eventId);

CREATE INDEX idx_rawEvents_canonical_order 
    ON rawEvents (canonicalTruthTime, connectionId, sequence, eventId);
```

**TimescaleDB (Target)**:
```sql
CREATE TABLE rawEvents (
    eventId TEXT PRIMARY KEY,
    scopeId TEXT NOT NULL,
    sourceTruthTime TIMESTAMPTZ NOT NULL,    -- CHANGED: TEXT → TIMESTAMPTZ
    canonicalTruthTime TIMESTAMPTZ NOT NULL,  -- CHANGED: TEXT → TIMESTAMPTZ
    connectionId TEXT NOT NULL,
    sequence BIGINT NOT NULL,                 -- CHANGED: INTEGER → BIGINT
    bytes BYTEA NOT NULL,                     -- CHANGED: BLOB → BYTEA
    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
);

-- Convert to hypertable (automatic time-based partitioning)
SELECT create_hypertable('rawEvents', 'canonicalTruthTime', 
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes (TimescaleDB automatically adds time column to indexes)
CREATE INDEX idx_rawEvents_source_order 
    ON rawEvents (sourceTruthTime, connectionId, sequence, eventId);

CREATE INDEX idx_rawEvents_canonical_order 
    ON rawEvents (canonicalTruthTime, connectionId, sequence, eventId);
```

**Key Changes**:
1. `TEXT` timestamps → `TIMESTAMPTZ` (native timezone-aware timestamps)
2. `INTEGER` → `BIGINT` (sequence numbers can exceed 2B)
3. `BLOB` → `BYTEA` (PostgreSQL binary type)
4. Hypertable with 1-day chunks (automatic partitioning)

### Parsed Lane

**TimescaleDB Schema**:
```sql
CREATE TABLE parsedEvents (
    eventId TEXT PRIMARY KEY,
    scopeId TEXT NOT NULL,
    sourceTruthTime TIMESTAMPTZ NOT NULL,
    canonicalTruthTime TIMESTAMPTZ NOT NULL,
    streamId TEXT NOT NULL,
    streamType TEXT NOT NULL,
    schemaVersion TEXT NOT NULL,
    payload JSONB NOT NULL,  -- CHANGED: TEXT → JSONB (indexed JSON queries)
    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
);

SELECT create_hypertable('parsedEvents', 'canonicalTruthTime', 
    chunk_time_interval => INTERVAL '1 day'
);

CREATE INDEX idx_parsedEvents_source_order 
    ON parsedEvents (sourceTruthTime, eventId);

CREATE INDEX idx_parsedEvents_canonical_order 
    ON parsedEvents (canonicalTruthTime, eventId);

-- JSONB GIN index for payload queries (future)
CREATE INDEX idx_parsedEvents_payload_gin ON parsedEvents USING GIN (payload);
```

### UI, Command, Metadata Lanes

**Similar Pattern**:
- `TIMESTAMPTZ` for time fields
- `JSONB` for JSON payloads
- Hypertable with 1-day chunks
- Indexes matching ORDER BY clauses
- GIN indexes for JSONB fields (future query optimization)

---

## Code Changes

### 1. database.py - Async Rewrite

**Current Structure** (SQLite):
```python
import sqlite3
import asyncio
from contextlib import asynccontextmanager

class Database:
    def __init__(self, dbPath):
        self.dbPath = dbPath
        self.conn = None  # Sync connection
    
    def connect(self):
        self.conn = sqlite3.connect(self.dbPath)
    
    async def queryRawEvents(self, ...):
        # Wrap sync SQLite in asyncio.to_thread()
        def _query():
            cursor = self.conn.cursor()
            cursor.execute(sql, params)
            return cursor.fetchall()
        
        rows = await asyncio.to_thread(_query)
        return [self._rowToEvent(row) for row in rows]
```

**Target Structure** (TimescaleDB):
```python
import asyncpg
from contextlib import asynccontextmanager

class Database:
    def __init__(self, dbConfig):
        self.dbConfig = dbConfig  # {host, port, user, password, database}
        self.pool = None  # asyncpg connection pool
    
    async def connect(self):
        self.pool = await asyncpg.create_pool(
            host=self.dbConfig['host'],
            port=self.dbConfig['port'],
            user=self.dbConfig['user'],
            password=self.dbConfig['password'],
            database=self.dbConfig['database'],
            min_size=10,  # Minimum connections
            max_size=50,  # Maximum connections
            command_timeout=60
        )
    
    async def queryRawEvents(self, scopeId, startTime, stopTime, timebase, ...):
        # Direct async query (no thread wrapping)
        timeField = 'sourceTruthTime' if timebase == Timebase.SOURCE else 'canonicalTruthTime'
        orderByClause = ordering.buildOrderByClause(timebase, Lane.RAW)
        
        sql = f"""
            SELECT * FROM rawEvents
            WHERE scopeId = $1 AND {timeField} >= $2 AND {timeField} < $3
            {orderByClause}
        """
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, scopeId, startTime, stopTime)
        
        return [self._rowToEvent(row) for row in rows]
```

**Key Changes**:
1. `sqlite3.connect()` → `asyncpg.create_pool()` (connection pooling)
2. `asyncio.to_thread()` → `await conn.fetch()` (native async)
3. `?` placeholders → `$1, $2, $3` (PostgreSQL parameterization)
4. `TEXT` timestamps → `TIMESTAMPTZ` (automatic parsing)
5. `cursor.fetchall()` → `await conn.fetch()` (returns list of Record objects)

### 2. Timestamp Handling

**SQLite (Current)**:
```python
# Store as TEXT ISO8601:
sourceTruthTime = "2026-01-27T14:32:15.123456Z"

# Query with TEXT comparison:
sql = "SELECT * FROM rawEvents WHERE sourceTruthTime >= ? AND sourceTruthTime < ?"
params = (startTime, stopTime)  # Both TEXT
```

**TimescaleDB (Target)**:
```python
# Store as TIMESTAMPTZ:
sourceTruthTime = datetime.datetime(2026, 1, 27, 14, 32, 15, 123456, tzinfo=timezone.utc)

# Query with TIMESTAMPTZ:
sql = "SELECT * FROM rawEvents WHERE sourceTruthTime >= $1 AND sourceTruthTime < $2"
params = (startTime, stopTime)  # Both datetime objects

# asyncpg automatically converts:
# - Python datetime → PostgreSQL TIMESTAMPTZ (insert)
# - PostgreSQL TIMESTAMPTZ → Python datetime (select)
```

**Conversion in events.py**:
```python
# Add helper method to event classes:
class RawFrame:
    @classmethod
    def fromDict(cls, data):
        return cls(
            eventId=data['eventId'],
            scopeId=data['scopeId'],
            # CHANGED: Parse ISO8601 to datetime
            sourceTruthTime=datetime.fromisoformat(data['sourceTruthTime'].replace('Z', '+00:00')),
            canonicalTruthTime=datetime.fromisoformat(data['canonicalTruthTime'].replace('Z', '+00:00')),
            connectionId=data['connectionId'],
            sequence=data['sequence'],
            bytes=data['bytes']
        )
```

### 3. Transaction Handling

**SQLite (Current)**:
```python
def insertRawEvent(self, event):
    try:
        # Insert into dedupe table
        self.conn.execute("INSERT INTO eventIndex (eventId) VALUES (?)", (event.eventId,))
        
        # Insert into lane table
        self.conn.execute("INSERT INTO rawEvents (...) VALUES (...)", (...))
        
        self.conn.commit()
    except sqlite3.IntegrityError:
        self.conn.rollback()
        raise DuplicateEventError()
```

**TimescaleDB (Target)**:
```python
async def insertRawEvent(self, event):
    async with self.pool.acquire() as conn:
        async with conn.transaction():
            try:
                # Insert into dedupe table
                await conn.execute(
                    "INSERT INTO eventIndex (eventId) VALUES ($1)", 
                    event.eventId
                )
                
                # Insert into lane table
                await conn.execute(
                    "INSERT INTO rawEvents (...) VALUES ($1, $2, ...)",
                    event.eventId, event.scopeId, ...
                )
                
            except asyncpg.UniqueViolationError:
                raise DuplicateEventError()
```

**Key Changes**:
1. `self.conn.execute()` → `await conn.execute()` (async)
2. Manual `commit()/rollback()` → `async with conn.transaction()` (automatic)
3. `sqlite3.IntegrityError` → `asyncpg.UniqueViolationError`

### 4. Connection Lifecycle

**SQLite (Current)**:
```python
# main.py
db = Database('truth.db')
db.connect()  # Single persistent connection

# At shutdown:
db.close()
```

**TimescaleDB (Target)**:
```python
# main.py
db = Database({
    'host': 'localhost',
    'port': 5432,
    'user': 'nova',
    'password': os.environ['NOVA_DB_PASSWORD'],
    'database': 'nova'
})
await db.connect()  # Create connection pool

# At shutdown:
await db.pool.close()  # Close all connections
```

---

## Migration Strategy

### Option A: Side-by-Side Migration (Recommended)

**Steps**:

1. **Install TimescaleDB** (see Installation section below)

2. **Create new database** alongside existing SQLite:
   ```bash
   psql -U postgres -c "CREATE DATABASE nova;"
   psql -U postgres -d nova -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
   ```

3. **Run schema creation script**:
   ```bash
   python3 scripts/create_timescaledb_schema.py --config config.json
   ```

4. **Backfill historical data** (optional):
   ```bash
   python3 scripts/migrate_sqlite_to_timescaledb.py \
       --sqlite truth.db \
       --pg-config config.json \
       --batch-size 10000
   ```

5. **Deploy new database.py** with feature flag:
   ```python
   # config.json:
   {
       "database": {
           "type": "timescaledb",  # or "sqlite"
           "timescaledb": {...},
           "sqlite": {...}
       }
   }
   ```

6. **Validate side-by-side** (dual-write for 24 hours):
   - Write to both SQLite and TimescaleDB
   - Compare query results
   - Monitor performance metrics

7. **Cutover**:
   - Change config: `"type": "timescaledb"`
   - Restart NOVA
   - Monitor for 48 hours

8. **Decomission SQLite**:
   - Archive SQLite DB
   - Remove dual-write code

**Advantages**:
- Zero-downtime migration
- Easy rollback (change config back to sqlite)
- Validate correctness before cutover
- Backfill historical data at leisure

**Disadvantages**:
- Requires dual-write implementation (temporary)
- Disk space for both databases

---

### Option B: Snapshot Migration (Faster, Requires Downtime)

**Steps**:

1. **Stop NOVA** (controlled shutdown)

2. **Snapshot SQLite database**:
   ```bash
   cp truth.db truth.db.backup
   ```

3. **Create TimescaleDB schema**:
   ```bash
   python3 scripts/create_timescaledb_schema.py
   ```

4. **Migrate all data**:
   ```bash
   python3 scripts/migrate_sqlite_to_timescaledb.py \
       --sqlite truth.db.backup \
       --pg-config config.json \
       --batch-size 50000  # Larger batches for speed
   ```

5. **Validate row counts**:
   ```bash
   # SQLite:
   sqlite3 truth.db.backup "SELECT COUNT(*) FROM rawEvents;"
   
   # TimescaleDB:
   psql -U nova -d nova -c "SELECT COUNT(*) FROM rawEvents;"
   ```

6. **Deploy new database.py**:
   ```bash
   # Update config.json to use TimescaleDB
   # Restart NOVA
   ```

7. **Monitor and validate**

**Advantages**:
- Simpler (no dual-write)
- Faster migration (no ongoing writes during backfill)
- Clean cutover

**Disadvantages**:
- Requires downtime (hours to days depending on data volume)
- More risky (harder to rollback)

---

## Installation Prerequisites

### TimescaleDB Installation

**Ubuntu/Debian**:
```bash
# Add PostgreSQL repository
sudo sh -c 'echo "deb https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -

# Add TimescaleDB repository
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -

# Install
sudo apt update
sudo apt install -y postgresql-15 timescaledb-2-postgresql-15

# Configure TimescaleDB
sudo timescaledb-tune --quiet --yes

# Restart PostgreSQL
sudo systemctl restart postgresql
```

**Docker** (Development):
```bash
docker run -d --name timescaledb \
    -p 5432:5432 \
    -e POSTGRES_PASSWORD=password \
    -v timescaledb-data:/var/lib/postgresql/data \
    timescale/timescaledb:latest-pg15
```

### Python Dependencies

**Add to requirements.txt**:
```
asyncpg>=0.29.0      # Async PostgreSQL driver
psycopg2-binary>=2.9  # For migration scripts (sync driver)
```

**Install**:
```bash
pip install -r requirements.txt
```

---

## Configuration

### config.json (New Database Section)

```json
{
  "core": {
    "scopeId": "nova-instance-1"
  },
  
  "database": {
    "type": "timescaledb",
    
    "timescaledb": {
      "host": "localhost",
      "port": 5432,
      "database": "nova",
      "user": "nova",
      "password_env": "NOVA_DB_PASSWORD",
      "pool": {
        "minSize": 10,
        "maxSize": 50,
        "commandTimeout": 60,
        "connectionTimeout": 10
      },
      "hypertable": {
        "chunkInterval": "1 day",
        "compressionAfter": "7 days",
        "retentionAfter": "90 days"
      }
    },
    
    "sqlite": {
      "path": "truth.db"
    }
  },
  
  "server": {...},
  "nats": {...}
}
```

**Environment Variables**:
```bash
export NOVA_DB_PASSWORD="secure_password_here"
```

---

## Migration Scripts

### Script 1: create_timescaledb_schema.py

**Purpose**: Create all tables, indexes, hypertables in TimescaleDB

```python
#!/usr/bin/env python3
"""
Create TimescaleDB schema for NOVA

Usage:
    python3 create_timescaledb_schema.py --config config.json
"""

import asyncio
import asyncpg
import json
import sys
from pathlib import Path

async def create_schema(pgConfig):
    """Create all NOVA tables in TimescaleDB"""
    
    conn = await asyncpg.connect(
        host=pgConfig['host'],
        port=pgConfig['port'],
        user=pgConfig['user'],
        password=pgConfig['password'],
        database=pgConfig['database']
    )
    
    try:
        # Enable TimescaleDB extension
        await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        
        # Create eventIndex (global dedupe)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS eventIndex (
                eventId TEXT PRIMARY KEY,
                firstSeen TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        
        # Create rawEvents
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rawEvents (
                eventId TEXT PRIMARY KEY,
                scopeId TEXT NOT NULL,
                sourceTruthTime TIMESTAMPTZ NOT NULL,
                canonicalTruthTime TIMESTAMPTZ NOT NULL,
                connectionId TEXT NOT NULL,
                sequence BIGINT NOT NULL,
                bytes BYTEA NOT NULL,
                FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
            );
        """)
        
        # Convert to hypertable
        await conn.execute("""
            SELECT create_hypertable('rawEvents', 'canonicalTruthTime',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );
        """)
        
        # Create indexes
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rawEvents_source_order
                ON rawEvents (sourceTruthTime, connectionId, sequence, eventId);
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rawEvents_canonical_order
                ON rawEvents (canonicalTruthTime, connectionId, sequence, eventId);
        """)
        
        # ... similar for parsedEvents, uiEvents, commandEvents, metadataEvents ...
        
        print("✅ TimescaleDB schema created successfully")
        
    finally:
        await conn.close()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, help='Path to config.json')
    args = parser.parse_args()
    
    with open(args.config) as f:
        config = json.load(f)
    
    pgConfig = config['database']['timescaledb']
    asyncio.run(create_schema(pgConfig))
```

---

### Script 2: migrate_sqlite_to_timescaledb.py

**Purpose**: Backfill historical data from SQLite to TimescaleDB

```python
#!/usr/bin/env python3
"""
Migrate data from SQLite to TimescaleDB

Usage:
    python3 migrate_sqlite_to_timescaledb.py \
        --sqlite truth.db \
        --pg-config config.json \
        --batch-size 10000
"""

import sqlite3
import asyncpg
import asyncio
import json
from datetime import datetime

async def migrate_lane(sqliteConn, pgPool, tableName, batchSize):
    """Migrate one lane table"""
    
    cursor = sqliteConn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {tableName}")
    totalRows = cursor.fetchone()[0]
    
    print(f"Migrating {tableName}: {totalRows} rows")
    
    cursor.execute(f"SELECT * FROM {tableName}")
    
    batch = []
    migrated = 0
    
    async with pgPool.acquire() as conn:
        while True:
            rows = cursor.fetchmany(batchSize)
            if not rows:
                break
            
            # Convert TEXT timestamps to datetime
            converted_rows = []
            for row in rows:
                row_dict = dict(row)
                if 'sourceTruthTime' in row_dict:
                    row_dict['sourceTruthTime'] = datetime.fromisoformat(
                        row_dict['sourceTruthTime'].replace('Z', '+00:00')
                    )
                if 'canonicalTruthTime' in row_dict:
                    row_dict['canonicalTruthTime'] = datetime.fromisoformat(
                        row_dict['canonicalTruthTime'].replace('Z', '+00:00')
                    )
                converted_rows.append(row_dict)
            
            # Batch insert
            async with conn.transaction():
                await conn.executemany(
                    f"INSERT INTO {tableName} VALUES ($1, $2, $3, ...) ON CONFLICT DO NOTHING",
                    [(r['eventId'], r['scopeId'], ...) for r in converted_rows]
                )
            
            migrated += len(rows)
            print(f"  {migrated}/{totalRows} ({migrated*100//totalRows}%)")
    
    print(f"✅ {tableName} migration complete")

async def migrate_all(sqlitePath, pgConfig, batchSize):
    """Migrate all tables"""
    
    # Connect to SQLite
    sqliteConn = sqlite3.connect(sqlitePath)
    sqliteConn.row_factory = sqlite3.Row
    
    # Connect to PostgreSQL
    pgPool = await asyncpg.create_pool(
        host=pgConfig['host'],
        port=pgConfig['port'],
        user=pgConfig['user'],
        password=pgConfig['password'],
        database=pgConfig['database'],
        min_size=5,
        max_size=10
    )
    
    try:
        # Migrate eventIndex first (dedupe table)
        await migrate_lane(sqliteConn, pgPool, 'eventIndex', batchSize)
        
        # Migrate all lanes
        for lane in ['rawEvents', 'parsedEvents', 'uiEvents', 'commandEvents', 'metadataEvents']:
            await migrate_lane(sqliteConn, pgPool, lane, batchSize)
        
        print("\n✅ All data migrated successfully")
        
    finally:
        sqliteConn.close()
        await pgPool.close()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sqlite', required=True, help='SQLite database path')
    parser.add_argument('--pg-config', required=True, help='PostgreSQL config JSON')
    parser.add_argument('--batch-size', type=int, default=10000)
    args = parser.parse_args()
    
    with open(args.pg_config) as f:
        config = json.load(f)
    
    asyncio.run(migrate_all(args.sqlite, config['database']['timescaledb'], args.batch_size))
```

---

## Testing Strategy

### 1. Unit Tests (database.py)

**Create**: `test/nova/test_timescaledb.py`

```python
"""
TimescaleDB-specific tests

Tests database.py with PostgreSQL backend, validates:
- Connection pooling
- Async query execution
- TIMESTAMPTZ handling
- JSONB queries
- Transaction rollback
- Concurrent query stress test
"""

@pytest.mark.asyncio
async def test_connection_pool():
    """Validate connection pool creation and cleanup"""
    db = Database(pgConfig)
    await db.connect()
    
    assert db.pool is not None
    assert db.pool.get_size() >= pgConfig['pool']['minSize']
    
    await db.pool.close()
    assert db.pool._closed

@pytest.mark.asyncio
async def test_concurrent_queries():
    """Validate 20 concurrent queries (target scale)"""
    db = Database(pgConfig)
    await db.connect()
    
    async def query_task():
        events = await db.queryRawEvents(
            scopeId='test',
            startTime=datetime.now() - timedelta(hours=1),
            stopTime=datetime.now(),
            timebase=Timebase.CANONICAL
        )
        return len(events)
    
    # Run 20 concurrent queries
    tasks = [query_task() for _ in range(20)]
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 20
    assert all(isinstance(r, int) for r in results)
```

### 2. Integration Tests (Full Stack)

**Reuse existing**: `test/nova/test_phase1.py`, `test_phase2.py`, etc.

**Strategy**: Run all existing tests against TimescaleDB backend

```bash
# Set environment to use TimescaleDB
export NOVA_DB_TYPE=timescaledb
export NOVA_DB_PASSWORD=test_password

# Run full test suite
pytest test/nova/ -v

# Validate all 44 tests pass
```

### 3. Performance Tests

**Create**: `test/nova/test_performance.py`

```python
@pytest.mark.asyncio
@pytest.mark.slow
async def test_write_throughput():
    """Validate 50,000 events/sec write throughput"""
    db = Database(pgConfig)
    await db.connect()
    
    events = [generate_raw_event() for _ in range(50000)]
    
    start = time.time()
    
    async def insert_batch(batch):
        for event in batch:
            await db.insertRawEvent(event)
    
    # Insert in batches of 1000
    batches = [events[i:i+1000] for i in range(0, len(events), 1000)]
    await asyncio.gather(*[insert_batch(b) for b in batches])
    
    elapsed = time.time() - start
    throughput = len(events) / elapsed
    
    print(f"Write throughput: {throughput:.0f} events/sec")
    assert throughput >= 50000  # Target threshold

@pytest.mark.asyncio
@pytest.mark.slow
async def test_concurrent_streaming():
    """Validate 20 concurrent streams (target scale)"""
    # ... simulate 20 WebSocket clients streaming simultaneously ...
    # ... validate query latency < 10ms ...
    # ... validate no connection pool exhaustion ...
```

---

## Rollback Plan

### If Migration Fails

**Step 1**: Identify failure mode
- Schema creation failed? → Fix SQL, retry
- Data migration incomplete? → Resume from last checkpoint
- Performance issues? → Tune indexes, analyze queries

**Step 2**: Immediate rollback (if critical)
```bash
# Change config.json:
"database": {
    "type": "sqlite",  # Back to SQLite
    ...
}

# Restart NOVA
systemctl restart nova
```

**Step 3**: Preserve TimescaleDB for debugging
```bash
# Don't drop the database yet
# Keep PostgreSQL running for analysis
```

**Step 4**: Root cause analysis
```bash
# Check PostgreSQL logs
tail -f /var/log/postgresql/postgresql-15-main.log

# Check query plans
psql -U nova -d nova
EXPLAIN ANALYZE SELECT * FROM rawEvents WHERE ...;

# Check index usage
SELECT * FROM pg_stat_user_indexes WHERE schemaname = 'public';
```

### If Performance Degrades

**Symptoms**: Queries slower than SQLite, high CPU, connection timeouts

**Diagnosis**:
1. Check query plans: `EXPLAIN ANALYZE SELECT ...`
2. Verify indexes: `SELECT * FROM pg_indexes WHERE tablename = 'rawEvents';`
3. Check connection pool: `SELECT count(*) FROM pg_stat_activity;`
4. Hypertable chunks: `SELECT * FROM timescaledb_information.chunks;`

**Fixes**:
1. Missing indexes → Create indexes matching ORDER BY
2. Full table scans → Add WHERE clause indexes
3. Connection pool exhausted → Increase maxSize
4. Chunk interval too small → Adjust chunk_time_interval

---

## Operational Considerations

### Monitoring

**Key Metrics**:
1. Query latency (p50, p95, p99)
2. Write throughput (events/sec)
3. Connection pool usage (active/idle/waiting)
4. Database size (disk space)
5. Chunk count (hypertable fragmentation)

**Tools**:
- PostgreSQL `pg_stat_statements` extension
- TimescaleDB Toolkit (continuous aggregates)
- Prometheus PostgreSQL exporter
- Grafana dashboards

### Backup Strategy

**TimescaleDB-specific**:
```bash
# Full backup (pg_dump)
pg_dump -U nova -d nova -Fc -f nova_backup_$(date +%Y%m%d).dump

# Continuous archiving (WAL)
# Configure in postgresql.conf:
archive_mode = on
archive_command = 'cp %p /backups/wal/%f'

# Point-in-time recovery
pg_basebackup -D /backups/base -Ft -z -P
```

### Compression Policy

**Automatic compression after 7 days**:
```sql
SELECT add_compression_policy('rawEvents', INTERVAL '7 days');
SELECT add_compression_policy('parsedEvents', INTERVAL '7 days');
-- ... for all lanes ...
```

**Expected compression**: 10-20x reduction (1GB → 50-100MB)

### Retention Policy

**Automatic deletion after 90 days**:
```sql
SELECT add_retention_policy('rawEvents', INTERVAL '90 days');
SELECT add_retention_policy('parsedEvents', INTERVAL '90 days');
-- ... for all lanes ...
```

---

## Risk Assessment

### High Risk

❌ **Data loss during migration**  
Mitigation: Backup SQLite before migration, validate row counts, test rollback

❌ **Performance regression**  
Mitigation: Load testing before production, side-by-side validation

❌ **Breaking changes in database.py API**  
Mitigation: Maintain same async function signatures, comprehensive testing

### Medium Risk

⚠️ **Connection pool exhaustion under load**  
Mitigation: Configure maxSize=50, add connection pool monitoring

⚠️ **PostgreSQL configuration tuning required**  
Mitigation: Run `timescaledb-tune`, monitor query performance

⚠️ **TIMESTAMPTZ timezone handling**  
Mitigation: Always use UTC (datetime.timezone.utc), test edge cases

### Low Risk

✅ **Schema changes after migration**  
Mitigation: PostgreSQL supports ALTER TABLE, migrations well-understood

✅ **Operational complexity**  
Mitigation: PostgreSQL is mature, widely deployed, good documentation

---

## Timeline Estimate

### Preparation (1-2 weeks)
- [ ] Install TimescaleDB (development + staging)
- [ ] Write migration scripts (create_schema, migrate_data)
- [ ] Rewrite database.py for asyncpg
- [ ] Update config.json structure
- [ ] Write TimescaleDB-specific tests

### Testing (1-2 weeks)
- [ ] Unit tests (database.py functions)
- [ ] Integration tests (full stack with PostgreSQL)
- [ ] Performance tests (50K events/sec write, 20 concurrent streams)
- [ ] Load testing (sustained load over 24 hours)
- [ ] Failure testing (connection loss, transaction rollback)

### Migration (1 day - 1 week depending on data volume)
- [ ] Deploy TimescaleDB in production
- [ ] Run schema creation
- [ ] Backfill historical data (if using side-by-side)
- [ ] Validate data integrity (row counts, spot checks)
- [ ] Dual-write validation (if using side-by-side)

### Cutover (1 day)
- [ ] Change config to TimescaleDB
- [ ] Restart NOVA
- [ ] Monitor for 48 hours
- [ ] Decomission SQLite

**Total Estimate**: 3-5 weeks for complete migration with validation

---

## Success Criteria

### Functional

✅ All 44 existing tests pass with TimescaleDB backend  
✅ Zero data loss during migration (row count validation)  
✅ Same query results as SQLite (deterministic ordering preserved)  
✅ WebSocket streaming works identically (LIVE/REWIND modes)

### Performance

✅ Write throughput ≥ 50,000 events/sec  
✅ Query latency < 10ms (p95)  
✅ 20 concurrent streams without degradation  
✅ Connection pool no exhaustion under load

### Operational

✅ Backup/restore procedures documented and tested  
✅ Monitoring dashboards operational  
✅ Rollback plan validated (can revert to SQLite in <1 hour)  
✅ Compression and retention policies active

---

## Conclusion

Migration from SQLite to TimescaleDB is **necessary** for 100x scale but **high-risk** due to database backend change. Recommended approach:

1. **Side-by-side migration** with dual-write validation
2. **Comprehensive testing** at each stage (unit → integration → performance → load)
3. **Careful monitoring** during and after cutover
4. **Validated rollback plan** for fast revert if needed

**Expected Outcome**: System capable of 500 devices @ 10Hz (50,000 events/sec write), 20 concurrent users, 1B+ events stored with <10ms query latency.

---

**Document Version**: 1.0  
**Last Updated**: January 2026  
**Status**: Planning document - not yet implemented  
**Next Step**: Install TimescaleDB in development environment, begin database.py rewrite
