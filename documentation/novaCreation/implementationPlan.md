# NOVA 2.0 Implementation Plan

**Date**: January 26, 2026  
**Architecture Reference**: nova architecture.md  
**Design Philosophy**: Zen of NOVA + SVS patterns (simplicity, inheritance, plugin architecture)

---

## Architecture Summary

NOVA is a **time-indexed truth system** delivering seamless live and replay operations through one unified interface. It consists of:

- **Core Process**: Sole owner/writer of truth database, applies deterministic ordering and dedupe, produces replay/live streams, runs file/export drivers
- **Server Process**: Edge handler for WebSocket (Web UI), TCP endpoints, authentication; forwards requests to Core via multiprocess-safe IPC

### Key Invariants
1. **Single Truth DB** per instance; ground mirrors all scopes append-only
2. **Deterministic Replay**: Same input → same output; fixed ordering with explicit tie-breaks
3. **Replay Safety**: Commands blocked during replay; no external side effects
4. **Stateless Server**: No persistent per-client session state; streaming is ephemeral/restartable
5. **Scope Authority**: Payload authoritative for its scope; ground adds canonicalTruthTime without overwriting sourceTruthTime
6. **One Way**: One inter-service transport (sdk.transport), one API, one ordering contract, one driver codepath. UI uses WebSocket at NOVA edge. Intra-service Server↔Core IPC/queues allowed.

### System Behavior
- **Ingest**: Producer → /transport → Core validates/dedupes/assigns time → append DB → trigger drivers
- **Query**: UI → Server → Core reads DB [T0..T1] with deterministic ordering
- **Stream**: UI → Server → Core streams from DB server-paced; seek/rate = cancel + restart
- **Commands (Live)**: UI → Server → Core records + dispatches → producer executes → results ingested → streamed to UI
- **Commands (Replay)**: Blocked at Server (UX) and Core (authority); not recorded as truth
- **Export**: Query [T0..T1] + same drivers as real-time → identical files
- **TCP**: Raw loopback data and LIVE command input/output only; no timeline control

---

## Phase 1: Core Database and Ingest Foundation

**Goal**: Build the truth database core with append-only ingest, eventId dedupe, ordering, and basic query capability.

### New/Changed Files
- `nova/core/__init__.py` - Package init
- `nova/core/database.py` - Truth database (SQLite/DuckDB) with time-indexed tables per lane
- `nova/core/ingest.py` - Ingest pipeline: validate, dedupe, assign canonicalTruthTime, append
- `nova/core/ordering.py` - Deterministic ordering rules (timebase → lane priority → within-lane)
- `nova/core/events.py` - Event envelope classes (RawFrame, ParsedMessage, UiUpdate, CommandRequest, etc.)
- `nova/core/query.py` - Bounded read implementation [T0..T1] with filters
- `nova/config.json` - Core configuration (scopeId, dbPath, timebaseDefault, transport)
- `nova/requirements.txt` - Dependencies

### Ordering Contract (Authoritative Reference)

**This ordering contract applies identically to Query, Stream, Export, and TCP loopback.**

| Layer | Rule |
|-------|------|
| Primary time | Selected timebase (canonicalTruthTime for ground default, sourceTruthTime for payload default; request may override) |
| Lane priority | Metadata → Command → UI → Parsed → Raw (when primary time ties) |
| Within-lane | All lanes: (timebase) then eventId. If sequence is present on Raw events, it can be used as an extra stable tie-break within the same entity, but is not required. |
| Final tie-break | EventId (lexicographic comparison of SHA256 hash) |

**Implementation**: `nova/core/ordering.py` defines the ordering rules as the single source of truth. For query/stream/export hot paths, ordering.py generates SQL ORDER BY clauses that DB executes using indexes. Python comparators are available for tests and rare cross-database operations. All query/stream/export/TCP paths MUST use this module.

**Test coverage**: Ordering tests MUST verify:
- Lane priority with timestamp collisions
- EventId tie-break determinism
- Same ordering across query/stream/export/TCP outputs

### Conceptual Changes
- Create truth database schema with tables: rawEvents, parsedEvents, uiEvents, commandEvents, metadataEvents
- **Per-lane table schemas**:
  - rawEvents: eventId (FK to eventIndex), scopeId, systemId, containerId, uniqueId, sourceTruthTime, canonicalTruthTime, **connectionId** (nullable, debug), **sequence** (nullable), bytes (BLOB)
  - parsedEvents: eventId (FK), scopeId, systemId, containerId, uniqueId, sourceTruthTime, canonicalTruthTime, messageType, payload (JSON)
  - uiEvents: eventId (FK), scopeId, systemId, containerId, uniqueId, sourceTruthTime, canonicalTruthTime, messageType, viewId, payload (JSON)
  - commandEvents: eventId (FK), scopeId, systemId, containerId, uniqueId, sourceTruthTime, canonicalTruthTime, messageType, commandId, payload (JSON)
  - metadataEvents: eventId (FK), scopeId, sourceTruthTime, canonicalTruthTime, systemId (nullable), containerId (nullable), uniqueId (nullable; `__scope__` for scope-global), **messageType** (indexed), payload (JSON)
    - Note: Scope-global metadata (like ChatMessage) uses uniqueId=`__scope__` sentinel
- **Raw lane ordering**: See "Ordering Contract (Authoritative Reference)" section above
  - connectionId + sequence are optional; if present, they can be used for stable tie-breaks within same entity
  - DB executes ordering via indexes per the ordering contract for queries/streams/exports
- **Global dedupe via eventIndex table**: single table with eventId (PK) for cross-lane/cross-scope dedupe; ingest checks here first
- **EventId is content-derived** (stable, deterministic hash for ingest idempotency + deterministic tie-break):
  - Purpose: enables idempotent republishing (same content = dedupe) and stable ordering tie-breaks
  - Construction: `SHA256(eidV1 + scopeId + lane + entityIdentityKey + sourceTruthTime + canonicalPayload)`
  - **EntityIdentityKey**: `systemId|containerId|uniqueId` (always, for all lanes)
  - **MessageIdentity**: `lane + messageType` (+ optional subKey like viewId, requestId)
  - **canonicalPayload serialization rules** (exact, to ensure stability):
    - For JSON lanes (Parsed, UI, Command, Metadata): UTF-8 encoded JSON with sorted object keys, no insignificant whitespace, **numbers per RFC 8785 JSON Canonicalization Scheme** (integers as integers, floats as shortest decimal representation that round-trips)
    - For Raw lane: hash the raw bytes directly (no JSON serialization)
  - **eventId MAY be provided by producer**; if missing, Core computes a stable eventId using canonicalized envelope (including scopeId/lane/entity ids/messageType/sourceTruthTime/payload hash). Core may validate producer-provided eventId but does not rewrite it.
  - **Note**: sourceTruthTime is included in EventId so dedupe/idempotency is defined by the producer's notion of "same event"; canonicalTruthTime is for ordering/timebase and does not replace dedupe
  - **Producer idempotency rule**: If re-sending the "same event" for catch-up/retry, producer must use the same sourceTruthTime (do not regenerate); this ensures same eventId for dedupe
  - Same event content = same eventId (idempotent)
  - Different event content = different eventId (unique)
- **Ingest flow** (eventId optional from producer, Core validates/computes and dedupes atomically):
  - Producer MAY compute eventId and include it in envelope; if missing, Core computes it
  - Producer MAY omit systemId/containerId/uniqueId in envelope if encoded in subject (or configured per connection). Core derives them.
  - **Conflict rejection**: If envelope provides IDs and they conflict with subject/config-derived values, Core **rejects** (does not silently overwrite)
  - Core validates required fields (scopeId, lane, sourceTruthTime; entity IDs from envelope or derived)
  - **Atomic dedupe + insert**: Core inserts into eventIndex AND lane table in a single DB transaction
    - On duplicate eventId: transaction fails, event is dropped (dedupe)
    - On success: both tables updated atomically
    - No orphaned eventIndex rows or inconsistent state
  - Core assigns canonicalTruthTime as wall-clock receive time (used for unified ordering; does NOT replace eventId dedupe)
- Single dedupe point: eventIndex table only; no other dedupe mechanisms
- **Ordering module**: Implements the ordering contract defined in "Ordering Contract (Authoritative Reference)" section above
  - ordering.py generates SQL ORDER BY clauses for DB execution in query/stream/export hot paths
  - DB maintains indexes on (timebase, lane, eventId) and (connectionId, sequence) for efficient ordered queries
  - Python comparators available for tests and rare non-SQL operations
  - All query/stream/export/TCP paths must use nova/core/ordering.py
  - **Metadata lane reminder**: Must include time-versioning events (ManifestPublished, DriverBinding, ProducerDescriptor) to enable "as-of T" resolution for manifests and drivers
- **Replay produces no external effects (hard rule)**: fileWriter must never run on replay/query/stream; only on ingest of producer truth
- Query returns ordered results for bounded time windows
- No file writes or drivers yet; focus on DB correctness

### Phase 1 Exit Criteria
- Database schema created with eventIndex + per-lane tables (all lanes include systemId, containerId, uniqueId)
- DB indexes created for efficient ordering: (timebase, lane, eventId)
- EventId hash construction implemented and tested (same content → same hash)
- Global dedupe proven: inserting duplicate eventId fails at eventIndex
- **Atomic dedupe test**: duplicate eventId insertion fails with no orphaned eventIndex rows; DB transaction rollback verified
- **Ordering SQL test**: ordering.py generates correct ORDER BY clauses; DB queries return deterministically ordered results
- **Replay no-fileWriter test**: query/stream paths never invoke fileWriter (hard prohibition verified)

### File Structure After Phase 1
```
nova/
  config.json                  # Core config (scopeId, dbPath, ingest rules)
  requirements.txt             # Python deps (asyncio, sqlite/duckdb, sdk.transport deps)
  core/
    __init__.py
    database.py                # Truth DB implementation
    ingest.py                  # Ingest pipeline
    ordering.py                # Ordering rules
    events.py                  # Event envelopes/schemas
    query.py                   # Bounded read queries
```

---

## Phase 2: Transport Integration and Producer Adapter

**Goal**: Connect Core to /transport, subscribe to scoped events, and retrofit hardwareService to publish NOVA-compliant events.

### New/Changed Files
- `nova/core/transportManager.py` - Transport subscription manager (uses sdk.transport), forwards to ingest
- `sdk/hardwareService/novaAdapter.py` - Plugin that publishes Raw/Parsed/Metadata via /transport with scopeId
- `sdk/hardwareService/hardwareService.py` - Modified to load novaAdapter
- `sdk/hardwareService/config.json` - Add scopeId configuration

### Conceptual Changes
- Core uses sdk.transport (abstraction layer; NATS is one implementation detail)
- **Public transport address format**: `nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v1` is a public contract documented in `subjects.py` for non-SDK producers. NOVA may derive missing IDs from subject; if envelope provides conflicting IDs, NOVA rejects.
- Transport event routing by: scopeId + lane + entity identity (sdk.transport maps public format to backend-specific addressing)
- Core subscribes to its scopeId filter (or all scopes for ground mode) via sdk.transport
- **Producer Truth Envelope v1** (defined in nova/core/events.py):
  - **Exact envelope schemas (JSON)**:
    - **RawFrame**: `{"eventId": "sha256Hash" (optional), "scopeId": "string", "systemId": "string", "containerId": "string", "uniqueId": "string", "lane": "raw", "sourceTruthTime": "ISO8601", "connectionId": "string" (optional debug), "sequence": uint64 (optional), "bytes": "base64EncodedBytes"}`
    - **ParsedMessage**: `{"eventId": "sha256Hash" (optional), "scopeId": "string", "systemId": "string", "containerId": "string", "uniqueId": "string", "lane": "parsed", "sourceTruthTime": "ISO8601", "messageType": "string", "schemaVersion": "string", "payload": {...}}`
    - **UiUpdate**: `{"eventId": "sha256Hash" (optional), "scopeId": "string", "systemId": "string", "containerId": "string", "uniqueId": "string", "lane": "ui", "sourceTruthTime": "ISO8601", "messageType": "UiUpdate", "viewId": "string", "manifestId": "string", "manifestVersion": "string", "data": {...}}`
    - **CommandRequest**: `{"eventId": "sha256Hash" (optional), "scopeId": "string", "systemId": "string", "containerId": "string", "uniqueId": "string", "lane": "command", "sourceTruthTime": "ISO8601", "messageType": "CommandRequest", "commandId": "string", "requestId": "string", "targetId": "string", "commandType": "string", "timelineMode": "LIVE|REPLAY", "payload": {...}}`
    - **ProducerDescriptor** (Metadata lane): `{"eventId": "sha256Hash" (optional), "scopeId": "string", "systemId": "string", "containerId": "string", "uniqueId": "string", "lane": "metadata", "sourceTruthTime": "ISO8601", "messageType": "ProducerDescriptor", "capabilities": [...], "schemaVersion": "string", "effectiveTime": "ISO8601"}`
    - **ChatMessage** (Metadata lane, scope-global): `{"eventId": "sha256Hash" (optional), "scopeId": "string", "uniqueId": "__scope__", "lane": "metadata", "sourceTruthTime": "ISO8601", "messageType": "ChatMessage", "authorDisplayName": "string", "content": "string"}`
  - **Entity identity**: All envelopes include `systemId`, `containerId`, `uniqueId` (or derive from subject)
  - **ID derivation rule**: Producers may omit entity IDs in envelope if encoded in subject. Core derives missing IDs; if envelope provides IDs and they conflict with subject/config, Core rejects (no silent mutation).
  - Required fields: scopeId, lane, sourceTruthTime, messageType (for non-Raw), payload/bytes
  - eventId is optional; if missing, Core computes stable eventId from canonicalized envelope
  - **EventId construction** (optional producer responsibility; Core computes if missing):
    - Producer MAY compute eventId before publishing; if missing, Core computes stable hash
    - Hash: `SHA256(eidV1 + scopeId + lane + systemId|containerId|uniqueId + messageType + sourceTruthTime + canonicalPayload)`
    - **canonicalPayload serialization** (exact rules for stability):
      - JSON lanes: UTF-8 encoded JSON, sorted object keys, no whitespace, **numbers per RFC 8785 JSON Canonicalization Scheme** (integers as integers, floats as shortest decimal that round-trips)
      - Raw lane: hash raw bytes directly
    - **Note**: sourceTruthTime is included in EventId so dedupe/idempotency is defined by the producer's notion of "same event"; canonicalTruthTime is for ordering/timebase and does not replace dedupe
    - **Producer idempotency rule**: When re-sending the "same event" for catch-up/retry, producer must reuse the original sourceTruthTime (do not regenerate); ensures same eventId for dedupe
  - eventId must be stable (same content = same eventId) and unique (different content = different eventId)
  - sourceTruthTime assigned once at producer, never overwritten
