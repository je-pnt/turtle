"""
NOVA Event Envelope Classes

Defines the truth event envelope schemas for all lanes.
These are the authoritative contracts for producer truth events.

Architecture Invariants (nova architecture.md):
- EventId is content-derived (SHA256 hash) for idempotent dedupe
- sourceTruthTime is producer-assigned, never overwritten
- canonicalTruthTime is added at ingest by receiving NOVA instance

Identity Model (nova architecture.md Section 3):
  Public/external identity is always: scopeId + lane + systemId + containerId + uniqueId
  - systemId: The data system that produced the truth (e.g., hardwareService, adsb)
  - containerId: The node/payload/site instance (e.g., node1, payloadA, truck7)
  - uniqueId: The entity identifier within that system+container (deviceId/taskId/etc)

EventId Construction Contract:
  SHA256(eidV1 + scopeId + lane + entityIdentityKey + sourceTruthTime + canonicalPayload)
  where entityIdentityKey = systemId|containerId|uniqueId

  Uses RFC 8785 JSON Canonicalization Scheme (JCS) for cross-language stability.
  
Optional debug fields (not part of identity, Raw lane only):
  - connectionId: Raw-byte source identity (TCP/serial/etc). Optional debug label.
  - sequence: Frame sequence for Raw lane. Optional debug.
"""

import json
import hashlib
import base64
from datetime import datetime
from typing import Any, Dict, Optional, Union
from enum import Enum

# Import RFC 8785 JCS for cross-language EventId stability
from .canonical_json import canonicalJson, canonicalJsonBytes


class Lane(str, Enum):
    """Event lane enumeration"""
    RAW = "raw"
    PARSED = "parsed"
    UI = "ui"
    COMMAND = "command"
    METADATA = "metadata"


class Timebase(str, Enum):
    """Timebase selection for ordering"""
    SOURCE = "source"
    CANONICAL = "canonical"


class TimelineMode(str, Enum):
    """Timeline mode for command blocking"""
    LIVE = "live"
    REPLAY = "replay"


def buildEntityIdentityKey(systemId: str, containerId: str, uniqueId: str) -> str:
    """Build the entity identity key for EventId hash construction."""
    return f"{systemId}|{containerId}|{uniqueId}"


def computeEventId(
    scopeId: str,
    lane: Lane,
    entityIdentityKey: str,
    sourceTruthTime: str,
    canonicalPayload: Union[str, bytes]
) -> str:
    """
    Compute content-derived EventId hash.
    
    Construction: SHA256(eidV1 + scopeId + lane + entityIdentityKey + sourceTruthTime + canonicalPayload)
    
    Uses RFC 8785 JSON Canonicalization Scheme (via canonicalJson) for cross-language stability.
    
    Args:
        scopeId: Scope identifier
        lane: Event lane
        entityIdentityKey: Universal entity identity (systemId|containerId|uniqueId)
        sourceTruthTime: ISO8601 timestamp from producer
        canonicalPayload: Canonical JSON string or raw bytes
        
    Returns:
        Hex-encoded SHA256 hash (64 characters)
    """
    hasher = hashlib.sha256()
    
    # Version prefix for future compatibility
    hasher.update(b"eidV1")
    hasher.update(scopeId.encode('utf-8'))
    hasher.update(lane.value.encode('utf-8'))
    hasher.update(entityIdentityKey.encode('utf-8'))
    hasher.update(sourceTruthTime.encode('utf-8'))
    
    if isinstance(canonicalPayload, bytes):
        hasher.update(canonicalPayload)
    else:
        hasher.update(canonicalPayload.encode('utf-8'))
    
    return hasher.hexdigest()


