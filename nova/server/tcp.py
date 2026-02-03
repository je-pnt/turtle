"""
NOVA TCP Server - Stream-out with LIVE-follow and timeline binding.

Architecture (Phase 8.1):
- TCP streams are output forks (read truth, emit to clients)
- Default: LIVE-follow (no WebSocket required)
- Optional: Timeline binding (follows bound WebSocket cursor)
- Binding model: last-binder-wins, fallback to LIVE on disconnect
- Output formats: payloadOnly | hierarchyPerMessage
- Backpressure: catchUp (drop queued, resume) is default

Property of Uncompromising Sensors LLC.
"""

import asyncio
import socket
import time
import orjson
from typing import Dict, Any, Optional, Callable, Awaitable
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
    boundInstanceId: Optional[str] = None
    
    def bind(self, instanceId: str):
        """Bind to a WebSocket instance (last-binder-wins)"""
        self.boundInstanceId = instanceId
    
    def unbind(self, instanceId: Optional[str] = None):
        """
        Unbind from timeline.
        
        If instanceId provided, only unbind if it matches current binding.
        """
        if instanceId is None or self.boundInstanceId == instanceId:
            self.boundInstanceId = None
    
    def isBound(self) -> bool:
        return self.boundInstanceId is not None
    
    def getBoundInstance(self) -> Optional[str]:
        return self.boundInstanceId


class TcpStreamConnection:
    """
    Single TCP client connection to a stream.
    
    Receives data from stream and writes to TCP socket.
    Connection is ephemeral - no persistent state.
    """
    
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 streamId: str, connId: str):
        self.reader = reader
        self.writer = writer
        self.streamId = streamId
        self.connId = connId
        self.running = False
        self.log = getLogger()
        
        # Stats
        self.bytesOut = 0
        self.msgsOut = 0
        
        # Get peer info
        peername = writer.get_extra_info('peername')
        self.peerAddr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
    
    async def write(self, data: bytes):
        """Write data to client"""
        if not self.running:
            return
        self.writer.write(data)
        await self.writer.drain()
        self.bytesOut += len(data)
        self.msgsOut += 1
    
    async def close(self):
        """Close connection"""
        self.running = False
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass
        self.log.info(f"[TcpStream:{self.streamId}] Connection {self.connId} closed "
                     f"({self.bytesOut} bytes, {self.msgsOut} msgs)")


