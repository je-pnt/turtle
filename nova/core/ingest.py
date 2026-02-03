"""
NOVA Ingest Pipeline

Validates, dedupes, and appends truth events to the database.
Assigns canonicalTruthTime at wall-clock receive time.

Architecture Invariants (nova architecture.md):
- eventId is internal to NOVA for dedupe/idempotency
- Core computes eventId deterministically if producer omits it
- If producer provides eventId, Core validates (warning on mismatch)
- Core validates required fields before attempting insert
- Atomic dedupe + insert via DB transaction (no orphaned rows)
- canonicalTruthTime assigned once at ingest as wall-clock receive time
- sourceTruthTime is never overwritten
- Single dedupe point: eventIndex table only

Identity Model (nova architecture.md Section 3):
  Public identity is always: scopeId + lane + systemId + containerId + uniqueId
  - systemId: The data system that produced the truth (e.g., hardwareService, nova)
  - containerId: The node/payload/site instance (e.g., node1, payloadA)
  - uniqueId: The renderable entity identifier within that system+container

  Optional debug fields (not part of identity):
  - connectionId: Raw-byte source identity (TCP/serial/etc). Optional.
  - sequence: Frame sequence for Raw lane. Optional.

Ingest Flow:
  1. Validate required fields (scopeId, lane, sourceTruthTime, systemId, containerId, uniqueId)
  2. Compute eventId if missing; verify if provided (warning on mismatch)
  3. Assign canonicalTruthTime (wall-clock now)
  4. Atomic insert: eventIndex + lane table (transaction ensures no orphans)
  5. On duplicate eventId: silently dedupe (return False)
  6. On success: return True
"""

from datetime import datetime, timezone
from typing import Optional

from .database import Database, DatabaseError
from .events import Event, Lane, computeEventId, buildEntityIdentityKey
from .canonical_json import canonicalJson


class IngestError(Exception):
    """Ingest validation or processing error"""
    pass


