
"""
AnalogOscopePlugin: Plugin for analog oscilloscope device integration.

- Probes hardware ports and identifies analog oscope devices
- Used by hardwareService for device discovery and management
- Provides async test and device factory methods
- Extensible for new analog oscope models

Property of Uncompromising Sensors LLC.
"""

# Imports
import ctypes
from sys import platform

# Local imports
from .basePlugin import BasePlugin
from ..devices.analogOscopeDevice import AnalogOscopeDevice

# Class
class AnalogOscopePlugin(BasePlugin):
    
    _deviceFound = False  # Track if device already discovered (class-level flag)
    

    def __init__(self):
        super().__init__()
        self.triggerChannel = 1  # Default trigger channel
    

    @staticmethod
    def getKind() -> str:
        return 'analogOscope'
    

    def configure(self, hardwareConfig: dict, config: dict, triggerChannel: int = 1) -> None:
        """Configure plugin with trigger channel from hardware config.
        
        Args:
            triggerChannel: Channel number as int or string ("1", "2", etc.)
        """
        # Convert to int if needed
        if isinstance(triggerChannel, str):
            try:
                self.triggerChannel = int(triggerChannel)
            except ValueError:
                print(f"[AnalogOscopePlugin] Warning: Invalid triggerChannel '{triggerChannel}', defaulting to 1")
                self.triggerChannel = 1
        elif isinstance(triggerChannel, int):
            self.triggerChannel = triggerChannel
        else:
            print(f"[AnalogOscopePlugin] Warning: Invalid triggerChannel type {type(triggerChannel)}, defaulting to 1")
            self.triggerChannel = 1
    

    @staticmethod
    def resetDiscovery():
        """Reset discovery flag to allow rediscovery (called after device removal)"""
        AnalogOscopePlugin._deviceFound = False
    
    
    @staticmethod
    async def test(ports: list, ioLayer) -> list:
        """Probe for analog discovery, return candidates (only once)"""

        # Skip if already found
        if AnalogOscopePlugin._deviceFound:
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
                
                # Check for connected devices
                cDevices = ctypes.c_int()
                dwf.FDwfEnum(ctypes.c_int(0), ctypes.byref(cDevices))
                return cDevices.value > 0
            
            result = await ioLayer.runInExecutor(_probe)
            
            if result:
                AnalogOscopePlugin._deviceFound = True
                candidates.append({'deviceId': 'analogOscope','kind': 'analogOscope','meta': {}})
                print('[AnalogOscopePlugin] Found Analog Discovery')
        
        except Exception as e:
            pass 
        
        return candidates
    
    async def createDevice(self, deviceId: str, ports: list, meta: dict, ioLayer, transport=None, subjectBuilder=None, novaAdapter=None):
        """Create analog oscope device instance with configured trigger channel"""
        return AnalogOscopeDevice(deviceId, ioLayer, transport=transport, subjectBuilder=subjectBuilder, novaAdapter=novaAdapter, triggerChannel=self.triggerChannel)
