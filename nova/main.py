"""
NOVA 2.0 main entry point.

Spawns Core and Server as cooperating subprocesses with IPC.

Architecture:
- Core process: owns DB, ingest, query, streaming
- Server process: owns WebSocket edge, auth, routing
- IPC: multiprocessing.Queue for request/response

Usage:
    python nova/main.py [--config path/to/config.json]

Property of Uncompromising Sensors LLC.
"""

import asyncio
import argparse
import signal
import sys
import orjson
from multiprocessing import Process, Queue
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from nova.core.database import Database
from nova.core.ingest import Ingest
from nova.core.transportManager import TransportManager
from nova.core.ipc import CoreIPCHandler
from nova.core.fileWriter import FileWriter
from nova.core.uiState import UiStateManager
from nova.core.manifests import ManifestRegistry, setRegistry
from nova.server.server import NovaServer
from sdk.logging import getLogger, configureLogging
from sdk.transport import createTransport


def loadConfig(configPath: str) -> dict:
    """Load configuration from JSON file"""
    with open(configPath, 'r') as f:
        return orjson.loads(f.read())


def runCoreProcess(configPath: str, requestQueue: Queue, responseQueue: Queue):
    """
    Core process entry point.
    
    Owns:
    - Truth database
    - Ingest pipeline
    - Transport manager (subscriber)
    - IPC handler (query, stream, command)
    """
    # Configure logging
    configureLogging()
    log = getLogger()
    log.info("[Core] Process starting...")
    
    # Load config
    config = loadConfig(configPath)
    
    # Initialize Core components
    dbPath = config.get('dbPath', 'nova.db')
    
    # Ensure data directory exists
    dbPathObj = Path(dbPath)
    dbPathObj.parent.mkdir(parents=True, exist_ok=True)
    
    database = Database(dbPath)
    
    # Get scopeId from config
    scopeId = config.get('scopeId', 'local')
    
    # Initialize ManifestRegistry
    manifestRegistry = ManifestRegistry(database=database, scopeId=scopeId)
    setRegistry(manifestRegistry)
    manifestRegistry.loadBuiltinManifests()
    log.info("[Core] ManifestRegistry initialized")
    
    # Emit ManifestPublished events for all loaded manifests (Phase 10)
    from nova.core.events import MetadataEvent
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc).isoformat()
    allManifests = manifestRegistry.getAllManifests()
    for manifest in allManifests:
        manifestVersion = manifest.manifestVersion
        publishedEvent = MetadataEvent.create(
            scopeId=scopeId,
            sourceTruthTime=now,
            messageType="ManifestPublished",
            effectiveTime=now,
            payload={'manifestId': manifest.manifestId, 'manifestVersion': manifestVersion},
            systemId="nova",
            containerId="core",
            uniqueId=f"manifest-{manifest.manifestId}"
        )
        database.insertEvent(publishedEvent, now)
    log.info(f"[Core] Emitted ManifestPublished for {len(allManifests)} manifests")
    
    # Initialize UiStateManager (Phase 10: config-driven intervals)
    uiConfig = config.get('ui', {})
    uiStateManager = UiStateManager(
        database, 
        manifestRegistry,
        checkpointIntervalSeconds=uiConfig.get('checkpointIntervalSeconds', 500),
        historyTimeoutSeconds=uiConfig.get('historyTimeoutSeconds', 120)
    )
    log.info("[Core] UiStateManager initialized")
    
    # IPC handler (creates StreamingManager)
    ipcHandler = CoreIPCHandler(database, requestQueue, responseQueue, config)
    
    # DriverBinding emitter - inserts binding as Metadata event
    def emitDriverBinding(binding: dict):
        from nova.core.events import MetadataEvent
        from datetime import datetime, timezone
        
        now = datetime.now(timezone.utc).isoformat()
        bindingEvent = MetadataEvent.create(
            scopeId=config.get('scopeId', 'local'),
            sourceTruthTime=now,
            messageType="DriverBinding",
            effectiveTime=now,
            payload=binding,
            systemId="nova",
            containerId="core",
            uniqueId="driver-registry"
        )
        database.insertEvent(bindingEvent, now)
    
    # FileWriter with DriverBinding emission
    dataDir = Path(config.get('dataDir', './nova/data/files'))
    fileWriter = FileWriter(dataDir, emitBinding=emitDriverBinding)
    fileWriter.start()
    
    # Ingest (with StreamingManager + FileWriter + UiStateManager)
    ingest = Ingest(database, verifyEventId=False, streamingManager=ipcHandler.streamingManager, fileWriter=fileWriter, uiStateManager=uiStateManager)
    
    # Run Core event loop
    async def runCore():
        transportManager = None
        checkpointTask = None
        
        # Periodic checkpoint task - runs every 60 seconds
        async def periodicCheckpoint():
            while True:
                await asyncio.sleep(60)
                try:
                    blocked, logPages, checkpointed = database.checkpoint('PASSIVE')
                    if logPages > 0:
                        log.debug(f"[Core] WAL checkpoint: {checkpointed}/{logPages} pages")
                except Exception as e:
                    log.warning(f"[Core] Checkpoint error: {e}")
        
        try:
            # Start periodic checkpoint task
            checkpointTask = asyncio.create_task(periodicCheckpoint())
            
            # Initialize transport (if configured)
            transportConfig = config.get('transport')
            if transportConfig:
                transportUri = transportConfig.get('uri')
                transport = createTransport(transportUri)
                await transport.connect(transportUri)
                scopeId = config.get('scopeId')
                transportManager = TransportManager(ingest, transport, scopeId)
                
                # Connect CommandManager to transport (Phase 5)
                ipcHandler.setTransportManager(transportManager)
                
                # Start transport
                await transportManager.start()
            
            # Start IPC handler
            await ipcHandler.start()
        
        except KeyboardInterrupt:
            log.info("[Core] Shutdown signal received")
        except Exception as e:
            log.error(f"[Core] Fatal error: {e}", exc_info=True)
        finally:
            # Cancel checkpoint task
            if checkpointTask:
                checkpointTask.cancel()
                try:
                    await checkpointTask
                except asyncio.CancelledError:
                    pass
            
            # Cleanup
            await ipcHandler.stop()
            if transportManager:
                await transportManager.stop()
            
            # Stop FileWriter
            fileWriter.stop()
            
            # Final checkpoint and close database
            database.close()
            
            log.info("[Core] Process stopped")
    
    asyncio.run(runCore())


