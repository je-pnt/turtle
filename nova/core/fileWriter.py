"""
NOVA Real-time File Writer

Triggered on ingest to write events to files via drivers.
Emits DriverBinding metadata on first write to a stream.

Architecture:
- FileWriter runs ONLY on ingest (never on query/stream/replay)
- DB is primary truth; files are derived output
- DriverBinding metadata emitted on first write per stream
"""

from pathlib import Path
from typing import Dict, Any, Optional, Callable
from queue import Queue, Empty
import threading

from nova.core.events import Lane
from nova.core.drivers.registry import DriverRegistry
from sdk.logging import getLogger


class FileWriter:
    """
    Real-time file writer.
    
    Triggered on ingest, writes events via drivers.
    Emits DriverBinding metadata on first write to each stream.
    """
    
    def __init__(self, outputDir: Path, registry: Optional[DriverRegistry] = None,
                 emitBinding: Optional[Callable] = None):
        """
        Args:
            outputDir: Base directory for file output
            registry: Driver registry (created if not provided)
            emitBinding: Callback to emit DriverBinding metadata event
        """
        self.outputDir = outputDir
        self.log = getLogger()
        self.emitBinding = emitBinding
        
        if registry:
            self.registry = registry
        else:
            self.registry = DriverRegistry(outputDir)
            self.registry.loadBuiltinDrivers()
        
        self._writeQueue: Queue = Queue()
        self._running = False
        self._writerThread: Optional[threading.Thread] = None
        
        # Track which streams have had DriverBinding emitted
        # Key: (systemId, containerId, uniqueId, lane, messageType)
        self._boundStreams: set = set()
        
        self._eventsWritten = 0
        self._writeErrors = 0
    
    def start(self):
        """Start the file writer background thread."""
        if self._running:
            return
        
        self._running = True
        self._writerThread = threading.Thread(target=self._writerLoop, daemon=True)
        self._writerThread.start()
        self.log.info(f"[FileWriter] Started. Output dir: {self.outputDir}")
    
    def stop(self):
        """Stop the file writer."""
        if not self._running:
            return
        
        self._running = False
        self._writeQueue.put(None)
        
        if self._writerThread:
            self._writerThread.join(timeout=5.0)
        
        self.registry.finalize()
        self.log.info(f"[FileWriter] Stopped. Written: {self._eventsWritten}, Errors: {self._writeErrors}")
    
    def write(self, event: Dict[str, Any], canonicalTruthTime: str):
        """Queue event for writing. Non-blocking."""
        if not self._running:
            return
        self._writeQueue.put((event, canonicalTruthTime))
    
    def _writerLoop(self):
        """Background writer loop."""
        while self._running:
            try:
                item = self._writeQueue.get(timeout=1.0)
                if item is None:
                    break
                event, canonicalTruthTime = item
                self._processWrite(event, canonicalTruthTime)
            except Empty:
                continue
    
    def _processWrite(self, event: Dict[str, Any], canonicalTruthTime: str):
        """Process a single event write."""
        laneStr = event.get('lane')
        if not laneStr:
            return
        
        lane = Lane(laneStr)
        messageType = event.get('messageType')
        
        driver = self.registry.selectDriver(lane, messageType)
        if not driver:
            return
        
        # Emit DriverBinding on first write to this stream
        streamKey = (
            event['systemId'],
            event['containerId'],
            event['uniqueId'],
            laneStr,
            messageType
        )
        
        if streamKey not in self._boundStreams:
            self._boundStreams.add(streamKey)
            self._emitDriverBinding(event, driver, canonicalTruthTime)
        
        # Write via driver
        filePath = driver.write(event, canonicalTruthTime)
        if filePath:
            self._eventsWritten += 1
    
    def _emitDriverBinding(self, event: Dict[str, Any], driver, canonicalTruthTime: str):
        """Emit DriverBinding metadata event."""
        if not self.emitBinding:
            return
        
        caps = driver.capabilities
        # Use pipe-delimited targetId format matching entityIdentityKey pattern
        targetId = f"{event['systemId']}|{event['containerId']}|{event['uniqueId']}"
        binding = {
            'targetId': targetId,
            'targetLane': event['lane'],
            'targetMessageType': event.get('messageType'),
            'driverId': caps.driverId,
            'driverVersion': caps.version,
            'outputFilename': caps.outputFilename,
            'effectiveTime': canonicalTruthTime
        }
        
        self.emitBinding(binding)
