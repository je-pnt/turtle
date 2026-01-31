# Phases 1-5 Summary

**Status**: ✅ All Phase 1-5 goals complete and verified  
**Test Results**: 21/21 tests passing  
**Date**: 2026-01-28

---

## Overview

This document summarizes the current state of the NOVA codebase after completing Phases 1-5 of the implementation plan. All core goals are implemented and verified via a comprehensive test suite.

---

## Architecture Contract Summary

### Identity Model (Universal for ALL Lanes)

Per `nova architecture.md` Section 3:

```
Public identity: scopeId + lane + systemId + containerId + uniqueId
```

- **systemId**: Data system that produced the truth (e.g., `hardwareService`, `nova`)
- **containerId**: Node/payload/site/vehicle instance (e.g., `node1`, `payloadA`)
- **uniqueId**: Renderable entity identifier within system+container (e.g., `gps1`, `rxA`)

**messageType** is the lane-internal message identity (required for non-Raw lanes):
- Parsed: `gnss.navPvt`, `nmea.gga`, etc.
- Metadata: `ProducerDescriptor`, `StreamDescriptor`, etc.
- Command: `CommandRequest`, `CommandProgress`, `CommandResult`

**Optional debug fields** (NOT part of identity):
- `connectionId`: TCP/serial source (Raw lane only)
- `sequence`: Frame sequence (Raw lane only)

### Subject Contract (Canonical Format)

```
nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{schemaVersion}
```

This applies to ALL lanes uniformly, including commands:
- Command lane: `systemId=nova` for requests (NOVA dispatches), `systemId=producer` for progress/result
- Routing selectors (targetId, commandType) stay in envelope, not subject path

### EventId Contract

- Content-derived SHA256 hash (internal to NOVA for dedupe/idempotency)
- Hash includes: `eidV1 + scopeId + lane + entityIdentityKey + sourceTruthTime + canonicalPayload`
- **Core computes eventId if producer omits it**
- If producer provides eventId, Core validates (warning on mismatch but accepts)

### Ordering Contract

```
Primary: timebase (source or canonical)
Secondary: lane_priority (Metadata=0 → Command=1 → UI=2 → Parsed=3 → Raw=4)
Tertiary: eventId (lexicographic tiebreak)
```

---

## Core Package Structure (`nova/core/`)

| File | Lines | Purpose |
|------|-------|---------|
| `database.py` | 776 | SQLite storage with per-lane tables, eventIndex dedupe, cross-lane ordering |
| `events.py` | ~400 | Event dataclasses (RawFrame, ParsedMessage, UiUpdate, CommandRequest/Progress/Result, MetadataEvent), Lane/Timebase enums |
| `ingest.py` | ~300 | Event validation and atomic insertion, eventId verification |
| `query.py` | ~150 | Bounded read queries with identity filters and ordering |
| `ordering.py` | 160 | SQL ORDER BY generation, Python comparators, LANE_PRIORITY |
| `contract.py` | 150 | Architecture constants (LANE_PRIORITY, required fields) |
| `subjects.py` | 268 | Subject formatting/parsing, RouteKey construction |
| `ipc.py` | 308 | Core IPC handlers for Server requests |
| `streaming.py` | ~200 | Server-paced stream playback |
| `commands.py` | 208 | Command lifecycle (validate → record → dispatch) |
| `transportManager.py` | ~320 | Transport subscription management |
| `canonical_json.py` | ~100 | RFC 8785 JCS serialization for eventId stability |

---

## Phase-by-Phase Implementation Status

### Phase 1: Core Database and Ingest Foundation ✅

**Goal**: Create foundation with SQLite database, eventIndex dedupe, and per-lane tables.

**Implemented**:
- `database.py`: Schema with eventIndex + per-lane tables
- `ingest.py`: Atomic dedupe via eventIndex UNIQUE constraint
- `events.py`: Event dataclasses with identity model

**Verified Tests**:
- `test_identity_model_in_raw_event` - Raw events use correct identity
- `test_identity_model_in_parsed_event` - Parsed events use correct identity
- `test_eventid_content_derived_determinism` - Same content → same hash
- `test_eventid_different_content_different_hash` - Different content → different hash
- `test_global_dedupe_via_eventindex` - Duplicate eventId deduped
- `test_required_fields_validation` - Required fields enforced

---

### Phase 2: Transport Integration ✅

**Goal**: Producer envelopes with content-derived eventId, novaAdapter alignment.

