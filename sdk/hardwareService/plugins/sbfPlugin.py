"""
SBFPlugin: Plugin for SBF GNSS receiver integration (X5, Mosaic, and compatible).

- Probes hardware ports and identifies SBF receiver devices
- Used by hardwareService for device discovery and management
- Supports dynamic rxType for multiple SBF receiver models
- Provides async test and device factory methods
- Extensible for new SBF models

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio

# Local imports
from sdk.parsers import Sbf
from ..devices.sbfDevice import SBFDevice
from .basePlugin import BasePlugin


# Class
class SBFPlugin(BasePlugin):
    
    def __init__(self):
        self.rxType = None
        self.serialNumber = None
        self.virtualPort = None
    

    @staticmethod
    def getKind() -> str:
        return 'sbf'
    

    @staticmethod
    async def test(ports: list, ioLayer) -> list:
        """Probe all ports using SBF parser test() contract - exact match to Sbf.test()"""
        
        candidates = []
        parser = Sbf()
        portMap = {}
        
        for port in ports:
            try:

                # Open port at 115200 and set flags
                reader, writer = await asyncio.wait_for(ioLayer.openConnection('serial', port=port, baudrate=115200), timeout=2.0)
                bytesBin, deviceId, rxType, foundPort = b'', None, None, None
                
                # Try both virtual ports 
                for virtualPort in ['USB1', 'USB2']:

                    # Send command 3 times 
                    for _ in range(3):
                        cmd = f'esoc, {virtualPort}, ReceiverSetup \n'.encode('ASCII')
                        writer.write(cmd)
                        await writer.drain()
                        await asyncio.sleep(0.125)
                    
                    # Read response
                    try:
                        data = await asyncio.wait_for(reader.read(4096), timeout=0.5)
                        bytesBin += data
                    except:
                        pass
                    
                    # Parse using SBF parser
                    bytesBin, messages = parser.parseAll(bytesBin)
                    
                    # Look for ReceiverSetup response 
                    for parsed in messages:
                        if message := parsed.get('ReceiverSetup', False):
                            deviceId = message.get('RxSerialNumber', False)
                            rxType = message.get('ProductName', None)
                            rxName = message.get('RxName', '')
                            rxVersion = message.get('RxVersion', '')
                            foundPort = virtualPort
                            
                            # Debug: print what we got
                            print(f'[SBFPlugin DEBUG] deviceId={deviceId}, rxType={rxType}, rxName={rxName}, rxVersion={rxVersion}')
                            break
                    
                    if deviceId:
                        portMap[port] = foundPort
                        break  # Found it on this virtual port
                
                # Close probe connection
                await ioLayer.closeConnection(writer)
                # Give OS time to release the port (critical on Windows)
                await asyncio.sleep(0.5)
                
                if deviceId:
                    # Format deviceId consistently: serialNumber-Type (e.g., '37651704393-X5')
                    # Clean up rxType and determine proper type string
                    if rxType and rxType.strip():
                        typeStr = rxType.strip()
                    elif rxName and ('mosaic' in rxName.lower() or 'x5' in rxName.lower()):
                        # X5 receivers often have "mosaic-X5" or similar in RxName
                        typeStr = 'X5'
                    elif rxVersion and 'x5' in rxVersion.lower():
                        # Check version string for X5 indicator
                        typeStr = 'X5'
                    else:
                        # Default to SBF for unknown SBF receivers
                        typeStr = 'SBF'
                    
                    formattedDeviceId = f"{deviceId}-{typeStr}"
                    candidates.append({'deviceId': formattedDeviceId, 'kind': 'sbf', 'port': port, 'meta': {'baudrate': 115200, 'portMap': portMap.copy(), 'rxType': typeStr}})
                    print(f'[SBFPlugin] Found SBF receiver deviceId={formattedDeviceId} on {port}/{foundPort}')
                        
            except Exception as e:
                pass  
        
        return candidates
    

    @staticmethod
    async def createDevice(deviceId: str, ports: list, meta: dict, ioLayer, transport=None, subjectBuilder=None, novaAdapter=None):

        # Ensure port(s) is available
        port = ports[0] if ports else None
        if not port:
            return None
        
        # Create Device with portMap
        baudrate, portMap, rxType = meta.get('baudrate', 115200), meta.get('portMap', {}), meta.get('rxType', 'X5')
        return SBFDevice(deviceId, port, baudrate, ioLayer, portMap=portMap, transport=transport, 
                        subjectBuilder=subjectBuilder, novaAdapter=novaAdapter, rxType=rxType)