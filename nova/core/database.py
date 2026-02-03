"""
NOVA Truth Database Implementation

SQLite-based truth database with abstract interface for future DB swapping.
Implements append-only semantics, atomic dedupe, and time-indexed queries.

Architecture Invariants (nova architecture.md):
- Single truth DB per NOVA instance
- Append-only: corrections are new events, not overwrites
- Global dedupe via eventIndex table (cross-lane, cross-scope)
- Atomic dedupe + insert: transaction ensures no orphaned rows
- Two timebases: sourceTruthTime (never overwritten) + canonicalTruthTime (added at ingest)

Identity Model (nova architecture.md Section 3):
  Public/external identity is always: scopeId + lane + systemId + containerId + uniqueId
  - All lane tables include the entity identity triplet
  - connectionId, sequence are optional debug labels (Raw lane only)

Schema:
- eventIndex: Global dedupe table (eventId PK)
- rawEvents, parsedEvents, uiEvents, commandEvents, metadataEvents: Per-lane tables
- All lane tables reference eventIndex via FK
"""

import sqlite3
import json
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from sdk.logging import getLogger

# Import architectural invariants from single source of truth
from .contract import (
    Lane, Timebase,
    LANE_TABLE_NAMES
)
from .events import (
    Event,
    RawFrame, ParsedMessage, UiUpdate, 
    CommandRequest, CommandProgress, CommandResult, 
    MetadataEvent
)


class DatabaseError(Exception):
    """Database operation error"""
    pass


