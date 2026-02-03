"""
NOVA Base Driver Class

Abstract base class for all drivers.
Drivers transform truth events into file output.

Architecture (nova architecture.md):
- Each driver declares driverId, version, supported lane/messageType
- Drivers write to hierarchy: {date}/{systemId}/{containerId}/{uniqueId}/filename
- Same driver used for real-time and export (parity by design)
"""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass

from nova.core.events import Lane


@dataclass
class DriverCapabilities:
    """Driver capability declaration for registry."""
    driverId: str
    version: str
    lane: Lane
    messageType: Optional[str] = None  # None = all types for this lane
    outputFilename: str = ""  # e.g., "raw.bin", "rx_llas.csv"


class BaseDriver(ABC):
    """
    Abstract base driver class.
    
    All drivers inherit from this and implement write logic.
    Same driver instance used for real-time and export.
    """
    
    def __init__(self, outputDir: Path):
        self.outputDir = outputDir
        self._openFiles: Dict[str, Any] = {}
    
    @property
    @abstractmethod
    def capabilities(self) -> DriverCapabilities:
        """Return driver capabilities for registry."""
        pass
    
    @abstractmethod
    def write(self, event: Dict[str, Any], canonicalTruthTime: str) -> Optional[Path]:
        """
        Write event to file.
        
        Returns:
            Path to written file, or None if not applicable
        """
        pass
    
    def finalize(self):
        """Close all open files."""
        for handle in self._openFiles.values():
            handle.close()
        self._openFiles.clear()
    
    def _buildPath(self, event: Dict[str, Any], canonicalTruthTime: str) -> Path:
        """
        Build file path with proper hierarchy.
        
        Pattern: {outputDir}/{YYYY-MM-DD}/{systemId}/{containerId}/{uniqueId}/{filename}
        """
        dt = datetime.fromisoformat(canonicalTruthTime.replace('Z', '+00:00'))
        dateStr = dt.strftime('%Y-%m-%d')
        
        systemId = event['systemId']
        containerId = event['containerId']
        uniqueId = event['uniqueId']
        filename = self.capabilities.outputFilename
        
        return self.outputDir / dateStr / systemId / containerId / uniqueId / filename
    
    def _getFileHandle(self, path: Path, mode: str = 'ab'):
        """Get or create file handle."""
        pathStr = str(path)
        if pathStr not in self._openFiles:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._openFiles[pathStr] = open(path, mode)
        return self._openFiles[pathStr]
