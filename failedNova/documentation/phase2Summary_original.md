# NOVA 2.0 Phase 2 Implementation Summary

> **NOTE**: This is the original planning document, updated to reflect final implementation.  
> For the complete validation report with test results and violation fixes, see [../phase2Summary.md](../phase2Summary.md).

**Date Completed**: January 26, 2026  
**Phase**: Transport Integration and Producer Adapter  
**Status**: ✅ COMPLETE - All exit criteria met

---

## Overview

Phase 2 implements transport integration where NOVA Core subscribes to truth events published by producers, and creates a producer adapter for hardwareService to emit NOVA-compliant envelopes. This phase establishes the public routing contract with RFC 8785 JCS for cross-language eventId stability.

---

## Architecture Contracts Implemented

### 1. NOVA Subject Naming (Public Routing Contract)
- ✅ Format: `nova.{scopeId}.{lane}.{identityKey}.v{schemaVersion}`
- ✅ Version-last pattern for multi-version subscription support
- ✅ Deterministic: same inputs → same subject
- ✅ URL-safe identityKey: `[A-Za-z0-9_\-:.]+`
- ✅ Alphanumeric scopeId: `[A-Za-z0-9]+`
- ✅ identityKey per lane:
  - Raw: connectionId
  - Parsed: streamId
  - UI: assetId:viewId
  - Command: requestId
  - Metadata: streamId:messageType OR manifestId:messageType

### 2. RFC 8785 JCS (JSON Canonicalization Scheme)
- ✅ Cross-language eventId stability via RFC 8785 canonical JSON
- ✅ Wraps `canonicaljson` library (Python implementation)
- ✅ Single-function API: `canonicalJson(obj) → str`
- ✅ Used for eventId computation in all lanes (except Raw: uses raw bytes)
- ✅ Replaces Python-only json.dumps from Phase 1

### 3. Transport Subscription (Core Side)
- ✅ TransportManager subscribes via sdk.transport
- ✅ Async message handling with envelope validation
- ✅ Forwards validated events to Phase 1 ingest pipeline
- ✅ Scope filtering: payload mode (own scopeId) vs ground mode (all scopes)
- ✅ Mismatch detection: logs warnings for routing/envelope discrepancies

### 4. Producer Adapter (HardwareService Side)
- ✅ NovaAdapter publishes Raw/Parsed/Metadata lanes
- ✅ Computes eventId with RFC 8785 JCS
- ✅ Connection ID mapping for deviceId → connectionId stability
- ✅ Integrates with hardwareService via BaseDevice.emit()
- ✅ Wraps existing ioLayer outputs (minimal changes)

### 5. Envelope Structure
- ✅ Required fields: schemaVersion, eventId, scopeId, lane, sourceTruthTime
- ✅ Lane-specific fields: connectionId/sequence (Raw), streamId/streamType/payload (Parsed), messageType/effectiveTime/payload (Metadata)
- ✅ Producer computes eventId (Core trusts but can verify)

### 6. Dedupe and Uniqueness Conformance
- ✅ **Dedupe**: Same envelope twice → one DB entry (eventId collision handled)
- ✅ **Uniqueness**: Different content → different eventIds → separate DB entries
- ✅ EventId collision detection: Phase 1 dedupe mechanism (eventIndex UNIQUE constraint)

---

## Files Created

```
nova/
├── core/
│   ├── subjects.py              # NOVA subject naming functions (282 lines)
│   ├── canonicalJson.py         # RFC 8785 JCS wrapper (67 lines)
│   └── transportManager.py      # Transport subscription manager (277 lines)
└── requirements.txt             # Added: canonicaljson>=2.0.0

sdk/
└── hardwareService/
    ├── novaAdapter.py           # NOVA publisher adapter (331 lines)
    ├── hardwareService.py       # Modified: integrated novaAdapter
    ├── devices/
    │   └── baseDevice.py        # Modified: Device.emit() calls novaAdapter
    └── plugins/
        └── ubxPlugin.py         # Modified: pass novaAdapter to devices

test/
└── nova/
    └── test_phase2.py           # Comprehensive Phase 2 test suite (637 lines, 11 tests)
```

## Files Modified

```
nova/
├── core/
│   ├── events.py                # Replaced Python json.dumps with RFC 8785 JCS
│   └── ingest.py                # Updated canonicalJson import
├── config.json                  # Added transport config (uri, reconnectAttempts, timeout, mode)

sdk/
└── hardwareService/
    └── config.json              # Added scopeId and novaTransport fields
```

