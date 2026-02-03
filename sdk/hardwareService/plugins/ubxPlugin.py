"""
M9Plugin: Plugin for M9/Ublox GNSS receiver integration.

- Probes hardware ports and identifies M9/Ublox devices
- Used by hardwareService for device discovery and management
- Provides async test and device factory methods
- Extensible for new M9/Ublox models

Property of Uncompromising Sensors LLC.
"""

# ImportsImports
import asyncio, re

# Local imports
from sdk.parsers import Ubx
from .basePlugin import BasePlugin
from ..devices.ubxDevice import UBXDevice


# Class
class UBXPlugin(BasePlugin):
    rxType = None


    @classmethod
    def getKind(cls) -> str:
        return cls.rxType if cls.rxType else 'ubx'
    

    @staticmethod
    async def test(ports: list, ioLayer) -> list:
        """Probe all ports using UBX parser test() contract - async-compatible and concise."""

        parser = Ubx()
        messageBytes = [b"\xb5b'\x03\x00\x00*\xa5", b'\xb5b\n\x04\x00\x00\x0e4']
        messageNames = ['ubx_sec', 'mon_ver']
        fields = ['uniqueId', 'extension3']
        candidates = []

        # Test all ports and baud rates
        for port in ports:
            for baudrate in (9600, 115200, 38400):
                try:
                    reader, writer = await asyncio.wait_for(ioLayer.openConnection('serial', port=port, baudrate=baudrate), timeout=2.0)
                    ret = {}
                    for messageByte, messageName, field in zip(messageBytes, messageNames, fields):
                        for _ in range(3):
                            try:
                                await asyncio.wait_for(reader.read(1024), timeout=0.01)
                            except Exception:
                                pass
                            for _ in range(3):
                                writer.write(messageByte)
                                await writer.drain()
                                await asyncio.sleep(0.125)
                            data = await asyncio.wait_for(reader.read(4096), timeout=0.5)
                            _, messages = parser.parseAll(data)
                            for parsed in messages:
                                if messageName in parsed:
                                    ret[messageName] = parsed[messageName].get(field, 'unknown')
                                    break
                            if messageName in ret:
                                break
                    await ioLayer.closeConnection(writer)
                    # Give OS time to release the port (critical on Windows)
                    await asyncio.sleep(0.5)

                    # Handle return here to break out early if baud rate is found
                    if ret:
                        uniqueId = ret.get('ubx_sec', 'unknown')
                        model = ret.get('mon_ver', 'unknown')
                        if (match := re.search('[0-9]{1,2}', model)):
                            model = model[match.start() - 1: match.end()+1]
                        deviceId = f"{uniqueId}-{model}" if uniqueId != 'unknown' else model
                        candidates.append({'deviceId': deviceId, 'kind': 'ubx','port': port,'meta': {'baudrate': baudrate, 'model': model, 'uniqueId': uniqueId}})
                        print(f'[UBXPlugin] Found UBX deviceId={deviceId} on {port}')
                        break
                except Exception:
                    pass

        return candidates
    

    @staticmethod
    async def createDevice(deviceId: str, ports: list, meta: dict, ioLayer, transport=None, subjectBuilder=None, novaAdapter=None):
        """Create UBX device instance"""
        port = ports[0] if ports else None
        if not port:
            return None
        baudrate = meta.get('baudrate', 115200)
        rxType = meta.get('kind', 'ubx')
        return UBXDevice(deviceId, port, baudrate, ioLayer, transport=transport, subjectBuilder=subjectBuilder, novaAdapter=novaAdapter, rxType=rxType)