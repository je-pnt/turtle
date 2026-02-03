"""
NNG Transport Adapter

API: connect, publish, subscribe, request, close
Options: ipcDir (str, default: platform /tmp or C:\tmp)
URI: nng+ipc://path, nng+tcp://host:port, nng://path
Design: Connectionless, instance-scoped, validated options

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio, os, time
from typing import Callable, Optional, Dict, Any, Set
from urllib.parse import urlparse

# Local Imports
from .transportBase import TransportBase, SubscriptionHandle


# Class
class NngTransport(TransportBase):
    """NNG transport for IPC and TCP."""
    
    # Valid option keys for this adapter
    _VALID_CONNECT_OPTS = {'ipcDir'}
    
    def __init__(self):
        super().__init__()
        
        # Instance-scoped state
        self._ipcDir: Optional[str] = None
        self._uri: Optional[str] = None
        self._scheme: Optional[str] = None         # 'nng', 'nng+ipc', or 'nng+tcp'
        self._tcpHost: Optional[str] = None        # TCP host for nng+tcp://
        self._tcpBasePort: Optional[int] = None    # TCP base port for nng+tcp://
        self._pubSockets: Dict[str, Any] = {}      # subject -> PUB socket
        self._subSockets: Dict[str, Any] = {}      # subject -> SUB socket
        self._subTasks: Dict[str, asyncio.Task] = {}  # subject -> reader task
        self._publishCounters: Dict[str, int] = {}
        self._pynng = None  # Lazy-loaded
    
    @property
    def transportType(self) -> str:
        return 'nng'
    

    async def connect(self, uri: str, **opts) -> None:

        if self._state == 'READY':
            raise RuntimeError('NngTransport already connected')
        
        # Allow reconnection after close by resetting state
        if self._state == 'CLOSED':
            self._state = 'IDLE'
            self._pubSockets.clear()
            self._subSockets.clear()
            self._subTasks.clear()
            self._publishCounters.clear()
        
        # Validate options
        self._validateOptions(opts, self._VALID_CONNECT_OPTS)
        
        # Parse URI
        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        
        if scheme not in ('nng', 'nng+ipc', 'nng+tcp'):
            raise ValueError(f"Unsupported NNG scheme '{scheme}'. "f"Supported: nng, nng+ipc, nng+tcp")
        
        # Lazy-load pynng
        if not self._pynng:
            try:
                import pynng
                self._pynng = pynng
            except ImportError as e:
                raise ImportError('pynng not installed. Run: pip install pynng') from e
        
        # Extract IPC directory from URI if needed
        if scheme in ('nng', 'nng+ipc'):

            # Extract path from URI: nng+ipc://C:\tmp\hwService or nng+ipc:///tmp/hwService
            # netloc on Windows: 'C:\\tmp\\hwService', on Unix: '' (path in parsed.path)
            self._log(f'Parsing IPC URI: netloc={parsed.netloc!r}, path={parsed.path!r}', level='INFO', uri=uri)
            if parsed.netloc:
                self._ipcDir = parsed.netloc      # Windows: nng+ipc://C:\tmp\hwService
            elif parsed.path:
                self._ipcDir = parsed.path        # Unix: nng+ipc:///tmp/hwService
            else:
                raise ValueError(f"NNG IPC URI must include path: 'nng+ipc://C:\\path' or 'nng+ipc:///path'. Got: {uri}")
            self._log(f'Using ipcDir: {self._ipcDir!r}', level='INFO')
            os.makedirs(self._ipcDir, exist_ok=True)
        
        elif scheme == 'nng+tcp':                 # Extract host and port from URI: nng+tcp://host:basePort
            self._tcpHost = parsed.hostname or 'localhost'
            self._tcpBasePort = parsed.port or 5555
            
            if not parsed.hostname:
                self._log(f'No hostname in URI, using localhost', level='WARNING', uri=uri)
            if not parsed.port:
                self._log(f'No port in URI, using default 5555', level='WARNING', uri=uri)
        
        # Store scheme and URI, mark ready
        self._scheme = scheme
        self._uri = uri
        self._endpoint = self._buildEndpoint(parsed)
        self._state = 'READY'
        
        self._connectedAt = time.time()
        
        self._log(f'NngTransport connected', event='connect')
    

    async def publish(self, subject: str, payload: bytes | memoryview, timeout: Optional[float] = None) -> None:

        # Validate state
        if self._state != 'READY':
            raise RuntimeError('NngTransport not connected')
        
        # Convert memoryview to bytes if needed
        if isinstance(payload, memoryview):
            payload = bytes(payload)
        
        # Get or create PUB socket
        if subject not in self._pubSockets:
            sock = self._pynng.Pub0()
            
            # Listen on socket (bind address for TCP, IPC path for IPC)
            bindAddr = self._addrBind(subject)
            sock.listen(bindAddr)
            self._pubSockets[subject] = sock
            self._publishCounters[subject] = 0
            
            self._log(f'Created PUB socket for subject', event='socket_created', subject=subject, endpoint=bindAddr)
        
        sock = self._pubSockets[subject]
        
        # Publish
        try:
            sock.send(payload)
        except Exception as e:
            self._log(f'Publish failed: {e}', level='ERROR')
            raise  # Preserve native exception
    

    async def subscribe(self, subject: str, handler: Callable[[str, bytes], Any], 
                       timeout: Optional[float] = None) -> SubscriptionHandle:
        
        # Validate state
        if self._state != 'READY':
            raise RuntimeError('NngTransport not connected')
        
        if not callable(handler):
            raise ValueError(f"Handler must be callable, got {type(handler)}")
        
        # Create subscription handle
        handle = SubscriptionHandle(subject, self._unsubscribeSubject)
        self._subscriptions[subject] = handle
        
        # Create SUB socket if not already present
        if subject not in self._subSockets:
            sock = self._pynng.Sub0()
            
            # Subscribe to all messages (empty topic = receive all)
            # NNG SUB sockets must explicitly subscribe or they receive nothing
            sock.subscribe(b'')
            
            # Dial socket asynchronously (non-blocking)
            # This allows SUB to connect before PUB exists, with auto-retry
            dialAddr = self._addr(subject)
            sock.dial(dialAddr, block=False)
            self._subSockets[subject] = sock
            
            # Start reader task
            task = asyncio.create_task(self._subscriptionLoop(subject, sock, handler, handle))
            self._subTasks[subject] = task
            
            self._log(f'Created SUB: {subject}', event='subscribe', endpoint=dialAddr)
        
        return handle
    

    async def close(self, timeout: Optional[float] = None) -> None:

        # Validate state
        if self._state == 'CLOSED':
            return
        
        self._state = 'CLOSED'
        
        # Stop subscription reader tasks
        for subject, task in list(self._subTasks.items()):
            task.cancel()
            try:
                if timeout:
                    await asyncio.wait_for(task, timeout=timeout)
                else:
                    await task
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        self._subTasks.clear()
        
        # Close SUB sockets
        for sock in self._subSockets.values():
            try:
                sock.close()
            except Exception as e:
                self._log(f'Close SUB error: {e}', level='WARNING')
        
        self._subSockets.clear()
        
        # Close PUB sockets
        for sock in self._pubSockets.values():
            try:
                sock.close()
            except Exception as e:
                self._log(f'Close PUB error: {e}', level='WARNING')
        
        self._pubSockets.clear()
        self._subscriptions.clear()
        
        self._log('NngTransport closed', event='close')
    

    def status(self) -> Dict[str, Any]:
        base = super().status(); base['pubSockets'] = len(self._pubSockets); base['subSockets'] = len(self._subSockets); return base
    

    # ===== Internal Methods =====
    async def _subscriptionLoop(self, subject: str, sock: Any, 
                                handler: Callable, handle: SubscriptionHandle):
        
        self._log(f'Start sub loop: {subject}', event='sub_loop_start')
        
        while self._state == 'READY' and handle.active:
            try:

                # Receive message
                payload = await asyncio.to_thread(sock.recv)             # Blocking receive (in thread to avoid blocking event loop)
                handle._incrementMessages()                              # Increment message counter
                
                # Invoke handler
                if asyncio.iscoroutinefunction(handler):
                    await handler(subject, payload)
                else:
                    handler(subject, payload)
            
            except Exception as e:
                if self._state == 'READY' and handle.active:
                    self._log(f'Sub loop error: {e}', level='ERROR')
                    await asyncio.sleep(0.1)  # Backoff on error
                else:
                    break  # Shutting down
        
        self._log(f'Sub loop exit: {subject}', event='sub_loop_exit')
    

    async def _unsubscribeSubject(self, handle: SubscriptionHandle):

        subject = handle.subject
        
        # Cancel task
        if subject in self._subTasks:
            task = self._subTasks[subject]
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            del self._subTasks[subject]
        
        # Close socket
        if subject in self._subSockets:
            try:
                self._subSockets[subject].close()
            except Exception:
                pass
            del self._subSockets[subject]
        
        # Remove from subscriptions
        await self._unsubscribeHandle(handle)
        
        self._log(f'Unsubscribed: {subject}', event='unsubscribe')
    

    def _getSocketPath(self, subject: str) -> str:
        filename = subject.replace('.', '_') + '.ipc'
        return os.path.join(self._ipcDir, filename)
    
    def _addr(self, subject: str) -> str:
        """Generate socket address for subject (dial/connect endpoint).
        
        For TCP: returns tcp://<host>:<port> where port = basePort + hash(subject) % 1000
        For IPC: returns ipc://<path>
        
        Port allocation: Uses deterministic hash to assign each subject a unique port
        within range [basePort, basePort+999]. Collision risk exists with >1000 subjects."""

        if self._scheme == 'nng+tcp':
            offset = abs(hash(subject)) % 1000
            port = self._tcpBasePort + offset
            return f'tcp://{self._tcpHost}:{port}'
        else:
            return f'ipc://{self._getSocketPath(subject)}'
    
    def _addrBind(self, subject: str) -> str:
        """Generate socket address for subject (listen/bind endpoint).
        For TCP: returns tcp://0.0.0.0:<port> (binds to all interfaces)
        For IPC: returns ipc://<path>"""

        if self._scheme == 'nng+tcp':
            offset = abs(hash(subject)) % 1000
            port = self._tcpBasePort + offset
            return f'tcp://0.0.0.0:{port}'
        else:
            return f'ipc://{self._getSocketPath(subject)}'
    

    def _buildEndpoint(self, parsed) -> str:
        """Build endpoint from URI."""
        if parsed.scheme in ('nng', 'nng+ipc'):
            return f'ipc://{self._ipcDir}'
        elif parsed.scheme == 'nng+tcp':
            return f'tcp://{parsed.netloc}'
        return parsed.geturl()
    

    def _validateOptions(self, opts: dict, validKeys: Set[str]):
        """Validate options."""
        unknown = set(opts.keys()) - validKeys
        if unknown:
            raise ValueError(f"Unknown options for NngTransport: {unknown}. Valid options: {validKeys}")
    

    # ===== Request/Reply Support (Optional Extension) =====
    async def registerHandler(self, subject: str, handler: Callable[[str, bytes], bytes]):

        # Validate state
        if self._state != 'READY':
            raise RuntimeError('NngTransport not connected')
        
        sock = self._pynng.Rep0()
        
        try:
            bindAddr = self._addrBind(subject)
            sock.listen(bindAddr)
            self._log(f'Registered REP: {subject}', event='register_handler', subject=subject, endpoint=bindAddr)
        except Exception as e:
            self._log(f'Register REP error: {e}', level='ERROR')
            raise
        
        # Start handler loop
        task = asyncio.create_task(self._handlerLoop(sock, subject, handler))
        self._subTasks[f'handler:{subject}'] = task
    

    async def _handlerLoop(self, sock: Any, subject: str, handler: Callable):

        self._log(f'Start handler loop: {subject}', event='handler_loop_start')
        
        while self._state == 'READY':
            try:
                request = await asyncio.to_thread(sock.recv)
                
                # Invoke handler
                if asyncio.iscoroutinefunction(handler):
                    response = await handler(subject, request)
                else:
                    response = handler(subject, request)
                
                if response is None:
                    response = b'{"status":"ok"}'
                
                await asyncio.to_thread(sock.send, response)
            
            except Exception as e:
                if self._state == 'READY':
                    self._log(f'Handler loop error: {e}', level='ERROR')
                    await asyncio.sleep(0.1)
                else:
                    break
        
        self._log(f'Handler loop exit: {subject}', event='handler_loop_exit')
    

    async def request(self, subject: str, payload: bytes) -> bytes:
        
        # Validate state
        if self._state != 'READY':
            raise RuntimeError('NngTransport not connected')
        
        sock = self._pynng.Req0()
        
        try:
            dialAddr = self._addr(subject)
            sock.dial(dialAddr)
            
            # Send request and receive response
            await asyncio.to_thread(sock.send, payload)
            response = await asyncio.to_thread(sock.recv)
            
            return response
            
        except Exception as e:
            self._log(f'Request error: {e}', level='ERROR')
            raise
        finally:
            try:
                sock.close()
            except:
                pass