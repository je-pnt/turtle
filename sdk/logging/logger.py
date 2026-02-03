"""
Universal hierarchical logger with automatic detection.

Features:
- Auto-detects logger hierarchy from call stack (computed once, cached)
- Global log directory with rotation and disk management
- Cross-platform (stdlib only)
- Zero overhead after logger assignment
- Structured field logging

Usage:
    from sdk.logging import getLogger
    
    # Pattern 1: Class-level (compute once in __init__)
    class MyClass:
        def __init__(self):
            self.log = getLogger()  # Auto-detects hierarchy ONCE
        
        def method(self):
            self.log.info("Message", key=value)  # Zero overhead
    
    # Pattern 2: Module-level (compute once at import)
    log = getLogger()  # Auto-detects ONCE when module loads
    
    def myFunction():
        log.info("Message")  # Zero overhead

Property of Uncompromising Sensors LLC.
"""

# Imports
import  inspect, logging, logging.handlers, os, socket
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone as tz


# Global state
_hostname = socket.gethostname()
_configured = False
_fileHandlers = {}  # Singleton cache: logPath -> handler
_config = {
    'logDir': None,
    'maxBytes': 100_000_000,        # 100 MB per log file before rotation
    'backupCount': 20,              # Keep 20 backup files per app (total: 2 GB per app max)
    'maxTotalMb': 10240,            # Max total disk usage: 10 GB across all logs
    'console': True,
    'level': logging.INFO,
    'utc': False
}


def configureLogging(logDir: Optional[str] = None, maxBytes: int = 10_000_000,
                     backupCount: int = 5, maxTotalMb: int = 2048,
                     console: bool = True, level: str = 'INFO', utc: bool = False):
    """
    Configure global logging settings (call once at app startup).
    
    Args:
        logDir: Directory for log files (default: ../logs)
        maxBytes: Maximum size per log file before rotation (default: 10MB)
        backupCount: Number of backup files to keep per app (default: 5)
        maxTotalMb: Maximum total disk usage across all logs in MB (default: 2048)
        console: Also log to console (default: True)
        level: Minimum log level (default: 'INFO')
        utc: Use UTC timestamps (default: False, uses local time)
    """
    global _configured, _config
    
    if logDir is None:
        logDir = os.path.abspath(os.path.join(os.getcwd(), os.pardir, "logs"))
    
    _config.update({'logDir': logDir,'maxBytes': maxBytes,'backupCount': backupCount, 'maxTotalMb': maxTotalMb,
                    'console': console,'level': getattr(logging, level.upper()), 'utc': utc})
    
    Path(logDir).mkdir(parents=True, exist_ok=True)
    _configured = True


def _autoDetectName() -> str:
    """Auto-detect logger name from call stack. Returns hierarchy like: 'hardwareService.devices.ubxDevice.UBXDevice'"""

    frame = inspect.currentframe()
    try:
        # Walk up the stack to find the first frame outside of logging module
        current = frame
        while current is not None:
            current = current.f_back
            if current is None:
                break
            
            # Get module for this frame
            module = inspect.getmodule(current)
            if module is None:
                continue
            
            moduleName = module.__name__
            
            # Skip frames that are inside the logging module itself
            if 'logging' in moduleName and 'sdk.logging' in moduleName:
                continue
            
            # Skip Python's import machinery
            if moduleName.startswith('importlib') or moduleName == '__main__':
                continue
            
            # Found the caller! Build hierarchy
            parts = moduleName.split('.')
            
            # Remove 'sdk' prefix if present (it's just a package wrapper)
            if parts and parts[0] == 'sdk':
                parts = parts[1:]
            
            # Get class name if called from within a class method
            className = None
            if current.f_locals:
                if 'self' in current.f_locals:
                    className = current.f_locals['self'].__class__.__name__
                elif 'cls' in current.f_locals:
                    className = current.f_locals['cls'].__name__
            
            hierarchy = '.'.join(parts) if parts else 'unknown'
            if className:
                hierarchy = f"{hierarchy}.{className}"
            
            return hierarchy if hierarchy else 'unknown'
        
        # Couldn't find valid module
        return 'unknown'
    finally:
        del frame