class TcpStreamServer:
    """
    TCP server for a single stream definition.
    
    Manages: socket listener, client connections, data distribution.
    """
    
    def __init__(self, definition: StreamDefinition, dataCallback: Callable[..., Awaitable]):
        self.definition = definition
        self.dataCallback = dataCallback
        self.log = getLogger()
        
        # Server state
        self._server: Optional[asyncio.Server] = None
        self._connections: Dict[str, TcpStreamConnection] = {}
        self._connCounter = 0
        
        # Binding state
        self.binding = StreamBinding()
        
        # Data streaming task
        self._streamTask: Optional[asyncio.Task] = None
    
    async def start(self, host: str = '0.0.0.0') -> tuple[bool, str]:
        """
        Start the TCP server on the configured port.
        
        Returns (success, error_message).
        """
        port = self.definition.port
        
        # Check port availability before binding
        available, err = self._checkPortAvailable(host, port)
        if not available:
            return False, err
        
        try:
            self._server = await asyncio.start_server(
                self._handleConnection, host, port
            )
            self.log.info(f"[TcpStream:{self.definition.streamId}] Listening on {host}:{port} "
                         f"({self.definition.name})")
            
            # Start data streaming if enabled
            if self.definition.enabled:
                self._startStreaming()
            
            return True, ""
        except OSError as e:
            return False, f"Failed to bind port {port}: {e}"
    
    def _checkPortAvailable(self, host: str, port: int) -> tuple[bool, str]:
        """Check if port is available for binding"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
            sock.close()
            return True, ""
        except OSError as e:
            return False, f"Port {port} is not available: {e}"
    
    async def stop(self):
        """Stop the TCP server"""
        # Stop streaming
        if self._streamTask:
            self._streamTask.cancel()
            try:
                await self._streamTask
            except asyncio.CancelledError:
                pass
            self._streamTask = None
        
        # Close all connections
        for conn in list(self._connections.values()):
            await conn.close()
        self._connections.clear()
        
        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        
        self.log.info(f"[TcpStream:{self.definition.streamId}] Stopped")
    
    async def _handleConnection(self, reader: asyncio.StreamReader, 
                                 writer: asyncio.StreamWriter):
        """Handle new client connection"""
        self._connCounter += 1
        connId = f"{self.definition.streamId}-{self._connCounter}"
        
        conn = TcpStreamConnection(reader, writer, self.definition.streamId, connId)
        conn.running = True
        self._connections[connId] = conn
        
        self.log.info(f"[TcpStream:{self.definition.streamId}] Client connected from {conn.peerAddr}")
        
        try:
            # Wait for disconnect (we push data, client just receives)
            while conn.running:
                try:
                    data = await asyncio.wait_for(reader.read(1), timeout=1.0)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            self.log.debug(f"[TcpStream:{self.definition.streamId}] Connection {connId} error: {e}")
        finally:
            self._connections.pop(connId, None)
            await conn.close()
    
    def _startStreaming(self):
        """Start the data streaming task"""
        if self._streamTask is None or self._streamTask.done():
            self._streamTask = asyncio.create_task(self._streamData())
    
    async def _streamData(self):
        """
        Stream data to all connected clients.
        
        LIVE-follow by default.
        If bound to timeline, follows bound instance's cursor.
        """
        self.log.info(f"[TcpStream:{self.definition.streamId}] Streaming started")
        
        eventCount = 0
        lastLogTime = time.perf_counter()
        
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
                formatStart = time.perf_counter()
                output = self._formatOutput(event)
                if output is None:
                    continue
                    
                distributeStart = time.perf_counter()
                await self._distributeToClients(output)
                
                # Track throughput
                eventCount += 1
                now = time.perf_counter()
                if now - lastLogTime >= 5.0:  # Log every 5 seconds
                    rate = eventCount / (now - lastLogTime)
                    self.log.info(f"[TcpStream:{self.definition.streamId}] Throughput: {rate:.1f} events/sec, clients={len(self._connections)}")
                    eventCount = 0
                    lastLogTime = now
                
        except asyncio.CancelledError:
            self.log.info(f"[TcpStream:{self.definition.streamId}] Streaming cancelled")
            raise
        except Exception as e:
            self.log.error(f"[TcpStream:{self.definition.streamId}] Streaming error: {e}")
    
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
        for connId, conn in self._connections.items():
            try:
                await conn.write(data)
            except Exception:
                disconnected.append(connId)
        
        for connId in disconnected:
            conn = self._connections.pop(connId, None)
            if conn:
                await conn.close()
    
    def getConnectionCount(self) -> int:
        """Get number of active connections"""
        return len(self._connections)
    
    def getStatus(self) -> Dict[str, Any]:
        """Get stream status"""
        return {
            'streamId': self.definition.streamId,
            'name': self.definition.name,
            'port': self.definition.port,
            'enabled': self.definition.enabled,
            'running': self._server is not None,
            'connectionCount': self.getConnectionCount(),
            'bound': self.binding.isBound(),
            'boundInstance': self.binding.getBoundInstance(),
            'outputFormat': self.definition.outputFormat,
            'lane': self.definition.lane,
            'selectionSummary': self.definition.selectionSummary()
        }
    
    def bindToTimeline(self, instanceId: str):
        """Bind stream to a WebSocket instance's timeline (last-binder-wins)"""
        self.binding.bind(instanceId)
        self.log.info(f"[TcpStream:{self.definition.streamId}] Bound to instance {instanceId}")
        # Restart streaming to use new binding
        self._restartStreaming()
    
    def unbindFromTimeline(self, instanceId: Optional[str] = None):
        """Unbind stream from timeline (falls back to LIVE-follow)"""
        self.binding.unbind(instanceId)
        self.log.info(f"[TcpStream:{self.definition.streamId}] Unbound, reverting to LIVE-follow")
        # Restart streaming to use LIVE-follow
        self._restartStreaming()
    
    def _restartStreaming(self):
        """Restart the streaming task to pick up binding changes"""
        if self._streamTask and not self._streamTask.done():
            self._streamTask.cancel()
            self._streamTask = None
        # Will restart on next client activity or immediately if clients connected
        if self.getConnectionCount() > 0:
            self._startStreaming()


class TcpStreamManager:
    """
    Manages all TCP stream servers.
    
    Handles: creation, deletion, binding, status queries.
    """
    
    def __init__(self, dataCallback: Callable[..., Awaitable], host: str = '0.0.0.0'):
        self.dataCallback = dataCallback
        self.host = host
        self.log = getLogger()
        
        # Active streams: streamId → TcpStreamServer
        self._streams: Dict[str, TcpStreamServer] = {}
    
    async def startStream(self, definition: StreamDefinition) -> tuple[bool, str]:
        """
        Start a TCP stream server.
        
        Returns (success, message).
        """
        streamId = definition.streamId
        
        if streamId in self._streams:
            return False, f"Stream {streamId} is already running"
        
        server = TcpStreamServer(definition, self.dataCallback)
        success, err = await server.start(self.host)
        
        if success:
            self._streams[streamId] = server
            return True, f"Stream {definition.name} started on port {definition.port}"
        else:
            return False, err
    
    async def stopStream(self, streamId: str) -> tuple[bool, str]:
        """Stop a TCP stream server"""
        server = self._streams.pop(streamId, None)
        if server:
            await server.stop()
            return True, "Stream stopped"
        return False, f"Stream {streamId} not found"
    
    async def restartStream(self, definition: StreamDefinition) -> tuple[bool, str]:
        """Restart a stream with updated definition"""
        await self.stopStream(definition.streamId)
        return await self.startStream(definition)
    
    def getStream(self, streamId: str) -> Optional[TcpStreamServer]:
        """Get a stream server by ID"""
        return self._streams.get(streamId)
    
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
    
    async def stopAll(self):
        """Stop all streams"""
        for server in list(self._streams.values()):
            await server.stop()
        self._streams.clear()