**Implemented**:
- `novaAdapter.py`: Wraps hardwareService outputs into NOVA envelopes
- `subjects.py`: Subject formatting (`nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{version}`)
- `transportManager.py`: Transport subscription via sdk.transport

**Verified Tests**:
- `test_entity_identity_key_construction` - entityIdentityKey = systemId|containerId|uniqueId
- `test_eventid_uses_entity_identity_key` - EventId hash includes identity
- `test_optional_debug_fields_not_in_identity` - connectionId/sequence are optional

**novaAdapter Methods** (uniqueId = renderable entity):
- `publishRaw(deviceId, sequence, rawBytes)` - uniqueId = deviceId
- `publishParsed(deviceId, streamId, streamType, payload)` - uniqueId = deviceId, messageType = streamType
- `publishMetadata(messageType, entityId, manifestId, payload)` - uniqueId = entityId or manifestId
- `publishCommandProgress/Result(commandId, ...)` - uniqueId = commandId

---

### Phase 3: Server Process and IPC ✅

**Goal**: Server process for WebSocket, IPC with Core, query/stream handlers.

**Implemented**:
- `nova/server/server.py`: Main server loop
- `nova/server/ipc.py`: Server-side IPC client
- `nova/core/ipc.py`: Core-side IPC handlers
- `nova/core/streaming.py`: Server-paced stream playback

**Streaming Pacing Fix** (2026-01-28):
- **Problem**: Replay had erratic timing - smooth for periods then "jumping" several seconds
- **Root Cause**: Pacing was based on event span within batch (time between first/last event). When events clustered (e.g., GPS burst then nothing), batches had tiny spans followed by huge spans, causing perceived jumps.
- **Fix**: Pace by query window size (~1 second) instead of event density. Cursor now moves by fixed window, ensuring consistent ~1s delays at rate=1.0 regardless of data distribution.
- **Architecture alignment**: "Server-paced playback at requested rate" - 1 second of timeline = 1 second of wall clock at rate=1.0.

**Verified Tests**:
- `test_query_by_entity_identity` - Filter by systemId/containerId/uniqueId
- `test_query_ordered_by_timebase` - Ordering respects timebase selection

**IPC Request Types**:
- `QueryRequest(startTime, stopTime, filters, timelineMode)`
- `StreamRequest(startTime, stopTime, rate, timebase, timelineMode)`
- `CancelStreamRequest(clientConnId)`
- `CommandRequest(timelineMode)`

---

### Phase 4: Web UI and Timeline Control ✅

**Goal**: Timeline controls, WebSocket client, ordering verification.

**Implemented**:
- `nova/ui/html/index.html`: Main UI page with timeline bar
- `nova/ui/js/timeline.js`: Play/pause, seek, rate, timebase selection
- `nova/ui/js/websocket.js`: WebSocket client
- `nova/ui/js/display.js`: Event routing, iTOW display

**UI Features**:
- Timeline bar with play/pause, jump to live, rate control
- Timebase selector (source/canonical)
- Date/time input with cursor jumping
- LIVE/REPLAY indicator
- iTOW/GNSS time display (from parsed events)
- Server-authoritative cursor (anti-drift)

**Verified Tests**:
- `test_lane_priority_order` - LANE_PRIORITY constants correct
- `test_order_by_clause_generation` - SQL ORDER BY generation
- `test_compare_events_same_time_different_lanes` - Lane priority tiebreak
- `test_compare_events_eventid_tiebreak` - EventId final tiebreak

---

### Phase 5: Command Plane ✅

**Goal**: Command lifecycle, replay blocking, idempotency.

**Implemented**:
- `nova/core/commands.py`: Command lifecycle manager
- Command identity: systemId="nova" for requests, producer systemId for progress/result
- Record-before-dispatch: CommandRequest stored in DB before dispatch
- Replay blocking: timelineMode=REPLAY rejected at Server and Core

**Verified Tests**:
- `test_command_request_identity_model` - CommandRequest uses nova as systemId
- `test_command_progress_identity_model` - CommandProgress uses producer systemId
- `test_command_result_identity_model` - CommandResult uses producer systemId

**Command Envelope Contract**:
```json
{
  "eventId": "sha256hash",
  "scopeId": "scope",
  "lane": "command",
  "sourceTruthTime": "ISO8601",
  "systemId": "nova|hardwareService",
  "containerId": "instance",
  "uniqueId": "requestId|commandId:messageType",
  "messageType": "CommandRequest|CommandProgress|CommandResult",
  "commandId": "cmd-001",
  "requestId": "req-001",
  "targetId": "device-gps1",
  "commandType": "startStream",
  "payload": {...}
}
```

