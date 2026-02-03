"""
TransportFactory: Selects and instantiates transport adapters by URI scheme.
Purpose: Central registry and factory for all transport implementations.
Usage: createTransport(uri) -> TransportBase

Property of Uncompromising Sensors LLC.
"""


# Imports
from typing import Dict, Type, Optional
from urllib.parse import urlparse

# Local imports
from .transportBase import TransportBase


# Class
class TransportRegistry:
    """TransportRegistry() -> registry for URI scheme -> adapter class"""
    

    def __init__(self):
        self._adapters: Dict[str, Type[TransportBase]] = {}
    

    def register(self, scheme: str, adapterClass: Type[TransportBase]) -> None:
        if not issubclass(adapterClass, TransportBase):
            raise TypeError(f"Adapter {adapterClass} must be a TransportBase subclass")
        self._adapters[scheme.lower()] = adapterClass
    

    def get(self, scheme: str) -> Optional[Type[TransportBase]]:
        return self._adapters.get(scheme.lower())
    

    def schemes(self) -> list:
        return list(self._adapters.keys())


# Global default registry (can be replaced/injected for testing)
_defaultRegistry = TransportRegistry()


def registerAdapter(scheme: str, adapterClass: Type[TransportBase]) -> None:
    _defaultRegistry.register(scheme, adapterClass)


def createTransport(uri: str, registry: Optional[TransportRegistry] = None, **opts) -> TransportBase:

    # Parse URI
    try:
        parsed = urlparse(uri)
    except Exception as e:
        raise ValueError(f"Invalid URI '{uri}': {e}") from e
    
    if not parsed.scheme:
        raise ValueError(f"URI must include scheme (e.g., 'nng+ipc://', 'nats://'): {uri}")
    
    # Select adapter
    reg = registry or _defaultRegistry
    adapterClass = reg.get(parsed.scheme)
    
    if not adapterClass:
        available = ', '.join(reg.schemes()) or 'none'
        raise ValueError( f"No adapter registered for scheme '{parsed.scheme}'. " f"Available schemes: {available}")
    
    # Instantiate adapter
    try:
        transport = adapterClass(**opts)
    except TypeError as e:
        raise TypeError(f"Failed to instantiate {adapterClass.__name__}: {e}. " f"Check that provided options match adapter constructor.") from e
    
    return transport


def getDefaultRegistry() -> TransportRegistry:
    return _defaultRegistry