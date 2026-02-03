"""SubjectBuilder: Explicit subject string generator for hardwareService messaging.

- One builder per service instance
- Explicit identifiers: serviceId, containerId, deviceId, kind, dataKind
- Pattern: {serviceId}.{category}.{containerId}.{deviceId}.{kind}.{dataKind}
- Example: hardwareService.data.Payload.3816849.x5.sbf

Property of Uncompromising Sensors LLC"""


# Imports
from typing import Optional


class SubjectBuilder:
    """Generates subject strings for hardwareService."""
    

    def __init__(self, serviceId: str, containerId: str = None, separator: str = '.'):
        """serviceId: service name, containerId: container/computer ID, separator: join char (default '.')"""
        self.serviceId = serviceId
        self.containerId = containerId or 'unknown'
        self.separator = separator
    
    # ===== Core API (Simple, Explicit) =====
    def data(self, deviceId: str, kind: str, dataKind: str) -> str:
        """deviceId, kind, dataKind -> serviceId.data.containerId.deviceId.kind.dataKind"""
        return self._build('data', self.containerId, deviceId, kind, dataKind)
    

    def commands(self) -> str:
        return f'{self.serviceId}.commands'
    

    def events(self) -> str:
        return f'{self.serviceId}.events.{self.containerId}'
    

    def topology(self) -> str:
        return f'{self.serviceId}.topology.{self.containerId}'
    
    def discovery(self) -> str:
        return f'{self.serviceId}.discovery'
    
    def control(self, deviceId: Optional[str] = None) -> str:
        """
        Generate control subject.
        
        Args:
            deviceId: Optional device ID for device-specific control
        
        Returns:
            serviceId.control.containerId (general) or serviceId.control.containerId.deviceId (device-specific)
        """
        if deviceId:
            return f'{self.serviceId}.control.{self.containerId}.{deviceId}'
        return f'{self.serviceId}.control.{self.containerId}'
    
    # ===== Convenience API (Smart defaults) =====
    
    def dataAuto(self, deviceId: str, kind: str, dataKind: Optional[str] = None) -> str:
        """deviceId, kind, dataKind -> serviceId.data.containerId.deviceId.kind.dataKind (requires dataKind)"""
        if dataKind is None:
            raise ValueError("dataKind must be provided explicitly or via topology; no default mapping.")
        return self.data(deviceId, kind, dataKind)
    
    def subscribe(self, pattern: str) -> str:
        """pattern='data.*' -> serviceId.data.containerId.*"""
        if pattern.startswith(self.serviceId):
            return pattern
        return f'{self.serviceId}.{pattern}'
    

    # ===== Multi-App Support (Future) =====
    def peer(self, peerServiceId: str, category: str) -> str:
        """peerServiceId, category -> peerServiceId.category"""
        return f'{peerServiceId}.{category}'
    
    # ===== Internal Methods =====
    def _build(self, category: str, containerId: str, deviceId: str, kind: str, dataKind: str) -> str:
        """Build hierarchical subject string with container."""
        return self.separator.join([self.serviceId, category, containerId, deviceId, kind, dataKind])
    
    def __repr__(self) -> str:
        return f"SubjectBuilder(serviceId='{self.serviceId}', containerId='{self.containerId}', separator='{self.separator}')"
