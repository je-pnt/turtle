"""
NOVA Raw Binary Driver

Writes Raw lane events to raw.bin files.
Preserves exact byte frame boundaries.

Output: {date}/{systemId}/{containerId}/{uniqueId}/raw.bin
"""

import base64
from pathlib import Path
from typing import Dict, Any, Optional

from nova.core.events import Lane
from .base import BaseDriver, DriverCapabilities


class RawBinaryDriver(BaseDriver):
    """Raw lane → raw.bin file driver."""
    
    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            driverId="raw-binary",
            version="1.0.0",
            lane=Lane.RAW,
            messageType=None,  # All Raw events
            outputFilename="raw.bin"
        )
    
    def write(self, event: Dict[str, Any], canonicalTruthTime: str) -> Optional[Path]:
        """Write raw bytes to raw.bin, preserving exact byte boundaries."""
        # Get bytes - handle multiple formats:
        # 1. bytesData: raw bytes (from direct ingest/fileWriter)
        # 2. bytes: hex string (from DB query for export)
        # 3. bytes: base64 string (legacy/IPC)
        bytesData = event.get('bytesData')
        
        if bytesData is None:
            bytesField = event.get('bytes')
            if bytesField:
                if isinstance(bytesField, bytes):
                    bytesData = bytesField
                elif isinstance(bytesField, str):
                    # Try hex decode first (from DB query)
                    try:
                        bytesData = bytes.fromhex(bytesField)
                    except ValueError:
                        # Fall back to base64
                        bytesData = base64.b64decode(bytesField)
        
        if not bytesData:
            return None
        
        filePath = self._buildPath(event, canonicalTruthTime)
        handle = self._getFileHandle(filePath, 'ab')
        handle.write(bytesData)
        handle.flush()
        
        return filePath
