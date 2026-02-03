"""
NOVA Stream Store - SQLite-based stream definition CRUD.

Architecture (Phase 8.1):
- Stream definitions are operational config, NOT truth events
- Stored in streams.db (separate from nova_truth.db)
- Definitions persist across server restarts
- Sessions/connections are ephemeral (memory only)

Property of Uncompromising Sensors LLC.
"""

import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

from sdk.logging import getLogger


@dataclass 
class StreamDefinition:
    """
    Stream definition (persisted config).
    
    NOT a truth event - operational config stored in streams.db.
    Supports tcp, websocket, and udp protocols.
    """
    streamId: str
    name: str
    
    # Protocol and endpoint
    protocol: str = "tcp"  # tcp|websocket|udp
    endpoint: str = ""     # port number (tcp/udp) or path (websocket)
    
    # Selection (what to stream)
    lane: str = "raw"  # raw|parsed|metadata|ui
    systemIdFilter: Optional[str] = None
    containerIdFilter: Optional[str] = None
    uniqueIdFilter: Optional[str] = None
    messageTypeFilter: Optional[str] = None
    
    # Output format
    outputFormat: str = "payloadOnly"  # payloadOnly|hierarchyPerMessage
    
    # Backpressure
    backpressure: str = "catchUp"  # catchUp|disconnect
    
    # State
    enabled: bool = True
    
    # Ownership (Phase 9 ready)
    createdBy: str = "system"
    visibility: str = "private"  # private|public
    
    # Timestamps
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    @classmethod
    def fromDict(cls, data: Dict[str, Any]) -> 'StreamDefinition':
        """Create from dictionary"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def selectionSummary(self) -> str:
        """Human-readable selection summary"""
        parts = [self.lane]
        if self.systemIdFilter:
            parts.append(f"sys={self.systemIdFilter}")
        if self.containerIdFilter:
            parts.append(f"cont={self.containerIdFilter}")
        if self.uniqueIdFilter:
            parts.append(f"uniq={self.uniqueIdFilter}")
        if self.messageTypeFilter:
            parts.append(f"type={self.messageTypeFilter}")
        return " | ".join(parts)
    
    def isSingleIdentity(self) -> bool:
        """Check if selection resolves to single identity (for payloadOnly validation)"""
        # Single identity requires all three identity filters to be set
        return bool(self.systemIdFilter and self.containerIdFilter and self.uniqueIdFilter)


class StreamStore:
    """
    SQLite-based stream definition storage.
    
    Separate from truth DB - this is operational config.
    """
    
    def __init__(self, dbPath: Optional[Path] = None):
        self.log = getLogger()
        
        # Default path: nova/data/streams.db
        if dbPath is None:
            dbPath = Path(__file__).parent.parent / 'data' / 'streams.db'
        
        self.dbPath = dbPath
        self._ensureDatabase()
    
    def _ensureDatabase(self):
        """Create database and tables if needed, migrate schema if necessary"""
        self.dbPath.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(str(self.dbPath))
        try:
            # Check if we need to migrate from old schema
            cursor = conn.execute("PRAGMA table_info(streams)")
            columns = {row[1] for row in cursor.fetchall()}
            
            if 'port' in columns and 'protocol' not in columns:
                # Migrate: rename port to endpoint, add protocol
                self.log.info("[StreamStore] Migrating schema to multi-protocol...")
                conn.execute('ALTER TABLE streams RENAME COLUMN port TO endpoint')
                conn.execute("ALTER TABLE streams ADD COLUMN protocol TEXT NOT NULL DEFAULT 'tcp'")
                conn.commit()
                self.log.info("[StreamStore] Schema migration complete")
            elif not columns:
                # Fresh database - create with new schema
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS streams (
                        streamId TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        protocol TEXT NOT NULL DEFAULT 'tcp',
                        endpoint TEXT NOT NULL,
                        lane TEXT NOT NULL DEFAULT 'raw',
                        systemIdFilter TEXT,
                        containerIdFilter TEXT,
                        uniqueIdFilter TEXT,
                        messageTypeFilter TEXT,
                        outputFormat TEXT NOT NULL DEFAULT 'payloadOnly',
                        backpressure TEXT NOT NULL DEFAULT 'catchUp',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        createdBy TEXT NOT NULL DEFAULT 'system',
                        visibility TEXT NOT NULL DEFAULT 'private',
                        createdAt TEXT NOT NULL,
                        updatedAt TEXT NOT NULL,
                        UNIQUE(protocol, endpoint)
                    )
                ''')
                conn.commit()
            
            self.log.info(f"[StreamStore] Database ready at {self.dbPath}")
        finally:
            conn.close()
    
    def create(self, definition: StreamDefinition) -> StreamDefinition:
        """
        Create a new stream definition.
        
        Raises ValueError on validation failure or duplicate endpoint.
        """
        # Validate protocol
        validProtocols = ('tcp', 'websocket', 'udp')
        if definition.protocol not in validProtocols:
            raise ValueError(f"Invalid protocol '{definition.protocol}'. Must be one of: {validProtocols}")
        
        # Validate endpoint based on protocol
        if definition.protocol == 'tcp':
            try:
                port = int(definition.endpoint)
                if port <= 80:
                    raise ValueError("Port must be greater than 80")
            except ValueError:
                raise ValueError("Port must be a valid number greater than 80")
        elif definition.protocol == 'udp':
            # UDP endpoint is host:port or just port (defaults to localhost)
            endpoint = definition.endpoint
            if ':' in endpoint:
                host, portStr = endpoint.rsplit(':', 1)
                try:
                    port = int(portStr)
                    if port <= 0 or port > 65535:
                        raise ValueError("Port must be 1-65535")
                except ValueError:
                    raise ValueError("UDP endpoint must be host:port or just port number")
            else:
                try:
                    port = int(endpoint)
                    if port <= 0 or port > 65535:
                        raise ValueError("Port must be 1-65535")
                except ValueError:
                    raise ValueError("UDP endpoint must be host:port or just port number")
        elif definition.protocol == 'websocket':
            if not definition.endpoint or not definition.endpoint.replace('-', '').replace('_', '').isalnum():
                raise ValueError("WebSocket path must be alphanumeric (with - or _ allowed)")
        
        # Validate lane
        validLanes = ('raw', 'parsed', 'metadata', 'ui', 'command')
        if not definition.lane or definition.lane not in validLanes:
            raise ValueError(f"Invalid lane '{definition.lane}'. Must be one of: {validLanes}")
        
        # Validate payloadOnly requires single identity
        if definition.outputFormat == "payloadOnly" and not definition.isSingleIdentity():
            raise ValueError("payloadOnly format requires single identity (all filters set)")
        
        # Set timestamps
        now = datetime.now(timezone.utc).isoformat()
        definition.createdAt = now
        definition.updatedAt = now
        
        # Generate ID if not set
        if not definition.streamId:
            definition.streamId = str(uuid.uuid4())[:8]
        
        conn = sqlite3.connect(str(self.dbPath))
        try:
            conn.execute('''
                INSERT INTO streams (
                    streamId, name, protocol, endpoint, lane,
                    systemIdFilter, containerIdFilter, uniqueIdFilter, messageTypeFilter,
                    outputFormat, backpressure, enabled,
                    createdBy, visibility, createdAt, updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                definition.streamId, definition.name, definition.protocol, definition.endpoint,
                definition.lane, definition.systemIdFilter, definition.containerIdFilter, 
                definition.uniqueIdFilter, definition.messageTypeFilter,
                definition.outputFormat, definition.backpressure, int(definition.enabled),
                definition.createdBy, definition.visibility, 
                definition.createdAt, definition.updatedAt
            ))
            conn.commit()
            
            if definition.protocol == 'websocket':
                self.log.info(f"[StreamStore] Created stream {definition.streamId}: {definition.name} at /ws/streams/{definition.endpoint}")
            else:
                self.log.info(f"[StreamStore] Created stream {definition.streamId}: {definition.name} on port {definition.endpoint}")
            return definition
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                if definition.protocol == 'websocket':
                    raise ValueError(f"WebSocket path '{definition.endpoint}' is already in use")
                else:
                    raise ValueError(f"Port {definition.endpoint} is already in use by another {definition.protocol.upper()} stream")
            raise
        finally:
            conn.close()
    
    def get(self, streamId: str) -> Optional[StreamDefinition]:
        """Get stream definition by ID"""
        conn = sqlite3.connect(str(self.dbPath))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                'SELECT * FROM streams WHERE streamId = ?', 
                (streamId,)
            ).fetchone()
            if row:
                return self._rowToDefinition(row)
            return None
        finally:
            conn.close()
    
    def getByEndpoint(self, protocol: str, endpoint: str) -> Optional[StreamDefinition]:
        """Get stream definition by protocol and endpoint"""
        conn = sqlite3.connect(str(self.dbPath))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                'SELECT * FROM streams WHERE protocol = ? AND endpoint = ?', 
                (protocol, endpoint)
            ).fetchone()
            if row:
                return self._rowToDefinition(row)
            return None
        finally:
            conn.close()
    
    def list(self) -> List[StreamDefinition]:
        """List all stream definitions"""
        conn = sqlite3.connect(str(self.dbPath))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute('SELECT * FROM streams ORDER BY name').fetchall()
            return [self._rowToDefinition(row) for row in rows]
        finally:
            conn.close()
    
    def update(self, definition: StreamDefinition) -> StreamDefinition:
        """
        Update an existing stream definition.
        
        Raises ValueError on validation failure or endpoint conflict.
        """
        # Validate protocol
        validProtocols = ('tcp', 'websocket', 'udp')
        if definition.protocol not in validProtocols:
            raise ValueError(f"Invalid protocol '{definition.protocol}'")
        
        # Validate endpoint
        if definition.protocol in ('tcp', 'udp'):
            try:
                port = int(definition.endpoint)
                if port <= 80:
                    raise ValueError("Port must be greater than 80")
            except ValueError:
                raise ValueError("Port must be a valid number")
        
        # Validate payloadOnly requires single identity
        if definition.outputFormat == "payloadOnly" and not definition.isSingleIdentity():
            raise ValueError("payloadOnly format requires single identity (all filters set)")
        
        # Check endpoint conflict
        existing = self.getByEndpoint(definition.protocol, definition.endpoint)
        if existing and existing.streamId != definition.streamId:
            raise ValueError(f"Endpoint {definition.endpoint} is already in use")
        
        # Update timestamp
        definition.updatedAt = datetime.now(timezone.utc).isoformat()
        
        conn = sqlite3.connect(str(self.dbPath))
        try:
            conn.execute('''
                UPDATE streams SET
                    name = ?, protocol = ?, endpoint = ?, lane = ?,
                    systemIdFilter = ?, containerIdFilter = ?, 
                    uniqueIdFilter = ?, messageTypeFilter = ?,
                    outputFormat = ?, backpressure = ?, enabled = ?,
                    createdBy = ?, visibility = ?, updatedAt = ?
                WHERE streamId = ?
            ''', (
                definition.name, definition.protocol, definition.endpoint, definition.lane,
                definition.systemIdFilter, definition.containerIdFilter,
                definition.uniqueIdFilter, definition.messageTypeFilter,
                definition.outputFormat, definition.backpressure, int(definition.enabled),
                definition.createdBy, definition.visibility, definition.updatedAt,
                definition.streamId
            ))
            conn.commit()
            self.log.info(f"[StreamStore] Updated stream {definition.streamId}")
            return definition
        finally:
            conn.close()
    
    def delete(self, streamId: str) -> bool:
        """Delete a stream definition"""
        conn = sqlite3.connect(str(self.dbPath))
        try:
            cursor = conn.execute('DELETE FROM streams WHERE streamId = ?', (streamId,))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                self.log.info(f"[StreamStore] Deleted stream {streamId}")
            return deleted
        finally:
            conn.close()
    
    def isEndpointAvailable(self, protocol: str, endpoint: str, excludeStreamId: Optional[str] = None) -> bool:
        """Check if an endpoint is available for use"""
        existing = self.getByEndpoint(protocol, endpoint)
        if not existing:
            return True
        if excludeStreamId and existing.streamId == excludeStreamId:
            return True
        return False
    
    def _rowToDefinition(self, row: sqlite3.Row) -> StreamDefinition:
        """Convert database row to StreamDefinition"""
        return StreamDefinition(
            streamId=row['streamId'],
            name=row['name'],
            protocol=row['protocol'],
            endpoint=row['endpoint'],
            lane=row['lane'],
            systemIdFilter=row['systemIdFilter'],
            containerIdFilter=row['containerIdFilter'],
            uniqueIdFilter=row['uniqueIdFilter'],
            messageTypeFilter=row['messageTypeFilter'],
            outputFormat=row['outputFormat'],
            backpressure=row['backpressure'],
            enabled=bool(row['enabled']),
            createdBy=row['createdBy'],
            visibility=row['visibility'],
            createdAt=row['createdAt'],
            updatedAt=row['updatedAt']
        )
