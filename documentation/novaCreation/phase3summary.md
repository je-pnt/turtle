# Phase 3 Summary: Server Process and IPC

**Date**: January 26, 2026  
**Status**: ✅ COMPLETE  
**Architecture Reference**: nova architecture.md § 8 (Single Implementation Pattern)  
**Implementation**: Following guidelines.md (no shortcuts, explicit code, reuse from archive)

---

## Overview

Phase 3 implements the Server process for WebSocket edges and multiprocess IPC between Server ↔ Core. This establishes the stateless request/response architecture with ephemeral streaming cursors.

### Core Architecture Principles

1. **Stateless Server**: No persistent per-client session storage
2. **Ephemeral Streaming**: Cursors exist only during active stream, discarded on disconnect
3. **Fencing**: playbackRequestId prevents chunk interleaving after seek/rate changes
4. **Core Authority**: All validation, all DB access owned by Core
5. **One Way**: Single IPC mechanism (multiprocessing.Queue), no parallel paths

### Process Split

```
Main Process
├── Core Process
│   ├── Database (SQLite)
│   ├── Ingest Pipeline
│   ├── Transport Manager (sdk.transport subscriber - NATS binding)
│   ├── Streaming Manager (server-paced playback)
│   └── IPC Handler (receive requests → execute → send responses)
│
└── Server Process
    ├── aiohttp WebSocket Handler
    ├── Auth Manager (edge validation only - token generation/verification)
    ├── Client Connection Manager (ephemeral state per WebSocket)
    └── IPC Client (forward requests with connId → route responses)

Note: Auth is Server-edge responsibility only. Core receives connId and trusts Server's validation.

IPC Communication:
  Server → Core: QueryRequest, StreamRequest, CancelStreamRequest, CommandRequest
  Core → Server: QueryResponse, StreamChunk, StreamComplete, ErrorResponse
```

---

## File Structure

### New Files

```
nova/
  main.py                     # Entry point: spawns Core + Server subprocesses
  config.json                 # MODIFIED: Added server.host, server.port, server.auth
  
  core/
    contracts.py              # IPC request/response dataclasses with TimelineMode enum
    ipc.py                    # Core IPC handler: receives requests, executes, sends responses
    streaming.py              # StreamingManager: ephemeral cursors, server-paced playback
  
  server/
    __init__.py               # Package init
    server.py                 # NovaServer: aiohttp WebSocket handler, routing
    ipc.py                    # ServerIPCClient: sends requests, receives responses
    auth.py                   # AuthManager: stateless JWT token validation

test/
  nova/
    test_phase3.py            # Phase 3 integration tests (12 tests)
```

### Modified Files

- `nova/config.json`: Added `server` section with host, port, auth config

---

## Implementation Details

### 1. IPC Contracts (nova/core/contracts.py)

**Purpose**: Define request/response protocol for Server ↔ Core communication

**Key Types**:
- `TimelineMode` enum: `LIVE` | `REPLAY` (determines command blocking)
- Request types: `QueryRequest`, `StreamRequest`, `CancelStreamRequest`, `CommandRequest`, `DiscoverRequest` (stub), `GetUiStateRequest` (stub)
- Response types: `QueryResponse`, `StreamChunk`, `StreamComplete`, `ErrorResponse`, `AckResponse`
- Note: `DiscoverRequest` and `GetUiStateRequest` are contract definitions only in Phase 3; implementation deferred to Phase 4+

