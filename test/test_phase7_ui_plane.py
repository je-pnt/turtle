"""
Phase 7 Tests: UI Plane (UiUpdate, UiCheckpoint)

Tests for UI updates and checkpoint generation.

Architecture Invariants (nova architecture.md):
- UiUpdate: partial state upserts referencing manifestId+manifestVersion
- UiCheckpoint: full state snapshots, NOVA-generated (not from producers)
- State-at-time(T): find latest UiCheckpoint ≤ T, apply subsequent UiUpdates

Test Coverage:
1. UiUpdate event creation and ingestion
2. UiCheckpoint generation on discovery
3. UiCheckpoint generation periodic (60 min)
4. State-at-time(T) query algorithm
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add parent to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nova.core.events import (
    Lane, Timebase,
    UiUpdate, UiCheckpoint, MetadataEvent,
    buildEntityIdentityKey, computeEventId
)
from nova.core.database import Database
from nova.core.uiState import UiStateManager, EntityViewKey, DEFAULT_CHECKPOINT_INTERVAL_SECONDS


@pytest.fixture
def manifestRegistry():
    """Optional manifest registry (not required for these tests)."""
    return None


@pytest.fixture
def tempDb():
    """Create and cleanup temp database"""
    tmpDir = Path(tempfile.mkdtemp())
    dbPath = tmpDir / "test.db"
    db = Database(str(dbPath))
    db.scopeId = "test"
    yield db
    db.close()
    shutil.rmtree(tmpDir, ignore_errors=True)


@pytest.fixture
def uiStateManager(tempDb):
    """Create UiStateManager with database"""
    return UiStateManager(tempDb)


class TestUiUpdateEvent:
    """Test UiUpdate event creation and handling"""
    
    def test_create_uiupdate(self):
        """UiUpdate.create produces valid event"""
        update = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.7749, "lon": -122.4194}
        )
        
        assert update.eventId is not None
        assert update.lane == Lane.UI
        assert update.messageType == "UiUpdate"
        assert update.viewId == "telemetry.gnss"
        assert update.data["lat"] == 37.7749
    
    def test_uiupdate_todict_fromdict(self):
        """UiUpdate serialization roundtrips"""
        original = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.0, "lon": -122.0}
        )
        
        dictForm = original.toDict()
        restored = UiUpdate.fromDict(dictForm)
        
        assert restored.eventId == original.eventId
        assert restored.viewId == original.viewId
        assert restored.data == original.data


class TestUiCheckpointEvent:
    """Test UiCheckpoint event creation and handling"""
    
    def test_create_uicheckpoint(self):
        """UiCheckpoint.create produces valid event"""
        checkpoint = UiCheckpoint.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.7749, "lon": -122.4194, "alt": 10.0}
        )
        
        assert checkpoint.eventId is not None
        assert checkpoint.lane == Lane.UI
        assert checkpoint.messageType == "UiCheckpoint"
        assert checkpoint.data["lat"] == 37.7749
    
    def test_uicheckpoint_todict_fromdict(self):
        """UiCheckpoint serialization roundtrips"""
        original = UiCheckpoint.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="entity.status",
            manifestId="entity.status",
            manifestVersion="1.0.0",
            data={"displayName": "GPS Device 1"}
        )
        
        dictForm = original.toDict()
        restored = UiCheckpoint.fromDict(dictForm)
        
        assert restored.eventId == original.eventId
        assert restored.messageType == "UiCheckpoint"
        assert restored.data == original.data


class TestUiStateManager:
    """Test UiStateManager checkpoint generation"""
    
    def test_discovery_checkpoint(self, uiStateManager):
        """First UiUpdate for entity/view generates checkpoint"""
        update = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.7749}
        )
        
        checkpoint = uiStateManager.processUiUpdate(update)
        
        assert checkpoint is not None
        assert checkpoint.messageType == "UiCheckpoint"
        assert checkpoint.data["lat"] == 37.7749
    
    def test_subsequent_update_no_checkpoint(self, uiStateManager):
        """Second UiUpdate for same entity/view does NOT generate checkpoint"""
        update1 = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.7749}
        )
        
        update2 = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:01:00Z",
            systemId="hardwareService",
            containerId="node1",
            uniqueId="gps1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lon": -122.4194}
        )
        
        # First generates checkpoint
        cp1 = uiStateManager.processUiUpdate(update1)
        assert cp1 is not None
        
        # Second does not (not discovery, not periodic)
        cp2 = uiStateManager.processUiUpdate(update2)
        assert cp2 is None
    
    def test_accumulated_state(self, uiStateManager):
        """State accumulates across UiUpdates"""
        update1 = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.0}
        )
        
        update2 = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:01:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lon": -122.0}
        )
        
        uiStateManager.processUiUpdate(update1)
        uiStateManager.processUiUpdate(update2)
        
        # Get accumulated state
        key = EntityViewKey(
            scopeId="test",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0"
        )
        
        acc = uiStateManager._accumulators.get(key)
        assert acc is not None
        assert acc.data["lat"] == 37.0
        assert acc.data["lon"] == -122.0
    
    def test_periodic_checkpoint_check(self, uiStateManager):
        """Periodic checkpoint triggers when bucket advances"""
        key = EntityViewKey(
            scopeId="test",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0"
        )
        
        # First update
        update1 = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.0}
        )
        uiStateManager.processUiUpdate(update1)
        
        # Same bucket (advance a small amount)
        start = datetime.fromisoformat("2026-01-28T12:00:00+00:00")
        notEnough = (start + timedelta(seconds=60)).isoformat()
        assert not uiStateManager.shouldGeneratePeriodicCheckpoint(key, notEnough)
        
        # Next bucket (advance by one full interval)
        enough = (start + timedelta(seconds=DEFAULT_CHECKPOINT_INTERVAL_SECONDS)).isoformat()
        assert uiStateManager.shouldGeneratePeriodicCheckpoint(key, enough)


class TestStateAtTime:
    """Test state-at-time(T) query algorithm"""
    
    def test_state_at_time_from_updates(self, tempDb, manifestRegistry):
        """State-at-time builds from UiUpdates when no checkpoint"""
        uiStateManager = UiStateManager(tempDb, manifestRegistry)
        
        # Insert UiUpdate events directly to DB
        now = "2026-01-28T12:00:00Z"
        
        update1 = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.0}
        )
        
        update2 = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:01:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lon": -122.0}
        )
        
        tempDb.insertEvent(update1, now)
        tempDb.insertEvent(update2, now)
        
        # Query state at 12:02
        state = uiStateManager.getStateAtTime(
            scopeId="test",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            targetTime="2026-01-28T12:02:00Z"
        )
        
        assert state is not None
        assert state["lat"] == 37.0
        assert state["lon"] == -122.0
    
    def test_state_at_time_from_checkpoint(self, tempDb, manifestRegistry):
        """State-at-time uses checkpoint as base"""
        uiStateManager = UiStateManager(tempDb, manifestRegistry)
        
        now = "2026-01-28T12:00:00Z"
        
        # Checkpoint at 12:00
        checkpoint = UiCheckpoint.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:00:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"lat": 37.0, "lon": -122.0, "alt": 10.0}
        )
        
        # Update at 12:01 (changes alt)
        update = UiUpdate.create(
            scopeId="test",
            sourceTruthTime="2026-01-28T12:01:00Z",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            manifestId="telemetry.gnss",
            manifestVersion="1.0.0",
            data={"alt": 20.0}
        )
        
        tempDb.insertEvent(checkpoint, now)
        tempDb.insertEvent(update, now)
        
        # Query state at 12:02
        state = uiStateManager.getStateAtTime(
            scopeId="test",
            systemId="hs",
            containerId="n1",
            uniqueId="dev1",
            viewId="telemetry.gnss",
            targetTime="2026-01-28T12:02:00Z"
        )
        
        assert state is not None
        assert state["lat"] == 37.0  # From checkpoint
        assert state["lon"] == -122.0  # From checkpoint
        assert state["alt"] == 20.0  # From update


class TestEventFromDict:
    """Test eventFromDict handles UI lane events"""
    
    def test_uiupdate_from_dict(self):
        """eventFromDict handles UiUpdate"""
        from nova.core.events import eventFromDict
        
        eventDict = {
            "eventId": "test123",
            "scopeId": "test",
            "lane": "ui",
            "sourceTruthTime": "2026-01-28T12:00:00Z",
            "messageType": "UiUpdate",
            "systemId": "hs",
            "containerId": "n1",
            "uniqueId": "dev1",
            "viewId": "telemetry.gnss",
            "manifestId": "telemetry.gnss",
            "manifestVersion": "1.0.0",
            "data": {"lat": 37.0}
        }
        
        event = eventFromDict(eventDict)
        
        assert isinstance(event, UiUpdate)
        assert event.viewId == "telemetry.gnss"
        assert event.data["lat"] == 37.0
    
    def test_uicheckpoint_from_dict(self):
        """eventFromDict handles UiCheckpoint"""
        from nova.core.events import eventFromDict
        
        eventDict = {
            "eventId": "test456",
            "scopeId": "test",
            "lane": "ui",
            "sourceTruthTime": "2026-01-28T12:00:00Z",
            "messageType": "UiCheckpoint",
            "systemId": "hs",
            "containerId": "n1",
            "uniqueId": "dev1",
            "viewId": "telemetry.gnss",
            "manifestId": "telemetry.gnss",
            "manifestVersion": "1.0.0",
            "data": {"lat": 37.0, "lon": -122.0}
        }
        
        event = eventFromDict(eventDict)
        
        assert isinstance(event, UiCheckpoint)
        assert event.messageType == "UiCheckpoint"
        assert event.data["lon"] == -122.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
