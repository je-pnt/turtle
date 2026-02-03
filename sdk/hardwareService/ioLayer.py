"""
I/O Layer - Generic transport abstraction for hardware communication.

Supports serial, socket, and custom transports with unified API.
Manages async I/O and provides executor for blocking calls.

Property of Uncompromising Sensors LLC.
"""


# Imports
import asyncio, concurrent.futures, serial_asyncio
from sdk.logging import getLogger

# Module-level logger (auto-detects: 'hardwareService.ioLayer')
log = getLogger()


class IoLayer:
    
    def __init__(self):
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    
    # --- API Methods ---
    async def openConnection(self, transportType: str, **kwargs):
        transportType = transportType.lower()
        if transportType == 'serial':
            return await self._openSerial(**kwargs)
        elif transportType == 'socket':
            return await self._openSocket(**kwargs)
        elif transportType == 'custom':
            return await self._openCustom(**kwargs)
        else:
            raise ValueError(f'Unsupported transport type: {transportType}')
    

    async def closeConnection(self, writer):
        if hasattr(writer, 'close'):
            writer.close()
            if hasattr(writer, 'wait_closed'):
                await writer.wait_closed()
    

    async def send(self, writer, data: bytes):
        writer.write(data)
        await writer.drain()
    

    async def receive(self, reader, size: int = 4096):
        return await reader.read(size)
    

    async def runInExecutor(self, func):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, func)
        
    
    def shutdown(self):
        self.executor.shutdown(wait=False)

    
    # --- Internal Methods ---
    async def _openSerial(self, port: str, baudrate: int, **kwargs):
        reader, writer = await serial_asyncio.open_serial_connection(url=port, baudrate=baudrate)
        log.info('Opened serial connection', port=port, baudrate=baudrate)
        return reader, writer
    

    async def _openSocket(self, host: str, port: int, protocol: str = 'tcp', **kwargs):
        protocol = protocol.lower()
        if protocol == 'tcp':
            reader, writer = await asyncio.open_connection(host, port)
            log.info('Opened TCP socket', host=host, port=port)
            return reader, writer
        elif protocol == 'udp':
            loop = asyncio.get_event_loop()
            transport, protocolInstance = await loop.create_datagram_endpoint(lambda: _UdpProtocol(), remote_addr=(host, port))
            log.info('Opened UDP socket', host=host, port=port)
            return _UdpReader(transport, protocolInstance), _UdpWriter(transport, protocolInstance)
        else:
            raise ValueError(f'Unsupported socket protocol: {protocol}')
    

    async def _openCustom(self, handler, **kwargs):
        reader, writer = await handler(**kwargs)
        log.info('Opened custom connection', handler=handler.__name__)
        return reader, writer
    

# --- Internal Duck Typing Example with UDP ---
class _UdpProtocol(asyncio.DatagramProtocol):
    
    def __init__(self):
        self.queue = asyncio.Queue()
        self.transport = None
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        self.queue.put_nowait(data)
    
    def error_received(self, exc):
        pass


class _UdpReader:
    
    def __init__(self, transport, protocol):
        self.transport = transport
        self.protocol = protocol
    
    async def read(self, size=4096):
        # --- Read next datagram from queue ---
        return await self.protocol.queue.get()


class _UdpWriter:
    
    def __init__(self, transport, protocol):
        self.transport = transport
        self.protocol = protocol
    
    def write(self, data):
        # --- Send datagram ---
        self.transport.sendto(data)
    
    async def drain(self):
        # --- Compatibility method (UDP is fire-and-forget) ---
        pass
    
    def close(self):
        # --- Close transport ---
        self.transport.close()
    
    async def wait_closed(self):
        # --- Compatibility method ---
        pass