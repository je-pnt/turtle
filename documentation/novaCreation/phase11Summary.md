# Phase 11 Summary: Replays Tab + Run Manifests + Presentation System

**Completed:** February 4, 2026  
**Specification:** `phase9-11Updated.md`

---

## 1. Executive Summary

Phase 11 implements the **Replays** feature for NOVA, allowing users to:
- Create **runs** (named time windows) for export purposes
- **Clamp** the timeline to a run's time window for focused review  
- **Download bundles** containing driver exports + run.json for a run's time range
- Select **signals** for hardwareService runs (GNSS recording)
- Configure **admin defaults** for entity presentations that apply to all users

This phase follows the architectural principle that **runs are NOT truth**—they are user artifacts that drive UI convenience and export generation, never altering replay-visible system state.

### Key Architecture Changes

1. **Manifest-Driven Run Types**: Run schemas are now defined by plugin manifests (`*.runManifest.py`) instead of hardcoded fields
2. **Two-Tier Presentation System**: User overrides take precedence over admin defaults
3. **UTC Time Consistency**: Timeline and all datetime inputs display UTC to prevent confusion
4. **Bounded Streaming Fix**: Reverse playback now correctly scans the full time window

---

## 2. Architecture Compliance

### Invariants Preserved

| Invariant | How Preserved |
|-----------|---------------|
| **Truth isolation** | Runs stored in `data/users/<username>/runs/`, emitting zero truth events |
| **Server statelessness** | RunStore uses only file artifacts, no per-connection state |
| **Replay safety** | Exports allowed, no hardware/C2 effects possible |
| **Single export codepath** | Bundle generation calls Phase 6 driver export via IPC |
| **Timebase non-mixing** | Each run stores one timebase, passed through to export unchanged |
| **Manifest-driven UI** | Run cards render dynamically from `*.runManifest.py` definitions |

### Design Decisions

1. **Runs are per-user artifacts** stored in filesystem, not in the truth database
2. **Last write wins** for concurrent edits (no conflict resolution)
3. **Always regenerate** bundles on download (no caching)
4. **Delete-then-rename** for folder conflicts on run rename
5. **Plugin discovery** for run manifests (sorted filename order)
6. **UTC everywhere** for timeline display and datetime inputs
7. **Admin defaults** stored per-scope with fallback chain: user → admin → factory

---

## 3. Changes Made in This Session

### 3.1 Manifest-Driven Run Architecture

**Problem**: Run fields were hardcoded in cards.js, making it difficult to add new run types.

**Solution**: Introduced manifest-driven architecture:
- Created `nova/core/manifests/runs/` directory
- Added `registry.py` with `RunManifest`, `RunField`, `FieldType` definitions
- Created `generic.runManifest.py` and `hardwareService.runManifest.py`
- Updated `cards.js` to dynamically render fields from manifests

### 3.2 Presentation System Enhancements

**Problem**: Admin defaults weren't persisting across page refresh.

**Solution**: Implemented two-tier caching in `map.js`:
- `presentationCache`: User-specific overrides (always take precedence)
- `adminDefaultsCache`: Admin defaults loaded on page load
- Added `loadAllPresentations()` that fetches both `/api/presentation` and `/api/presentation-default`
- Fallback chain: user presentation → admin default → factory defaults

**Problem**: Admin "Save as Default" button wasn't visible.

**Solution**: Added `NovaPres.setAdmin(user.role === 'admin')` call in `init.js`

**Problem**: Windows filename error with pipe character in admin default filenames.

**Solution**: Added `_scopeToFilename()` helper in `presentationStore.py` that replaces `|` with `_`

### 3.3 Timeline and Streaming Fixes

**Problem**: Reverse playback wasn't working - stream completed immediately.

**Solution**: Two fixes applied:
1. **streaming.py**: Changed bounded streaming logic to continue scanning when no events found in current window (instead of completing immediately)
2. **timeline.js**: Changed clamped REWIND mode to pass full clamp bounds (`startTimeSec` to `stopTimeSec`) instead of cursor to stop

**Problem**: Timeline showed local time but datetime inputs expected local time - caused confusion.

