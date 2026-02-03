"""
EventEnvelope: Full envelope for formal events (state changes, user actions).

Contains complete metadata for correlation, ordering, and scope control.
Used for low-volume events where full context is needed.

Property of Uncompromising Sensors LLC.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import json


@dataclass
class EventEnvelope:
    """Full event envelope for formal events
    
    Overhead: ~200 bytes (full metadata)
    
    Use for:
    - Entity lifecycle (created, destroyed)
    - Metadata changes (name updated, config changed)
    - User actions (button pressed, command sent)
    - System alerts (threshold exceeded, error detected)
    - Mission events (started, completed, aborted)
    - Task status (command sent, response received)
    """
    
    eventId: str            # UUID v7 for this event
    entityId: str           # Which entity (device, asset, etc.)
    scopeId: str            # Which scope (payload-1, fleet, etc.)
    eventType: str          # Event category (entity.created, metadata.updated)
    timestamp: str          # ISO8601 UTC timestamp
    sequenceNum: int        # Producer-local sequence (for ordering)
    producerId: str         # Which producer sent this (e.g., "gem-payload-1")
    payload: Dict[str, Any] # Event-specific data
    
    # Optional fields
    schemaVersion: Optional[str] = None      # Envelope schema version
    correlationId: Optional[str] = None      # Correlate request/response
    parentEventId: Optional[str] = None      # Trace derived events
    
    def toDict(self) -> dict:
        """Convert to dict for JSON serialization"""
        result = {
            'eventId': self.eventId,
            'entityId': self.entityId,
            'scopeId': self.scopeId,
            'eventType': self.eventType,
            'timestamp': self.timestamp,
            'sequenceNum': self.sequenceNum,
            'producerId': self.producerId,
            'payload': self.payload
        }
        
        # Add optional fields if present
        if self.schemaVersion:
            result['schemaVersion'] = self.schemaVersion
        if self.correlationId:
            result['correlationId'] = self.correlationId
        if self.parentEventId:
            result['parentEventId'] = self.parentEventId
            
        return result
    
    def toJson(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.toDict())
    
    def toBytes(self) -> bytes:
        """Convert to bytes for transport"""
        return self.toJson().encode('utf-8')
    
    @staticmethod
    def fromDict(data: dict) -> 'EventEnvelope':
        """Create from dict"""
        return EventEnvelope(
            eventId=data['eventId'],
            entityId=data['entityId'],
            scopeId=data['scopeId'],
            eventType=data['eventType'],
            timestamp=data['timestamp'],
            sequenceNum=data['sequenceNum'],
            producerId=data['producerId'],
            payload=data['payload'],
            schemaVersion=data.get('schemaVersion'),
            correlationId=data.get('correlationId'),
            parentEventId=data.get('parentEventId')
        )
    
    @staticmethod
    def fromJson(jsonStr: str) -> 'EventEnvelope':
        """Create from JSON string"""
        data = json.loads(jsonStr)
        return EventEnvelope.fromDict(data)
    
    @staticmethod
    def fromBytes(data: bytes) -> 'EventEnvelope':
        """Create from bytes"""
        return EventEnvelope.fromJson(data.decode('utf-8'))
