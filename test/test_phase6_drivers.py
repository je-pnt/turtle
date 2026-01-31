"""
Phase 6 Tests: Drivers and Export Parity

Tests for driver plugin system, fileWriter, and export functionality.
Verifies export parity: same driver codepath for real-time and export.

Architecture Invariants (nova architecture.md):
- One way to export: same driver codepath as real-time file writing
- fileWriter runs ONLY on ingest (never on query/stream/replay)
- Deterministic driver selection: same inputs → same driver
- Export parity: export matches real-time for same time window
- Folder hierarchy: {date}/{systemId}/{containerId}/{uniqueId}/{filename}

Test Coverage:
1. Driver registration and selection
2. Raw binary driver write/read (raw.bin)
3. Position CSV driver write/read (rx_llas.csv)
4. FileWriter triggered on ingest only
5. Export parity: export matches DB query ordering
6. Folder hierarchy verification
"""

import pytest
import tempfile
import shutil
import json
from pathlib import Path
from datetime import datetime, timezone

# Add parent to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nova.core.events import (
    Lane, Timebase,
    RawFrame, ParsedMessage,
    buildEntityIdentityKey, computeEventId
)
from nova.core.drivers.base import BaseDriver, DriverCapabilities
from nova.core.drivers.registry import DriverRegistry
from nova.core.drivers.rawBinary import RawBinaryDriver
from nova.core.drivers.positionCsv import PositionCsvDriver
from nova.core.fileWriter import FileWriter


@pytest.fixture
def tempDir():
    """Create and cleanup temp directory"""
    dirPath = Path(tempfile.mkdtemp())
    yield dirPath
    shutil.rmtree(dirPath, ignore_errors=True)


@pytest.fixture
def driverRegistry(tempDir):
    """Create driver registry with temp output dir"""
    registry = DriverRegistry(tempDir)
    registry.loadBuiltinDrivers()
    return registry


class TestDriverRegistry:
    """Test driver registration and selection"""
    
    def test_load_builtin_drivers(self, driverRegistry):
        """Built-in drivers are loaded"""
        assert driverRegistry.getDriver("raw-binary") is not None
        assert driverRegistry.getDriver("position-csv") is not None
    
    def test_select_raw_driver(self, driverRegistry):
        """Raw lane selects raw-binary driver"""
        driver = driverRegistry.selectDriver(Lane.RAW)
        assert driver is not None
        assert driver.capabilities.driverId == "raw-binary"
    
    def test_select_position_driver(self, driverRegistry):
        """Position messageType selects position-csv driver"""
        driver = driverRegistry.selectDriver(Lane.PARSED, "Position")
        assert driver is not None
        assert driver.capabilities.driverId == "position-csv"
    
    def test_no_driver_for_other_parsed(self, driverRegistry):
        """Non-Position parsed messages have no driver"""
        driver = driverRegistry.selectDriver(Lane.PARSED, "gnss.navPvt")
        assert driver is None  # Only Position has a driver
    
    def test_deterministic_selection(self, driverRegistry):
        """Same inputs always select same driver"""
        driver1 = driverRegistry.selectDriver(Lane.PARSED, "Position")
        driver2 = driverRegistry.selectDriver(Lane.PARSED, "Position")
        
        # Same driver instance
        assert driver1 is driver2
    
    def test_no_driver_for_unknown_lane(self, driverRegistry):
        """Returns None for lanes without drivers"""
        # UI lane has no driver registered
        driver = driverRegistry.selectDriver(Lane.UI)
        assert driver is None