---

## NOVA Subject Examples

### Raw Lane
```
nova.payloadA.raw.conn1.v1
nova.ground.raw.conn-device42.v1
```

### Parsed Lane
```
nova.payloadB.parsed.streamGps.v1
nova.ground.parsed.streamRfPower.v1
```

### Metadata Lane (with colon in identityKey)
```
nova.payloadA.metadata.streamGps:ProducerDescriptor.v1
nova.ground.metadata.manifest123:ManifestDescriptor.v1
```

### Subscription Patterns (NATS wildcards)
```
nova.payloadA.*.*.*            # All lanes for scopeId=payloadA
nova.*.parsed.*.*              # All parsed events across all scopes
nova.ground.metadata.*.*       # All metadata for ground mode
```

---

## Implementation Details

### Subject Formatting API (subjects.py)

```python
from nova.core.subjects import formatNovaSubject, parseNovaSubject, RouteKey
from nova.core.contract import Lane

# Format subject
routeKey = RouteKey(
    scopeId="payloadA",
    lane=Lane.RAW,
    identityKey="conn1",
    schemaVersion=1
)
subject = formatNovaSubject(routeKey)
# Result: "nova.payloadA.raw.conn1.v1"

# Parse subject
parsed = parseNovaSubject("nova.payloadA.raw.conn1.v1")
# Result: RouteKey(scopeId="payloadA", lane=Lane.RAW, identityKey="conn1", schemaVersion=1)

# Subscription pattern
pattern = formatSubscriptionPattern(scopeId="payloadA", lane=Lane.PARSED)
# Result: "nova.payloadA.parsed.*.*"
```

### RFC 8785 JCS API (canonicalJson.py)

```python
from nova.core.canonicalJson import canonicalJson, canonicalJsonBytes

# Canonicalize JSON (key ordering, no whitespace)
payload = {"z": 3, "a": 1, "m": 2}
canonical = canonicalJson(payload)
# Result: '{"a":1,"m":2,"z":3}'

# Same content, different order → same canonical form
payload2 = {"a": 1, "m": 2, "z": 3}
canonical2 = canonicalJson(payload2)
# canonical == canonical2 (True)

# EventId computation uses canonical form
eventId = computeEventId(scopeId, lane, identityKey, sourceTruthTime, canonical)
```

### Transport Manager Flow (transportManager.py)

1. **Subscribe**: Format subscription pattern via `formatSubscriptionPattern()`
2. **On message receive**:
   - Parse subject with `parseNovaSubject()`
   - Decode JSON envelope
   - Validate required fields (schemaVersion, eventId, scopeId, lane, sourceTruthTime)
   - Check routing/envelope mismatch (log warnings, prefer envelope)
   - Convert envelope to Event object (RawFrame, ParsedMessage, MetadataEvent)
   - Forward to Phase 1 ingest pipeline
   - Dedupe handled by Phase 1 eventIndex
3. **On dedupe**: Ingest returns False (event already in DB)
4. **On success**: Ingest returns True (new event stored)

### Producer Adapter Flow (novaAdapter.py)

#### publishRaw(deviceId, sequence, rawBytes)
1. Get connectionId via `getConnectionId(deviceId)` (stable mapping)
2. Get sourceTruthTime (ISO8601 UTC now)
3. Compute eventId: `SHA256(eidV1 + scopeId + lane + connectionId + sourceTruthTime + rawBytes)`
4. Build Raw envelope (schemaVersion, eventId, scopeId, lane, sourceTruthTime, connectionId, sequence, bytes)
5. Format subject: `nova.{scopeId}.raw.{connectionId}.v1`
6. Publish to transport

#### publishParsed(deviceId, streamId, streamType, payload)
1. Get sourceTruthTime
2. Canonicalize payload with RFC 8785 JCS
3. Compute eventId: `SHA256(eidV1 + scopeId + lane + streamId + sourceTruthTime + canonicalPayload)`
4. Build Parsed envelope (schemaVersion, eventId, scopeId, lane, sourceTruthTime, streamId, streamType, payload)
5. Format subject: `nova.{scopeId}.parsed.{streamId}.v1`
6. Publish to transport