class RawFrame:
    """
    Raw lane event: byte frames from hardware connections.
    Preserves frame boundaries exactly (no re-chunking).
    
    Identity: systemId + containerId + uniqueId
    Optional debug: connectionId, sequence
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        bytesData: bytes,
        connectionId: Optional[str] = None,
        sequence: Optional[int] = None
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.RAW
        self.sourceTruthTime = sourceTruthTime
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId
        self.bytesData = bytesData
        self.connectionId = connectionId  # Optional debug
        self.sequence = sequence  # Optional debug
        self.canonicalTruthTime: Optional[str] = None  # Added at ingest
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        bytesData: bytes,
        connectionId: Optional[str] = None,
        sequence: Optional[int] = None
    ) -> 'RawFrame':
        """
        Create RawFrame with computed EventId.
        
        Args:
            scopeId: Scope identifier
            sourceTruthTime: ISO8601 timestamp
            systemId: System that produced the data
            containerId: Container/node instance
            uniqueId: Entity identifier
            bytesData: Raw bytes
            connectionId: Optional debug - connection identifier
            sequence: Optional debug - frame sequence number
            
        Returns:
            RawFrame instance with computed eventId
        """
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=bytesData
        )
        return RawFrame(
            eventId, scopeId, sourceTruthTime,
            systemId, containerId, uniqueId, bytesData,
            connectionId, sequence
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary for transport/storage"""
        result = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": self.uniqueId,
            "bytes": base64.b64encode(self.bytesData).decode('ascii')
        }
        if self.connectionId is not None:
            result["connectionId"] = self.connectionId
        if self.sequence is not None:
            result["sequence"] = self.sequence
        if self.canonicalTruthTime is not None:
            result["canonicalTruthTime"] = self.canonicalTruthTime
        return result
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'RawFrame':
        """Create from dictionary"""
        frame = RawFrame(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            systemId=data["systemId"],
            containerId=data["containerId"],
            uniqueId=data["uniqueId"],
            bytesData=base64.b64decode(data["bytes"]),
            connectionId=data.get("connectionId"),
            sequence=data.get("sequence")
        )
        if "canonicalTruthTime" in data:
            frame.canonicalTruthTime = data["canonicalTruthTime"]
        return frame


class ParsedMessage:
    """
    Parsed lane event: typed semantic messages from streams.
    
    Identity: systemId + containerId + uniqueId
    Required: messageType, schemaVersion, payload
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        messageType: str,
        schemaVersion: str,
        payload: Dict[str, Any]
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.PARSED
        self.sourceTruthTime = sourceTruthTime
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId
        self.messageType = messageType
        self.schemaVersion = schemaVersion
        self.payload = payload
        self.canonicalTruthTime: Optional[str] = None
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        messageType: str,
        schemaVersion: str,
        payload: Dict[str, Any]
    ) -> 'ParsedMessage':
        """Create ParsedMessage with computed EventId"""
        canonicalPayload = canonicalJson(payload)
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.PARSED,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        return ParsedMessage(
            eventId, scopeId, sourceTruthTime,
            systemId, containerId, uniqueId,
            messageType, schemaVersion, payload
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": self.uniqueId,
            "messageType": self.messageType,
            "schemaVersion": self.schemaVersion,
            "payload": self.payload
        }
        if self.canonicalTruthTime is not None:
            result["canonicalTruthTime"] = self.canonicalTruthTime
        return result
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'ParsedMessage':
        """Create from dictionary"""
        msg = ParsedMessage(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            systemId=data["systemId"],
            containerId=data["containerId"],
            uniqueId=data["uniqueId"],
            messageType=data["messageType"],
            schemaVersion=data["schemaVersion"],
            payload=data["payload"]
        )
        if "canonicalTruthTime" in data:
            msg.canonicalTruthTime = data["canonicalTruthTime"]
        return msg


class UiUpdate:
    """
    UI lane event: partial state upserts for entity views.
    Keys must match manifest allowedKeys.
    
    Identity: systemId + containerId + uniqueId + viewId
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        messageType: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        viewId: str,
        manifestId: str,
        manifestVersion: str,
        data: Dict[str, Any]
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.UI
        self.sourceTruthTime = sourceTruthTime
        self.messageType = messageType
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId
        self.viewId = viewId
        self.manifestId = manifestId
        self.manifestVersion = manifestVersion
        self.data = data
        self.canonicalTruthTime: Optional[str] = None
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        viewId: str,
        manifestId: str,
        manifestVersion: str,
        data: Dict[str, Any],
        messageType: str = "UiUpdate"
    ) -> 'UiUpdate':
        """Create UiUpdate with computed EventId"""
        canonicalPayload = canonicalJson(data)
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.UI,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        return UiUpdate(
            eventId, scopeId, sourceTruthTime, messageType,
            systemId, containerId, uniqueId, viewId,
            manifestId, manifestVersion, data
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "messageType": self.messageType,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": self.uniqueId,
            "viewId": self.viewId,
            "manifestId": self.manifestId,
            "manifestVersion": self.manifestVersion,
            "data": self.data
        }
        if self.canonicalTruthTime is not None:
            result["canonicalTruthTime"] = self.canonicalTruthTime
        return result
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'UiUpdate':
        """Create from dictionary"""
        update = UiUpdate(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            messageType=data.get("messageType", "UiUpdate"),
            systemId=data["systemId"],
            containerId=data["containerId"],
            uniqueId=data["uniqueId"],
            viewId=data["viewId"],
            manifestId=data["manifestId"],
            manifestVersion=data["manifestVersion"],
            data=data["data"]
        )
        if "canonicalTruthTime" in data:
            update.canonicalTruthTime = data["canonicalTruthTime"]
        return update