**Solution**: Standardized on UTC:
- `timeline.js`: Changed `updateDisplay()` to use `getUTCHours()`, `getUTCMinutes()`, `getUTCSeconds()`
- `cards.js`: Updated `formatDatetimeLocal()` and `parseDatetimeLocal()` to use UTC methods with 'Z' suffix

### 3.4 Signals Field Improvements

**Problem**: Constellations were visible even when Signals header was collapsed.

**Solution**: 
- Added missing CSS rule `.signals-field-content.collapsed { display: none; }`
- Changed constellations to always start collapsed (`constExpanded = false`)

### 3.5 Bundle Export Improvements

**Problem**: Bundle zip was empty when no telemetry data existed for the time range.

**Solution**: Updated `handleCreateBundle()` in `server.py`:
- Creates empty zip if export returns no data
- Always adds `run.json` to the bundle using `zipfile.ZipFile(bundlePath, 'a')`
- Users now always get the run definition, even if no telemetry

### 3.6 User Default Scopes

**Problem**: New users had empty `allowedScopes` array, causing 403 on presentation operations.

**Solution**: Changed `userStore.py` to set `allowedScopes = ['ALL']` for all new users

---

## 4. New Files Created

### Run Manifest System

| File | Purpose |
|------|---------|
| `nova/core/manifests/runs/__init__.py` | Package initialization |
| `nova/core/manifests/runs/registry.py` | `RunManifest`, `RunField`, `FieldType` classes, `RunManifestRegistry` for plugin discovery |
| `nova/core/manifests/runs/generic.runManifest.py` | Default/base run manifest with core fields only |
| `nova/core/manifests/runs/hardwareService.runManifest.py` | Hardware service manifest with music times and signal selection |

### Server-Side

| File | Purpose |
|------|---------|
| `nova/server/runStore.py` | Per-user run storage management. Contains `RunStore` class, `Run` dataclass, sanitization utilities. |

### Client-Side

| File | Purpose |
|------|---------|
| `nova/ui/js/replays.js` | Replays module mirroring Streams pattern. Handles run CRUD, timeline clamping, bundle downloads, and tab collapse state persistence. |

### Tests

| File | Purpose |
|------|---------|
| `test/test_phase11_replays.py` | Comprehensive test suite: syntax validation, unit tests for RunStore, and integration tests for run APIs. |

---

## 5. Files Modified

### Core

| File | Changes |
|------|---------|
| `nova/core/streaming.py` | Fixed bounded streaming to continue scanning when no events in current window (line ~127-140) |
| `nova/core/export.py` | No changes - bundle uses existing export pipeline |

### Server

| File | Changes |
|------|---------|
| `nova/server/server.py` | Added RunStore import/initialization, 9 API routes for runs, updated `handleCreateBundle()` to always include run.json in bundle zip |
| `nova/server/presentationStore.py` | Added `getAllUserOverrides()`, `getAllAdminDefaults()`, `_scopeToFilename()` helper for Windows compatibility |
| `nova/server/userStore.py` | Changed default `allowedScopes` from `[]` to `['ALL']` for new users |

### UI - HTML

| File | Changes |
|------|---------|
| `nova/ui/html/index.html` | New sidebar structure with 3 collapsible tabs (Entities, Streams, Replays). Added clamp indicator in timeline. Added `replays.js` script reference. |

### UI - CSS

| File | Changes |
|------|---------|
| `nova/ui/css/styles.css` | Added ~290 lines: collapsible sidebar tab styles, run card styles, signal toggle grid styles, clamp indicator styles. Added `.signals-field-content.collapsed { display: none; }` |

### UI - JavaScript

| File | Changes |
|------|---------|
| `nova/ui/js/cards.js` | Major refactor for manifest-driven rendering. Added `renderRunFieldByType()`, `renderArrayField()`, `renderSignalsField()`, UTC datetime handling (`formatDatetimeLocal`, `parseDatetimeLocal`), removed signal count display |
| `nova/ui/js/timeline.js` | Added `clamp` property, clamp enforcement in `updateDisplay()`, clamp clearing in `handleJumpToLive()`, changed time display to UTC, fixed `startStream()` to use full clamp bounds for REWIND mode |
| `nova/ui/js/entities.js` | Added `toggleSystemCollapse()` and `toggleContainerCollapse()` for collapsible entity tree |
| `nova/ui/js/init.js` | Added `initReplays()` call, added `NovaPres.setAdmin(user.role === 'admin')` for admin button visibility |
| `nova/ui/js/map.js` | Added `adminDefaultsCache`, updated `loadAllPresentations()` to fetch both user and admin presentations |