#### publishMetadata(messageType, streamId/manifestId, payload)
1. Get sourceTruthTime (= effectiveTime for metadata)
2. Determine identityKey: `{streamId}:{messageType}` or `{manifestId}:{messageType}`
3. Canonicalize payload with RFC 8785 JCS
4. Compute eventId: `SHA256(eidV1 + scopeId + lane + identityKey + sourceTruthTime + canonicalPayload)`
5. Build Metadata envelope (schemaVersion, eventId, scopeId, lane, sourceTruthTime, messageType, effectiveTime, streamId/manifestId, payload)
6. Format subject: `nova.{scopeId}.metadata.{identityKey}.v1`
7. Publish to transport

### HardwareService Integration

```python
# BaseDevice.emit() modified to call novaAdapter
async def emit(self, dataType: str, ts: float, data: bytes):
    # Legacy subject-based publish
    # Single execution path - NOVA if available, legacy otherwise
    if self.novaAdapter:
        # Phase 2+ NOVA path
        await self.novaAdapter.publishRaw(self.deviceId, self._rawSequence, data)
        self._rawSequence += 1
    elif self.transport:
        # Legacy path (backward compatibility when novaAdapter=None)
        subject = self.subjectBuilder.data(self.deviceId, self.getKind(), dataType)
        await self.transport.publish(subject, data)
```

---

## Configuration

### nova/config.json
```json
{
  "scopeId": "payload-local",
  "dbPath": "nova/nova.db",
  "timebaseDefault": "source",
  "mode": "payload",
  "transport": {
    "uri": "nats://localhost:4222",
    "reconnectAttempts": 5,
    "timeout": 10.0
  }
}
```

### sdk/hardwareService/config.json
```json
{
  "containerId": "payload-1",
  "scopeId": "payload-local",
  "novaTransport": "nats://localhost:4222",
  "scanIntervalSeconds": 5,
  "deviceTimeoutSeconds": 15,
  "transport": "nats://localhost:4222"
}
```

---

## Test Coverage (11 tests, 100% pass)

### Subject Formatting (3 tests)
- ✅ Raw lane subject: `nova.payloadA.raw.conn1.v1`
- ✅ Parsed lane subject: `nova.ground.parsed.streamGps.v1`
- ✅ Metadata lane with colon: `nova.payloadB.metadata.streamGps:ProducerDescriptor.v1`

### EventId Stability (3 tests)
- ✅ Same content → same eventId (RFC 8785 JCS deterministic)
- ✅ Different content → different eventIds
- ✅ JCS key ordering: `{"z":3, "a":1}` === `{"a":1, "z":3}`

### Transport Integration (5 tests)
- ✅ Raw event published → Core subscribes → DB stores
- ✅ Parsed event published → Core subscribes → DB stores
- ✅ Metadata event published → Core subscribes → DB stores
- ✅ **Dedupe test**: Same envelope twice → only 1 DB entry
- ✅ **Uniqueness test**: Different envelopes → different eventIds → 2 DB entries

---

## Phase 2 Exit Criteria: ✅ ALL MET

1. ✅ **Producer adapter implemented**: NovaAdapter publishes Raw/Parsed/Metadata with computed eventId
   
2. ✅ **Transport subscription implemented**: TransportManager subscribes to NOVA events via sdk.transport
   
3. ✅ **Subject naming contract validated**: All subjects follow `nova.{scopeId}.{lane}.{identityKey}.v{schemaVersion}`
   
4. ✅ **RFC 8785 JCS integrated**: Cross-language eventId stability via canonicaljson library
   
5. ✅ **End-to-end ingest proven**: Producer → transport → Core → DB (all 3 lanes tested)
   
6. ✅ **Dedupe conformance test**: Publishing same event twice produces only one DB entry (eventId collision handled by Phase 1 dedupe)
   
7. ✅ **Uniqueness conformance test**: Publishing different events produces different eventIds and separate DB entries

---

## Key Design Decisions

### 1. Version-Last Subject Pattern
- Chose `nova.{scopeId}.{lane}.{identityKey}.v{schemaVersion}` over version-first
- Enables multi-version subscription: `nova.scope.parsed.stream.*` matches all versions
- Wildcard subscription: `nova.*.parsed.*.*` (all parsed events across all scopes)
- Version filtering: `nova.scope.*.*.v1` (all v1 events for a scope)

### 2. RFC 8785 JCS for EventId
- Upgraded from Python-only json.dumps to RFC 8785 standard
- Wrapped `canonicaljson` library in single-function API
- Future-proof: same canonical form across Python/Node.js/Go/Rust
- Contained in `canonicalJson.py` module for easy replacement if needed