- hardwareService novaAdapter alignment:
  - Populates scopeId from config (required)
  - Computes eventId using SHA256 hash over canonical fields (producer responsibility)
  - Core validates eventId but does not recompute or rewrite it
  - Sets sourceTruthTime once at publish time (wall-clock)
  - Wraps device plugin outputs into NOVA event envelopes (Raw, Parsed, Metadata lanes)
  - Publishes via sdk.transport.publish() only (routing: scopeId, lane, identity)
  - Never publishes directly to transport implementation (no NATS imports in hardwareService)
  - Wraps device plugin outputs into NOVA event envelopes (Raw, Parsed, Metadata lanes)
  - Publishes via sdk.transport.publish() only (routing: scopeId, lane, identity)
  - Never publishes directly to transport implementation (no NATS imports in hardwareService)
- Core transport module receives events via sdk.transport callbacks, validates envelope structure (required fields present), forwards to ingest
- Ground/Archive mode: subscribe to all scopes via broader transport filter, apply eventId dedupe via eventIndex table
- **Phase 2 Exit Criteria**: 
  - hardwareService emits valid NOVA truth envelopes with content-derived eventId
  - Core ingests and stores them via eventIndex dedupe
  - Conformance test: same event content published twice = only one DB entry (dedupe works)
  - Conformance test: different event content = different eventId (uniqueness works)

### File Structure After Phase 2
```
nova/
  config.json
  requirements.txt
  core/
    __init__.py
    database.py
    ingest.py
    ordering.py
    events.py
    query.py
    transportManager.py       # NEW: Transport subscription via sdk.transport

sdk/
  hardwareService/
    config.json               # MODIFIED: Add scopeId
    hardwareService.py        # MODIFIED: Load novaAdapter
    novaAdapter.py            # NEW: NOVA publisher plugin (uses sdk.transport)
```

---

## Phase 3: Server Process and IPC

**Goal**: Create Server process for WebSocket edge, implement multiprocess IPC with Core, and establish stateless request/response flow. TCP loopback deferred to Phase 8.

### New/Changed Files
- `nova/server/__init__.py` - Package init
- `nova/server/server.py` - Server main loop, WebSocket handler (TCP deferred to Phase 8)
- `nova/server/ipc.py` - IPC mechanism (multiprocessing.Queue or zmq) for Server ↔ Core
- `nova/server/auth.py` - Authentication/authorization (stateless tokens, user allowlist)
- `nova/core/ipc.py` - Core-side IPC handlers (receive requests, send responses)
- `nova/core/streaming.py` - Stream playback: server-paced read from DB, ephemeral cursor
- `nova/main.py` - Application entry point: spawn Core and Server as subprocesses
- `nova/config.json` - Add Server config (ports, auth, IPC path)

### Conceptual Changes
- Server process handles:
  - WebSocket connections from Web UI (auth, message routing)
  - Forward requests to Core via IPC: Query, StartStream, StopStream, SubmitCommand
  - Note: TCP loopback (Raw lane replay) deferred to Phase 8
- Core process handles:
  - Receive IPC requests from Server
  - Execute queries (bounded read)
  - Execute streams (server-paced playback with ephemeral cursor)
  - Send results back via IPC
