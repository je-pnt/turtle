"""sdk.transport - Modular, connectionless-first transport layer.

Public API:
    - TransportBase: Abstract base class for transport adapters
    - SubscriptionHandle: Lightweight subscription handle
    - createTransport: Factory function for creating transports from URIs
    - registerAdapter: Register custom transport adapters
    - NngTransport: NNG transport implementation
    - NatsTransport: NATS transport implementation (if available)

Default Adapters:
    - NngTransport: Registered for 'nng', 'nng+ipc', 'nng+tcp' schemes
    - NatsTransport: Registered for 'nats' scheme (if available)

Usage:
    # Create and connect transport
    from sdk.transport import createTransport
    
    transport = createTransport('nng+ipc:///tmp/hwService')
    await transport.connect('nng+ipc:///tmp/hwService')
    
    # Publish data
    await transport.publish('device.data', b'{"temp": 25.5}')
    
    # Subscribe to data
    def handler(subject, payload):
        print(f"Received on {subject}: {payload}")
    
    handle = await transport.subscribe('device.data', handler)
    
    # Later: unsubscribe
    await handle.unsubscribe()
    
    # Cleanup
    await transport.close()
    
Property of Uncompromising Sensors LLC.
"""

from .transportBase import TransportBase, SubscriptionHandle
from .transportFactory import (
    createTransport, 
    registerAdapter, 
    TransportRegistry,
    getDefaultRegistry
)
from .nngTransport import NngTransport

# Register default adapters
registerAdapter('nng', NngTransport)
registerAdapter('nng+ipc', NngTransport)
registerAdapter('nng+tcp', NngTransport)

# Optional: Register NATS adapter if available (plugin pattern)
try:
    from .natsTransport import NatsTransport
    registerAdapter('nats', NatsTransport)
    _nats_available = True
except ImportError:
    _nats_available = False
    NatsTransport = None

__all__ = [
    'TransportBase',
    'SubscriptionHandle',
    'createTransport',
    'registerAdapter',
    'TransportRegistry',
    'getDefaultRegistry',
    'NngTransport',
    'NatsTransport'
]