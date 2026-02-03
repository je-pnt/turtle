"""
NOVA Driver Registry

Loads and manages driver plugins.
Deterministic driver selection based on lane + messageType.

Architecture:
- Registry loads drivers on startup
- selectDriver(lane, messageType) returns driver or None
- Same registry used for real-time fileWriter and export
"""

from pathlib import Path
from typing import Dict, List, Optional, Type

from nova.core.events import Lane
from .base import BaseDriver, DriverCapabilities
from sdk.logging import getLogger


class DriverRegistry:
    """Driver plugin registry with deterministic selection."""
    
    def __init__(self, outputDir: Path):
        self.outputDir = outputDir
        self.log = getLogger()
        
        # driverId → driver instance
        self._drivers: Dict[str, BaseDriver] = {}
        
        # (lane, messageType) → driverId for specific matches
        self._specificIndex: Dict[tuple, str] = {}
        
        # lane → driverId for lane-wide matches (messageType=None)
        self._laneIndex: Dict[Lane, str] = {}
    
    def registerDriver(self, driverClass: Type[BaseDriver]):
        """Register a driver class."""
        driver = driverClass(self.outputDir)
        caps = driver.capabilities
        
        self._drivers[caps.driverId] = driver
        
        if caps.messageType:
            # Specific messageType match
            self._specificIndex[(caps.lane, caps.messageType)] = caps.driverId
        else:
            # Lane-wide match (all messageTypes for this lane)
            self._laneIndex[caps.lane] = caps.driverId
        
        self.log.info(f"[DriverRegistry] Registered: {caps.driverId} v{caps.version}")
    
    def loadBuiltinDrivers(self):
        """Load built-in drivers."""
        from .rawBinary import RawBinaryDriver
        from .positionCsv import PositionCsvDriver
        
        self.registerDriver(RawBinaryDriver)
        self.registerDriver(PositionCsvDriver)
    
    def selectDriver(self, lane: Lane, messageType: Optional[str] = None) -> Optional[BaseDriver]:
        """
        Select driver for lane/messageType.
        
        Priority:
        1. Specific (lane, messageType) match
        2. Lane-wide match (messageType=None in driver capabilities)
        3. None (no driver for this combination)
        """
        # Try specific match
        if messageType:
            driverId = self._specificIndex.get((lane, messageType))
            if driverId:
                return self._drivers[driverId]
        
        # Try lane-wide match
        driverId = self._laneIndex.get(lane)
        if driverId:
            return self._drivers[driverId]
        
        return None
    
    def finalize(self):
        """Finalize all drivers (close files)."""
        for driver in self._drivers.values():
            driver.finalize()
    
    def getAllDrivers(self) -> List[BaseDriver]:
        """Get all registered drivers."""
        return list(self._drivers.values())
    
    def getDriver(self, driverId: str) -> Optional[BaseDriver]:
        """Get driver by ID."""
        return self._drivers.get(driverId)