class Ingest:
    """
    Ingest pipeline for truth events.
    
    Validates, dedupes, assigns canonicalTruthTime, and appends to DB.
    Computes eventId if missing; verifies if provided.
    Notifies StreamingManager of new events for push-based LIVE streaming.
    Triggers FileWriter for real-time file output (Phase 6).
    Processes UiUpdate events through UiStateManager for checkpoint generation (Phase 7).
    
    CRITICAL: FileWriter is ONLY triggered on ingest (producer truth).
    FileWriter must NEVER be called from query/stream/replay paths.
    """
    
    def __init__(self, database: Database, verifyEventId: bool = True, streamingManager=None, fileWriter=None, uiStateManager=None):
        """
        Initialize ingest pipeline.
        
        Args:
            database: Database instance
            verifyEventId: If True, verify producer-provided eventId (warn on mismatch)
            streamingManager: StreamingManager instance for LIVE stream notifications (optional)
            fileWriter: FileWriter instance for real-time file output (optional)
            uiStateManager: UiStateManager instance for UiCheckpoint generation (optional)
        """
        self.database = database
        self.verifyEventId = verifyEventId
        self.streamingManager = streamingManager
        self.fileWriter = fileWriter
        self.uiStateManager = uiStateManager
    
    def ingest(self, event: Event) -> bool:
        """
        Ingest a single event.
        
        Args:
            event: Event to ingest
            
        Returns:
            True if ingested (new event)
            False if deduped (duplicate eventId)
            
        Raises:
            IngestError: On validation failure or database error (not dedupe)
        """
        # Step 1: Validate required fields (eventId computed if missing)
        self._validate(event)
        
        # Step 2: Compute or verify eventId
        self._ensureEventId(event)
        
        # Step 3: Assign canonicalTruthTime (wall-clock now, UTC ISO8601)
        canonicalTruthTime = datetime.now(timezone.utc).isoformat()
        
        # Step 4: Atomic insert (dedupe + append)
        try:
            inserted = self.database.insertEvent(event, canonicalTruthTime)
            
            if inserted:
                # Success: new event ingested
                # Notify StreamingManager for LIVE stream push
                if self.streamingManager:
                    self.streamingManager.notifyNewEvent(event, canonicalTruthTime)
                
                # Trigger FileWriter for real-time file output (Phase 6)
                # CRITICAL: Only on ingest, NEVER on query/stream/replay
                if self.fileWriter:
                    eventDict = event.toDict()
                    eventDict['canonicalTruthTime'] = canonicalTruthTime
                    self.fileWriter.write(eventDict, canonicalTruthTime)
                
                # Process UiUpdate through UiStateManager for checkpoint generation (Phase 7)
                if self.uiStateManager and event.lane == Lane.UI:
                    if hasattr(event, 'messageType') and event.messageType == "UiUpdate":
                        checkpoint = self.uiStateManager.processUiUpdate(event)
                        if checkpoint:
                            # Ingest the generated checkpoint
                            self._ingestCheckpoint(checkpoint, canonicalTruthTime)
                
                return True
            else:
                # Dedupe: eventId already exists
                return False
        
        except DatabaseError as e:
            raise IngestError(f"Database insert failed: {e}")
    
    def _ingestCheckpoint(self, checkpoint, parentCanonicalTime: str):
        """
        Ingest a generated UiCheckpoint event.
        
        UiCheckpoints are NOVA-generated, not from producers.
        Uses parent event's canonical time for ordering consistency.
        """
        try:
            inserted = self.database.insertEvent(checkpoint, parentCanonicalTime)
            if inserted and self.streamingManager:
                self.streamingManager.notifyNewEvent(checkpoint, parentCanonicalTime)
            if inserted and self.fileWriter:
                eventDict = checkpoint.toDict()
                eventDict['canonicalTruthTime'] = parentCanonicalTime
                self.fileWriter.write(eventDict, parentCanonicalTime)
        except DatabaseError:
            # Checkpoint insert failed - log but don't fail ingest
            pass
    
    def _validate(self, event: Event):
        """
        Validate required fields per architecture contract.
        
        All lanes require: scopeId, lane, sourceTruthTime, systemId, containerId, uniqueId
        eventId is computed by Core if missing (not required from producer)
        Lane-specific fields validated per nova architecture.md Section 5.
        
        Args:
            event: Event to validate
            
        Raises:
            IngestError: If validation fails
        """
        # === Universal required fields (all lanes) ===
        # Note: eventId is NOT required - Core computes if missing
        
        if not event.scopeId or not isinstance(event.scopeId, str):
            raise IngestError("Missing or invalid scopeId")
        
        if not event.lane or not isinstance(event.lane, Lane):
            raise IngestError("Missing or invalid lane")
        
        if not event.sourceTruthTime or not isinstance(event.sourceTruthTime, str):
            raise IngestError("Missing or invalid sourceTruthTime")
        
        # Try parsing sourceTruthTime to ensure it's valid ISO8601
        try:
            datetime.fromisoformat(event.sourceTruthTime.replace('Z', '+00:00'))
        except ValueError as e:
            raise IngestError(f"Invalid sourceTruthTime format: {e}")
        
        # Identity triplet - required for ALL lanes
        if not event.systemId or not isinstance(event.systemId, str):
            raise IngestError("Missing or invalid systemId")
        
        if not event.containerId or not isinstance(event.containerId, str):
            raise IngestError("Missing or invalid containerId")
        
        if not event.uniqueId or not isinstance(event.uniqueId, str):
            raise IngestError("Missing or invalid uniqueId")
        
        # === Lane-specific validation ===
        
        if event.lane == Lane.RAW:
            # Raw lane: bytes required
            if not event.bytesData or not isinstance(event.bytesData, bytes):
                raise IngestError("Raw event missing or invalid bytesData")
            # connectionId and sequence are OPTIONAL debug fields
        
        elif event.lane == Lane.PARSED:
            # Parsed lane: messageType, schemaVersion, payload required
            if not event.messageType:
                raise IngestError("Parsed event missing messageType")
            if not event.schemaVersion:
                raise IngestError("Parsed event missing schemaVersion")
            if not isinstance(event.payload, dict):
                raise IngestError("Parsed event payload must be dict")
            # streamId is OPTIONAL debug field
        
        elif event.lane == Lane.UI:
            # UI lane: viewId, manifestId, manifestVersion, data required
            if not event.viewId:
                raise IngestError("UI event missing viewId")
            if not event.manifestId:
                raise IngestError("UI event missing manifestId")
            if not event.manifestVersion:
                raise IngestError("UI event missing manifestVersion")
            if not isinstance(event.data, dict):
                raise IngestError("UI event data must be dict")
        
        elif event.lane == Lane.COMMAND:
            # Command lane: commandId, targetId, commandType, messageType required
            # Note: timelineMode is NOT part of stored events - it's request-time only
            if not event.commandId:
                raise IngestError("Command event missing commandId")
            if not event.targetId:
                raise IngestError("Command event missing targetId")
            if not event.commandType:
                raise IngestError("Command event missing commandType")
            if not event.messageType:
                raise IngestError("Command event missing messageType")
            if not isinstance(event.payload, dict):
                raise IngestError("Command event payload must be dict")
        
        elif event.lane == Lane.METADATA:
            # Metadata lane: messageType, effectiveTime, payload required
            if not event.messageType:
                raise IngestError("Metadata event missing messageType")
            if not event.effectiveTime:
                raise IngestError("Metadata event missing effectiveTime")
            if not isinstance(event.payload, dict):
                raise IngestError("Metadata event payload must be dict")
    
    def _ensureEventId(self, event: Event):
        """
        Ensure event has eventId - compute if missing, verify if provided.
        
        eventId = SHA256(eidV1 + scopeId + lane + entityIdentityKey + sourceTruthTime + canonicalPayload)
        
        Args:
            event: Event to process (may be mutated to add eventId)
        """
        # Build entity identity key (universal for all lanes)
        entityIdentityKey = buildEntityIdentityKey(
            event.systemId, event.containerId, event.uniqueId
        )
        
        # Determine canonical payload based on lane
        if event.lane == Lane.RAW:
            canonicalPayload = event.bytesData
        elif event.lane == Lane.PARSED:
            canonicalPayload = canonicalJson(event.payload)
        elif event.lane == Lane.UI:
            canonicalPayload = canonicalJson(event.data)
        elif event.lane == Lane.COMMAND:
            canonicalPayload = canonicalJson(event.payload)
        elif event.lane == Lane.METADATA:
            canonicalPayload = canonicalJson(event.payload)
        else:
            raise IngestError(f"Unknown lane: {event.lane}")
        
        # Compute expected eventId
        expectedEventId = computeEventId(
            scopeId=event.scopeId,
            lane=event.lane,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=event.sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        
        if not event.eventId:
            # Producer omitted eventId - Core computes it
            event.eventId = expectedEventId
        elif self.verifyEventId and event.eventId != expectedEventId:
            # Producer provided eventId but it doesn't match - REJECT
            # EventId is a cryptographic content hash - mismatch means either:
            # 1) Producer has a bug in their hash computation
            # 2) Content was corrupted in transit
            # Either way, we cannot trust this event
            raise IngestError(
                f"EventId mismatch for {event.lane.value} event. "
                f"Producer: {event.eventId}, Expected: {expectedEventId}"
            )