### 3. Minimal HardwareService Integration
- NovaAdapter wraps existing ioLayer outputs (no new plugin architecture)
- BaseDevice.emit() calls novaAdapter in addition to legacy transport
- Optional integration: legacy path continues to work if novaAdapter is None
- Non-invasive: plugin modifications only add novaAdapter parameter

### 4. Single Publish Path - "One Way" Principle
- **NOVA path**: `nova.{scopeId}.{lane}.{identityKey}.v{schemaVersion}` (when novaAdapter configured)
- **Legacy path**: `payload.{containerId}.{deviceId}.{kind}.{dataType}` (when novaAdapter=None)
- **No parallel execution**: Single if/elif ensures only ONE path executes
- **Migration strategy**: Legacy path provides backward compatibility

### 5. Connection ID Determinism
- NovaAdapter deterministically formats `connectionId` as `conn-{deviceId}`
- Same deviceId always produces same connectionId (restart-stable)
- No persistence needed - computed from deviceId on every call
- Ensures Raw lane identityKey stability across device/service restarts

### 6. Envelope Validation with Mismatch Detection
- TransportManager validates required fields before ingest
- Logs warnings for subject/envelope mismatches (scopeId, lane, schemaVersion)
- Prefers envelope fields over subject routing (envelope is source of truth)
- Mismatch detection helps debug producer issues without dropping data

### 7. Mock Transport for Testing
- Created MockTransport that implements sdk.transport interface
- Supports NATS wildcard matching (*, >) for subscription patterns
- In-memory message routing (no NATS server required)
- Enables fast, isolated integration tests

---

## Mock Transport Implementation

```python
class MockTransport:
    """Mock transport for testing without NATS"""
    
    def __init__(self):
        self.subscriptions = []
        self.messages = []
    
    async def publish(self, subject: str, payload: bytes, timeout=None):
        self.messages.append((subject, payload))
        for sub_pattern, handler in self.subscriptions:
            if self._matches(subject, sub_pattern):
                await handler(subject, payload)
    
    async def subscribe(self, subject: str, handler, timeout=None):
        self.subscriptions.append((subject, handler))
        return MockSubscriptionHandle(subject, lambda h: self._unsubscribe(subject, handler))
    
    def _matches(self, subject: str, pattern: str) -> bool:
        # Simplified NATS wildcard matching
        # Supports * (single token) and > (multi-token)
        ...
```

---

## Next Steps (Phase 3)

Phase 3: Server Process and IPC
- Server process for WebSocket/TCP edges
- Multiprocess IPC between Server ↔ Core
- Stateless request/response flow (QueryRequest, StreamRequest, CommandRequest)
- Stream playback with server-paced read from DB
- Authentication/authorization for external clients

---

## Architectural Compliance

✅ **Public routing contract**: Subject naming documented and validated  
✅ **RFC 8785 JCS**: Cross-language eventId stability  
✅ **Minimal producer changes**: Wrapper approach preserves existing code  
✅ **End-to-end validation**: Producer → transport → Core → DB tested  
✅ **Dedupe proven**: Same event twice → one DB entry  
✅ **Uniqueness proven**: Different events → different eventIds  
✅ **Guidelines adherence**: camelCase naming, explicit coding, functional names  

---

## Command to Verify

```bash
cd c:\us\dev
$env:PYTHONPATH="c:\us\dev"
python -m pytest test\nova\test_phase2.py -v
```

**Result**: 11 passed in 0.68s ✅

---

## Integration Summary

**Producer Side**:
- hardwareService devices call `self.emit(dataType, ts, data)`
- BaseDevice.emit() calls novaAdapter.publishRaw() with incremented sequence
- NovaAdapter computes eventId, builds envelope, formats subject, publishes to transport

**Transport Layer**:
- NATS broker routes messages based on subject patterns
- MockTransport used for testing (no NATS server required)

**Core Side**:
- TransportManager subscribes to `nova.{scopeId}.*.*.*` pattern
- On message: parse subject → validate envelope → convert to Event → ingest
- Phase 1 ingest handles dedupe (eventIndex UNIQUE constraint)
- Query returns ordered events from DB

**Conformance**:
- Same envelope published twice → dedupe at eventIndex (one DB row)
- Different envelopes → different eventIds → separate DB rows
- EventId uniqueness proven via content-derived hash

---

**Phase 2 is complete and ready for Phase 3.**
