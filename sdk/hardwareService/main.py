"""
Main script: Entry point for hardware service startup.

- Loads config.json (hardwareService config) and hardware-config.json (shared with consumer apps)
- Selects and instantiates transport and I/O layer
- Instantiates HardwareService (plugin/device management)
- Starts and stops the service process

Property of Uncompromising Sensors LLC.
"""

# Imports
import asyncio, json, os
from sdk.transport import createTransport
from sdk.hardware_config_loader import load_hardware_config

# Local imports
from .hardwareService import HardwareService
from .ioLayer import IoLayer
from .novaAdapter import NovaAdapter
from sdk.logging import getLogger
from datetime import datetime, timezone
from .configManager import ConfigManager
from .subjects import SubjectBuilder

# HardwareServiceApp class
class HardwareServiceApp:
    def __init__(self, configPath=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json'),
                 hardwareConfigPath=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hardware-config.json')):
        
        # Set attributes
        self.configPath = configPath 
        self.hardwareConfigPath = hardwareConfigPath

        # Initialize logger (auto-detects: 'hardwareService.main.HardwareServiceApp')
        self.log = getLogger()
        self.config = self._loadConfig()
        
        # Fix transport path for platform
        transport_url = self.config['transport']
        if os.name == 'posix' and 'C:\\' in transport_url:
            # Convert Windows path to Unix for Linux/Mac
            transport_url = transport_url.replace('C:\\tmp\\', '/tmp/')
        elif os.name == 'nt' and transport_url.startswith('nng+ipc:///tmp/'):
            # Convert Unix path to Windows
            transport_url = transport_url.replace('/tmp/', 'C:\\tmp\\')
        self.config['transport'] = transport_url
        
        # Create local transport (NNG IPC for local consumers like svs)
        self.transport = createTransport(self.config['transport'])
        self.transport.setLogger(self.log)
        
        # Create NOVA adapter if novaTransport is configured
        self.novaAdapter = None
        self.novaTransport = None
        if 'novaTransport' in self.config and 'scopeId' in self.config:
            self.log.info('Creating NOVA adapter', scopeId=self.config['scopeId'], novaTransport=self.config['novaTransport'])
            self.novaTransport = createTransport(self.config['novaTransport'])
            self.novaTransport.setLogger(self.log)
            # NovaAdapter will get hardwareService reference after service is created
        
        self.ioLayer = IoLayer()
        
        # Get container ID from config (defaults to hostname or 'unknown')
        containerId = self.config.get('containerId', os.environ.get('HOSTNAME', 'unknown'))
        subjectBuilder = SubjectBuilder('hardwareService', containerId=containerId)

        self.service = HardwareService(self.config, self.transport, self.ioLayer, configManager=None, 
                                      subjectBuilder=subjectBuilder, novaAdapter=None)    # novaAdapter will be set after creation
        
        # Now create NovaAdapter with reference to service (for command dispatch)
        if self.novaTransport:
            self.novaAdapter = NovaAdapter(self.config, self.novaTransport, hardwareService=self.service)
            self.service.novaAdapter = self.novaAdapter  # Inject novaAdapter into service
        
        self.configManager = ConfigManager(self.ioLayer, self.service.devices, datetime.now(timezone.utc))                              # Now create ConfigManager with reference to service's devices dict
        self.service.configManager = self.configManager                                                                                 # Inject configManager back into service
        
        # Load shared hardware config (with fallback + flush)
        self.hardwareConfig, self.hardwareConfigVersion, self.hardwareConfigFallback = self._loadHardwareConfig()


    def _loadConfig(self):
        try:
            with open(self.configPath, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.log.error('Failed to load config', error=str(e), component='fileLoader')
            return {'scanIntervalSeconds': 5, 'topologyIntervalSeconds': 10, 'deviceTimeoutSeconds' : 15,'serialHints': [],'deviceConfig': {}}


    def _flushHardwareState(self):
        if hasattr(self, 'service') and self.service:
            self.log.warning('Clearing hardwareService device/plugin state due to hardware config error',
                            component='HardwareConfig')
            self.service.devices.clear()
            self.service.plugins.clear()

    def _loadHardwareConfig(self):
        return load_hardware_config(self.hardwareConfigPath, self.log, flush_on_error=self._flushHardwareState)


    async def run(self):
        # Connect and start NOVA adapter if present
        if self.novaAdapter:
            await self.novaTransport.connect(self.config['novaTransport'])
            await self.novaAdapter.start()
        
        self.service.loadPlugins(self.hardwareConfig, self.config)
        await self.service.start()
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            await self.service.stop()
            if self.novaAdapter:
                await self.novaAdapter.stop()


if __name__ == '__main__':
    app = HardwareServiceApp(hardwareConfigPath=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hardware-config.json'))
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass