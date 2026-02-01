# Single Database Truth

**Archive as Authoritative Source**

---

## Overview

NOVA implements a **single database truth model**: novaArchive contains the only authoritative database. All other services (novaCore, GEM, browsers) are stateless or cache-only.

**Key Principle**: One source of truth, multiple read-only views.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    PRODUCERS (GEM, 3rd-party)                 │
│                                                               │
│         Publish streams + metadata via NATS                   │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                       novaArchive                             │
│                 ╔═══════════════════════════════╗            │
│                 ║   SQLite Database (TRUTH)     ║            │
│                 ║                               ║            │
│                 ║  • messages table             ║            │
│                 ║  • metadata table             ║            │
│                 ║  • commands table             ║            │
│                 ║                               ║            │
│                 ║  Receive-time authority       ║            │
│                 ║  Deterministic ingestion      ║            │
│                 ╚═══════════════════════════════╝            │
│                                                               │
│         Republish + HTTP API (read-only)                      │
└───────────────────────┬────────────────┬──────────────────────┘
                        │                │
           NATS         │                │  HTTP
           republish    │                │  queries
                        │                │
                        ▼                ▼
           ┌────────────────┐   ┌────────────────┐
           │   novaCore     │   │   Browser      │
           │                │   │                │
           │  No truth DB   │   │  No truth DB   │
           │  Cache only    │   │  Timeline store│
           │  (metadata)    │   │  (playback)    │
           └────────────────┘   └────────────────┘
