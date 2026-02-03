"""
NATS Transport Adapter

API:
    connect(uri, **opts)      # Connect to NATS server
    publish(subject, payload) # Publish message
    subscribe(subject, handler) # Subscribe to subject
    request(subject, payload) # Request/Reply
    close()                  # Close connection

Options:
    name (str, default: auto)
    authToken (str)
    user (str)
    password (str)
    noEcho (bool, default: False)
    maxReconnectAttempts (int, default: 60)
    reconnectTimeWait (float, default: 2.0)
    pedantic (bool, default: False)
    verbose (bool, default: False)

URI Schemes:
    nats://host:port
    nats://user:pass@host:port

Design:
    - Connectionless, instance-scoped, no globals
    - Subscriptions managed per subject
    - Options validated, unknowns rejected
    - No core dependencies, plugin-friendly

Property of Uncompromising Sensors LLC.
"""


# Imports
import asyncio, time
from typing import Callable, Optional, Dict, Any, Set
from urllib.parse import urlparse

# Local imports
from .transportBase import TransportBase, SubscriptionHandle


# Class
class NatsTransport(TransportBase):
    """NATS transport for pub/sub and request/reply."""
    
    # Valid option keys for this adapter
    _VALID_CONNECT_OPTS = {
        'name', 'authToken', 'user', 'password', 'noEcho',
        'maxReconnectAttempts', 'reconnectTimeWait', 'pedantic', 'verbose'}
    

    def __init__(self):
        super().__init__()
        self._uri: Optional[str] = None
        self._nc = None                             # NATS client (lazy-loaded)
        self._nats_module = None                    # nats module reference
        self._publishCounter: int = 0
        self._subscriptionMap: Dict[str, Any] = {}  # subject -> NATS subscription object
    

    @property
    def transportType(self) -> str:
        return 'nats'
    

    async def connect(self, uri: str, **opts) -> None:

        # Ensure ready for connection
        if self._state == 'READY':
            raise RuntimeError('NatsTransport already connected')
        
        # Validate options
        self._validateOptions(opts, self._VALID_CONNECT_OPTS)
        
        # Parse URI
        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        
        if scheme != 'nats':
            raise ValueError(f"Unsupported NATS scheme '{scheme}'. "f"Supported: nats")
        
        # Lazy-load nats module
        if not self._nats_module:
            try:
                import nats
                self._nats_module = nats
            except ImportError as e:
                raise ImportError('nats-py not installed. Run: pip install nats-py') from e
        
        # Build server URL
        servers = [self._buildServerUrl(parsed)]
        
        # Build NATS options
        nats_opts = {
            'servers': servers,
            'name': opts.get('name', f'transport-{self._instanceId}'),
            'max_reconnect_attempts': opts.get('maxReconnectAttempts', 60),
            'reconnect_time_wait': opts.get('reconnectTimeWait', 2.0),
            'pedantic': opts.get('pedantic', False),
            'verbose': opts.get('verbose', False),
        }
        
        # Add authentication
        if 'authToken' in opts:
            nats_opts['token'] = opts['authToken']
        elif parsed.username and parsed.password:
            nats_opts['user'] = parsed.username
            nats_opts['password'] = parsed.password
        elif 'user' in opts and 'password' in opts:
            nats_opts['user'] = opts['user']
            nats_opts['password'] = opts['password']
        
        # Add no_echo
        if opts.get('noEcho'):
            nats_opts['no_echo'] = True
        
        # Add callbacks for connection events
        nats_opts['error_cb'] = self._errorCallback
        nats_opts['disconnected_cb'] = self._disconnectedCallback
        nats_opts['reconnected_cb'] = self._reconnectedCallback
        nats_opts['closed_cb'] = self._closedCallback
        
        # Connect to NATS
        try:
            self._nc = await self._nats_module.connect(**nats_opts)
        except Exception as e:
            self._log(f'Connection failed: {e}', level='ERROR')
            raise  # Preserve native exception
        
        # Store URI and mark ready
        self._uri = uri
        self._endpoint = servers[0]
        self._state = 'READY'
        
        self._connectedAt = time.time()
        
        self._log('NatsTransport connected', event='connect')
    

    async def publish(self, subject: str, payload: bytes | memoryview, timeout: Optional[float] = None) -> None:

        if self._state != 'READY':
            raise RuntimeError('NatsTransport not connected')
        
        if not self._nc:
            raise RuntimeError('NATS client not initialized')
        
        # Publish (fire and forget)
        try:
            await self._nc.publish(subject, payload)
            
            # Update counter and log occasionally
            self._publishCounter += 1
            if self._publishCounter % 100 == 1:
                self._log(f'Published message: {subject}', event='publish')
        
        except Exception as e:
            self._log(f'Publish failed: {e}', level='ERROR')
            raise  # Preserve native exception
    

    async def subscribe(self, subject: str, handler: Callable[[str, bytes], Any], 
                       timeout: Optional[float] = None) -> SubscriptionHandle:
        
        if self._state != 'READY':
            raise RuntimeError('NatsTransport not connected')
        
        if not self._nc:
            raise RuntimeError('NATS client not initialized')
        
        if not callable(handler):
            raise ValueError(f"Handler must be callable, got {type(handler)}")
        
        # Create subscription handle
        handle = SubscriptionHandle(subject, self._unsubscribeSubject)
        self._subscriptions[subject] = handle
        
        # Create message callback
        async def message_callback(msg):
            """Internal callback that wraps user handler."""
            try:
                handle._incrementMessages()
                if asyncio.iscoroutinefunction(handler):
                    await handler(msg.subject, msg.data)
                else:
                    handler(msg.subject, msg.data)
            except Exception as e:
                self._log(f'Handler error: {e}', level='ERROR')
        
        # Subscribe via NATS
        try:
            sub = await self._nc.subscribe(subject, cb=message_callback)
            self._subscriptionMap[subject] = sub
            self._log(f'Subscribed: {subject}', event='subscribe')
        except Exception as e:
            del self._subscriptions[subject]
            self._log(f'Subscribe failed: {e}', level='ERROR')
            raise  # Preserve native exception
        
        return handle
    

    async def close(self, timeout: Optional[float] = None) -> None:

        # Validate state
        if self._state == 'CLOSED':
            return
        self._state = 'CLOSED'
        
        # Unsubscribe all
        for subject, sub in list(self._subscriptionMap.items()):
            try:
                await sub.unsubscribe()
            except Exception as e:
                self._log(f'Unsubscribe error: {e}', level='WARNING')
        
        self._subscriptionMap.clear()
        self._subscriptions.clear()
        
        # Close NATS connection
        if self._nc:
            try:
                if timeout:
                    await asyncio.wait_for(self._nc.close(), timeout=timeout)
                else:
                    await self._nc.close()
            except asyncio.TimeoutError:
                self._log('Close timed out', level='WARNING')
            except Exception as e:
                self._log(f'Close error: {e}', level='WARNING')
        
        self._nc = None
        
        self._log('NatsTransport closed', event='close')
    

    async def drain(self, timeout: Optional[float] = None) -> None:

        # Validate state
        if self._state != 'READY' or not self._nc:
            return
        
        try:
            # NATS drain flushes pending messages and stops accepting new ones
            if timeout:
                await asyncio.wait_for(self._nc.drain(), timeout=timeout)
            else:
                await self._nc.drain()
            
            self._log('Drained connection', event='drain')
        
        except asyncio.TimeoutError:
            self._log('Drain timed out', level='WARNING')
        except Exception as e:
            self._log(f'Drain failed: {e}', level='WARNING')
    

    def status(self) -> Dict[str, Any]:

        base = super().status()
        
        if self._nc:
            base['connected'] = self._nc.is_connected
            base['servers'] = self._nc.servers if hasattr(self._nc, 'servers') else None
        else:
            base['connected'] = False
            base['servers'] = None
        
        return base
    

    # ===== Internal Methods =====
    async def _unsubscribeSubject(self, handle: SubscriptionHandle):

        # Get subject
        subject = handle.subject
        
        # Unsubscribe from NATS
        if subject in self._subscriptionMap:
            try:
                await self._subscriptionMap[subject].unsubscribe()
            except Exception as e:
                self._log(f'Unsubscribe error: {e}', level='WARNING')
            
            del self._subscriptionMap[subject]
        
        # Remove from subscriptions
        await self._unsubscribeHandle(handle)
        
        self._log(f'Unsubscribed: {subject}', event='unsubscribe')
    

    def _buildServerUrl(self, parsed) -> str:
        host = parsed.hostname or 'localhost'
        port = parsed.port or 4222
        return f'nats://{host}:{port}'
    
    
    def _validateOptions(self, opts: dict, validKeys: Set[str]):
        unknown = set(opts.keys()) - validKeys
        if unknown:
            raise ValueError(f"Unknown options for NatsTransport: {unknown}. " f"Valid options: {validKeys}")
    

    # ===== NATS Event Callbacks =====
    async def _errorCallback(self, e):
        self._log(f'NATS error: {e}', level='ERROR')
    
    async def _disconnectedCallback(self):
        self._log('NATS disconnected', level='WARNING')
    
    async def _reconnectedCallback(self):
        self._log('NATS reconnected', event='nats_reconnected')
    
    async def _closedCallback(self):
        self._log('NATS connection closed', event='nats_closed')
    

    # ===== Request/Reply Support (Optional Extension) =====
    async def request(self, subject: str, payload: bytes | memoryview, 
                     timeout: Optional[float] = 1.0) -> bytes:

        # Validate state
        if self._state != 'READY':
            raise RuntimeError('NatsTransport not connected')
        
        if not self._nc:
            raise RuntimeError('NATS client not initialized')
        
        # Convert memoryview to bytes if needed
        if isinstance(payload, memoryview):
            payload = bytes(payload)
        
        try:
            # Send request and wait for response
            if timeout:
                response = await asyncio.wait_for(self._nc.request(subject, payload), timeout=timeout)
            else:
                response = await self._nc.request(subject, payload)
            
            self._log(f'Request completed: {subject}', event='request')
            
            return response.data
        
        except asyncio.TimeoutError:
            self._log('Request timed out', level='ERROR')
            raise
        
        except Exception as e:
            self._log(f'Request failed: {e}', level='ERROR')
            raise

    async def registerHandler(self, subject: str, handler: Callable[[str, bytes], bytes]):
        """Register a request/response handler (server-side).
        
        Handler receives (subject, request_bytes) and returns response_bytes.
        This is a transport-agnostic wrapper around NATS request/response.
        """
        if self._state != 'READY':
            raise RuntimeError('NatsTransport not connected')
        
        if not self._nc:
            raise RuntimeError('NATS client not initialized')
        
        async def wrappedHandler(msg):
            """Wrapper to call user handler and send response."""
            try:
                # Call user handler
                response = await handler(msg.subject, msg.data)
                
                # Send response
                await self._nc.publish(msg.reply, response)
            except Exception as e:
                self._log(f'Handler error: {e}', level='ERROR', subject=subject)
                # Send error response
                import json
                error_response = json.dumps({"error": str(e)}).encode('utf-8')
                await self._nc.publish(msg.reply, error_response)
        
        # Subscribe to requests on this subject
        sub = await self._nc.subscribe(subject, cb=wrappedHandler)
        
        # Track subscription
        handle = SubscriptionHandle(subject, self._unsubscribeHandle)
        self._subscriptions[subject] = handle
        
        self._log(f'Registered handler: {subject}', event='register_handler', subject=subject)
        
        return handle