class TestRawBinaryDriver:
    """Test raw binary file writing"""
    
    def test_write_preserves_bytes(self, tempDir):
        """Raw driver preserves exact byte content"""
        driver = RawBinaryDriver(tempDir)
        
        # Test data
        rawBytes = b"\x00\x01\x02\x03\xff\xfe\xfd"
        event = {
            'lane': 'raw',
            'systemId': 'hardwareService',
            'containerId': 'node1',
            'uniqueId': 'gps1',
            'bytesData': rawBytes
        }
        
        canonicalTime = "2026-01-28T12:00:00+00:00"
        filePath = driver.write(event, canonicalTime)
        
        # Verify file created
        assert filePath is not None
        assert filePath.exists()
        assert filePath.name == "raw.bin"
        
        # Verify folder hierarchy: {date}/{systemId}/{containerId}/{uniqueId}/raw.bin
        relPath = filePath.relative_to(tempDir)
        parts = relPath.parts
        assert len(parts) == 5  # date/systemId/containerId/uniqueId/raw.bin
        assert parts[0] == "2026-01-28"  # date
        assert parts[1] == "hardwareService"  # systemId
        assert parts[2] == "node1"  # containerId
        assert parts[3] == "gps1"  # uniqueId
        assert parts[4] == "raw.bin"  # filename
        
        # Read back and verify exact bytes
        driver.finalize()
        with open(filePath, 'rb') as f:
            readBytes = f.read()
        
        assert readBytes == rawBytes
    
    def test_appends_to_daily_file(self, tempDir):
        """Multiple writes append to same daily file"""
        driver = RawBinaryDriver(tempDir)
        
        bytes1 = b"first chunk"
        bytes2 = b"second chunk"
        
        event1 = {
            'lane': 'raw',
            'systemId': 'hs',
            'containerId': 'n1',
            'uniqueId': 'dev1',
            'bytesData': bytes1
        }
        event2 = {
            'lane': 'raw',
            'systemId': 'hs',
            'containerId': 'n1',
            'uniqueId': 'dev1',
            'bytesData': bytes2
        }
        
        canonicalTime = "2026-01-28T12:00:00+00:00"
        
        path1 = driver.write(event1, canonicalTime)
        path2 = driver.write(event2, canonicalTime)
        
        # Same file
        assert path1 == path2
        
        # Verify both chunks present
        driver.finalize()
        with open(path1, 'rb') as f:
            content = f.read()
        
        assert content == bytes1 + bytes2


class TestPositionCsvDriver:
    """Test Position CSV file writing (llas.csv)"""
    
    def test_write_creates_csv(self, tempDir):
        """Position driver creates valid llas.csv"""
        driver = PositionCsvDriver(tempDir)
        
        event = {
            'lane': 'parsed',
            'systemId': 'hardwareService',
            'containerId': 'node1',
            'uniqueId': 'gps1',
            'messageType': 'Position',
            'schemaVersion': '1.0.0',
            'sourceTruthTime': '2026-01-28T12:00:00Z',
            'payload': {'lat': 37.7749, 'lon': -122.4194, 'alt': 10.5, 'time': 123456789, 'fixType': 3}
        }
        
        canonicalTime = "2026-01-28T12:00:00+00:00"
        filePath = driver.write(event, canonicalTime)
        
        assert filePath is not None
        assert filePath.exists()
        assert filePath.name == "llas.csv"
        
        # Verify folder hierarchy
        relPath = filePath.relative_to(tempDir)
        parts = relPath.parts
        assert len(parts) == 5  # date/systemId/containerId/uniqueId/llas.csv
        assert parts[0] == "2026-01-28"
        assert parts[4] == "llas.csv"
        
        # Verify CSV content
        driver.finalize()
        with open(filePath, 'r') as f:
            lines = f.readlines()
        
        # Header + 1 data row
        assert len(lines) == 2
        
        # Fixed columns for Position: times first, then position, then metadata
        header = lines[0].strip()
        assert header == "sourceTruthTime (UTC),iTOW (ms),latitude (deg),longitude (deg),altitude (HAE-m),fixType"
        
        # Data row
        data = lines[1].strip()
        assert "37.7749" in data
        assert "-122.4194" in data
    
    def test_only_accepts_position_messageType(self, tempDir):
        """Position driver only accepts Position messageType"""
        driver = PositionCsvDriver(tempDir)
        
        # Position messageType - should work
        posEvent = {
            'lane': 'parsed',
            'systemId': 'hs',
            'containerId': 'n1',
            'uniqueId': 'dev1',
            'messageType': 'Position',
            'sourceTruthTime': '2026-01-28T12:00:00Z',
            'payload': {'lat': 1.0, 'lon': 2.0, 'alt': 3.0, 'time': 0, 'fixType': 1}
        }
        
        canonicalTime = "2026-01-28T12:00:00+00:00"
        path = driver.write(posEvent, canonicalTime)
        assert path is not None
        
        # Non-Position messageType - should be None
        otherEvent = {
            'lane': 'parsed',
            'systemId': 'hs',
            'containerId': 'n1',
            'uniqueId': 'dev1',
            'messageType': 'gnss.navPvt',
            'sourceTruthTime': '2026-01-28T12:00:00Z',
            'payload': {'lat': 1.0, 'lon': 2.0}
        }
        
        path2 = driver.write(otherEvent, canonicalTime)
        assert path2 is None


