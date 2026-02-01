# Additional Key Features

**Quick Reference for Remaining NOVA Features**

This document provides summaries of additional key features. Full detailed documents can be created as needed.

---

## Deterministic Messages

**Principle**: Messages must produce stable hashes for deduplication and reproducibility.

**Requirements**:
- JSON fields alphabetically ordered (`json.dumps(data, sort_keys=True)`)
- camelCase field names (not snake_case)
- No floating-point precision issues (round to fixed decimal places)
- No random UUIDs in message body (use external commandId/messageId)

**Hash Computation**:
```python
msg_hash = hashlib.sha256(json.dumps(msg, sort_keys=True).encode()).hexdigest()
```

**Deduplication**: Archive checks hash before insertion, rejects duplicates.

---

## Time-Versioned Metadata

**Principle**: Full history of metadata changes with priority-based overrides.

**Schema**:
```sql
CREATE TABLE metadata (
    assetId TEXT, timestampMs INTEGER, data TEXT, priority INTEGER, source TEXT
)
```

**Priority Rules**:
- `0`: Producer (GEM) default
- `10`: Ground override (novaCore overrides.json)
- Higher priority wins; equal priority → latest wins

**Query** (get metadata at time T):
```sql
SELECT * FROM metadata WHERE assetId = ? AND timestampMs <= ? ORDER BY timestampMs DESC LIMIT 1
```

---

## Command Pipeline

**Flow**: Browser → novaCore → NATS → GEM → hardwareService → Device

**Manifest-Driven**: Commands defined in `gem.manifest.json` (verb, actionId, params).

**Audit Trail**: Full request→result stored in archive commands table.

**Validation**: GEM validates scopeId, novaCore caches metadata for latency.

---

## Scope Model

**Purpose**: Authorization boundaries, network isolation.

**Examples**: `payload-1`, `payload-2`, `ground`

**Validation**:
- GEM: Rejects commands if scopeId doesn't match device scopeId
- Archive: Stores scopeId with every message
- Replay: Filter by scopeId in queries

**Future**: Multi-tenant isolation, role-based scope access.

---

## Receive-Time Authority

**Principle**: novaArchive assigns `timestampMs` on ingestion (overrides device timestamp).

**Rationale**:
- Device clocks drift
- GPS time ≠ UTC (leap seconds)
- Deterministic ordering requires single clock source

**Device Timestamp**: Stored as `deviceTimestampMs` (auxiliary field for debugging).

---

## Three-Level Hierarchy

**Structure**: System → Container → Asset

**Example**:
- System: "Mission Alpha" (systemId: `mission-alpha`)
- Container: "Payload 1" (containerId: `payload-1`)
- Asset: "ZED-F9P Receiver" (assetId: `8220-F9P`)

**Purpose**: Organizational grouping, UI navigation, scope validation.

---

## Change-Only Metadata

**Principle**: Publish metadata on connect + change (not periodic heartbeat).

**Implementation**:
```python
# Full metadata on connect
await publish_metadata(device, full_metadata)

# Change-only on update
await publish_metadata_update(device, {"online": True, "lastSeen": 1706188496789})
```

**Benefits**: Reduces NATS message volume, archive storage, and query complexity.

---

## Multi-Lane Publishing

(See [laneArchitecture.md](laneArchitecture.md) for full details)

**Three Producer Lanes**: raw (binary), truth (10 Hz), UI (1-2 Hz)

**Two Consumer Lanes**: firehose (10 Hz), UI (1-2 Hz)

**Purpose**: Separation of concerns (operators vs analysts vs engineers).

---

## Stateless Servers

**novaCore**: No session state, no client tracking, no mode tracking.

**Archive**: Stateless HTTP API (snapshot, deltas, bounds).

**Benefits**: Infinite scalability, zero memory overhead per client, simple recovery.

---

## HTTP Metadata API (3rd-Party Integration)

**Endpoint**: `POST /api/v1/metadata`

**Purpose**: Allow 3rd-party producers to publish metadata via HTTP (alternative to NATS).

**Flow**: 3rd-party → novaCore → overrides.json → NATS → Archive

**Use Case**: Weather sensors, drones, ground stations without NATS access.

---

## Manifest Authority

**File**: `gem.manifest.json`

**Purpose**: Define commands (verb, actionId, displayName, params, targetType).

**Benefits**: Declarative command definitions, no code changes for new commands, UI auto-generation.

**Example**:
```json
{
  "verb": "receiver.hotStart",
  "actionId": "hotStart",
  "displayName": "Hot Start",
  "targetType": "gnss-receiver",
  "params": []
}
```

---

## Command Audit Trail

**Table**: `commands` (commandId, entityId, verb, status, message, timestampMs, resultTimestampMs)

**Purpose**: Full history of commands for compliance, debugging, forensics.

**Query** (get all commands for entity):
```sql
SELECT * FROM commands WHERE entityId = ? ORDER BY timestampMs DESC
```

---

## Deterministic Ingestion

**Deduplication**: Archive computes SHA-256 hash of deterministic JSON, rejects duplicates.

**Ordering**: Archive orders by `timestampMs` (receive-time authority).

**Reproducibility**: Same ingestion order → same database state → same replay output.

---

## Rate Limiting (UI Lane)

**Producer Side** (GEM): Rate limit to 1-2 Hz per (assetId, streamType).

**Consumer Side** (Archive): Republish UI lane at ingested rate (passthrough).

**Purpose**: Prevent browser overwhelm, reduce NATS message volume.

---

## WebSocket Protocol

**Snapshot**: Sent on connect (full state).

**Delta**: Sent on NATS message (incremental update).

**Error**: Sent on auth failure or protocol error.

**Mode Derivation**: WebSocket open = live, WebSocket closed = playback.

---

## Metadata Overrides

**File**: `novaCore/overrides.json`

**Purpose**: Ground operator overrides producer metadata (higher priority).

**Example**:
```json
{
  "8220-F9P": {
    "name": "Primary GNSS Receiver",
    "priority": 10
  }
}
```

**Flow**: 3rd-party HTTP → novaCore → apply overrides → NATS → Archive

---

## Scope Validation

**GEM**: Validates scopeId in command matches device scopeId.

**Archive**: Stores scopeId with every message.

**novaCore**: Caches metadata (including scopeId) for command validation.

**Purpose**: Prevent cross-scope commands (e.g., payload-1 commanding payload-2 device).

---

## Related Documents

- [nova architecture.md](../nova%20architecture.md) - Full system architecture
- [nova api.md](../nova%20api.md) - Complete API reference
- [gem architecture.md](../gem%20architecture.md) - GEM implementation
- [statelessReplay.md](statelessReplay.md) - Detailed stateless replay
- [laneArchitecture.md](laneArchitecture.md) - Detailed lane architecture
- [singleDatabaseTruth.md](singleDatabaseTruth.md) - Detailed database model