```

---

## Truth Model

### Single Source of Truth

**novaArchive Database**:
- Messages (stream data)
- Metadata (entity definitions)
- Commands (audit trail)

**NOT in novaCore**:
- ❌ No messages database
- ❌ No metadata database
- ❌ No command database

**novaCore has**:
- ✅ Metadata cache (read from archive, expires after 30s TTL)
- ✅ HTTP proxy (forwards requests to archive)
- ✅ WebSocket forwarder (forwards NATS deltas to browser)

### Receive-Time Authority

**Rule**: novaArchive assigns `timestampMs` on ingestion (overrides device timestamp).

**Rationale**:
- Device clocks drift
- GPS time ≠ UTC (leap seconds)
- Network delays introduce jitter
- Deterministic ordering requires single clock source

**Implementation**:
```python
async def ingest_message(self, msg):
    # Parse producer message
    data = json.loads(msg.data.decode())
    
    # Archive assigns receive-time (authoritative)
    receive_time = int(time.time() * 1000)
    
    # Store device timestamp as auxiliary field
    device_timestamp = data.get('timestampMs', receive_time)
    
    # Insert to database
    cursor.execute('''
        INSERT INTO messages (
            assetId, scopeId, streamType, lane,
            data, timestampMs, deviceTimestampMs,
            sequenceNum, version, hash, ingestTime
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['assetId'],
        data['scopeId'],
        data['streamType'],
        data.get('lane', 'truth'),
        json.dumps(data, sort_keys=True),
        receive_time,              # Archive timestamp (authoritative)
        device_timestamp,          # Device timestamp (auxiliary)
        data['sequenceNum'],
        data['version'],
        hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
        time.time()
    ))
```

**Query Usage**:
```sql
-- Query by receive-time (authoritative)
SELECT * FROM messages WHERE timestampMs BETWEEN ? AND ?

-- Query by device-time (auxiliary, for debugging)
SELECT * FROM messages WHERE deviceTimestampMs BETWEEN ? AND ?
```

---

## Database Schema

### messages Table

**Schema**:
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assetId TEXT NOT NULL,
    scopeId TEXT NOT NULL,
    streamType TEXT NOT NULL,
    lane TEXT NOT NULL,
    data TEXT NOT NULL,                 -- JSON (deterministic)
    timestampMs INTEGER NOT NULL,        -- Receive-time (authoritative)
    deviceTimestampMs INTEGER,           -- Device timestamp (auxiliary)
    sequenceNum INTEGER,
    version INTEGER,
    hash TEXT,                           -- SHA-256 of deterministic JSON
    ingestTime REAL NOT NULL
);

CREATE INDEX idx_messages_query ON messages(scopeId, timestampMs, streamType, assetId);
CREATE INDEX idx_messages_hash ON messages(hash);
CREATE INDEX idx_messages_asset ON messages(assetId, timestampMs);
```

**Why `timestampMs` (not `deviceTimestampMs`)?**
- Deterministic ordering (single clock source)
- Replay consistency (same order every time)
- Deduplication (hash + receive-time)

### metadata Table

**Schema**:
```sql
CREATE TABLE metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assetId TEXT NOT NULL,
    scopeId TEXT NOT NULL,
    timestampMs INTEGER NOT NULL,        -- Receive-time (authoritative)
    data TEXT NOT NULL,                  -- JSON (partial or full metadata)
    priority INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    hash TEXT,
    ingestTime REAL NOT NULL
);

CREATE INDEX idx_metadata_asset ON metadata(assetId, timestampMs DESC);
CREATE INDEX idx_metadata_scope ON metadata(scopeId, timestampMs DESC);
```

**Time-Versioned**: Full history of metadata changes.

**Priority-Based**: Higher priority wins (ground=10, producer=0).

### commands Table

**Schema**:
```sql
CREATE TABLE commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commandId TEXT NOT NULL UNIQUE,
    entityId TEXT NOT NULL,
    verb TEXT NOT NULL,
    actionId TEXT NOT NULL,
    params TEXT,                         -- JSON
    scopeId TEXT NOT NULL,
    status TEXT NOT NULL,                -- sent, success, error, timeout
    message TEXT,
    timestampMs INTEGER NOT NULL,        -- Receive-time (authoritative)
    resultTimestampMs INTEGER,           -- Result receive-time
    source TEXT NOT NULL,
    ingestTime REAL NOT NULL
);

CREATE INDEX idx_commands_entity ON commands(entityId, timestampMs DESC);
CREATE INDEX idx_commands_status ON commands(status, timestampMs DESC);
```

**Audit Trail**: Full history of commands (request + result).

---

## Read Patterns

### Live Streaming (NATS)

**Flow**: Archive → novaCore → Browser

**Archive Republish**:
```python
async def republish_live(self, msg):
    # After ingestion, republish to NATS
    await self.nats.publish(
        f"archive.{msg.scopeId}.ui.{msg.streamType}",
        msg.data
    )
```

**novaCore Forward**:
```python
async def forward_to_websocket(self, msg):
    # Forward NATS message to WebSocket
    data = json.loads(msg.data.decode())
    
    for client in self.websocket_clients:
        await client.send_json({
            'type': 'delta',
            'streamType': data['streamType'],
            'assetId': data['assetId'],
            'timestampMs': data['timestampMs'],
            'patch': data['patch']
        })
```

**Browser Receive**:
```javascript
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data)
  if (msg.type === 'delta') {
    timeline.applyDelta(msg)
  }
}
```

### Replay (HTTP)

**Flow**: Browser → novaCore → Archive (query) → novaCore → Browser

**Browser Request**:
```javascript
const deltas = await fetch(
  `/api/replay/deltas?start=${start}&end=${end}&scope=${scope}`
).then(r => r.json())
```

**novaCore Proxy**:
```python
@app.get('/api/replay/deltas')
async def get_deltas(start: int, end: int, scope: str):
    # Forward to archive HTTP API
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f'http://localhost:8081/api/replay/deltas',
            params={'start': start, 'end': end, 'scope': scope}
        ) as resp:
            return await resp.json()
```

**Archive Query**:
```python
@app.get('/api/replay/deltas')
async def get_deltas(start: int, end: int, scope: str):
    cursor.execute('''
        SELECT assetId, streamType, data, timestampMs
        FROM messages
        WHERE scopeId = ? AND timestampMs > ? AND timestampMs <= ?
        ORDER BY timestampMs ASC
    ''', (scope, start, end))
    
    messages = []
    for row in cursor:
        asset_id, stream_type, data_json, timestamp = row
        data = json.loads(data_json)
        messages.append({
            'assetId': asset_id,
            'streamType': stream_type,
            'timestampMs': timestamp,
            'patch': data['patch']
        })
    
    return {'start': start, 'end': end, 'scope': scope, 'messages': messages}
```

---

## Write Patterns

### Producer Ingestion

**Flow**: GEM → NATS → Archive

**GEM Publish**:
```python
msg = {
    "assetId": "8220-F9P",
    "scopeId": "payload-1",
    "streamType": "position",
    "sequenceNum": 42,
    "timestampMs": int(time.time() * 1000),  # Device time (auxiliary)
    "patch": {"lat": 40.647002, "lon": -111.818352, "alt": 1354.2},
    "version": 1
}

await nats.publish(
    f"stream.truth.position.{msg['scopeId']}.{msg['assetId']}",
    json.dumps(msg, sort_keys=True).encode()
)
```

**Archive Ingest**:
```python
async def handle_stream_message(self, msg):
    data = json.loads(msg.data.decode())
    
    # Archive assigns receive-time (authoritative)
    receive_time = int(time.time() * 1000)
    device_time = data.get('timestampMs', receive_time)
    
    # Compute hash (deduplication)
    msg_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    
    # Check for duplicate
    cursor.execute('SELECT id FROM messages WHERE hash = ?', (msg_hash,))
    if cursor.fetchone():
        logger.warning(f"Duplicate message: {msg_hash}")
        return
    
    # Insert to database
    cursor.execute('''
        INSERT INTO messages (
            assetId, scopeId, streamType, lane, data,
            timestampMs, deviceTimestampMs, sequenceNum, version, hash, ingestTime
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['assetId'], data['scopeId'], data['streamType'], 'truth',
        json.dumps(data, sort_keys=True),
        receive_time, device_time,
        data['sequenceNum'], data['version'], msg_hash, time.time()
    ))
    
    # Republish to NATS
    await self.republish_live(data)