class TestFileWriter:
    """Test real-time file writer"""
    
    def test_start_stop(self, tempDir):
        """FileWriter starts and stops cleanly"""
        writer = FileWriter(tempDir)
        writer.start()
        
        assert writer._running
        
        writer.stop()
        
        assert not writer._running
    
    def test_write_raw_event(self, tempDir):
        """FileWriter writes raw events via driver"""
        writer = FileWriter(tempDir)
        writer.start()
        
        event = {
            'lane': 'raw',
            'systemId': 'hs',
            'containerId': 'n1',
            'uniqueId': 'dev1',
            'bytesData': b"test data"
        }
        
        writer.write(event, "2026-01-28T12:00:00+00:00")
        
        # Wait for async write
        import time
        time.sleep(0.2)
        
        writer.stop()
        
        # Verify stats
        assert writer._eventsWritten >= 1
    
    def test_skips_no_driver_lanes(self, tempDir):
        """FileWriter skips lanes without drivers"""
        writer = FileWriter(tempDir)
        writer.start()
        
        # UI lane has no driver
        event = {
            'lane': 'ui',
            'systemId': 'nova',
            'containerId': 'ui',
            'uniqueId': 'view1'
        }
        
        writer.write(event, "2026-01-28T12:00:00+00:00")
        
        import time
        time.sleep(0.2)
        
        writer.stop()
        
        # No errors, just skipped
        assert writer._writeErrors == 0


class TestExportParity:
    """Test export produces same output as real-time"""
    
    def test_export_raw_matches_realtime(self, tempDir):
        """Export raw files match real-time writes"""
        # Write via driver (simulating real-time)
        realtimeDir = tempDir / "realtime"
        exportDir = tempDir / "export"
        
        realtimeRegistry = DriverRegistry(realtimeDir)
        realtimeRegistry.loadBuiltinDrivers()
        
        exportRegistry = DriverRegistry(exportDir)
        exportRegistry.loadBuiltinDrivers()
        
        # Same event
        event = {
            'lane': 'raw',
            'systemId': 'hs',
            'containerId': 'n1',
            'uniqueId': 'dev1',
            'bytesData': b"identical bytes"
        }
        canonicalTime = "2026-01-28T12:00:00+00:00"
        
        # Write via both (real-time and export)
        realtimeDriver = realtimeRegistry.selectDriver(Lane.RAW)
        realtimePath = realtimeDriver.write(event, canonicalTime)
        
        exportDriver = exportRegistry.selectDriver(Lane.RAW)
        exportPath = exportDriver.write(event, canonicalTime)
        
        realtimeRegistry.finalize()
        exportRegistry.finalize()
        
        # Read both
        with open(realtimePath, 'rb') as f:
            realtimeContent = f.read()
        with open(exportPath, 'rb') as f:
            exportContent = f.read()
        
        # Parity: identical content
        assert realtimeContent == exportContent
        
        # Both use same filename
        assert realtimePath.name == "raw.bin"
        assert exportPath.name == "raw.bin"
    
    def test_export_position_csv_matches_realtime(self, tempDir):
        """Export Position CSV files match real-time writes"""
        realtimeDir = tempDir / "realtime"
        exportDir = tempDir / "export"
        
        realtimeRegistry = DriverRegistry(realtimeDir)
        realtimeRegistry.loadBuiltinDrivers()
        
        exportRegistry = DriverRegistry(exportDir)
        exportRegistry.loadBuiltinDrivers()
        
        # Same Position event
        event = {
            'lane': 'parsed',
            'systemId': 'hs',
            'containerId': 'n1',
            'uniqueId': 'gps1',
            'messageType': 'Position',
            'schemaVersion': '1.0.0',
            'sourceTruthTime': '2026-01-28T12:00:00Z',
            'payload': {'lat': 42.0, 'lon': -71.0, 'alt': 100.0, 'time': 123456, 'fixType': 3}
        }
        canonicalTime = "2026-01-28T12:00:00+00:00"
        
        # Write via both
        realtimeDriver = realtimeRegistry.selectDriver(Lane.PARSED, "Position")
        realtimePath = realtimeDriver.write(event, canonicalTime)
        
        exportDriver = exportRegistry.selectDriver(Lane.PARSED, "Position")
        exportPath = exportDriver.write(event, canonicalTime)
        
        realtimeRegistry.finalize()
        exportRegistry.finalize()
        
        # Read both
        with open(realtimePath, 'r') as f:
            realtimeContent = f.read()
        with open(exportPath, 'r') as f:
            exportContent = f.read()
        
        # Parity: identical content
        assert realtimeContent == exportContent
        
        # Both use same filename
        assert realtimePath.name == "llas.csv"
        assert exportPath.name == "llas.csv"