---

## 6. Feature Details

### 6.1 Run Manifest System

> **IMPORTANT ARCHITECTURAL DISTINCTION**:
> 
> **Run Manifests** are **presentation artifacts** (NOT truth). They define the UI schema for per-user run forms. Runs themselves are user artifacts stored per-user, never entering the truth database.
> 
> **Card Manifests** are **truth-level artifacts**. They define how truth entities are displayed in entity cards.
> 
> This distinction is critical: run manifests drive UI form rendering, while card manifests drive truth entity display.

Run types are defined via plugin manifests discovered at startup:

```
nova/core/manifests/runs/
├── __init__.py
├── registry.py              # RunManifest, RunField, FieldType, RunManifestRegistry
├── generic.runManifest.py   # Default run type (core fields only)
└── hardwareService.runManifest.py  # Hardware service with music/signals
```

**Field Types** (`FieldType` enum):
- `STRING` - Single-line text input
- `TEXT` - Multi-line textarea
- `NUMBER` - Numeric input
- `DATETIME` - UTC datetime picker
- `BOOLEAN` - Toggle/checkbox
- `SELECT` - Dropdown from options
- `ARRAY` - Array of sub-fields (e.g., music times)
- `OBJECT` - Nested object
- `SIGNALS` - Signal selection grid (custom widget)

**Manifest Discovery**:
1. Scans `*.runManifest.py` files in sorted filename order
2. Each file must export `RUN_MANIFEST` (RunManifest instance)
3. Collision on `runType` = fail fast at startup
4. `generic.runManifest.py` provides the base/default

### 6.2 Run Schema (v2 - Manifest-Driven)

```json
{
  "schemaVersion": 2,
  "runNumber": 1,
  "runName": "string",
  "runType": "generic" | "hardwareService",
  "timebase": "source" | "canonical",
  "startTimeSec": 0,
  "stopTimeSec": 0,
  "analystNotes": "",
  // ... additional fields defined by runType manifest
  "signals": {},           // For hardwareService
  "musicOnTimes": [],      // For hardwareService
  "musicOffTimes": []      // For hardwareService
}
```

### 6.3 Storage Layout

```
data/users/<username>/
├── runs/
│   ├── settings.json          # User defaults (defaultRunType, lastRunName)
│   ├── 1. My First Run/
│   │   ├── run.json           # Run definition
│   │   └── bundle.zip         # Generated on download (includes run.json)
│   └── 2. Another Run/
│       ├── run.json
│       └── bundle.zip
└── presentation/              # User presentation overrides
    └── ALL_<scope>.json

data/presentation/
└── admin-defaults/
    └── ALL_<scope>.json       # Admin default presentations
```

### 6.4 Presentation System

**Two-Tier Cache Architecture**:
```javascript
// map.js
const presentationCache = {};      // User overrides (highest priority)
const adminDefaultsCache = {};     // Admin defaults (fallback)

function getPresentationForEntity(entityId) {
    // Priority: user → admin → factory
    return presentationCache[entityId] 
        || adminDefaultsCache[entityId] 
        || factoryDefaults;
}
```

**On Page Load** (`loadAllPresentations()`):
1. Fetch `/api/presentation` → populate `presentationCache`
2. Fetch `/api/presentation-default` → populate `adminDefaultsCache`

**Admin Default Storage**:
- Filename: `ALL_<sanitized-scope>.json` (pipes replaced with underscores)
- Location: `data/presentation/admin-defaults/`

### 6.5 Signal List (Manifest-Embedded)

The available signals are defined in `hardwareService.runManifest.py` and embedded in the signals field config. The client reads them from `field.config.availableSignals`.

