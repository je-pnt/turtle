"""
Logging Context Manager

Provides service-level context (nodeId, serviceType) to all log messages.
Integrates with sdk.logging for consistent log formatting.
"""

import logging
from typing import Optional
from contextvars import ContextVar

# Context variables for service identity
_service_type: ContextVar[Optional[str]] = ContextVar('service_type', default=None)
_node_id: ContextVar[Optional[str]] = ContextVar('node_id', default=None)
_scope_id: ContextVar[Optional[str]] = ContextVar('scope_id', default=None)


class ServiceContextFilter(logging.Filter):
    """
    Logging filter that adds service context to all log records
    """
    
    def filter(self, record):
        """Add service context to record"""
        # Get context values
        serviceType = _service_type.get()
        nodeId = _node_id.get()
        scopeId = _scope_id.get()
        
        # Add to record if available
        if serviceType:
            record.serviceType = serviceType
        if nodeId:
            record.nodeId = nodeId
        if scopeId:
            record.scopeId = scopeId
        
        return True


def setServiceContext(serviceType: str, nodeId: str, scopeId: str = None):
    """
    Set service-level context for logging
    
    Args:
        serviceType: Type of service ('gem', 'novaArchive', 'novaCore')
        nodeId: Node identifier
        scopeId: Scope identifier (optional)
    """
    _service_type.set(serviceType)
    _node_id.set(nodeId)
    if scopeId:
        _scope_id.set(scopeId)


def getServiceContext() -> dict:
    """Get current service context"""
    return {
        'serviceType': _service_type.get(),
        'nodeId': _node_id.get(),
        'scopeId': _scope_id.get()
    }


def clearServiceContext():
    """Clear service context"""
    _service_type.set(None)
    _node_id.set(None)
    _scope_id.set(None)


def installServiceContextFilter():
    """
    Install service context filter on root logger
    
    Call this once at service startup after configureLogging()
    """
    rootLogger = logging.getLogger()
    
    # Check if filter already installed
    for f in rootLogger.filters:
        if isinstance(f, ServiceContextFilter):
            return
    
    # Add filter
    contextFilter = ServiceContextFilter()
    rootLogger.addFilter(contextFilter)
