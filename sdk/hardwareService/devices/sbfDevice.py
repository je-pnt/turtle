""" SBFDevice: Hardware abstraction for SBF GNSS receiver devices (X5 and compatible).

- Manages device connection, configuration, and data streaming
- Used by hardwareService via plugin architecture
- Supports dynamic rxType for multiple SBF receiver models
- Provides async open/close and data methods
- Extensible for new SBF models

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio, re, time
from datetime import datetime, timezone, timedelta

# Local imports
from sdk.logging import getLogger
from sdk.parsers.sbf import Sbf
from .baseDevice import BaseDevice


# Class
class SBFDevice(BaseDevice):
    
    def __init__(self, deviceId: str, port: str, baudrate: int, ioLayer, portMap: dict = None, 
                 transport=None, subjectBuilder=None, novaAdapter=None, rxType: str = None):
        super().__init__(deviceId, ioLayer, transport, subjectBuilder, novaAdapter)
        self.port = port
        self.baudrate = baudrate
        self.portMap = portMap if portMap else {port: 'USB1'}
        self.virtualPort = self.portMap.get(port, 'USB1')
        self.ports = [port]
        self.readers = {}
        self.writers = {}
        self.reader = None
        self.writer = None
        self.lastWriteTime = 0
        self.parseBuffer = b''
        self.sbf = Sbf()
        self.log = getLogger()
        self.rxType = rxType
        self.pendingCmds = {}
        self.ackTimeout = 1.5


    def getKind(self):
        """Return the device kind for subject routing (always 'sbf' for SBF devices)."""
        return 'sbf'
    

    async def open(self):
        """Open serial port and configure virtual port for data streaming"""
        self.reader, self.writer = await self.ioLayer.openConnection('serial', port=self.port, baudrate=self.baudrate)
        self.readers[self.port] = self.reader
        self.writers[self.port] = self.writer
    

    async def softwareReset(self):
        """Send software reset command to Mosaic receiver"""
        try:
            if self.writer:
                resetCmd = f'exeResetReceiver, soft, none\n'.encode('ascii')
                self.log.info('Sending software reset to receiver', deviceId=self.deviceId, port=self.port)
                self.writer.write(resetCmd)
                await self.writer.drain()
                await asyncio.sleep(0.2)  # Give receiver time to process reset
        except Exception as e:
            self.log.warning('Failed to send reset command', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
    

    async def close(self):
        """Close all serial ports"""
        for port, writer in self.writers.items():
            if writer:
                try:
                    await self.ioLayer.closeConnection(writer)
                except Exception as e:
                    self.log.warning('Error closing port', deviceId=self.deviceId, port=port, 
                                   errorClass=type(e).__name__, errorMsg=str(e))
        self.writers.clear()
        self.readers.clear()
    

    async def writeTo(self, data: bytes) -> dict:
        """Write raw bytes to device with virtualPort rewriting
        
        Sends command 3 times with 125ms delay (hardware reliability requirement).
        Ensures minimum 125ms delay from last write operation."""

        try:
            self.lastSeen = time.time()  # Update activity timestamp
            elapsed = time.time() - self.lastWriteTime
            if elapsed < 0.125:
                await asyncio.sleep(0.125 - elapsed)
            
            virtualPortBytes = self.virtualPort.encode('utf-8')
            dataRewritten = re.sub(b'USB1|USB2', virtualPortBytes, data)
            dataStr = dataRewritten.decode('ascii', errors='replace').strip()
            
            cmdKey = dataStr.split(',')[0].strip() if ',' in dataStr else dataStr
            self.pendingCmds[cmdKey] = {'sent': time.time(), 'cmd': dataStr, 'acked': False}
            
            self.log.info('Sending command to device (3x)', deviceId=self.deviceId, port=self.port, virtualPort=self.virtualPort, cmd=dataStr)
            
            for attempt in range(3):
                self.writer.write(dataRewritten)
                await self.writer.drain()
                # Linux: Ensure serial buffer is flushed to hardware
                if hasattr(self.writer.transport, 'serial') and hasattr(self.writer.transport.serial, 'flush'):
                    self.writer.transport.serial.flush()
                self.lastWriteTime = time.time()
                if attempt < 2:
                    await asyncio.sleep(0.125)
            
            if self.pendingCmds.get(cmdKey, {}).get('acked'):
                return {"status": "confirmed", "deviceId": self.deviceId, "bytesLength": len(dataRewritten), "cmd": dataStr}
            else:
                return {"status": "applied", "deviceId": self.deviceId, "bytesLength": len(dataRewritten), "cmd": dataStr, "warning": "No acknowledgement received"}
        
        except Exception as e:
            self.log.error('Command send failed', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
            return {"status": "error", "deviceId": self.deviceId, "bytesLength": len(data), "error": str(e)}
    

    async def readLoop(self):
        """Read loop: stream raw SBF/NMEA data - Exceptions propagate to hardwareService's runReadLoop for full restart cycle."""
        
        self.log.info('ReadLoop started', deviceId=self.deviceId)
        
        # Publish entity descriptor so UI knows the entity type for card selection
        if self.novaAdapter:
            await self.novaAdapter.publishEntityDescriptor(
                deviceId=self.deviceId,
                entityType='mosaic-x5',  # Maps to gnss-receiver-card
                displayName=self.deviceId,
                description=f'SBF GNSS Receiver ({self.rxType}) on {self.port}'
            )
        
        maxBufferSize = 100000  # 100KB buffer limit for incomplete messages
        while True:
            try:
                data = await self.reader.read(4096)
                
                if data:
                    self.lastSeen = time.time()  # Update activity timestamp
                    
                    # Publish raw bytes to NOVA (before buffering)
                    if self.novaAdapter:
                        await self.novaAdapter.publishRaw(self.deviceId, self._rawSequence, data)
                        self._rawSequence += 1
                    
                    self.parseBuffer += data
                    
                    # Prevent buffer from growing infinitely if we have corrupted stream
                    if len(self.parseBuffer) > maxBufferSize:
                        # Keep only the most recent data
                        self.parseBuffer = self.parseBuffer[-maxBufferSize:]
                    
                    # Parse SBF messages from buffer using parseAll (handles ACKs internally)
                    self.parseBuffer, messages = self.sbf.parseAll(self.parseBuffer)
                    
                    # Process each parsed message
                    for msgDict in messages:
                        # msgDict is {msgName: msgData} - extract the single key/value
                        for msgName, msgData in msgDict.items():
                            # Publish parsed message to NOVA
                            if self.novaAdapter and msgData:
                                await self.novaAdapter.publishParsed(
                                    deviceId=self.deviceId,
                                    streamId=f'stream{self.deviceId}',
                                    streamType='sbf',
                                    payload={'messageType': msgName, **msgData}
                                )
                                
                                # Publish UiUpdate for PVTGeodetic (position messages)
                                if msgName == 'PVTGeodetic':
                                    gnssTime = self._buildGnssTime(msgData)
                                    
                                    await self.novaAdapter.publishUiUpdate(
                                        deviceId=self.deviceId,
                                        viewId='telemetry.gnss',
                                        manifestId='telemetry.gnss',
                                        manifestVersion='1.0.0',
                                        data={
                                            'gnssTime': gnssTime,
                                            'lat': msgData.get('Latitude (rad)', 0) * 57.2957795131,
                                            'lon': msgData.get('Longitude (rad)', 0) * 57.2957795131,
                                            'alt': msgData.get('Height (m)'),
                                            'fixType': msgData.get('Type of PVT Solution', 0),
                                            'numSv': msgData.get('NrSV'),
                                            'hAcc': msgData.get('HAccuracy (m)'),
                                            'vAcc': msgData.get('VAccuracy (m)'),
                                        }
                                    )
                                
                                # MeasEpoch - satellite signal data for UI tables (same pattern as UBX nav_sat + nav_sig)
                                elif msgName == 'MeasEpoch':
                                    # Parser provides: signals = {constellation: {svid: {signalId: (cn0, lockTime)}}}
                                    signals = msgData.get('signals', {})
                                    
                                    # Build svInfo: {constellation: {svId: {cno, elev, azim}}}
                                    # Note: MeasEpoch has CN0 but no El/Az (that comes from ChannelStatus)
                                    svInfo = {}
                                    sigInfo = {}
                                    allCnos = []
                                    
                                    for const, svs in signals.items():
                                        if not isinstance(svs, dict):
                                            continue
                                        svInfo[const] = {}
                                        sigInfo[const] = {}
                                        
                                        for svId, sigData in svs.items():
                                            if not isinstance(sigData, dict):
                                                continue
                                            
                                            # Build sigInfo: {constellation: {svId: {signalId: {cno}}}}
                                            sigEntries = {}
                                            firstCno = None
                                            for sigName, sigTuple in sigData.items():
                                                # sigTuple is (cn0, lockTime)
                                                cn0 = sigTuple[0] if isinstance(sigTuple, tuple) and len(sigTuple) > 0 else sigTuple
                                                if cn0 is not None and cn0 > 0:
                                                    allCnos.append(cn0)
                                                    sigEntries[sigName] = {'cno': cn0}
                                                    if firstCno is None:
                                                        firstCno = cn0
                                            
                                            if sigEntries:
                                                sigInfo[const][svId] = sigEntries
                                            
                                            # Build svInfo with first signal's CN0 (El/Az will be merged from ChannelStatus)
                                            svInfo[const][svId] = {'cno': firstCno}
                                    
                                    # Compute CN0 metrics
                                    uiData = {'svInfo': svInfo, 'sigInfo': sigInfo}
                                    if allCnos:
                                        sortedCnos = sorted(allCnos, reverse=True)
                                        uiData['avgCn0'] = sum(allCnos) / len(allCnos)
                                        uiData['cn04th'] = sortedCnos[3] if len(sortedCnos) >= 4 else (sortedCnos[-1] if sortedCnos else None)
                                    
                                    await self.novaAdapter.publishUiUpdate(
                                        deviceId=self.deviceId,
                                        viewId='telemetry.gnss',
                                        manifestId='telemetry.gnss',
                                        manifestVersion='1.0.0',
                                        data=uiData
                                    )
                                
                                # ChannelStatus - satellite tracking info with El/Az (same pattern as UBX nav_sat)
                                elif msgName == 'ChannelStatus':
                                    # Build svInfo structure: {constellation: {svId: {cno, elev, azim}}}
                                    # Parser returns: {constellation: {svNumber: {'Elevation (deg)', 'Azimuth (deg)', ...}}}
                                    svInfo = {}
                                    for const, sats in msgData.items():
                                        # Skip non-constellation keys (N, SB1Length, messageType, etc.)
                                        if not isinstance(sats, dict):
                                            continue
                                        constSvs = {}
                                        for svId, satData in sats.items():
                                            if isinstance(satData, dict):
                                                constSvs[svId] = {
                                                    'elev': satData.get('Elevation (deg)'),
                                                    'azim': satData.get('Azimuth (deg)'),
                                                }
                                        if constSvs:
                                            svInfo[const] = constSvs
                                    
                                    if svInfo:
                                        await self.novaAdapter.publishUiUpdate(
                                            deviceId=self.deviceId,
                                            viewId='telemetry.gnss',
                                            manifestId='telemetry.gnss',
                                            manifestVersion='1.0.0',
                                            data={'svInfo': svInfo}
                                        )
                    
                    # Legacy emit for backward compatibility
                    ts = datetime.now(timezone.utc).timestamp()
                    await self.emit('telemetry', ts, data)
                else:
                    if self.reader.at_eof():
                        raise ConnectionError(f"Serial port {self.port} closed (EOF)")
                    await asyncio.sleep(0.1)
            except (ConnectionError, OSError, AttributeError) as e:
                self.log.error('Serial connection error', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
                raise
    
    def _buildGnssTime(self, msgData: dict) -> str:
        """Build ISO 8601 UTC timestamp from SBF TOW and WNc fields.
        
        SBF uses GPS time: TOW (time of week in ms) and WNc (GPS week number).
        GPS epoch is January 6, 1980. Leap seconds adjustment needed for UTC.
        """
        try:
            tow = msgData.get('TOW (s)')  # Time of week in seconds
            wnc = msgData.get('WNc (weeks)')  # GPS week number
            
            if tow is None or wnc is None:
                return None
            
            # GPS epoch: January 6, 1980 00:00:00 UTC
            gps_epoch = datetime(1980, 1, 6, 0, 0, 0, tzinfo=timezone.utc)
            
            # Current GPS-UTC leap seconds (as of 2024: 18 seconds)
            # This should ideally come from receiver or be updated periodically
            leap_seconds = 18
            
            # Calculate GPS time
            gps_time = gps_epoch + timedelta(weeks=wnc, seconds=tow)
            
            # Convert to UTC (subtract leap seconds)
            utc_time = gps_time - timedelta(seconds=leap_seconds)
            
            # Format as ISO 8601 with milliseconds
            return utc_time.strftime('%Y-%m-%dT%H:%M:%S.') + f'{int((tow % 1) * 1000):03d}Z'
        except Exception:
            return None
    

    def _extractAcks(self, buffer: bytes) -> tuple:
        """Extract SBF acknowledgements/echoes from buffer
        
        Septentrio receivers echo commands back with format:
        - $R: <command>\r\n  (command accepted)
        - $R? <command>\r\n  (command with unknown status)
        - May also have SBF header bytes before the echo
        """
        acks = []
        # Match both $R: and $R? patterns, handle potential SBF header bytes
        pattern = rb'\$R[?:]\s*([^\r\n]+)'
        matches = list(re.finditer(pattern, buffer))
        
        if not matches:
            return buffer, acks
        
        # Extract acknowledged commands
        for match in matches:
            try:
                acks.append(match.group(1).decode('ascii', errors='replace').strip())
            except:
                pass
        
        # Remove all echo patterns from buffer to keep only data
        cleaned_buffer = re.sub(pattern + rb'[\r\n]*', b'', buffer)
        return cleaned_buffer, acks
    

    def _processAck(self, ackStr: str):
        """Process acknowledgement and match to pending command"""
        cmdKey = ackStr.split(',')[0].strip() if ',' in ackStr else ackStr
        
        if cmdKey in self.pendingCmds:
            self.pendingCmds[cmdKey]['acked'] = True
            self.log.info('Command acknowledged', deviceId=self.deviceId, cmd=cmdKey)
        else:
            self.log.debug('Unmatched acknowledgement', deviceId=self.deviceId, ack=ackStr)
        
        now = time.time()
        expired = [k for k, v in self.pendingCmds.items() if now - v['sent'] > self.ackTimeout]
        for k in expired:
            del self.pendingCmds[k]
    

    async def attachPort(self, port: str):
        """Attach additional port (claim without opening connection)"""
        if port not in self.ports:
            self.ports.append(port)
            if port in self.portMap:
                self.log.info('Port claimed', deviceId=self.deviceId, port=port, virtualPort=self.portMap[port])
            else:
                self.logger.log(f'SBFDevice {self.deviceId} claimed port {port}', level='INFO')
    
    # ============================================================================
    # Command Handlers (cmd_{commandType} pattern for novaAdapter)
    # ============================================================================
    
    async def cmd_coldReset(self):
        """Perform cold reset (clears all navigation data)"""
        self.log.info('[Command] coldReset', deviceId=self.deviceId)
        result = await self.writeTo(b'exeResetReceiver, cold, none\n')
        return {"status": "reset", "message": "Cold reset initiated"}
    
    async def cmd_hotStart(self):
        """Perform hot start (keeps all navigation data)"""
        self.log.info('[Command] hotStart', deviceId=self.deviceId)
        result = await self.writeTo(b'exeResetReceiver, soft, none\n')
        return {"status": "reset", "message": "Hot start initiated"}
    
    async def cmd_warmStart(self):
        """Perform warm start (clears ephemeris, keeps almanac)"""
        self.log.info('[Command] warmStart', deviceId=self.deviceId)
        result = await self.writeTo(b'exeResetReceiver, warm, none\n')
        return {"status": "reset", "message": "Warm start initiated"}
    
    async def cmd_configUpload(self, filename: str = "", commands: list = None):
        """Apply configuration from uploaded file content.
        
        Mirrors /svs configurator pattern for SBF receivers:
        1. Parse lines as ASCII commands (text commands with CRLF terminator)
        2. Send each command via writeTo()
        3. Return summary of success/failure counts
        
        SBF config format: plain text commands, one per line
        Example: 'setDataInOut, USB1, , SBF+NMEA'
        
        Args:
            filename: Original filename (for logging)
            commands: List of command lines from config file
        """
        if not commands:
            return {"status": "error", "message": "No commands provided"}
        
        self.log.info(f'[Command] configUpload: {len(commands)} lines from {filename}', deviceId=self.deviceId)
        
        successCount = 0
        failureCount = 0
        errors = []
        
        for i, line in enumerate(commands, 1):
            line = line.strip()
            
            # Skip comments and blank lines
            if not line or line.startswith('#'):
                continue
            
            # SBF commands are plain ASCII text with CRLF terminator
            cmdBytes = f'{line}\r\n'.encode('ascii')
            
            try:
                result = await self.writeTo(cmdBytes)
                status = result.get('status', 'unknown')
                
                if status in ('applied', 'confirmed'):
                    successCount += 1
                    self.log.debug(f'[configUpload] Line {i}/{len(commands)} OK', deviceId=self.deviceId)
                else:
                    failureCount += 1
                    errors.append(f"Line {i}: {result.get('error', status)}")
                    self.log.warning(f'[configUpload] Line {i} failed: {status}', deviceId=self.deviceId)
                
                # Delay between commands
                await asyncio.sleep(0.25)
                
            except Exception as e:
                failureCount += 1
                errors.append(f"Line {i}: {str(e)}")
                self.log.error(f'[configUpload] Line {i} error: {e}', deviceId=self.deviceId)
        
        message = f"{successCount} succeeded, {failureCount} failed"
        self.log.info(f'[Command] configUpload complete: {message}', deviceId=self.deviceId)
        
        return {
            "status": "complete" if failureCount == 0 else "partial",
            "message": message,
            "successCount": successCount,
            "failureCount": failureCount,
            "errors": errors[:10]
        }