def runServerProcess(configPath: str, requestQueue: Queue, responseQueue: Queue):
    """
    Server process entry point.
    
    Owns:
    - WebSocket edge
    - Auth
    - IPC client (forwards requests to Core)
    """
    # Configure logging
    configureLogging()
    log = getLogger()
    log.info("[Server] Process starting...")
    
    # Load config
    config = loadConfig(configPath)
    serverConfig = config.get('server', {})
    
    # Initialize Server
    server = NovaServer(serverConfig, requestQueue, responseQueue)
    
    # Run Server event loop
    async def runServer():
        try:
            await server.start()
            
            # Keep running
            while True:
                await asyncio.sleep(1)
        
        except KeyboardInterrupt:
            log.info("[Server] Shutdown signal received")
        except Exception as e:
            log.error(f"[Server] Fatal error: {e}", exc_info=True)
        finally:
            await server.stop()
            log.info("[Server] Process stopped")
    
    asyncio.run(runServer())


def main():
    """Main entry point - spawns Core and Server subprocesses"""
    parser = argparse.ArgumentParser(description='NOVA 2.0 - Timeline Truth System')
    parser.add_argument('--config', default='nova/config.json', help='Path to config file')
    args = parser.parse_args()
    
    # Configure logging
    configureLogging()
    log = getLogger()
    log.info("=" * 60)
    log.info("NOVA 2.0 - Timeline Truth System")
    log.info("=" * 60)
    log.info(f"Config: {args.config}")
    
    # Verify config exists
    configPath = Path(args.config)
    if not configPath.exists():
        log.error(f"Config file not found: {args.config}")
        sys.exit(1)
    
    # Create IPC queues
    requestQueue = Queue()  # Server → Core requests
    responseQueue = Queue()  # Core → Server responses
    
    # Spawn subprocesses
    coreProcess = Process(
        target=runCoreProcess,
        args=(str(configPath), requestQueue, responseQueue),
        name='NovaCore'
    )
    
    serverProcess = Process(
        target=runServerProcess,
        args=(str(configPath), requestQueue, responseQueue),
        name='NovaServer'
    )
    
    try:
        # Start processes
        log.info("[Main] Starting Core process...")
        coreProcess.start()
        
        log.info("[Main] Starting Server process...")
        serverProcess.start()
        
        log.info("[Main] NOVA running (Ctrl+C to stop)")
        
        # Wait for processes
        coreProcess.join()
        serverProcess.join()
    
    except KeyboardInterrupt:
        log.info("[Main] Shutdown signal received")
    
    finally:
        # Terminate processes
        if coreProcess.is_alive():
            log.info("[Main] Terminating Core process...")
            coreProcess.terminate()
            coreProcess.join(timeout=5)
        
        if serverProcess.is_alive():
            log.info("[Main] Terminating Server process...")
            serverProcess.terminate()
            serverProcess.join(timeout=5)
        
        log.info("[Main] NOVA stopped")


if __name__ == '__main__':
    main()
