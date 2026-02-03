"""
EventBuilder: Helper for building consistent formal events.

Creates full event envelopes with automatic ID generation, timestamps, and sequencing.

Property of Uncompromising Sensors LLC.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from .eventEnvelope import EventEnvelope


class EventBuilder:
    """Builds consistent event envelopes for formal events"""
    
    def __init__(self, producerId: str, scopeId: str):
        """Initialize event builder
        
        Args:
            producerId: Producer identifier (e.g., 'gem-payload-1')
            scopeId: Scope identifier (e.g., 'payload-1')
        """
        self.producerId = producerId
        self.scopeId = scopeId
        self.sequence = 0
    
    def buildEvent(self, entityId: str, eventType: str, payload: Dict[str, Any],
                   correlationId: Optional[str] = None,
                   parentEventId: Optional[str] = None) -> Dict[str, Any]:
        """Build event envelope
        
        Args:
            entityId: Entity identifier (e.g., 'ubx-receiver-001')
            eventType: Event type (e.g., 'entity.created')
            payload: Event-specific data
            correlationId: Optional correlation ID
            parentEventId: Optional parent event ID
            
        Returns:
            Dict ready for JSON serialization
        """
        self.sequence += 1
        
        result = {
            'eventId': str(uuid.uuid4()),
            'entityId': entityId,
            'scopeId': self.scopeId,
            'eventType': eventType,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'sequenceNum': self.sequence,
            'producerId': self.producerId,
            'payload': payload
        }
        
        # Add optional fields if provided
        if correlationId:
            result['correlationId'] = correlationId
        if parentEventId:
            result['parentEventId'] = parentEventId
            
        return result
    
    def buildEventEnvelope(self, entityId: str, eventType: str, payload: Dict[str, Any],
                           correlationId: Optional[str] = None,
                           parentEventId: Optional[str] = None) -> EventEnvelope:
        """Build EventEnvelope object
        
        Args:
            entityId: Entity identifier
            eventType: Event type
            payload: Event-specific data
            correlationId: Optional correlation ID
            parentEventId: Optional parent event ID
            
        Returns:
            EventEnvelope instance
        """
        self.sequence += 1
        
        return EventEnvelope(
            eventId=str(uuid.uuid4()),
            entityId=entityId,
            scopeId=self.scopeId,
            eventType=eventType,
            timestamp=datetime.now(timezone.utc).isoformat(),
            sequenceNum=self.sequence,
            producerId=self.producerId,
            payload=payload,
            correlationId=correlationId,
            parentEventId=parentEventId
        )
    
    def getSubject(self, eventType: str) -> str:
        """Get NATS subject for event
        
        Args:
            eventType: Event type (e.g., 'entity.created')
            
        Returns:
            NATS subject (e.g., 'event.payload-1.entity.created')
        """
        return f'event.{self.scopeId}.{eventType}'
    
    def getSessionSubject(self, sessionId: str, eventType: str) -> str:
        """Get NATS subject for session replay event
        
        Args:
            sessionId: Session identifier
            eventType: Event type
            
        Returns:
            NATS subject (e.g., 'session.abc123.event.payload-1.entity.created')
        """
        return f'session.{sessionId}.event.{self.scopeId}.{eventType}'
