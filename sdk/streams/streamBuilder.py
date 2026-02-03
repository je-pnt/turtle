"""
StreamBuilder: Helper for building consistent stream messages.

Creates minimal overhead stream messages with automatic timestamp generation.

Property of Uncompromising Sensors LLC.
"""

from datetime import datetime, timezone
from typing import Dict, Any
from .streamMessage import StreamMessage


class StreamBuilder:
    """Builds consistent stream messages with minimal overhead"""
    
    def buildStream(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build stream message (minimal overhead)
        
        Args:
            payload: Message-specific data
            
        Returns:
            Dict ready for JSON serialization
        """
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'payload': payload
        }
    
    def buildStreamMessage(self, payload: Dict[str, Any]) -> StreamMessage:
        """Build StreamMessage object
        
        Args:
            payload: Message-specific data
            
        Returns:
            StreamMessage instance
        """
        return StreamMessage(
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload
        )
    
    def getSubject(self, entityId: str, messageType: str) -> str:
        """Get NATS subject for live stream
        
        Args:
            entityId: Entity identifier (e.g., 'ubx-receiver-001')
            messageType: Message type (e.g., 'llas-row')
            
        Returns:
            NATS subject (e.g., 'stream.ubx-receiver-001.llas-row')
        """
        return f'stream.{entityId}.{messageType}'
    
    def getSessionSubject(self, sessionId: str, entityId: str, messageType: str) -> str:
        """Get NATS subject for session replay stream
        
        Args:
            sessionId: Session identifier
            entityId: Entity identifier
            messageType: Message type
            
        Returns:
            NATS subject (e.g., 'session.abc123.stream.ubx-receiver-001.llas-row')
        """
        return f'session.{sessionId}.stream.{entityId}.{messageType}'