class UiCheckpoint:
    """
    UI lane event: full state snapshot for efficient state-at-T queries.
    
    UiCheckpoint is NOVA-owned (Core generates it, not producers).
    Generated:
    - On first discovery of new entity/viewId
    - Periodically (every 60 minutes)
    
    Contains complete UI state (not just a delta like UiUpdate).
    
    Identity: systemId + containerId + uniqueId + viewId
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        viewId: str,
        manifestId: str,
        manifestVersion: str,
        data: Dict[str, Any],
        messageType: str = "UiCheckpoint"
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.UI
        self.sourceTruthTime = sourceTruthTime
        self.messageType = messageType
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId
        self.viewId = viewId
        self.manifestId = manifestId
        self.manifestVersion = manifestVersion
        self.data = data  # Full state snapshot
        self.canonicalTruthTime: Optional[str] = None
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        viewId: str,
        manifestId: str,
        manifestVersion: str,
        data: Dict[str, Any]
    ) -> 'UiCheckpoint':
        """Create UiCheckpoint with computed EventId"""
        canonicalPayload = canonicalJson(data)
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.UI,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        return UiCheckpoint(
            eventId, scopeId, sourceTruthTime,
            systemId, containerId, uniqueId, viewId,
            manifestId, manifestVersion, data
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "messageType": self.messageType,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": self.uniqueId,
            "viewId": self.viewId,
            "manifestId": self.manifestId,
            "manifestVersion": self.manifestVersion,
            "data": self.data
        }
        if self.canonicalTruthTime is not None:
            result["canonicalTruthTime"] = self.canonicalTruthTime
        return result
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'UiCheckpoint':
        """Create from dictionary"""
        checkpoint = UiCheckpoint(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            systemId=data["systemId"],
            containerId=data["containerId"],
            uniqueId=data["uniqueId"],
            viewId=data["viewId"],
            manifestId=data["manifestId"],
            manifestVersion=data["manifestVersion"],
            data=data["data"],
            messageType=data.get("messageType", "UiCheckpoint")
        )
        if "canonicalTruthTime" in data:
            checkpoint.canonicalTruthTime = data["canonicalTruthTime"]
        return checkpoint


class CommandRequest:
    """
    Command lane event: command request with idempotency via requestId.
    
    Identity: commandId (correlates request/progress/result)
    Idempotency: requestId (unique per request, prevents duplicate submissions)
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        messageType: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        commandId: str,
        requestId: Optional[str],
        targetId: str,
        commandType: str,
        payload: Dict[str, Any]
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.COMMAND
        self.sourceTruthTime = sourceTruthTime
        self.messageType = messageType
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId
        self.commandId = commandId
        self.requestId = requestId  # nullable, only present on CommandRequest
        self.targetId = targetId
        self.commandType = commandType
        self.payload = payload
        self.canonicalTruthTime: Optional[str] = None
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        commandId: str,
        requestId: str,
        targetId: str,
        commandType: str,
        payload: Dict[str, Any]
    ) -> 'CommandRequest':
        """Create CommandRequest with computed EventId"""
        canonicalPayload = canonicalJson(payload)
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        return CommandRequest(
            eventId, scopeId, sourceTruthTime, "CommandRequest",
            systemId, containerId, uniqueId,
            commandId, requestId, targetId, commandType, payload
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "messageType": self.messageType,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": self.uniqueId,
            "commandId": self.commandId,
            "targetId": self.targetId,
            "commandType": self.commandType,
            "payload": self.payload
        }
        if self.requestId is not None:
            result["requestId"] = self.requestId
        if self.canonicalTruthTime is not None:
            result["canonicalTruthTime"] = self.canonicalTruthTime
        return result
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'CommandRequest':
        """Create from dictionary"""
        cmd = CommandRequest(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            messageType=data.get("messageType", "CommandRequest"),
            systemId=data["systemId"],
            containerId=data["containerId"],
            uniqueId=data["uniqueId"],
            commandId=data["commandId"],
            requestId=data.get("requestId"),
            targetId=data["targetId"],
            commandType=data["commandType"],
            payload=data["payload"]
        )
        if "canonicalTruthTime" in data:
            cmd.canonicalTruthTime = data["canonicalTruthTime"]
        return cmd


