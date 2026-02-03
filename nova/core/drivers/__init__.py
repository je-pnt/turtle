"""
NOVA Driver Plugin System

Drivers transform truth events into files.
Same codepath for real-time and export (parity by design).

Drivers:
- RawBinaryDriver: Raw lane → raw.bin
- PositionCsvDriver: Parsed/Position → rx_llas.csv
"""

from .base import BaseDriver, DriverCapabilities
from .registry import DriverRegistry
from .rawBinary import RawBinaryDriver
from .positionCsv import PositionCsvDriver

__all__ = [
    'BaseDriver',
    'DriverCapabilities', 
    'DriverRegistry',
    'RawBinaryDriver',
    'PositionCsvDriver'
]
