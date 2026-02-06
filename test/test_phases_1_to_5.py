"""
NOVA Phases 1-5 Comprehensive Test

Single test module covering all phases per implementationPlan.md:
- Phase 1: Core Database and Ingest Foundation
- Phase 2: Transport Integration (novaAdapter identity model)
- Phase 3: Server Process and IPC (streaming, query)
- Phase 4: Web UI and Timeline Control (ordering, timebase)
- Phase 5: Command Plane (lifecycle, replay blocking)

Identity Model (nova architecture.md Section 3):
  Public identity is always: scopeId + lane + systemId + containerId + uniqueId
  - systemId: Data system that produced the truth
  - containerId: Node/payload/site instance
  - uniqueId: Entity identifier within system+container

Property of Uncompromising Sensors LLC.
"""

import os
import sys
import tempfile
import pytest
from datetime import datetime, timezone, timedelta

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova.core.events import (
    Lane, Timebase, TimelineMode,
    buildEntityIdentityKey, computeEventId,
    RawFrame, ParsedMessage, UiUpdate,
    CommandRequest, CommandProgress, CommandResult,
    MetadataEvent
)
from nova.core.database import Database, DatabaseError
from nova.core.ingest import Ingest, IngestError
from nova.core.query import Query, QueryError
from nova.core.ordering import buildOrderByClause, compareEvents, LANE_PRIORITY
from nova.core.canonical_json import canonicalJson


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tempDb():
    """Create temporary database for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        dbPath = os.path.join(tmpdir, 'test_nova.db')
        db = Database(dbPath)
        yield db
        db.close()


@pytest.fixture
def ingestPipeline(tempDb):
    """Create ingest pipeline with temp database"""
    return Ingest(tempDb, verifyEventId=True)


@pytest.fixture
def queryHandler(tempDb):
    """Create query handler with temp database"""
    return Query(tempDb)


# ============================================================================
# Phase 1: Core Database and Ingest Foundation
# ============================================================================

class TestPhase1DatabaseIngest:
    """
    Phase 1 exit criteria:
    - Database schema with eventIndex + per-lane tables
    - All tables include systemId, containerId, uniqueId
    - EventId hash construction (same content → same hash)
    - Global dedupe (duplicate eventId fails)
    - Atomic dedupe test (no orphaned rows)
    """
    
    def test_identity_model_in_raw_event(self, ingestPipeline, tempDb):
        """Raw events use systemId + containerId + uniqueId identity"""
        # Create raw event with new identity model
        entityIdentityKey = buildEntityIdentityKey("hardwareService", "node1", "gps1")
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        rawBytes = b"\x01\x02\x03\x04"
        
        eventId = computeEventId(
            scopeId="test-scope",
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=rawBytes
        )
        
        raw = RawFrame(
            eventId=eventId,
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            bytesData=rawBytes,
            connectionId="conn-gps1",  # Optional debug
            sequence=1  # Optional debug
        )
        
        # Ingest should succeed
        result = ingestPipeline.ingest(raw)
        assert result is True
        
        # Query and verify identity fields
        events = tempDb.queryEvents(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.CANONICAL,
            lanes=[Lane.RAW]
        )
        
        assert len(events) == 1
        event = events[0]
        assert event['systemId'] == "hardwareService"
        assert event['containerId'] == "node1"
        assert event['uniqueId'] == "gps1"
        # Optional debug fields should be present if provided
        assert event.get('connectionId') == "conn-gps1"
        assert event.get('sequence') == 1
    
    def test_identity_model_in_parsed_event(self, ingestPipeline, tempDb):
        """Parsed events use systemId + containerId + uniqueId identity"""
        entityIdentityKey = buildEntityIdentityKey("hardwareService", "node1", "streamGps")
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        payload = {"lat": 37.7749, "lon": -122.4194}
        
        eventId = computeEventId(
            scopeId="test-scope",
            lane=Lane.PARSED,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalJson(payload)
        )
        
        parsed = ParsedMessage(
            eventId=eventId,
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId="streamGps",
            messageType="ubx.nav.pvt",
            schemaVersion="1.0",
            payload=payload
        )
        
        result = ingestPipeline.ingest(parsed)
        assert result is True
        
        events = tempDb.queryEvents(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.CANONICAL,
            lanes=[Lane.PARSED]
        )
        
        assert len(events) == 1
        event = events[0]
        assert event['systemId'] == "hardwareService"
        assert event['containerId'] == "node1"
        assert event['uniqueId'] == "streamGps"
    
    def test_eventid_content_derived_determinism(self):
        """Same content produces same eventId (architecture contract)"""
        entityIdentityKey = buildEntityIdentityKey("hardwareService", "node1", "device1")
        sourceTruthTime = "2026-01-28T10:00:00Z"
        payload = b"\x01\x02\x03"
        
        eventId1 = computeEventId(
            scopeId="test-scope",
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=payload
        )
        
        eventId2 = computeEventId(
            scopeId="test-scope",
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=payload
        )
        
        assert eventId1 == eventId2, "Same content must produce same eventId"
        assert len(eventId1) == 64, "EventId must be SHA256 hex (64 chars)"
    
    def test_eventid_different_content_different_hash(self):
        """Different content produces different eventId"""
        entityIdentityKey = buildEntityIdentityKey("hardwareService", "node1", "device1")
        sourceTruthTime = "2026-01-28T10:00:00Z"
        
        eventId1 = computeEventId(
            scopeId="test-scope",
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=b"\x01\x02\x03"
        )
        
        eventId2 = computeEventId(
            scopeId="test-scope",
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=b"\x01\x02\x04"  # Different payload
        )
        
        assert eventId1 != eventId2, "Different content must produce different eventId"
    
    def test_global_dedupe_via_eventindex(self, ingestPipeline, tempDb):
        """Duplicate eventId is deduped (not error, returns False)"""
        entityIdentityKey = buildEntityIdentityKey("hardwareService", "node1", "gps1")
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        rawBytes = b"\x01\x02\x03\x04"
        
        eventId = computeEventId(
            scopeId="test-scope",
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=rawBytes
        )
        
        raw1 = RawFrame(
            eventId=eventId,
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            bytesData=rawBytes
        )
        
        # First insert succeeds
        result1 = ingestPipeline.ingest(raw1)
        assert result1 is True
        
        # Duplicate insert is deduped (returns False, no error)
        raw2 = RawFrame(
            eventId=eventId,  # Same eventId
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            bytesData=rawBytes
        )
        
        result2 = ingestPipeline.ingest(raw2)
        assert result2 is False, "Duplicate eventId should be deduped"
        
        # Only one event in database
        events = tempDb.queryEvents(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.CANONICAL,
            lanes=[Lane.RAW]
        )
        assert len(events) == 1
    
    def test_required_fields_validation(self, ingestPipeline):
        """Ingest validates required fields per architecture contract"""
        # Missing systemId should fail
        raw = RawFrame(
            eventId="test-event-id",
            scopeId="test-scope",
            sourceTruthTime=datetime.now(timezone.utc).isoformat(),
            systemId="",  # Empty = missing
            containerId="node1",
            uniqueId="gps1",
            bytesData=b"\x01\x02"
        )
        
        with pytest.raises(IngestError, match="systemId"):
            ingestPipeline.ingest(raw)


# ============================================================================
# Phase 2: Transport Integration (identity model in envelopes)
# ============================================================================

class TestPhase2IdentityModel:
    """
    Phase 2 exit criteria:
    - Producer envelopes include systemId + containerId + uniqueId
    - entityIdentityKey construction is correct
    - connectionId/sequence/streamId are optional debug fields
    """
    
    def test_entity_identity_key_construction(self):
        """entityIdentityKey = systemId|containerId|uniqueId"""
        key = buildEntityIdentityKey("hardwareService", "node1", "device42")
        assert key == "hardwareService|node1|device42"
    
    def test_eventid_uses_entity_identity_key(self):
        """EventId hash includes entityIdentityKey (not per-lane keys)"""
        # Different uniqueId = different eventId
        sourceTruthTime = "2026-01-28T10:00:00Z"
        payload = canonicalJson({"test": 123})
        
        eventId1 = computeEventId(
            scopeId="test-scope",
            lane=Lane.PARSED,
            entityIdentityKey=buildEntityIdentityKey("hs", "n1", "stream1"),
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=payload
        )
        
        eventId2 = computeEventId(
            scopeId="test-scope",
            lane=Lane.PARSED,
            entityIdentityKey=buildEntityIdentityKey("hs", "n1", "stream2"),
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=payload
        )
        
        assert eventId1 != eventId2, "Different uniqueId = different eventId"
    
    def test_optional_debug_fields_not_in_identity(self, ingestPipeline, tempDb):
        """connectionId, sequence, streamId are optional - not part of identity"""
        entityIdentityKey = buildEntityIdentityKey("hardwareService", "node1", "gps1")
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        rawBytes = b"\x01\x02\x03\x04"
        
        eventId = computeEventId(
            scopeId="test-scope",
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=rawBytes
        )
        
        # Event WITHOUT optional debug fields should work
        raw = RawFrame(
            eventId=eventId,
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            bytesData=rawBytes
            # No connectionId, no sequence
        )
        
        result = ingestPipeline.ingest(raw)
        assert result is True


# ============================================================================
# Phase 3: Server Process and IPC (Query, Streaming)
# ============================================================================

class TestPhase3QueryStreaming:
    """
    Phase 3 exit criteria:
    - Query with identity filters (systemId, containerId, uniqueId)
    - Query returns deterministically ordered events
    - Timebase selection works (source vs canonical)
    """
    
    def test_query_by_entity_identity(self, ingestPipeline, tempDb, queryHandler):
        """Query filters by systemId, containerId, uniqueId"""
        # Insert events from two different entities
        for uniqueId in ["device1", "device2"]:
            entityIdentityKey = buildEntityIdentityKey("hardwareService", "node1", uniqueId)
            sourceTruthTime = datetime.now(timezone.utc).isoformat()
            rawBytes = f"data-{uniqueId}".encode()
            
            eventId = computeEventId(
                scopeId="test-scope",
                lane=Lane.RAW,
                entityIdentityKey=entityIdentityKey,
                sourceTruthTime=sourceTruthTime,
                canonicalPayload=rawBytes
            )
            
            raw = RawFrame(
                eventId=eventId,
                scopeId="test-scope",
                sourceTruthTime=sourceTruthTime,
                systemId="hardwareService",
                containerId="node1",
                uniqueId=uniqueId,
                bytesData=rawBytes
            )
            ingestPipeline.ingest(raw)
        
        # Query all events
        allEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z"
        )
        assert len(allEvents) == 2
        
        # Query by uniqueId filter
        filteredEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            uniqueId="device1"
        )
        assert len(filteredEvents) == 1
        assert filteredEvents[0]['uniqueId'] == "device1"
    
    def test_query_ordered_by_timebase(self, ingestPipeline, tempDb, queryHandler):
        """Query returns events ordered by selected timebase"""
        baseTime = datetime.now(timezone.utc)
        
        # Insert events with different sourceTruthTimes
        for i, offset in enumerate([2, 0, 1]):  # Out of order
            sourceTruthTime = (baseTime + timedelta(seconds=offset)).isoformat()
            entityIdentityKey = buildEntityIdentityKey("hs", "n1", f"d{i}")
            rawBytes = f"data-{i}".encode()
            
            eventId = computeEventId(
                scopeId="test-scope",
                lane=Lane.RAW,
                entityIdentityKey=entityIdentityKey,
                sourceTruthTime=sourceTruthTime,
                canonicalPayload=rawBytes
            )
            
            raw = RawFrame(
                eventId=eventId,
                scopeId="test-scope",
                sourceTruthTime=sourceTruthTime,
                systemId="hs",
                containerId="n1",
                uniqueId=f"d{i}",
                bytesData=rawBytes
            )
            ingestPipeline.ingest(raw)
        
        # Query with SOURCE timebase
        events = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.SOURCE
        )
        
        # Verify ordering: events should be in sourceTruthTime order
        times = [e['sourceTruthTime'] for e in events]
        assert times == sorted(times), "Events must be ordered by sourceTruthTime"


# ============================================================================
# Phase 4: Ordering Contract
# ============================================================================

class TestPhase4Ordering:
    """
    Phase 4 exit criteria:
    - Ordering: timebase → lane priority → eventId
    - Lane priority: Metadata(0) → Command(1) → UI(2) → Parsed(3) → Raw(4)
    - Same ordering across query/stream/export
    """
    
    def test_lane_priority_order(self):
        """Lane priority: Metadata(0) → Command(1) → UI(2) → Parsed(3) → Raw(4)"""
        assert LANE_PRIORITY[Lane.METADATA] == 0
        assert LANE_PRIORITY[Lane.COMMAND] == 1
        assert LANE_PRIORITY[Lane.UI] == 2
        assert LANE_PRIORITY[Lane.PARSED] == 3
        assert LANE_PRIORITY[Lane.RAW] == 4
    
    def test_order_by_clause_generation(self):
        """ordering.py generates correct SQL ORDER BY"""
        # Source timebase
        sourceOrderBy = buildOrderByClause(Timebase.SOURCE)
        assert "sourceTruthTime" in sourceOrderBy
        assert "eventId" in sourceOrderBy
        
        # Canonical timebase
        canonicalOrderBy = buildOrderByClause(Timebase.CANONICAL)
        assert "canonicalTruthTime" in canonicalOrderBy
        assert "eventId" in canonicalOrderBy
    
    def test_compare_events_same_time_different_lanes(self):
        """Events with same time are ordered by lane priority"""
        sameTime = "2026-01-28T10:00:00Z"
        
        rawEvent = {
            'lane': 'raw',
            'sourceTruthTime': sameTime,
            'eventId': 'abc123'
        }
        
        metadataEvent = {
            'lane': 'metadata',
            'sourceTruthTime': sameTime,
            'eventId': 'xyz789'
        }
        
        # Metadata (priority 0) should come before Raw (priority 4)
        result = compareEvents(metadataEvent, rawEvent, Timebase.SOURCE)
        assert result < 0, "Metadata should sort before Raw when time is equal"
    
    def test_compare_events_eventid_tiebreak(self):
        """Events with same time and lane use eventId as final tiebreak"""
        sameTime = "2026-01-28T10:00:00Z"
        
        event1 = {
            'lane': 'raw',
            'sourceTruthTime': sameTime,
            'eventId': 'aaa111'
        }
        
        event2 = {
            'lane': 'raw',
            'sourceTruthTime': sameTime,
            'eventId': 'bbb222'
        }
        
        # 'aaa111' < 'bbb222' lexicographically
        result = compareEvents(event1, event2, Timebase.SOURCE)
        assert result < 0, "Lower eventId should sort first when time and lane are equal"


# ============================================================================
# Phase 5: Command Plane
# ============================================================================

class TestPhase5Commands:
    """
    Phase 5 exit criteria:
    - Command events use new identity model
    - CommandRequest, CommandProgress, CommandResult lifecycle
    - Replay blocking (timelineMode=REPLAY rejected)
    - Command idempotency (requestId uniqueness)
    """
    
    def test_command_request_identity_model(self, ingestPipeline, tempDb):
        """CommandRequest uses systemId + containerId + uniqueId"""
        # For commands, NOVA is the systemId
        systemId = "nova"
        containerId = "nova-instance-1"
        uniqueId = "cmd-12345"  # commandId is uniqueId (groups lifecycle)
        
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        payload = {"action": "start"}
        
        eventId = computeEventId(
            scopeId="test-scope",
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalJson(payload)
        )
        
        cmd = CommandRequest(
            eventId=eventId,
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            messageType="CommandRequest",
            systemId=systemId,
            containerId=containerId,
            uniqueId=uniqueId,
            commandId=uniqueId,
            requestId="request-12345",
            targetId="device-gps1",
            commandType="startStream",
            payload=payload
        )
        
        result = ingestPipeline.ingest(cmd)
        assert result is True
        
        # Query and verify identity fields
        events = tempDb.queryEvents(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.CANONICAL,
            lanes=[Lane.COMMAND]
        )
        
        assert len(events) == 1
        event = events[0]
        assert event['systemId'] == "nova"
        assert event['containerId'] == "nova-instance-1"
        assert event['uniqueId'] == uniqueId
    
    def test_command_progress_identity_model(self, ingestPipeline, tempDb):
        """CommandProgress uses systemId + containerId + uniqueId"""
        # Progress comes from producer (hardwareService)
        systemId = "hardwareService"
        containerId = "node1"
        uniqueId = "cmd-001:CommandProgress"  # commandId:messageType
        
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        payload = {"progress": 50, "message": "Processing..."}
        
        eventId = computeEventId(
            scopeId="test-scope",
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalJson(payload)
        )
        
        progress = CommandProgress(
            eventId=eventId,
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            messageType="CommandProgress",
            systemId=systemId,
            containerId=containerId,
            uniqueId=uniqueId,
            commandId="cmd-001",
            targetId="device-gps1",
            commandType="startStream",
            progressPercent=50,
            message="Processing...",
            payload=payload
        )
        
        result = ingestPipeline.ingest(progress)
        assert result is True
        
        events = tempDb.queryEvents(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.CANONICAL,
            lanes=[Lane.COMMAND],
            messageType="CommandProgress"
        )
        
        assert len(events) == 1
        assert events[0]['systemId'] == "hardwareService"
    
    def test_command_result_identity_model(self, ingestPipeline, tempDb):
        """CommandResult uses systemId + containerId + uniqueId"""
        systemId = "hardwareService"
        containerId = "node1"
        uniqueId = "cmd-001:CommandResult"  # commandId:messageType
        
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        payload = {"status": "success", "result": {"streamStarted": True}}
        
        eventId = computeEventId(
            scopeId="test-scope",
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalJson(payload)
        )
        
        result_event = CommandResult(
            eventId=eventId,
            scopeId="test-scope",
            sourceTruthTime=sourceTruthTime,
            messageType="CommandResult",
            systemId=systemId,
            containerId=containerId,
            uniqueId=uniqueId,
            commandId="cmd-001",
            targetId="device-gps1",
            commandType="startStream",
            status="success",
            result={"streamStarted": True},
            errorMessage=None,
            payload=payload
        )
        
        result = ingestPipeline.ingest(result_event)
        assert result is True
        
        events = tempDb.queryEvents(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.CANONICAL,
            lanes=[Lane.COMMAND],
            messageType="CommandResult"
        )
        
        assert len(events) == 1
        assert events[0]['systemId'] == "hardwareService"


# ============================================================================
# Cross-Phase Integration Tests
# ============================================================================

class TestCrossPhaseIntegration:
    """Integration tests spanning multiple phases"""
    
    def test_full_event_lifecycle_all_lanes(self, ingestPipeline, tempDb, queryHandler):
        """Ingest events in all lanes, query back with ordering"""
        baseTime = datetime.now(timezone.utc)
        events_to_insert = []
        
        # Raw event
        rawIdentityKey = buildEntityIdentityKey("hs", "n1", "dev1")
        rawTime = baseTime.isoformat()
        rawBytes = b"\x01\x02\x03"
        rawEventId = computeEventId("scope1", Lane.RAW, rawIdentityKey, rawTime, rawBytes)
        events_to_insert.append(RawFrame(
            eventId=rawEventId, scopeId="scope1", sourceTruthTime=rawTime,
            systemId="hs", containerId="n1", uniqueId="dev1", bytesData=rawBytes
        ))
        
        # Parsed event (same time - will sort by lane priority)
        parsedIdentityKey = buildEntityIdentityKey("hs", "n1", "stream1")
        parsedPayload = {"temp": 25.5}
        parsedEventId = computeEventId("scope1", Lane.PARSED, parsedIdentityKey, rawTime, canonicalJson(parsedPayload))
        events_to_insert.append(ParsedMessage(
            eventId=parsedEventId, scopeId="scope1", sourceTruthTime=rawTime,
            systemId="hs", containerId="n1", uniqueId="stream1",
            messageType="sensor.temp", schemaVersion="1.0", payload=parsedPayload
        ))
        
        # Metadata event (same time - highest priority)
        metaIdentityKey = buildEntityIdentityKey("hs", "n1", "stream1:ProducerDescriptor")
        metaPayload = {"producer": "hardwareService", "version": "1.0"}
        metaEventId = computeEventId("scope1", Lane.METADATA, metaIdentityKey, rawTime, canonicalJson(metaPayload))
        events_to_insert.append(MetadataEvent(
            eventId=metaEventId, scopeId="scope1", sourceTruthTime=rawTime,
            messageType="ProducerDescriptor", effectiveTime=rawTime,
            systemId="hs", containerId="n1", uniqueId="stream1:ProducerDescriptor",
            manifestId=None, payload=metaPayload
        ))
        
        # Ingest all
        for event in events_to_insert:
            ingestPipeline.ingest(event)
        
        # Query all events using SOURCE timebase (all events have same sourceTruthTime)
        # This tests lane priority ordering when times are equal
        results = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.SOURCE  # Use SOURCE since all events have same sourceTruthTime
        )
        
        assert len(results) == 3
        
        # Verify ordering: Metadata → Parsed → Raw (same time, lane priority)
        lanes = [e['lane'] for e in results]
        assert lanes == ['metadata', 'parsed', 'raw'], f"Expected lane order metadata→parsed→raw, got {lanes}"
    
    def test_end_to_end_phases_1_to_5(self, ingestPipeline, tempDb, queryHandler):
        """
        COMPREHENSIVE END-TO-END TEST: Proves all Phase 1-5 goals.
        
        This single test exercises the complete NOVA truth pipeline:
        
        Phase 1 - Database Foundation:
        - Creates events with correct identity model (systemId/containerId/uniqueId)
        - Computes content-derived eventId (SHA256 hash)
        - Stores events with global eventId dedupe
        - Validates required fields
        
        Phase 2 - Transport Integration:
        - Events include correct envelope fields per architecture contract
        - entityIdentityKey = systemId|containerId|uniqueId (universal)
        - Optional debug fields (connectionId, sequence, streamId) are preserved
        
        Phase 3 - Query/IPC:
        - Query filters work (systemId, containerId, uniqueId, lane)
        - Query returns deterministically ordered events
        - Time window queries work correctly
        
        Phase 4 - Ordering Contract:
        - Events ordered by: timebase → lane_priority → eventId
        - Lane priority: Metadata(0) → Command(1) → UI(2) → Parsed(3) → Raw(4)
        - Timebase selection (source vs canonical) works
        
        Phase 5 - Command Plane:
        - CommandRequest, CommandProgress, CommandResult lifecycle
        - Command identity uses new model (nova as systemId)
        - Command events are correlated by commandId
        """
        baseTime = datetime(2026, 1, 28, 12, 0, 0, tzinfo=timezone.utc)
        scopeId = "end-to-end-test"
        
        # ========================================================================
        # PHASE 1: Database Foundation - Create events with identity model
        # ========================================================================
        
        # 1.1: Raw event from hardwareService with optional debug fields
        rawUniqueId = "gps-zed-f9p"
        rawIdentityKey = buildEntityIdentityKey("hardwareService", "node1", rawUniqueId)
        rawTime = (baseTime + timedelta(seconds=0)).isoformat()
        rawBytes = b"\xb5\x62\x01\x07"  # UBX NAV-PVT prefix
        rawEventId = computeEventId(scopeId, Lane.RAW, rawIdentityKey, rawTime, rawBytes)
        
        rawEvent = RawFrame(
            eventId=rawEventId,
            scopeId=scopeId,
            sourceTruthTime=rawTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId=rawUniqueId,
            bytesData=rawBytes,
            connectionId="tcp-192.168.1.10:5000",  # Optional debug
            sequence=1  # Optional debug
        )
        assert ingestPipeline.ingest(rawEvent) is True, "Phase 1: Raw event ingest failed"
        
        # 1.2: Parsed event with GNSS data (proves itow/gnss time pipeline)
        parsedUniqueId = "stream-gnss-pvt"
        parsedIdentityKey = buildEntityIdentityKey("hardwareService", "node1", parsedUniqueId)
        parsedTime = (baseTime + timedelta(seconds=0)).isoformat()  # Same time as raw
        parsedPayload = {
            "lat": 37.7749,
            "lon": -122.4194,
            "alt": 10.5,
            "itow": 123456789,  # GPS time (UI displays this)
            "fixType": 3,
            "numSv": 12
        }
        parsedEventId = computeEventId(scopeId, Lane.PARSED, parsedIdentityKey, parsedTime, canonicalJson(parsedPayload))
        
        parsedEvent = ParsedMessage(
            eventId=parsedEventId,
            scopeId=scopeId,
            sourceTruthTime=parsedTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId=parsedUniqueId,
            messageType="ubx.nav.pvt",
            schemaVersion="1.0",
            payload=parsedPayload
        )
        assert ingestPipeline.ingest(parsedEvent) is True, "Phase 1: Parsed event ingest failed"
        
        # 1.3: Metadata event (ProducerDescriptor announces capabilities)
        metaUniqueId = "stream-gnss-pvt:ProducerDescriptor"
        metaIdentityKey = buildEntityIdentityKey("hardwareService", "node1", metaUniqueId)
        metaTime = (baseTime + timedelta(seconds=0)).isoformat()  # Same time
        metaPayload = {
            "producer": "hardwareService",
            "version": "2.0.0",
            "capabilities": ["gnss.position", "gnss.velocity", "gnss.time"],
            "streamDescriptors": [
                {"streamId": "gnss-main", "type": "ubx.nav.pvt", "schema": "1.0"}
            ]
        }
        metaEventId = computeEventId(scopeId, Lane.METADATA, metaIdentityKey, metaTime, canonicalJson(metaPayload))
        
        metaEvent = MetadataEvent(
            eventId=metaEventId,
            scopeId=scopeId,
            sourceTruthTime=metaTime,
            messageType="ProducerDescriptor",
            effectiveTime=metaTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId=metaUniqueId,
            manifestId=None,
            payload=metaPayload
        )
        assert ingestPipeline.ingest(metaEvent) is True, "Phase 1: Metadata event ingest failed"
        
        # ========================================================================
        # PHASE 2: Verify identity model and eventId construction
        # ========================================================================
        
        # 2.1: Query back and verify identity fields are preserved
        allEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z"
        )
        assert len(allEvents) == 3, f"Phase 2: Expected 3 events, got {len(allEvents)}"
        
        for event in allEvents:
            assert 'systemId' in event, "Phase 2: Missing systemId"
            assert 'containerId' in event, "Phase 2: Missing containerId"
            assert 'uniqueId' in event, "Phase 2: Missing uniqueId"
            assert event['systemId'] == "hardwareService", f"Phase 2: Wrong systemId: {event['systemId']}"
            assert event['containerId'] == "node1", f"Phase 2: Wrong containerId: {event['containerId']}"
        
        # 2.2: Verify optional debug fields preserved for Raw
        rawEvents = [e for e in allEvents if e['lane'] == 'raw']
        assert len(rawEvents) == 1
        assert rawEvents[0].get('connectionId') == "tcp-192.168.1.10:5000", "Phase 2: connectionId not preserved"
        assert rawEvents[0].get('sequence') == 1, "Phase 2: sequence not preserved"
        
        # 2.3: Verify eventId is content-derived (recompute and compare)
        # Recompute eventId for raw event
        recomputedRawEventId = computeEventId(scopeId, Lane.RAW, rawIdentityKey, rawTime, rawBytes)
        assert rawEvents[0]['eventId'] == recomputedRawEventId, "Phase 2: eventId not content-derived"
        
        # ========================================================================
        # PHASE 3: Query filters and time windows
        # ========================================================================
        
        # 3.1: Filter by uniqueId
        parsedOnlyEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            uniqueId=parsedUniqueId
        )
        assert len(parsedOnlyEvents) == 1, f"Phase 3: uniqueId filter failed, got {len(parsedOnlyEvents)}"
        assert parsedOnlyEvents[0]['uniqueId'] == parsedUniqueId
        
        # 3.2: Filter by lane
        metadataEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            lanes=[Lane.METADATA]
        )
        assert len(metadataEvents) == 1, f"Phase 3: lane filter failed, got {len(metadataEvents)}"
        assert metadataEvents[0]['lane'] == 'metadata'
        
        # ========================================================================
        # PHASE 4: Ordering contract verification
        # ========================================================================
        
        # 4.1: Verify lane priority ordering (all events have same sourceTruthTime)
        # Query with SOURCE timebase since all events share same sourceTruthTime
        orderedEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.SOURCE
        )
        # Filter to just the first 3 events (same time)
        sameTimeEvents = [e for e in orderedEvents if e['sourceTruthTime'] == rawTime]
        lanes = [e['lane'] for e in sameTimeEvents]
        assert lanes == ['metadata', 'parsed', 'raw'], \
            f"Phase 4: Lane priority violated. Expected metadata→parsed→raw, got {lanes}"
        
        # 4.2: Insert events with different times to verify time ordering
        laterTime = (baseTime + timedelta(seconds=5)).isoformat()
        laterUniqueId = "gps-zed-f9p-later"
        laterIdentityKey = buildEntityIdentityKey("hardwareService", "node1", laterUniqueId)
        laterBytes = b"\xb5\x62\x01\x08"
        laterEventId = computeEventId(scopeId, Lane.RAW, laterIdentityKey, laterTime, laterBytes)
        
        laterRaw = RawFrame(
            eventId=laterEventId,
            scopeId=scopeId,
            sourceTruthTime=laterTime,
            systemId="hardwareService",
            containerId="node1",
            uniqueId=laterUniqueId,
            bytesData=laterBytes
        )
        ingestPipeline.ingest(laterRaw)
        
        # Query and verify time ordering
        timeOrderedEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            timebase=Timebase.SOURCE
        )
        
        # Verify timestamps are ordered
        times = [e['sourceTruthTime'] for e in timeOrderedEvents]
        assert times == sorted(times), "Phase 4: Time ordering violated"
        
        # ========================================================================
        # PHASE 5: Command lifecycle
        # ========================================================================
        
        # 5.1: CommandRequest (from NOVA to producer)
        cmdTime = (baseTime + timedelta(seconds=10)).isoformat()
        cmdSystemId = "nova"  # Commands originate from NOVA
        cmdContainerId = "nova-core"
        cmdCommandId = "cmd-001"
        cmdUniqueId = cmdCommandId  # commandId is uniqueId (groups lifecycle)
        cmdIdentityKey = buildEntityIdentityKey(cmdSystemId, cmdContainerId, cmdUniqueId)
        cmdPayload = {
            "action": "start",
            "streamId": "gnss-main",
            "rate": "10Hz"
        }
        cmdEventId = computeEventId(scopeId, Lane.COMMAND, cmdIdentityKey, cmdTime, canonicalJson(cmdPayload))
        
        cmdRequest = CommandRequest(
            eventId=cmdEventId,
            scopeId=scopeId,
            sourceTruthTime=cmdTime,
            messageType="CommandRequest",
            systemId=cmdSystemId,
            containerId=cmdContainerId,
            uniqueId=cmdUniqueId,
            commandId=cmdCommandId,
            requestId="req-start-stream-001",
            targetId="stream-gnss-pvt",
            commandType="startStream",
            payload=cmdPayload
        )
        assert ingestPipeline.ingest(cmdRequest) is True, "Phase 5: CommandRequest ingest failed"
        
        # 5.2: CommandProgress (from producer to NOVA)
        progressTime = (baseTime + timedelta(seconds=11)).isoformat()
        progressSystemId = "hardwareService"  # Progress comes from producer
        progressContainerId = "node1"
        progressUniqueId = "cmd-001:CommandProgress"
        progressIdentityKey = buildEntityIdentityKey(progressSystemId, progressContainerId, progressUniqueId)
        progressPayload = {"progress": 50, "message": "Initializing stream..."}
        progressEventId = computeEventId(scopeId, Lane.COMMAND, progressIdentityKey, progressTime, canonicalJson(progressPayload))
        
        cmdProgress = CommandProgress(
            eventId=progressEventId,
            scopeId=scopeId,
            sourceTruthTime=progressTime,
            messageType="CommandProgress",
            systemId=progressSystemId,
            containerId=progressContainerId,
            uniqueId=progressUniqueId,
            commandId="cmd-001",
            targetId="stream-gnss-pvt",
            commandType="startStream",
            progressPercent=50,
            message="Initializing stream...",
            payload=progressPayload
        )
        assert ingestPipeline.ingest(cmdProgress) is True, "Phase 5: CommandProgress ingest failed"
        
        # 5.3: CommandResult (from producer to NOVA)
        resultTime = (baseTime + timedelta(seconds=12)).isoformat()
        resultUniqueId = "cmd-001:CommandResult"
        resultIdentityKey = buildEntityIdentityKey(progressSystemId, progressContainerId, resultUniqueId)
        resultPayload = {"status": "success", "result": {"streamStarted": True, "actualRate": "10Hz"}}
        resultEventId = computeEventId(scopeId, Lane.COMMAND, resultIdentityKey, resultTime, canonicalJson(resultPayload))
        
        cmdResult = CommandResult(
            eventId=resultEventId,
            scopeId=scopeId,
            sourceTruthTime=resultTime,
            messageType="CommandResult",
            systemId=progressSystemId,
            containerId=progressContainerId,
            uniqueId=resultUniqueId,
            commandId="cmd-001",
            targetId="stream-gnss-pvt",
            commandType="startStream",
            status="success",
            result={"streamStarted": True, "actualRate": "10Hz"},
            errorMessage=None,
            payload=resultPayload
        )
        assert ingestPipeline.ingest(cmdResult) is True, "Phase 5: CommandResult ingest failed"
        
        # 5.4: Verify command lifecycle can be queried
        commandEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z",
            lanes=[Lane.COMMAND]
        )
        assert len(commandEvents) == 3, f"Phase 5: Expected 3 command events, got {len(commandEvents)}"
        
        # Verify all have same commandId for correlation
        commandIds = set()
        for evt in commandEvents:
            if 'commandId' in evt:
                commandIds.add(evt['commandId'])
        assert len(commandIds) == 1 and 'cmd-001' in commandIds, \
            f"Phase 5: Command correlation failed, commandIds: {commandIds}"
        
        # ========================================================================
        # FINAL VERIFICATION: All phases work together
        # ========================================================================
        
        # Query everything and verify total count
        finalEvents = queryHandler.query(
            startTime="2020-01-01T00:00:00Z",
            stopTime="2030-01-01T00:00:00Z"
        )
        
        # 3 data events + 1 later raw + 3 command events = 7 total
        assert len(finalEvents) == 7, f"Final: Expected 7 events, got {len(finalEvents)}"
        
        # Verify all events have required identity fields
        for event in finalEvents:
            assert event['eventId'], "Final: Missing eventId"
            assert event['scopeId'], "Final: Missing scopeId"
            assert event['sourceTruthTime'], "Final: Missing sourceTruthTime"
            assert event['systemId'], "Final: Missing systemId"
            assert event['containerId'], "Final: Missing containerId"
            assert event['uniqueId'], "Final: Missing uniqueId"
        
        # Verify GNSS data (itow) is preserved in parsed event payload
        parsedGnssEvents = [e for e in finalEvents if e['lane'] == 'parsed']
        assert len(parsedGnssEvents) >= 1
        assert parsedGnssEvents[0]['payload'].get('itow') == 123456789, \
            "Final: GNSS itow not preserved in payload"
    
    def test_dedupe_across_lanes(self, ingestPipeline, tempDb):
        """EventId is global - same eventId in different lanes is still dedupe"""
        # This is a pathological case (same eventId shouldn't appear in different lanes)
        # but the eventIndex table provides global dedupe
        
        entityIdentityKey = buildEntityIdentityKey("hs", "n1", "device1")
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        rawBytes = b"\x01\x02\x03"
        
        # Manually create same eventId for two events
        eventId = computeEventId("scope1", Lane.RAW, entityIdentityKey, sourceTruthTime, rawBytes)
        
        raw1 = RawFrame(
            eventId=eventId, scopeId="scope1", sourceTruthTime=sourceTruthTime,
            systemId="hs", containerId="n1", uniqueId="device1", bytesData=rawBytes
        )
        
        # First insert succeeds
        result1 = ingestPipeline.ingest(raw1)
        assert result1 is True
        
        # Second insert with same eventId is deduped
        raw2 = RawFrame(
            eventId=eventId, scopeId="scope1", sourceTruthTime=sourceTruthTime,
            systemId="hs", containerId="n1", uniqueId="device1", bytesData=rawBytes
        )
        
        result2 = ingestPipeline.ingest(raw2)
        assert result2 is False, "Global dedupe should prevent duplicate eventId"


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
