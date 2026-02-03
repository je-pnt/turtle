"""
NOVA UI State Management

Handles UiCheckpoint generation for fast seek.

Architecture (nova architecture.md):
- UiCheckpoint: full state snapshot per (entity, viewId)
- Generated at deterministic timeline boundaries (bucketed by sourceTruthTime)
- State-at-time(T): find latest UiCheckpoint ≤ T, apply subsequent UiUpdates

Phase 10 (phase9-11Updated.md):
- Checkpoint interval is config-driven (default 500s)
- History timeout is config-driven (default 120s)
- Seek reconstruction: checkpoint + recent UiUpdates within timeout

Design (guidelines.md):
- Deterministic checkpoint generation: pure function of timeline time, not wall-clock
- Bucket key: (identity, viewId, manifestVersion, bucketStart)
- At most one checkpoint per bucket (idempotent)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple, TYPE_CHECKING

from sdk.logging import getLogger

if TYPE_CHECKING:
    from nova.core.database import Database
    from nova.core.manifests import ManifestRegistry


# Default checkpoint interval in seconds (Phase 10: config-driven, default 500s)
DEFAULT_CHECKPOINT_INTERVAL_SECONDS = 500

# Default history timeout in seconds (Phase 10: config-driven, default 120s)
DEFAULT_HISTORY_TIMEOUT_SECONDS = 120


@dataclass
class EntityViewKey:
    """Identity key for UI state: entity + viewId."""
    scopeId: str
    systemId: str
    containerId: str
    uniqueId: str
    viewId: str
    manifestId: str
    manifestVersion: str
    
    def toTuple(self) -> Tuple[str, ...]:
        return (self.scopeId, self.systemId, self.containerId, 
                self.uniqueId, self.viewId, self.manifestId, self.manifestVersion)
    
    def __hash__(self) -> int:
        return hash(self.toTuple())
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EntityViewKey):
            return False
        return self.toTuple() == other.toTuple()


@dataclass
class UiStateAccumulator:
    """
    Accumulates UI state for an entity view.
    
    Applies UiUpdate upserts in order to build complete state.
    Tracks which timeline buckets have had checkpoints emitted.
    """
    key: 'EntityViewKey'
    data: Dict[str, Any] = field(default_factory=dict)
    lastUpdateTime: Optional[str] = None
    checkpointedBuckets: Set[str] = field(default_factory=set)  # Set of bucket timestamps (ISO8601)
    
    def applyUpdate(self, updateData: Dict[str, Any], updateTime: str) -> None:
        """Apply partial upsert from UiUpdate."""
        for k, v in updateData.items():
            if v is None:
                # None removes the key (upsert delete semantics)
                self.data.pop(k, None)
            else:
                self.data[k] = v
        self.lastUpdateTime = updateTime


def computeBucketStart(timestamp: str, intervalSeconds: int = DEFAULT_CHECKPOINT_INTERVAL_SECONDS) -> str:
    """
    Compute the bucket start time for a given timestamp.
    
    Deterministic: floor to the nearest interval boundary.
    E.g., with 500-second intervals: 14:37:22 -> 14:30:00
    
    Args:
        timestamp: ISO8601 timestamp
        intervalSeconds: Bucket size in seconds (Phase 10 default: 500s)
        
    Returns:
        ISO8601 timestamp of bucket start
    """
    try:
        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        # Floor to interval boundary (in seconds from start of day)
        totalSeconds = dt.hour * 3600 + dt.minute * 60 + dt.second
        bucketSeconds = (totalSeconds // intervalSeconds) * intervalSeconds
        bucket_dt = dt.replace(
            hour=bucketSeconds // 3600,
            minute=(bucketSeconds % 3600) // 60,
            second=bucketSeconds % 60,
            microsecond=0
        )
        return bucket_dt.isoformat()
    except (ValueError, TypeError):
        # Fallback: return as-is if parsing fails
        return timestamp


class UiStateManager:
    """
    Manages UI state and checkpoint generation.
    
    Responsibilities:
    - Track entity/view state from UiUpdates
    - Generate UiCheckpoints on discovery and periodically
    - Provide state-at-time(T) queries
    
    Phase 10: Config-driven checkpoint interval and history timeout.
    """
    
    def __init__(
        self, 
        database: 'Database', 
        registry: Optional['ManifestRegistry'] = None,
        checkpointIntervalSeconds: int = DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
        historyTimeoutSeconds: int = DEFAULT_HISTORY_TIMEOUT_SECONDS
    ):
        self._db = database
        self._registry = registry
        self._checkpointInterval = checkpointIntervalSeconds
        self._historyTimeout = historyTimeoutSeconds
        self._accumulators: Dict[EntityViewKey, UiStateAccumulator] = {}
        self.log = getLogger()
    
    def processUiUpdate(self, event) -> Optional['UiCheckpoint']:
        """
        Process incoming UiUpdate event.
        
        Returns UiCheckpoint if this event's bucket hasn't been checkpointed yet.
        Deterministic: checkpoint time is the bucket start (floor of sourceTruthTime),
        not wall-clock or "first seen" time.
        
        Args:
            event: UiUpdate event
            
        Returns:
            UiCheckpoint if generated (new bucket), None otherwise
        """
        from nova.core.events import UiCheckpoint
        
        key = EntityViewKey(
            scopeId=event.scopeId,
            systemId=event.systemId,
            containerId=event.containerId,
            uniqueId=event.uniqueId,
            viewId=event.viewId,
            manifestId=event.manifestId,
            manifestVersion=event.manifestVersion
        )
        
        # Get or create accumulator
        if key not in self._accumulators:
            self._accumulators[key] = UiStateAccumulator(key=key)
        
        acc = self._accumulators[key]
        acc.applyUpdate(event.data, event.sourceTruthTime)
        
        # Compute deterministic bucket for this event's timeline time
        bucketStart = computeBucketStart(event.sourceTruthTime, self._checkpointInterval)
        
        # Check if we need a checkpoint for this bucket
        checkpoint = None
        if bucketStart not in acc.checkpointedBuckets:
            # Generate checkpoint at bucket boundary (deterministic)
            checkpoint = self._generateCheckpoint(key, acc, bucketStart)
            acc.checkpointedBuckets.add(bucketStart)
            self.log.debug(f"[UiState] Checkpoint for bucket {bucketStart}: {key.systemId}/{key.containerId}/{key.uniqueId}:{key.viewId}")
        
        return checkpoint
    
    def shouldGeneratePeriodicCheckpoint(self, key: EntityViewKey, currentTime: str) -> bool:
        """
        Check if periodic checkpoint should be generated for a new bucket.
        
        Deterministic: based on timeline time bucket, not wall-clock.
        """
        acc = self._accumulators.get(key)
        if not acc:
            return False
        
        bucketStart = computeBucketStart(currentTime, self._checkpointInterval)
        return bucketStart not in acc.checkpointedBuckets
    
    def generatePeriodicCheckpoints(self, currentTime: str) -> List['UiCheckpoint']:
        """
        Generate periodic checkpoints for all tracked entity/views at current bucket.
        
        Deterministic: uses bucket boundary of currentTime.
        
        Returns:
            List of generated UiCheckpoints
        """
        checkpoints = []
        bucketStart = computeBucketStart(currentTime, self._checkpointInterval)
        
        for key, acc in self._accumulators.items():
            if bucketStart not in acc.checkpointedBuckets:
                checkpoint = self._generateCheckpoint(key, acc, bucketStart)
                acc.checkpointedBuckets.add(bucketStart)
                checkpoints.append(checkpoint)
        
        if checkpoints:
            self.log.info(f"[UiState] Generated {len(checkpoints)} periodic checkpoints at bucket {bucketStart}")
        
        return checkpoints
        
        return checkpoints
    
    def _generateCheckpoint(self, key: EntityViewKey, acc: UiStateAccumulator, checkpointTime: str) -> 'UiCheckpoint':
        """Generate UiCheckpoint event from accumulated state."""
        from nova.core.events import UiCheckpoint
        
        return UiCheckpoint.create(
            scopeId=key.scopeId,
            sourceTruthTime=checkpointTime,
            systemId=key.systemId,
            containerId=key.containerId,
            uniqueId=key.uniqueId,
            viewId=key.viewId,
            manifestId=key.manifestId,
            manifestVersion=key.manifestVersion,
            data=dict(acc.data)  # Copy to avoid mutation
        )
    
    def getStateAtTime(
        self,
        scopeId: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        viewId: str,
        targetTime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Compute UI state at a specific time.
        
        Phase 10 bounded seek algorithm (per phase9-11Updated.md):
        1. Find latest UiCheckpoint ≤ targetTime
        2. Apply UiUpdates within historyTimeoutSeconds of targetTime
        3. Return complete state dict
        
        This prevents full-history scans during seek.
        
        Args:
            scopeId: Scope identifier
            systemId: Data system
            containerId: Node/payload
            uniqueId: Entity identifier
            viewId: View identifier
            targetTime: ISO8601 timestamp
            
        Returns:
            State dict at time, or None if no data exists
        """
        from nova.core.events import Lane, Timebase
        
        # Query for checkpoint ≤ targetTime
        checkpoints = self._db.queryEvents(
            startTime="1970-01-01T00:00:00Z",
            stopTime=targetTime,
            timebase=Timebase.SOURCE,
            scopeIds=[scopeId],
            lanes=[Lane.UI],
            systemId=systemId,
            containerId=containerId,
            uniqueId=uniqueId,
            viewId=viewId,
            messageType="UiCheckpoint",
            limit=1  # Get only the latest
        )
        
        if not checkpoints:
            # No checkpoint - use bounded history window only
            baseState = {}
            # Phase 10: Bounded seek - only look back historyTimeoutSeconds
            try:
                targetDt = datetime.fromisoformat(targetTime.replace('Z', '+00:00'))
                historyStart = (targetDt - timedelta(seconds=self._historyTimeout)).isoformat()
            except ValueError:
                historyStart = "1970-01-01T00:00:00Z"
            baseTime = historyStart
        else:
            # Start from checkpoint
            cp = checkpoints[-1]  # Latest
            baseState = cp.get('data', {})
            baseTime = cp.get('sourceTruthTime', "1970-01-01T00:00:00Z")
        
        # Query UiUpdates after checkpoint/history start up to targetTime
        updates = self._db.queryEvents(
            startTime=baseTime,
            stopTime=targetTime,
            timebase=Timebase.SOURCE,
            scopeIds=[scopeId],
            lanes=[Lane.UI],
            systemId=systemId,
            containerId=containerId,
            uniqueId=uniqueId,
            viewId=viewId,
            messageType="UiUpdate"
        )
        
        # Apply updates in order
        state = dict(baseState)
        for update in updates:
            updateTime = update.get('sourceTruthTime', '')
            # Skip updates from before/at checkpoint time (already included)
            if updateTime <= baseTime and checkpoints:
                continue
            
            updateData = update.get('data', {})
            for k, v in updateData.items():
                if v is None:
                    state.pop(k, None)
                else:
                    state[k] = v
        
        return state if state else None
    
    def buildUiStateForEntity(
        self,
        scopeId: str,
        systemId: str,
        containerId: str,
        uniqueId: str,
        targetTime: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Build complete UI state for an entity across all views.
        
        Returns:
            Dict of viewId -> state dict
        """
        from nova.core.events import Lane, Timebase
        
        # Query all UI events for this entity
        events = self._db.queryEvents(
            startTime="1970-01-01T00:00:00Z",
            stopTime=targetTime,
            timebase=Timebase.SOURCE,
            scopeIds=[scopeId],
            lanes=[Lane.UI],
            systemId=systemId,
            containerId=containerId,
            uniqueId=uniqueId
        )
        
        # Group by viewId
        viewIds = set(e.get('viewId') for e in events if e.get('viewId'))
        
        result = {}
        for viewId in viewIds:
            state = self.getStateAtTime(scopeId, systemId, containerId, uniqueId, viewId, targetTime)
            if state:
                result[viewId] = state
        
        return result
    
    def reset(self) -> None:
        """Reset all accumulated state (for replay restart)."""
        self._accumulators.clear()
        self.log.debug("[UiState] Reset all accumulated state")
