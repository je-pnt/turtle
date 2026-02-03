"""
TransportBase: Abstract base for modular, bytes-in/bytes-out transport.
connect(uri, **opts) -> select adapter
publish(subject, bytes), subscribe(subject, handler), close()
REQ/REP: Layer A (shim, not yet implemented) uses PUB/SUB; Layer B uses native sockets if available.

Property of Uncompromising Sensors LLC.
"""


# Imports
import uuid
from abc import ABC, abstractmethod
from typing import Callable, Optional, Dict, Any


class SubscriptionHandle:
    """
    Lightweight subscription handle for local lifecycle control.
    
    Read-only fields:
        - subject: The subscription subject/topic
        - active: Whether this subscription is currently active
        - messagesSeen: Optional counter for local messages received (adapter-specific)"""
    

    def __init__(self, subject: str, unsubscribeCallback: Callable):
        self._subject = subject
        self._active = True
        self._messagesSeen = 0
        self._unsubscribeCallback = unsubscribeCallback
    
    @property
    def subject(self) -> str:
        return self._subject
    
    @property
    def active(self) -> bool:
        return self._active
    
    @property
    def messagesSeen(self) -> int:
        return self._messagesSeen
    
    def _incrementMessages(self):
        self._messagesSeen += 1
    
    async def unsubscribe(self):
        """Unsubscribe from this subject (local instance only)."""
        if self._active:
            self._active = False
            await self._unsubscribeCallback(self)


class TransportBase(ABC):
    """
    Abstract base class for transport adapters.
    
    Lifecycle States:
        - READY: Transport is operational (connectionless default)
        - CLOSED: Transport has been shut down
        - (CONNECTING: Optional internal state for connectionful protocols)"""
    

    def __init__(self):

        # Setup logging
        try:
            from sdk.logging import getLogger
            self._logger = getLogger()
        except ImportError:
            import logging
            self._logger = logging.getLogger("TransportBase")
        
        # Setup state, attributes
        self._state = 'CLOSED'
        self._endpoint = None
        self._connectedAt = None
        self._instanceId = str(uuid.uuid4())[:8]
        self._subscriptions: Dict[str, SubscriptionHandle] = {}
    

    # ===== Core Abstract Methods (Must Implement) =====
    @abstractmethod
    async def connect(self, uri: str, **opts) -> None:
        pass
    
    @abstractmethod
    async def publish(self, subject: str, payload: bytes | memoryview, timeout: Optional[float] = None) -> None:
        pass
    

    @abstractmethod
    async def subscribe(self, subject: str, handler: Callable[[str, bytes], Any], 
                       timeout: Optional[float] = None) -> SubscriptionHandle:
        pass
    

    @abstractmethod
    async def close(self, timeout: Optional[float] = None) -> None:
        pass


    # ===== Core Properties =====
    @property
    @abstractmethod
    def transportType(self) -> str:
        pass
    
    @property
    def state(self) -> str:
        return self._state
    

    @property
    def isConnected(self) -> bool:
        return self._state == 'READY'
    

    # ===== Optional Methods (Safe Base Defaults) =====
    async def drain(self, timeout: Optional[float] = None) -> None:
        pass  # No-op by default
    

    def status(self) -> Dict[str, Any]:
        return {'state': self._state, 'endpoint': self._endpoint,'sinceTs': self._connectedAt,'subs': len([h for h in self._subscriptions.values() if h.active]),
            'inFlight': None,  # Adapter-specific
            'rttMs': None      # Adapter-specific
        }
    

    def setLogger(self, logger) -> None:
        self._logger = logger
    

    # ===== Helper Methods =====
    def _log(self, message: str, level: str = 'INFO', **fields):
        fields.setdefault('transport', self.transportType)
        fields.setdefault('endpoint', self._endpoint)
        fields.setdefault('instanceId', self._instanceId)
        
        # Support both custom Logger (with string level) and standard logging.Logger (integer level)
        if hasattr(self._logger, 'log') and hasattr(self._logger.log, '__self__'):
            # Custom Logger wrapper - accepts string levels
            try:
                self._logger.log(message, level, **fields)
                return
            except TypeError:
                pass  # Fall through to standard logger
        
        # Standard logging.Logger - convert string level to integer
        import logging
        logLevel = getattr(logging, level.upper(), logging.INFO)
        self._logger.log(logLevel, message, extra=fields)
    

    async def _unsubscribeHandle(self, handle: SubscriptionHandle):
        if handle.subject in self._subscriptions:
            del self._subscriptions[handle.subject]

    # ===== Context Manager Support =====
    async def __aenter__(self):
        return self
    
    
    async def __aexit__(self, exc_type, exc, tb):
        await self.close()