class Database:
    """
    SQLite truth database with abstract interface.
    
    Design: Keep DB-specific details isolated to enable future swapping.
    """
    
    def __init__(self, dbPath: str):
        """
        Initialize database connection.
        
        Args:
            dbPath: Path to SQLite database file
        """
        self.log = getLogger()
        self.dbPath = Path(dbPath)
        self.dbPath.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None
        self._readConn: Optional[sqlite3.Connection] = None  # Dedicated read connection
        self._writeLock = threading.Lock()  # Serialize writes only
        self._readLock = threading.Lock()  # Serialize read connection access
        self._connect()
        self._initSchema()
    
    def _connect(self):
        """Establish database connection with settings for high-throughput writes"""
        self.conn = sqlite3.connect(
            str(self.dbPath),
            check_same_thread=False,  # Allow multi-thread access (with caution)
            isolation_level='DEFERRED',  # Let Python manage transactions
            timeout=30.0  # Wait up to 30s for locks instead of failing immediately
        )
        self.conn.row_factory = sqlite3.Row  # Access columns by name
        
        # Configure for high-throughput writes (gigabytes of data)
        # WAL mode for better write concurrency
        self.conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL sync is safe in WAL mode, balances durability/performance
        self.conn.execute("PRAGMA synchronous=NORMAL")
        # Large cache for high throughput (negative = KB, -64000 = ~64MB cache)
        self.conn.execute("PRAGMA cache_size=-64000")
        # Disable auto-checkpoint - we checkpoint manually or on close
        # This prevents checkpoint stalls during heavy writes
        self.conn.execute("PRAGMA wal_autocheckpoint=0")
        # Memory-map up to 256MB of database for faster access
        self.conn.execute("PRAGMA mmap_size=268435456")
        # Temp tables in memory (faster)
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.commit()
    
    def _getReadConnection(self) -> sqlite3.Connection:
        """
        Get the dedicated read connection for queries.
        
        SQLite WAL mode allows concurrent reads with writes.
        Uses a single persistent read connection (with lock for cursor isolation).
        """
        if self._readConn is None:
            self._readConn = sqlite3.connect(
                str(self.dbPath),
                check_same_thread=False,
                timeout=30.0
            )
            self._readConn.row_factory = sqlite3.Row
            # Read-only optimizations
            self._readConn.execute("PRAGMA query_only=ON")
            self._readConn.execute("PRAGMA cache_size=-32000")  # 32MB cache for reads
            self._readConn.execute("PRAGMA mmap_size=268435456")  # Memory-map
        return self._readConn
    
    def _initSchema(self):
        """
        Initialize database schema with new identity model.
        
        Identity: systemId + containerId + uniqueId (all lanes)
        Optional debug: connectionId, sequence (Raw lane only)
        """
        cursor = self.conn.cursor()
        
        try:
            # Global dedupe table (cross-lane, cross-scope)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS eventIndex (
                    eventId TEXT PRIMARY KEY NOT NULL
                )
            """)
            
            # Raw lane: byte frames with entity identity
            rawTable = LANE_TABLE_NAMES[Lane.RAW]
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {rawTable} (
                    eventId TEXT PRIMARY KEY NOT NULL,
                    scopeId TEXT NOT NULL,
                    sourceTruthTime TEXT NOT NULL,
                    canonicalTruthTime TEXT NOT NULL,
                    systemId TEXT NOT NULL,
                    containerId TEXT NOT NULL,
                    uniqueId TEXT NOT NULL,
                    bytes BLOB NOT NULL,
                    connectionId TEXT,
                    sequence INTEGER,
                    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
                )
            """)
            # Indexes for ORDER BY clauses per ordering.py contract
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{rawTable}_source_order 
                ON {rawTable}(sourceTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{rawTable}_canonical_order
                ON {rawTable}(canonicalTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{rawTable}_entity
                ON {rawTable}(systemId, containerId, uniqueId)
            """)
            # Composite index for TCP stream queries (entity + time range)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{rawTable}_entity_canonical
                ON {rawTable}(systemId, containerId, uniqueId, canonicalTruthTime)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{rawTable}_entity_source
                ON {rawTable}(systemId, containerId, uniqueId, sourceTruthTime)
            """)
            
            # Parsed lane: typed messages with entity identity
            parsedTable = LANE_TABLE_NAMES[Lane.PARSED]
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {parsedTable} (
                    eventId TEXT PRIMARY KEY NOT NULL,
                    scopeId TEXT NOT NULL,
                    sourceTruthTime TEXT NOT NULL,
                    canonicalTruthTime TEXT NOT NULL,
                    systemId TEXT NOT NULL,
                    containerId TEXT NOT NULL,
                    uniqueId TEXT NOT NULL,
                    messageType TEXT NOT NULL,
                    schemaVersion TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
                )
            """)
            # Indexes for ORDER BY clauses per ordering.py contract
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{parsedTable}_source_order
                ON {parsedTable}(sourceTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{parsedTable}_canonical_order
                ON {parsedTable}(canonicalTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{parsedTable}_entity
                ON {parsedTable}(systemId, containerId, uniqueId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{parsedTable}_messageType
                ON {parsedTable}(messageType)
            """)
            # Composite index for entity + time range queries
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{parsedTable}_entity_canonical
                ON {parsedTable}(systemId, containerId, uniqueId, canonicalTruthTime)
            """)
            
            # UI lane: partial upserts with entity identity
            uiTable = LANE_TABLE_NAMES[Lane.UI]
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {uiTable} (
                    eventId TEXT PRIMARY KEY NOT NULL,
                    scopeId TEXT NOT NULL,
                    sourceTruthTime TEXT NOT NULL,
                    canonicalTruthTime TEXT NOT NULL,
                    systemId TEXT NOT NULL,
                    containerId TEXT NOT NULL,
                    uniqueId TEXT NOT NULL,
                    messageType TEXT NOT NULL,
                    viewId TEXT NOT NULL,
                    manifestId TEXT NOT NULL,
                    manifestVersion TEXT NOT NULL,
                    data TEXT NOT NULL,
                    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
                )
            """)
            # Indexes for ORDER BY clauses per ordering.py contract
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{uiTable}_source_order
                ON {uiTable}(sourceTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{uiTable}_canonical_order
                ON {uiTable}(canonicalTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{uiTable}_entity
                ON {uiTable}(systemId, containerId, uniqueId, viewId)
            """)
            
            # Command lane: requests/progress/results with entity identity
            commandTable = LANE_TABLE_NAMES[Lane.COMMAND]
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {commandTable} (
                    eventId TEXT PRIMARY KEY NOT NULL,
                    scopeId TEXT NOT NULL,
                    sourceTruthTime TEXT NOT NULL,
                    canonicalTruthTime TEXT NOT NULL,
                    systemId TEXT NOT NULL,
                    containerId TEXT NOT NULL,
                    uniqueId TEXT NOT NULL,
                    messageType TEXT NOT NULL,
                    commandId TEXT NOT NULL,
                    requestId TEXT,
                    targetId TEXT NOT NULL,
                    commandType TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
                )
            """)
            # Indexes for ORDER BY clauses per ordering.py contract
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{commandTable}_source_order
                ON {commandTable}(sourceTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{commandTable}_canonical_order
                ON {commandTable}(canonicalTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{commandTable}_commandId
                ON {commandTable}(commandId)
            """)
            cursor.execute(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{commandTable}_requestId_unique
                ON {commandTable}(requestId) WHERE messageType = 'CommandRequest' AND requestId IS NOT NULL
            """)
            
            # Metadata lane: time-versioned descriptors with entity identity
            metadataTable = LANE_TABLE_NAMES[Lane.METADATA]
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {metadataTable} (
                    eventId TEXT PRIMARY KEY NOT NULL,
                    scopeId TEXT NOT NULL,
                    sourceTruthTime TEXT NOT NULL,
                    canonicalTruthTime TEXT NOT NULL,
                    systemId TEXT,
                    containerId TEXT,
                    uniqueId TEXT,
                    messageType TEXT NOT NULL,
                    effectiveTime TEXT NOT NULL,
                    manifestId TEXT,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (eventId) REFERENCES eventIndex(eventId)
                )
            """)
            # Indexes for ORDER BY clauses per ordering.py contract
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{metadataTable}_source_order
                ON {metadataTable}(sourceTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{metadataTable}_canonical_order
                ON {metadataTable}(canonicalTruthTime, eventId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{metadataTable}_entity
                ON {metadataTable}(systemId, containerId, uniqueId)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{metadataTable}_manifest
                ON {metadataTable}(manifestId, messageType)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{metadataTable}_effective
                ON {metadataTable}(effectiveTime)
            """)
            
            self.conn.commit()
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Schema initialization failed: {e}")
        finally:
            cursor.close()
    
    def insertEvent(self, event: Event, canonicalTruthTime: str) -> bool:
        """
        Insert event with atomic dedupe.
        
        Transaction ensures eventIndex + lane table are updated atomically.
        On duplicate eventId: transaction fails, returns False (dedupe).
        On success: both tables updated, returns True.
        
        Args:
            event: Event to insert
            canonicalTruthTime: Wall-clock receive time at this NOVA instance
            
        Returns:
            True if inserted, False if duplicate (deduped)
            
        Raises:
            DatabaseError: On database errors (not dedupe)
        """
        with self._writeLock:
            cursor = self.conn.cursor()
            
            try:
                # Insert into global dedupe table first
                cursor.execute(
                    "INSERT INTO eventIndex (eventId) VALUES (?)",
                    (event.eventId,)
                )
                
                # Insert into lane-specific table
                if event.lane == Lane.RAW:
                    cursor.execute("""
                        INSERT INTO rawEvents (
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, bytes,
                            connectionId, sequence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.eventId,
                        event.scopeId,
                        event.sourceTruthTime,
                        canonicalTruthTime,
                        event.systemId,
                        event.containerId,
                        event.uniqueId,
                        event.bytesData,
                        event.connectionId,
                        event.sequence
                    ))
                
                elif event.lane == Lane.PARSED:
                    cursor.execute("""
                        INSERT INTO parsedEvents (
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            schemaVersion, payload
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.eventId,
                        event.scopeId,
                        event.sourceTruthTime,
                        canonicalTruthTime,
                        event.systemId,
                        event.containerId,
                        event.uniqueId,
                        event.messageType,
                        event.schemaVersion,
                        json.dumps(event.payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                    ))
                
                elif event.lane == Lane.UI:
                    cursor.execute("""
                        INSERT INTO uiEvents (
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            viewId, manifestId, manifestVersion, data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.eventId,
                        event.scopeId,
                        event.sourceTruthTime,
                        canonicalTruthTime,
                        event.systemId,
                        event.containerId,
                        event.uniqueId,
                        event.messageType,
                        event.viewId,
                        event.manifestId,
                        event.manifestVersion,
                        json.dumps(event.data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                    ))
                
                elif event.lane == Lane.COMMAND:
                    cursor.execute("""
                        INSERT INTO commandEvents (
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            commandId, requestId, targetId, commandType, payload
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.eventId,
                        event.scopeId,
                        event.sourceTruthTime,
                        canonicalTruthTime,
                        event.systemId,
                        event.containerId,
                        event.uniqueId,
                        event.messageType,
                        event.commandId,
                        getattr(event, 'requestId', None),
                        event.targetId,
                        event.commandType,
                        json.dumps(event.payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                    ))
                
                elif event.lane == Lane.METADATA:
                    cursor.execute("""
                        INSERT INTO metadataEvents (
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            effectiveTime, manifestId, payload
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.eventId,
                        event.scopeId,
                        event.sourceTruthTime,
                        canonicalTruthTime,
                        event.systemId,
                        event.containerId,
                        event.uniqueId,
                        event.messageType,
                        event.effectiveTime,
                        event.manifestId,
                        json.dumps(event.payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                    ))
                
                else:
                    self.conn.rollback()
                    raise DatabaseError(f"Unknown lane: {event.lane}")
                
                self.conn.commit()
                return True
                
            except sqlite3.IntegrityError as e:
                # Dedupe: eventId or requestId already exists
                self.conn.rollback()
                errStr = str(e)
                # Handle duplicate eventId (eventIndex) or duplicate requestId (CommandRequest)
                if "eventIndex" in errStr or "eventId" in errStr or "requestId" in errStr or "UNIQUE constraint" in errStr:
                    return False  # Duplicate, this is expected (idempotent insert)
                raise DatabaseError(f"Integrity error: {e}")
            
            except sqlite3.Error as e:
                self.conn.rollback()
                errStr = str(e)
                # Handle duplicate eventId or requestId as dedupe
                if "UNIQUE constraint" in errStr:
                    return False  # Dedupe
                self.log.error(f'[Database] sqlite3.Error in insertEvent: {errStr}',
                              eventId=event.eventId[:16] if event.eventId else 'none',
                              lane=event.lane.value if event.lane else 'none')
                raise DatabaseError(f"Insert failed: {e}")
            finally:
                cursor.close()

    def queryEvents(
        self,
        startTime: str,
        stopTime: str,
        timebase: Timebase,
        scopeIds: Optional[List[str]] = None,
        lanes: Optional[List[Lane]] = None,
        systemId: Optional[str] = None,
        containerId: Optional[str] = None,
        uniqueId: Optional[str] = None,
        viewId: Optional[str] = None,
        messageType: Optional[str] = None,
        manifestId: Optional[str] = None,
        commandId: Optional[str] = None,
        commandType: Optional[str] = None,
        requestId: Optional[str] = None,
        limit: Optional[int] = None,
        ingestOrder: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Query events with time range and filters.
        
        Returns ordered rows per ordering.py contract (DB executes ORDER BY).
        
        Identity Model (nova architecture.md Section 3):
          Filters use universal entity identity: systemId, containerId, uniqueId
        
        Args:
            startTime: ISO8601 start time (inclusive)
            stopTime: ISO8601 stop time (inclusive)
            timebase: Source or Canonical for time filtering
            scopeIds: Filter by scope IDs
            lanes: Filter by lanes
            systemId: Filter by systemId (data system)
            containerId: Filter by containerId (node/payload)
            uniqueId: Filter by uniqueId (entity identifier)
            viewId: Filter UI by viewId
            messageType: Filter by messageType
            manifestId: Filter Metadata/UI by manifestId
            commandId: Filter Command by commandId
            commandType: Filter Command by commandType
            requestId: Filter Command by requestId
            limit: Max results (applied per-lane before merging)
            ingestOrder: If True, order by rowid (ingest order) for export parity.
                         If False, use timebase ordering per ordering.py contract.
            
        Returns:
            List of event dicts with lane, timebase times, and all fields
        """
        # Performance tracking
        queryStart = time.perf_counter()
        
        # Use dedicated read connection with lock for cursor isolation
        # SQLite WAL mode supports concurrent reads with writes
        with self._readLock:
            readConn = self._getReadConnection()
            timeField = "sourceTruthTime" if timebase == Timebase.SOURCE else "canonicalTruthTime"
            results = []
            
            # Default to all lanes if not specified
            if lanes is None:
                lanes = list(Lane)
            
            # Build scope filter
            scopeFilter = ""
            scopeParams = []
            if scopeIds:
                placeholders = ','.join('?' * len(scopeIds))
                scopeFilter = f" AND scopeId IN ({placeholders})"
                scopeParams = scopeIds
            
            # Build entity filter
            def buildEntityFilter(params):
                filter_parts = []
                if systemId:
                    filter_parts.append(" AND systemId = ?")
                    params.append(systemId)
                if containerId:
                    filter_parts.append(" AND containerId = ?")
                    params.append(containerId)
                if uniqueId:
                    filter_parts.append(" AND uniqueId = ?")
                    params.append(uniqueId)
                return ''.join(filter_parts)
            
            cursor = readConn.cursor()
            
            try:
                # Query each lane with ORDER BY per ordering contract
                from . import ordering
                
                # Helper: get ORDER BY clause
                # TWO SEPARATE ORDERING CONTRACTS:
                #   1. Global Truth Ordering (default): timebase + lane priority + eventId
                #      Used for: queries, streaming, UI display
                #   2. File Parity Ordering (ingestOrder=True): rowid (insertion order)
                #      Used for: export file generation to match real-time FileWriter output
                # These are NOT interchangeable - see phase6Summary.md for details.
                def getOrderByClause(lane: Lane) -> str:
                    if ingestOrder:
                        # File Parity Sub-Contract: match real-time FileWriter order
                        return "ORDER BY rowid ASC"
                    # Global Truth Contract: deterministic timebase ordering
                    return ordering.buildOrderByClause(timebase, lane)
                
                if Lane.RAW in lanes:
                    orderByClause = getOrderByClause(Lane.RAW)
                    query = f"""
                        SELECT 
                            'raw' as lane,
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, bytes,
                            connectionId, sequence
                        FROM rawEvents
                        WHERE {timeField} >= ? AND {timeField} <= ?
                        {scopeFilter}
                    """
                    params = [startTime, stopTime] + scopeParams
                    query += buildEntityFilter(params)
                    query += f" {orderByClause}"
                    
                    if limit:
                        query += f" LIMIT ?"
                        params.append(limit)
                    
                    cursor.execute(query, params)
                    for row in cursor.fetchall():
                        results.append(dict(row))
                
                if Lane.PARSED in lanes:
                    orderByClause = getOrderByClause(Lane.PARSED)
                    query = f"""
                        SELECT
                            'parsed' as lane,
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            schemaVersion, payload
                        FROM parsedEvents
                        WHERE {timeField} >= ? AND {timeField} <= ?
                        {scopeFilter}
                    """
                    params = [startTime, stopTime] + scopeParams
                    query += buildEntityFilter(params)
                    
                    if messageType:
                        query += " AND messageType = ?"
                        params.append(messageType)
                    
                    query += f" {orderByClause}"
                    
                    if limit:
                        query += f" LIMIT ?"
                        params.append(limit)
                    
                    cursor.execute(query, params)
                    for row in cursor.fetchall():
                        result = dict(row)
                        result['payload'] = json.loads(result['payload'])
                        results.append(result)
                
                if Lane.UI in lanes:
                    orderByClause = getOrderByClause(Lane.UI)
                    query = f"""
                        SELECT
                            'ui' as lane,
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            viewId, manifestId, manifestVersion, data
                        FROM uiEvents
                        WHERE {timeField} >= ? AND {timeField} <= ?
                        {scopeFilter}
                    """
                    params = [startTime, stopTime] + scopeParams
                    query += buildEntityFilter(params)
                    
                    if viewId:
                        query += " AND viewId = ?"
                        params.append(viewId)
                    
                    if manifestId:
                        query += " AND manifestId = ?"
                        params.append(manifestId)
                    
                    query += f" {orderByClause}"
                    
                    if limit:
                        query += f" LIMIT ?"
                        params.append(limit)
                    
                    cursor.execute(query, params)
                    for row in cursor.fetchall():
                        result = dict(row)
                        result['data'] = json.loads(result['data'])
                        results.append(result)
                
                if Lane.COMMAND in lanes:
                    orderByClause = getOrderByClause(Lane.COMMAND)
                    query = f"""
                        SELECT
                            'command' as lane,
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            commandId, requestId, targetId, commandType, payload
                        FROM commandEvents
                        WHERE {timeField} >= ? AND {timeField} <= ?
                        {scopeFilter}
                    """
                    params = [startTime, stopTime] + scopeParams
                    query += buildEntityFilter(params)
                    
                    if commandId:
                        query += " AND commandId = ?"
                        params.append(commandId)
                    
                    if commandType:
                        query += " AND commandType = ?"
                        params.append(commandType)
                    
                    if requestId:
                        query += " AND requestId = ?"
                        params.append(requestId)
                
                    query += f" {orderByClause}"
                    
                    if limit:
                        query += f" LIMIT ?"
                        params.append(limit)
                    
                    cursor.execute(query, params)
                    for row in cursor.fetchall():
                        result = dict(row)
                        result['payload'] = json.loads(result['payload'])
                        results.append(result)
                
                if Lane.METADATA in lanes:
                    orderByClause = getOrderByClause(Lane.METADATA)
                    query = f"""
                        SELECT
                            'metadata' as lane,
                            eventId, scopeId, sourceTruthTime, canonicalTruthTime,
                            systemId, containerId, uniqueId, messageType,
                            effectiveTime, manifestId, payload
                        FROM metadataEvents
                        WHERE {timeField} >= ? AND {timeField} <= ?
                        {scopeFilter}
                    """
                    params = [startTime, stopTime] + scopeParams
                    query += buildEntityFilter(params)
                    
                    if manifestId:
                        query += " AND manifestId = ?"
                        params.append(manifestId)
                    
                    if messageType:
                        query += " AND messageType = ?"
                        params.append(messageType)
                    
                    query += f" {orderByClause}"
                    
                    if limit:
                        query += f" LIMIT ?"
                        params.append(limit)
                    
                    cursor.execute(query, params)
                    for row in cursor.fetchall():
                        result = dict(row)
                        result['payload'] = json.loads(result['payload'])
                        results.append(result)
                
                # Cross-lane ordering
                # When ingestOrder=True: skip cross-lane sort, preserve per-lane rowid order
                #   (used for file/export parity where we need to match real-time FileWriter order)
                # When ingestOrder=False: sort by (timebase, lane_priority, eventId)
                #   (Global Truth Contract for queries, streaming, UI)
                if len(results) > 1 and not ingestOrder:
                    results.sort(key=lambda e: (
                        e['sourceTruthTime'] if timebase == Timebase.SOURCE else e['canonicalTruthTime'],
                        ordering.LANE_PRIORITY.get(Lane(e['lane']), 999),
                        e['eventId']
                    ))
                
                # Performance logging
                queryMs = (time.perf_counter() - queryStart) * 1000
                if len(results) > 0 or queryMs > 50:  # Log if results or slow query
                    self.log.debug(f"[Database] queryEvents: {len(results)} events in {queryMs:.1f}ms "
                                   f"[{startTime[:19]}→{stopTime[:19]}] lanes={[l.value for l in lanes]}")
                
                return results
                
            except sqlite3.Error as e:
                raise DatabaseError(f"Query failed: {e}")
            finally:
                cursor.close()

    def insertCommandEvent(self, commandEvent: Dict[str, Any]) -> bool:
        """
        Insert command event with convenience dict interface.
        
        Args:
            commandEvent: Dict with event fields
        
        Returns:
            True if inserted, False if duplicate (idempotency)
        """
        event = CommandRequest(
            eventId=commandEvent['eventId'],
            scopeId=commandEvent['scopeId'],
            sourceTruthTime=commandEvent['sourceTruthTime'],
            messageType=commandEvent['messageType'],
            systemId=commandEvent['systemId'],
            containerId=commandEvent['containerId'],
            uniqueId=commandEvent['uniqueId'],
            commandId=commandEvent['commandId'],
            requestId=commandEvent.get('requestId'),
            targetId=commandEvent['targetId'],
            commandType=commandEvent['commandType'],
            payload=commandEvent['payload']
        )
        canonicalTruthTime = datetime.now().isoformat() + 'Z'
        return self.insertEvent(event, canonicalTruthTime)
    
    def queryCommands(
        self, 
        commandId: Optional[str] = None, 
        requestId: Optional[str] = None, 
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query command events with convenience filters.
        
        Args:
            commandId: Filter by commandId
            requestId: Filter by requestId
            limit: Max results
            
        Returns:
            List of command event dicts
        """
        # Use a wide time range for convenience queries
        return self.queryEvents(
            startTime="1970-01-01T00:00:00Z",
            stopTime="2100-01-01T00:00:00Z",
            timebase=Timebase.SOURCE,
            lanes=[Lane.COMMAND],
            commandId=commandId,
            requestId=requestId,
            limit=limit
        )
    
    def checkpoint(self, mode: str = 'PASSIVE') -> tuple:
        """
        Manually checkpoint the WAL file.
        
        Since wal_autocheckpoint is disabled for high-throughput writes,
        call this periodically (e.g., during idle time or on a schedule).
        
        Args:
            mode: PASSIVE (default, non-blocking), FULL, RESTART, or TRUNCATE
            
        Returns:
            Tuple of (blocked, log_pages, checkpointed_pages)
            - blocked: 1 if checkpoint was blocked, 0 otherwise  
            - log_pages: pages in WAL before checkpoint
            - checkpointed_pages: pages moved back to database
        """
        if not self.conn:
            return (1, -1, -1)
        
        with self._writeLock:
            try:
                cursor = self.conn.execute(f"PRAGMA wal_checkpoint({mode})")
                result = cursor.fetchone()
                cursor.close()
                return tuple(result) if result else (1, -1, -1)
            except sqlite3.Error as e:
                self.log.warning(f'[Database] Checkpoint failed: {e}')
                return (1, -1, -1)
    
    def close(self):
        """Close database connections with final checkpoint"""
        # Close read connection first
        if self._readConn:
            with self._readLock:
                try:
                    self._readConn.close()
                except sqlite3.Error:
                    pass
                self._readConn = None
        
        # Close write connection with checkpoint
        if self.conn:
            with self._writeLock:
                # Do a TRUNCATE checkpoint on close to clean up WAL file
                try:
                    self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass  # Best effort - connection is closing anyway
                self.conn.close()
                self.conn = None
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
