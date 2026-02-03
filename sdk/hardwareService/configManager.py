"""
ConfigManager: Manages per-device configuration and audit logging for hardwareService

- Applies raw configuration bytes to devices via ioLayer
- Logs all config actions to per-device CSV files (logging/{deviceId}.csv)
- Retrieves configuration history from device logs
- Supports online and offline device states

Functions:
  applyConfig: Send config bytes to device and log result
  getConfigHistory: Retrieve config history from audit log

Property of Uncompromising Sensors LLC.
"""

# Imports
import os, csv
from datetime import datetime, timezone
from typing import Optional, Dict

# Local imports
from sdk.logging import getLogger


class ConfigManager:
    def __init__(self, ioLayer, devices: Dict, startTime: datetime):

        # Initialize ConfigManager with ioLayer, devices, start time -  logger hierarchy (computed ONCE): 'hardwareService.configManager.ConfigManager'
        self.log = getLogger()
        self.devices = devices  
        self.startTime = startTime
        self.configLogDir = os.path.join(os.path.dirname(__file__), 'logging')
        self.configCsvHeader = ['TimeUTC', 'DeviceId', 'Label', 'BytesHex', 'Status', 'ErrorMsg']
        
        # Create config log directory if it doesn't exist
        os.makedirs(self.configLogDir, exist_ok=True)


    async def applyConfig(self, deviceId: str, configBytes: bytes, label: str = "") -> dict:

        # Apply config bytes to device and log result
        bytesHex = configBytes.hex()
        bytesLength = len(configBytes)
        
        # Ensure the device is online
        deviceEntry = self.devices.get(deviceId)
        if not deviceEntry:
            self._auditConfig(deviceId, label, bytesHex, "offline", "Device not found")
            self.log.warning(f'Config failed: device offline', component='ConfigManager', deviceId=deviceId, label=label, bytesLength=bytesLength)
            return {"status": "offline", "deviceId": deviceId, "bytesLength": bytesLength, "error": "Device not found"}
        
        try:

            # Get device instance and call writeTo method
            device = deviceEntry['device']
            if hasattr(device, 'writeTo'):
                result = await device.writeTo(configBytes)
                
                # Audit result
                status = result.get('status', 'unknown')
                errorMsg = result.get('error', '')
                self._auditConfig(deviceId, label, bytesHex, status, errorMsg)
                
                if status in ('applied', 'confirmed'):
                    self.log.info(f'Config applied to {deviceId}', component='ConfigManager', deviceId=deviceId, label=label, bytesLength=bytesLength, status=status)
                else:
                    self.log.error(f'Config failed on {deviceId}: {errorMsg}', component='ConfigManager', deviceId=deviceId, label=label, errorMsg=errorMsg, status=status)
                
                return result
            
            else:
                # Audit failure due to missing writeTo method
                self._auditConfig(deviceId, label, bytesHex, "error", "No writeTo method")
                self.log.error(f'Config failed: no writeTo method', component='ConfigManager', deviceId=deviceId, label=label, errorMsg="No writeTo method")
                return {"status": "error", "deviceId": deviceId, "bytesLength": bytesLength, "error": "No writeTo method"}
        
        # Handle exceptions
        except Exception as e:
            errorMsg = str(e)
            self._auditConfig(deviceId, label, bytesHex, "error", errorMsg)
            self.log.error(f'Config error: {e!r}', component='ConfigManager', deviceId=deviceId, label=label, errorClass=type(e).__name__, errorMsg=errorMsg)
            return {"status": "error", "deviceId": deviceId, "bytesLength": bytesLength, "error": errorMsg}
    

    def getConfigHistory(self, deviceId: str, startTime: Optional[str] = None) -> dict:

        # Retrieve configuration history from audit log
        filterStart = startTime if startTime else self.startTime.isoformat()
        csvPath = os.path.join(self.configLogDir, f"{deviceId}.csv")
        
        # Check if config log exists
        if not os.path.exists(csvPath):
            self.log.info(f'Config history empty: no log file', component='ConfigManager', deviceId=deviceId)
            return {"deviceId": deviceId, "entries": [], "count": 0, "startTime": filterStart}
        
        # Read and filter CSV entries
        entries = []
        try:
            with open(csvPath, 'r', encoding='utf-8', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['TimeUTC'] >= filterStart:
                        entries.append({"timestamp": row['TimeUTC'], "label": row['Label'],"bytesHex": row['BytesHex'],"status": row['Status'],"errorMsg": row['ErrorMsg']})
            self.log.info(f'Config history retrieved', component='ConfigManager', deviceId=deviceId, count=len(entries))
            return {"deviceId": deviceId, "entries": entries, "count": len(entries), "startTime": filterStart}
        
        # Handle exceptions
        except Exception as e:
            self.log.error(f'Config history error: {e!r}', component='ConfigManager', deviceId=deviceId, errorClass=type(e).__name__, errorMsg=str(e))
            return {"deviceId": deviceId, "entries": [], "count": 0, "error": str(e), "startTime": filterStart}
    

    def _auditConfig(self, deviceId: str, label: str, bytesHex: str, status: str, errorMsg: str):
        
        # Write config action to device-specific CSV audit log
        timestamp = datetime.now(timezone.utc).isoformat()
        csvPath = os.path.join(self.configLogDir, f"{deviceId}.csv")
        
        # Create file with header if it doesn't exist
        fileExists = os.path.exists(csvPath)
        try:
            with open(csvPath, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                if not fileExists:
                    writer.writerow(self.configCsvHeader)
                writer.writerow([timestamp, deviceId, label, bytesHex, status, errorMsg])
                
        except Exception as e:
            self.log.error(f'Config audit write failed: {e!r}', component='ConfigManager', deviceId=deviceId, errorClass=type(e).__name__, errorMsg=str(e))
