
"""
The hardwareService app is responsible for managing hardware device plugins, orchestrating device discovery, lifecycle, and data flow within the system. It provides a unified interface for hardware integration, enabling dynamic detection and management of devices through a plugin architecture. For integration, consumer apps interact with hardwareService over the transport module for device discovery and data streaming. The implementation is designed for resilience, automatically handling device restarts and topology changes.

The HardwareService class acts as the central point for hardware orchestration, supporting integration with transport layers and IO modules. It abstracts device management, making hardware access reliable and extensible for consumer applications. This file (`hardwareService.py`) implements the main HardwareService class, which:
- Initializes and manages hardware plugins
- Discovers devices by scanning available ports
- Creates and tracks device instances
- Manages per-device data flows and periodic rescans
- Coordinates configuration, logging, and device restarts
- Provides a command interface for restarts, topology requests, configuration management, and other device operations

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio, base64, importlib, json, os, serial.tools.list_ports, time
from typing import Dict, List
from datetime import datetime, timezone
from .restartManager import restartUsb
from sdk.logging import getLogger


class HardwareService:

    # --- Initialization & Setup ---
    def __init__(self, config: dict, transport, ioLayer, configManager=None, subjectBuilder=None, novaAdapter=None) -> None:
        """Initialize HardwareService with config, transport, ioLayer, configManager, subjectBuilder, novaAdapter."""

        # Auto-detect logger hierarchy (computed ONCE): 'hardwareService.hardwareService.HardwareService'
        self.log = getLogger()
        
        # Setup config
        self.config = config
        self.containerId = config.get('containerId', 'Unknown')
        self.scanIntervalSeconds: int = config.get('scanIntervalSeconds', 5)
        self.deviceTimeoutSeconds: int = config.get('deviceTimeoutSeconds', 15)

        # Setup data structures
        self.devices: Dict[str, Dict] = {}
        self.plugins: list = []
        self.running: bool = False
        self.stopping: bool = False  # Prevent device removal during shutdown
        self.startTime = datetime.now(timezone.utc)

        # Apply initialized sub systems
        self.transport = transport
        self.ioLayer = ioLayer
        self.configManager = configManager
        self.subjectBuilder = subjectBuilder
        self.novaAdapter = novaAdapter


    def loadPlugins(self, hardwareConfig: dict, config: dict) -> None:
        """Register plugins based on hardwareConfig rules."""

        # Get plugins and oscope configuration
        hw = hardwareConfig.get('hardware', {})
        oscopeConfig = hw.get('oscope', {"type": "digital", "triggerChannel": 0})
        
        # Extract oscope configuration (dict format only)
        # Expected format: {"type": "analog", "triggerChannel": 1}
        oscopeKind = oscopeConfig.get('type', 'digital').lower()
        triggerChannel = oscopeConfig.get('triggerChannel', 0)

        def getPluginClasses():
            from . import plugins                                                                                    # Import from .plugins subpackage
            classes = []
            pluginsDir = os.path.dirname(plugins.__file__)
            for f in [x for x in os.listdir(pluginsDir) if x.endswith('.py') and not x.startswith('__')]:            # Iterate over .py files
                mod = importlib.import_module(f'.plugins.{f[:-3]}', package='sdk.hardwareService') 
                classes += [getattr(mod, attr) for attr in dir(mod) if isinstance(getattr(mod, attr), type)]         # Add list comprehension classes
            return classes
        
        # Iterate and register plugins -- only register one oscope! 
        for cls in getPluginClasses():
            try:
                kind = cls.getKind() if hasattr(cls, 'getKind') else None                                           # Call getKind as static method (no instance needed)
            except TypeError:
                kind = None
            
            if kind:
                kindLower = kind.lower()
                if kindLower.endswith('oscope') and kindLower.startswith(oscopeKind):                                # If oscope and oscope type matches config - add it! 
                    plugin = cls()
                    configureFunc = getattr(plugin, 'configure', lambda *_: None)
                    configureFunc(hardwareConfig, config, triggerChannel=triggerChannel)                             # Pass triggerChannel via hardwareConfig to plugin.configure()
                    self.plugins.append(plugin)
                elif not kindLower.endswith('oscope'):
                    plugin = cls(); getattr(plugin, 'configure', lambda *_: None)(hardwareConfig, config)
                    self.plugins.append(plugin)


    # --- Lifecycle Management ---
    async def start(self) -> None:
        """Start the hardware service and begin scanning."""

        # Set running flag and connect transport
        self.running = True
        await self.transport.connect(self.config['transport'])
        self.log.info('Transport connected', uri=self.config['transport'])
        
        # gRPC-type command handler for HardwareService that processes incoming control commands (topology, restart, config, etc.) and returns structured responses
        async def handleControl(subject: str, request: bytes) -> bytes:

            try:

                # Get message and command
                msg = json.loads(request.decode('utf-8'))
                command = msg.get('command')

                # Handle commands
                if command == 'getTopology':
                    response = {
                        "event": "topology",
                        "containerId": self.containerId,
                        "devices": [{'deviceId': deviceId, 'kind': entry['kind'], 'subject': entry.get('subject')} for deviceId, entry in self.devices.items()]
                    }  # Include data subject for direct subscription

                elif command == 'restart':
                    targets = msg.get('targets', 'all')
                    asyncio.create_task(self._handleRestartRequest(targets))
                    response = {"event": "restart", "status": "started", "targets": targets}
                
                elif command == 'applyConfig':
                    deviceId = msg.get('deviceId')
                    configBytes = msg.get('configBytes')
                    label = msg.get('label', '')
                    if not deviceId or configBytes is None:
                        response = {"error": "Missing deviceId or configBytes"}
                    else:
                        try:
                            if isinstance(configBytes, list):
                                configBytes = bytes(configBytes)                                                 # Convert byte array to bytes (direct pass-through to device)
                            elif isinstance(configBytes, str):
                                configBytes = configBytes.encode('utf-8')                                        # Also support string in case someone sends text
                            result = await self.configManager.applyConfig(deviceId, configBytes, label)
                            result['bytesPreview'] = str(configBytes[:60])  
                            response = {"event": "configApplied", **result}
                        except Exception as e:
                            response = {"error": f"Config error: {str(e)}"}

                elif command == 'getConfigHistory':
                    deviceId = msg.get('deviceId')
                    startTime = msg.get('startTime')      # optional ISO timestamp
                    if not deviceId:
                        response = {"error": "Missing deviceId"}
                    else:
                        result = self.configManager.getConfigHistory(deviceId, startTime)
                        response = {"event": "configHistory", **result}

                else:
                    response = {"error": "Unknown command", "command": command}

                # Return response generated by command handlers
                return json.dumps(response).encode('utf-8')
            
            except Exception as e:
                self.log.error('Error in REQ/REP handler', component='HardwareService', errorClass=type(e).__name__, errorMsg=str(e))
                return json.dumps({"error": str(e)}).encode('utf-8')
        

        # Register control handler on container-specific subject
        controlSubject = self.subjectBuilder.control()
        await self.transport.registerHandler(controlSubject, handleControl)
        self.log.info('Control handler registered', subject=controlSubject)
        
        # Register discovery handler for NATS transport (auto-detect scheme)
        if self.config['transport'].startswith('nats'):
            async def handleDiscovery(subject: str, payload: bytes):
                try:
                    self.log.info('Discovery request received', subject=subject)
                    topology = {"event": "topology", "containerId": self.containerId,
                               "devices": [{'deviceId': deviceId, 'kind': entry['kind'], 'subject': entry.get('subject')} for deviceId, entry in self.devices.items()]}
                    await self.transport.publish(self.subjectBuilder.topology(), json.dumps(topology).encode('utf-8'))
                    self.log.info('Topology published', subject=self.subjectBuilder.topology(), deviceCount=len(self.devices))
                except Exception as e:
                    self.log.error('Discovery error', errorClass=type(e).__name__, errorMsg=str(e))
            await self.transport.subscribe(self.subjectBuilder.discovery(), handleDiscovery)
            self.log.info('Discovery handler registered', subject=self.subjectBuilder.discovery())
        
        # Start scan loop initializing hardware discover, registry, data streaming...etc.
        asyncio.create_task(self._scanLoop())
        self.log.info('HardwareService started', state='READY')
        

    async def stop(self) -> None:
        """Stop service, close devices, disconnect transports."""
        self.running = False
        self.stopping = True  # Signal to device error handlers: don't modify self.devices
        for entry in list(self.devices.values()):
            try:
                await entry['device'].close()
            except Exception as e:
                self.log.error(f'ERROR stopping device: {e!r}', component='HardwareService')
        self.devices.clear()
        await self.transport.close()
        self.ioLayer.shutdown()
        self.log.info('HardwareService stopped', component='HardwareService', state='CLOSED')


    # --- Event Publishing Helper ---
    async def _publishEvent(self, event: dict) -> None:
        """Publish event to container-specific subject."""
        eventBytes = json.dumps(event).encode('utf-8')
        await self.transport.publish(self.subjectBuilder.events(), eventBytes)

    async def _publishTopology(self) -> None:
        """Publish current topology to events subject."""
        topology = {
            "event": "topology",
            "containerId": self.containerId,
            "devices": [{"deviceId": deviceId, "kind": entry["kind"], "subject": entry.get("subject")}
                       for deviceId, entry in self.devices.items()]
        }
        await self._publishEvent(topology)
        self.log.info('Topology published', deviceCount=len(self.devices), devices=list(self.devices.keys()))


    # --- Main Loop & Scanning ---
    async def _scanLoop(self) -> None:
        """Main loop for device management and hot-plug support: 1. scan, 2. resolve (starting devices as needed)."""
        try:
            candidates = await self._probeAll()
            await self._resolve(candidates)
        except Exception as e:
            self.log.error(f'ERROR in initial scan: {e!r}', component='HardwareService', errorClass=type(e).__name__, errorMsg=str(e))
        while self.running:
            try:
                # Skip scanning if restart is in progress (prevents port conflicts during USB reset)
                if not getattr(self, '_restartInProgress', False):
                    candidates = await self._probeAll()
                    # Wait for all ports to be fully released by OS before starting devices
                    if candidates:
                        await asyncio.sleep(0.3)
                    await self._resolve(candidates)
                else:
                    self.log.debug('Skipping scan - restart in progress', component='HardwareService')
            except Exception as e:
                self.log.error(f'ERROR in scan loop: {e!r}', component='HardwareService', errorClass=type(e).__name__, errorMsg=str(e))
            await asyncio.sleep(self.scanIntervalSeconds)
    

    async def _probeAll(self) -> List[Dict]:
        """Probe all available ports with plugins, return candidate devices."""
        candidates: List[Dict] = []

        # Get available ports
        allPorts = self.config.get('serialHints', []) if self.config.get('serialHints', []) else [p.device for p in serial.tools.list_ports.comports()]
        ownedPorts = set()
        for entry in self.devices.values():
            ownedPorts.update(entry.get('ports', []))
        availablePorts = [p for p in allPorts if p not in ownedPorts]

        # Test all plugins (with available ports) -adding to candidates if successful
        for plugin in self.plugins:
            if hasattr(plugin, 'test'):
                results = await plugin.test(availablePorts, self.ioLayer)
                if results:
                    candidates.extend(results)
        return candidates
    

    async def _resolve(self, candidates: List[Dict]) -> None:
        """Resolve candidates, attach new devices, update existing ones."""
        candidateMap: Dict[str, Dict] = {}

        # Register candidates, adding additional ports if needed
        for c in candidates:
            deviceId = c['deviceId']
            if deviceId not in candidateMap:
                candidateMap[deviceId] = {'kind': c['kind'], 'ports': [], 'meta': c.get('meta', {})}
            if 'port' in c:
                candidateMap[deviceId]['ports'].append(c['port'])

        # Update lastSeen for ALL existing devices (they're still connected and running)
        now = time.time()
        for deviceId, entry in self.devices.items():
            entry['lastSeen'] = now
        
        # Start new devices, attach additional ports if needed
        for deviceId, info in candidateMap.items():
            if deviceId not in self.devices:
                await self._startDevice(deviceId, info['kind'], info['ports'], info['meta'])
            else:
                entry = self.devices[deviceId]
                if newPorts := set(info['ports']) - set(entry['ports']):
                    for port in newPorts:
                        try:
                            if hasattr(entry['device'], 'attachPort'):
                                await entry['device'].attachPort(port)
                                self.log.info(f'Attached port {port} to {entry["device"].deviceId}', component='HardwareService', deviceId=deviceId, port=port)
                        except Exception as e:
                            self.log.error(f'ERROR attaching port: {e!r}', component='HardwareService', deviceId=deviceId, port=port, errorClass=type(e).__name__, errorMsg=str(e))
                    entry['ports'].extend(newPorts)
        
        # Remove stale devices (not seen in recent scan)
        now = time.time()
        staleDevices = []
        for deviceId, entry in list(self.devices.items()):
            if now - entry.get('lastSeen', now) > self.deviceTimeoutSeconds:
                staleDevices.append(deviceId)
        
        if staleDevices:
            topologyChanged = False
            for deviceId in staleDevices:
                try:
                    entry = self.devices[deviceId]
                    device = entry['device']
                    kind = entry.get('kind', 'unknown')
                    ports = entry.get('ports', [])
                    
                    self.log.warning(f'Device {deviceId} not seen for {self.deviceTimeoutSeconds}s, removing', 
                                   component='HardwareService', deviceId=deviceId, kind=kind, ports=ports)
                    
                    # Close device connection
                    try:
                        await device.close()
                        self.log.info(f'Closed stale device connection', deviceId=deviceId)
                    except Exception as closeErr:
                        self.log.warning(f'Failed to close stale device', deviceId=deviceId, error=str(closeErr))
                    
                    # Remove from topology
                    del self.devices[deviceId]
                    topologyChanged = True
                    self.log.info(f'Removed stale device from topology', deviceId=deviceId, kind=kind, ports=ports)
                    
                except Exception as e:
                    self.log.error(f'Error removing stale device {deviceId}', errorClass=type(e).__name__, errorMsg=str(e))
            
            # Publish topology update if any devices were removed
            if topologyChanged:
                try:
                    await self._publishTopology()
                except Exception as topErr:
                    self.log.error('Failed to publish topology after stale device removal', errorMsg=str(topErr))


    # --- Device Management ---
    async def _startDevice(self, deviceId: str, kind: str, ports: List[str], meta: dict) -> None:
        """Create, open, and start a device."""
        
        # Get plugin
        plugin = next((p for p in self.plugins if getattr(p, "getKind", lambda: None)() == kind), None)
        if not plugin:
            self.log.error(f'ERROR: No plugin for kind={kind}', component='HardwareService', kind=kind)
            return
        
        try:

            # Create device (pass transport, subjectBuilder, and novaAdapter)
            device = await plugin.createDevice(deviceId, ports, meta, self.ioLayer, transport=self.transport, 
                                              subjectBuilder=self.subjectBuilder, novaAdapter=self.novaAdapter)
            
            if not device:
                self.log.error(f'ERROR: Failed to create device {deviceId}', component='HardwareService', deviceId=deviceId, kind=kind)
                return
            
            # Open and handshake - catch serial errors
            try:
                await device.open()
            except Exception as openErr:
                # Check if it's a permission/access error (port still locked from probe)
                if 'PermissionError' in str(type(openErr).__name__) or 'Access is denied' in str(openErr):
                    self.log.warning(f'Port locked for {deviceId}, waiting and retrying...', component='HardwareService', 
                                   deviceId=deviceId, ports=str(ports), errorMsg=str(openErr))
                    # Wait for port release and retry once
                    await asyncio.sleep(1.0)
                    try:
                        await device.open()
                        self.log.info(f'Successfully opened {deviceId} after retry', deviceId=deviceId)
                    except Exception as retryErr:
                        self.log.error(f'ERROR opening device {deviceId} after retry: {retryErr!r}', component='HardwareService', 
                                     deviceId=deviceId, kind=kind, ports=str(ports), errorClass=type(retryErr).__name__, errorMsg=str(retryErr))
                        return
                else:
                    self.log.error(f'ERROR opening device {deviceId} on {ports}: {openErr!r}', component='HardwareService', 
                                 deviceId=deviceId, kind=kind, ports=str(ports), errorClass=type(openErr).__name__, errorMsg=str(openErr))
                    return
            deviceConfig = self.config.get('deviceConfig', {}).get(deviceId, {})

            # Determine dataType for subject - use SubjectBuilder's mapping for consistency
            dataTypeMap = {
                'sbf': 'telemetry',
                'ubx': 'telemetry',
                'x5': 'telemetry',
                'm9': 'telemetry',
                'digitalOscope': 'samples',
                'analogOscope': 'samples'
            }
            dataType = dataTypeMap.get(kind, kind)  # fallback to kind if not in map
            
            # Build subject immediately so it's available in topology
            subject = self.subjectBuilder.data(deviceId, kind, dataType)
            
            # Create device entry with subject populated immediately
            deviceEntry = {'device': device, 'kind': kind, 'ports': ports.copy(), 'lastSeen': time.time(), 'subject': subject}
            self.devices[deviceId] = deviceEntry

            # Create and run read loop (device emits data directly via self.emit())
            async def runReadLoop():
                try:
                    await device.readLoop()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.log.error(f'Device {device.deviceId} error - removing from topology', event='deviceError', deviceId=device.deviceId, errorClass=type(e).__name__, errorMsg=str(e))
                    
                    # Clean shutdown: close device, then remove from topology
                    try:
                        await device.close()
                    except Exception as closeErr:
                        self.log.warning(f'Device close failed during error cleanup', deviceId=device.deviceId, closeError=str(closeErr))  # Device already dead
                    
                    # Remove from topology so it can be rediscovered on next scan
                    # UNLESS we're shutting down (stop() will clear devices dict)
                    if not self.stopping and deviceId in self.devices:
                        deviceKind = self.devices[deviceId].get('kind')
                        devicePorts = self.devices[deviceId].get('ports', [])
                        del self.devices[deviceId]
                        self.log.info(f'Device removed from topology after error', deviceId=deviceId, kind=deviceKind, ports=devicePorts)
                        
                        # Publish topology update so frontend knows device is gone
                        try:
                            await self._publishTopology()
                        except Exception as topErr:
                            self.log.error('Failed to publish topology after device removal', errorMsg=str(topErr))
                        
                        # Reset oscope plugin discovery flag if this was an oscope device
                        if deviceKind and 'oscope' in deviceKind.lower():
                            plugin = next((p for p in self.plugins if getattr(p, "getKind", lambda: None)() == deviceKind), None)
                            if plugin and hasattr(plugin, 'resetDiscovery'):
                                plugin.resetDiscovery()
                                self.log.info(f'Reset discovery flag for {deviceKind}', deviceId=deviceId, kind=deviceKind)
                        
                        # IMPORTANT: Do NOT trigger full USB reset here - let scan loop handle rediscovery
                        # This isolates failures and prevents cascade restarts of working devices
            readTask = asyncio.create_task(runReadLoop())

            # Add readTask to device entry
            deviceEntry['readTask'] = readTask
            
            # Log started device
            self.log.info(f'Started device {deviceId} ({kind}) on {ports}', component='HardwareService', deviceId=deviceId, kind=kind, ports=str(ports))
            
            # Publish topology update so frontend knows new device is available
            await self._publishTopology()
            
        except Exception as e:
            self.log.error(f'ERROR starting device {deviceId}: {e!r}', component='HardwareService', deviceId=deviceId, kind=kind, errorClass=type(e).__name__, errorMsg=str(e))


    # --- Flow Management ---
    async def stopDeviceFlows(self, deviceId: str) -> int:
        """Stop device flows and remove from topology."""
        if deviceId not in self.devices:
            return 0
        try:
            device = self.devices[deviceId]['device']
            await device.close()
            del self.devices[deviceId]
            
            # Publish topology update
            await self._publishTopology()
            
            return 1
        except Exception as e:
            self.log.error('Error stopping flows', deviceId=deviceId, errorMsg=str(e))
            return 0


    async def _restartDevice(self, deviceId: str) -> None:
        """Restart single device - software first, remove from topology."""
        if deviceId not in self.devices:
            return
        
        # Get device and metadata
        device = self.devices[deviceId]['device']
        kind = self.devices[deviceId].get('kind')
        readTask = self.devices[deviceId].get('readTask')
                
        # Cancel the read loop task first
        if readTask and not readTask.done():
            readTask.cancel()
            try:
                await readTask
            except asyncio.CancelledError:
                pass

        # Send software reset command first (if supported)
        try:
            if hasattr(device, 'softwareReset'):
                await device.softwareReset()
                self.log.info(f'Software reset sent to device', deviceId=deviceId)
        except Exception as e:
            self.log.warning(f'Software reset failed for {deviceId}', deviceId=deviceId, errorMsg=str(e))
        
        # Close the device to release serial port (DLL/oscope software shutdown allows a cleaner restart)
        try:
            if hasattr(device, 'close'):
                await device.close()
                self.log.info(f'Device closed for restart', deviceId=deviceId)
                # Critical: Give OS time to fully release serial ports
                await asyncio.sleep(0.5)
            else:
                self.log.warning(f'Device {deviceId} has no close method', deviceId=deviceId)
        except Exception as e:
            self.log.error(f'Software shutdown failed for {deviceId}', deviceId=deviceId, errorMsg=str(e))
            # Still wait to allow any partial port cleanup
            await asyncio.sleep(0.5)
        
        # Remove from topology immediately
        try:
            del self.devices[deviceId]
            self.log.info(f'Device removed from topology', deviceId=deviceId)
            
            # Reset oscope plugin discovery flag if this was an oscope device
            if kind and 'oscope' in kind.lower():
                plugin = next((p for p in self.plugins if getattr(p, "getKind", lambda: None)() == kind), None)
                if plugin and hasattr(plugin, 'resetDiscovery'):
                    plugin.resetDiscovery()
                    self.log.info(f'Reset discovery flag for {kind}', deviceId=deviceId, kind=kind)
        except Exception as e:
            self.log.error(f'Failed to remove device {deviceId} from topology: {e}', deviceId=deviceId, errorMsg=str(e))


    # --- Restart Command Handler ---
    async def _handleRestartRequest(self, targets) -> None:
        """Handle restart request - software first, then USB reset, then rescan.

        NOTE: a usb reset is only performed if 'all' devices are targeted.
        IF all: usb reset is safest - if not, a reset will still result in all devices being restarted.
        Thus: consumer apps need to call 'all' to ensure a usb reset is performed!"""
        
        # Restart command received
        startTime = int(time.time() * 1000)
        event = {"event": "restart.start", "targets": targets, "ts": startTime}
        await self._publishEvent(event)
        
        # Resolve target device set
        targetDeviceIds = list(self.devices.keys()) if targets == 'all' else targets.get('deviceIds', [])
        
        # Restarting devices
        restarted = 0
        errors = []
        
        # Process each device - prioritize data flow recovery
        for deviceId in targetDeviceIds:
            if deviceId not in self.devices:
                continue
            
            # Software-first restart for individual device
            await self._restartDevice(deviceId)
            restarted += 1
        
        # Global USB reset as final step if any devices were restarted
        if targets == 'all': 

            # Set flag to prevent scan loop from probing during USB reset
            self._restartInProgress = True
            
            # CRITICAL: Clear topology BEFORE USB reset so clients stop expecting devices
            self.log.info('Clearing topology before USB reset', deviceCount=len(self.devices))
            for deviceId in list(self.devices.keys()):
                # Remove from registry
                del self.devices[deviceId]
            
            # Publish empty topology to inform clients
            await self._publishTopology()
            
            # Additional wait to ensure all ports are fully released by OS
            # (Each device had 0.5s delay, but need extra time for OS cleanup)
            await asyncio.sleep(1.5)
            
            # Performing USB subsystem reset
            self.log.info('Starting USB reset', component='HardwareService', deviceCount=restarted)
            
            # Perform USB reset
            usbResult = await restartUsb("global")
            if not usbResult['ok']:
                self.log.error('USB reset failed', component='HardwareService', error=usbResult['error'])
                errors.append({"deviceId": "usb_subsystem", "msg": usbResult['error']})
            else:
                self.log.info('USB reset completed', component='HardwareService')
                # Extended wait for USB re-enumeration and port release
                await asyncio.sleep(5)
            
            # Clear flag to resume scanning
            self._restartInProgress = False
        
        # Publish restart done event
        endTime = int(time.time() * 1000)
        event = {"event": "restart.done", "ok": len(errors) == 0, "restarted": restarted, "errors": errors, "ts": endTime}
        await self._publishEvent(event)
        
        # Restart completed
        self.log.info('Restart completed', restarted=restarted, errorCount=len(errors))   