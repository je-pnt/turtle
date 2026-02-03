"""
sdk.hardwareService - Hardware Abstraction Layer

This module provides a Hardware Abstraction Layer (HAL) that manages hardware 
devices and communicates with other applications using transport protocols.

Public API:
    - HardwareServiceApp: Main application class for running the hardware service
    - HardwareService: Core service logic (plugin/device management)
    - IoLayer: Low-level I/O operations abstraction

Usage:
    # Run as standalone service
    python -m sdk.hardwareService
    
    # Programmatic usage
    from sdk.hardwareService import HardwareServiceApp
    app = HardwareServiceApp()
    await app.run()
"""

from .main import HardwareServiceApp
from .hardwareService import HardwareService
from .ioLayer import IoLayer

__all__ = ['HardwareServiceApp', 'HardwareService', 'IoLayer']