**Critical Fields**:
- `playbackRequestId`: Fence token for stream chunk discard (prevents interleaving)
- `clientConnId`: WebSocket connection ID (distinct from Raw lane's deviceConnectionId)
- `timelineMode`: Explicit LIVE or REPLAY for command blocking enforcement

**Architecture Compliance**:
- ✅ Explicit contracts (no inference)
- ✅ Deterministic serialization (orjson)
- ✅ No parallel paths (single IPC protocol)

---

### 2. Core Streaming (nova/core/streaming.py)

**Purpose**: Server-paced playback from truth database with ephemeral cursors

**Components**:
- `StreamCursor`: Ephemeral cursor for one active stream
  - Server-paced emission based on `rate` (1x, 2x, 0.5x, reverse)
  - Reads chunks from DB using `ordering.py` for determinism
  - Emits `StreamChunk` messages with `playbackRequestId` fence token
- `StreamingManager`: Manages active cursors per `clientConnId`
  - Creates cursor on `startStream`
  - Cancels existing stream before starting new one (automatic fence)
  - Discards cursor on `cancelStream` (ephemeral, no persistence)

**Server-Paced Algorithm**:
1. Read next chunk from DB (100 events, deterministically ordered)
2. Calculate timeline span: `timelineSpanUs = events[-1][timebase] - events[0][timebase]`
3. Calculate real-time delay: `delaySec = (timelineSpanUs / 1_000_000) / abs(rate)`
4. Emit chunk to queue
5. Sleep for `delaySec` (capped at 1 sec to prevent stalls)
6. Repeat until `stopTime` reached or canceled

**Architecture Compliance**:
- ✅ Stateless: cursors are ephemeral, discarded on disconnect
- ✅ Deterministic: uses `ordering.py` for event sequencing
- ✅ Fencing: `playbackRequestId` in every chunk
- ✅ No persistent state: no DB writes for cursor positions

---

### 3. Core IPC Handler (nova/core/ipc.py)

**Purpose**: Core-side IPC handler for receiving/executing requests

**Request Processing**:
- Receives serialized requests from `requestQueue` (multiprocessing.Queue)
- Parses request type and dispatches to handler:
  - `QUERY`: Bounded read from DB using `database.query()`
  - `START_STREAM`: Creates cursor via `StreamingManager.startStream()`
  - `CANCEL_STREAM`: Stops cursor via `StreamingManager.cancelStream()`
  - `SUBMIT_COMMAND`: Validates timelineMode, blocks if REPLAY (Phase 5 TODO: full lifecycle)
- Sends responses to `responseQueue`

**Stream Response Forwarding**:
- Background task checks all active stream queues (`asyncio.Queue` per connection)
- Forwards `StreamChunk` messages from cursors to IPC `responseQueue`
- Each chunk includes `clientConnId` for Server routing

**Architecture Compliance**:
- ✅ Core owns all DB access (single authority)
- ✅ Command blocking in REPLAY mode (defense in depth)
- ✅ No persistent session state (ephemeral queues)

---

### 4. Server IPC Client (nova/server/ipc.py)

**Purpose**: Server-side IPC client for forwarding requests to Core

**Key Methods**:
- `query()`: Send QueryRequest, wait for response (with 30s timeout)
- `startStream()`: Send StreamRequest, register chunk handler callback
- `cancelStream()`: Send CancelStreamRequest (fire-and-forget)
- `submitCommand()`: Send CommandRequest, wait for response (with 10s timeout)

**Response Routing**:
- Background task processes responses from `responseQueue`
- Routes by `requestId` to waiting futures (query, command responses)
- Routes by `clientConnId` to registered chunk handlers (stream chunks)

**Architecture Compliance**:
- ✅ Stateless: no session storage, only ephemeral callback registrations
- ✅ Timeout-based (no indefinite waits)
- ✅ Single IPC path (no parallel mechanisms)

---

### 5. Server Main (nova/server/server.py)

**Purpose**: WebSocket edge handler with aiohttp (reused from archive/failedNova/novaCore)

**Components**:
- `ClientConnection`: Ephemeral per-connection state
  - Tracks `activePlaybackId` for fence discard
  - Methods: `setActiveStream()`, `clearActiveStream()`, `shouldDiscardChunk()`
  - No persistent storage (exists only while WebSocket open)
- `NovaServer`: aiohttp application
  - Routes: `/ws` (WebSocket), `/health` (health check)
  - Auth: validates token per-connection (stateless JWT)
  - Message handlers: query, startStream, cancelStream, command

**WebSocket Message Flow (Updated in Phase 4 to message-based auth)**:
1. Client connects: `/ws` (no token in URL - prevents log leakage)
2. Client sends first message: `{type: 'auth', token: '<jwt>'}`
3. Server authenticates token via `AuthManager.validateToken()`
4. Server responds: `{type: 'authResponse', connId: '<uuid>'}`
5. Server creates `ClientConnection` (ephemeral)
6. Client sends JSON message: `{type: 'startStream', startTime, stopTime, rate, ...}`
7. Server generates `playbackRequestId` (UUID)
8. Server sets `conn.activePlaybackId` for fencing
9. Server registers chunk handler callback
10. Server forwards request to Core via `ipcClient.startStream()`
11. Core emits chunks → Server chunk handler → fence check → forward to WebSocket
12. Client sends `{type: 'cancelStream'}` → Server clears fence → Core stops cursor

**Note:** Phase 3 initially used query-param auth (`/ws?token=...`) but Phase 4 changed to message-based auth for security (tokens in URLs get logged). Architecture pattern is now message-based.

**Fence Discard Logic** (prevents interleaving):
```python
async def chunkHandler(chunk: Dict[str, Any]):
    chunkPlaybackId = chunk.get('playbackRequestId')
    if conn.shouldDiscardChunk(chunkPlaybackId):
        return  # Discard stale chunk
    await conn.sendMessage(chunk)
```

**Architecture Compliance**:
- ✅ Stateless: ephemeral connections only (no persistent sessions)
- ✅ Reuse: aiohttp patterns from archive (web.Application, web.WebSocketResponse)
- ✅ Explicit code: clear fence logic, no try/catch patches
- ✅ Command blocking: defense in depth (Server + Core both check timelineMode)

---

### 6. Auth Manager (nova/server/auth.py)

**Purpose**: Stateless JWT token validation with user allowlist

**Phase 3 Implementation**:
- `generateToken(username)`: Create JWT with userId, username, role, expiry
- `validateToken(token)`: Decode JWT, check expiry, verify user in allowlist
- `checkPermission(role, action)`: Simple role-based checks
  - admin: read, write, command, admin
  - operator: read, write, command
  - viewer: read

**Config**:
```json
{
  "auth": {
    "enabled": false,
    "secret": "dev-secret-change-in-production",
    "tokenExpirySeconds": 3600,
    "allowlist": {
      "admin": "admin",
      "operator": "operator",
      "viewer": "viewer"
    }
  }
}
```

**Architecture Compliance**:
- ✅ Stateless: no session storage, token contains all state
- ✅ Defense in depth: Server checks permissions before forwarding commands
- ✅ Phase 3 stub: ready for Phase 4 integration with real auth system

---

### 7. Main Entry Point (nova/main.py)

**Purpose**: Spawn Core and Server as cooperating subprocesses

**Process Flow**:
1. Load config from `--config` argument (default: `nova/config.json`)
2. Create IPC queues: `requestQueue`, `responseQueue` (multiprocessing.Queue)
3. Spawn Core process: `runCoreProcess(configPath, requestQueue, responseQueue)`
   - Initializes Database, IngestPipeline, TransportManager
   - Starts CoreIPCHandler event loop
4. Spawn Server process: `runServerProcess(configPath, requestQueue, responseQueue)`
   - Initializes NovaServer with aiohttp
   - Starts ServerIPCClient event loop
5. Wait for processes (join)
6. Handle Ctrl+C: terminate processes gracefully

**Usage**:
```bash
python nova/main.py [--config path/to/config.json]
```

**Architecture Compliance**:
- ✅ Single entry point (no parallel launchers)
- ✅ Clean shutdown (terminate → join with timeout)
- ✅ Multiprocess IPC (intra-service only, as per architecture)

---

### 8. Config Updates (nova/config.json)

**Added Section**:
```json
{
  "scopeId": "payload-local",
  "dbPath": "./nova/data/nova_truth.db",
  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "auth": {
      "enabled": false,
      "secret": "dev-secret-change-in-production",
      "tokenExpirySeconds": 3600,
      "allowlist": {
        "admin": "admin",
        "operator": "operator",
        "viewer": "viewer"
      }
    }
  }
}
```

**Notes**:
- Database files organized in `nova/data/` directory (prevents root clutter)
- Directory automatically created if missing (see nova/main.py)

---

## Test Coverage (test/nova/test_phase3.py)

### 13 Tests (all passing):

1. **IPC Query Roundtrip**: Validates Server → Core query request/response
2. **IPC Stream Fencing**: Validates playbackRequestId prevents interleaving
3. **Streaming Manager Ephemeral**: Validates cursors are ephemeral (no persistence)
4. **Auth Token Generation**: Validates JWT token creation
5. **Auth Token Validation Rejects Invalid**: Validates token rejection
6. **Auth Disabled**: Validates bypass when auth disabled
7. **Auth Permission Check**: Validates role-based permissions
8. **Command Blocked in REPLAY Mode**: Validates commands rejected in REPLAY
9. **Command Allowed in LIVE Mode**: Validates commands accepted in LIVE (stub)
10. **Server Stateless No Persistent Sessions**: Design validation (ephemeral state only)
11. **Stream Chunk Callback**: Validates chunk routing to correct connection
12. **Fence Discard After Seek**: Validates stale chunks discarded after seek
13. **End-to-End Producer to Server Stream**: Full integration test with HardwareService → Transport → Core → Server → Client (requires NATS)

### Test Execution:
```bash
cd c:\us\dev
pytest test/nova/test_phase3.py -v
```

---

## Dependencies

### New Dependencies (add to nova/requirements.txt):
```
aiohttp>=3.9.0
PyJWT>=2.8.0
orjson>=3.9.0
```

### Existing Dependencies:
- nats-py (Phase 2)
- pytest, pytest-asyncio (Phase 1)
- sqlite3 (builtin)

---

## Architecture Validation

### ✅ Stateless Server
- No persistent session storage in DB or files
- Ephemeral `ClientConnection` objects (exist only while WebSocket open)
- Auth tokens contain all state (JWT)

### ✅ Ephemeral Streaming
- Cursors created on `startStream`, destroyed on `cancelStream` or disconnect
- No cursor position stored in DB
- Restartable from any time T

### ✅ Fencing
- Every `StreamRequest` includes unique `playbackRequestId`
- Every `StreamChunk` echoes `playbackRequestId`
- Server discards chunks where `playbackRequestId != conn.activePlaybackId`
- Core cancels old cursor before starting new one (automatic fence)

### ✅ Core Authority
- Core owns all DB reads/writes
- Core validates all requests (timelineMode, filters, auth)
- Core blocks commands in REPLAY mode
- Server is thin edge (auth + routing only)

### ✅ One Way
- Single IPC mechanism: multiprocessing.Queue
- No parallel code paths for request/response
- No service-to-service HTTP (WebSocket only at UI edge)

### ✅ Reuse from Archive
- aiohttp patterns from archive/failedNova/novaCore/api/http/websocketHandlers.py
- web.Application, web.WebSocketResponse usage
- Message loop structure (async for msg in ws)
- NO reuse of broken architecture (stateful sessions, timeline reconstruction)

### ✅ Guidelines.md Compliance
- Fix root causes: IPC designed correctly (no try/catch patches)
- No parallel paths: single IPC mechanism
- Reuse: aiohttp from archive, ordering.py from Phase 1
- Explicit code: clear fence logic, deterministic contracts
- Minimal: no code slop, no regex catches, no ad-hoc fallbacks

---

## Exit Criteria

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Server process handles WebSocket connections | ✅ COMPLETE | nova/server/server.py with aiohttp |
| IPC mechanism between Server ↔ Core | ✅ COMPLETE | multiprocessing.Queue with contracts.py |
| Stateless request/response flow | ✅ COMPLETE | No persistent session storage, ephemeral state only |
| Stream fencing prevents interleaving | ✅ COMPLETE | playbackRequestId in StreamChunk, fence discard logic |
| Auth stub implemented | ✅ COMPLETE | JWT token validation with allowlist |
| Command blocking in REPLAY mode | ✅ COMPLETE | Server + Core both enforce (defense in depth) |
| Main entry point spawns subprocesses | ✅ COMPLETE | nova/main.py with multiprocessing.Process |
| Test coverage (≥10 tests) | ✅ COMPLETE | 13 tests in test_phase3.py (including end-to-end) |
| Documentation | ✅ COMPLETE | This document |
| Database organized in nova/data/ | ✅ COMPLETE | nova/data/nova_truth.db (not root clutter) |

---

## Known Limitations (Phase 3 Scope)

1. **No UI**: Phase 4 will add Web UI (timeline.js, websocket.js, display.js)
2. **Command Stub**: Phase 5 will implement full command lifecycle (validate, record, dispatch, progress, result)
3. **Discover/GetUiState Contracts Only**: Phase 3 defines IPC message shapes; Phase 4+ implements metadata resolution (streams, manifests, drivers as-of-T, UI checkpoint retrieval)
4. **Auth Stub**: Phase 3 uses simple allowlist; Phase 4+ may integrate real auth system
5. **No TCP Loopback**: Phase 8 will add Raw lane TCP replay (deferred from original Phase 3 scope for clarity)

---

## Next Steps (Phase 4)

1. Implement Web UI:
   - `nova/ui/index.html`: Main UI page
   - `nova/ui/timeline.js`: Timeline controls (play/pause, seek, rate, timebase)
   - `nova/ui/websocket.js`: WebSocket client
   - `nova/ui/display.js`: Event display
2. Add static file serving to Server
3. Implement timeline-aware command blocking in UI
4. Add seek/rate change testing with fence validation

---

## References

- **Architecture**: nova architecture.md § 7 (Timeline Truth), § 8 (Single Implementation Pattern)
- **Implementation Plan**: documentation/novaCreation/implementationPlan.md § Phase 3
- **Guidelines**: guidelines (implementation rules)
- **Archive Reference**: archive/failedNova/novaCore/api/http/websocketHandlers.py (aiohttp patterns)

---

## Summary

Phase 3 successfully implements the Server process and IPC layer with strict adherence to architecture invariants:

- **Stateless Server**: No persistent sessions, ephemeral state only
- **Fencing**: playbackRequestId prevents chunk interleaving after seek/rate changes
- **Core Authority**: All validation and DB access owned by Core
- **One Way**: Single IPC mechanism (multiprocessing.Queue)
- **Reuse**: aiohttp patterns from archive (no broken architecture)
- **Guidelines Compliance**: No shortcuts, explicit code, fix root causes
- **Database Organization**: nova/data/ directory (not root clutter)
- **End-to-End Tested**: Full Producer → Transport → Core → Server → Client flow validated

**All 13 tests passing. Phase 3 COMPLETE.** ✅
