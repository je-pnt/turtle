"""
NOVA Output Streams - Base classes for multi-protocol streaming.

Architecture:
- BaseStreamServer: Common streaming logic (data flow, binding, formatting)
- Protocol-specific subclasses: Only implement connection mechanics
- StreamConnection: Abstract per-client connection

Property of Uncompromising Sensors LLC.
"""

import asyncio
import time
import orjson
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable, Awaitable, List
from dataclasses import dataclass

from nova.server.streamStore import StreamDefinition
from sdk.logging import getLogger


@dataclass
class StreamBinding:
    """
    Timeline binding state for a stream.
    
    If bound, stream follows the bound WebSocket instance's cursor.
    If unbound or binding disconnects, stream reverts to LIVE-follow.
    """
    def __init__(self):
        self.boundInstanceId: Optional[str] = None
    
    def bind(self, instanceId: str):
        """Bind to a WebSocket instance (last-binder-wins)"""
        self.boundInstanceId = instanceId
    
    def unbind(self, instanceId: Optional[str] = None):
        """Unbind - if instanceId provided, only unbind if it matches"""
        if instanceId is None or self.boundInstanceId == instanceId:
            self.boundInstanceId = None
    
    def isBound(self) -> bool:
        return self.boundInstanceId is not None
    
    def getBoundInstance(self) -> Optional[str]:
        return self.boundInstanceId


class StreamConnection(ABC):
    """
    Abstract base for a single client connection.
    
    Subclasses implement protocol-specific write/close.
    """
    
    def __init__(self, streamId: str, connId: str):
        self.streamId = streamId
        self.connId = connId
        self.running = False
        self.log = getLogger()
        
        # Stats
        self.bytesOut = 0
        self.msgsOut = 0
    
    @abstractmethod
    async def write(self, data: bytes):
        """Write data to client - protocol-specific"""
        pass
    
    @abstractmethod
    async def close(self):
        """Close connection - protocol-specific"""
        pass
    
    def _trackWrite(self, dataLen: int):
        """Track write stats"""
        self.bytesOut += dataLen
        self.msgsOut += 1