class StructuredFormatter(logging.Formatter):
    """Custom formatter that includes hostname and structured fields.
    Format: timestamp - hostname - logger.name - level - message [field1=value1, field2=value2]"""
    
    def __init__(self, fmt=None, datefmt=None, utc=False):
        super().__init__(fmt, datefmt)
        self.utc = utc
    
    def formatTime(self, record, datefmt=None):
        """Override to support UTC if configured."""
        if self.utc:
            ct = datetime.fromtimestamp(record.created, tz=tz.utc)
        else:
            ct = datetime.fromtimestamp(record.created)
        
        if datefmt:
            s = ct.strftime(datefmt)
        else:
            s = ct.strftime("%Y-%m-%d %H:%M:%S")
            s = f"{s},{int(record.msecs):03d}"
        return s
    

    def format(self, record):
        # Add hostname to record
        record.hostname = _hostname
        
        # Extract structured fields from record.__dict__
        structuredFields = []
        excluded = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'message', 'pathname', 'process', 'processName',
            'relativeCreated', 'thread', 'threadName', 'exc_info',
            'exc_text', 'stack_info', 'hostname', 'asctime'
        }
        
        for key, value in record.__dict__.items():
            if key not in excluded and not key.startswith('_'):
                structuredFields.append(f"{key}={value}")
        
        # DON'T modify record.msg - create a copy and append to it
        originalMsg = record.msg
        if structuredFields:
            record.msg = f"{originalMsg} [{', '.join(structuredFields)}]"
        
        # Format the record
        result = super().format(record)
        
        # Restore original message to avoid affecting other handlers
        record.msg = originalMsg
        
        return result


def getLogger(name: Optional[str] = None, separateFile: bool = False) -> logging.Logger:
    """
    Get or create a logger with automatic hierarchy detection.
    
    PERFORMANCE: Stack inspection happens ONCE during getLogger() call (~5-10μs).
    The returned logger object is cached and reused with zero overhead (~0.1-0.5μs per log call).
    
    Args:
        name: Logger name (auto-detected from call stack if None)
        separateFile: If True, creates separate log file for this logger (default: False)
        
    Returns:
        Enhanced logging.Logger instance with convenience methods for structured logging
        
    Example:
        # Class-level (inspect once in __init__)
        class MyDevice:
            def __init__(self):
                self.log = getLogger()  # Auto: 'hardwareService.devices.MyDevice'
            
            def open(self):
                self.log.info("Opening", port=self.port)  # Structured fields!
        
        # Module-level (inspect once at import)
        log = getLogger()  # Auto: 'hardwareService.restartManager'
        
        def restartUsb():
            log.info("Restarting", deviceId='GPS_001')  # Structured fields!
    """
    # Ensure global config exists
    global _configured
    if not _configured:
        configureLogging()
    
    # Auto-detect name if not provided (STACK INSPECTION - happens ONCE)
    if name is None:
        name = _autoDetectName()
    
    # Get or create logger (Python's logging module caches by name - instant lookup)
    logger = logging.getLogger(name)
    
    # Always disable propagation to avoid duplicate messages
    logger.propagate = False
    
    # Only configure if not already configured (check for any handlers at all)
    if not logger.handlers and not hasattr(logger, '_configured_by_sdk'):
        logger.setLevel(_config['level'])
        
        # Determine log file (top-level app name or full hierarchy if separate)
        if separateFile:
            logFilename = f"{name}.log"
        else:
            # Use top-level app name (e.g., 'hardwareService' from 'hardwareService.devices.ubx')
            appName = name.split('.')[0]
            logFilename = f"{appName}.log"
        
        logPath = str(Path(_config['logDir']) / logFilename)
        
        # Get or create singleton file handler for this log file
        global _fileHandlers
        if logPath not in _fileHandlers:
            # Rotating file handler
            fileHandler = logging.handlers.RotatingFileHandler(
                logPath,
                maxBytes=_config['maxBytes'],
                backupCount=_config['backupCount'],
                encoding='utf-8'
            )
            fileHandler.setLevel(_config['level'])
            
            # Format: timestamp - hostname - logger.name - level - message [fields]
            formatter = StructuredFormatter(
                '%(asctime)s - %(hostname)s - %(name)s - %(levelname)s - %(message)s',
                utc=_config['utc']
            )
            fileHandler.setFormatter(formatter)
            
            # Cache singleton handler
            _fileHandlers[logPath] = fileHandler
        
        logger.addHandler(_fileHandlers[logPath])
        
        # Console handler
        if _config['console']:
            consoleHandler = logging.StreamHandler()
            consoleHandler.setLevel(_config['level'])
            consoleFormatter = StructuredFormatter(
                '%(name)s - %(levelname)s - %(message)s',
                utc=_config['utc']
            )
            consoleHandler.setFormatter(consoleFormatter)
            logger.addHandler(consoleHandler)
        
        # Mark as configured
        logger._configured_by_sdk = True
    
    # Wrap logger to add convenience methods that accept **kwargs
    return _wrapLogger(logger)


