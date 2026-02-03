"""
NOVA Export Execution

Bounded read + driver pipeline for export generation.
Uses same driver codepath as real-time fileWriter (parity by design).

Architecture Invariants (nova architecture.md):
- One way to export: same driver codepath as real-time file writing
- Export parity: same driver, same ordering → identical files
- Export does NOT trigger real-time fileWriter
- Exports are explicit user actions, not automatic

Export Flow:
  1. Query [startTime..stopTime] from DB (bounded read, ingest order)
  2. Create export folder
  3. For each event, resolve DriverBinding-at-time(T) or fall back to registry
  4. Zip export folder
  5. Return download path

File Parity Ordering (Phase 6 Sub-Contract):
  ⚠️ This is a NARROW sub-contract for file/export parity ONLY.
  It does NOT replace the Global Truth Ordering contract (Phase 4).
  
  - Files and exports use INGEST ORDER (rowid), NOT timestamp order.
  - FileWriter writes as events arrive (implicitly ingest order).
  - Export uses ingestOrder=True to match real-time writes.
  - This is the ONLY ordering that can match real-time file content.
  
  Global Truth Ordering (queries, streams, UI) still uses:
    timebase + lane priority + eventId (per ordering.py)

Binding Resolution:
- DriverBinding is authoritative once it exists.
- Export resolves binding-at-time(eventTime) first.
- If no binding exists, falls back to registry.selectDriver().
- This ensures historical exports use historical driver mappings.

Design (from guidelines.md):
- Reuse drivers from fileWriter (no duplicate code)
- Explicit, deterministic logic
"""

import asyncio
import zipfile
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from nova.core.database import Database
from nova.core.events import Lane, Timebase
from nova.core.drivers.registry import DriverRegistry
from nova.core.drivers.base import BaseDriver
from sdk.logging import getLogger


class ExportError(Exception):
    """Export execution error"""
    pass