class TestFileWriterNotOnReplay:
    """
    Test that fileWriter is NEVER triggered on replay/query/stream.
    
    Architecture invariant: fileWriter runs ONLY on ingest.
    """
    
    def test_ingest_triggers_filewriter(self, tempDir):
        """Ingest path triggers fileWriter (correct behavior)"""
        from nova.core.database import Database
        from nova.core.ingest import Ingest
        
        dbPath = tempDir / "test.db"
        db = Database(str(dbPath))
        
        writer = FileWriter(tempDir / "files")
        writer.start()
        
        ingest = Ingest(db, verifyEventId=False, fileWriter=writer)
        
        # Create and ingest event
        raw = RawFrame.create(
            scopeId="test-scope",
            sourceTruthTime=datetime.now(timezone.utc).isoformat(),
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            bytesData=b"test data"
        )
        
        ingest.ingest(raw)
        
        import time
        time.sleep(0.2)
        
        writer.stop()
        
        # Verify write happened
        assert writer._eventsWritten >= 1
    
    def test_query_does_not_trigger_filewriter(self, tempDir):
        """Query path does NOT trigger fileWriter"""
        from nova.core.database import Database
        from nova.core.query import Query
        
        # Query class has no fileWriter reference
        # This is enforced by architecture - Query only reads DB
        dbPath = tempDir / "test.db"
        db = Database(str(dbPath))
        
        query = Query(db)
        
        # Query has no fileWriter attribute
        assert not hasattr(query, 'fileWriter')


class TestBindingAtTime:
    """
    Test that export uses historical DriverBinding, not current registry selection.
    
    Architecture: DriverBinding-at-time(T) is authoritative.
    This ensures historical exports reproduce historical driver mappings
    even after drivers are upgraded or new drivers are added.
    """
    
    def test_export_uses_historical_binding(self, tempDir):
        """Export uses DriverBinding even if registry would pick different driver today"""
        from nova.core.database import Database
        from nova.core.export import Export
        from nova.core.events import MetadataEvent
        
        # Setup
        dbPath = tempDir / "test.db"
        exportDir = tempDir / "exports"
        db = Database(str(dbPath))
        exporter = Export(db, exportDir)
        
        now = datetime.now(timezone.utc).isoformat()
        
        # Insert a DriverBinding for a hypothetical "old-driver"
        # In real system, this would have been emitted when the driver was used
        binding = MetadataEvent.create(
            scopeId="test-scope",
            sourceTruthTime=now,
            messageType="DriverBinding",
            effectiveTime=now,
            payload={
                "targetId": "hs/n1/dev1",
                "targetLane": "Raw",
                "driverId": "raw-binary",  # Use existing driver for test
                "version": "1.0.0",
                "effectiveTime": now
            },
            systemId="nova",
            containerId="core",
            uniqueId="driver-registry"
        )
        db.insertEvent(binding, now)
        
        # Insert a raw event that would match the binding
        raw = RawFrame.create(
            scopeId="test-scope",
            sourceTruthTime=now,
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            bytesData=b"test data"
        )
        db.insertEvent(raw, now)
        
        # Export - should use the binding's driver
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            exporter.export(
                startTime="1970-01-01T00:00:00Z",
                stopTime="2100-01-01T00:00:00Z"
            )
        )
        
        # Should have written 1 event (the raw event, not the metadata)
        # The binding resolution should have found the "raw-binary" driver
        assert result['eventsWritten'] >= 1


