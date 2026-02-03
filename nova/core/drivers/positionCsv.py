"""
NOVA Position CSV Driver (llas.csv)

Writes Position messageType events to llas.csv.
Only handles messageType="Position" - all other parsed events are ignored.

Output: {date}/{systemId}/{containerId}/{uniqueId}/llas.csv
Columns: sourceTruthTime (UTC), iTOW (ms), latitude (deg), longitude (deg), altitude (HAE-m), fixType
"""

import csv
from pathlib import Path
from typing import Dict, Any, Optional

from nova.core.events import Lane
from .base import BaseDriver, DriverCapabilities


class PositionCsvDriver(BaseDriver):
    """Position messageType → llas.csv driver."""
    
    # Fixed column order: times first, then position, then metadata
    COLUMNS = [
        'sourceTruthTime (UTC)',
        'iTOW (ms)',
        'latitude (deg)',
        'longitude (deg)',
        'altitude (HAE-m)',
        'fixType'
    ]
    
    def __init__(self, outputDir: Path):
        super().__init__(outputDir)
        self._headersWritten: set = set()
    
    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            driverId="position-csv",
            version="1.0.0",
            lane=Lane.PARSED,
            messageType="Position",
            outputFilename="llas.csv"
        )
    
    def write(self, event: Dict[str, Any], canonicalTruthTime: str) -> Optional[Path]:
        """Write Position event to llas.csv. Returns None for non-Position events."""
        # Only accept Position messageType
        if event.get('messageType') != 'Position':
            return None
        
        payload = event.get('payload', {})
        
        filePath = self._buildPath(event, canonicalTruthTime)
        pathStr = str(filePath)
        
        # Write header if first time
        needsHeader = pathStr not in self._headersWritten and not filePath.exists()
        
        filePath.parent.mkdir(parents=True, exist_ok=True)
        
        with open(filePath, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS, extrasaction='ignore')
            
            if needsHeader:
                writer.writeheader()
                self._headersWritten.add(pathStr)
            
            row = {
                'sourceTruthTime (UTC)': event.get('sourceTruthTime', ''),
                'iTOW (ms)': payload.get('time', ''),
                'latitude (deg)': payload.get('lat', ''),
                'longitude (deg)': payload.get('lon', ''),
                'altitude (HAE-m)': payload.get('alt', ''),
                'fixType': payload.get('fixType', '')
            }
            writer.writerow(row)
        
        return filePath
