"""
NOVA Deterministic Ordering Implementation

Implements the authoritative ordering contract for all event lanes.
This ordering MUST be used identically across Query, Stream, Export, and TCP loopback.

Ordering Contract (nova architecture.md Section 5.3):
  Primary time: Selected timebase (canonical or source)
  Lane priority: Metadata → Command → UI → Parsed → Raw (when time ties)
  Within-lane: (timebase) then eventId (all lanes)
  Final tie-break: EventId (lexicographic comparison)

Note: connectionId and sequence are optional debug fields on Raw lane.
If present, they can provide additional ordering stability but are not required.

Architecture Invariants:
- Ordering is deterministic: same input → same output
- EventId tie-break is stable (lexicographic byte order)
- ordering.py generates SQL ORDER BY clauses; DB executes them via indexes
- Python comparators available for tests and rare non-SQL operations
"""

from typing import List, Dict, Any, Optional
from functools import cmp_to_key

from .events import Lane, Timebase
# Import architectural invariants from single source of truth
from .contract import LANE_PRIORITY


def buildOrderByClause(timebase: Timebase, lane: Optional[Lane] = None) -> str:
    """
    Build SQL ORDER BY clause for deterministic ordering.
    Single source of truth for ordering rules - DB executes via indexes.
    
    Args:
        timebase: Source or Canonical for primary time ordering
        lane: If specified, optimized ORDER BY for single-lane query
              If None, cross-lane ORDER BY with lane priority
    
    Returns:
        SQL ORDER BY clause string
    """
    timeField = "sourceTruthTime" if timebase == Timebase.SOURCE else "canonicalTruthTime"
    
    # All lanes: (time, eventId) - simple and deterministic
    # eventId is content-derived hash which provides stable ordering
    if lane is not None:
        return f"ORDER BY {timeField} ASC, eventId ASC"
    else:
        # Cross-lane: (time, lane_priority, eventId)
        # Lane priority: Metadata=0, Command=1, UI=2, Parsed=3, Raw=4
        return f"""ORDER BY {timeField} ASC,
                 CASE lane
                     WHEN 'metadata' THEN 0
                     WHEN 'command' THEN 1
                     WHEN 'ui' THEN 2
                     WHEN 'parsed' THEN 3
                     WHEN 'raw' THEN 4
                 END ASC,
                 eventId ASC"""


def compareEvents(a: Dict[str, Any], b: Dict[str, Any], timebase: Timebase) -> int:
    """
    Compare two events according to the ordering contract.
    
    Returns:
      -1 if a < b
       0 if a == b
       1 if a > b
    
    Args:
        a: First event (dict from database)
        b: Second event (dict from database)
        timebase: Source or Canonical for primary time comparison
        
    Returns:
        Comparison result
    """
    # Select timebase field
    timeField = "sourceTruthTime" if timebase == Timebase.SOURCE else "canonicalTruthTime"
    
    # Primary sort: time
    timeA = a[timeField]
    timeB = b[timeField]
    
    if timeA < timeB:
        return -1
    elif timeA > timeB:
        return 1
    
    # Time tie: sort by lane priority
    laneA = Lane(a['lane'])
    laneB = Lane(b['lane'])
    
    priorityA = LANE_PRIORITY[laneA]
    priorityB = LANE_PRIORITY[laneB]
    
    if priorityA < priorityB:
        return -1
    elif priorityA > priorityB:
        return 1
    
    # Same lane and same time: final tie-break with eventId
    # All lanes use eventId as the final deterministic tie-break
    
    # Final tie-break: eventId (lexicographic)
    eventIdA = a['eventId']
    eventIdB = b['eventId']
    
    if eventIdA < eventIdB:
        return -1
    elif eventIdA > eventIdB:
        return 1
    
    return 0  # Identical events (shouldn't happen with proper eventId)


def sortEvents(events: List[Dict[str, Any]], timebase: Timebase) -> List[Dict[str, Any]]:
    """
    Sort events according to the deterministic ordering contract.
    
    This is the canonical implementation used by all query/stream/export/TCP paths.
    
    Args:
        events: List of event dicts from database
        timebase: Source or Canonical for primary time ordering
        
    Returns:
        Sorted list of events (new list, input not modified)
    """
    # Create comparison key function
    compareFn = lambda a, b: compareEvents(a, b, timebase)
    
    # Sort events
    sortedEvents = sorted(events, key=cmp_to_key(compareFn))
    
    return sortedEvents


def validateOrdering(events: List[Dict[str, Any]], timebase: Timebase) -> bool:
    """
    Validate that events are correctly ordered.
    
    Used for testing and verification.
    
    Args:
        events: List of event dicts
        timebase: Source or Canonical
        
    Returns:
        True if correctly ordered, False otherwise
    """
    for i in range(len(events) - 1):
        cmp = compareEvents(events[i], events[i + 1], timebase)
        if cmp > 0:
            return False  # Out of order
    return True