- IPC contract:
  - Request types: 
    - QueryRequest(startTime, stopTime, filters, timelineMode)
    - StreamRequest(startTime, stopTime?, rate, timebase, timelineMode, **playbackRequestId**, **clientConnId**)
    - CancelStreamRequest(**clientConnId**)
    - CommandRequest(timelineMode)
    - **DiscoverRequest(time=T, filters)** → descriptors[] (contract defined Phase 3, implemented Phase 4+)
    - **GetUiStateRequest(time=T, systemId, containerId, uniqueId, viewId)** → UiCheckpoint + subsequent UiUpdates (contract defined Phase 3, implemented Phase 4+)
  - Response types: 
    - QueryResponse(events[])
    - StreamChunk(events[], **playbackRequestId**)
    - StreamComplete(**playbackRequestId**)
    - ErrorResponse
  - timelineMode: explicit enum (LIVE or REPLAY) determines command blocking and side-effect gating
  - **playbackRequestId**: unique ID per StartStream request for fencing; Server discards chunks with stale playbackRequestId
  - **clientConnId**: WebSocket client connection ID (distinct from Raw lane's connectionId for device connections)
- **"As-of T" resolution rule** (metadata state at time T):
  - State resolution uses **effectiveTime** field (from metadata events like ManifestPublished, DriverBinding, ProducerDescriptor)
  - **Timebase determines effectiveTime evaluation**:
    - Canonical timebase (default for ground): effectiveTime ≤ T evaluated using canonicalTruthTime
    - Source timebase (default for payload): effectiveTime ≤ T evaluated using sourceTruthTime
    - Metadata events missing required timebase are ineligible for that query
  - If effectiveTime is absent, fallback to the event's timestamp in the selected timebase
  - UiCheckpoint selection: find latest checkpoint where effectiveTime ≤ T in selected timebase
  - Driver/Manifest binding: use binding where effectiveTime ≤ T in selected timebase
  - No timebase mixing within one query/stream/export
- **Stream restart fencing** (prevents interleaving):
  - Every StartStream includes unique playbackRequestId
  - Every StreamChunk echoes playbackRequestId
  - Server discards chunks where playbackRequestId != current active ID for that connection
  - Core stops producing chunks for canceled requests ASAP; fence is hard guarantee at Server
- Streaming state is per-connection, held only in Core while connection is open; a new StreamRequest cancels prior stream for that connection
- No persistent per-client state stored in DB or config

### File Structure After Phase 3
```
nova/
  main.py                     # NEW: Spawn Core + Server subprocesses
  config.json                 # MODIFIED: Add server ports, auth, IPC
  requirements.txt
  core/
    __init__.py
    database.py
    ingest.py
    ordering.py
    events.py
    query.py
    transportManager.py
    ipc.py                    # NEW: Core IPC handler
    streaming.py              # NEW: Stream playback with cursor
  server/
    __init__.py               # NEW
    server.py                 # NEW: Main server loop
    ipc.py                    # NEW: Server IPC client
    auth.py                   # NEW: Auth/AuthZ
```

---

## Phase 4: Web UI and Timeline Control

**Goal**: Implement Web UI with timeline controls (cursor, rate, seek), WebSocket client, and basic data display.

### New/Changed Files
- `nova/server/websocket.py` - WebSocket handler (message parsing, connection handling - ephemeral only)
- `nova/ui/index.html` - Main UI page
- `nova/ui/timeline.js` - Timeline controls (play/pause, seek, rate, timebase selection)
- `nova/ui/websocket.js` - WebSocket client (connect, send requests, handle stream chunks)
- `nova/ui/display.js` - Data display (events list, basic telemetry)
- `nova/ui/auth.js` - Login/logout, token management
- `nova/ui/styles.css` - UI styling
- `nova/server/static.py` - Serve static UI files
- `nova/config.json` - Add UI config (default rate, timebase)

### Conceptual Changes
- **Server endpoints**: WebSocket at `/ws` for all timeline/data operations (query, stream, command). HTTP endpoints permitted for: (1) auth bootstrap via `/auth/login` to acquire JWT token before WebSocket connection, and (2) static file hosting for UI assets. Truth ingest remains exclusively via /transport; HTTP is NOT an ingest API.
- Web UI connects to Server WebSocket message handler with auth token
- Timeline controls:
  - Cursor: datetime picker + slider
  - Rate: 0.1x, 1x, 2x, 10x, forward/reverse
  - Timebase: Source or Canonical
  - Mode selector: LIVE (follow latest) or REPLAY (fixed window)
  - Play/Pause: issues StartStream or CancelStream with explicit timelineMode
  - Seek: issues new StartStream with new cursor
- UI maintains client-side timeline state (cursor, rate, playing, timelineMode)
- WebSocket receives StreamChunk messages, displays events in order
- No client-side reordering or timeline reconstruction; UI trusts Server ordering
- Command blocking: UI disables command buttons when timelineMode=REPLAY; all requests include timelineMode for Server/Core enforcement
- **No persistent session state**: Server maintains ephemeral per-connection state (active stream cursor) only while connection is open; state is discarded on disconnect

### File Structure After Phase 4
```
nova/
  main.py
  config.json                 # MODIFIED: Add UI defaults
  requirements.txt
  core/                       # (unchanged)
  server/
    __init__.py
    server.py                 # MODIFIED: Add static file serving
    ipc.py
    auth.py
    websocket.py              # NEW: WebSocket handler
    static.py                 # NEW: Serve UI files
  ui/
    index.html                # NEW
    timeline.js               # NEW: Timeline controls
    websocket.js              # NEW: WebSocket client
    display.js                # NEW: Data display
    auth.js                   # NEW: Login/logout
    styles.css                # NEW
```

---

## Phase 5: Command Plane (Live and Replay Safety)

**Goal**: Implement full command lifecycle (request, dispatch, progress, result) with replay blocking at Server and Core.

### New/Changed Files
- `nova/core/commands.py` - Command lifecycle: validate, record request, dispatch via sdk.transport (live only), ingest progress/result
- `nova/core/replay.py` - TimelineMode enforcement (LIVE vs REPLAY)
- `nova/server/commands.py` - Server-side authorization and request forwarding only
- `nova/ui/commands.js` - Command UI (buttons, forms, timeline-aware blocking)
- `sdk/hardwareService/commandHandler.py` - Receive and execute commands (existing pattern from SVS)
- `sdk/hardwareService/novaAdapter.py` - Modified to publish CommandProgress and CommandResult

### Conceptual Changes
- UI submits CommandRequest with commandId, targetId (deviceId/streamId), commandType, payload, timelineMode
- Core is the sole authority for command validation:
  - Validates command against manifests/descriptors (target exists, command type valid)
  - **Command idempotency**: enforce unique requestId on CommandRequest
  - If requestId already exists: skip recording and dispatch, return idempotent ACK
  - If requestId is new: record CommandRequest in DB (append-only truth) before any dispatch
  - **LIVE-only dispatch**: publish to NATS only when timelineMode=LIVE
  - **Ingest CommandProgress / CommandResult if received** (optional from producers)
  - Stream command events to UI in deterministic order
- Server responsibilities:
  - Authorization check (user role permits this command?)
  - Reject if timelineMode != LIVE (UI should already block)
  - **Server never orchestrates lifecycle; it only forwards allowed requests**
- Producer contract (minimal, optional):
  - Producer MAY subscribe to command subjects and execute
  - Producer MAY publish CommandProgress and/or CommandResult for richer feedback
  - Producer is not required to respond
  - Producer does not need to understand timelineMode (NOVA concern; NOVA never dispatches during replay)
- UI behavior:
  - Renders "sent" immediately after ACK (request is recorded + dispatched)
  - If progress events exist, show them
  - If a result exists, show terminal status
  - If no result exists, status remains "sent" indefinitely (no synthetic timeout)
- Producer contract (minimal, optional):
  - Producer MAY subscribe to command subjects and execute
  - Producer MAY publish CommandProgress and/or CommandResult for richer feedback
  - Producer is not required to respond
  - Producer does not need to understand timelineMode (NOVA concern; NOVA never dispatches during replay)
- UI behavior:
  - Renders "sent" immediately after ACK (request is recorded + dispatched)
  - If progress events exist, show them
  - If a result exists, show terminal status
  - If no result exists, status remains "sent" indefinitely (no synthetic timeout)
- **Command correlation contract**:
  - **commandId** correlates request/progress/result (all three events share same commandId)
  - **requestId** only present on CommandRequest (for idempotency, prevents duplicate submissions)
  - CommandProgress and CommandResult use commandId for lifecycle correlation
  - Database enforces: UNIQUE(requestId) WHERE messageType='CommandRequest' (partial unique index)
- **Command atomicity contract**:
  - Record-before-dispatch is mandatory: CommandRequest recorded in DB before dispatch
  - If dispatch fails (transport error, validation failure), record CommandResult with failure status
  - Never dispatch without a recorded CommandRequest event
  - Core validates → records → dispatches (sequential); Server only does authz and forwarding

### File Structure After Phase 5
```
nova/
  main.py
  config.json
  requirements.txt
  core/
    __init__.py
    database.py
    ingest.py
    ordering.py
    events.py
    query.py
    transportManager.py
    ipc.py
    streaming.py
    commands.py               # NEW: Command lifecycle
    replay.py                 # NEW: TimelineMode enforcement
  server/
    __init__.py
    server.py
    ipc.py
    auth.py
    websocket.py
    static.py
    commands.py               # NEW: Command validation + blocking
  ui/
    index.html                # MODIFIED: Add command UI
    timeline.js
    websocket.js
    display.js
    auth.js
    commands.js               # NEW: Command submission
    styles.css

sdk/
  hardwareService/
    novaAdapter.py            # MODIFIED: Publish command results
    commandHandler.py         # NEW: Execute commands
```

### Command Envelope Contract (Public Integration API)
- **Command Envelope v1** (defined in nova/core/events.py):
  - CommandRequest: commandId, requestId (idempotency), scopeId, targetId, commandType, payload, timelineMode
  - CommandProgress: **optional** - commandId, scopeId, targetId, commandType, progressPercent, message
  - CommandResult: **optional** - commandId, scopeId, targetId, commandType, status (success/failure), result, errorMessage
  - If a producer publishes any response, it must include commandId for correlation
- **Transport subjects are public integration contract**:
  - Command dispatch: `nova.{scopeId}.command.{requestId}.v1`
  - Progress/Result publish: `nova.{scopeId}.command.{commandId}:{messageType}.v1`
  - Non-SDK producers MAY subscribe and publish if they conform to envelope schemas
- Example producer integration (hardwareService with novaAdapter):
  - Subscribes to command subjects via sdk.transport
  - Validates commandType and targetId against local device registry
  - Optionally publishes CommandProgress for live operator feedback
  - Optionally publishes CommandResult for explicit completion status

### Phase 5 Exit Criteria (API v1 Lock)
- **Conformance tests pass**:
  - Core records CommandRequest before dispatch; dispatch only in LIVE
  - Command idempotency: submitting same requestId twice yields one DB entry, one dispatch, idempotent ACK
  - Replay blocks at Server and Core; no NATS publish in replay
  - Producers may be silent; if a producer publishes result/progress, Core records and UI displays it
  - Export of command timeline matches live ingest (same ordering, same events)
- **DB constraints**: commandEvents table has partial UNIQUE constraint: `UNIQUE(requestId) WHERE messageType='CommandRequest'`
- **API v1 frozen**: truth envelope + command envelope schemas locked; changes require explicit versioning

---

## Phase 6: Drivers and Export Parity

**Goal**: Implement driver plugin system for file/folder writes and exports, ensuring real-time and replay use the same codepath.

### New/Changed Files
- `nova/core/drivers/__init__.py` - Driver plugin loader
- `nova/core/drivers/base.py` - Abstract base driver class
- `nova/core/drivers/rawBinary.py` - Raw lane → raw.bin files (preserve byte boundaries)
- `nova/core/drivers/positionCsv.py` - Position messageType → llas.csv files
- `nova/core/drivers/registry.py` - Driver selection logic (deterministic)
- `nova/core/fileWriter.py` - Real-time file writing from ingest (async, non-blocking)
- `nova/core/export.py` - Export execution: query + driver pipeline
- `nova/server/server.py` - Export request handling (via WebSocket message)
- `nova/ui/js/export.js` - Export UI (time range picker, trigger export, download link)
- `nova/config.json` - Add driver config (output paths, driver mappings)

### Conceptual Changes
- Driver plugin architecture (inspired by hardwareService and SVS):
  - Each driver declares driverId, version, supported (lane, streamType, schemaVersion)
  - Base class defines interface: `write(event) -> filePath`, `finalize() -> None`
  - Drivers inherit from base, implement lane-specific logic
- Core loads drivers on startup, builds registry
- Driver selection:
  - Inputs: lane, streamType, schemaVersion, optional capabilities
  - Deterministic selection (same inputs → same driver)
  - Emit DriverBinding metadata event: targetId → driverId/version with effectiveTime
- Real-time file writing:
  - Ingest triggers fileWriter after DB append (non-blocking)
  - **fileWriter runs only on ingest of producer truth; replay/query/stream never call fileWriter**
  - fileWriter uses DriverBinding-at-time(T) to select driver
  - Driver writes event to daily file (e.g., `/data/2026-01-26/stream123.csv`)
  - File writing failures are logged but do not block DB ingest (DB is primary truth)
- Export execution:
  - Query [startTime..stopTime] (bounded read from DB)
  - For each event, use DriverBinding-at-time(event.time) to select driver
  - Driver writes event to export folder (e.g., `/exports/export-abc/stream123.csv`)
  - Zip export folder, return download link
  - Export is an explicit user action, not automatic file writing
- Parity guarantee: same driver code, same DriverBinding resolution, same ordering → identical files (export matches what was written in real-time)

### Phase 6 Exit Criteria
- **Export parity test**: export [T0..T1] produces output that matches the ordered DB window query for the same interval
  - Golden fixture: ingest known events → export [T0..T1] → compare to query [T0..T1] output (byte-for-byte match)
  - Test proves: drivers produce identical output regardless of when they run (real-time vs export)
- Driver registry loads plugins correctly and selects deterministically
- Real-time fileWriter triggered on ingest (not on replay/query)

### File Structure After Phase 6
```
nova/
  main.py
  config.json                 # MODIFIED: Add driver config
  requirements.txt
  core/
    __init__.py
    database.py
    ingest.py                 # MODIFIED: Trigger fileWriter
    ordering.py
    events.py
    query.py
    transportManager.py
    ipc.py
    streaming.py
    commands.py
    replay.py
    fileWriter.py             # NEW: Real-time file writing
    export.py                 # NEW: Export execution
    drivers/
      __init__.py             # NEW: Plugin loader
      base.py                 # NEW: Base driver class
      rawBinary.py            # NEW: Raw → raw.bin
      positionCsv.py          # NEW: Position → llas.csv
      registry.py             # NEW: Driver selection
  server/
    __init__.py
    server.py                 # MODIFIED: Export message handling
    ipc.py
    auth.py
    websocket.py
    static.py
    commands.py
  ui/
    index.html                # MODIFIED: Add export UI
    timeline.js
    websocket.js
    display.js
    auth.js
    commands.js
    js/
      export.js               # NEW: Export controls
    styles.css
```

---

## Phase 7: UI Plane (Manifests, UiUpdate, UiCheckpoint)

**Goal**: Implement manifest-driven UI updates, UiCheckpoint generation for fast seek, and time-versioned UI state.

**Key Constraint**: The Web UI renders **only** from UiUpdate/UiCheckpoint events (UI lane) and Metadata (Descriptors). Raw and Parsed lanes are **not** streamed to the UI—producers must transform telemetry into UiUpdate events for display.

### New/Changed Files
- `nova/core/manifests/__init__.py` - Manifest registry
- `nova/core/manifests/base.py` - Base manifest class (defines viewId, allowed keys, version)
- `nova/core/manifests/telemetry.py` - Example telemetry manifest
- `nova/core/manifests/registry.py` - Load manifests, emit ManifestPublished events
- `nova/core/uiState.py` - UiCheckpoint generation logic (periodic, on discovery)
- `nova/core/ingest.py` - Modified to trigger UiCheckpoint generation
- `nova/ui/manifests.js` - Manifest-driven rendering (cards, shields)
- `nova/ui/display.js` - Modified to render UiUpdate using manifests
- `sdk/hardwareService/novaAdapter.py` - Modified to publish UiUpdate events

### Conceptual Changes
- Manifests are NOVA-owned, define UI semantics:
  - viewId: unique view identifier (e.g., "telemetry.gps")
  - manifestVersion: semantic versioning
  - allowedKeys: dict of key → type/validation rules
  - layout: optional card/shield arrangement hints
- Manifest registry loads manifests on startup, emits ManifestPublished metadata events (timestamped)
- Producers publish UiUpdate events:
  - Partial upsert: {systemId, containerId, uniqueId, viewId, manifestId, manifestVersion, data: {key1: val1, ...}}
  - Data keys must match manifest's allowedKeys
- UiCheckpoint generation (Core-owned):
  - **Bucketed by timeline time**: checkpoints at 60-minute boundaries of sourceTruthTime (floor to hour)
  - Key: (identity, viewId, manifestVersion, bucketStart) — at most one checkpoint per bucket
  - On first discovery: checkpoint time = bucketStart of first UiUpdate's sourceTruthTime (not wall-clock)
  - **Deterministic**: identical data → identical checkpoint positions (pure function of timeline time)
  - Full-state snapshot: {systemId, containerId, uniqueId, viewId, manifestId, manifestVersion, data: {...}}
- UI state-at-time(T) query:
  - Find latest UiCheckpoint ≤ T
  - Apply subsequent UiUpdate upserts ≤ T in order
  - Result: complete UI state at T
- Web UI uses manifests to render state (cards, values, colors from manifest definitions)

### File Structure After Phase 7
```
nova/
  main.py
  config.json                 # MODIFIED: Add manifest config
  requirements.txt
  core/
    __init__.py
    database.py
    ingest.py                 # MODIFIED: Trigger UiCheckpoint
    ordering.py
    events.py
    query.py
    transportManager.py
    ipc.py
    streaming.py
    commands.py
    replay.py
    fileWriter.py
    export.py
    uiState.py                # NEW: UiCheckpoint generation
    drivers/                  # (unchanged)
    manifests/
      __init__.py             # NEW
      base.py                 # NEW: Base manifest
      telemetry.py            # NEW: Example manifest
      registry.py             # NEW: Load manifests
  server/                     # (unchanged)
  ui/
    index.html                # MODIFIED: Manifest-driven layout
    timeline.js
    websocket.js
    display.js                # MODIFIED: Render UiUpdate via manifests
    auth.js
    commands.js
    export.js
    manifests.js              # NEW: Manifest rendering
    styles.css

sdk/
  hardwareService/
    novaAdapter.py            # MODIFIED: Publish UiUpdate
```

---

## Phase 8: Manifest-Driven Cards (COMPLETE)

**Goal**: Make card selection fully manifest-driven with deterministic discovery.

### Manifest Discovery (Determinism)
- **Deterministic discovery**: core scans `nova/core/manifests/*.manifest.py` in **sorted filename order**.
- **Manifest object contract**: each file exports a required `MANIFEST` object (CardManifest).
- **Collision policy**: if multiple manifests claim the same `entityType`, **fail fast** at startup with explicit error (no last-wins).
- **Default manifest**: if no manifest exists for an `entityType`, render the `default-card` manifest.
- **Fully manifest-driven**: no hardcoded `entityType` lists in cards/shields selection.

### Phase 8 Exit Criteria (DONE)
- ✅ Manifest discovery is deterministic (sorted import order)
- ✅ Duplicate entityType collision causes startup failure  
- ✅ Card selection has zero hardcoded entityType lists
- ✅ 20 Phase 8 tests passing

---

## Phase 8.1: TCP Stream-Out

**Goal**: Implement TCP stream-out as an output mechanism for external integrators. Streams are first-class output forks with shields/cards, but definitions are operational config (not truth).

### Architecture Principles

#### What TCP Streams Are
- **Output forks**: They read from truth and emit bytes/JSON to external clients.
- **NOT truth sources**: Stream definitions and sessions are operational config, not observed reality.
- **Replay-safe**: Serving replay data over TCP is allowed (per architecture clarification). No hardware/C2 effects.

#### What Gets Persisted Where
| Data | Storage | Rationale |
|------|---------|----------|
| Stream definitions (name, port, selection, format, createdBy, visibility, enabled) | `nova/data/streams.db` (SQLite config DB) | Operational config, not observed truth. Survives restarts. |
| Stream sessions, TCP connections, buffers, bindings | Memory only (ephemeral) | Per-architecture: no persistent per-client state. |
| Truth events (raw/parsed/ui/commands) | `nova/data/nova_truth.db` | Observed reality. Never polluted with stream config. |

### Binding Model (Timeline Control)

| Mode | Behavior | WebSocket Required? |
|------|----------|---------------------|
| LIVE-follow (default) | Stream tails real-time data | No |
| Timeline-tied | Stream follows bound WebSocket cursor (replay/seek/rate) | Yes |

**Binding rules**:
- Default: LIVE-follow with no timeline owner.
- User can enable "Tie to my timeline" to bind stream to their WebSocket instance.
- **Last-binder-wins**: only that instance's cursor controls output.
- If bound instance disconnects: stream immediately falls back to LIVE-follow.
- New instance does NOT auto-bind; user must explicitly rebind.

### Output Formats

| Format | Output | Constraint |
|--------|--------|------------|
| `payloadOnly` | Raw bytes (raw lane) or JSON payload only (other lanes) | Selection must resolve to single identity. UI warns/blocks on multi-identity. |
| `hierarchyPerMessage` | `{"s":"...","c":"...","u":"...","t":"...","p":{...}}` | Multi-identity safe. One JSON object per message. |

### Backpressure
- **Default (catchUp)**: Buffer full → drop queued data → resume from cursor. No markers in TCP stream.
- **Optional (disconnect)**: Disconnect on overflow (Phase 9).

### Validation Rules
- **payloadOnly multi-identity**: Validate at create/edit time. If selection resolves to multiple identities, warn/block.
- **No tcp-of-tcp**: Block stream sources from selecting TCP stream outputs as inputs (prevents recursion).

### Shield/Card Structure

#### Setup Streams Shield (System Entity)
- **Identity**: `systemId=tcpStream`, `containerId=system`, `uniqueId=setupStreams`
- **EntityType**: `setup-streams`
- **Purpose**: Always visible. Opens Setup Streams card.
- **Note**: Uses `systemId=tcpStream` (not `nova`) to satisfy Phase 7 shield rule.

#### Setup Streams Card
- **Create Stream section**: Name, Port, Lane + filters (selection), Output format, Create button
- **Existing Streams list**: Table with name, port, status, selection summary, format. Actions: Open, Delete

#### Per-Stream Shield
- **Identity**: `systemId=tcpStream`, `containerId=streams`, `uniqueId=<streamId>`
- **EntityType**: `tcp-stream`
- **At-a-glance fields**: name, port, mode (LIVE/bound), format, selection summary

#### Per-Stream Card (Editor + Controller)
- **Definition (persisted)**: Name, Port, Selection (lane + filters), Output format, Backpressure policy
- **Runtime controls (not persisted)**: Enabled/Disabled toggle, "Tie to my timeline" toggle
- **Status (read-only)**: Connection count, bound instance info
- **Actions**: Start/Stop, Delete

### Stream Discovery (UI)

Stream shields come from API, not truth events:
1. UI calls `GET /api/streams` or sends WS `listStreams` message
2. Response includes stream definitions with `entityType: tcp-stream`
3. Manifest lookup works normally: `tcp-stream` → `tcp-stream-card`
4. Setup Streams is always present with `entityType: setup-streams`

### New/Changed Files
- `nova/data/streams.db` - SQLite config DB for stream definitions (NOT truth)
- `nova/server/streamStore.py` - Stream definition CRUD (SQLite backend)
- `nova/server/tcp.py` - TCP server with LIVE-follow default + timeline binding
- `nova/server/server.py` - Stream API endpoints, binding logic
- `nova/core/manifests/setupStreams.manifest.py` - Setup Streams card manifest
- `nova/core/manifests/tcpStream.manifest.py` - Updated with selection/format/binding fields
- DELETE: `nova/server/streamEntities.py` - Remove truth-publishing logic

### Phase 8.1 Exit Criteria
- [ ] Setup Streams shield exists; can create/delete stream definitions
- [ ] Stream definitions persist in `streams.db` (not truth DB)
- [ ] LIVE-follow works with no WebSocket
- [ ] Timeline binding works (last-binder-wins, fallback on disconnect)
- [ ] Two output formats work: `payloadOnly` and `hierarchyPerMessage`
- [ ] payloadOnly warns/blocks on multi-identity selection
- [ ] No tcp-of-tcp selection allowed
- [ ] Backpressure catch-up works
- [ ] Stream definitions include `createdBy` + `visibility` fields (Phase 9 ready)
- [ ] Server restart: definitions survive, connections don't

---

## Phase 9: Admin and Authentication (Inspired by Old NOVA)

**Goal**: Implement admin-managed user allowlist, credential management, and audit logging, reusing old NOVA patterns without bloat.

### New/Changed Files
- `nova/server/admin.py` - Admin message handling (create/disable users, reset credentials, view audit)
- `nova/core/audit.py` - Audit event logging (user actions, auth events)
- `nova/ui/admin.html` - Admin UI page
- `nova/ui/admin.js` - Admin controls (user management)
- `nova/config.json` - Add admin config (initial admin credentials, token settings)
- `nova/server/auth.py` - Modified to use user allowlist, stateless tokens (JWT)

### Conceptual Changes
- Authentication:
  - Users stored in DB (username, hashed password, role, enabled flag)
  - Stateless JWT tokens issued on login (expiration, role claims)
  - WebSocket connections validate token on connect and per-request
- Authorization:
  - Roles: admin, operator, viewer
  - Admin: full access (user management, all commands, exports)
  - Operator: commands, exports, no user management
  - Viewer: read-only (query/stream, no commands/exports/admin)
- Admin features:
  - Create user (username, password, role)
  - Disable/enable user
  - Reset password
  - View audit log (login events, command submissions, export requests)
- Audit events:
  - UserLogin, UserLogout, CommandSubmitted, ExportRequested, AdminAction
  - Stored as metadata events in DB (queryable by timeline)
- Old NOVA patterns:
  - WebSocket auth: token in initial message, validated per connection
  - Admin page: simple HTML form, no framework bloat
  - Password hashing: bcrypt or argon2

### File Structure After Phase 9
```
nova/
  main.py
  config.json                 # MODIFIED: Add admin config
  requirements.txt
  core/
    __init__.py
    database.py               # MODIFIED: Add users table
    ingest.py
    ordering.py
    events.py
    query.py
    transportManager.py
    ipc.py
    streaming.py
    commands.py
    replay.py
    fileWriter.py
    export.py
    uiState.py
    audit.py                  # NEW: Audit logging
    drivers/
    manifests/
  server/
    __init__.py
    server.py
    ipc.py
    auth.py                   # MODIFIED: User allowlist + JWT
    websocket.py              # MODIFIED: Token validation
    static.py
    commands.py               # MODIFIED: Role-based access
    exports.py                # MODIFIED: Role-based access
    tcp.py
    tcpSession.py
    admin.py                  # NEW: Admin message handling
  ui/
    index.html                # MODIFIED: Add admin link
    timeline.js
    websocket.js
    display.js
    auth.js                   # MODIFIED: Token handling
    commands.js
    export.js
    manifests.js
    tcp.js
    admin.html                # NEW: Admin page
    admin.js                  # NEW: Admin controls
    styles.css
```

---

## Phase 10: Ground/Archive Mode with Broader Subscriptions

**Goal**: Enable Ground/Archive NOVA to subscribe to all scopes via /transport filters, apply eventId dedupe, and add canonicalTruthTime during normal ingest.

### New/Changed Files
- `nova/core/database.py` - Modified to store both sourceTruthTime and canonicalTruthTime
- `nova/core/ingest.py` - Modified to add canonicalTruthTime during ingest (always, for all modes)
- `nova/core/ordering.py` - Modified to support timebase selection (Source or Canonical)
- `nova/config.json` - Add mode config (payload or ground) controlling transport subscription scope
- `nova/core/transportManager.py` - Modified to configure subscription filters based on mode

### Conceptual Changes
- NOVA modes (config-driven):
  - Payload mode: subscribe to own scopeId only via transport filter
  - Ground/Archive mode: subscribe to all scopes via broader transport filter (e.g., wildcard or all-scopes subscription)
- Ground is not a separate subsystem; it's the same NOVA Core with different transport subscription configuration
- Ingest behavior (same for both modes):
  - Receive events via sdk.transport callbacks
  - Dedupe by eventId (ignore duplicates; first-seen wins)
  - Add canonicalTruthTime as wall-clock receive timestamp at this NOVA instance
  - Never overwrite sourceTruthTime or any producer-authored fields
  - Append to DB with both time fields
- Timebase selection:
  - Query/Stream requests specify timebase: Source or Canonical
  - Payload UI defaults to Source
  - Ground UI defaults to Canonical
  - Ordering uses selected timebase as primary sort key
  - **Ordering is deterministic per DB instance and chosen timebase**; cross-instance ordering may differ when using canonicalTruthTime (ground assigns it at ingest time, which varies by instance)
- Cross-scope visibility:
  - Ground sees events from all scopes because its transport subscription is broader
  - Payload sees only its own scope because its transport subscription is filtered
  - No special "sync" or "replication" code path; it's just /transport + ingest + dedupe
- Catch-up mechanism (if needed):
  - Producers re-emit the same events (same eventId) over /transport
  - NOVA dedupe handles idempotency (first-seen wins)
  - No separate sync channel; catch-up uses the same /transport publish mechanism
  - NOVA provides no NOVA-to-NOVA sync protocol

### File Structure After Phase 10
```
nova/
  main.py
  config.json                 # MODIFIED: Add mode (payload/ground) for transport filters
  requirements.txt
  core/
    __init__.py
    database.py               # MODIFIED: Store both time fields
    ingest.py                 # MODIFIED: Always add canonicalTruthTime during ingest
    ordering.py               # MODIFIED: Timebase selection in queries/streams
    events.py
    query.py
    transportManager.py       # MODIFIED: Configure subscription filters by mode
    ipc.py
    streaming.py
    commands.py
    replay.py
    fileWriter.py
    export.py
    uiState.py
    audit.py
    drivers/
    manifests/
  server/                     # (unchanged)
  ui/
    timeline.js               # MODIFIED: Timebase selector
```

---

## Phase 11: Optimization and Production Readiness

**Goal**: Performance tuning, monitoring, health status queries, and production deployment preparation.

### New/Changed Files
- `nova/core/monitoring.py` - Metrics collection (ingest rate, query latency, DB size)
- `nova/server/health.py` - Health status query handler (via internal IPC or WebSocket message)
- `nova/core/database.py` - Modified for indexing, query optimization
- `nova/core/streaming.py` - Modified for batching, rate limiting
- `nova/main.py` - Modified for graceful shutdown, restart handling
- `nova/config.json` - Add performance tuning config (batch sizes, cache sizes)
- `nova/logging.py` - Structured logging with context (eventId, scopeId, requestId)
- `nova/install.sh` - Installation script
- `nova/start.sh` - Startup script
- `nova/requirements.txt` - Pin versions
- `documentation/novaCreation/deployment.md` - Deployment guide

### Conceptual Changes
- Performance optimizations:
  - DB indexing on (scopeId, lane, time) for fast queries
  - Streaming batch size tuning (e.g., send 100 events per WebSocket message)
  - Rate limiting for query/export requests
  - Transport connection pooling (implementation-specific)
- Monitoring:
  - Metrics: events ingested/sec, queries/sec, active streams, DB size
  - Expose via health status (JSON via WebSocket message or internal IPC) or Prometheus format
- Health checks:
  - Core process alive, DB accessible, transport connected
  - Server process alive, Core IPC responsive
- Graceful shutdown:
  - Cancel active streams
  - Flush DB writes
  - Close transport connections
  - Wait for in-flight requests
- Logging:
  - Structured JSON logs with context (eventId, scopeId, userId, requestId)
  - Log rotation, compression
  - Integration with external log aggregators (optional)
- Deployment:
  - Systemd service files (Linux)
  - Docker containers (optional)
  - Configuration management (env vars, config files)

### File Structure After Phase 11
```
nova/
  main.py                     # MODIFIED: Graceful shutdown
  config.json                 # MODIFIED: Performance tuning
  requirements.txt            # MODIFIED: Pin versions
  install.sh                  # NEW
  start.sh                    # NEW
  logging.py                  # NEW: Structured logging
  core/
    __init__.py
    database.py               # MODIFIED: Indexing + optimization
    ingest.py
    ordering.py
    events.py
    query.py
    transportManager.py
    ipc.py
    streaming.py              # MODIFIED: Batching + rate limiting
    commands.py
    replay.py
    fileWriter.py
    export.py
    uiState.py
    audit.py
    monitoring.py             # NEW: Metrics collection
    drivers/
    manifests/
  server/
    __init__.py
    server.py
    ipc.py
    auth.py
    websocket.py
    static.py
    commands.py
    exports.py
    tcp.py
    tcpSession.py
    admin.py
    health.py                 # NEW: Health status handler
  ui/                         # (unchanged)

documentation/
  novaCreation/
    implementationPlan.md     # This file
    deployment.md             # NEW: Deployment guide
```

---

## Phase 12: Testing and Validation

**Goal**: Comprehensive testing of all flows (ingest, query, stream, export, commands, replay), validation against architecture invariants.

### New/Changed Files
- `test/nova/test_ingest.py` - Ingest pipeline tests (validation, dedupe, ordering)
- `test/nova/test_query.py` - Query correctness tests (bounded reads, filters)
- `test/nova/test_streaming.py` - Stream playback tests (cursor, rate, seek, cancel)
- `test/nova/test_commands.py` - Command lifecycle tests (live + replay blocking)
- `test/nova/test_export.py` - Export parity tests (real-time vs replay files)
- `test/nova/test_ordering.py` - Ordering determinism tests (tie-breaks, lane priority)
- `test/nova/test_ground_mode.py` - Ground mode tests (broader subscriptions, dedupe, canonicalTruthTime)
- `test/nova/test_drivers.py` - Driver tests (selection, file writing)
- `test/nova/test_manifests.py` - Manifest tests (UiUpdate, UiCheckpoint, state-at-time)
- `test/nova/test_integration.py` - End-to-end integration tests (producer → NOVA → UI)
- `test/nova/fixtures/` - Test fixtures (sample events, configs)

### Conceptual Changes
- Unit tests:
  - Each module (ingest, query, streaming, etc.) has isolated tests
  - Mock DB, transport, IPC for unit tests
- Integration tests:
  - Spawn real NOVA processes (Core + Server)
  - Use test producer (publish sample events)
  - Validate UI receives correct ordered stream
  - Validate exports match real-time files
- Determinism validation:
  - Run same ingest twice, verify identical DB state
  - Run same query twice, verify identical results
  - Run same export twice, verify identical files (byte-for-byte)
- Replay safety validation:
  - Issue command during replay, verify blocked at Server and Core
  - Verify no external side effects (mock producer command handler)
- Performance benchmarks:
  - Ingest throughput (events/sec)
  - Query latency (P50, P95, P99)
  - Stream latency (time from ingest to UI display)

### File Structure After Phase 12
```
nova/                         # (unchanged from Phase 11)

test/
  nova/
    test_ingest.py            # NEW
    test_query.py             # NEW
    test_streaming.py         # NEW
    test_commands.py          # NEW
    test_export.py            # NEW
    test_ordering.py          # NEW
    test_replication.py       # NEW
    test_drivers.py           # NEW
    test_manifests.py         # NEW
    test_integration.py       # NEW
    fixtures/                 # NEW: Test data
      sample_events.json
      test_config.json
```

---

## Summary

This phased plan builds NOVA 2.0 incrementally, ensuring each phase is complete and testable before moving forward. The architecture is implemented as written, with no alternative paths or workarounds. The design follows SVS patterns (simplicity, inheritance, plugin architecture) and the Zen of NOVA (stateless, single truth, one way, explicit boundaries).

### Key Milestones
- **Phase 1-2**: Core truth database + /transport integration (foundational)
- **Phase 3-4**: Server process + Web UI (user-facing MVP)
- **Phase 5**: Command plane (live + replay safety)
- **Phase 6-7**: Drivers + manifests (full feature parity)
- **Phase 8-9**: TCP + admin (external integrations + security)
- **Phase 10**: Ground mode (broader subscriptions, no sync subsystem)
- **Phase 11-12**: Production readiness + validation

### Design Principles Applied
- **One proxy**: Server is the sole edge; Core is the sole DB owner
- **Stateless**: No persistent per-client state
- **Single truth**: One DB, append-only, deterministic
- **One command path**: Commands via /transport, blocked explicitly in replay mode
- **Explicit boundaries**: Core ↔ Server via IPC; Producer ↔ NOVA via /transport abstraction
- **Fewer managers**: Simple Core/Server split, no session managers, no sync subsystem
- **Delete old code**: hardwareService retrofit removes GEM dependencies
- **/transport abstraction**: Architecture uses sdk.transport boundary; implementations are pluggable
- **Core authority**: Core is the sole validator and recorder for commands before dispatch; Server only does authz

---

## Transport Implementation Notes

The architecture specifies sdk.transport as the boundary. The initial implementation uses **NATS** as the transport layer:

- **NATS binding**: sdk.transport wraps nats-py for publish/subscribe semantics
- **Subject routing**: NOVA defines a public transport address format `nova.{scopeId}.{lane}.{identity}.v{version}` so non-SDK producers can publish/subscribe. NATS uses this format as subjects; other backends (Kafka, Redis) map it to their addressing schemes
- **Subscription filters**: Ground mode uses wildcard subscriptions (`nova.*`), payload mode uses scoped filters (`nova.{scopeId}.*`)
- **Dependencies**: nats-py is listed in sdk.transport requirements, not in NOVA Core requirements
- **Pluggability**: Future transports (Kafka, Redis Streams, etc.) can be added by implementing the sdk.transport interface without changing NOVA Core

**Key architectural rule**: NOVA Core code must never import or reference NATS directly. All transport operations go through sdk.transport methods.

---

The plan is ready for implementation. Each phase is self-contained and advances toward the complete NOVA 2.0 architecture.


**recall: Phase 1 says “fileWriter must never run on replay/query/stream” — that’s correct, but make sure you don’t accidentally interpret that as “drivers never run on export”. Exports must use drivers later (Phase 6). So: ingest triggers daily file writing; export triggers driver-based export; replay/query/stream never trigger file writing.

EventId stability depends on strict canonical JSON (RFC 8785 / JCS). If you don’t actually implement true JCS (especially number formatting), you’ll get cross-language mismatches and dedupe/order instability. Either implement real JCS or constrain payload numbers tightly.