| Constellation | Signals |
|---------------|---------|
| GPS | L1CA, L2C, L5, L1P, L2P, L1C |
| GLONASS | L1CA, L2CA, L1P, L2P, L3 |
| Galileo | E1, E5a, E5b, E6, E5AltBOC |
| BeiDou | B1I, B1C, B2I, B2a, B2b, B3I |
| QZSS | L1CA, L2C, L5, L6, L1C, L1S |
| SBAS | L1CA, L5 |
| NAVIC | L5 |

### 6.6 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/runs` | List all runs for authenticated user |
| POST | `/api/runs` | Create new run |
| GET | `/api/runs/{runNumber}` | Get specific run |
| PUT | `/api/runs/{runNumber}` | Update run (last write wins) |
| DELETE | `/api/runs/{runNumber}` | Delete run and folder |
| POST | `/api/runs/{runNumber}/bundle` | Generate and download bundle.zip |
| GET | `/api/runs/settings` | Get user's run settings |
| PUT | `/api/runs/settings` | Update user's run settings |
| GET | `/config` | Returns all manifests (card + run) in config bundle |
| GET | `/api/presentation` | Get user's presentation overrides |
| PUT | `/api/presentation` | Save user's presentation |
| GET | `/api/presentation-default` | Get admin defaults |
| PUT | `/api/presentation-default` | Save admin default (admin only) |

> **Note**: Signal lists are embedded in the run manifest config (`field.config.availableSignals`), not a separate API endpoint. This ensures the manifest is the single source of truth for run UI schema.

### 6.7 Timeline Clamp

- **Clamp on select**: Sets `timeline.clamp = {startTimeSec, stopTimeSec, timebase}`
- **Enforcement**: `updateDisplay()` restricts cursor within clamp bounds
- **Exit**: "Jump to Live" clears clamp automatically
- **UI indicator**: 🔒 shown in timeline when clamped

### 6.8 UTC Time Display

All times are displayed in UTC for consistency:
- Timeline shows `HH:MM:SS.mmm` in UTC with "UTC" label
- Datetime inputs format/parse as UTC (with 'Z' suffix internally)
- Prevents confusion between timeline display and input fields

---

## 7. Complete Codebase Structure

