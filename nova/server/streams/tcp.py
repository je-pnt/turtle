"""
NOVA TCP Stream Server - TCP socket output.

Inherits all streaming logic from BaseStreamServer.
Only implements TCP-specific socket handling.

Property of Uncompromising Sensors LLC.
"""

import asyncio
import socket
from typing import Optional

from nova.server.streams.base import BaseStreamServer, StreamConnection
from nova.server.streamStore import StreamDefinition


class TcpStreamConnection(StreamConnection):
    """TCP client connection"""
    
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 streamId: str, connId: str):
        super().__init__(streamId, connId)
        self.reader = reader
        self.writer = writer
        
        # Get peer info
        peername = writer.get_extra_info('peername')
        self.peerAddr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
    
    async def write(self, data: bytes):
        """Write data to TCP client"""
        if not self.running:
            return
        self.writer.write(data)
        await self.writer.drain()
        self._trackWrite(len(data))
    
    async def close(self):
        """Close TCP connection"""
        self.running = False
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass
        self.log.info(f"[TCP:{self.streamId}] Connection {self.connId} closed "
                     f"({self.bytesOut} bytes, {self.msgsOut} msgs)")


class TcpStreamServer(BaseStreamServer):
    """
    TCP stream server.
    
    Listens on configured port, pushes data to connected clients.
    """
    
    def __init__(self, definition: StreamDefinition, dataCallback):
        super().__init__(definition, dataCallback)
        self._server: Optional[asyncio.Server] = None
    
    def _getProtocolName(self) -> str:
        return "tcp"
    
    async def start(self, host: str = '0.0.0.0') -> tuple[bool, str]:
        """Start TCP server on configured port"""
        port = int(self.definition.endpoint)
        
        # Check port availability
        available, err = self._checkPortAvailable(host, port)
        if not available:
            return False, err
        
        try:
            self._server = await asyncio.start_server(
                self._handleConnection, host, port
            )
            self._running = True
            self.log.info(f"{self._logPrefix()} Listening on {host}:{port} ({self.definition.name})")
            
            if self.definition.enabled:
                self._startStreaming()
            
            return True, ""
        except OSError as e:
            return False, f"Failed to bind port {port}: {e}"
    
    async def stop(self):
        """Stop TCP server"""
        self._stopStreaming()
        await self._closeAllConnections()
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        
        self._running = False
        self.log.info(f"{self._logPrefix()} Stopped")
    
    def _checkPortAvailable(self, host: str, port: int) -> tuple[bool, str]:
        """Check if port is available for binding"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
            sock.close()
            return True, ""
        except OSError as e:
            return False, f"Port {port} is not available: {e}"
    
    async def _handleConnection(self, reader: asyncio.StreamReader, 
                                 writer: asyncio.StreamWriter):
        """Handle new TCP client connection"""
        connId = self._nextConnId()
        conn = TcpStreamConnection(reader, writer, self.definition.streamId, connId)
        self._addConnection(conn)
        
        self.log.info(f"{self._logPrefix()} Client connected from {conn.peerAddr}")
        
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
            self.log.debug(f"{self._logPrefix()} Connection {connId} error: {e}")
        finally:
            self._removeConnection(connId)
            await conn.close()