```

### Command Ingestion

**Flow**: Browser → novaCore → NATS → GEM → hardwareService → Device  
**Result Flow**: GEM → NATS → Archive → Database

**Archive Ingest (Command Request)**:
```python
async def handle_command_publish(self, msg):
    data = json.loads(msg.data.decode())
    
    cursor.execute('''
        INSERT INTO commands (
            commandId, entityId, verb, actionId, params, scopeId,
            status, timestampMs, source, ingestTime
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['commandId'], data['entityId'], data['verb'], data['actionId'],
        json.dumps(data.get('params', {})), data['scopeId'],
        'sent', int(time.time() * 1000), data['source'], time.time()
    ))
```

**Archive Ingest (Command Result)**:
```python
async def handle_command_result(self, msg):
    data = json.loads(msg.data.decode())
    
    cursor.execute('''
        UPDATE commands
        SET status = ?, message = ?, resultTimestampMs = ?
        WHERE commandId = ?
    ''', (
        data['status'], data['message'], int(time.time() * 1000), data['commandId']
    ))
```

---

## Cache Strategy (novaCore)

### Metadata Cache

**Purpose**: Reduce HTTP round-trips for command validation.

**Implementation**:
```python
class MetadataCache:
    def __init__(self, ttl_seconds=30):
        self.cache = {}  # assetId -> (metadata, expiry)
        self.ttl = ttl_seconds
    
    async def get(self, asset_id):
        now = time.time()
        
        if asset_id in self.cache:
            metadata, expiry = self.cache[asset_id]
            if now < expiry:
                return metadata
        
        # Cache miss, fetch from archive
        metadata = await self.fetch_from_archive(asset_id)
        self.cache[asset_id] = (metadata, now + self.ttl)
        return metadata
    
    async def fetch_from_archive(self, asset_id):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'http://localhost:8081/api/v1/metadata/{asset_id}'
            ) as resp:
                data = await resp.json()
                return data['metadata']
```

**Usage**:
```python
@app.post('/api/commands')
async def send_command(cmd: dict):
    # Lookup metadata (from cache or archive)
    metadata = await metadata_cache.get(cmd['entityId'])
    
    # Validate scopeId
    if metadata['scopeId'] != cmd['scopeId']:
        return {'error': 'Scope mismatch'}, 400
    
    # Forward command to NATS
    await nats.publish(f"command.{cmd['verb']}.{cmd['entityId']}", ...)
```

**Why Cache?** Commands are latency-sensitive. Caching reduces command latency from ~50ms to ~5ms.

**Why TTL?** Metadata can change (device reconnect, config change). TTL ensures cache freshness.

---

## Benefits

### Simplicity

**One Database**: No distributed consensus, no replication, no sync conflicts.

**Query Path**: Single point of truth → simple query logic.

**Backup**: Single SQLite file → simple backup/restore.

### Reliability

**No Split-Brain**: Archive is authoritative. If novaCore crashes, archive continues ingesting.

**Stateless Recovery**: novaCore restarts, re-caches metadata, resumes forwarding.

**Deterministic Replay**: Same query always returns same result (receive-time ordering).

### Scalability

**Read Scaling**: Add more novaCore instances (stateless proxy).

**Write Scaling**: Archive is single-writer (SQLite limitation), but handles 1000+ msg/s easily.

**Future**: Migrate to Postgres for multi-writer scaling.

---

## Limitations & Mitigations

### SQLite Write Throughput

**Limitation**: SQLite single-writer, ~1000 inserts/second.

**Mitigation**: Batch inserts (100 messages per transaction).

**Future**: Migrate to Postgres (multi-writer, 10K+ inserts/second).

### Single Point of Failure

**Limitation**: Archive crash → ingestion stops.

**Mitigation**: Archive auto-restart (systemd, Docker), NATS buffering (30s window).

**Future**: Active-passive replica (Postgres logical replication).

### Database Size

**Limitation**: 10 Hz × 86400 seconds × 365 days = 315M messages/year/entity.

**Mitigation**: Retention policy (delete messages older than 1 year).

**Storage**: 1KB/message × 315M = 315 GB/year/entity (acceptable for modern storage).

---

## Comparison to Distributed Models

### Multi-Database Model (NOT used)

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│ novaArchive  │       │  novaCore    │       │   Browser    │
│              │       │              │       │              │
│  messages DB │ ────> │  messages DB │ ────> │  IndexedDB   │
│  (truth)     │ sync  │  (replica)   │ sync  │  (replica)   │
└──────────────┘       └──────────────┘       └──────────────┘
```

**Problems**:
- Sync lag → inconsistent views
- Conflict resolution (what if novaCore DB diverges?)
- Backup complexity (3 databases to backup)
- Query complexity (which DB is authoritative?)

### Distributed Consensus (NOT used)

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│ novaArchive  │       │  novaCore    │       │   Replica    │
│ (Raft leader)│ <───> │  (follower)  │ <───> │  (follower)  │
│  messages DB │ sync  │  messages DB │ sync  │  messages DB │
└──────────────┘       └──────────────┘       └──────────────┘
```

**Problems**:
- Complexity (Raft/Paxos implementation)
- Split-brain scenarios
- Network partition handling
- Overkill for single-writer workload

---

## Future Work

### Postgres Migration

**Goal**: Multi-writer scaling, active-passive replication.

**Migration Path**:
1. Dual-write (SQLite + Postgres)
2. Validate data consistency
3. Switch read queries to Postgres
4. Deprecate SQLite

### Read Replicas

**Goal**: Distribute read load across multiple novaCore instances.

**Implementation**: Archive writes to primary DB, replicates to read replicas (Postgres logical replication).

### Retention Policies

**Goal**: Auto-delete old messages (configurable retention period).

**Implementation**:
```python
async def cleanup_old_messages():
    retention_days = 365
    cutoff_time = (time.time() - retention_days * 86400) * 1000
    
    cursor.execute('DELETE FROM messages WHERE timestampMs < ?', (cutoff_time,))
    logger.info(f"Deleted {cursor.rowcount} old messages")
```

---

## Summary

Single database truth model ensures novaArchive is the authoritative source for all data. Other services are stateless or cache-only. Receive-time authority provides deterministic ordering.

**Key Takeaways**:
- ✅ novaArchive is authoritative (single source of truth)
- ✅ novaCore has no truth DB (cache only)
- ✅ Browser has no truth DB (timeline store for playback only)
- ✅ Receive-time authority (archive assigns timestamps)
- ✅ Deterministic ingestion (hash-based deduplication)
- ✅ Simple query path (single database)
- ✅ Scalable read pattern (stateless proxy)

**Related Features**:
- [Receive-Time Authority](receiveTimeAuthority.md) - Timestamp authority
- [Deterministic Messages](deterministicMessages.md) - Stable hashing
- [Stateless Replay](statelessReplay.md) - Read-only query interface