class Export:
    """
    Export execution handler.
    
    Queries DB for time window, writes files via drivers, creates zip.
    Uses same drivers as real-time fileWriter (parity guarantee).
    """
    
    def __init__(self, database: Database, exportDir: Path):
        """
        Initialize export handler.
        
        Args:
            database: Database instance
            exportDir: Base directory for export output
        """
        self.database = database
        self.exportDir = exportDir
        self.log = getLogger()
        
        # Ensure export directory exists
        self.exportDir.mkdir(parents=True, exist_ok=True)
    
    async def export(
        self,
        startTime: str,
        stopTime: str,
        timebase: Timebase = Timebase.CANONICAL,
        scopeIds: Optional[List[str]] = None,
        lanes: Optional[List[Lane]] = None,
        systemId: Optional[str] = None,
        containerId: Optional[str] = None,
        uniqueId: Optional[str] = None,
        exportId: Optional[str] = None,
        ingestOrder: bool = True
    ) -> Dict[str, Any]:
        """
        Execute export for time window.
        
        Args:
            startTime: ISO8601 start time (inclusive)
            stopTime: ISO8601 stop time (inclusive)
            timebase: Source or Canonical for ordering
            scopeIds: Filter by scope IDs
            lanes: Filter by lanes (None = all)
            systemId: Filter by system
            containerId: Filter by container
            uniqueId: Filter by entity
            exportId: Custom export ID (generated if not provided)
            ingestOrder: If True, use ingest order (rowid) for parity with real-time.
                         If False, use timebase order (for UI display exports).
            
        Returns:
            Export result dict with path, stats
            
        Raises:
            ExportError: On failure
        """
        # Generate export ID
        if not exportId:
            exportId = f"export-{uuid.uuid4().hex[:8]}"
        
        self.log.info(f"[Export] Starting {exportId}: {startTime} → {stopTime}")
        
        # Create export folder
        exportFolder = self.exportDir / exportId
        exportFolder.mkdir(parents=True, exist_ok=True)
        
        # Create driver registry for this export
        # Uses same drivers as fileWriter (parity)
        registry = DriverRegistry(exportFolder)
        registry.loadBuiltinDrivers()
        
        try:
            # Query events from DB (bounded read)
            # For parity: use ingest order (rowid) to match real-time file writes
            events = self.database.queryEvents(
                startTime=startTime,
                stopTime=stopTime,
                timebase=timebase,
                scopeIds=scopeIds,
                lanes=lanes,
                systemId=systemId,
                containerId=containerId,
                uniqueId=uniqueId,
                ingestOrder=ingestOrder
            )
            
            # Pre-load DriverBindings for this time window (for binding-at-time resolution)
            bindings = self._loadBindings(startTime, stopTime)
            
            eventCount = len(events)
            self.log.info(f"[Export] {exportId}: {eventCount} events to export")
            
            # Write events via drivers (same codepath as real-time)
            filesWritten = set()
            eventsWritten = 0
            
            for event in events:
                result = self._writeEvent(event, registry, bindings)
                if result:
                    filesWritten.add(str(result))
                    eventsWritten += 1
            
            # Finalize drivers (close files)
            registry.finalize()
            
            # Create zip archive
            zipPath = self.exportDir / f"{exportId}.zip"
            self._createZip(exportFolder, zipPath)
            
            self.log.info(f"[Export] {exportId}: Complete. {eventsWritten} events → {len(filesWritten)} files")
            
            return {
                'exportId': exportId,
                'zipPath': str(zipPath),
                'folder': str(exportFolder),
                'startTime': startTime,
                'stopTime': stopTime,
                'eventCount': eventCount,
                'eventsWritten': eventsWritten,
                'filesWritten': len(filesWritten)
            }
        
        except Exception as e:
            self.log.error(f"[Export] {exportId} failed: {e}")
            raise ExportError(f"Export failed: {e}")
    
    def _writeEvent(self, event: Dict[str, Any], registry: DriverRegistry, 
                     bindings: Dict[str, Dict[str, Any]]) -> Optional[Path]:
        """
        Write single event via driver using binding-at-time resolution.
        
        Resolution Order:
        1. Look up DriverBinding for this (targetId, lane) at event time
        2. If binding exists, use that driver (by driverId)
        3. If no binding, fall back to registry.selectDriver()
        
        This ensures historical exports use historical driver mappings,
        surviving driver upgrades/additions.
        
        Args:
            event: Event dict
            registry: Driver registry
            bindings: Pre-loaded bindings from _loadBindings()
            
        Returns:
            Path to written file, or None
        """
        # Get lane
        laneStr = event.get('lane')
        if not laneStr:
            return None
        
        try:
            lane = Lane(laneStr)
        except ValueError:
            return None
        
        # Get message type
        messageType = event.get('messageType')
        
        # Build targetId for binding lookup (pipe-delimited, matching entityIdentityKey)
        systemId = event.get('systemId', '')
        containerId = event.get('containerId', '')
        uniqueId = event.get('uniqueId', '')
        targetId = f"{systemId}|{containerId}|{uniqueId}"
        
        # Resolve driver via binding-at-time(T)
        driver = self._resolveDriver(targetId, lane, event, bindings, registry)
        if not driver:
            return None
        
        # Get canonical time for file organization
        canonicalTruthTime = event.get('canonicalTruthTime') or event.get('sourceTruthTime', '')
        
        # Write via driver (same as fileWriter)
        return driver.write(event, canonicalTruthTime)
    
    def _loadBindings(self, startTime: str, stopTime: str) -> Dict[str, Dict[str, Any]]:
        """
        Pre-load DriverBinding metadata events for time window.
        
        Returns dict: bindingKey → binding payload
        where bindingKey = "{targetId}|{targetLane}"
        
        For multiple bindings with same key, uses the one with latest effectiveTime
        that is <= the event time (resolved per-event in _resolveDriver).
        """
        bindings = {}
        
        try:
            # Query DriverBinding metadata events
            bindingEvents = self.database.queryEvents(
                startTime="1970-01-01T00:00:00Z",  # All bindings up to stopTime
                stopTime=stopTime,
                timebase=Timebase.CANONICAL,
                lanes=[Lane.METADATA],
                messageType="DriverBinding"
            )
            
            for evt in bindingEvents:
                payload = evt.get('payload', {})
                if isinstance(payload, str):
                    import json
                    payload = json.loads(payload)
                
                targetId = payload.get('targetId', '')
                targetLane = payload.get('targetLane', '')
                effectiveTime = payload.get('effectiveTime', '')
                
                bindingKey = f"{targetId}|{targetLane}"
                
                # Keep binding with latest effectiveTime
                existing = bindings.get(bindingKey)
                if not existing or effectiveTime > existing.get('effectiveTime', ''):
                    bindings[bindingKey] = payload
                    
        except Exception as e:
            self.log.warning(f"[Export] Failed to load bindings: {e}")
        
        return bindings
    
    def _resolveDriver(self, targetId: str, lane: Lane, event: Dict[str, Any],
                       bindings: Dict[str, Dict[str, Any]], 
                       registry: DriverRegistry) -> Optional[BaseDriver]:
        """
        Resolve driver using binding-at-time(T) or fall back to registry.
        
        Resolution:
        1. Look up binding for (targetId, lane)
        2. If binding exists and effectiveTime <= event time, use that driver
        3. Otherwise fall back to registry.selectDriver()
        """
        # Build binding key
        bindingKey = f"{targetId}|{lane.value}"
        
        # Check for binding
        binding = bindings.get(bindingKey)
        if binding:
            eventTime = event.get('canonicalTruthTime') or event.get('sourceTruthTime', '')
            effectiveTime = binding.get('effectiveTime', '')
            
            # Use binding if effectiveTime <= eventTime
            if effectiveTime <= eventTime:
                driverId = binding.get('driverId')
                if driverId:
                    driver = registry.getDriver(driverId)
                    if driver:
                        return driver
        
        # Fall back to registry selection
        messageType = event.get('messageType')
        return registry.selectDriver(lane, messageType)
    
    def _createZip(self, folder: Path, zipPath: Path):
        """
        Create zip archive of export folder.
        
        Args:
            folder: Folder to zip
            zipPath: Output zip path
        """
        with zipfile.ZipFile(zipPath, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filePath in folder.rglob('*'):
                if filePath.is_file():
                    arcname = filePath.relative_to(folder)
                    zf.write(filePath, arcname)
    
    def listExports(self) -> List[Dict[str, Any]]:
        """
        List existing exports.
        
        Returns:
            List of export info dicts
        """
        exports = []
        
        for zipFile in self.exportDir.glob('*.zip'):
            stat = zipFile.stat()
            exports.append({
                'exportId': zipFile.stem,
                'zipPath': str(zipFile),
                'size': stat.st_size,
                'created': datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
            })
        
        return sorted(exports, key=lambda x: x['created'], reverse=True)
    
    def getExportPath(self, exportId: str) -> Optional[Path]:
        """
        Get path to export zip.
        
        Args:
            exportId: Export identifier
            
        Returns:
            Path to zip file, or None if not found
        """
        zipPath = self.exportDir / f"{exportId}.zip"
        return zipPath if zipPath.exists() else None
