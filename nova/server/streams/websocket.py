"""
NOVA WebSocket Stream Server - WebSocket output via aiohttp.

Inherits all streaming logic from BaseStreamServer.
Uses aiohttp WebSocket - must be registered with main server's app.

Property of Uncompromising Sensors LLC.
"""

import asyncio
from typing import Optional, Dict, Any
from aiohttp import web, WSMsgType

from nova.server.streams.base import BaseStreamServer, StreamConnection
from nova.server.streamStore import StreamDefinition


class WsStreamConnection(StreamConnection):
    """WebSocket client connection"""
    
    def __init__(self, ws: web.WebSocketResponse, streamId: str, connId: str, peerAddr: str):
        super().__init__(streamId, connId)
        self.ws = ws
        self.peerAddr = peerAddr
    
    async def write(self, data: bytes):
        """Write data to WebSocket client"""
        if not self.running or self.ws.closed:
            return
        await self.ws.send_bytes(data)
        self._trackWrite(len(data))
    
    async def close(self):
        """Close WebSocket connection"""
        self.running = False
        try:
            if not self.ws.closed:
                await self.ws.close()
        except Exception:
            pass
        self.log.info(f"[WS:{self.streamId}] Connection {self.connId} closed "
                     f"({self.bytesOut} bytes, {self.msgsOut} msgs)")


class WsStreamServer(BaseStreamServer):
    """
    WebSocket stream server.
    
    Unlike TCP, doesn't create its own listener.
    Registered as a route handler on the main aiohttp app.
    """
    
    def __init__(self, definition: StreamDefinition, dataCallback):
        super().__init__(definition, dataCallback)
        # Path is the endpoint (e.g., "mystream" for /ws/streams/mystream)
        self._path = definition.endpoint
    
    def _getProtocolName(self) -> str:
        return "ws"
    
    @property
    def path(self) -> str:
        """WebSocket path (for route registration)"""
        return self._path
    
    async def start(self, host: str = '0.0.0.0') -> tuple[bool, str]:
        """
        'Start' WebSocket server - just marks as running.
        
        Actual route registration happens in StreamManager.
        """
        self._running = True
        self.log.info(f"{self._logPrefix()} Ready at /ws/streams/{self._path} ({self.definition.name})")
        return True, ""
    
    async def stop(self):
        """Stop WebSocket server"""
        self._stopStreaming()
        await self._closeAllConnections()
        self._running = False
        self.log.info(f"{self._logPrefix()} Stopped")
    
    async def handleConnection(self, request: web.Request) -> web.WebSocketResponse:
        """
        Handle incoming WebSocket connection.
        
        Called by aiohttp route handler in server.py.
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        connId = self._nextConnId()
        peername = request.transport.get_extra_info('peername')
        peerAddr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        
        conn = WsStreamConnection(ws, self.definition.streamId, connId, peerAddr)
        self._addConnection(conn)
        
        self.log.info(f"{self._logPrefix()} Client connected from {peerAddr}")
        
        try:
            # Wait for disconnect (we push data, client just receives)
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
                # Ignore client messages - this is output-only
        except Exception as e:
            self.log.debug(f"{self._logPrefix()} Connection {connId} error: {e}")
        finally:
            self._removeConnection(connId)
            await conn.close()
        
        return ws