```
turtle/
├── documentation/
│   ├── guidelines                    # Development rules and architecture constraints
│   ├── nova architecture.md          # Source of truth for system design
│   └── novaCreation/
│       ├── implementationPlan.md     # Original implementation roadmap
│       ├── phase1Summary.md          # Phase 1: Core data model
│       ├── phase2Summary.md          # Phase 2: Subject naming
│       ├── phase3summary.md          # Phase 3: Event flow
│       ├── phase4Summary.md          # Phase 4: Performance
│       ├── phase5Summary.md          # Phase 5: Replay architecture
│       ├── phase6Summary.md          # Phase 6: Driver exports
│       ├── phase7Summary.md          # Phase 7: UI cleanup
│       ├── phase9Summary.md          # Phase 9: Authentication
│       ├── phase9-11Updated.md       # Phase 9-11 specification
│       ├── phase10Summary.md         # Phase 10: Local Cesium
│       ├── phase11Summary.md         # Phase 11: Replays (THIS FILE)
│       └── ...                       # Other planning docs
│
├── hardware-config.json              # Hardware configuration for SDK
│
├── nova/                             # NOVA Core Application
│   ├── main.py                       # Application entry point
│   ├── config.json                   # Server configuration
│   ├── requirements.txt              # Python dependencies
│   │
│   ├── core/                         # Truth Engine (server-authoritative)
│   │   ├── __init__.py
│   │   ├── database.py               # SQLite truth store (nova_truth.db)
│   │   ├── ingest.py                 # Event ingestion pipeline
│   │   ├── query.py                  # Time-range queries
│   │   ├── streaming.py              # Live/replay streaming (fixed bounded reverse)
│   │   ├── ordering.py               # Event ordering (sourceTime/canonicalTime)
│   │   ├── events.py                 # Event type definitions
│   │   ├── subjects.py               # Subject hierarchy (system|container|unique)
│   │   ├── contract.py               # Lane contracts
│   │   ├── contracts.py              # Contract registry
│   │   ├── commands.py               # Command handling
│   │   ├── export.py                 # Data export orchestration
│   │   ├── fileWriter.py             # File export utilities
│   │   ├── transportManager.py       # Transport layer management
│   │   ├── ipc.py                    # Inter-process communication
│   │   ├── uiState.py                # UI state computation
│   │   ├── canonical_json.py         # Deterministic JSON serialization
│   │   │
│   │   ├── drivers/                  # Export Drivers (Phase 6)
│   │   │   ├── __init__.py
│   │   │   ├── base.py               # BaseDriver abstract class
│   │   │   ├── registry.py           # Driver registration
│   │   │   ├── positionCsv.py        # CSV position export
│   │   │   └── rawBinary.py          # Raw binary export
│   │   │
│   │   └── manifests/                # Card/Run Manifests (UI definitions)
│   │       ├── __init__.py
│   │       ├── base.py               # BaseManifest
│   │       ├── registry.py           # Card manifest registration
│   │       ├── cards.py              # Card type definitions
│   │       ├── telemetry.py          # Telemetry card
│   │       ├── default.manifest.py   # Default entity card
│   │       ├── gnssReceiver.manifest.py  # GNSS receiver card
│   │       ├── spectrum.manifest.py  # Spectrum analyzer card
│   │       ├── setupStreams.manifest.py  # Stream setup card
│   │       ├── tcpStream.manifest.py # TCP stream card
│   │       │
│   │       └── runs/                 # [NEW] Run Manifests (Phase 11)
│   │           ├── __init__.py
│   │           ├── registry.py       # RunManifest, RunField, FieldType, Registry
│   │           ├── generic.runManifest.py     # Default run type
│   │           └── hardwareService.runManifest.py  # HW service with signals
│   │
│   ├── server/                       # HTTP/WebSocket Server
│   │   ├── __init__.py
│   │   ├── server.py                 # aiohttp server, all HTTP routes
│   │   ├── ipc.py                    # IPC client for Core communication
│   │   ├── auth.py                   # JWT authentication utilities
│   │   ├── userStore.py              # User management (bcrypt, roles, default scopes)
│   │   ├── streamStore.py            # Output stream definitions
│   │   ├── streamEntities.py         # Stream entity management
│   │   ├── presentationStore.py      # Entity presentation (colors, models, admin defaults)
│   │   ├── runStore.py               # Per-user run storage (Phase 11)
│   │   ├── tcp.py                    # TCP stream server
│   │   ├── data/                     # Server-side data
│   │   └── streams/                  # Stream configurations
│   │
│   ├── ui/                           # Web UI
│   │   ├── html/
│   │   │   ├── index.html            # Main application (map, timeline, panels)
│   │   │   ├── login.html            # Login page
│   │   │   ├── register.html         # Registration page
│   │   │   ├── admin.html            # Admin panel
│   │   │   └── approval-pending.html # Pending approval page
│   │   │
│   │   ├── css/
│   │   │   ├── styles.css            # Main styles (cards, shields, timeline, runs, signals)
│   │   │   ├── auth.css              # Login/register styles
│   │   │   ├── chat.css              # Chat panel styles
│   │   │   ├── map.css               # Map container styles
│   │   │   └── presentation.css      # Presentation dialog styles
│   │   │
│   │   ├── js/
│   │   │   ├── init.js               # Application bootstrap (includes admin flag)
│   │   │   ├── websocket.js          # WebSocket connection management
│   │   │   ├── timeline.js           # LIVE/REWIND timeline + clamp (UTC display)
│   │   │   ├── display.js            # Event display routing
│   │   │   ├── entities.js           # Entity shields + collapsible tree
│   │   │   ├── streams.js            # Stream shields + cards
│   │   │   ├── replays.js            # Run shields + cards (Phase 11)
│   │   │   ├── cards.js              # Card rendering (manifest-driven runs, UTC datetimes)
│   │   │   ├── map.js                # Cesium globe + presentation caches
│   │   │   ├── presentation.js       # Entity presentation editor
│   │   │   ├── chat.js               # Real-time chat
│   │   │   ├── auth.js               # Client-side auth utilities
│   │   │   ├── login.js              # Login page logic
│   │   │   ├── register.js           # Registration logic
│   │   │   ├── admin.js              # Admin panel logic
│   │   │   └── split-setup.js        # Panel resizing
│   │   │
│   │   ├── icons/                    # SVG icons
│   │   ├── assets/                   # Static assets
│   │   ├── cesium/                   # Local Cesium.js (Phase 10)
│   │   └── favicon.ico
│   │
│   ├── data/                         # Runtime Data
│   │   ├── nova_truth.db             # SQLite truth database
│   │   ├── streams.db                # Stream definitions
│   │   ├── users.json                # User accounts (scopes default to ['ALL'])
│   │   ├── files/                    # Ingested files
│   │   ├── presentation/             # Entity presentations
│   │   │   └── admin-defaults/       # [NEW] Admin default presentations
│   │   │       └── ALL_<scope>.json
│   │   └── users/                    # Per-user data
│   │       └── <username>/
│   │           ├── presentation/     # User presentation overrides
│   │           │   └── ALL_<scope>.json
│   │           └── runs/             # User's runs (Phase 11)
│   │               ├── settings.json
│   │               └── {N}. {name}/
│   │                   ├── run.json
│   │                   └── bundle.zip
│   │
│   ├── exports/                      # Generated exports
│   └── logs/                         # Server logs
│
├── sdk/                              # Hardware Service SDK
│   ├── __init__.py
│   ├── subjects.py                   # Subject utilities
│   ├── pyproject.toml                # Package definition
│   ├── requirements.txt              # SDK dependencies
│   ├── install.sh                    # Installation script
│   ├── start.sh                      # Startup script
│   ├── hardware_config_loader.py     # Config loading
│   ├── hardware_config_defaults.py   # Default configurations
│   │
│   ├── events/                       # Event Building
│   │   ├── __init__.py
│   │   ├── eventBuilder.py           # Event construction
│   │   └── eventEnvelope.py          # Event envelope format
│   │
│   ├── streams/                      # Stream Utilities
│   │   ├── __init__.py
│   │   ├── streamBuilder.py          # Stream construction
│   │   └── streamMessage.py          # Stream message format
│   │
│   ├── transport/                    # Transport Layers
│   │   ├── __init__.py
│   │   ├── transportBase.py          # Abstract transport
│   │   ├── transportFactory.py       # Transport instantiation
│   │   ├── natsTransport.py          # NATS transport
│   │   └── nngTransport.py           # NNG transport
│   │
│   ├── parsers/                      # Protocol Parsers
│   │   ├── __init__.py
│   │   ├── globe.py                  # Coordinate utilities
│   │   ├── geoidHeights.csv          # Geoid height data
│   │   ├── nmea.py                   # NMEA sentence parser
│   │   ├── ubx.py                    # u-blox UBX parser
│   │   └── sbf.py                    # Septentrio SBF parser (signal source)
│   │
│   ├── globe/                        # Geographic Utilities
│   │   ├── __init__.py
│   │   ├── globe.py                  # Coordinate transforms
│   │   ├── geoidHeights.csv          # Geoid data
│   │   └── visualization/            # Visualization helpers
│   │
│   ├── logging/                      # SDK Logging
│   │   ├── __init__.py
│   │   ├── logger.py                 # Logger implementation
│   │   └── context.py                # Logging context
│   │
│   └── hardwareService/              # Hardware Service Application
│       ├── __init__.py
│       ├── __main__.py               # Entry point
│       ├── main.py                   # Service initialization
│       ├── hardwareService.py        # Main service class
│       ├── novaAdapter.py            # NOVA integration
│       ├── ioLayer.py                # Hardware I/O abstraction
│       ├── configManager.py          # Configuration management
│       ├── restartManager.py         # Device restart handling
│       ├── subjects.py               # Subject definitions
│       ├── config.json               # Service configuration
│       ├── API.txt                   # API documentation
│       ├── requirements.txt          # Service dependencies
│       ├── regressionTest.py         # Regression tests
│       ├── devices/                  # Device drivers
│       ├── plugins/                  # Plugin system
│       ├── WF_SDK/                   # Waveforms SDK
│       └── logging/                  # Service logging
│
├── test/                             # Test Suite
│   ├── test_phases_1_to_5.py         # Core architecture tests
│   ├── test_phase5_architecture.py   # Replay architecture tests
│   ├── test_phase6_drivers.py        # Driver export tests
│   ├── test_phase7_ui_plane.py       # UI plane tests
│   ├── test_phase8_tcp_manifests.py  # TCP/manifest tests
│   ├── test_phase9_auth.py           # Authentication tests
│   ├── test_phase11_replays.py       # Replays tests (Phase 11)
│   ├── test_replay_flow.py           # Replay flow tests
│   ├── check_db.py                   # Database inspection
│   ├── check_ui_events.py            # UI event inspection
│   ├── diagnose_flow.py              # Flow diagnostics
│   ├── quick_ws_test.py              # WebSocket quick test
│   ├── test.py                       # General tests
│   ├── logs/                         # Test logs
│   └── nova/                         # Test fixtures
│
└── logs/                             # Application logs
```

