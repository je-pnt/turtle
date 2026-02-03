"""
NOVA Query Implementation

Bounded read implementation for time windows [T0..T1].
Returns deterministically ordered events.

Architecture Invariants (nova architecture.md):
- Query is read-only (no side effects)
- Ordering via ordering.py SQL ORDER BY (DB executes, not Python)
- Timebase selection (Source or Canonical)
- No persistent per-client state
- Query does NOT trigger fileWriter (hard prohibition)

Identity Model (nova architecture.md Section 3):
  Public identity is always: scopeId + lane + systemId + containerId + uniqueId
  Filters can specify any combination of these identity fields.

Query Flow:
  1. Validate inputs (time range, timebase, filters)
  2. Database.queryEvents() returns ordered rows (via SQL ORDER BY)
  3. Apply limit if needed
  4. Return ordered results
"""

from typing import List, Dict, Any, Optional
from datetime import datetime

from .database import Database
from .events import Lane, Timebase


class QueryError(Exception):
    """Query validation or execution error"""
    pass


class Query:
    """
    Bounded read query for time windows.
    
    Returns deterministically ordered events for [startTime..stopTime].
    """
    
    def __init__(self, database: Database):
        """
        Initialize query handler.
        
        Args:
            database: Database instance
        """
        self.database = database
    
    def query(
        self,
        startTime: str,
        stopTime: str,
        timebase: Timebase = Timebase.CANONICAL,
        scopeIds: Optional[List[str]] = None,
        lanes: Optional[List[Lane]] = None,
        # Entity identity filters (nova architecture.md Section 3)
        systemId: Optional[str] = None,
        containerId: Optional[str] = None,
        uniqueId: Optional[str] = None,
        # Lane-specific filters (optional)
        messageType: Optional[str] = None,  # Metadata/Command
        viewId: Optional[str] = None,  # UI
        manifestId: Optional[str] = None,  # UI/Metadata
        commandType: Optional[str] = None,  # Command
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute bounded read query.
        
        Args:
            startTime: ISO8601 start time (inclusive)
            stopTime: ISO8601 stop time (inclusive)
            timebase: Source or Canonical for ordering
            scopeIds: Filter by scope IDs (None = all scopes)
            lanes: Filter by lanes (None = all lanes)
            systemId: Filter by data system (e.g., hardwareService, adsb)
            containerId: Filter by node/payload instance
            uniqueId: Filter by entity identifier
            messageType: Filter Metadata/Command by messageType
            viewId: Filter UI by viewId
            manifestId: Filter UI/Metadata by manifestId
            commandType: Filter Command by commandType
            limit: Max results (applied AFTER ordering)
            
        Returns:
            List of ordered event dicts
            
        Raises:
            QueryError: On validation or execution failure
        """
        # Validate inputs
        self._validate(startTime, stopTime, timebase)
        
        # Query database (returns ordered rows via SQL ORDER BY)
        try:
            events = self.database.queryEvents(
                startTime=startTime,
                stopTime=stopTime,
                timebase=timebase,
                scopeIds=scopeIds,
                lanes=lanes,
                systemId=systemId,
                containerId=containerId,
                uniqueId=uniqueId,
                messageType=messageType,
                viewId=viewId,
                manifestId=manifestId,
                commandType=commandType,
                limit=limit  # DB can apply limit with ORDER BY
            )
        except Exception as e:
            raise QueryError(f"Database query failed: {e}")
        
        # DB returns ordered results - no Python sorting needed
        return events
    
    def _validate(self, startTime: str, stopTime: str, timebase: Timebase):
        """
        Validate query inputs.
        
        Args:
            startTime: ISO8601 start time
            stopTime: ISO8601 stop time
            timebase: Timebase enum
            
        Raises:
            QueryError: If validation fails
        """
        # Validate time format
        try:
            start = datetime.fromisoformat(startTime.replace('Z', '+00:00'))
            stop = datetime.fromisoformat(stopTime.replace('Z', '+00:00'))
        except ValueError as e:
            raise QueryError(f"Invalid time format: {e}")
        
        # Validate time ordering
        if start > stop:
            raise QueryError("startTime must be <= stopTime")
        
        # Validate timebase
        if not isinstance(timebase, Timebase):
            raise QueryError(f"Invalid timebase: {timebase}")
