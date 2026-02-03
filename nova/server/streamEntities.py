"""
NOVA Stream Entity Manager

Manages TCP streams as first-class entities with:
- ProducerDescriptor publication (identity/configuration)
- UiUpdate publication (runtime status)
- Shield eligibility (external entity by systemId)

Architecture (implementationPlan.md Phase 8):
- Entity identity: systemId=tcpStream, containerId=streams, uniqueId=<streamId>
- EntityType: tcp-stream (fixed)
- Descriptor = configuration (port, displayName, createdBy, visibility)
- UiUpdate = runtime status (state, lastError, counters)

Property of Uncompromising Sensors LLC.
"""

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, Callable, Awaitable
from enum import Enum

from nova.core.contracts import TimelineMode
from nova.server.tcp import TcpStreamConfig
from sdk.logging import getLogger


class StreamState(str, Enum):
    """TCP stream states"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class StreamEntity:
    """
    TCP stream entity state.
    
    Tracks both descriptor (config) and runtime status.
    """
    streamId: str
    displayName: str
    port: int
    scopeId: str
    createdBy: str = "system"
    visibility: str = "private"
    
    # Runtime status
    state: StreamState = StreamState.STOPPED
    lastError: Optional[str] = None
    bytesOut: int = 0
    msgsOut: int = 0
    
    # Source filters
    laneFilter: str = "raw"
    systemIdFilter: Optional[str] = None
    containerIdFilter: Optional[str] = None
    uniqueIdFilter: Optional[str] = None
    
    def toDescriptor(self) -> Dict[str, Any]:
        """
        Generate ProducerDescriptor payload.
        
        Descriptor = identity/configuration truth.
        """
        return {
            'uniqueId': self.streamId,
            'displayName': self.displayName,
            'port': self.port,
            'createdBy': self.createdBy,
            'visibility': self.visibility,
            'entityType': 'tcp-stream',
            # Source filter config
            'laneFilter': self.laneFilter,
            'systemIdFilter': self.systemIdFilter,
            'containerIdFilter': self.containerIdFilter,
            'uniqueIdFilter': self.uniqueIdFilter
        }
    
    def toUiUpdate(self) -> Dict[str, Any]:
        """
        Generate UiUpdate data payload.
        
        UiUpdate = runtime status.
        """
        return {
            'state': self.state.value,
            'lastError': self.lastError,
            'bytesOut': self.bytesOut,
            'msgsOut': self.msgsOut,
            'displayName': self.displayName,
            'port': self.port
        }
    
    def toTcpConfig(self) -> TcpStreamConfig:
        """Convert to TcpStreamConfig for TCP server"""
        return TcpStreamConfig(
            streamId=self.streamId,
            displayName=self.displayName,
            port=self.port,
            scopeId=self.scopeId,
            laneFilter=self.laneFilter,
            systemIdFilter=self.systemIdFilter,
            containerIdFilter=self.containerIdFilter,
            uniqueIdFilter=self.uniqueIdFilter,
            createdBy=self.createdBy,
            visibility=self.visibility
        )


# Stream entity identity constants (nova architecture.md)
STREAM_SYSTEM_ID = "tcpStream"
STREAM_CONTAINER_ID = "streams"
STREAM_ENTITY_TYPE = "tcp-stream"


class StreamEntityManager:
    """
    Manages TCP stream entities as first-class NOVA entities.
    
    Publishes:
    - ProducerDescriptor on create/update (metadata lane)
    - UiUpdate on status change (ui lane)
    
    Architecture:
    - Stream entities use operator's scopeId
    - External entity (systemId != nova) → shield eligible
    - Commands blocked in REPLAY mode
    """
    
    def __init__(self, scopeId: str, 
                 eventPublisher: Callable[[Dict[str, Any]], Awaitable[None]],
                 timelineModeCallback: Callable[[], TimelineMode]):
        self.scopeId = scopeId
        self.eventPublisher = eventPublisher
        self.timelineModeCallback = timelineModeCallback
        self.log = getLogger()
        
        # Active streams: streamId → StreamEntity
        self._streams: Dict[str, StreamEntity] = {}
    
    def _makeEntityIdentity(self, streamId: str) -> Dict[str, str]:
        """Generate entity identity fields"""
        return {
            'systemId': STREAM_SYSTEM_ID,
            'containerId': STREAM_CONTAINER_ID,
            'uniqueId': streamId
        }
    
    async def _publishDescriptor(self, entity: StreamEntity):
        """Publish ProducerDescriptor for stream entity"""
        now = datetime.now(timezone.utc).isoformat()
        
        event = {
            'scopeId': self.scopeId,
            'lane': 'metadata',
            'sourceTruthTime': now,
            'messageType': 'ProducerDescriptor',
            **self._makeEntityIdentity(entity.streamId),
            'capabilities': ['tcpStream.start', 'tcpStream.stop', 'tcpStream.update'],
            'schemaVersion': '1.0.0',
            'effectiveTime': now,
            'payload': entity.toDescriptor()
        }
        
        await self.eventPublisher(event)
        self.log.info(f"[StreamEntity] Published descriptor for {entity.streamId}")
    
    async def _publishUiUpdate(self, entity: StreamEntity):
        """Publish UiUpdate for stream entity"""
        now = datetime.now(timezone.utc).isoformat()
        
        event = {
            'scopeId': self.scopeId,
            'lane': 'ui',
            'sourceTruthTime': now,
            'messageType': 'UiUpdate',
            **self._makeEntityIdentity(entity.streamId),
            'viewId': f"tcp-stream-{entity.streamId}",
            'manifestId': 'tcp-stream-card',
            'manifestVersion': '1.0.0',
            'data': entity.toUiUpdate()
        }
        
        await self.eventPublisher(event)
        self.log.debug(f"[StreamEntity] Published UiUpdate for {entity.streamId}: {entity.state.value}")
    
    async def createStream(self, config: Dict[str, Any], createdBy: str = "system") -> StreamEntity:
        """
        Create a new stream entity.
        
        LIVE only - blocked in REPLAY.
        Emits ProducerDescriptor (metadata truth).
        """
        if self.timelineModeCallback() == TimelineMode.REPLAY:
            raise ValueError("Stream creation blocked in REPLAY mode")
        
        streamId = config.get('streamId') or config.get('uniqueId')
        if not streamId:
            import uuid
            streamId = str(uuid.uuid4())[:8]
        
        if streamId in self._streams:
            raise ValueError(f"Stream {streamId} already exists")
        
        entity = StreamEntity(
            streamId=streamId,
            displayName=config.get('displayName', f"Stream {streamId}"),
            port=config['port'],
            scopeId=self.scopeId,
            createdBy=createdBy,
            visibility=config.get('visibility', 'private'),
            laneFilter=config.get('laneFilter', 'raw'),
            systemIdFilter=config.get('systemIdFilter'),
            containerIdFilter=config.get('containerIdFilter'),
            uniqueIdFilter=config.get('uniqueIdFilter'),
            state=StreamState.STOPPED
        )
        
        self._streams[streamId] = entity
        
        # Publish descriptor
        await self._publishDescriptor(entity)
        await self._publishUiUpdate(entity)
        
        self.log.info(f"[StreamEntity] Created {streamId}: port={entity.port}")
        return entity
    
    async def updateStream(self, streamId: str, updates: Dict[str, Any]) -> StreamEntity:
        """
        Update stream configuration.
        
        LIVE only - blocked in REPLAY.
        Emits updated ProducerDescriptor.
        """
        if self.timelineModeCallback() == TimelineMode.REPLAY:
            raise ValueError("Stream update blocked in REPLAY mode")
        
        entity = self._streams.get(streamId)
        if not entity:
            raise ValueError(f"Stream {streamId} not found")
        
        # Apply allowed updates
        if 'displayName' in updates:
            entity.displayName = updates['displayName']
        if 'visibility' in updates:
            entity.visibility = updates['visibility']
        if 'systemIdFilter' in updates:
            entity.systemIdFilter = updates['systemIdFilter']
        if 'containerIdFilter' in updates:
            entity.containerIdFilter = updates['containerIdFilter']
        if 'uniqueIdFilter' in updates:
            entity.uniqueIdFilter = updates['uniqueIdFilter']
        
        # Publish updated descriptor
        await self._publishDescriptor(entity)
        
        self.log.info(f"[StreamEntity] Updated {streamId}")
        return entity
    
    async def setStreamState(self, streamId: str, state: StreamState, 
                             error: Optional[str] = None):
        """
        Update stream runtime state.
        
        Emits UiUpdate with new status.
        """
        entity = self._streams.get(streamId)
        if not entity:
            self.log.warning(f"[StreamEntity] Stream {streamId} not found for state update")
            return
        
        entity.state = state
        if error:
            entity.lastError = error
        elif state == StreamState.RUNNING:
            entity.lastError = None
        
        await self._publishUiUpdate(entity)
    
    async def startStream(self, streamId: str) -> StreamEntity:
        """
        Start a stream.
        
        LIVE only - blocked in REPLAY.
        Emits UiUpdate: starting → running.
        """
        if self.timelineModeCallback() == TimelineMode.REPLAY:
            raise ValueError("Stream start blocked in REPLAY mode")
        
        entity = self._streams.get(streamId)
        if not entity:
            raise ValueError(f"Stream {streamId} not found")
        
        # Transition: stopped → starting
        await self.setStreamState(streamId, StreamState.STARTING)
        
        return entity
    
    async def markStreamRunning(self, streamId: str):
        """Mark stream as running (called after TCP server starts)"""
        await self.setStreamState(streamId, StreamState.RUNNING)
    
    async def markStreamError(self, streamId: str, error: str):
        """Mark stream as error state"""
        await self.setStreamState(streamId, StreamState.ERROR, error)
    
    async def stopStream(self, streamId: str) -> StreamEntity:
        """
        Stop a stream.
        
        LIVE only - blocked in REPLAY.
        Emits UiUpdate: stopped.
        Stream remains in UI (historical) unless deleted.
        """
        if self.timelineModeCallback() == TimelineMode.REPLAY:
            raise ValueError("Stream stop blocked in REPLAY mode")
        
        entity = self._streams.get(streamId)
        if not entity:
            raise ValueError(f"Stream {streamId} not found")
        
        await self.setStreamState(streamId, StreamState.STOPPED)
        
        return entity
    
    async def deleteStream(self, streamId: str):
        """
        Delete a stream entity.
        
        LIVE only - blocked in REPLAY.
        Removes from active tracking (historical data remains in DB).
        """
        if self.timelineModeCallback() == TimelineMode.REPLAY:
            raise ValueError("Stream delete blocked in REPLAY mode")
        
        entity = self._streams.pop(streamId, None)
        if not entity:
            raise ValueError(f"Stream {streamId} not found")
        
        # Set final state to stopped
        entity.state = StreamState.STOPPED
        await self._publishUiUpdate(entity)
        
        self.log.info(f"[StreamEntity] Deleted {streamId}")
    
    def getStream(self, streamId: str) -> Optional[StreamEntity]:
        """Get stream entity by ID"""
        return self._streams.get(streamId)
    
    def getAllStreams(self) -> Dict[str, StreamEntity]:
        """Get all stream entities"""
        return dict(self._streams)
    
    def updateCounters(self, streamId: str, bytesOut: int = 0, msgsOut: int = 0):
        """Update stream counters (called from TCP server)"""
        entity = self._streams.get(streamId)
        if entity:
            entity.bytesOut += bytesOut
            entity.msgsOut += msgsOut