---

## 8. Test Coverage

### Test File: `test/test_phase11_replays.py`

| Category | Tests | Description |
|----------|-------|-------------|
| **Syntax** | 9 | Verify all Phase 11 code parses correctly |
| **Unit** | 6 | RunStore logic with isolated temp directories |
| **Integration** | 7 | API endpoints with running server |

### Run Tests
```bash
# All tests (integration skipped if no server)
python test/test_phase11_replays.py

# With pytest
python -m pytest test/test_phase11_replays.py -v
```

---

## 9. Bug Fixes Summary

| Issue | Root Cause | Fix |
|-------|------------|-----|
| Presentation 403 errors | Empty `allowedScopes` for new users | Default to `['ALL']` in userStore.py |
| Admin defaults not persisting | No client-side cache for admin defaults | Added `adminDefaultsCache` in map.js |
| Windows filename error | Pipe character `\|` in filenames | Added `_scopeToFilename()` to replace with `_` |
| Admin button not visible | `isAdmin` flag not set in presentation.js | Added `NovaPres.setAdmin()` call in init.js |
| Reverse playback not working | Bounded stream completing on first empty window | Changed to continue scanning until boundary |
| Reverse playback not starting | startTime set to cursor instead of clamp start | Use full clamp bounds for clamped REWIND |
| Timeline/datetime mismatch | Timeline showed local, inputs expected UTC | Changed both to use UTC consistently |
| Signals not collapsing | CSS class mismatch (wrong selector) | Added `.signals-field-content.collapsed` rule |
| Constellations visible | Auto-expand logic exposing all | Changed `constExpanded = false` |
| Bundle empty | No run.json when no telemetry | Always add run.json to bundle zip |

