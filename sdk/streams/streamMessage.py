"""
StreamMessage: Minimal overhead message format for high-volume data.

Designed for 20,000+ messages/second with minimal serialization overhead.
Only contains timestamp and payload - no routing metadata needed.

Property of Uncompromising Sensors LLC.
"""

from dataclasses import dataclass
from typing import Any, Dict
import json


@dataclass
class StreamMessage:
    """Minimal stream message for high-volume data
    
    Overhead: ~60 bytes (timestamp + JSON structure)
    Reduction: 70% less than full event envelope
    
    Use for:
    - GPS coordinates
    - Spectrum analyzer sweeps
    - Digital inputs
    - Raw protocol packets
    - Telemetry values
    - High-rate sensor data
    """
    
    timestamp: str      # ISO8601 UTC timestamp
    payload: Dict[str, Any]  # Message-specific data
    
    def toDict(self) -> dict:
        """Convert to dict for JSON serialization"""
        return {
            'timestamp': self.timestamp,
            'payload': self.payload
        }
    
    def toJson(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.toDict())
    
    def toBytes(self) -> bytes:
        """Convert to bytes for transport"""
        return self.toJson().encode('utf-8')
    
    @staticmethod
    def fromDict(data: dict) -> 'StreamMessage':
        """Create from dict"""
        return StreamMessage(
            timestamp=data['timestamp'],
            payload=data['payload']
        )
    
    @staticmethod
    def fromJson(jsonStr: str) -> 'StreamMessage':
        """Create from JSON string"""
        data = json.loads(jsonStr)
        return StreamMessage.fromDict(data)
    
    @staticmethod
    def fromBytes(data: bytes) -> 'StreamMessage':
        """Create from bytes"""
        return StreamMessage.fromJson(data.decode('utf-8'))
