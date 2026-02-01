# NOVA System Architecture

**Complete System-of-Systems Architecture**  
**Version:** 2.0 (Single Database Truth Model)  
**Date:** January 25, 2026  
**Status:** Production

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Overview](#system-overview)
3. [Architecture Principles](#architecture-principles)
4. [Service Boundaries](#service-boundaries)
5. [Data Flow: Live Operations](#data-flow-live-operations)
6. [Data Flow: Playback/Replay](#data-flow-playbackreplay)
7. [Message Architecture](#message-architecture)
8. [Metadata Architecture](#metadata-architecture)
9. [Command Architecture](#command-architecture)
10. [Scope & Network Control](#scope--network-control)
11. [Lane Model (UI vs Firehose)](#lane-model-ui-vs-firehose)
12. [Determinism & Timebase Contract](#determinism--timebase-contract)
13. [Storage Architecture](#storage-architecture)
14. [Integration Patterns](#integration-patterns)
15. [System Diagrams](#system-diagrams)

---

## Executive Summary

NOVA is a **distributed real-time telemetry, command, and control system** for hardware assets with **complete replay/playback capability**. The system is built on a **single-database-truth architecture** where novaArchive is the authoritative data store, and all other services are stateless or derive their state from the archive.

### Key Architectural Decisions

1. **Single Source of Truth**: novaArchive owns the authoritative SQLite database; novaCore has no truth database
2. **Stateless UI Playback**: Client-driven HTTP queries (snapshot + deltas) replace server-side session management
3. **Lane Architecture**: UI lane (low-rate) and firehose lane (high-rate) are filters over the same stored truth
4. **Receive-Time Authority**: novaArchive receive timestamp is authoritative for all ordering and replay
5. **Deterministic Messages**: All typed messages follow consistent, alphabetically-ordered JSON structure
6. **Manifest Authority**: Hardware capabilities and UI commands are manifest-defined, not hard-coded
7. **Time-Versioned Metadata**: Metadata is change-only with priority-based override system
8. **Command Audit Trail**: All commands recorded with request + progress + result for complete audit

### System Scale

- **Throughput**: 1000+ messages/sec aggregate (all entities)
- **UI Update Rate**: 1-2 Hz per entity (deterministic, stable)
- **Firehose Rate**: Native device rate (10-100 Hz) for analysis tools
- **Replay Speed**: Real-time to 100x (client-controlled)
- **Storage**: SQLite + Parquet files, day-partitioned structure
- **Network Modes**: Local (edge node), Aggregator (ground station), Hybrid

---

## System Overview

NOVA consists of five primary services:

```
┌────────────────────────────────────────────────────────────────┐
│                        NOVA ECOSYSTEM                          │
│                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ hardwareService│→│     GEM      │→│ novaArchive  │       │
│  │   (IPC/Raw)  │  │   (Parse)    │  │ (Truth DB)   │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
│         ↓                 ↓                   ↓               │
│    Raw Bytes       Typed Streams      Storage + Index        │
│                                                                │
│                   ┌──────────────┐                           │
│                   │  novaCore    │                           │
│                   │ (UI Proxy)   │                           │
│                   └──────────────┘                           │
│                         ↓                                     │
│                  Browser Client                              │
│                (Timeline + Cards)                            │
└────────────────────────────────────────────────────────────────┘
```

### Service Roles

| Service | Role | State | Database |
|---------|------|-------|----------|
| **hardwareService** | Device discovery + raw IPC | Stateless | None |
| **GEM** | Protocol parsing + typing | Stateless | None (config only) |
| **novaArchive** | Truth storage + replay | Authoritative | SQLite (truth DB) |
| **novaCore** | Web UI + proxy + auth | Stateless | Users only |
| **Browser** | Timeline rendering + control | Derived state | In-memory store |

---

## Transport Abstraction

### Overview

All messaging in NOVA flows through **`/transport`** (from `/sdk`), a modular abstraction that supports multiple underlying bindings:

**Local chain**: IPC (hardwareService → GEM → novaArchive)  
**Network chain**: NATS (remote novaArchive → ground novaArchive)  
**UI flows**: HTTP/WebSocket currently, with desired direction to move behind `/transport` over time

**Key Principle**: No service should directly import `nats`, `zmq`, `socket`, etc. All messaging goes through the transport abstraction.

### Transport Types

**NngTransport** (`nng+ipc://`, `nng+tcp://`):
- Local IPC for hardwareService → GEM → novaArchive
- Connectionless pub/sub with subject-based routing
- REQ/REP support for control channels
- Platform-agnostic (Windows, Linux)

**NatsTransport** (`nats://`):
- Network messaging for remote novaArchive → ground novaArchive
- Distributed pub/sub with wildcard subscriptions
- Built-in reconnection and buffering
- Multi-tenancy via scopeId subscription filtering

### Usage Pattern

```python
from sdk.transport import createTransport

# Create transport from URI
transport = createTransport('nng+ipc:///tmp/hwService')
await transport.connect('nng+ipc:///tmp/hwService')

# Publish message
await transport.publish('device.data', payload_bytes)

# Subscribe to messages
async def handler(subject: str, payload: bytes):
    # Process message
    pass

await transport.subscribe('device.data', handler)

# Request/reply (control channel)
response = await transport.request('control.command', request_bytes, timeout=5.0)

# Cleanup
await transport.close()
```

### Why Abstract Transport?

1. **Future-proof**: Add TCP, WebSocket, gRPC without changing service code
2. **Testable**: Mock transport for unit tests
3. **Portable**: Swap IPC for NATS for remote deployments
4. **Scope control**: Visibility rules enforced by subscription patterns, not hardcoded protocols

---

## Architecture Principles

### 1. Single Database Truth (Non-Negotiable)

**Rule**: novaArchive is the ONLY authoritative database for streams, metadata, and command audit.

**Implementation**:
- Every `stream.*` message MUST be persisted to SQLite (unconditional)
- novaCore has NO truth database (only user accounts)
- GEM stores NO runtime state (only configuration files)
- Browser maintains DERIVED timeline store (rebuilt from archive data)

**Consequence**: Replay is always consistent because there's only one source of truth.

```python
# novaArchive: Authoritative ingest
async def ingestMessage(msg):
    # MUST write to DB first (never skip)
    await messageStore.insertMessage(msg)
    
    # OPTIONAL: Write to file/export (failures don't block ingest)
    try:
        await fileWriter.write(msg)
    except:
        pass  # Log but don't fail
```

### 2. Stateless Replay/Playback

**Rule**: No server-side client session tracking, mode management, or subscription switching.

**Live Mode**:
- WebSocket open → `/ws/live`
- Initial snapshot loaded from archive storage (< 100ms)
- Live deltas forwarded via WebSocket
- Archive publishes to `archive.{scopeId}.ui.{streamType}`

**Playback Mode** (Client-Driven):
- WebSocket closed
- HTTP queries: `GET /api/replay/snapshot?time=T&scope=X`
- HTTP deltas: `GET /api/replay/deltas?start=T0&end=T1&scope=X`
- Window semantics: `(start, end]` (exclusive start, inclusive end)
- Client owns cursor, speed, and mode state

**No Server-Side Sessions**:
- ❌ No `sessionId` tracking (client manages replay sessions via HTTP endpoints)
- ❌ No replay subscription management
- ❌ No cursor state on server
- ❌ No playback timers per client
- ✅ Browser drives everything via HTTP
- ✅ Client controls cursor timeline (current time, playback speed)
- ✅ Supports immediate seek (jump in time) and immediate rate changes
- ✅ Renders/plays locally from buffered data

### 3. Receive-Time Truth

**Rule**: novaArchive receive-time is authoritative for all ordering, replay, and file writing.

**Implementation**:
- Device timestamps (if present) stored as auxiliary data ONLY
- novaArchive assigns `receiveTimestampMs` when message arrives
- All ordering uses `receiveTimestampMs`
- Device `timestampMs` kept in payload for debugging

**Rationale**: Remote hardware clocks may be wrong (no GPS fix, bad RTC, time jumps).

**Validation**:
```sql
-- Messages always ordered by receive time
SELECT * FROM messages ORDER BY timestampMs ASC;

-- Device timestamp stored but not used for ordering
SELECT payload->>'timestampMs' AS device_time, timestampMs AS truth_time FROM messages;
```

### 4. Deterministic Message Structure

**Rule**: All typed messages follow ONE consistent shape with alphabetically-ordered fields.

**Standard Envelope** (GEM→Archive→UI):
```json
{
  "assetId": "device-12345",
  "patch": {
    "lat": 40.647,
    "lon": -111.818,
    "alt": 1354.2,
    "heading": 90.0
  },
  "scopeId": "payload-1",
  "sequenceNum": 42,
  "streamType": "position",
  "timestampMs": 1706054400000,
  "version": 1
}
```

**Field Rules**:
- **camelCase** everywhere (assetId, streamType, timestampMs)
- **Alphabetically ordered** (use `json.dumps(data, sort_keys=True)`)
- **Complete snapshots** (not tiny patches; include all UI-relevant fields)
- **No optional nulls** (omit field if not present, or use explicit `null`)

### 5. Manifest Authority

**Rule**: Device capabilities, UI commands, and card types are manifest-defined, not hard-coded.

**Manifest Structure** (gem.manifest.json):
```json
{
  "serviceId": "gem",
  "name": "Ground Equipment Manager",
  "version": "2.0.0",
  "devices": [
    {
      "kind": "ubx",
      "entityType": "gnss-receiver",
      "cardType": "gnss-card",
      "capabilities": ["position", "gnss-signals"],
      "actions": [
        {
          "actionId": "hotStart",
          "verb": "receiver.hotStart",
          "displayName": "Hot Start",
          "description": "Restart with preserved ephemeris",
          "icon": "restart",
          "params": []
        }
      ]
    }
  ]
}
```

**Authority Flow**:
1. Producer (GEM) publishes device metadata with `entityType`
2. novaCore loads manifest to get `cardType` for `entityType`
3. Browser loads card spec and renders action buttons
4. User clicks button → POST /api/commands with `verb` from manifest
5. novaCore forwards to producer via NATS: `command.{verb}.{entityId}`

---

## Database + Drivers Pattern

### Single Database for Indexing, Drivers for Files

novaArchive uses **one authoritative SQLite database** for:
- Replay queries (snapshot, deltas, bounds)
- Time-versioned metadata storage
- Command audit trail
- Message indexing and deduplication

**Drivers** are responsible for:
- **Writing daily cold files** (.bin for raw receivers, .csv for parsed data)
- **Exporting truth files** for any (startTime, stopTime) window
- **File format abstraction** (binary, CSV, HDF5, etc.)

### Driver Architecture *

**Current approach** (open to change): Drivers can be split into two classes:

**StreamDriver**: Handles ingest + file writing + export
- `ingest(message)` - Receive message from transport
- `write()` - Write message to cold file (buffered, daily rotation)
- `export(startTime, stopTime, outputPath)` - Export messages to file

**CommandAdapter**: Handles command→bytes and optional ack parsing
- `encodeCommand(verb, params)` - Convert command to raw bytes
- `parseAck(raw_bytes)` - Parse acknowledgment from device (optional)
- `validateScope(scopeId)` - Validate command is authorized for scope

Both inherit from a generic `BaseDriver` with common lifecycle methods.

### File Organization

**Cold files** (daily rotation):
```
storage/
  2026-01-25/
    8220-F9P.bin           # Raw bytes (TCP replay)
    8220-F9P_position.csv  # Parsed position data
    8220-F9P_signals.csv   # Parsed GNSS signals
  2026-01-26/
    ...
```

**Export files** (on-demand):
```
exports/
  mission_alpha_1706188400000_1706274800000.bin  # Raw export
  mission_alpha_positions.csv                     # Parsed export
```

### Why Separate DB and Drivers?

1. **DB is fast**: SQLite handles queries in milliseconds (indexed lookups)
2. **Files are portable**: Export .bin or .csv for offline analysis
3. **Drivers are reusable**: Same driver can export any time window
4. **Replay doesn't need files**: Replay queries hit the DB only (fast)

---

## Service Boundaries

### hardwareService

**Purpose**: Device lifecycle management and raw byte streaming.

**Responsibilities**:
- Scan serial ports and discover devices
- Publish topology events (device list)
- Stream raw bytes on per-device subjects
- Execute hardware-level commands (restart, config apply)
- NO protocol parsing (raw bytes only)

**Subjects Published**:
- `hardwareService.events.{containerId}` → topology updates
- `device.raw.{deviceId}.{kind}.{dataKind}` → raw byte streams

**Subjects Consumed**:
- `hardwareService.control.{containerId}` → REQ/REP (getTopology, restart, applyConfig)
- `hardwareService.discovery.{containerId}` → stateless discovery requests

**Authority**: Sole source of device existence truth.

### GEM (Ground Equipment Manager)

**Purpose**: Protocol parsing, typing, and operator stream publishing.

**Responsibilities**:
- Subscribe to hardwareService topology events
- Create device instances with appropriate parsers
- Parse raw bytes into typed streams (position, gnss, raw-packet)
- Publish deterministic UI snapshots (1-2 Hz rate-limited)
- Publish metadata (entity hierarchy) on connect + change
- Forward commands to hardwareService
- NO storage (stateless)

**Subjects Published**:
- `archive.ingest.{scopeId}.metadata.upsert` → metadata changes
- `stream.raw.{scopeId}.{entityId}` → raw bytes (for TCP replay)
- `stream.truth.{streamType}.{scopeId}.{entityId}` → high-rate parsed (native device rate)
- `stream.ui.{streamType}.{scopeId}.{entityId}` → low-rate snapshots (1-2 Hz)

**Subjects Consumed**:
- `hardwareService.events.{containerId}` → topology
- `device.raw.{deviceId}.{kind}.{dataKind}` → raw bytes to parse
- `command.{verb}.{entityId}` → command execution

**Authority**: Sole parser of protocol-specific messages (UBX, SBF, NMEA).

**Transport Usage**: Uses `/transport` abstraction (IPC for local, NATS for remote).

### novaArchive

**Purpose**: Authoritative truth storage, indexing, and replay service.

**Responsibilities**:
- Ingest ALL streams (unconditional DB write)
- Maintain SQLite message index (authoritative for replay)
- Write Parquet/CSV files (optional export, failures don't block)
- Store time-versioned metadata (change-only, priority-based)
- Record command audit trail (request + result)
- Republish live data to UI lane (`archive.{scopeId}.ui.*`)
- Republish live data to firehose lane (`archive.{scopeId}.firehose.*`)
- Serve stateless replay queries (snapshot + deltas)
- Provide aggregator sync endpoints (watermark-based catch-up)

**HTTP Endpoints**:
- `GET /api/replay/snapshot?time=T&scope=X` → snapshot at time T
- `GET /api/replay/deltas?start=T0&end=T1&scope=X` → events in window
- `GET /api/replay/bounds?scope=X` → time range for scope
- `GET /api/metadata/{assetId}` → current metadata
- `GET /api/sync/events?since=WATERMARK&scope=X` → aggregator catch-up

**Subjects Consumed**:
- `stream.raw.{scopeId}.>` → raw byte streams
- `stream.truth.*.{scopeId}.>` → high-rate truth streams
- `stream.ui.*.{scopeId}.>` → UI lane streams (for DB ingest)
- `archive.ingest.*.metadata.upsert` → metadata upserts

**Subjects Published**:
- `archive.{scopeId}.ui.{streamType}` → UI lane (low-rate republish)
- `archive.{scopeId}.firehose.{streamType}` → firehose lane (full-rate)

**Database Schema**:
```sql
-- Messages table (authoritative for replay)
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    timestampMs INTEGER NOT NULL,  -- Receive time (authority)
    timestamp TEXT NOT NULL,       -- ISO 8601
    entityId TEXT NOT NULL,
    streamType TEXT NOT NULL,
    scopeId TEXT,
    subject TEXT NOT NULL,
    payload TEXT NOT NULL,         -- JSON (authoritative)
    filePath TEXT,                 -- Legacy file reference
    fileOffset INTEGER,
    payloadLength INTEGER,
    metadata TEXT                  -- Minimal filtering data
);

-- Metadata table (time-versioned)
CREATE TABLE metadata (
    assetId TEXT NOT NULL,
    timestampMs INTEGER NOT NULL,
    metadata TEXT NOT NULL,        -- JSON
    priority INTEGER DEFAULT 0,
    source TEXT,
    PRIMARY KEY (assetId, timestampMs)
);

-- Commands table (audit trail)
CREATE TABLE commands (
    commandId TEXT PRIMARY KEY,
    entityId TEXT NOT NULL,
    verb TEXT NOT NULL,
    params TEXT,                   -- JSON
    scopeId TEXT,
    timestamp INTEGER NOT NULL,
    status TEXT NOT NULL,
    resultMessage TEXT,
    resultTimestamp INTEGER,
    source TEXT
);
```

**Authority**: ONLY service that writes truth database; all others are stateless.

### novaCore

**Purpose**: Web UI server, HTTP proxy, authentication, and metadata override management.

**Responsibilities**:
- Serve web application (HTML/CSS/JS)
- Proxy live streams from novaArchive to browser (WebSocket)
- Proxy replay HTTP requests to novaArchive
- Proxy metadata HTTP requests to novaArchive
- Forward commands from browser to producers via NATS
- Apply metadata overrides (ground control overrides)
- User authentication and session management
- Manifest loading and card registry
- NO truth database (only user accounts)

**HTTP Endpoints**:
- `GET /` → web application
- `GET /ws/live` → WebSocket (live stream forwarding)
- `POST /api/commands` → forward to NATS: `command.{verb}.{entityId}`
- `POST /api/commands/upload-config` → forward config file
- `POST /api/v1/metadata` → publish metadata via NATS (3rd-party integration)
- `GET /api/replay/*` → proxy to novaArchive
- `GET /api/metadata/*` → proxy to novaArchive

**Subjects Published**:
- `command.{verb}.{entityId}` → command forwarding
- `archive.ingest.{scopeId}.metadata.upsert` → 3rd-party metadata (with overrides)

**Subjects Consumed**:
- `archive.*.ui.>` → all UI lane data (wildcard scope subscription)

**Override System**:
```json
// overrides.json (ground control authority)
{
  "sensor-123": {
    "systemId": "mission-alpha",
    "systemDisplayName": "Mission Alpha",
    "containerId": "payload-special",
    "displayName": "Primary Sensor",
    "priority": 10  // Higher than producer (0)
  }
}
```

**Authority**: Web UI server and metadata override authority (ground control).

### Browser Client

**Purpose**: Timeline visualization and user interaction.

**Responsibilities**:
- Maintain single derived timeline store (in-memory)
- Subscribe to live WebSocket OR query replay HTTP (user-controlled)
- Render Cesium 3D globe + hierarchical entity tree
- Render manifest-driven cards (entity details)
- Send commands via POST /api/commands
- Drive playback (cursor, speed, mode) via HTTP queries
- NO server-side state (client is authoritative for UI state)

**Client Timeline Store**:
```javascript
class TimelineStore {
  entities = {}      // entityId → metadata
  positions = {}     // entityId → latest position
  streams = {}       // entityId → streamType → latest value
  
  applyDelta(msg) {
    // Deterministic patch application
    if (msg.streamType === 'entity.metadata') {
      this.entities[msg.assetId] = { ...this.entities[msg.assetId], ...msg.patch }
    } else if (msg.streamType === 'position') {
      this.positions[msg.assetId] = msg.patch
    }
  }
}
```

**Mode Derivation** (No Server Tracking):
```javascript
getTimeMode() {
  return this.websocket && this.websocket.readyState === WebSocket.OPEN 
    ? 'realtime' 
    : 'playback'
}
```

**Authority**: Client is authoritative for UI state (cursor position, playback speed, visibility toggles).

---

## UI Architecture

### Overview

The NOVA UI is a **single-page application** with a **responsive panel-based layout** supporting real-time and replay modes. The interface features resizable panels, collapsible sidebars, drag-to-resize functionality, and persistent user preferences.

**Design Principles**:
- **Stateless Server**: UI state lives in browser, server provides data only
- **Manifest-Driven**: Cards, shields, and actions defined by service manifests
- **Responsive Layout**: Panels constrain and adapt to viewport size
- **Persistent Preferences**: Panel sizes, collapsed states saved to localStorage
- **Replay Fidelity**: UI state rewinds correctly during timeline navigation

### Panel System Architecture

**Layout Structure** (4 main panels):

```
┌─────────────────────────────────────────────────────────────┐
│ Header (50px height, fixed)                                 │
├──────┬────────────────────────────────────┬─────────────────┤
│      │                                    │                 │
│ Left │         Cesium Map                 │  Right Panel    │
│ Side │         (3D Globe)                 │  (Detailed)     │
│ bar  │                                    │                 │
│      │                                    │  - Entity Cards │
│ 200- │         + Entity Shields           │  - Device Info  │
│ 600  │         (overlay icons)            │  - Task Buttons │
│ px   │                                    │                 │
│      │                                    │  250-800px      │
├──────┴────────────────────────────────────┴─────────────────┤
│ Chat Panel (draggable, collapsible)                         │
│ - Positioned between left/right sidebars                    │
│ - Resizable vertically (drag top edge)                      │
│ - Min 100px height, max 20% viewport                        │
├──────────────────────────────────────────────────────────────┤
│ Timeline (40-200px height, resizable)                        │
│ - Playback controls + slider                                │
│ - Speed controls, time display                              │
└──────────────────────────────────────────────────────────────┘
```

**Panel Constraints**:
- Left sidebar: 200-600px width, collapsible
- Right panel: 250-800px width (or 80% viewport max), collapsible
- Chat: Positioned between sidebars, draggable top edge, min 100px height
- Timeline: 40-200px height, fixed to bottom
- Map: Fills remaining space (responsive)

### Client-Side Components

**Core UI Modules** (location: `novaCore/static/js/`):

#### 1. **nova-resize.js** - Reusable Panel Resize Utility

**Purpose**: Provides draggable resize handles for panels with localStorage persistence.

**API**:
```javascript
class NovaPanelResize {
    constructor(storage) { /* localStorage wrapper */ }
    
    makeResizable(panel, options) {
        // options: {
        //   storageKey: 'sidebar:left:width',
        //   direction: 'horizontal' | 'vertical',
        //   property: 'width' | 'height',
        //   min: 200, max: 600,
        //   invert: false  // For right-side panels
        // }
    }
}
```

**Features**:
- Drag handle creation and positioning
- Min/max size constraints
- localStorage persistence (restore on page load)
- RAF-based smooth dragging
- Visual feedback (cursor change, handle highlight)

**Usage Example**:
```javascript
window.novaPanelResize.makeResizable(leftSidebar, {
    storageKey: 'sidebar:left:width',
    direction: 'horizontal',
    property: 'width',
    min: 200,
    max: 600
});
```

#### 2. **nova-panel-setup.js** - Panel Initialization

**Purpose**: Initialize all resizable panels after DOM load.

**Responsibilities**:
- Apply saved sizes from localStorage
- Create resize handles for all panels
- Set up event listeners for panel interactions
- Update chat position relative to timeline
- Handle panel toggle states

**Initialization Flow**:
```javascript
document.addEventListener('DOMContentLoaded', () => {
    // Left sidebar
    novaPanelResize.makeResizable(leftSidebar, {...});
    
    // Right detailed panel
    novaPanelResize.makeResizable(detailedPanel, {...});
    
    // Timeline
    novaPanelResize.makeResizable(timeline, {...});
    
    // Update chat position when timeline resizes
    timeline.addEventListener('panel-resize', updateChatPosition);
});
```

#### 3. **nova-split-setup.js** - Legacy Split Panel Handlers

**Purpose**: Custom drag handlers for sidebar and chat positioning.

**Responsibilities**:
- Left sidebar width adjustment
- Right panel width adjustment
- Chat vertical positioning (drag top edge)
- Chat horizontal bounds (constrain between sidebars)
- Gutter visual feedback

**Chat Positioning Logic**:
```javascript
// Chat stays between sidebars
const updateChatBounds = () => {
    const leftWidth = leftSidebar.offsetWidth;
    const rightWidth = detailedPanel.offsetWidth;
    chatPanel.style.left = `${leftWidth}px`;
    chatPanel.style.right = `${rightWidth}px`;
};

// Chat stays above timeline
const updateChatPosition = () => {
    const timelineHeight = timeline.offsetHeight;
    chatPanel.style.bottom = `${timelineHeight}px`;
};
```

#### 4. **nova-chat.js** - Chat Panel with Replay Integration

**Purpose**: Real-time chat with timeline-aware message visibility.

**Features**:
- Collapse/expand state (persistent)
- Unread badge count
- Channel selection (ops, team, mission)
- Timeline integration (messages appear/disappear during replay)
- Export to JSON
- XSS protection (HTML escaping)

**Replay Integration**:
```javascript
class NovaChat {
    _onTimeUpdate(timeDetail) {
        // Filter messages by timeline cursor
        this.currentTime = timeDetail.timestampMs;
        this._render();  // Only show messages <= currentTime
    }
    
    _getVisibleMessages() {
        return this.messages.filter(msg => 
            msg.timestampMs <= this.currentTime &&
            msg.channel === this.selectedChannel
        );
    }
}
```

**DOM Structure**:
```html
<section class="chat-panel collapsed" id="chatPanel">
    <header class="chat-header">
        <button class="chat-toggle">💬 Chat</button>
        <select class="chat-channel">...</select>
        <button class="chat-export">Export</button>
    </header>
    <div class="chat-messages" role="log"></div>
    <div class="chat-input-row">
        <input class="chat-input" />
        <button class="chat-send">Send</button>
    </div>
</section>
```

#### 5. **nova-detailed-panel.js** - Manifest-Driven Card Rendering

**Purpose**: Render entity detail cards using service manifests.

**Responsibilities**:
- Fetch card specifications from manifests
- Instantiate custom card classes (ReceiverCard, OscopeCard)
- Patch card data on stream updates (zero custom update logic)
- Handle card open/close/switch

**Card Rendering Flow**:
```javascript
async _renderCard(entity) {
    // 1. Determine category
    const category = this._mapEntityTypeToCategory(entity.entityType);
    
    // 2. Try custom card class
    if (category === 'receiver' && window.ReceiverCard) {
        const deviceData = await this._fetchDeviceData(entity.entityId);
        return ReceiverCard.createCard(deviceData, entity);
    }
    
    // 3. Fallback: Manifest-based widget rendering
    return this._renderManifestCard(entity);
}

_patchCardData(entity) {
    // Update all data-field attributes
    cardElement.querySelectorAll('[data-field]').forEach(el => {
        const field = el.getAttribute('data-field');
        if (entity[field] !== undefined) {
            el.textContent = entity[field];
        }
    });
}
```

#### 6. **nova-timeline.js** - Playback Controls

**Purpose**: Timeline scrubber with play/pause, speed control, seek.

**Features**:
- Live/replay mode switching
- Play/pause toggle
- Speed multiplier (0.5x, 1x, 2x, 5x, 10x)
- Seek via slider drag
- Time display (current / total)
- Bounds fetching (startTime, endTime)

**Mode Switching**:
```javascript
class NovaTimeline {
    async toggleLive() {
        if (this.mode === 'live') {
            // Switch to replay: close WebSocket, fetch bounds
            await this.socketManager.close();
            const bounds = await this.apiClient.getReplayBounds(scopeId);
            this.enterReplayMode(bounds);
        } else {
            // Switch to live: reconnect WebSocket
            await this.socketManager.connect();
            this.enterLiveMode();
        }
    }
}
```

#### 7. **nova-storage.js** - Client-Side State Persistence

**Purpose**: localStorage wrapper with JSON serialization.

**API**:
```javascript
class NovaStorage {
    set(key, value) {
        localStorage.setItem(`nova:${key}`, JSON.stringify(value));
    }
    
    get(key, defaultValue) {
        const value = localStorage.getItem(`nova:${key}`);
        return value ? JSON.parse(value) : defaultValue;
    }
    
    remove(key) {
        localStorage.removeItem(`nova:${key}`);
    }
}
```

**Stored Preferences**:
- `sidebar:left:width` - Left sidebar width (px)
- `sidebar:right:width` - Right panel width (px)
- `timeline:height` - Timeline height (px)
- `chat:top` - Chat panel top position (px)
- `chat:collapsed` - Chat collapsed state (boolean)
- `chat:channel` - Selected chat channel (string)
- `map:view` - Last map view (lat, lon, zoom)
- `replay:speed` - Last playback speed

#### 8. **nova-auth.js** - Authentication Client

**Purpose**: JWT token management, login/logout, session validation.

**Features**:
- Token storage in localStorage
- Authorization header injection
- 401/403 error handling (redirect to login)
- Token expiration detection
- WebSocket auth (token as query parameter)

**API Client Integration**:
```javascript
class NovaAuth {
    async login(username, password) {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            body: JSON.stringify({username, password})
        });
        const {token, user} = await response.json();
        this.storage.set('auth:token', token);
        this.storage.set('auth:user', user);
        return user;
    }
    
    getAuthHeaders() {
        const token = this.storage.get('auth:token');
        return token ? {'Authorization': `Bearer ${token}`} : {};
    }
}
```

#### 9. **entity-customizer.js** - User Customization UI

**Purpose**: Per-user entity name/color/model overrides.

**Features**:
- Right-click menu on shields → "Customize"
- Modal dialog for editing entity properties
- Sync to server (POST /api/customization/{entityId})
- Merge with system defaults on load

**Customization Flow**:
```javascript
// User right-clicks entity shield → "Customize"
async customizeEntity(entityId) {
    const modal = this._createCustomizationModal(entityId);
    
    // On save:
    const overrides = {name: '...', color: '#...', modelStyle: '...'};
    await this.apiClient.setCustomization(entityId, overrides);
    
    // Apply locally + dispatch event
    this._applyCustomization(entityId, overrides);
    document.dispatchEvent(new CustomEvent('nova:entity-updated', {
        detail: {entityId}
    }));
}
```

### UI State Management

**State Architecture**:

```
┌────────────────────────────────────────────────┐
│ Server (novaCore)                              │
│ - User authentication                          │
│ - Per-user customizations (persistent)        │
│ - System defaults (admin-managed)              │
│ - NO UI state tracking                         │
└─────────────────┬──────────────────────────────┘
                  │ (on login: fetch customizations)
                  ▼
┌────────────────────────────────────────────────┐
│ Client (Browser)                               │
│ ┌────────────────────────────────────────────┐ │
│ │ Timeline Store (in-memory)                 │ │
│ │ - entities: {entityId → metadata}          │ │
│ │ - positions: {entityId → latest position}  │ │
│ │ - streams: {entityId → streamType → value} │ │
│ └────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────┐ │
│ │ View State (localStorage)                  │ │
│ │ - panel:sizes (sidebar widths, heights)    │ │
│ │ - panel:collapsed (visibility toggles)     │ │
│ │ - map:view (camera position)               │ │
│ │ - replay:speed (playback speed)            │ │
│ └────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────┐ │
│ │ User Customizations (localStorage + server)│ │
│ │ - Fetched on login                         │ │
│ │ - Merged with entity metadata              │ │
│ │ - Synced to server on change               │ │
│ └────────────────────────────────────────────┘ │
└────────────────────────────────────────────────┘
```

**State Update Flow**:

```
1. WebSocket/HTTP delivers delta message
   ↓
2. TimelineStore.applyDelta(msg)
   ↓
3. Dispatch 'nova:entity-updated' event
   ↓
4. Components listen and re-render:
   - Cesium map updates entity position
   - Shield updates icon/status
   - Detailed panel patches card data
   - Chat filters messages by timeline cursor
```

**Merge Logic** (Customizations):
```javascript
function mergeEntity(serverEntity, userCustomizations, systemDefaults) {
    return {
        ...systemDefaults,      // Lowest priority
        ...serverEntity,        // Producer data
        ...userCustomizations   // Highest priority (user overrides)
    };
}
```

### Responsive Layout

**CSS Architecture** (location: `novaCore/static/css/nova-main.css`):

**Layout Grid**:
```css
.app-container {
    display: grid;
    grid-template-columns: auto 1fr auto;
    grid-template-rows: 50px 1fr auto;
    grid-template-areas:
        "header header header"
        "sidebar map detailed"
        "timeline timeline timeline";
    height: 100vh;
    overflow: hidden;
}

.sidebar { grid-area: sidebar; }
.map-container { grid-area: map; }
.detailed-panel { grid-area: detailed; }
.timeline { grid-area: timeline; }
```

**Chat Positioning** (fixed, overlay):
```css
.chat-panel {
    position: fixed;
    left: var(--sidebar-width);
    right: var(--detailed-width);
    bottom: var(--timeline-height);
    top: calc(100vh - var(--chat-height));
    z-index: 10000;
}

.chat-panel.collapsed {
    top: calc(100vh - 7.5rem);  /* Show only header */
}
```

**Resize Handles**:
```css
.resize-handle {
    position: fixed;
    background: transparent;
    transition: background 0.2s;
    z-index: 10000;
}

.resize-handle-horizontal {
    width: 10px;
    height: 100vh;
    cursor: ew-resize;
}

.resize-handle-vertical {
    width: 100%;
    height: 10px;
    cursor: ns-resize;
}

.resize-handle:hover {
    background: rgba(64, 150, 255, 0.6);
}
```

**Panel Constraints**:
```javascript
// Enforced in nova-resize.js and nova-split-setup.js
const constraints = {
    leftSidebar: {min: 200, max: 600},
    detailedPanel: {min: 250, max: Math.min(800, window.innerWidth * 0.8)},
    timeline: {min: 40, max: 200},
    chat: {minHeight: 100, maxHeight: window.innerHeight * 0.2}
};
```

### Shields & Cards

**Shield System** (compact icons, always visible):

**Purpose**: Quick-glance entity inventory on map overlay.

**Implementation**:
- Cesium billboard entities (icon + label)
- Position updated from `position` stream
- Status color from `online` field
- Click → open detailed panel card

**Rendering**:
```javascript
class CesiumMapRenderer {
    addShield(entity) {
        const billboard = viewer.entities.add({
            id: entity.entityId,
            position: Cesium.Cartesian3.fromDegrees(lon, lat, alt),
            billboard: {
                image: this._getIconForEntityType(entity.entityType),
                scale: 1.0,
                color: this._getColorForStatus(entity.online)
            },
            label: {
                text: entity.name,
                font: '12px sans-serif',
                fillColor: Cesium.Color.WHITE,
                outlineColor: Cesium.Color.BLACK,
                outlineWidth: 2,
                pixelOffset: new Cesium.Cartesian2(0, 20)
            }
        });
    }
}
```

**Card System** (detailed info, opened on-demand):

**Purpose**: Rich entity details with live data, action buttons, charts.

**Implementation**:
- Right panel (detailed-panel.js)
- Manifest-driven layout (widgets from service manifests)
- Custom card classes (ReceiverCard, OscopeCard) for rich UI
- Zero-custom-update: data-field attributes auto-patch on stream updates

**Card Types**:
1. **Manifest-Based Cards** (generic):
   - Text widgets: `{type: 'text', label, field}`
   - Status dots: `{type: 'status-dot', field, values}`
   - Tables: `{type: 'table', rows: [...fields]}`

2. **Custom Card Classes** (rich UI):
   - `ReceiverCard`: Constellation charts, signal strength bars
   - `OscopeCard`: Waveform canvas, frequency controls
   - Loaded dynamically: `nova-device-cards.js`

**Auto-Patch System**:
```html
<!-- Card HTML with data-field attributes -->
<div class="card">
    <div data-field="name"></div>
    <div data-field="fixType"></div>
    <div data-field="satCount"></div>
</div>
```

```javascript
// Zero custom update logic needed
function patchCardData(entity) {
    card.querySelectorAll('[data-field]').forEach(el => {
        const field = el.getAttribute('data-field');
        if (entity[field] !== undefined) {
            el.textContent = entity[field];
        }
    });
}
```

### Mobile Considerations

**Current State**: Desktop-optimized (Cesium requires WebGL, mouse drag).

**Responsive Breakpoints** (not yet implemented):
- Tablets: Hide left sidebar by default, show toggle
- Mobile: Single-column layout, bottom sheet cards

**Touch Events** (not yet implemented):
- Touch drag for panel resize
- Pinch-to-zoom for map
- Swipe to dismiss panels

### Performance Optimizations

**Rendering**:
- RAF-based resize throttling (1 frame per drag event)
- CSS containment for panels (`contain: layout style paint`)
- Virtual scrolling for large entity lists (not yet implemented)

**State Updates**:
- Debounced localStorage writes (300ms)
- Batch DOM updates (single reflow per frame)
- Memoized entity filters (chat timeline visibility)

**WebSocket**:
- Backpressure detection (pause stream if client can't keep up)
- Message batching (multiple deltas per frame)
- Binary protocol for high-rate streams (not yet implemented)

---

## Raw TCP Replay (Option 1)

For **total-fidelity raw byte replay**, novaArchive can serve TCP streams directly:

### Requirements

1. **TCP streams carry raw bytes only** (no framing, no JSON, no delimiters)
2. **Bytes are served in the same chunk boundaries** they arrived in (preserved from hardwareService)
3. **Client timeline controls requested window and pacing** (via HTTP control channel or pre-defined window)
4. **End state: total-fidelity raw byte replay** (bit-for-bit identical to original stream)

### Use Cases

- Laboratory device testing (feed raw stream to physical hardware)
- Parser validation (verify parser handles all edge cases)
- Forensic analysis (reconstruct exact device behavior)
- Regression testing (ensure protocol changes don't break parsing)

### API Design (Proposed) *

**Current approach** (open to change): Hybrid HTTP control + TCP data stream

```
# Step 1: Request raw TCP replay session
POST /api/replay/raw-tcp
{
  "assetId": "8220-F9P",
  "scopeId": "payload-1",
  "startTime": 1706188400000,
  "endTime": 1706274800000,
  "lane": "raw"
}

Response:
{
  "sessionId": "replay-uuid-123",
  "tcpPort": 9000,
  "status": "ready"
}

# Step 2: Connect to TCP port
nc localhost 9000 | deviceSimulator --protocol ubx

# Step 3: Receive raw bytes (streamed at client-controlled pace)
<binary data streamed in original chunk boundaries>
```

### Implementation Notes

**Storage**: novaArchive stores raw bytes in `messages` table (`lane='raw'`, `payload` as hex string or BLOB)

**Chunk Boundary Preservation**: Store original message boundaries from hardwareService:
```python
# In novaArchive ingest
async def ingest_raw_stream(subject, payload: bytes):
    # Store exactly as received (don't concatenate or split)
    await db.execute('''
        INSERT INTO messages (lane, assetId, timestampMs, payload, chunkSize)
        VALUES ('raw', ?, ?, ?, ?)
    ''', (asset_id, receive_time, payload.hex(), len(payload)))
```

**TCP Serving**: Replay chunks in original sizes:
```python
# In novaArchive TCP replay server
async def serve_raw_tcp(session_id, tcp_client):
    cursor = db.execute('''
        SELECT payload, chunkSize, timestampMs FROM messages
        WHERE sessionId = ? AND lane = 'raw'
        ORDER BY timestampMs ASC
    ''', (session_id,))
    
    for row in cursor:
        payload_bytes = bytes.fromhex(row['payload'])
        await tcp_client.send(payload_bytes)  # Send original chunk
        await asyncio.sleep(0.001)  # Throttle at controllable rate
```

**Client Pacing**: Client can control replay speed via:
- HTTP control channel (POST /api/replay/raw-tcp/control?speed=2.0)
- Pre-configured window size + sleep duration
- External tool (e.g., `pv --rate-limit 115200` for serial port simulation)

---

## Replay Mixing Prevention *

**Problem**: Live streams and replay streams must never mix in client view.

**Current approach** (open to change): **Session-scoped endpoints** prevent mixing:

### Live Endpoints

**Transport subjects**: `archive.{scopeId}.ui.{streamType}` (via /transport)

**WebSocket**: `ws://localhost:8080/ws/live` (novaCore forwards from archive subjects)

**Client subscribes**: Browser opens WebSocket, receives snapshot + deltas

### Replay Endpoints *

**Option 1 (HTTP-only)**: Replay is pull-only via stateless HTTP queries
- Browser makes repeated `GET /api/replay/deltas?start=T&end=T+window` requests
- No transport subjects involved
- No risk of mixing (different API endpoints)

**Option 2 (Session-scoped transport)**: Replay uses transport subjects with session ID
- Transport subjects: `replay.{sessionId}.{scopeId}.ui.{streamType}`
- Client subscribes to session-specific subjects
- No mixing because subject namespace is different

**Current implementation**: Option 1 (HTTP-only replay)

### Client Mode Switching

**Live → Playback**:
```javascript
// Close WebSocket (stop receiving live deltas)
this.websocket.close()

// Start HTTP replay polling
this.playbackEngine.start(startTime)
```

**Playback → Live**:
```javascript
// Stop HTTP replay polling
this.playbackEngine.stop()

// Reopen WebSocket (receive live deltas)
this.websocket = new WebSocket('ws://localhost:8080/ws/live')
```

**Mode Derivation** (no explicit tracking):
```javascript
get mode() {
  if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
    return 'realtime'
  } else if (this.playbackEngine && this.playbackEngine.playing) {
    return 'playback'
  } else {
    return 'paused'
  }
}
```

### Alternative Approaches (Not Currently Implemented)

**Continuation Tokens/Watermarks**: For deterministic pulls and deduplication
- Each delta response includes `continuationToken` (last message ID + hash)
- Client sends token in next request: `GET /api/replay/deltas?continuation=token123`
- Server starts query from token position (no gaps, no duplicates)
- Benefit: Handles network failures gracefully
- Drawback: More complex server state (token → cursor mapping)

**Client-Side Filtering**: Client receives mixed stream and filters by time range
- Benefit: Simple server (no session management)
- Drawback: Client must handle out-of-order messages
- Drawback: External tools (PlotJuggler) can't filter reliably

---

## Command Flow (Connectionless)

### Overview

**Command flow is connectionless**: A command/button press sends a command to novaArchive; novaArchive routes the command (via an adapter/driver) as raw bytes back to the producer.

**UI shows progress** (similar to `/svs` config flow):
1. **Sent**: Command accepted by novaArchive
2. **Confirmed**: Producer (GEM) acknowledged receipt  
3. **Progress**: Device reports execution progress (optional, driver-specific)
4. **Result**: Success/error/timeout

### Command Flow Diagram

```
┌─────────┐          ┌──────────┐          ┌─────────────┐          ┌─────┐          ┌──────────┐
│ Browser │          │ novaCore │          │ novaArchive │          │ GEM │          │ hwService│
│         │          │          │          │             │          │     │          │          │
│  Click  │   HTTP   │  Lookup  │   HTTP   │   Route     │  Transp. │     │   IPC    │          │
│  Button │──POST───>│  metadata│──POST───>│   Command   │──cmd.───>│     │──REQ────>│  Write   │
│  {verb, │  /api/   │  (cache) │  /api/   │   Adapter   │  {verb}. │     │  /REP    │  bytes   │
│  params}│  commands│          │  commands│             │  {entity}│     │          │  to      │
│         │          │          │          │   Store     │          │     │          │  serial  │
│         │          │  Gen     │          │   audit row │          │     │          │  port    │
│         │          │  cmdId   │          │             │          │     │          │          │
│         │          │          │          │             │          │     │          │          │
│ 200 OK  │<─JSON────│          │          │             │          │     │          │          │
│ {sent}  │  {cmdId, │          │          │             │          │     │          │          │
│         │  status} │          │          │             │          │     │          │          │
│         │          │          │          │             │          │     │          │          │
│ (async) │          │          │  Transp. │   Update    │<─Transp──│     │<─IPC─────│  Result  │
│  UI     │          │          │<─result──│   audit row │  result  │     │  ACK     │          │
│  update │          │          │          │   w/result  │          │     │          │          │
└─────────┘          └──────────┘          └─────────────┘          └─────┘          └──────────┘
```

### Replay Safety

**Critical Rule**: **In replay mode, commands must NEVER execute hardware**.

**Implementation 1** (Client-side blocking):
```javascript
// In novaCore httpServer.py (command endpoint)
if timeline.mode == 'replay':
    return {"error": "Commands disabled during replay"}, 403
```

**Implementation 2** (Producer-side validation):
```python
# In GEM commandHandler.py
async def handle_command(subject, payload):
    cmd = json.loads(payload)
    
    # Check if command originates from replay session
    if 'replaySessionId' in cmd or cmd.get('source') == 'replay':
        logger.warning(f"Ignoring command from replay: {cmd['commandId']}")
        return  # Do not execute hardware
    
    # Execute command
    await device.execute_action(cmd)
```

**Implementation 3** (Archive tagging):
```python
# novaArchive marks replay-sourced commands
async def handle_command_request(request):
    cmd = request.json
    
    if request.headers.get('X-Replay-Session'):
        cmd['source'] = 'replay'
        cmd['replaySessionId'] = request.headers['X-Replay-Session']
    
    # Store in audit trail (marked as replay)
    await commandStore.insert(cmd)
    
    # Do NOT forward to producer if replay
    if cmd.get('source') != 'replay':
        await transport.publish(f"command.{cmd['verb']}.{cmd['entityId']}", cmd)
```

### Replay Command Viewing

**During replay**: Commands are displayed in timeline (read-only view)

**UI shows**:
- Command timestamp (when it was sent)
- Command verb + params
- Result status (success/error/timeout)
- Result message

**Browser queries**: `GET /api/commands?assetId=X&start=T1&end=T2` (from audit trail)

---

## Message Ordering

**When timestamps collide**: Favor **metadata ordering** over other messages (metadata is rarer and higher priority).

**Tie-break rule** (deterministic):
1. Compare `timestampMs` (receive-time from archive)
2. If equal, metadata messages sort first (`streamType='entity.metadata'` has priority)
3. If still equal, order by `assetId` (alphabetical)
4. If still equal, order by `sequenceNum`

**Implementation**:
```python
def message_sort_key(msg):
    # Metadata gets priority (0), others get 1
    is_metadata = 0 if msg['streamType'] == 'entity.metadata' else 1
    return (msg['timestampMs'], is_metadata, msg['assetId'], msg.get('sequenceNum', 0))

# Sort messages
messages.sort(key=message_sort_key)
```

**Rationale**: Metadata is critical for rendering (defines entity hierarchy, card types). If metadata and position arrive at same time, metadata must be applied first.

---

## Scope & Network Control

**Scope**: Authorization boundary for messages and commands. **scopeId controls visibility and network spread**.

**scopeId**: Identifier for scope (e.g., `payload-1`, `payload-2`, `ground`).

### Rules

1. **Local payload** publishes with its own scopeId
2. **Ground/aggregator** subscribes broadly across scopes and therefore receives all
3. **Subjects themselves are not \"local/global\"** - visibility is controlled by scopeId + subscription rules
4. Archive stores scopeId with every message
5. Commands validated against device scopeId
6. Replay queries filter by scopeId

### Example

- **Payload 1**: scopeId = `payload-1` (publishes to transport with scopeId=payload-1)
- **Payload 2**: scopeId = `payload-2` (publishes to transport with scopeId=payload-2)  
- **Ground**: Subscribes to `*` (receives both payload-1 and payload-2 via transport wildcards)

### Transport Implementation

```python
# Payload publishes with its scopeId (via /transport)
await transport.publish(f'stream.truth.position.{scopeId}.{assetId}', payload)

# Ground subscribes to all scopes (wildcard)
await transport.subscribe('stream.truth.position.*.*', handler)  # Wildcard scopeId + assetId

# Or scope-specific subscription
await transport.subscribe('stream.truth.position.payload-1.*', handler)  # Only payload-1
```

### Aggregator Pattern

**Remote payload** → NATS → **Ground novaArchive** (aggregator role)

**Ground novaArchive**:
- Subscribes to `stream.*.*.*.*` (all scopes, all streams)
- Ingests to local SQLite (merged database across all scopes)
- Republishes to UI lane: `archive.*.ui.*` (all scopes)
- Browser can filter by scopeId in UI

**Future**: Multi-tenant isolation, role-based scope access, ACLs per scope.

---

## Data Flow: Live Operations

**See full document continuation in nova api.md for detailed API specifications and complete flow diagrams.**

---

**Document Authority**: This is the master architecture reference. All other documentation must align with this document. Conflicting information should be resolved by updating this document first.

**Continued in**: [nova api.md](nova%20api.md) for API specifications, [gem architecture.md](gem%20architecture.md) for GEM implementation details.
