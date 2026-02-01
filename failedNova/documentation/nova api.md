# NOVA API Reference

**Complete HTTP, WebSocket, and NATS API Documentation**  
**Version:** 2.0  
**Date:** January 25, 2026  
**Status:** Production

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [HTTP REST API](#http-rest-api)
4. [WebSocket API](#websocket-api)
5. [NATS Subjects](#nats-subjects)
6. [Message Formats](#message-formats)
7. [Error Handling](#error-handling)
8. [Rate Limiting](#rate-limiting)

---

## Overview

NOVA exposes three API surfaces:

1. **HTTP REST API** (novaCore): Web UI, commands, replay queries, metadata
2. **WebSocket API** (novaCore): Live streaming, real-time deltas
3. **NATS Subjects** (Internal): Producer→Archive, Archive→novaCore, Commands

### Base URLs

- **novaCore HTTP**: `http://localhost:8080` (default)
- **novaCore WebSocket**: `ws://localhost:8080/ws/live`
- **novaArchive HTTP**: `http://localhost:8081` (default, proxied by novaCore)
- **NATS**: `nats://localhost:4222` (internal)

---

## Authentication

### Overview

novaCore supports JWT-based authentication with role-based access control.

**Roles**:
- `user`: Standard access (read, send commands)
- `admin`: Full access (user management, overrides)

### Endpoints

#### POST /api/auth/register

Register a new user (creates pending user requiring admin approval).

**Request**:
```json
{
  "username": "john.doe",
  "password": "securePassword123"
}
```

**Response (201 Created)**:
```json
{
  "userId": "uuid-123",
  "username": "john.doe",
  "role": "user",
  "status": "pending"
}
```

#### POST /api/auth/login

Login and receive JWT token.

**Request**:
```json
{
  "username": "john.doe",
  "password": "securePassword123"
}
```

**Response (200 OK)**:
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "userId": "uuid-123",
    "username": "john.doe",
    "role": "user",
    "status": "active"
  }
}
```

**Error Responses**:
- `401 Unauthorized`: Invalid credentials
- `403 Forbidden`: Account pending or disabled

#### POST /api/auth/logout

Logout current user (invalidates token).

**Headers**:
```
Authorization: Bearer <token>
```

**Response (200 OK)**:
```json
{
  "message": "Logged out successfully"
}
```

#### GET /api/auth/me

Get current user info.

**Headers**:
```
Authorization: Bearer <token>
```

**Response (200 OK)**:
```json
{
  "userId": "uuid-123",
  "username": "john.doe",
  "role": "user",
  "status": "active",
  "createdAt": "2026-01-25T10:00:00Z",
  "lastLoginAt": "2026-01-25T12:30:00Z"
}
```

---

## HTTP REST API

### Configuration

#### GET /api/config

Get client configuration.

**Response (200 OK)**:
```json
{
  "mapsOffline": false,
  "terrainEnabled": true,
  "imageryProvider": "ion",
  "authEnabled": true,
  "scopeId": "payload-1"
}
```

### Snapshot & State

#### GET /api/snapshot

Get complete system snapshot (entities, positions, tasks, artifacts).

**Query Parameters**:
- `clientTime` (optional): Client timestamp in milliseconds
- `ttlSeconds` (optional): Entity visibility window (default: 30)

**Response (200 OK)**:
```json
{
  "type": "snapshot",
  "clientSessionId": "session-uuid",
  "timestamp": "2026-01-25T12:34:56.789Z",
  "entities": {
    "8220-F9P": {
      "assetId": "8220-F9P",
      "name": "ZED-F9P Receiver",
      "entityType": "gnss-receiver",
      "cardType": "gnss-card",
      "systemId": "mission-alpha",
      "containerId": "payload-1",
      "online": true,
      "lastSeen": 1706188496789,
      "attributes": {
        "manufacturer": "u-blox",
        "model": "ZED-F9P"
      }
    }
  },
  "positions": {
    "8220-F9P": {
      "lat": 40.647002,
      "lon": -111.818352,
      "alt": 1354.2,
      "heading": 90.0,
      "timestamp": "2026-01-25T12:34:56.789Z"
    }
  },
  "tasks": {},
  "artifacts": {}
}
```

### Replay API (Stateless)

#### GET /api/replay/scopes

Get available scopes for replay.

**Response (200 OK)**:
```json
{
  "scopes": ["payload-1", "payload-2", "ground"],
  "default": "payload-1"
}
```

#### GET /api/replay/snapshot

Get snapshot at specific time (playback).

**Query Parameters** (Required):
- `time`: Timestamp in milliseconds
- `scope`: Scope ID (e.g., "payload-1")
- `entities` (optional): Comma-separated entity IDs to filter

**Example**:
```
GET /api/replay/snapshot?time=1706188400000&scope=payload-1
```

**Response (200 OK)**:
```json
{
  "time": 1706188400000,
  "scope": "payload-1",
  "entities": {
    "8220-F9P": {
      "assetId": "8220-F9P",
      "name": "ZED-F9P Receiver",
      "entityType": "gnss-receiver",
      "lat": 40.647002,
      "lon": -111.818352,
      "alt": 1354.2,
      "heading": 90.0,
      "timestampMs": 1706188395000
    }
  }
}
```

**Window Semantics**: Returns state as-of time T (all entities with data ≤ T).

#### GET /api/replay/deltas

Get delta events in time window.

**Query Parameters** (Required):
- `start`: Start timestamp in milliseconds (exclusive)
- `end`: End timestamp in milliseconds (inclusive)
- `scope`: Scope ID
- `entities` (optional): Comma-separated entity IDs to filter

**Window Semantics**: `(start, end]` - Exclusive start, inclusive end.

**Example**:
```
GET /api/replay/deltas?start=1706188400000&end=1706188402000&scope=payload-1
```

**Response (200 OK)**:
```json
{
  "start": 1706188400000,
  "end": 1706188402000,
  "scope": "payload-1",
  "messages": [
    {
      "assetId": "8220-F9P",
      "streamType": "position",
      "timestampMs": 1706188401000,
      "patch": {
        "lat": 40.647003,
        "lon": -111.818353,
        "alt": 1354.3,
        "heading": 90.0
      }
    },
    {
      "assetId": "8220-F9P",
      "streamType": "gnss-signals",
      "timestampMs": 1706188401500,
      "patch": {
        "avgCn0": 42.5,
        "fixStatus": "3D_FIX",
        "constellations": [...]
      }
    }
  ]
}
```

**Usage Pattern**:
```javascript
// Initial snapshot
const snapshot = await fetch(`/api/replay/snapshot?time=T&scope=X`)

// Continuous delta pulling (playback loop)
let lastDeltaEnd = T
while (playing) {
  const windowMs = speed * 2000  // 2 seconds of data per pull
  const deltas = await fetch(
    `/api/replay/deltas?start=${lastDeltaEnd}&end=${lastDeltaEnd + windowMs}&scope=X`
  )
  applyDeltas(deltas.messages)
  lastDeltaEnd = deltas.end  // Next window starts here (no gaps, no duplicates)
}
```

#### GET /api/replay/bounds

Get time bounds for scope (earliest/latest data).

**Query Parameters** (Required):
- `scope`: Scope ID

**Example**:
```
GET /api/replay/bounds?scope=payload-1
```

**Response (200 OK)**:
```json
{
  "scope": "payload-1",
  "startTime": 1706180000000,
  "endTime": 1706188500000,
  "duration": 8500000,
  "messageCount": 12543
}
```

#### POST /api/replay/raw-tcp *

Start raw TCP replay session (Option 1 - current approach, open to change).

**Request**:
```json
{
  "entityId": "8220-F9P",
  "startTime": 1706188400000,
  "endTime": 1706188500000,
  "speedMultiplier": 1.0
}
```

**Response (200 OK)**:
```json
{
  "status": "ready",
  "sessionId": "tcp-session-123",
  "host": "localhost",
  "port": 9001,
  "entityId": "8220-F9P",
  "startTime": 1706188400000,
  "endTime": 1706188500000,
  "message": "Connect to localhost:9001 to begin streaming"
}
```

**TCP Stream Behavior**:
1. Client connects to returned TCP port
2. Server streams raw bytes (as originally received)
3. Chunk boundaries preserved (same boundaries as arrival from producer)
4. Client controls pacing by reading from socket
5. Server closes connection when time window complete

**Use Cases**:
- Laboratory device testing (feed raw bytes into test equipment)
- Parser validation (replay exact bytes to test new parser versions)
- Forensic analysis (reproduce exact device behavior)

**Requirements**:
- Raw bytes only (no framing, no JSON, no envelopes)
- Same chunk boundaries as original arrival
- Client-controlled pacing (server waits for client to read)
- Bit-for-bit fidelity

**Replay Safety**:
This endpoint is for TCP replay only. Commands sent during replay sessions are blocked from hardware execution (see Command Flow section).

**Alternative Approaches** *:
- **Continuation Tokens**: Watermark-based pulls with deterministic ordering
- **HTTP Streaming**: Chunked transfer encoding with raw bytes
- **File Export**: Download .bin file, replay with external tool

### Commands

#### POST /api/commands

Execute manifest-driven command (connectionless flow).

**Request**:
```json
{
  "entityId": "8220-F9P",
  "verb": "receiver.hotStart",
  "actionId": "hotStart",
  "params": {},
  "timestamp": "2026-01-25T12:34:56Z"
}
```

**Response (200 OK)**:
```json
{
  "status": "sent",
  "commandId": "cmd-uuid-123",
  "message": "Command receiver.hotStart sent to 8220-F9P"
}
```

**Field Definitions**:
- `entityId`: Target entity (from metadata)
- `verb`: Command verb (from manifest, e.g., "receiver.hotStart")
- `actionId`: Action identifier (from manifest, e.g., "hotStart")
- `params`: Command parameters (JSON object)
- `timestamp`: ISO 8601 timestamp (optional, defaults to now)

**Error Responses**:
- `400 Bad Request`: Missing required fields
- `403 Forbidden`: Command submitted during replay session (replay safety block)
- `404 Not Found`: Entity not found or metadata not cached
- `503 Service Unavailable`: Transport not available

**Connectionless Flow**:
1. novaCore receives POST
2. Looks up entity metadata (scopeId, systemId)
3. Generates `commandId` (UUID)
4. Stores in novaArchive commands table (audit trail, isReplay=false)
5. Publishes to transport: `command.{verb}.{entityId}` with `isReplay=false`
6. GEM receives, validates scopeId and isReplay flag
7. If isReplay=true, GEM rejects with error
8. If isReplay=false, GEM encodes command and sends to hardwareService
9. GEM publishes result to archive via transport
10. Archive updates commands table with result

**Replay Safety**:
Commands are blocked from hardware execution during replay via three layers:
1. **Client-Side**: Replay UI disables command buttons
2. **Producer** (GEM): Checks `isReplay` flag, rejects if true
3. **Archive**: Commands table has `isReplay` column (replay-tagged commands never reach hardware)

**UI Progress States**:
- **Sent**: Command accepted by novaArchive, stored in DB
- **Confirmed**: GEM received command via transport
- **Progress**: GEM sent to hardwareService, waiting for ACK
- **Result**: ACK/NAK received, final status published

#### POST /api/commands/upload-config

Upload configuration file to device.

**Request** (multipart/form-data):
```
file: <binary file>
entityId: "8220-F9P"
verb: "receiver.uploadConfig"
actionId: "uploadConfig"
timestamp: "2026-01-25T12:34:56Z"
```

**Response (200 OK)**:
```json
{
  "status": "sent",
  "commandId": "cmd-uuid-456",
  "message": "Config file \"receiver_config.txt\" uploaded and sent to 8220-F9P",
  "fileName": "receiver_config.txt",
  "fileSize": 2048
}
```

**Flow**:
1. Browser uploads file via multipart form
2. novaCore receives file, encodes as hex
3. Builds command envelope with `configData` (hex string)
4. Publishes to NATS: `command.{verb}.{entityId}`
5. GEM receives, decodes hex, applies config
6. GEM publishes progress updates (per-command status)
7. GEM publishes final result

### Metadata API (3rd-Party Integration)

#### POST /api/v1/metadata

Publish metadata for 3rd-party assets (HTTP alternative to NATS).

**Headers**:
```
Authorization: Bearer <token>
Content-Type: application/json
```

**Request**:
```json
{
  "assetId": "sensor-123",
  "scopeId": "ground",
  "systemId": "environmental",
  "systemDisplayName": "Environmental Sensors",
  "containerId": "weather-1",
  "containerDisplayName": "Weather Station 1",
  "name": "Temperature Sensor",
  "entityType": "sensor",
  "cardType": "sensor-card",
  "online": true,
  "attributes": {
    "manufacturer": "Acme Corp",
    "model": "TS-100"
  }
}
```

**Response (200 OK)**:
```json
{
  "success": true,
  "assetId": "sensor-123",
  "scopeId": "ground",
  "timestampMs": 1706188496789
}
```

**Override Behavior**:
If `overrides.json` contains entry for `assetId`, novaCore applies overrides before publishing to NATS.

**Flow**:
1. 3rd-party POSTs metadata
2. novaCore applies overrides (if configured)
3. novaCore publishes to NATS: `archive.ingest.ground.metadata.upsert`
4. novaArchive ingests and stores
5. Browser receives via WebSocket (if live) or HTTP (if replay)

#### GET /api/v1/metadata/{assetId}

Retrieve metadata for asset.

**Response (200 OK)**:
```json
{
  "assetId": "sensor-123",
  "metadata": {
    "systemId": "environmental",
    "containerId": "weather-1",
    "name": "Temperature Sensor",
    "entityType": "sensor",
    "online": true,
    "lastSeen": 1706188496789,
    "attributes": {}
  }
}
```

**Error Responses**:
- `404 Not Found`: Metadata not found

#### GET /api/v1/systems

List all systems with containers.

**Response (200 OK)**:
```json
{
  "systems": [
    {
      "systemId": "mission-alpha",
      "displayName": "Mission Alpha",
      "containers": [
        {
          "containerId": "payload-1",
          "displayName": "Payload 1",
          "assetCount": 5
        }
      ]
    }
  ],
  "count": 1
}
```

#### GET /api/v1/systems/{systemId}/containers

List containers in system.

**Response (200 OK)**:
```json
{
  "systemId": "mission-alpha",
  "containers": [
    {
      "containerId": "payload-1",
      "systemId": "mission-alpha",
      "displayName": "Payload 1",
      "assets": [
        {
          "assetId": "8220-F9P",
          "name": "ZED-F9P Receiver",
          "entityType": "gnss-receiver",
          "online": true
        }
      ]
    }
  ],
  "count": 1
}
```

### Entities

#### GET /api/entities

List all entities.

**Query Parameters**:
- `type` (optional): Filter by entityType

**Response (200 OK)**:
```json
[
  {
    "entityId": "8220-F9P",
    "assetId": "8220-F9P",
    "name": "ZED-F9P Receiver",
    "entityType": "gnss-receiver",
    "cardType": "gnss-card",
    "systemId": "mission-alpha",
    "containerId": "payload-1",
    "online": true
  }
]
```

#### GET /api/entities/{entityId}

Get entity details.

**Response (200 OK)**:
```json
{
  "entityId": "8220-F9P",
  "assetId": "8220-F9P",
  "name": "ZED-F9P Receiver",
  "entityType": "gnss-receiver",
  "cardType": "gnss-card",
  "systemId": "mission-alpha",
  "containerId": "payload-1",
  "online": true,
  "lastSeen": 1706188496789,
  "attributes": {
    "manufacturer": "u-blox",
    "model": "ZED-F9P"
  }
}
```

---

## WebSocket API

### Overview

WebSocket provides real-time live streaming from archive to browser.

**Endpoint**: `ws://localhost:8080/ws/live?token=<jwt>`

**Authentication**: JWT token as query parameter (if auth enabled).

**Protocol**: JSON text messages over WebSocket.

### Connection Flow

```
1. Browser opens WebSocket: ws://localhost:8080/ws/live?token=xyz
2. novaCore validates token (if auth enabled)
3. novaCore queries archive for initial snapshot
4. novaCore sends snapshot message
5. novaCore forwards live deltas from archive
6. Browser applies deltas to timeline store
```

### Message Types (Server → Client)

#### snapshot

Initial state on connect.

**Message**:
```json
{
  "type": "snapshot",
  "clientSessionId": "session-uuid",
  "timestamp": "2026-01-25T12:34:56.789Z",
  "entities": {
    "8220-F9P": {
      "assetId": "8220-F9P",
      "name": "ZED-F9P Receiver",
      "entityType": "gnss-receiver",
      ...metadata fields...
    }
  },
  "positions": {
    "8220-F9P": {
      "lat": 40.647002,
      "lon": -111.818352,
      "alt": 1354.2,
      "heading": 90.0,
      "timestamp": "2026-01-25T12:34:56.789Z"
    }
  },
  "tasks": {},
  "artifacts": {}
}
```

**Behavior**: Client clears timeline store and applies snapshot.

#### delta

Real-time update.

**Message (Position)**:
```json
{
  "type": "delta",
  "streamType": "position",
  "assetId": "8220-F9P",
  "timestampMs": 1706188496789,
  "patch": {
    "lat": 40.647003,
    "lon": -111.818353,
    "alt": 1354.3,
    "heading": 90.0,
    "speed": 0.5
  }
}
```

**Message (Metadata)**:
```json
{
  "type": "delta",
  "streamType": "entity.metadata",
  "assetId": "8220-F9P",
  "timestampMs": 1706188496789,
  "patch": {
    "name": "Primary GNSS Receiver",
    "online": true,
    "lastSeen": 1706188496789
  }
}
```

**Message (GNSS Signals)**:
```json
{
  "type": "delta",
  "streamType": "gnss-signals",
  "assetId": "8220-F9P",
  "timestampMs": 1706188496789,
  "patch": {
    "avgCn0": 42.5,
    "fixStatus": "3D_FIX",
    "fourthHighestCn0": 45.0,
    "constellations": [
      {"constellation": "GPS", "numTracked": 12, "numUsed": 10},
      {"constellation": "Galileo", "numTracked": 8, "numUsed": 6}
    ],
    "signals": [
      {"signal": "GPS L1CA", "numTracked": 8, "numUsed": 7},
      {"signal": "GPS L5", "numTracked": 8, "numUsed": 6}
    ],
    "totalTracked": 24
  }
}
```

**Behavior**: Client calls `timeline.applyDelta(msg)` to update timeline store.

#### error

Error notification.

**Message**:
```json
{
  "type": "error",
  "message": "Authentication required"
}
```

**Behavior**: Client closes WebSocket and shows error to user.

### Message Types (Client → Server)

#### timeUpdate (Deprecated)

**Note**: This message type is no longer required. Server does not track client time/mode state.

**Legacy Format** (for reference):
```json
{
  "type": "timeUpdate",
  "mode": "realtime",
  "time": 1706188496789,
  "speed": 1.0,
  "clientNow": 1706188496789
}
```

**Current Behavior**: Server ignores timeUpdate messages. Client manages its own time/mode state.

### Client Implementation

**Opening WebSocket** (Live Mode):
```javascript
const token = localStorage.getItem('authToken')
const ws = new WebSocket(`ws://localhost:8080/ws/live?token=${token}`)

ws.onopen = () => {
  console.log('WebSocket connected (live mode)')
}

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data)
  
  if (msg.type === 'snapshot') {
    timeline.clearStore()
    timeline.applySnapshot(msg)
  } else if (msg.type === 'delta') {
    timeline.applyDelta(msg)
  } else if (msg.type === 'error') {
    console.error('WebSocket error:', msg.message)
    ws.close()
  }
}

ws.onerror = (error) => {
  console.error('WebSocket error:', error)
}

ws.onclose = () => {
  console.log('WebSocket closed')
}
```

**Closing WebSocket** (Enter Playback Mode):
```javascript
// User pauses or drags timeline away from live
if (ws && ws.readyState === WebSocket.OPEN) {
  ws.close()
}

// Mode is now 'playback' (derived from WebSocket closed state)
// Client begins HTTP polling: GET /api/replay/snapshot, GET /api/replay/deltas
```

---

## NATS Subjects

### Overview

NATS subjects are internal to the NOVA system (not exposed to browser).

**Transport Abstraction**: All internal messaging uses **`/transport`** (from `/sdk`):
- **Local chains** (hardwareService → GEM): NNG IPC (nng+ipc:// scheme)
- **Network chains** (GEM → novaArchive, remote): NATS (nats:// scheme)
- **Future direction**: Move UI flows behind `/transport` over time

**Subject Hierarchy**:
- `stream.*` - Producer streams (GEM → Archive)
- `archive.*` - Archive republish (Archive → novaCore)
- `command.*` - Commands (novaCore → GEM)
- `event.*` - Low-volume events
- `hardwareService.*` - Hardware lifecycle (hardwareService → GEM)
- `device.*` - Raw device data (hardwareService → GEM)

### Producer Streams (GEM → Archive)

#### stream.raw.{scopeId}.{entityId}

Raw bytes for TCP replay.

**Publisher**: GEM  
**Subscriber**: novaArchive  
**Rate**: Native device rate (1-100 Hz)

**Payload**: Binary (raw protocol bytes)

#### stream.truth.{streamType}.{scopeId}.{entityId}

High-fidelity truth streams (native rate).

**Publisher**: GEM  
**Subscriber**: novaArchive  
**Rate**: Native device rate (10 Hz typical)

**Subject Examples**:
- `stream.truth.position.payload-1.8220-F9P`
- `stream.truth.gnss-signals.payload-1.8220-F9P`

**Payload** (JSON):
```json
{
  "assetId": "8220-F9P",
  "streamType": "position",
  "scopeId": "payload-1",
  "sequenceNum": 42,
  "timestampMs": 1706188496789,
  "patch": {
    "lat": 40.647002,
    "lon": -111.818352,
    "alt": 1354.2,
    "heading": 90.0
  },
  "version": 1
}
```

#### stream.ui.{streamType}.{scopeId}.{entityId}

UI-optimized streams (rate-limited).

**Publisher**: GEM  
**Subscriber**: novaArchive  
**Rate**: 1-2 Hz (rate-limited by GEM)

**Subject Examples**:
- `stream.ui.position.payload-1.8220-F9P`
- `stream.ui.gnss-signals.payload-1.8220-F9P`

**Payload**: Same as truth streams, but rate-limited.

### Archive Republish (Archive → novaCore)

#### archive.{scopeId}.ui.{streamType}

UI lane (low-rate republish for operators).

**Publisher**: novaArchive  
**Subscriber**: novaCore  
**Rate**: 1-2 Hz (from UI lane streams)

**Subject Examples**:
- `archive.payload-1.ui.position`
- `archive.payload-1.ui.entity.metadata`
- `archive.payload-1.ui.gnss-signals`

**Payload**: Same as producer streams (passthrough).

#### archive.{scopeId}.firehose.{streamType}

Firehose lane (full-rate for analysis tools).

**Publisher**: novaArchive  
**Subscriber**: PlotJuggler, analysis tools  
**Rate**: Native device rate (10-100 Hz)

**Subject Examples**:
- `archive.payload-1.firehose.position`
- `archive.payload-1.firehose.gnss-signals`

**Payload**: Same as producer streams (passthrough).

### Metadata Ingestion

#### archive.ingest.{scopeId}.metadata.upsert

Metadata upsert (change-only).

**Publisher**: GEM, novaCore (3rd-party HTTP)  
**Subscriber**: novaArchive  
**Rate**: On connect + on change

**Payload** (JSON):
```json
{
  "assetId": "8220-F9P",
  "scopeId": "payload-1",
  "timestampMs": 1706188496789,
  "metadata": {
    "systemId": "mission-alpha",
    "systemDisplayName": "Mission Alpha",
    "containerId": "payload-1",
    "containerDisplayName": "Payload 1",
    "name": "ZED-F9P Receiver",
    "entityType": "gnss-receiver",
    "cardType": "gnss-card",
    "online": true,
    "lastSeen": 1706188496789,
    "attributes": {
      "manufacturer": "u-blox",
      "model": "ZED-F9P"
    },
    "priority": 0,
    "source": "producer"
  }
}
```

**Priority Rules**:
- `0`: Producer (GEM) default
- `10`: Ground override (novaCore overrides.json)
- Rule: Higher priority wins; equal priority → latest wins

### Commands

#### command.{verb}.{entityId}

Manifest-driven command execution (connectionless flow).

**Publisher**: novaCore  
**Subscriber**: GEM  
**Rate**: On-demand (user action)

**Subject Examples**:
- `command.receiver.hotStart.8220-F9P`
- `command.receiver.coldStart.8220-F9P`
- `command.receiver.uploadConfig.8220-F9P`

**Payload** (JSON):
```json
{
  "commandId": "cmd-uuid-123",
  "entityId": "8220-F9P",
  "verb": "receiver.hotStart",
  "actionId": "hotStart",
  "params": {},
  "scopeId": "payload-1",
  "isReplay": false,
  "timestamp": "2026-01-25T12:34:56Z",
  "source": "novaCore"
}
```

**Replay Safety**:
- `isReplay=false`: Live command, GEM will execute on hardware
- `isReplay=true`: Replay-tagged command, GEM will reject with error

**Config Upload Payload**:
```json
{
  "commandId": "cmd-uuid-456",
  "entityId": "8220-F9P",
  "verb": "receiver.uploadConfig",
  "actionId": "uploadConfig",
  "fileName": "receiver_config.txt",
  "fileSize": 2048,
  "configData": "B562060400FFFF020010680A0D...",  // Hex string
  "scopeId": "payload-1",
  "isReplay": false,
  "timestamp": "2026-01-25T12:34:56Z",
  "source": "novaCore"
}
```

#### archive.ingest.{scopeId}.command.result

Command result (audit trail).

**Publisher**: GEM  
**Subscriber**: novaArchive  
**Rate**: Per command execution

**Payload** (JSON):
```json
{
  "commandId": "cmd-uuid-123",
  "entityId": "8220-F9P",
  "verb": "receiver.hotStart",
  "actionId": "hotStart",
  "status": "success",  // success, error, timeout
  "message": "Hot start executed",
  "timestampMs": 1706188500000,
  "source": "gem"
}
```

### Hardware Service (Internal)

#### hardwareService.events.{containerId}

Topology and lifecycle events.

**Publisher**: hardwareService  
**Subscriber**: GEM  
**Rate**: On device connect/disconnect

**Payload (Topology)**:
```json
{
  "event": "topology",
  "containerId": "Payload",
  "devices": [
    {
      "deviceId": "8220-F9P",
      "kind": "ubx",
      "subject": "device.raw.8220-F9P.ubx.serial"
    }
  ]
}
```

#### hardwareService.control.{containerId}

REQ/REP control channel.

**Publisher**: GEM (request)  
**Subscriber**: hardwareService (reply)  
**Pattern**: Request/Reply

**Request (Get Topology)**:
```json
{
  "command": "getTopology"
}
```

**Response**:
```json
{
  "event": "topology",
  "containerId": "Payload",
  "devices": [...]
}
```

**Request (Apply Config)**:
```json
{
  "command": "applyConfig",
  "deviceId": "8220-F9P",
  "configBytes": [0xB5, 0x62, ...],  // Byte array
  "label": "Hot Start"
}
```

**Response**:
```json
{
  "event": "configApplied",
  "status": "applied",
  "deviceId": "8220-F9P",
  "label": "Hot Start",
  "message": "Command executed successfully"
}
```

#### device.raw.{deviceId}.{kind}.{dataKind}

Raw device byte streams.

**Publisher**: hardwareService  
**Subscriber**: GEM  
**Rate**: Native device rate

**Subject Example**: `device.raw.8220-F9P.ubx.serial`

**Payload**: Binary (raw protocol bytes)

---

## Message Formats

### Standard Stream Envelope

All typed stream messages follow this format:

```json
{
  "assetId": "string",          // Entity identifier
  "patch": {},                  // Type-specific data
  "scopeId": "string",          // Scope identifier
  "sequenceNum": 0,             // Monotonic sequence per entity
  "streamType": "string",       // Message type
  "timestampMs": 0,             // Producer timestamp (auxiliary)
  "version": 1                  // Schema version
}
```

**Field Ordering**: Alphabetical (enforced via `json.dumps(data, sort_keys=True)`)

### StreamType Payloads

#### position

```json
{
  "patch": {
    "lat": 40.647002,      // degrees (WGS84), 6+ decimal precision
    "lon": -111.818352,    // degrees (WGS84), 6+ decimal precision
    "alt": 1354.2,         // meters above sea level
    "heading": 90.0,       // degrees (0-360, true north)
    "speed": 0.5           // m/s
  }
}
```

**Validation**:
- `lat`: [-90, 90]
- `lon`: [-180, 180]
- `alt`: [-1000, 50000]
- `heading`: [0, 360)
- `speed`: [0, ∞)

#### gnss-signals

```json
{
  "patch": {
    "avgCn0": 42.5,                    // Average CN0 across all signals
    "constellations": [                // Sorted alphabetically by constellation
      {
        "constellation": "BeiDou",
        "numTracked": 8,
        "numUsed": 6
      },
      {
        "constellation": "GPS",
        "numTracked": 12,
        "numUsed": 10
      }
    ],
    "fixStatus": "3D_FIX",             // NO_FIX, 2D_FIX, 3D_FIX, RTK_FLOAT, RTK_FIXED
    "fourthHighestCn0": 45.0,          // Fourth-highest CN0 value
    "signals": [                       // Sorted alphabetically by signal
      {
        "signal": "GPS L1CA",
        "numTracked": 8,
        "numUsed": 7
      },
      {
        "signal": "GPS L5",
        "numTracked": 8,
        "numUsed": 6
      }
    ],
    "totalTracked": 24
  }
}
```

**Required Fields** (GNSS Card Contract):
- `fixStatus`
- `constellations` (array, sorted)
- `signals` (array, sorted)
- `avgCn0`
- `fourthHighestCn0`

#### entity.metadata

```json
{
  "patch": {
    "assetId": "8220-F9P",
    "systemId": "mission-alpha",
    "systemDisplayName": "Mission Alpha",
    "containerId": "payload-1",
    "containerDisplayName": "Payload 1",
    "name": "ZED-F9P Receiver",
    "entityType": "gnss-receiver",
    "cardType": "gnss-card",
    "online": true,
    "lastSeen": 1706188496789,
    "attributes": {
      "manufacturer": "u-blox",
      "model": "ZED-F9P",
      "firmwareVersion": "1.32"
    },
    "priority": 0,
    "source": "producer"
  }
}
```

---

## Error Handling

### HTTP Status Codes

- `200 OK`: Successful request
- `201 Created`: Resource created
- `400 Bad Request`: Invalid request (missing fields, bad format)
- `401 Unauthorized`: Authentication required or invalid token
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server error
- `503 Service Unavailable`: Service temporarily unavailable

### Error Response Format

```json
{
  "error": "Error message describing what went wrong"
}
```

**Example**:
```json
{
  "error": "Missing required field: entityId"
}
```

### WebSocket Errors

**Authentication Error**:
```json
{
  "type": "error",
  "message": "Authentication required"
}
```

**Connection Closed**: WebSocket close code `1008` (Policy Violation) for auth errors.

---

## Rate Limiting

### Overview

Rate limiting is applied at multiple layers to prevent system overload.

### Producer Rate Limiting (GEM)

**UI Lane**: 1-2 Hz per entity per streamType
**Truth Lane**: Native device rate (no limiting)
**Raw Lane**: Native device rate (no limiting)

### Archive Republish Rate Limiting

**UI Lane**: Matches producer UI lane (1-2 Hz)
**Firehose Lane**: Matches producer truth lane (no limiting)

### HTTP API Rate Limiting

**Not Currently Implemented**: Future enhancement for multi-tenant deployments.

**Proposed Limits**:
- `/api/replay/*`: 10 requests/second per client
- `/api/commands`: 5 requests/second per client
- `/api/v1/metadata`: 20 requests/second per client

---

## Summary

This API reference covers:

✅ **Authentication**: JWT-based auth with role-based access  
✅ **HTTP REST API**: Config, snapshot, replay, commands, metadata, entities  
✅ **WebSocket API**: Live streaming, snapshot + delta protocol  
✅ **NATS Subjects**: Producer streams, archive republish, commands, hardware service  
✅ **Message Formats**: Standard envelope, streamType payloads  
✅ **Error Handling**: Status codes, error responses  
✅ **Rate Limiting**: Producer, archive, HTTP layers  

**Related Documents**:
- [nova architecture.md](nova%20architecture.md) - System architecture
- [gem architecture.md](gem%20architecture.md) - GEM implementation details

---

**Document Authority**: This is the master API reference. All client implementations must follow these specifications.