---

## 10. Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| Runs persist across page reload and server restart | ✅ |
| Auto-increment runNumber works per user | ✅ |
| Default runName = last entered | ✅ |
| hardwareService signal selections persist | ✅ |
| Clamp restricts playback to run window | ✅ |
| Jump to Live exits clamp | ✅ |
| Bundle export includes run.json | ✅ |
| Bundle export matches driver output | ✅ |
| Collapsible top-level tabs (Entities/Streams/Replays) | ✅ |
| Collapsible sub-tabs (System > Container) | ✅ |
| No truth events emitted by run operations | ✅ |
| Manifest-driven run fields | ✅ |
| Admin defaults persist for all users | ✅ |
| UTC time display throughout | ✅ |
| Reverse playback works correctly | ✅ |
| Signals collapse properly | ✅ |

---

## 11. Known Limitations

1. **Signal availability gating**: The spec requires showing only signals present in the selected receiver's metadata. This needs a receiver selector UX, which was not specified in Phase 11. Currently, all 34 signals are shown.

2. **Receiver selection**: The run form doesn't have a receiver dropdown for hardwareService runs. This would be needed for proper signal availability filtering.

3. **Clamp cursor position**: When clamping to a run, the cursor always starts at the forward direction's beginning. Ideally it would position based on current rate direction.

---

## 12. Related Documentation

- **Specification**: `documentation/novaCreation/phase9-11Updated.md`
- **Architecture**: `documentation/nova architecture.md`
- **Guidelines**: `documentation/guidelines`
