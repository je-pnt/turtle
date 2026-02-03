"""
AnalogOscopeDevice: Hardware abstraction for analog oscilloscope devices.

- Manages device connection, configuration, and data acquisition
- Used by hardwareService via plugin architecture
- Provides async open/close and data methods
- Reports only lag values; signal generation handled client-side

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio, ctypes, json
import numpy as np
from sys import platform, path as syspath
from os import sep, path as ospath
from datetime import datetime, timezone

# Setup path for WF_SDK and dwfconstants (must happen before imports)
if platform.startswith("win"):
    constants_path = "C:" + sep + "Program Files (x86)" + sep + "Digilent" + sep + "WaveFormsSDK" + sep + "samples" + sep + "py"
elif platform.startswith("darwin"):
    constants_path = sep + "Applications" + sep + "WaveForms.app" + sep + "Contents" + sep + "Resources" + sep + "SDK" + sep + "samples" + sep + "py"
else:
    constants_path = sep + "usr" + sep + "share" + sep + "digilent" + sep + "waveforms" + sep + "samples" + sep + "py"

# Add WF_SDK to path
wf_sdk_path = ospath.dirname(ospath.abspath(__file__))
wf_sdk_parent = ospath.dirname(wf_sdk_path)
if wf_sdk_parent not in syspath:
    syspath.insert(0, wf_sdk_parent)
if constants_path not in syspath:
    syspath.append(constants_path)

# Now import after path is set
import dwfconstants as constants
from WF_SDK import device, scope
from sdk.logging import getLogger
from .baseDevice import BaseDevice


# Class
class AnalogOscopeDevice(BaseDevice):
    
    def __init__(self, deviceId: str, ioLayer, transport=None, subjectBuilder=None, triggerChannel: int = 1):
        super().__init__(deviceId, ioLayer, transport, subjectBuilder)
        self.device = None
        self.bufferSize = 16384
        self.triggerThreshold = 1.5
        self.triggerChannel = triggerChannel  # Configurable trigger channel from hardware-config.json
        self.beforeNs = 100
        self.afterNs = 100
        self.badVal = -999
        self.sampleRates = np.array([10e3, 20e3, 50e3, 100e3, 200e3, 500e3, 1e6, 2e6, 6.25e6, 12.5e6, 25e6, 50e6, 100e6])
        self.sampleRateIndex = 0
        self.sampleRate = self.sampleRates[-1]  # Start at max rate: 100MHz
        self.nsPerSample = int(np.ceil(1 / self.sampleRate * 1e9))
        self.beforeIndex = int(self.beforeNs / self.nsPerSample)
        self.afterIndex = int(self.afterNs / self.nsPerSample + 1)
        self.log = getLogger()
        self.ports = []
    

    def getKind(self) -> str:
        """Return device kind"""
        return 'analogOscope'
    

    async def open(self):
        """Open device and configure scope using WF_SDK"""

        def _open():
            # Open device using WF_SDK
            dev = device.open()
            
            # Open scope with WF_SDK
            scope.open(dev, sampling_frequency=self.sampleRate, buffer_size=self.bufferSize)
            
            # Set trigger using WF_SDK with configurable trigger channel
            scope.trigger(dev, enable=True, source=scope.trigger_source.analog, channel=self.triggerChannel, level=self.triggerThreshold)
            
            # Setup and start acquisition
            scope.setupRawBuffers(dev)
            
            return dev
        
        self.device = await self.ioLayer.runInExecutor(_open)
        self.log.info('Device opened', deviceId=self.deviceId)
    

    async def close(self):
        """Close device"""
        if self.device:
            await self.ioLayer.runInExecutor(lambda: device.close(self.device))
        self.log.info('Device closed', deviceId=self.deviceId)
    

    async def writeTo(self, data: bytes) -> dict:
        """Write raw bytes to device (not supported for oscope)"""
        return {"status": "not_supported", "deviceId": self.deviceId, "bytesLength": len(data), "error": "AnalogOscope does not support writeTo"}
    

    async def readLoop(self):
        """Read loop: wait for trigger, capture and process data
        
        Exceptions propagate to hardwareService's runReadLoop for full restart cycle.
        """
        while True:
            try:

                # Wait for trigger to fire (calls FDwfAnalogInStatus with True, which auto-rearms)
                await self._waitForTrigger()

                # Capture and process
                result = await self._captureAndProcess()
                if result and 'lag' in result and result['lag'] is not None:

                    # Emit data if we have valid lag measurements
                    ts = datetime.now(timezone.utc).timestamp()
                    summary = {'lag': result['lag'], 'ts': ts, 'deviceId': self.deviceId}
                    await self.emit('samples', ts, json.dumps(summary).encode('utf-8'))

                    # Adjust sample rate based on measured lag (will rearm only if rate changes)
                    if 1 in result['lag'] and result['lag'][1] != self.badVal:
                        await self._adjustSampleRate(result['lag'][1])

            except (OSError, AttributeError, Exception) as e:
                self.log.error('Hardware connection error', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e)) # Device unplugged or hardware error - propagate to trigger cleanup
                raise
    

    async def _waitForTrigger(self):
        """Wait for trigger to fire (status = Done)"""

        # Cache dwf library reference for performance
        if not hasattr(self, '_dwf'):
            if platform.startswith("win"):
                self._dwf = ctypes.cdll.dwf
            elif platform.startswith("darwin"):
                self._dwf = ctypes.cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
            else:
                self._dwf = ctypes.cdll.LoadLibrary("libdwf.so")

        def _checkStatus():
            status = ctypes.c_byte()
            result = self._dwf.FDwfAnalogInStatus(self.device.handle, ctypes.c_bool(True), ctypes.byref(status))
            if result == 0:
                raise OSError(f"Device communication failed (FDwfAnalogInStatus returned 0)")                                       # API call failed - likely device disconnected - raising an error allows re-finding and connecting
            return status.value == constants.DwfStateDone.value
        
        # Poll at ~80Hz (12.5ms interval)
        while True:
            done = await self.ioLayer.runInExecutor(_checkStatus)
            if done:
                return
            await asyncio.sleep(0.0125)
    

    async def _captureAndProcess(self) -> dict:
        """Capture raw data and process into lag values only"""

        # Cache dwf library reference for performance
        if not hasattr(self, '_dwf'):
            if platform.startswith("win"):
                self._dwf = ctypes.cdll.dwf
            elif platform.startswith("darwin"):
                self._dwf = ctypes.cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
            else:
                self._dwf = ctypes.cdll.LoadLibrary("libdwf.so")

        def _doCapture():

            # Read data from both channels
            buffer1 = (ctypes.c_double * scope.data.buffer_size)()
            buffer2 = (ctypes.c_double * scope.data.buffer_size)()
            self._dwf.FDwfAnalogInStatusData(self.device.handle, ctypes.c_int(0), buffer1, ctypes.c_int(scope.data.buffer_size))
            self._dwf.FDwfAnalogInStatusData(self.device.handle, ctypes.c_int(1), buffer2, ctypes.c_int(scope.data.buffer_size))

            # Convert to numpy arrays
            channel1 = np.array(buffer1)
            channel2 = np.array(buffer2)

            # Find rising edges (vectorized operation for performance)
            c1Rise = np.flatnonzero((channel1[:-1] < self.triggerThreshold) & (channel1[1:] > self.triggerThreshold)) + 1
            c2Rise = np.flatnonzero((channel2[:-1] < self.triggerThreshold) & (channel2[1:] > self.triggerThreshold)) + 1
            lag = {}

            # Report lag for the NON-trigger channel
            # triggerChannel is 1-based (1 or 2), so other channel is simply 3 - triggerChannel
            otherChannel = 3 - self.triggerChannel
            
            # Calculate lag if both channels have rising edges
            if len(c1Rise) > 0 and len(c2Rise) > 0:
                lag[otherChannel] = int(self.nsPerSample * ((c2Rise[0] - c1Rise[0]) if self.triggerChannel == 1 else (c1Rise[0] - c2Rise[0])))
            elif len(c1Rise) > 0 or len(c2Rise) > 0:
                # One channel has edge but not both - check if trigger channel is missing
                triggerHasEdge = (len(c1Rise) > 0 if self.triggerChannel == 1 else len(c2Rise) > 0)
                if not triggerHasEdge:
                    # Trigger channel has no edge - invalid capture, don't report bad data
                    self.log.warning('Trigger channel has no rising edge in capture', deviceId=self.deviceId,
                                   triggerChannel=self.triggerChannel, c1Edges=len(c1Rise), c2Edges=len(c2Rise))
                    return {'lag': {}}  # Return empty lag dict to skip this sample
                # Trigger OK but other channel missing - report as badVal
                lag[otherChannel] = self.badVal
            else:
                # Neither channel has edges - invalid capture
                self.log.warning('No rising edges detected in either channel', deviceId=self.deviceId,
                               triggerChannel=self.triggerChannel)
                return {'lag': {}}  # Return empty lag dict to skip this sample
            return {'lag': lag}
        
        return await self.ioLayer.runInExecutor(_doCapture)
    
    
    async def _adjustSampleRate(self, lag: float):
        """Adjust sample rate based on measured lag"""
        def _setSampleRate():

            # Calculate if resolution change is needed
            signalDelta = lag * 1e-9 + self.beforeNs * 1e-9 + self.afterNs * 1e-9
            lessResolutionIndex = max(self.sampleRateIndex - 1, 0)
            moreResolutionIndex = min(self.sampleRateIndex + 1, len(self.sampleRates) - 1)
            lessResolution = 1 / self.sampleRates[lessResolutionIndex] * self.bufferSize < signalDelta
            moreResolution = 1 / self.sampleRates[moreResolutionIndex] * self.bufferSize > signalDelta

            # Determine new index
            newIndex = self.sampleRateIndex
            if lessResolution:
                newIndex = lessResolutionIndex
            elif moreResolution:

                # Check if higher resolution won't lose signal - and apply as needed!
                timeCovered = (0.5 / self.sampleRates[moreResolutionIndex]) * self.bufferSize
                sampleUncertainty = 2 / self.sampleRates[moreResolutionIndex]
                if timeCovered >= signalDelta + sampleUncertainty:
                    newIndex = moreResolutionIndex

            # Apply new sample rate if changed
            if newIndex != self.sampleRateIndex and self.sampleRate != self.sampleRates[newIndex]:
                self.sampleRateIndex = newIndex
                self.sampleRate = self.sampleRates[newIndex]
                self.nsPerSample = int(np.ceil(1 / self.sampleRate * 1e9))
                self.beforeIndex = int(self.beforeNs / self.nsPerSample)
                self.afterIndex = int(self.afterNs / self.nsPerSample + 1)

                # Apply to hardware using WF_SDK
                scope.setSamplingFrequency(self.device, self.sampleRate)
                scope.setupRawBuffers(self.device)
                return True
            return False
        changed = await self.ioLayer.runInExecutor(_setSampleRate)
        if changed:
            self.log.info('Sample rate adjusted', deviceId=self.deviceId, sampleRate=self.sampleRate)