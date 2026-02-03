"""
NOVA Stream Manager - Multi-protocol stream management.

Dispatches to appropriate protocol handler based on StreamDefinition.protocol.

Property of Uncompromising Sensors LLC.
"""

from typing import Dict, Any, Optional, Callable, Awaitable

from nova.server.streamStore import StreamDefinition
from nova.server.streams.base import BaseStreamServer
from nova.server.streams.tcp import TcpStreamServer
from nova.server.streams.websocket import WsStreamServer
from nova.server.streams.udp import UdpStreamServer
from sdk.logging import getLogger


class StreamManager:
    """
    Manages all stream servers across protocols.
    
    Handles: creation, deletion, binding, status queries.
    Dispatches to appropriate protocol implementation.
    """
    
    def __init__(self, dataCallback: Callable[..., Awaitable], host: str = '0.0.0.0'):
        self.dataCallback = dataCallback
        self.host = host
        self.log = getLogger()
        
        # Active streams: streamId → BaseStreamServer (any protocol)
        self._streams: Dict[str, BaseStreamServer] = {}
        
        # WebSocket streams need route registration - track separately
        self._wsStreams: Dict[str, WsStreamServer] = {}
    
    def _createServer(self, definition: StreamDefinition) -> BaseStreamServer:
        """Create appropriate server based on protocol"""
        protocol = definition.protocol
        
        if protocol == 'tcp':
            return TcpStreamServer(definition, self.dataCallback)
        elif protocol == 'websocket':
            return WsStreamServer(definition, self.dataCallback)
        elif protocol == 'udp':
            return UdpStreamServer(definition, self.dataCallback)
        else:
            raise ValueError(f"Unknown protocol: {protocol}")
    
    async def startStream(self, definition: StreamDefinition) -> tuple[bool, str]:
        """
        Start a stream server.
        
        Returns (success, message).
        """
        streamId = definition.streamId
        
        if streamId in self._streams:
            return False, f"Stream {streamId} is already running"
        
        server = self._createServer(definition)
        success, err = await server.start(self.host)
        
        if success:
            self._streams[streamId] = server
            
            # Track WS streams for route handling
            if definition.protocol == 'websocket':
                self._wsStreams[definition.endpoint] = server
            
            endpoint = definition.endpoint
            if definition.protocol == 'websocket':
                return True, f"Stream {definition.name} ready at /ws/streams/{endpoint}"
            elif definition.protocol == 'tcp':
                return True, f"Stream {definition.name} started on port {endpoint}"
            else:
                return True, f"Stream {definition.name} started on port {endpoint} (UDP)"
        else:
            return False, err
    
    async def stopStream(self, streamId: str) -> tuple[bool, str]:
        """Stop a stream server"""
        server = self._streams.pop(streamId, None)
        if server:
            # Remove from WS tracking
            if isinstance(server, WsStreamServer):
                self._wsStreams.pop(server.path, None)
            
            await server.stop()
            return True, "Stream stopped"
        return False, f"Stream {streamId} not found"
    
    async def restartStream(self, definition: StreamDefinition) -> tuple[bool, str]:
        """Restart a stream with updated definition"""
        await self.stopStream(definition.streamId)
        return await self.startStream(definition)
    
    def getStream(self, streamId: str) -> Optional[BaseStreamServer]:
        """Get a stream server by ID"""
        return self._streams.get(streamId)
    
    def getWsStream(self, path: str) -> Optional[WsStreamServer]:
        """Get a WebSocket stream server by path"""
        return self._wsStreams.get(path)
    
    def getStatus(self, streamId: str) -> Optional[Dict[str, Any]]:
        """Get stream status"""
        server = self._streams.get(streamId)
        if server:
            return server.getStatus()
        return None
    
    def getAllStatuses(self) -> Dict[str, Dict[str, Any]]:
        """Get all stream statuses"""
        return {sid: server.getStatus() for sid, server in self._streams.items()}
    
    def bindToTimeline(self, streamId: str, instanceId: str) -> bool:
        """Bind a stream to a WebSocket instance's timeline"""
        server = self._streams.get(streamId)
        if server:
            server.bindToTimeline(instanceId)
            return True
        return False
    
    def unbindFromTimeline(self, streamId: str, instanceId: Optional[str] = None) -> bool:
        """Unbind a stream from timeline"""
        server = self._streams.get(streamId)
        if server:
            server.unbindFromTimeline(instanceId)
            return True
        return False
    
    def onInstanceDisconnect(self, instanceId: str):
        """Handle WebSocket instance disconnect - unbind any streams bound to it"""
        for server in self._streams.values():
            if server.binding.getBoundInstance() == instanceId:
                server.unbindFromTimeline(instanceId)
    
    def onInstanceStreamRestart(self, instanceId: str):
        """Handle when a bound UI instance restarts its stream (new playbackRequestId).
        
        Any output streams bound to this instance need to restart their Core cursor
        to follow the new leader cursor.
        """
        restartCount = 0
        for server in self._streams.values():
            if server.binding.getBoundInstance() == instanceId:
                server._restartStreaming()
                restartCount += 1
        if restartCount > 0:
            self.log.info(f"[StreamMgr] Restarted {restartCount} bound streams for instance {instanceId}")
    
    async def stopAll(self):
        """Stop all streams"""
        for server in list(self._streams.values()):
            await server.stop()
        self._streams.clear()
        self._wsStreams.clear()