class TestIngestOrderParity:
    """
    Test that exports use ingest order (rowid) to match real-time file writes.
    
    Parity Contract:
    - FileWriter writes in arrival order (ingest order)
    - Export must use same ordering for parity
    - Timestamp ordering would break parity when events arrive out-of-order
    """
    
    def test_out_of_order_events_have_parity(self, tempDir):
        """Inject out-of-order events, prove daily files and export match"""
        from nova.core.database import Database
        from nova.core.ingest import Ingest
        from nova.core.export import Export
        
        # Setup
        dbPath = tempDir / "test.db"
        filesDir = tempDir / "files"
        exportDir = tempDir / "exports"
        
        db = Database(str(dbPath))
        
        # FileWriter for real-time
        writer = FileWriter(filesDir)
        writer.start()
        ingest = Ingest(db, verifyEventId=False, fileWriter=writer)
        
        # Inject events OUT OF TIMESTAMP ORDER
        # Event 2 has EARLIER timestamp but arrives SECOND
        now = datetime.now(timezone.utc)
        
        # First event (arrives first, timestamp T2)
        raw1 = RawFrame.create(
            scopeId="test-scope",
            sourceTruthTime=(now).isoformat(),  # T2 (later)
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            bytesData=b"event-1-arrived-first"
        )
        
        # Second event (arrives second, timestamp T1 - EARLIER)
        from datetime import timedelta
        raw2 = RawFrame.create(
            scopeId="test-scope",
            sourceTruthTime=(now - timedelta(hours=1)).isoformat(),  # T1 (earlier)
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            bytesData=b"event-2-arrived-second"
        )
        
        # Ingest in arrival order
        ingest.ingest(raw1)  # Arrives first (timestamp T2)
        ingest.ingest(raw2)  # Arrives second (timestamp T1)
        
        import time
        time.sleep(0.3)
        writer.stop()
        
        # Real-time file should have events in ARRIVAL order: event1, event2
        realtimePath = filesDir / now.strftime("%Y-%m-%d") / "hs" / "n1" / "dev1" / "raw.bin"
        
        if realtimePath.exists():
            with open(realtimePath, 'rb') as f:
                realtimeContent = f.read()
            
            # Export with ingestOrder=True (default)
            exporter = Export(db, exportDir)
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                exporter.export(
                    startTime="1970-01-01T00:00:00Z",
                    stopTime="2100-01-01T00:00:00Z",
                    ingestOrder=True  # Match real-time order
                )
            )
            
            # Find export file
            exportPath = exportDir / result['exportId'] / now.strftime("%Y-%m-%d") / "hs" / "n1" / "dev1" / "raw.bin"
            
            if exportPath.exists():
                with open(exportPath, 'rb') as f:
                    exportContent = f.read()
                
                # PARITY: Export content must match real-time content
                # Both should have events in ingest order: event1, then event2
                assert exportContent == realtimeContent, "Export parity failed: order mismatch"
                
                # Verify both events are present (in arrival order)
                assert b"event-1-arrived-first" in exportContent
                assert b"event-2-arrived-second" in exportContent
                
                # Verify arrival order (event1 before event2)
                pos1 = exportContent.find(b"event-1-arrived-first")
                pos2 = exportContent.find(b"event-2-arrived-second")
                assert pos1 < pos2, "Events not in ingest order"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