---

## End-to-End Test (`test_end_to_end_phases_1_to_5`)

The comprehensive integration test exercises all phases in a single flow:

1. **Phase 1**: Creates Raw, Parsed, Metadata events with correct identity model
2. **Phase 2**: Verifies identity fields preserved, eventId content-derived
3. **Phase 3**: Tests query filters (uniqueId, lane)
4. **Phase 4**: Verifies lane priority ordering (Metadata → Parsed → Raw)
5. **Phase 5**: Tests full command lifecycle (Request → Progress → Result)

**Test validates**:
- GNSS data (itow=123456789) preserved in parsed payload
- Optional debug fields (connectionId, sequence) preserved
- 7 total events ingested and queryable
- All required identity fields present

---

## UI/Data Alignment Status

### uiDataPlan.md Alignment ✅

| Requirement | Status |
|-------------|--------|
| Identity hierarchy (systemId → containerId → uniqueId) | ✅ Implemented |
| Timeline controls (play/pause, seek, rate, timebase) | ✅ Implemented |
| Anti-drift (server-authoritative cursor) | ✅ Implemented in display.js |
| iTOW/GNSS time display | ✅ Implemented in display.js |
| Command replay blocking | ✅ Core + Server rejection |
| Chat as truth | ✅ MetadataEvent with messageType="ChatMessage" |

### UI Files Structure

```
nova/ui/
  html/
    index.html      # Main page with auth, panels, timeline, chat
  js/
    auth.js         # Login/logout, token management
    websocket.js    # WebSocket client, message handling
    timeline.js     # Timeline controls, cursor management
    display.js      # Event routing, iTOW display
    entities.js     # Entity/stream discovery
    cards.js        # Card rendering
    init.js         # Application initialization
    split-setup.js  # Panel layout
  css/
    styles.css      # Dark theme styling
```

---

## hardwareService Integration Status ✅

### novaAdapter (`sdk/hardwareService/novaAdapter.py`)

- **694 lines** of implementation
- Publishes Raw, Parsed, Metadata, Command lanes
- Computes eventId before publishing (producer responsibility)
- Uses canonical subject format
- Subscribes to command subjects for command handling

**Subject Format**:
```
nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{version}
```

**Example**:
```
nova.payload1.parsed.hardwareService.node1.stream-gnss-pvt.v1
```

---

## Files Changed/Created

### Core Package (`nova/core/`)
- `database.py` - Per-lane tables with identity model
- `events.py` - Event dataclasses with new identity
- `ingest.py` - Identity validation
- `query.py` - Identity filters
- `ordering.py` - Lane priority ordering
- `contract.py` - Architecture constants
- `subjects.py` - Canonical subject format
- `ipc.py` - Core IPC handlers
- `commands.py` - Command lifecycle

### SDK (`sdk/hardwareService/`)
- `novaAdapter.py` - Updated to new identity model

### Tests (`test/`)
- `test_phases_1_to_5.py` - 21 comprehensive tests

---

## Remaining Work (Phase 6+)

| Phase | Goal | Status |
|-------|------|--------|
| 6 | Drivers and Export Parity | Not started |
| 7 | UI Plane (Manifests, UiCheckpoint) | Not started |
| 8 | TCP Loopback | Not started |
| 9 | Auth and Admin | Not started |

---

## Running Tests

```bash
# Run all Phase 1-5 tests
python -m pytest test/test_phases_1_to_5.py -v

# Run specific phase tests
python -m pytest test/test_phases_1_to_5.py::TestPhase1DatabaseIngest -v
python -m pytest test/test_phases_1_to_5.py::TestPhase5Commands -v

# Run end-to-end integration test
python -m pytest test/test_phases_1_to_5.py::TestCrossPhaseIntegration::test_end_to_end_phases_1_to_5 -v
```

---

## Conclusion

Phases 1-5 are **complete and verified**. The codebase implements:

1. **Identity Model**: Universal `systemId + containerId + uniqueId` for all lanes
2. **EventId**: Content-derived SHA256 hash with global dedupe
3. **Ordering**: Timebase → lane priority → eventId deterministic ordering
4. **Commands**: Full lifecycle with replay blocking and idempotency
5. **UI**: Timeline controls with anti-drift, iTOW display

The architecture is clean, testable, and aligned with `nova architecture.md` and `implementationPlan.md`.
