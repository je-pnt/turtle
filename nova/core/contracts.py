"""
IPC contracts for Server ↔ Core communication.

These dataclasses define the request/response protocol over multiprocess IPC.
All messages are serialized via orjson for performance.

Architecture invariants:
- timelineMode: explicit LIVE or REPLAY (determines command blocking)
- playbackRequestId: unique per StartStream for fencing (prevents interleaving)
- clientConnId: WebSocket/TCP connection ID (distinct from Raw lane's deviceConnectionId)
- Server is stateless: no persistent session storage
- Core is authoritative: all validation, all DB access

Property of Uncompromising Sensors LLC.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from enum import Enum

# Import TimelineMode from events.py (single source of truth)
from .events import TimelineMode


class RequestType(str, Enum):
    """IPC request types from Server → Core"""
    QUERY = "query"
    START_STREAM = "startStream"
    CANCEL_STREAM = "cancelStream"
    SUBMIT_COMMAND = "submitCommand"
    EXPORT = "export"
    LIST_EXPORTS = "listExports"
    GET_EXPORT = "getExport"
    # Phase 8: TCP loopback
    STREAM_RAW = "streamRaw"
    CANCEL_STREAM_RAW = "cancelStreamRaw"
    # Phase 9: Metadata ingest from Server (chat messages)
    INGEST_METADATA = "ingestMetadata"


class ResponseType(str, Enum):
    """IPC response types from Core → Server"""
    QUERY_RESPONSE = "queryResponse"
    STREAM_CHUNK = "streamChunk"
    STREAM_COMPLETE = "streamComplete"
    EXPORT_RESPONSE = "exportResponse"
    EXPORTS_LIST_RESPONSE = "exportsListResponse"
    ERROR = "error"
    ACK = "ack"


@dataclass
class QueryRequest:
    """Bounded read from DB [startTime..stopTime]"""
    requestId: str
    clientConnId: str
    startTime: int  # microseconds
    stopTime: int  # microseconds
    timelineMode: TimelineMode
    timebase: str = "canonical"  # "canonical" or "source"
    filters: Optional[Dict[str, Any]] = None  # {scopeId, lane, streamId, etc}

    def toDict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['timelineMode'] = self.timelineMode.value
        return d


@dataclass
class StreamRequest:
    """Server-paced playback with ephemeral cursor"""
    requestId: str
    clientConnId: str
    playbackRequestId: str  # Fencing token - prevents interleaving after seek
    startTime: int  # microseconds
    stopTime: Optional[int]  # microseconds, None = open-ended (live follow)
    rate: float  # 1.0 = real-time, 2.0 = 2x, -1.0 = reverse
    timelineMode: TimelineMode
    timebase: str = "canonical"  # "canonical" or "source"
    filters: Optional[Dict[str, Any]] = None

    def toDict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['timelineMode'] = self.timelineMode.value
        return d


@dataclass
class CancelStreamRequest:
    """Cancel active stream for a connection"""
    requestId: str
    clientConnId: str

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CommandRequest:
    """Submit command (live only - blocked in replay)"""
    requestId: str
    clientConnId: str
    commandId: str
    targetId: str  # deviceId or streamId
    commandType: str
    payload: Dict[str, Any]
    timelineMode: TimelineMode
    userId: Optional[str] = None

    def toDict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['timelineMode'] = self.timelineMode.value
        return d


@dataclass
class IngestMetadataRequest:
    """
    Ingest metadata event from Server (Phase 9: chat messages).
    
    Server creates the metadata envelope, Core ingests to DB and returns eventId.
    Used for chat messages which are user-generated truth events.
    """
    requestId: str
    clientConnId: str
    scopeId: str
    messageType: str  # e.g., 'ChatMessage'
    effectiveTime: str  # ISO8601
    sourceTruthTime: str  # ISO8601
    systemId: str  # 'nova-server'
    containerId: str  # 'chat'
    uniqueId: str  # channel name (e.g., 'ops')
    payload: Dict[str, Any]  # message content

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QueryResponse:
    """Response for QueryRequest"""
    requestId: str
    events: List[Dict[str, Any]]  # Ordered event envelopes
    totalCount: int

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StreamChunk:
    """Chunk of events for active stream"""
    playbackRequestId: str  # Fence token - client discards if stale
    events: List[Dict[str, Any]]  # Ordered event envelopes
    timestamp: int  # Current playback timestamp (microseconds)
    complete: bool = False  # True if stream reached stopTime

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StreamComplete:
    """Stream finished (reached stopTime or canceled)"""
    playbackRequestId: str

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ErrorResponse:
    """Error response for any request"""
    requestId: str
    error: str
    details: Optional[str] = None

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AckResponse:
    """Simple acknowledgment"""
    requestId: str
    message: str = "ok"

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExportRequest:
    """Request export for time window"""
    requestId: str
    clientConnId: str
    startTime: int  # microseconds
    stopTime: int  # microseconds
    timebase: str = "canonical"
    filters: Optional[Dict[str, Any]] = None

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExportResponse:
    """Export completion response"""
    requestId: str
    exportId: str
    downloadUrl: str
    eventCount: int
    filesWritten: int

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ListExportsRequest:
    """List available exports"""
    requestId: str
    clientConnId: str

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExportsListResponse:
    """List of available exports"""
    requestId: str
    exports: List[Dict[str, Any]]

    def toDict(self) -> Dict[str, Any]:
        return asdict(self)