class BaseStreamServer(ABC):
    """
    Base stream server - common streaming logic.
    
    Subclasses implement:
    - start(): Protocol-specific listener setup
    - stop(): Protocol-specific cleanup
    - _getProtocolName(): For logging
    """
    
    def __init__(self, definition: StreamDefinition, dataCallback: Callable[..., Awaitable]):
        self.definition = definition
        self.dataCallback = dataCallback
        self.log = getLogger()
        
        # Connection tracking
        self._connections: Dict[str, StreamConnection] = {}
        self._connCounter = 0
        
        # Binding state
        self.binding = StreamBinding()
        
        # Data streaming task
        self._streamTask: Optional[asyncio.Task] = None
        self._running = False
    
    @abstractmethod
    def _getProtocolName(self) -> str:
        """Return protocol name for logging (tcp/ws/udp)"""
        pass
    
    @abstractmethod
    async def start(self, host: str = '0.0.0.0') -> tuple[bool, str]:
        """Start the server - protocol-specific"""
        pass
    
    @abstractmethod
    async def stop(self):
        """Stop the server - protocol-specific"""
        pass
    
    def _logPrefix(self) -> str:
        """Logging prefix"""
        return f"[{self._getProtocolName().upper()}:{self.definition.streamId}]"
    
    def _startStreaming(self):
        """Start the data streaming task"""
        if self._streamTask is None or self._streamTask.done():
            self._streamTask = asyncio.create_task(self._streamData())
    
    def _stopStreaming(self):
        """Stop the data streaming task"""
        if self._streamTask:
            self._streamTask.cancel()
            self._streamTask = None
    
    async def _streamData(self):
        """
        Stream data to all connected clients.
        
        LIVE-follow by default.
        If bound to timeline, follows bound instance's cursor.
        """
        self.log.info(f"{self._logPrefix()} Streaming started, connections={len(self._connections)}")
        
        eventCount = 0
        startTime = time.perf_counter()
        lastLogTime = startTime
        
        try:
            async for event in self.dataCallback(
                streamId=self.definition.streamId,
                lane=self.definition.lane,
                systemIdFilter=self.definition.systemIdFilter,
                containerIdFilter=self.definition.containerIdFilter,
                uniqueIdFilter=self.definition.uniqueIdFilter,
                messageTypeFilter=self.definition.messageTypeFilter,
                bindingCallback=lambda: self.binding.getBoundInstance()
            ):
                output = self._formatOutput(event)
                if output is None:
                    continue
                
                # Debug first few events only at startup
                if eventCount < 3 and lastLogTime == startTime:
                    self.log.debug(f"{self._logPrefix()} Event #{eventCount}: {len(output)} bytes, conns={len(self._connections)}")
                
                await self._distributeToClients(output)
                
                # Track throughput - log every 30 seconds
                eventCount += 1
                now = time.perf_counter()
                if now - lastLogTime >= 30.0:
                    rate = eventCount / (now - lastLogTime)
                    self.log.info(f"{self._logPrefix()} Throughput: {rate:.1f} evt/s, clients={len(self._connections)}")
                    eventCount = 0
                    lastLogTime = now
                
        except asyncio.CancelledError:
            self.log.info(f"{self._logPrefix()} Streaming cancelled")
            raise
        except Exception as e:
            self.log.error(f"{self._logPrefix()} Streaming error: {e}", exc_info=True)
    
    def _formatOutput(self, event: Dict[str, Any]) -> Optional[bytes]:
        """
        Format event for output based on outputFormat.
        
        payloadOnly: raw bytes or JSON payload only
        hierarchyPerMessage: {"s":"...","c":"...","u":"...","t":"...","p":{...}}
        """
        fmt = self.definition.outputFormat
        
        if fmt == "payloadOnly":
            if self.definition.lane == "raw":
                rawBytes = event.get('bytes')
                if rawBytes:
                    return rawBytes
                return None
            payload = event.get('payload', event)
            return orjson.dumps(payload) + b'\n'
        
        elif fmt == "hierarchyPerMessage":
            envelope = {
                's': event.get('systemId', ''),
                'c': event.get('containerId', ''),
                'u': event.get('uniqueId', ''),
                't': event.get('sourceTruthTime') or event.get('canonicalTruthTime', ''),
                'p': event.get('payload', event.get('bytes', ''))
            }
            return orjson.dumps(envelope) + b'\n'
        
        return None
    
    async def _distributeToClients(self, data: bytes):
        """Distribute data to all connected clients"""
        if not self._connections:
            return
        
        disconnected = []
        # Use list() to avoid "dictionary changed size during iteration" error
        for connId, conn in list(self._connections.items()):
            try:
                await conn.write(data)
            except Exception:
                disconnected.append(connId)
        
        for connId in disconnected:
            conn = self._connections.pop(connId, None)
            if conn:
                await conn.close()
    
    def _addConnection(self, conn: StreamConnection):
        """Add a connection and start streaming if needed"""
        self._connections[conn.connId] = conn
        conn.running = True
        if self.definition.enabled:
            self._startStreaming()
    
    def _removeConnection(self, connId: str) -> Optional[StreamConnection]:
        """Remove a connection"""
        return self._connections.pop(connId, None)
    
    def _nextConnId(self) -> str:
        """Generate next connection ID"""
        self._connCounter += 1
        return f"{self.definition.streamId}-{self._connCounter}"
    
    def getConnectionCount(self) -> int:
        """Get number of active connections"""
        return len(self._connections)
    
    def getStatus(self) -> Dict[str, Any]:
        """Get stream status"""
        return {
            'streamId': self.definition.streamId,
            'name': self.definition.name,
            'protocol': self.definition.protocol,
            'endpoint': self.definition.endpoint,
            'enabled': self.definition.enabled,
            'running': self._running,
            'connectionCount': self.getConnectionCount(),
            'bound': self.binding.isBound(),
            'boundInstance': self.binding.getBoundInstance(),
            'outputFormat': self.definition.outputFormat,
            'lane': self.definition.lane,
            'selectionSummary': self.definition.selectionSummary()
        }
    
    def bindToTimeline(self, instanceId: str):
        """Bind stream to a WebSocket instance's timeline"""
        oldBound = self.binding.getBoundInstance()
        self.binding.bind(instanceId)
        self.log.info(f"{self._logPrefix()} Bound to instance {instanceId}")
        if oldBound != instanceId:
            self._restartStreaming()
    
    def unbindFromTimeline(self, instanceId: Optional[str] = None):
        """Unbind stream from timeline (falls back to LIVE-follow)"""
        wasBound = self.binding.isBound()
        self.binding.unbind(instanceId)
        self.log.info(f"{self._logPrefix()} Unbound, reverting to LIVE-follow")
        if wasBound:
            self._restartStreaming()
    
    def _restartStreaming(self):
        """Restart the streaming task to pick up binding/timeline changes"""
        wasRunning = self._streamTask and not self._streamTask.done()
        if wasRunning:
            self._streamTask.cancel()
            self._streamTask = None
        # Restart if stream is running and either has connections or is enabled
        if self._running and (self.getConnectionCount() > 0 or self.definition.enabled):
            self.log.info(f"{self._logPrefix()} Restarting stream (was_running={wasRunning})")
            self._startStreaming()
    
    async def _closeAllConnections(self):
        """Close all connections"""
        for conn in list(self._connections.values()):
            await conn.close()
        self._connections.clear()
