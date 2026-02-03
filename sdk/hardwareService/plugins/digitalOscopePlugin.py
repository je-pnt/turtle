"""
DigitalOscopePlugin: Plugin for digital oscilloscope device integration.

- Probes hardware ports and identifies digital oscope devices
- Used by hardwareService for device discovery and management
- Provides async test and device factory methods
- Extensible for new digital oscope models

Property of Uncompromising Sensors LLC.
"""

# Imports
import ctypes
from sys import platform

# Local imports
from .basePlugin import BasePlugin
from ..devices.digitalOscopeDevice import DigitalOscopeDevice


# Class
class DigitalOscopePlugin(BasePlugin):
    
    _deviceFound = False  # Track if device already discovered (class-level flag)
    
    def __init__(self):
        super().__init__()
        self.triggerChannel = 0  # Default trigger channel
    

    @staticmethod
    def getKind() -> str:
        return 'digitalOscope'
    

    def configure(self, hardwareConfig: dict, config: dict, triggerChannel: int = 0) -> None:
        """Configure plugin with trigger channel from hardware config.
        
        Args:
            triggerChannel: Channel number as int or string ("0", "1", etc.)
        """
        # Convert to int if needed
        if isinstance(triggerChannel, str):
            try:
                self.triggerChannel = int(triggerChannel)
            except ValueError:
                print(f"[DigitalOscopePlugin] Warning: Invalid triggerChannel '{triggerChannel}', defaulting to 0")
                self.triggerChannel = 0
        elif isinstance(triggerChannel, int):
            self.triggerChannel = triggerChannel
        else:
            print(f"[DigitalOscopePlugin] Warning: Invalid triggerChannel type {type(triggerChannel)}, defaulting to 0")
            self.triggerChannel = 0
    

    @staticmethod
    def resetDiscovery():
        """Reset discovery flag to allow rediscovery (called after device removal)"""
        DigitalOscopePlugin._deviceFound = False
    

    @staticmethod
    async def test(ports: list, ioLayer) -> list:
        """Probe for digital discovery, return candidates (only once)"""
        
        # Skip if already found
        if DigitalOscopePlugin._deviceFound:
            return []
        
        candidates = []
        
        try:
            def _probe():
                
                if platform.startswith("win"):
                    dwf = ctypes.cdll.dwf
                elif platform.startswith("darwin"):
                    dwf = ctypes.cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
                else:
                    dwf = ctypes.cdll.LoadLibrary("libdwf.so")
                
                cDevices = ctypes.c_int()
                dwf.FDwfEnum(ctypes.c_int(0), ctypes.byref(cDevices))
                return cDevices.value > 0
            
            result = await ioLayer.runInExecutor(_probe)
            
            if result:
                DigitalOscopePlugin._deviceFound = True
                candidates.append({'deviceId': 'digitalOscope', 'kind': 'digitalOscope','meta': {}})
        
        except Exception as e:
            pass 
        
        return candidates
    
    
    async def createDevice(self, deviceId: str, ports: list, meta: dict, ioLayer, transport=None, subjectBuilder=None, novaAdapter=None):
        """Create digital oscope device instance with configured trigger channel"""
        return DigitalOscopeDevice(deviceId, ioLayer, transport=transport, subjectBuilder=subjectBuilder, novaAdapter=novaAdapter, triggerChannel=self.triggerChannel)
