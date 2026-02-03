"""
BaseDevice: Abstract base class for all hardware device implementations.

DEVICE LIFECYCLE CONTRACT:
==========================
HardwareService orchestrates devices through this standardized lifecycle:

1. DISCOVERY (via Plugin.test())
   - Plugin scans available ports/resources
   - Returns list of candidate devices with metadata

2. CREATION (via Plugin.createDevice())
   - Factory method instantiates device with deviceId, ports, ioLayer, transport
   - Device receives transport reference for autonomous data publication
   - MUST return instance inheriting from BaseDevice

3. INITIALIZATION (via device.open())
   - Device establishes hardware connection
   - Configures hardware for data acquisition
   - MUST be ready to start readLoop after open() completes

4. DATA ACQUISITION (via device.readLoop())
   - Infinite loop that reads data from hardware
   - Calls self.emit(dataType, ts, data) to publish data
   - MUST propagate exceptions for error recovery (see exception handling below)

5. CONFIGURATION (via device.writeTo())
   - Sends commands/config bytes to hardware
   - Called on-demand by control commands
   - Returns status dict with keys: status, deviceId, bytesLength, error (optional)

6. SHUTDOWN (via device.close())
   - Releases hardware resources (ports, connections, handles)
   - Called during normal shutdown or error recovery
   - MUST handle already-closed state gracefully

EXCEPTION HANDLING:
==================
- Let exceptions propagate from readLoop() to trigger automatic recovery
- HardwareService catches exceptions, calls close(), removes from topology
- Next scan cycle rediscovers and restarts the device
- Common exceptions: SerialException, OSError, ConnectionError

REQUIRED METHODS (all abstract):
================================
- async open()          : Initialize hardware
- async close()         : Release resources
- async readLoop()      : Data acquisition loop
- async writeTo(data)   : Send commands to hardware
- getKind()            : Return device type string

PROVIDED METHODS (concrete):
============================
- async emit(dataType, ts, data) : Publish data to transport
- async attachPort(port)         : Claim additional port (optional override)

Property of Uncompromising Sensors LLC.
"""

# Imports
from abc import ABC, abstractmethod
from typing import Dict
import time


class BaseDevice(ABC):
    """Abstract base class for hardware devices"""
    
    def __init__(self, deviceId: str, ioLayer, transport=None, subjectBuilder=None, novaAdapter=None, **kwargs):
        """Initialize base device with required attributes"""
        self.deviceId = deviceId
        self.ioLayer = ioLayer
        self.transport = transport
        self.subjectBuilder = subjectBuilder
        self.novaAdapter = novaAdapter
        self.ports = []
        self.lastSeen = time.time()
        self._rawSequence = 0  # Track raw frame sequence for NOVA
    

    @abstractmethod
    async def open(self):
        """Open device connection(s) - must be implemented by subclass"""
        pass
    

    @abstractmethod
    async def close(self):
        """Close device connection(s) - must be implemented by subclass"""
        pass
    

    async def emit(self, dataType: str, ts: float, data: bytes):
        """Publish data to transport using device's own metadata
        
        Single publish path: NOVA if available, legacy subject-based otherwise.
        Phase 2: No parallel pipelines - "One Way" principle.
        
        Args:
            dataType: Type of data being published (e.g., 'telemetry', 'samples')
            ts: Timestamp of the data
            data: Raw bytes to publish
        """
        # Single path: NOVA takes precedence if configured
        if self.novaAdapter:
            # NOVA Raw lane publish (Phase 2+)
            await self.novaAdapter.publishRaw(self.deviceId, self._rawSequence, data)
            self._rawSequence += 1
        elif self.transport:
            # Legacy subject-based publish (backward compatibility)
            subject = self.subjectBuilder.data(self.deviceId, self.getKind(), dataType)
            await self.transport.publish(subject, data)
    

    async def softwareReset(self):
        """Send software reset command to device before restart (optional override)
        
        Subclasses should override this to send device-specific reset commands.
        Default implementation does nothing.
        """
        pass
    

    @abstractmethod
    async def readLoop(self):
        """Read loop for streaming device data - must be implemented by subclass
        
        IMPORTANT: Let exceptions propagate to hardwareService's runReadLoop wrapper.
        This triggers proper device cleanup:
        - Device.close() is called to release resources
        - Device is removed from topology
        - Next scan cycle will rediscover the device if reconnected
        
        Common exceptions that trigger cleanup:
        - SerialException: Device unplugged or port access lost
        - OSError: Hardware communication failure
        - Any other unhandled exception
        
        Only catch specific, recoverable exceptions if you have a valid reason to continue.
        """
        pass
    

    @abstractmethod
    async def writeTo(self, data: bytes) -> dict:
        """Write raw bytes to device - must be implemented by subclass
        
        Single unified interface for writing configuration, commands, or any raw bytes to hardware.
        
        Args:
            data: Raw bytes to send to device
            
        Returns:
            dict: Status dictionary with keys: status, deviceId, bytesLength, error (optional)
                  status values: 'applied', 'error', 'not_supported'
        """
        pass
    

    @abstractmethod
    def getKind(self) -> str:
        """Return device kind/type string - must be implemented by subclass"""
        pass
    
    async def attachPort(self, port: str) -> bool:
        """Attach additional port to device (default: just claim port without opening)
        
        Args:
            port: Port identifier to attach
            
        Returns:
            bool: True if port was attached, False if already attached
        """
        if port not in self.ports:
            self.ports.append(port)
            if self.logger:
                self.logger.log(f'{self.__class__.__name__} {self.deviceId} claimed port {port}', level='INFO')
            return True
        return False