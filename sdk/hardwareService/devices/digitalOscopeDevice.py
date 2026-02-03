"""
DigitalOscopeDevice: Hardware abstraction for digital oscilloscope devices.

- Manages device connection, configuration, and data acquisition
- Used by hardwareService via plugin architecture
- Reports only lag values; signal generation handled client-side

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio, ctypes, json
import numpy as np
from datetime import datetime, timezone
from sys import platform

# Local imports
from sdk.logging import getLogger
from .baseDevice import BaseDevice


# Class
class DigitalOscopeDevice(BaseDevice):
    
    def __init__(self, deviceId: str, ioLayer, transport=None, subjectBuilder=None, triggerChannel: int = 0):
        super().__init__(deviceId, ioLayer, transport, subjectBuilder)
        self.device = None
        self.sampleRate = 100e6
        self.bufferSize = 16384
        self.triggerChannel = triggerChannel  # Configurable trigger channel from hardware-config.json
        self.channels = list(range(0, 15))  # Channels 0-14 (matching analog3.py)
        self.nsPerSample = 1e9 / self.sampleRate
        self.triggerPosition = int(self.bufferSize / 3)  # Match analog3.py: buffer_size/3
        self.log = getLogger()    # Auto-detect logger hierarchy (computed ONCE): 'hardwareService.devices.digitalOscopeDevice.DigitalOscopeDevice'
        self.ports = []
    

    def getKind(self) -> str:
        """Return device kind"""
        return 'digitalOscope'
    

    async def open(self):
        """Open device via DLL and configure for acquisition"""
        def _open():           
            if platform.startswith("win"):
                dwf = ctypes.cdll.dwf
            elif platform.startswith("darwin"):
                dwf = ctypes.cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
            else:
                dwf = ctypes.cdll.LoadLibrary("libdwf.so")
            hdwf = ctypes.c_int()
            dwf.FDwfDeviceOpen(ctypes.c_int(-1), ctypes.byref(hdwf))
            
            # Get internal clock frequency for divider calculation
            internal_frequency = ctypes.c_double()
            dwf.FDwfDigitalInInternalClockInfo(hdwf, ctypes.byref(internal_frequency))
            
            # Set clock frequency divider (needed for sampling rate)
            dwf.FDwfDigitalInDividerSet(hdwf, ctypes.c_int(int(internal_frequency.value / self.sampleRate)))
            
            # Set 16-bit sample format
            dwf.FDwfDigitalInSampleFormatSet(hdwf, ctypes.c_int(16))
            
            # Set buffer size
            dwf.FDwfDigitalInBufferSizeSet(hdwf, ctypes.c_int(self.bufferSize))
            
            # Configure trigger - matching analog3.py logic.trigger() pattern
            # Set trigger source to digital I/O lines
            dwf.FDwfDigitalInTriggerSourceSet(hdwf, ctypes.c_byte(3))
            
            # Set trigger position and prefill
            prefill = self.triggerPosition
            dwf.FDwfDigitalInTriggerPositionSet(hdwf, ctypes.c_int(self.bufferSize - prefill))
            dwf.FDwfDigitalInTriggerPrefillSet(hdwf, ctypes.c_int(prefill))
            
            # For rising edge: channel mask in SECOND parameter
            channel_mask = ctypes.c_int(1 << self.triggerChannel)
            dwf.FDwfDigitalInTriggerSet(hdwf, ctypes.c_int(0), channel_mask, ctypes.c_int(0), ctypes.c_int(0))
            dwf.FDwfDigitalInTriggerResetSet(hdwf, ctypes.c_int(0), ctypes.c_int(0), channel_mask, ctypes.c_int(0))
            
            # Start acquisition
            dwf.FDwfDigitalInConfigure(hdwf, ctypes.c_bool(False), ctypes.c_bool(True))
            
            return (dwf, hdwf)
        self.device = await self.ioLayer.runInExecutor(_open)
        self.log.info('Device opened and configured', deviceId=self.deviceId)
    

    async def close(self):
        """Close device"""
        if self.device:
            dwf, hdwf = self.device
            await self.ioLayer.runInExecutor(lambda: dwf.FDwfDeviceClose(hdwf))
        self.log.info('Device closed', deviceId=self.deviceId)
    

    async def writeTo(self, data: bytes) -> dict:
        """Write raw bytes to device (not supported for oscope)"""
        return {"status": "not_supported", "deviceId": self.deviceId, "bytesLength": len(data), "error": "DigitalOscope does not support writeTo"}
    

    async def readLoop(self):
        """Read loop: wait for trigger, capture and process data - Exceptions propagate to hardwareService's runReadLoop for full restart cycle."""
        
        self.log.info('ReadLoop started - waiting for triggers', deviceId=self.deviceId)
        
        while True:
            try:
                # Wait for trigger to fire (status = Done)
                await self._waitForTrigger()
                
                # Capture and process
                result = await self._captureAndProcess()
                if result and 'lag' in result and result['lag'] is not None:
                    ts = datetime.now(timezone.utc).timestamp()
                    summary = {'lag': result['lag'], 'ts': ts, 'deviceId': self.deviceId}
                    await self.emit('samples', ts, json.dumps(summary).encode('utf-8'))
            except (OSError, AttributeError, Exception) as e:
                # Device unplugged or hardware error - propagate to trigger cleanup
                self.log.error('Hardware connection error', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
                raise
    

    async def _waitForTrigger(self):
        """Wait for trigger to fire (status = Done)"""
        
        def _checkStatus():
            dwf, hdwf = self.device
            status = ctypes.c_byte()
            result = dwf.FDwfDigitalInStatus(hdwf, ctypes.c_bool(True), ctypes.byref(status))
            if result == 0:
                # API call failed - likely device disconnected
                raise OSError(f"Device communication failed (FDwfDigitalInStatus returned 0)")
            return (status.value == 2, status.value)            # Status codes: 0=Ready, 1=Armed, 2=Done
        
        # Poll at interval just under 1Hz since trigger fires at exactly 1Hz
        pollInterval = 0.95  # 950ms poll interval for 1Hz trigger
        maxWaitCycles = 5  # Timeout after 5 seconds (5 * 0.95s) with no trigger
        cycleCount = 0
        
        while True:
            done, statusValue = await self.ioLayer.runInExecutor(_checkStatus)
            if done:
                return True
            
            cycleCount += 1
            if cycleCount >= maxWaitCycles:
                # No trigger for extended period - scope may be stuck, trigger full restart
                self.log.error('No trigger detected for 5+ seconds, scope appears stuck', deviceId=self.deviceId, statusValue=statusValue)
                raise OSError(f"Trigger timeout - no trigger detected after {maxWaitCycles} polling cycles")
            
            await asyncio.sleep(pollInterval)
    

    async def _captureAndProcess(self) -> dict:
        """Capture raw data and process into lag values"""
        
        def _doCapture():
            # Get data
            dwf, hdwf = self.device
            buffer = (ctypes.c_uint16 * self.bufferSize)()
            
            # Call status to update internal state, then read data
            status = ctypes.c_byte()
            dwf.FDwfDigitalInStatus(hdwf, ctypes.c_bool(True), ctypes.byref(status))
            
            if dwf.FDwfDigitalInStatusData(hdwf, buffer, ctypes.c_int(2 * self.bufferSize)) == 0:
                dwf.FDwfDigitalInConfigure(hdwf, ctypes.c_bool(False), ctypes.c_bool(True))
                return {}
                
            # Convert to numpy array
            buffer = np.array(buffer)

            # Process data - find first rising edge for each channel
            indexes = {}
            for channel in range(0, 15):
                channelBuffer = (buffer & (1 << channel)) >> channel
                risingEdge = np.flatnonzero(channelBuffer > 0.5)
                if len(risingEdge) > 0:
                    indexes[channel] = risingEdge[0]
            
            # Calculate lag relative to trigger channel
            lag = {}
            if self.triggerChannel in indexes:
                triggerIndex = indexes[self.triggerChannel]
                for channel, index in indexes.items():
                    if channel != self.triggerChannel:
                        lag[channel] = int(self.nsPerSample * (index - triggerIndex))
            else:
                # Trigger channel not detected - invalid capture, don't report bad data
                self.log.warning('Trigger channel not detected in capture', deviceId=self.deviceId, 
                               triggerChannel=self.triggerChannel, detectedChannels=list(indexes.keys()))
                dwf.FDwfDigitalInConfigure(hdwf, ctypes.c_bool(False), ctypes.c_bool(True))
                return {'lag': {}}  # Return empty lag dict to skip this sample
            
            # Rearm the scope
            dwf.FDwfDigitalInConfigure(hdwf, ctypes.c_bool(False), ctypes.c_bool(True))
            return {'lag': lag}

        return await self.ioLayer.runInExecutor(_doCapture)