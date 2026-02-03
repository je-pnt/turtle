"""
UBXDevice: Hardware abstraction for UBX GNSS receiver devices.

- Manages device connection, configuration, and data streaming
- Used by hardwareService via plugin architecture
- Provides async open/close and data methods
- Verifies configuration with ACK/NACK responses

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio, time
from datetime import datetime, timezone

# Local imports
from sdk.logging import getLogger
from sdk.parsers.ubx import Ubx
from sdk.parsers.nmea import Nmea
from .baseDevice import BaseDevice


# Class
class UBXDevice(BaseDevice):
    def __init__(self, deviceId: str, port: str, baudrate: int, ioLayer, transport=None, subjectBuilder=None, novaAdapter=None, rxType: str = None):
        super().__init__(deviceId, ioLayer, transport, subjectBuilder, novaAdapter)
        self.port = port
        self.baudrate = baudrate
        self.ports = [port]
        self.reader = None
        self.writer = None
        self.lastWriteTime = 0                             # Track last write for 125ms delay enforcement
        self.log = getLogger()      # Auto-detect logger hierarchy (computed ONCE): 'hardwareService.devices.ubxDevice.UBXDevice'
        self.rxType = rxType
        self.ubxParser = Ubx()                             # UBX parser for messages
        self.nmeaParser = Nmea()                           # NMEA parser for mixed protocols
        self.parseBuffer = b''                             # Buffer for incomplete messages
        self.readerLock = asyncio.Lock()                   # Serialize access to reader between readLoop and writeTo
        self.pendingAcks = []                              # ACK/NACK messages saved by readLoop for writeTo


    def getKind(self) -> str:
        """Return device kind (dynamic)"""
        return self.rxType if self.rxType else None
    

    async def open(self):
        """Open serial port"""
        self.reader, self.writer = await self.ioLayer.openConnection('serial', port=self.port, baudrate=self.baudrate)
        self.log.info('Device opened', deviceId=self.deviceId, port=self.port)
    

    async def softwareReset(self):
        """Send software reset command to UBX receiver.
        
        Uses controlled software reset (GNSS only) to avoid USB disconnect.
        """
        try:
            if self.writer:
                # UBX CFG-RST: controlled software reset (GNSS only)
                # navBbrMask=0xFFFF (clear all), resetMode=0x02 (controlled SW reset GNSS only)
                resetCmd = self._buildUbxFrame(0x06, 0x04, b'\xFF\xFF\x02\x00')
                self.log.info('Sending controlled software reset to receiver', deviceId=self.deviceId, port=self.port)
                self.writer.write(resetCmd)
                await self.writer.drain()
                await asyncio.sleep(0.5)  # Give receiver time to reset
        except Exception as e:
            self.log.warning('Failed to send reset command', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
    

    async def close(self):
        """Close serial port"""
        if self.writer:
            await self.ioLayer.closeConnection(self.writer)
        self.log.info('Device closed', deviceId=self.deviceId)
    

    async def writeTo(self, data: bytes) -> dict:
        """Write config command to device and wait for ACK/NACK response.
        UBX protocol: device responds with ACK-ACK (0x05 0x01) or ACK-NACK (0x05 0x00).
        Extracts class+msgId from command (bytes 2:4) to verify correct ACK.
        Uses readerLock only during actual reads to minimize blocking."""
        try:
            # Extract class and message ID from command for ACK verification
            if len(data) < 6:
                return {"status": "error", "deviceId": self.deviceId, "bytesLength": len(data), "error": "Command too short"}
            
            cmdClassId = data[2:4]  # Bytes 2-3 are class and message ID
            
            # Enforce 125ms delay from last write
            elapsed = time.time() - self.lastWriteTime
            if elapsed < 0.125:
                await asyncio.sleep(0.125 - elapsed)
            
            # Log what we're sending
            try:
                dataStr = data.decode('ascii', errors='replace')
            except:
                dataStr = repr(data)
            
            self.log.info('Sending config command', deviceId=self.deviceId, port=self.port, bytesLength=len(data), 
                         bytesHex=data.hex(), cmdClassId=cmdClassId.hex())
            
            # Send command once
            self.writer.write(data)
            await self.writer.drain()
            self.lastWriteTime = time.time()
            
            # Wait for ACK/NACK response (timeout after 2 seconds)
            ackReceived = False
            nackReceived = False
            startTime = time.time()
            ackTimeout = 2.0
            
            self.log.debug(f'Waiting for ACK/NACK for cmd {cmdClassId.hex()}', deviceId=self.deviceId, cmdClassId=cmdClassId.hex())
            
            while time.time() - startTime < ackTimeout:
                # Check if readLoop already found the ACK/NACK we need
                for i, parsed in enumerate(self.pendingAcks[:]):
                    msgName = next(iter(parsed))
                    if msgName in ('ack-ack', 'ack-nack'):
                        msgData = parsed[msgName]
                        # Extract class and message ID from ACK
                        ackClsId = eval(msgData.get('clsId', "b''")) if isinstance(msgData.get('clsId'), str) else msgData.get('clsId', b'')
                        ackMsgId = eval(msgData.get('msgId', "b''")) if isinstance(msgData.get('msgId'), str) else msgData.get('msgId', b'')
                        ackClassId = ackClsId + ackMsgId
                        
                        # Check if this ACK matches our command
                        if ackClassId == cmdClassId:
                            self.pendingAcks.pop(i)  # Remove from pending list
                            if msgName == 'ack-ack':
                                ackReceived = True
                                self.log.info('Config ACK received (from readLoop)', deviceId=self.deviceId, cmdClassId=cmdClassId.hex())
                            else:
                                nackReceived = True
                                self.log.warning('Config NACK received (from readLoop)', deviceId=self.deviceId, cmdClassId=cmdClassId.hex())
                            break
                
                if ackReceived or nackReceived:
                    break
                
                # Wait a bit before checking again
                try:
                    await asyncio.sleep(0.05)
                except asyncio.TimeoutError:
                    continue  # Keep waiting until overall timeout
            
            # Return result based on ACK/NACK
            if ackReceived:
                return {"status": "applied", "deviceId": self.deviceId, "bytesLength": len(data), "ack": True}
            elif nackReceived:
                return {"status": "rejected", "deviceId": self.deviceId, "bytesLength": len(data), "error": "Device sent NACK"}
            else:
                self.log.warning('Config ACK timeout', deviceId=self.deviceId, cmdClassId=cmdClassId.hex(), timeoutSec=ackTimeout)
                return {"status": "timeout", "deviceId": self.deviceId, "bytesLength": len(data), "error": f"No ACK received within {ackTimeout}s"}
        
        except Exception as e:
            self.log.error('Command send failed', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
            return {"status": "error", "deviceId": self.deviceId, "bytesLength": len(data), "error": str(e)}

    async def readLoop(self):
        """Read loop: stream UBX data - Exceptions propagate to hardwareService's runReadLoop for full restart cycle.
        
        Pattern from /svs:
        1. Publish raw bytes immediately to raw lane
        2. Add to size-limited parseBuffer
        3. Parse all UBX messages from buffer
        4. Parse all NMEA messages from buffer (mixed protocols)
        5. Publish all parsed messages to parsed lane
        6. Extract ACK/NACK for writeTo() to consume
        """
        self.log.info('ReadLoop started', deviceId=self.deviceId)
        
        # Publish entity descriptor so UI knows the entity type for card selection
        if self.novaAdapter:
            await self.novaAdapter.publishEntityDescriptor(
                deviceId=self.deviceId,
                entityType='ubx',  # Maps to gnss-receiver-card
                displayName=self.deviceId,
                description=f'UBX GNSS Receiver on {self.port}'
            )
        
        while True:
            # Read data from serial port
            data = await self.reader.read(4096)
            if not data:
                await asyncio.sleep(0.1)
                continue
            
            # Update activity timestamp
            ts = datetime.now(timezone.utc).timestamp()
            self.lastSeen = ts
            
            # 1. Publish raw bytes IMMEDIATELY to raw lane (preserve exact read boundaries)
            if self.novaAdapter:
                await self.novaAdapter.publishRaw(self.deviceId, self._rawSequence, data)
                self._rawSequence += 1
            
            # 2. Add to parseBuffer (size-limited like /svs)
            self.parseBuffer += data
            if len(self.parseBuffer) > 50000:  # 50KB limit from /svs
                self.log.warning('Parse buffer overflow, resetting', deviceId=self.deviceId, bufferSize=len(self.parseBuffer))
                self.parseBuffer = b''
                continue
            
            # 3. Parse all UBX messages
            try:
                self.parseBuffer, ubxMessages = self.ubxParser.parseAll(self.parseBuffer)
            except Exception as e:
                self.log.error('UBX parser error', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
                self.parseBuffer = b''
                ubxMessages = []
            
            # 4. Parse all NMEA messages (mixed protocols)
            try:
                self.parseBuffer, nmeaMessages = self.nmeaParser.parseAll(self.parseBuffer)
            except Exception as e:
                self.log.error('NMEA parser error', deviceId=self.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
                nmeaMessages = []
            
            # 5. Process all parsed messages
            for parsed in ubxMessages + nmeaMessages:
                msgName = next(iter(parsed))
                msgData = parsed[msgName]
                
                # Extract ACK/NACK for writeTo()
                if msgName in ('ack-ack', 'ack-nack'):
                    self.pendingAcks.append(parsed)
                    self.log.debug(f'ReadLoop found {msgName}', deviceId=self.deviceId, msgName=msgName, 
                                 clsId=msgData.get('clsId'), msgId=msgData.get('msgId'))
                
                # Publish ALL parsed messages to parsed lane (if NOVA adapter available)
                if self.novaAdapter:
                    # Determine streamType based on message name
                    if msgName in ('ack-ack', 'ack-nack'):
                        streamType = f'ubx.{msgName}'
                    elif msgName in ('GGA', 'GNS', 'RMC', 'GSA', 'GSV', 'VTG', 'GLL'):
                        streamType = f'nmea.{msgName}'
                    else:
                        streamType = f'ubx.{msgName}'
                    
                    await self.novaAdapter.publishParsed(
                        deviceId=self.deviceId,
                        streamId=f'stream{self.deviceId}',
                        streamType=streamType,
                        payload=msgData
                    )
                    
                    # Emit standardized Position for position-containing messages
                    # Parser already extracted these fields - no re-parsing needed
                    if msgName == 'nav_pvt':
                        # Build UTC timestamp from nav_pvt time fields
                        # nav_pvt provides: year, month, day, hour, min, sec, nano (ns)
                        gnssTime = None
                        try:
                            year = msgData.get('year')
                            month = msgData.get('month')
                            day = msgData.get('day')
                            hour = msgData.get('hour')
                            minute = msgData.get('min')
                            sec = msgData.get('sec')
                            nano = msgData.get('nano (ns)', 0)
                            
                            if all(v is not None for v in [year, month, day, hour, minute, sec]):
                                # Construct ISO 8601 UTC timestamp
                                # Include milliseconds from nano field
                                ms = int(nano / 1_000_000) if nano else 0
                                gnssTime = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{sec:02d}.{ms:03d}Z"
                        except Exception:
                            pass  # gnssTime stays None if conversion fails
                        
                        position = {
                            'lat': msgData.get('lat (deg)'),
                            'lon': msgData.get('lon (deg)'),
                            'alt': msgData.get('height (m)'),
                            'time': msgData.get('iTOW (ms)'),
                            'fixType': msgData.get('fixType')
                        }
                        await self.novaAdapter.publishParsed(
                            deviceId=self.deviceId,
                            streamId=f'stream{self.deviceId}',
                            streamType='Position',
                            payload=position
                        )
                        
                        # Publish UiUpdate for GNSS telemetry card (UI lane)
                        # Maps nav_pvt fields to manifest-defined keys (telemetry.gnss)
                        await self.novaAdapter.publishUiUpdate(
                            deviceId=self.deviceId,
                            viewId='telemetry.gnss',
                            manifestId='telemetry.gnss',
                            manifestVersion='1.0.0',
                            data={
                                'gnssTime': gnssTime,
                                'lat': msgData.get('lat (deg)'),
                                'lon': msgData.get('lon (deg)'),
                                'alt': msgData.get('height (m)'),
                                'fixType': msgData.get('fixType'),
                                'numSv': msgData.get('numSv'),  # Parser uses lowercase 'v'
                                'hAcc': msgData.get('hAcc (m)'),
                                'vAcc': msgData.get('vAcc (m)'),
                                'pDOP': msgData.get('pDOP'),
                            }
                        )
                    
                    # NMEA RMC message handling (fallback for receivers without UBX nav_pvt)
                    # RMC has both date and time, making it the best NMEA source for gnssTime
                    elif msgName == 'RMC' and msgData.get('status') == 'A':  # 'A' = active/valid
                        gnssTime = self._buildNmeaTime(msgData.get('time'), msgData.get('date'))
                        
                        # Convert lat/lon from degMin to decimal degrees
                        lat = self._nmeaLatLonToDeg(msgData.get('lat (degMin)'), msgData.get('NS'))
                        lon = self._nmeaLatLonToDeg(msgData.get('lon (degMin)'), msgData.get('EW'))
                        
                        await self.novaAdapter.publishUiUpdate(
                            deviceId=self.deviceId,
                            viewId='telemetry.gnss',
                            manifestId='telemetry.gnss',
                            manifestVersion='1.0.0',
                            data={
                                'gnssTime': gnssTime,
                                'lat': lat,
                                'lon': lon,
                                # RMC doesn't have altitude, accuracy, or satellite count
                            }
                        )
                    
                    # NMEA GGA message handling - provides altitude and satellite count
                    # GGA quality: 0=invalid, 1=GPS fix, 2=DGPS fix, etc.
                    elif msgName == 'GGA' and msgData.get('quality') not in (None, 0, '0'):
                        # Convert lat/lon from degMin to decimal degrees
                        lat = self._nmeaLatLonToDeg(msgData.get('lat (degMin)'), msgData.get('NS'))
                        lon = self._nmeaLatLonToDeg(msgData.get('lon (degMin)'), msgData.get('EW'))
                        
                        # Map quality to fixType string for consistency with UBX
                        qualityMap = {
                            1: '2D Fix', 2: 'DGPS Fix', 4: 'RTK Fixed', 5: 'RTK Float',
                            6: 'Dead Reckoning'
                        }
                        quality = msgData.get('quality')
                        fixType = qualityMap.get(quality, f'Fix {quality}') if quality else 'No Fix'
                        
                        await self.novaAdapter.publishUiUpdate(
                            deviceId=self.deviceId,
                            viewId='telemetry.gnss',
                            manifestId='telemetry.gnss',
                            manifestVersion='1.0.0',
                            data={
                                'lat': lat,
                                'lon': lon,
                                'alt': msgData.get('alt (m)'),
                                'fixType': fixType,
                                'numSv': msgData.get('numSV'),
                                'hAcc': msgData.get('HDOP'),  # HDOP as horizontal accuracy proxy
                            }
                        )
                    
                    # nav_sat message - satellite/constellation info for UI table
                    elif msgName == 'nav_sat':
                        # Build svInfo structure: {constellation: {svId: {cno, elev, azim}}}
                        svInfo = {}
                        allCnos = []  # Collect all CN0 values for metrics
                        
                        for const in self.ubxParser.gnssId.values():
                            if const in msgData and isinstance(msgData[const], dict):
                                constSvs = {}
                                for svId, svData in msgData[const].items():
                                    if isinstance(svData, dict):
                                        cno = svData.get('cno (dBHz)')
                                        constSvs[svId] = {
                                            'cno': cno,
                                            'elev': svData.get('elev (deg)'),
                                            'azim': svData.get('azim (deg)'),
                                        }
                                        if cno is not None and cno > 0:
                                            allCnos.append(cno)
                                if constSvs:
                                    svInfo[const] = constSvs
                        
                        # Compute CN0 metrics
                        uiData = {'svInfo': svInfo}
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
                    
                    # nav_sig message - per-signal info (F9 and newer)
                    elif msgName == 'nav_sig':
                        # Build BOTH sigInfo and svInfo from nav_sig data
                        # sigInfo: {constellation: {svId: {signalId: {cno, quality}}}}
                        # svInfo: {constellation: {svId: {cno, elev, azim}}} - aggregated per SV
                        sigInfo = {}
                        svInfo = {}
                        allCnos = []
                        
                        for const in self.ubxParser.gnssId.values():
                            if const in msgData and isinstance(msgData[const], dict):
                                constSigSvs = {}
                                constSvSvs = {}
                                
                                for svId, svData in msgData[const].items():
                                    if isinstance(svData, dict):
                                        # Build sigInfo: per-signal data
                                        sigEntries = {}
                                        maxCno = None
                                        for sigName, sigData in svData.items():
                                            if isinstance(sigData, dict):
                                                cno = sigData.get('cno (dBHz)')
                                                sigEntries[sigName] = {
                                                    'cno': cno,
                                                    'quality': sigData.get('qualityInd'),
                                                }
                                                # Track max CN0 across all signals for this SV
                                                if cno is not None and (maxCno is None or cno > maxCno):
                                                    maxCno = cno
                                        
                                        if sigEntries:
                                            constSigSvs[svId] = sigEntries
                                        
                                        # Build svInfo: aggregate per-SV data (use max CN0 from any signal)
                                        # Note: nav_sig doesn't have elev/azim - don't include them so we don't overwrite nav_sat data
                                        if maxCno is not None:
                                            constSvSvs[svId] = {
                                                'cno': maxCno,
                                            }
                                            allCnos.append(maxCno)
                                
                                if constSigSvs:
                                    sigInfo[const] = constSigSvs
                                if constSvSvs:
                                    svInfo[const] = constSvSvs
                        
                        # Publish both sigInfo AND svInfo so both tables work
                        if sigInfo or svInfo:
                            uiData = {}
                            if sigInfo:
                                uiData['sigInfo'] = sigInfo
                            if svInfo:
                                uiData['svInfo'] = svInfo
                            # Add CN0 metrics
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
    
    # Phase 5: Command handlers
    async def cmd_setUpdateRate(self, rateHz: int):
        """Set GPS update rate in Hz (1-10)"""
        self.log.info(f'[Command] setUpdateRate: {rateHz} Hz', deviceId=self.deviceId)
        
        if not 1 <= rateHz <= 10:
            raise ValueError(f"Invalid rate: {rateHz}. Must be 1-10 Hz")
        
        measurementRate = int(1000 / rateHz)
        payload = measurementRate.to_bytes(2, 'little') + b'\x01\x00\x01\x00'
        frame = self._buildUbxFrame(0x06, 0x08, payload)
        result = await self.writeTo(frame)
        
        if result.get('status') == 'ack':
            return {"rateHz": rateHz, "measurementRateMs": measurementRate}
        else:
            raise Exception(f"Command failed: {result.get('error', 'NACK received')}")
    
    async def cmd_coldReset(self):
        """Perform cold reset (clears all navigation data)"""
        self.log.info('[Command] coldReset', deviceId=self.deviceId)
        await self._sendReset(0xFFFF)  # Clear all
        return {"status": "reset", "message": "Cold reset initiated"}
    
    async def cmd_hotStart(self):
        """Perform hot start (keeps all navigation data)"""
        self.log.info('[Command] hotStart', deviceId=self.deviceId)
        await self._sendReset(0x0000)  # Keep all
        return {"status": "reset", "message": "Hot start initiated"}
    
    async def cmd_warmStart(self):
        """Perform warm start (clears ephemeris, keeps almanac)"""
        self.log.info('[Command] warmStart', deviceId=self.deviceId)
        await self._sendReset(0x0001)  # Clear ephemeris only
        return {"status": "reset", "message": "Warm start initiated"}
    
    async def cmd_configUpload(self, filename: str = "", commands: list = None):
        """Apply configuration from uploaded file content.
        
        Mirrors /svs configurator pattern:
        1. Parse lines as UBX hex commands (format: 'msg-name - B5 62 ... checksum')
        2. Send each command via writeTo() with ACK/NACK verification
        3. Return summary of success/failure counts
        
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
            
            # Parse UBX hex command (format: 'msg-name - B5 62 ... checksum')
            cmdBytes = self._parseConfigLine(line)
            if not cmdBytes:
                self.log.warning(f'[configUpload] Failed to parse line {i}: {line[:50]}...', deviceId=self.deviceId)
                failureCount += 1
                errors.append(f"Line {i}: parse error")
                continue
            
            # Send command and wait for ACK
            try:
                result = await self.writeTo(cmdBytes)
                status = result.get('status', 'unknown')
                
                if status in ('applied', 'ack'):
                    successCount += 1
                    self.log.debug(f'[configUpload] Line {i}/{len(commands)} OK', deviceId=self.deviceId)
                else:
                    failureCount += 1
                    errors.append(f"Line {i}: {result.get('error', status)}")
                    self.log.warning(f'[configUpload] Line {i} failed: {status}', deviceId=self.deviceId)
                
                # Delay between commands (same as /svs)
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
            "errors": errors[:10]  # Limit error list
        }
    
    def _parseConfigLine(self, line: str) -> bytes:
        """Parse a UBX config line to bytes (same format as sdk/parsers/ubx.py configure).
        
        Formats supported:
        - 'msg-name - B5 62 06 01 ...' (standard UBX config file)
        - 'B5 62 06 01 ...' (raw hex)
        - 'b'\\xb5\\x62...' (Python bytes literal)
        - '\\xb5\\x62...' (escape sequences)
        """
        line = line.strip()
        
        # Format 1: 'msg-name - B5 62 ...' (extract after last '-')
        if '-' in line:
            hexString = line[line.rfind('-') + 1:].strip()
            try:
                return bytes.fromhex(hexString.replace(' ', ''))
            except ValueError:
                pass
        
        # Format 2: Python bytes literal b'\xb5\x62...'
        if line.startswith("b'") or line.startswith('b"'):
            try:
                return eval(line)
            except:
                pass
        
        # Format 3: Escape sequences \xb5\x62...
        if '\\x' in line:
            try:
                cleanHex = line.strip("'\"").replace('\\x', '')
                return bytes.fromhex(cleanHex)
            except:
                pass
        
        # Format 4: Raw hex string 'B5 62 06...' or 'B56206...'
        try:
            hexStr = line.replace(' ', '').replace('-', '').replace(':', '')
            return bytes.fromhex(hexStr)
        except:
            pass
        
        return None

    async def _sendReset(self, navBbrMask: int):
        """Send UBX CFG-RST with specified navBbrMask.
        
        Uses resetMode=2 (controlled software reset) instead of resetMode=0 (hardware reset)
        to avoid USB disconnect which causes serial port errors.
        
        resetMode values:
        - 0x00: Hardware reset (immediate) - disconnects USB!
        - 0x01: Controlled software reset
        - 0x02: Controlled software reset (GNSS only)
        - 0x04: Hardware reset after shutdown
        - 0x08: Controlled GNSS stop
        - 0x09: Controlled GNSS start
        """
        if not self.writer:
            raise Exception("Device not connected")
        # Use resetMode=0x02 (controlled software reset, GNSS only) to avoid USB disconnect
        payload = navBbrMask.to_bytes(2, 'little') + b'\x02\x00'
        frame = self._buildUbxFrame(0x06, 0x04, payload)
        self.writer.write(frame)
        await self.writer.drain()
        await asyncio.sleep(0.5)  # Give receiver time to reset GNSS

    async def cmd_enableItow(self, enable: bool = True):
        """Enable/disable iTOW output - YOUR SPECIAL TEST!"""
        self.log.info(f'[Command] enableItow: {enable}', deviceId=self.deviceId)
        
        rate = 1 if enable else 0
        payload = b'\x01\x07' + bytes([rate] * 6)
        frame = self._buildUbxFrame(0x06, 0x01, payload)
        result = await self.writeTo(frame)
        
        if result.get('status') == 'ack':
            return {"itowEnabled": enable}
        else:
            raise Exception(f"Command failed: {result.get('error')}")
    
    def _buildUbxFrame(self, msgClass: int, msgId: int, payload: bytes) -> bytes:
        """Build UBX frame with checksum"""
        header = b'\xb5\x62'
        classId = bytes([msgClass, msgId])
        length = len(payload).to_bytes(2, 'little')
        
        ckA, ckB = 0, 0
        for byte in classId + length + payload:
            ckA = (ckA + byte) & 0xFF
            ckB = (ckB + ckA) & 0xFF
        
        return header + classId + length + payload + bytes([ckA, ckB])
    
    def _buildNmeaTime(self, timeStr: str, dateStr: str) -> str:
        """Build ISO 8601 UTC timestamp from NMEA time and date strings.
        
        Args:
            timeStr: HHMMSS.sss format (e.g., "123456.789")
            dateStr: DDMMYY format (e.g., "290126" for Jan 29, 2026)
            
        Returns:
            ISO 8601 UTC string or None if conversion fails
        """
        try:
            if not timeStr or not dateStr:
                return None
            
            # Parse time: HHMMSS.sss
            hh = int(timeStr[0:2])
            mm = int(timeStr[2:4])
            ss = int(timeStr[4:6])
            ms = int(float(timeStr[6:]) * 1000) if len(timeStr) > 6 else 0
            
            # Parse date: DDMMYY
            day = int(dateStr[0:2])
            month = int(dateStr[2:4])
            year = int(dateStr[4:6])
            # Assume 2000s for two-digit year
            year = 2000 + year if year < 80 else 1900 + year
            
            return f"{year:04d}-{month:02d}-{day:02d}T{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}Z"
        except Exception:
            return None
    
    def _nmeaLatLonToDeg(self, degMin: str, direction: str) -> float:
        """Convert NMEA lat/lon (DDDMM.mmmm) to decimal degrees.
        
        Args:
            degMin: Degrees and minutes string (e.g., "4807.038" for 48°07.038')
            direction: N/S/E/W
            
        Returns:
            Decimal degrees (negative for S/W)
        """
        try:
            if not degMin:
                return None
            
            # Find decimal point position to split degrees from minutes
            dotPos = degMin.index('.')
            degrees = int(degMin[:dotPos-2])
            minutes = float(degMin[dotPos-2:])
            
            result = degrees + minutes / 60.0
            
            # Negate for South/West
            if direction in ('S', 'W'):
                result = -result
            
            return result
        except Exception:
            return None