class CommandProgress:
    """
    Command lane event: optional progress update from producer.
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        messageType: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        commandId: str,
        targetId: str,
        commandType: str,
        progressPercent: Optional[int],
        message: Optional[str],
        payload: Dict[str, Any]
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.COMMAND
        self.sourceTruthTime = sourceTruthTime
        self.messageType = messageType
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId
        self.commandId = commandId
        self.targetId = targetId
        self.commandType = commandType
        self.progressPercent = progressPercent
        self.message = message
        self.payload = payload
        self.canonicalTruthTime: Optional[str] = None
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        commandId: str,
        targetId: str,
        commandType: str,
        payload: Dict[str, Any],
        progressPercent: Optional[int] = None,
        message: Optional[str] = None
    ) -> 'CommandProgress':
        """Create CommandProgress with computed EventId"""
        canonicalPayload = canonicalJson(payload)
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        return CommandProgress(
            eventId, scopeId, sourceTruthTime, "CommandProgress",
            systemId, containerId, uniqueId,
            commandId, targetId, commandType,
            progressPercent, message, payload
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "messageType": self.messageType,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": self.uniqueId,
            "commandId": self.commandId,
            "targetId": self.targetId,
            "commandType": self.commandType,
            "payload": self.payload
        }
        if self.progressPercent is not None:
            result["progressPercent"] = self.progressPercent
        if self.message is not None:
            result["message"] = self.message
        if self.canonicalTruthTime is not None:
            result["canonicalTruthTime"] = self.canonicalTruthTime
        return result
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'CommandProgress':
        """Create from dictionary"""
        evt = CommandProgress(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            messageType=data.get("messageType", "CommandProgress"),
            systemId=data["systemId"],
            containerId=data["containerId"],
            uniqueId=data["uniqueId"],
            commandId=data["commandId"],
            targetId=data["targetId"],
            commandType=data["commandType"],
            progressPercent=data.get("progressPercent"),
            message=data.get("message"),
            payload=data["payload"]
        )
        if "canonicalTruthTime" in data:
            evt.canonicalTruthTime = data["canonicalTruthTime"]
        return evt


class CommandResult:
    """
    Command lane event: optional result from producer.
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        messageType: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        commandId: str,
        targetId: str,
        commandType: str,
        status: str,
        result: Optional[Dict[str, Any]],
        errorMessage: Optional[str],
        payload: Dict[str, Any]
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.COMMAND
        self.sourceTruthTime = sourceTruthTime
        self.messageType = messageType
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId
        self.commandId = commandId
        self.targetId = targetId
        self.commandType = commandType
        self.status = status
        self.result = result
        self.errorMessage = errorMessage
        self.payload = payload
        self.canonicalTruthTime: Optional[str] = None
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        commandId: str,
        targetId: str,
        commandType: str,
        status: str,
        payload: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        errorMessage: Optional[str] = None
    ) -> 'CommandResult':
        """Create CommandResult with computed EventId"""
        canonicalPayload = canonicalJson(payload)
        entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        return CommandResult(
            eventId, scopeId, sourceTruthTime, "CommandResult",
            systemId, containerId, uniqueId,
            commandId, targetId, commandType,
            status, result, errorMessage, payload
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        resultDict = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "messageType": self.messageType,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": self.uniqueId,
            "commandId": self.commandId,
            "targetId": self.targetId,
            "commandType": self.commandType,
            "status": self.status,
            "payload": self.payload
        }
        if self.result is not None:
            resultDict["result"] = self.result
        if self.errorMessage is not None:
            resultDict["errorMessage"] = self.errorMessage
        if self.canonicalTruthTime is not None:
            resultDict["canonicalTruthTime"] = self.canonicalTruthTime
        return resultDict
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'CommandResult':
        """Create from dictionary"""
        evt = CommandResult(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            messageType=data.get("messageType", "CommandResult"),
            systemId=data["systemId"],
            containerId=data["containerId"],
            uniqueId=data["uniqueId"],
            commandId=data["commandId"],
            targetId=data["targetId"],
            commandType=data["commandType"],
            status=data["status"],
            result=data.get("result"),
            errorMessage=data.get("errorMessage"),
            payload=data["payload"]
        )
        if "canonicalTruthTime" in data:
            evt.canonicalTruthTime = data["canonicalTruthTime"]
        return evt