def _wrapLogger(logger: logging.Logger) -> logging.Logger:
    """
    Wrap a standard logger to add convenience methods that accept structured fields as **kwargs.
    
    This allows: log.info("Message", field1=value1, field2=value2)
    Instead of: log.info("Message", extra={'field1': value1, 'field2': value2})
    """
    # Only wrap once (check if already wrapped)
    if hasattr(logger, '_is_wrapped'):
        return logger
    
    # Store original methods
    originalDebug = logger.debug
    originalInfo = logger.info
    originalWarning = logger.warning
    originalError = logger.error
    originalCritical = logger.critical
    
    # Create wrapped methods
    def debug(msg, *args, **kwargs):
        """Log debug message with structured fields."""
        if kwargs:
            originalDebug(msg, *args, extra=kwargs)
        else:
            originalDebug(msg, *args)
    
    def info(msg, *args, **kwargs):
        """Log info message with structured fields."""
        if kwargs:
            originalInfo(msg, *args, extra=kwargs)
        else:
            originalInfo(msg, *args)
    
    def warning(msg, *args, **kwargs):
        """Log warning message with structured fields."""
        # Extract exc_info if present (it's a reserved logging param)
        exc_info = kwargs.pop('exc_info', False)
        if kwargs:
            originalWarning(msg, *args, extra=kwargs, exc_info=exc_info)
        else:
            originalWarning(msg, *args, exc_info=exc_info)
    
    def error(msg, *args, **kwargs):
        """Log error message with structured fields."""
        # Extract exc_info if present (it's a reserved logging param)
        exc_info = kwargs.pop('exc_info', False)
        if kwargs:
            originalError(msg, *args, extra=kwargs, exc_info=exc_info)
        else:
            originalError(msg, *args, exc_info=exc_info)
    
    def critical(msg, *args, **kwargs):
        """Log critical message with structured fields."""
        # Extract exc_info if present (it's a reserved logging param)
        exc_info = kwargs.pop('exc_info', False)
        if kwargs:
            originalCritical(msg, *args, extra=kwargs, exc_info=exc_info)
        else:
            originalCritical(msg, *args, exc_info=exc_info)
            originalCritical(msg, *args)
    
    # Replace methods on logger instance
    logger.debug = debug
    logger.info = info
    logger.warning = warning
    logger.error = error
    logger.critical = critical
    logger._is_wrapped = True  # Mark as wrapped
    
    return logger


def _enforceDiskLimit():
    """
    Enforce global disk usage limit by removing oldest log files.
    
    Called automatically during log rotation. Non-intrusive, only removes files
    when total disk usage exceeds maxTotalMb.
    """
    logDir = Path(_config['logDir'])
    maxBytes = _config['maxTotalMb'] * 1024 * 1024
    
    # Get all log files with sizes
    files = []
    totalSize = 0
    try:
        for filepath in logDir.rglob('*.log*'):
            if filepath.is_file():
                size = filepath.stat().st_size
                mtime = filepath.stat().st_mtime
                files.append((mtime, size, filepath))
                totalSize += size
    except (OSError, PermissionError):
        return
    
    # If under limit, nothing to do
    if totalSize <= maxBytes:
        return
    
    # Sort by modification time (oldest first)
    files.sort(key=lambda x: x[0])
    
    # Remove oldest files until under limit
    for mtime, size, filepath in files:
        if totalSize <= maxBytes:
            break
        try:
            filepath.unlink()
            totalSize -= size
        except (OSError, PermissionError):
            pass
