"""
BasePlugin: Abstract base class for all hardware plugin implementations.

- Defines standard interface for device discovery and instantiation
- Ensures consistent method signatures across plugin types
- Used by hardwareService for plugin lifecycle management
- Makes it clear what new plugins must implement

Property of Uncompromising Sensors LLC.
"""

# Imports
from abc import ABC, abstractmethod
from typing import List, Dict, Optional


class BasePlugin(ABC):
    """Abstract base class for hardware plugins"""
    

    @staticmethod
    @abstractmethod
    def getKind() -> str:
        """Return plugin kind/type string - must be implemented by subclass
        
        Returns:
            str: Unique identifier for this plugin type (e.g., 'm9', 'x5', 'analogOscope')
        """
        pass
    

    @staticmethod
    @abstractmethod
    async def test(ports: List[str], ioLayer) -> List[Dict]:
        """Probe available ports to discover devices - must be implemented by subclass
        
        Args:
            ports: List of available port identifiers to probe
            ioLayer: IO layer instance for connection management
            
        Returns:
            List[Dict]: List of candidate devices, each with keys:
                - deviceId: Unique device identifier
                - kind: Device kind (should match getKind())
                - port: Port identifier (optional, for serial devices)
                - meta: Metadata dictionary with device-specific info
        """
        pass
    

    @staticmethod
    @abstractmethod
    async def createDevice(deviceId: str, ports: List[str], meta: Dict, ioLayer, transport=None, subjectBuilder=None, novaAdapter=None):
        """Factory method to create device instance - must be implemented by subclass
        
        Args:
            deviceId: Unique device identifier
            ports: List of port identifiers assigned to this device
            meta: Metadata dictionary from test() discovery
            ioLayer: IO layer instance for connection management
            transport: Transport instance for legacy data publication (optional)
            subjectBuilder: Subject builder for legacy publish (optional)
            novaAdapter: NOVA adapter for Phase 2+ publish (optional, takes precedence)
            
        Returns:
            Device instance inheriting from BaseDevice, or None if creation fails
            
        Note:
            All returned devices MUST implement the BaseDevice interface:
            - async open(): Initialize hardware connection
            - async close(): Release hardware resources
            - async readLoop(): Main data acquisition loop
            - async writeTo(data): Send commands/config to device
            - getKind(): Return device type string
            - emit(dataType, ts, data): Publish data (provided by BaseDevice)
        """
        pass
    
    
    @staticmethod
    def configure(hardwareConfig: dict, config: dict) -> None:
        """Optional configuration hook called during plugin loading
        
        Args:
            hardwareConfig: Hardware-specific configuration dictionary
            config: General application configuration dictionary
        """
        pass
