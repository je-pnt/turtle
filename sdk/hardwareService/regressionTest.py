"""Regression test for hardwareService - validates core functionality.

Tests:
1. Topology discovery and data streaming
2. Configuration management (applyConfig)
3. Device restart and recovery
4. Configuration history retrieval

Property of Uncompromising Sensors LLC.
"""

import asyncio, json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from sdk.transport import createTransport
from sdk.hardwareService.subjects import SubjectBuilder
from sdk.logging import getLogger

# Configuration commands by serial number
configCommands = {
    '3816849': [b'sso, Stream1, USB1, MeasEpoch+PVTGeod, sec1  \r\n']
}


class RegressionTest:
    def __init__(self):
        self.targetDeviceId = '3816849'
        self.currentTopology = {}
        self.bytesReceived = {}
        self.monitoring = False
        self.transport = None
        self.hwSubjects = None
        self.log = getLogger()  # Auto-detects 'regressionTest.RegressionTest'
        self.regressionTestPort = 5001
        self.svsPort = 5000
    

    async def run(self):
        """Execute complete regression test suite."""
        
        # Connect to hardwareService
        await self._connect()
        
        # Test 1: Topology and data streaming
        print("\n═══ TEST 1: Topology & Data Streaming ═══")
        await self.testGetTopology()
        await self._monitorData(5)
        self._logBytesReceived()
        
        # Test 2: Configuration
        print("\n═══ TEST 2: Configuration ═══")
        await asyncio.sleep(1)  # Brief delay to ensure device fully registered
        await self.testApplyConfig()
        await self._monitorData(5)
        self._logBytesReceived(compareWithPrevious=True)
        
        # Test 3: Restart
        print("\n═══ TEST 3: Restart ═══")
        await self.testRestart()
        await self._monitorWithTopology(25)
        
        # Test 4: Configuration history
        print("\n═══ TEST 4: Configuration History ═══")
        await self.testGetConfigHistory()
        
        print("\n╔════════════════════════════════════╗")
        print("║   Regression Test Complete ✓       ║")
        print("╚════════════════════════════════════╝\n")
        
        await self.transport.close()
    

    async def _connect(self):
        """Connect to hardwareService transport."""
        try:
            configPath = os.path.join(os.path.dirname(__file__), 'config.json')
            with open(configPath, 'r') as f:
                config = json.load(f)
            transportUri = config['transport']
        except:
            transportUri = 'nng+ipc://C:\\tmp\\hwService'
        
        self.transport = createTransport(transportUri)
        self.transport.setLogger(self.log)
        await self.transport.connect(transportUri)
        self.hwSubjects = SubjectBuilder('hardwareService', containerId='Payload')
        print(f"✓ Connected to {transportUri}\n")
    async def testGetTopology(self):
        """Test topology discovery and subscribe to data streams."""
        request = json.dumps({'command': 'getTopology'})
        response = await self._sendRequest(request)
        
        if response.get('event') == 'topology':
            devices = response.get('devices', [])
            self.currentTopology = {d['deviceId']: d['kind'] for d in devices}
            
            # Clean, concise output
            deviceStrs = [f"{d['deviceId']} ({d['kind']})" for d in devices]
            print(f"Topology: {', '.join(deviceStrs) if deviceStrs else 'none'}")
                
            # Subscribe to device data
            for d in devices:
                if subject := d.get('subject'):
                    await self.transport.subscribe(subject, self._handleData)
            
            return len(devices) > 0
        
        print("✗ Failed to get topology")
        return False
    

    async def testApplyConfig(self):
        """Test configuration application."""
        if self.targetDeviceId not in configCommands:
            print(f"No config commands for device {self.targetDeviceId}")
            return False
        
        commands = configCommands[self.targetDeviceId]
        print(f"Sending {len(commands)} commands to {self.targetDeviceId}")
        
        for i, cmdBytes in enumerate(commands, 1):
            cmdText = cmdBytes.decode('ascii', errors='replace').strip()
            request = {
                "command": "applyConfig",
                "deviceId": self.targetDeviceId,
                "configBytes": list(cmdBytes),
                "label": f"Test command {i}"
            }
            
            print(f"  [{i}] {cmdText[:50]}")
            response = await self._sendRequest(json.dumps(request))
            
            status = response.get('status', 'unknown')
            print(f"      Status: {status}")
            
            if status != 'applied':
                return False
            
            await asyncio.sleep(0.25)
        
        return True
    

    async def testRestart(self):
        """Test device restart command."""
        request = {"command": "restart", "targets": "all"}
        print("Triggering restart...")
        
        response = await self._sendRequest(json.dumps(request))
        
        if response.get('event') == 'restart':
            print(f"✓ Restart initiated: {response.get('status')}")
            return True
        
        print("✗ Restart failed")
        return False
    

    async def testGetConfigHistory(self):
        """Test configuration history retrieval for all devices."""
        if not self.currentTopology:
            print("No devices to query")
            return False
        
        print("Configuration history:")
        for deviceId in self.currentTopology.keys():
            request = {
                "command": "getConfigHistory",
                "deviceId": deviceId
            }
            
            response = await self._sendRequest(json.dumps(request))
            
            if response.get('event') == 'configHistory':
                count = response.get('count', 0)
                entries = response.get('entries', [])
                
                if count > 0:
                    print(f"  {deviceId}: {count} entries")
                    for entry in entries:
                        label = entry.get('label', '')
                        bytesHex = entry.get('bytesHex', '')
                        status = entry.get('status', '')
                        
                        # Decode hex to ASCII preview (first 40 chars)
                        try:
                            bytesPreview = bytes.fromhex(bytesHex).decode('ascii', errors='replace')[:40]
                        except:
                            bytesPreview = bytesHex[:40]
                        
                        print(f"    [{status}] {label}: {bytesPreview}")
                else:
                    print(f"  {deviceId}: {count} entries")
            else:
                print(f"  {deviceId}: failed to retrieve")
        
        return True
    

    async def _sendRequest(self, request: str) -> dict:
        """Send REQ/REP request and return parsed response."""
        try:
            controlSubject = self.hwSubjects.control()  # Use SubjectBuilder for consistency
            responseBytes = await self.transport.request(controlSubject, request.encode('utf-8'))
            return json.loads(responseBytes.decode('utf-8'))
        except Exception as e:
            print(f"✗ Request error: {e}")
            return {}
    

    async def _handleData(self, subject: str, payload: bytes):
        """Count bytes received per device."""
        if self.monitoring:
            deviceId = subject.split('.')[2] if len(subject.split('.')) > 2 else 'unknown'
            self.bytesReceived[deviceId] = self.bytesReceived.get(deviceId, 0) + len(payload)
    

    async def _monitorData(self, seconds: int):
        """Monitor data for specified duration."""
        self.monitoring = True
        self.previousBytes = self.bytesReceived.copy()
        self.bytesReceived.clear()
        
        print(f"Monitoring data ({seconds}s)...")
        await asyncio.sleep(seconds)
        
        self.monitoring = False
    

    def _logBytesReceived(self, compareWithPrevious=False):
        """Log bytes received, optionally comparing with previous measurement."""
        if not self.bytesReceived:
            print("  No data received")
            return
        
        print("  Bytes received:")
        for deviceId, byteCount in self.bytesReceived.items():
            output = f"    {deviceId}: {byteCount} bytes"
            
            if compareWithPrevious and hasattr(self, 'previousBytes'):
                prevCount = self.previousBytes.get(deviceId, 0)
                diff = byteCount - prevCount
                diffSymbol = "↑" if diff > 0 else "↓" if diff < 0 else "="
                output += f" ({diffSymbol} {abs(diff)})"
            
            print(output)
    

    async def _monitorWithTopology(self, seconds: int):
        """Monitor data and topology changes for specified duration."""
        self.monitoring = True
        self.bytesReceived.clear()
        
        endTime = time.time() + seconds
        lastCheck = 0
        
        print(f"Monitoring recovery ({seconds}s)...")
        
        while time.time() < endTime:
            now = time.time()
            
            # Check every 5 seconds
            if now - lastCheck >= 5:
                elapsed = int(now - (endTime - seconds))
                
                # Check topology
                request = json.dumps({'command': 'getTopology'})
                response = await self._sendRequest(request)
                
                if response.get('event') == 'topology':
                    devices = response.get('devices', [])
                    newTopology = {d['deviceId']: d['kind'] for d in devices}
                    
                    if newTopology != self.currentTopology:
                        deviceStrs = [f"{d['deviceId']} ({d['kind']})" for d in devices]
                        print(f"  [{elapsed}s] Topology changed: {', '.join(deviceStrs) if deviceStrs else 'none'}")
                        self.currentTopology = newTopology
                    else:
                        print(f"  [{elapsed}s] Topology: {len(devices)} devices")
                
                # Log bytes
                if self.bytesReceived:
                    for deviceId, byteCount in self.bytesReceived.items():
                        print(f"           {deviceId}: {byteCount} bytes")
                    self.bytesReceived.clear()
                else:
                    print(f"           No data")
                
                lastCheck = now
            
            await asyncio.sleep(0.5)
        
        self.monitoring = False


if __name__ == '__main__':
    # Check if hardware-config.json exists
    hardwareConfigPath = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'hardware-config.json')
    hasConfig = os.path.exists(hardwareConfigPath)
    
    if not hasConfig:
        print('\n' + '='*70)
        print('WARNING: hardware-config.json NOT FOUND')
        print('='*70)
        print(f'Expected location: {hardwareConfigPath}')
        print('Testing with DEFAULT configuration (digital oscope, channel 0)')
        print('This tests fallback behavior when config is missing.')
        print('='*70 + '\n')
    
    asyncio.run(RegressionTest().run())
    
    print('\n\n' + '='*70)
    print('MANUAL VALIDATION STEPS')
    print('='*70)
    print('1. Unplug and replug ONE device → verify auto-recovery')
    print('2. Unplug and replug ALL devices together → verify recovery')
    if hasConfig:
        print('3. Verify hardware-config.json oscope type/triggerChannel are correct')
    else:
        print('3. CREATE hardware-config.json with proper oscope configuration')
        print(f'   Location: {hardwareConfigPath}')
        print('   Format: {"hardware": {"oscope": {"type": "digital", "triggerChannel": 0}}}')
    print('='*70 + '\n')