class MetadataEvent:
    """
    Metadata lane event: time-versioned descriptors.
    Includes ProducerDescriptor, DriverBinding, ManifestPublished, ChatMessage, etc.
    
    Identity: systemId + containerId + uniqueId (or __scope__ for scope-global)
    For manifests: manifestId instead of entity triplet
    """
    
    def __init__(
        self,
        eventId: str,
        scopeId: str,
        sourceTruthTime: str,
        messageType: str,
        effectiveTime: str,
        systemId: Optional[str],
        containerId: Optional[str],
        uniqueId: Optional[str],
        manifestId: Optional[str],
        payload: Dict[str, Any]
    ):
        self.eventId = eventId
        self.scopeId = scopeId
        self.lane = Lane.METADATA
        self.sourceTruthTime = sourceTruthTime
        self.messageType = messageType
        self.effectiveTime = effectiveTime
        self.systemId = systemId
        self.containerId = containerId
        self.uniqueId = uniqueId  # Use __scope__ for scope-global metadata
        self.manifestId = manifestId
        self.payload = payload
        self.canonicalTruthTime: Optional[str] = None
    
    @staticmethod
    def create(
        scopeId: str,
        sourceTruthTime: str,
        messageType: str,
        effectiveTime: str,
        payload: Dict[str, Any],
        systemId: Optional[str] = None,
        containerId: Optional[str] = None,
        uniqueId: Optional[str] = None,
        manifestId: Optional[str] = None
    ) -> 'MetadataEvent':
        """Create MetadataEvent with computed EventId"""
        canonicalPayload = canonicalJson(payload)
        
        # Identity key: entity triplet OR manifestId
        if systemId and containerId and uniqueId:
            entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
        elif manifestId:
            entityIdentityKey = f"manifest|{manifestId}"
        else:
            raise ValueError("MetadataEvent requires either (systemId, containerId, uniqueId) or manifestId")
        
        eventId = computeEventId(
            scopeId=scopeId,
            lane=Lane.METADATA,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        return MetadataEvent(
            eventId, scopeId, sourceTruthTime, messageType,
            effectiveTime, systemId, containerId, uniqueId, manifestId, payload
        )
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {
            "eventId": self.eventId,
            "scopeId": self.scopeId,
            "lane": self.lane.value,
            "sourceTruthTime": self.sourceTruthTime,
            "messageType": self.messageType,
            "effectiveTime": self.effectiveTime,
            "payload": self.payload
        }
        if self.systemId is not None:
            result["systemId"] = self.systemId
        if self.containerId is not None:
            result["containerId"] = self.containerId
        if self.uniqueId is not None:
            result["uniqueId"] = self.uniqueId
        if self.manifestId is not None:
            result["manifestId"] = self.manifestId
        if self.canonicalTruthTime is not None:
            result["canonicalTruthTime"] = self.canonicalTruthTime
        return result
    
    @staticmethod
    def fromDict(data: Dict[str, Any]) -> 'MetadataEvent':
        """Create from dictionary"""
        evt = MetadataEvent(
            eventId=data["eventId"],
            scopeId=data["scopeId"],
            sourceTruthTime=data["sourceTruthTime"],
            messageType=data["messageType"],
            effectiveTime=data["effectiveTime"],
            systemId=data.get("systemId"),
            containerId=data.get("containerId"),
            uniqueId=data.get("uniqueId"),
            manifestId=data.get("manifestId"),
            payload=data["payload"]
        )
        if "canonicalTruthTime" in data:
            evt.canonicalTruthTime = data["canonicalTruthTime"]
        return evt


# Type alias for any event
Event = Union[RawFrame, ParsedMessage, UiUpdate, UiCheckpoint, CommandRequest, CommandProgress, CommandResult, MetadataEvent]


# Helper to parse any event from dict
def eventFromDict(data: Dict[str, Any]) -> Event:
    """Parse any event from dictionary based on lane and messageType"""
    lane = Lane(data["lane"])
    
    if lane == Lane.RAW:
        return RawFrame.fromDict(data)
    elif lane == Lane.PARSED:
        return ParsedMessage.fromDict(data)
    elif lane == Lane.UI:
        messageType = data.get("messageType", "UiUpdate")
        if messageType == "UiCheckpoint":
            return UiCheckpoint.fromDict(data)
        return UiUpdate.fromDict(data)
    elif lane == Lane.COMMAND:
        messageType = data.get("messageType", "CommandRequest")
        if messageType == "CommandProgress":
            return CommandProgress.fromDict(data)
        elif messageType == "CommandResult":
            return CommandResult.fromDict(data)
        else:
            return CommandRequest.fromDict(data)
    elif lane == Lane.METADATA:
        return MetadataEvent.fromDict(data)
    else:
        raise ValueError(f"Unknown lane: {lane}")
