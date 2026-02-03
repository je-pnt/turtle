"""
NOVA UDP Stream Server - UDP unicast/broadcast output.

Sends data to configured target address (host:port).
No listening required - just sends to target.

Inherits streaming logic from BaseStreamServer.

Property of Uncompromising Sensors LLC.
"""

import asyncio
import socket
from typing import Optional, Tuple

from nova.server.streams.base import BaseStreamServer, StreamConnection
from nova.server.streamStore import StreamDefinition


class UdpStreamConnection(StreamConnection):
    """
    UDP 'connection' - represents our outbound socket to a target.
    """
    
    def __init__(self, transport: asyncio.DatagramTransport, targetAddr: Tuple[str, int],
                 streamId: str, connId: str):
        super().__init__(streamId, connId)
        self.transport = transport
        self.targetAddr = targetAddr
        self.peerAddr = f"{targetAddr[0]}:{targetAddr[1]}"
    
    async def write(self, data: bytes):
        """Send data to UDP target"""
        if not self.running:
            return
        try:
            self.transport.sendto(data, self.targetAddr)
            self._trackWrite(len(data))
        except Exception as e:
            self.log.error(f"[UDP:{self.streamId}] Send error to {self.peerAddr}: {e}")
    
    async def close(self):
        """Close UDP connection"""
        self.running = False
        self.log.info(f"[UDP:{self.streamId}] Closed target {self.peerAddr} "
                     f"({self.bytesOut} bytes, {self.msgsOut} msgs)")


class UdpProtocol(asyncio.DatagramProtocol):
    """Simple UDP protocol - we only send, not receive"""
    
    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        pass  # Ignore incoming data
    
    def error_received(self, exc):
        pass


class UdpStreamServer(BaseStreamServer):
    """
    UDP stream server.
    
    Sends data to a configured target address (host:port).
    Endpoint format: "host:port" (e.g., "localhost:9000" or "192.168.1.10:5000")
    """
    
    def __init__(self, definition: StreamDefinition, dataCallback):
        super().__init__(definition, dataCallback)
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[UdpProtocol] = None
        self._targetAddr: Optional[Tuple[str, int]] = None
    
    def _getProtocolName(self) -> str:
        return "udp"
    
    def _parseEndpoint(self) -> Tuple[str, int]:
        """Parse endpoint as host:port"""
        endpoint = str(self.definition.endpoint)
        if ':' in endpoint:
            host, port = endpoint.rsplit(':', 1)
            return (host or '127.0.0.1', int(port))
        else:
            # Just a port number - default to 127.0.0.1 (localhost)
            return ('127.0.0.1', int(endpoint))
    
    async def start(self, host: str = '0.0.0.0') -> tuple[bool, str]:
        """Start UDP sender (no listening, just outbound)"""
        try:
            self._targetAddr = self._parseEndpoint()
        except ValueError as e:
            return False, f"Invalid endpoint format: {e}"
        
        try:
            loop = asyncio.get_event_loop()
            # Create unbound UDP socket for sending
            self._transport, self._protocol = await loop.create_datagram_endpoint(
                lambda: UdpProtocol(),
                family=socket.AF_INET
            )
            
            # Enable broadcast if target is broadcast address
            if self._targetAddr[0] in ('255.255.255.255', '<broadcast>'):
                sock = self._transport.get_extra_info('socket')
                if sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    self.log.info(f"{self._logPrefix()} Broadcast enabled")
            
            self._running = True
            
            # Create single "connection" to track stats
            connId = self._nextConnId()
            conn = UdpStreamConnection(self._transport, self._targetAddr,
                                       self.definition.streamId, connId)
            self._addConnection(conn)
            
            self.log.info(f"{self._logPrefix()} Sending to {self._targetAddr[0]}:{self._targetAddr[1]} ({self.definition.name})")
            self.log.info(f"{self._logPrefix()} Connection count: {len(self._connections)}, enabled={self.definition.enabled}")
            
            if self.definition.enabled:
                self._startStreaming()
                self.log.info(f"{self._logPrefix()} Streaming task started")
            
            return True, ""
        except OSError as e:
            return False, f"Failed to create UDP socket: {e}"
    
    async def stop(self):
        """Stop UDP sender"""
        self._stopStreaming()
        
        if self._transport:
            self._transport.close()
            self._transport = None
        
        self._connections.clear()
        self._running = False
        self.log.info(f"{self._logPrefix()} Stopped")
    
    def getConnectionCount(self) -> int:
        """UDP has 1 'connection' (our target) if transport exists"""
        return len(self._connections)
