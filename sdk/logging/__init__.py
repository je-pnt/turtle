"""
SDK Logging - Universal hierarchical logger with automatic detection.

API:
    from sdk.logging import getLogger
    
    # Class-level (auto-detect once in __init__)
    class MyDevice:
        def __init__(self):
            self.log = getLogger()  # Auto: 'hardwareService.devices.MyDevice'
        
        def open(self):
            self.log.info("Opening", port=self.port)
    
    # Module-level (auto-detect once at import)
    from sdk.logging import getLogger
    log = getLogger()  # Auto: 'hardwareService.restartManager'
    
    def myFunction():
        log.info("Message", key=value)
    
    # Global configuration (optional, once at app startup)
    from sdk.logging import configureLogging
    configureLogging(logDir='../logs', maxBytes=10_000_000, maxTotalMb=2048)
"""

from .logger import getLogger, configureLogging
from .context import (
    setServiceContext, 
    getServiceContext, 
    clearServiceContext,
    installServiceContextFilter
)

__all__ = [
    'getLogger', 
    'configureLogging',
    'setServiceContext',
    'getServiceContext',
    'clearServiceContext',
    'installServiceContextFilter'
]

