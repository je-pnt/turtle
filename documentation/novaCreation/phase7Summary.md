# Phase 7: UI Plane — Comprehensive Closeout Summary

**Document Status**: Phase 7 Closeout  
**Date**: 2026-01-29  
**Author**: Engineering  

---

## 1. Architecture Check — Invariants & Contracts

Phase 7 implements the UI Plane as specified in `nova architecture.md`. Key contracts verified:

### 1.1 Lanes
| Lane | Purpose | Phase 7 Usage |
|------|---------|---------------|
| `raw` | Byte frames | Not used directly by UI Plane |
| `parsed` | Typed messages | Source for UiUpdate production at hardwareService |
| `ui` | Partial upserts | **Primary lane** — UiUpdate + UiCheckpoint events |
| `command` | Request/Progress/Result | Card actions trigger CommandRequest |
| `metadata` | Descriptors, Manifests | ManifestPublished + *Descriptor events |

### 1.2 Identity Model (nova architecture.md §3)
```
scopeId | systemId | containerId | uniqueId
```
- **systemId**: Data system producing truth (e.g., `hardwareService`, `nova`)
- **containerId**: Node/payload instance (e.g., `payload-alpha`)
- **uniqueId**: Entity identifier (e.g., `396620287218-F9P`)

### 1.3 Shield Eligibility Rule
```
systemId === 'nova'  → INTERNAL (NOT a shield)
systemId !== 'nova'  → EXTERNAL (CAN be shield if publishes *Descriptor)
```

**Implementation**: `nova/ui/js/entities.js` lines 37-42
```javascript
// Step 3: Skip internal NOVA events
if (sysId === 'nova') {
    return;
}
```

### 1.4 Two Timebases
| Timebase | Controlled By | Meaning |
|----------|---------------|---------|
| `sourceTruthTime` | Producer | Never overwritten by NOVA |
| `canonicalTruthTime` | NOVA ingest | Wall-clock receive time at ingest |

### 1.5 UiCheckpoint Cadence (Deterministic, Bucketed)
- **Bucketed by timeline time**: Checkpoints at 60-minute boundaries of `sourceTruthTime`
- **Bucket key**: `(identity, viewId, manifestVersion, bucketStart)`
- **At most one checkpoint per bucket** (idempotent)
- **Deterministic**: Identical data → identical checkpoint positions (pure function of timeline time, not wall-clock)
- **Discovery**: First bucket is computed from first UiUpdate's `sourceTruthTime`, not "now"

```python
def computeBucketStart(timestamp, intervalMinutes=60):
    dt = parse(timestamp)
    minutes = (dt.hour * 60 + dt.minute) // intervalMinutes * intervalMinutes
    return dt.replace(hour=minutes//60, minute=minutes%60, second=0, microsecond=0)
```

---

## 2. Root-Cause Reality — What Phase 7 Fixed

### 2.1 Satellite Table Empty (svInfo showing null El/Az)

**Root Cause**: UBX `nav_sig` message was publishing `elev: null, azim: null` fields, overwriting valid values from `nav_sat`.

**Data Flow Problem**:
```
nav_sat → {cno: 45, elev: 35, azim: 120}  ✓
nav_sig → {cno: 48, elev: null, azim: null}  ✗ (overwrites!)
```

**Fix**: Modified `sdk/hardwareService/devices/ubxDevice.py` to only publish `cno` from nav_sig:
```python
# BEFORE (broken):
svData = {'cno': maxCno, 'elev': None, 'azim': None}

# AFTER (fixed):
svData = {'cno': maxCno}  # Only cno, no null elev/azim
```

### 2.2 Card Online/Offline Indicator Always Online

**Root Cause**: CSS selector `.card` didn't match actual class `.entity-card`

**Fix**: `nova/ui/js/entities.js` line 152
```javascript
// BEFORE:
var card = ind.closest('.card');

// AFTER:
var card = ind.closest('.entity-card');
```

### 2.3 Stream Not Receiving UI Events

**Root Cause**: Stream request missing `lanes` parameter, defaulting to raw/parsed only.

**Fix**: `nova/ui/js/timeline.js` now includes:
```javascript
lanes: ['metadata', 'ui', 'command']
```

---

## 3. Decision Log

### 3.1 Shield Eligibility
| Decision | Rationale |
|----------|-----------|
| Only external systems create shields | NOVA-internal events (manifests, checkpoints) shouldn't appear as hardware entities |
| Descriptor-only shield creation | Guarantees entity has displayName and entityType |

### 3.2 Card Selection
| Entity Type | Card Manifest |
|-------------|---------------|
| `gnss-receiver`, `ubx`, `mosaic-x5`, `septentrio` | `GNSS_RECEIVER_CARD` |
| `spectrum-analyzer`, `rsp1b` | `SPECTRUM_ANALYZER_CARD` |
| (all other) | `DEFAULT_CARD` |

### 3.3 UiUpdate allowedKeys
Keys per manifest are defined in `nova/core/manifests/cards.py`:
- **GNSS_RECEIVER_CARD**: `lat, lon, alt, gnssTime, fixType, numSv, cn04th, avgCn0, hAcc, vAcc, pDOP, svInfo, sigInfo`
- **SPECTRUM_ANALYZER_CARD**: `centerFreq, span, rbw, peakPower`

### 3.4 UiCheckpoint Cadence
| Trigger | Reason |
|---------|--------|
| Discovery | Immediate state snapshot for new entities |
| 60 minutes | Balance between storage cost and seek efficiency |

---

## 4. Significant Code Changes

### 4.1 Deep Merge for svInfo/sigInfo
`nova/ui/js/cards.js` lines 755-772 — prevents null overwrites:
```javascript
if (key === 'svInfo' || key === 'sigInfo') {
    for (const [const_, svs] of Object.entries(value)) {
        for (const [svId, fields] of Object.entries(svs)) {
            for (const [field, fieldValue] of Object.entries(fields)) {
                if (fieldValue !== null) {  // Only non-null values
                    existing[key][const_][svId][field] = fieldValue;
                }
            }
        }
    }
}
```

### 4.2 SQLite High-Throughput Configuration
`nova/core/database.py` lines 72-87:
```python
self.conn.execute("PRAGMA journal_mode=WAL")
self.conn.execute("PRAGMA synchronous=NORMAL")
self.conn.execute("PRAGMA cache_size=-64000")      # 64MB cache
self.conn.execute("PRAGMA wal_autocheckpoint=0")   # Manual checkpoint
self.conn.execute("PRAGMA mmap_size=268435456")    # 256MB mmap
```

---

## 5. Phase 7 File Inventory

### 5.1 New Files
| File | Purpose | Lines |
|------|---------|-------|
| `nova/core/manifests/__init__.py` | Package init with exports | ~30 |
| `nova/core/manifests/base.py` | FieldType, FieldDef, Manifest base | ~200 |
| `nova/core/manifests/telemetry.py` | GnssManifest, VelocityManifest, etc | ~150 |
| `nova/core/manifests/cards.py` | CardManifest, GNSS_RECEIVER_CARD | 197 |
| `nova/core/manifests/registry.py` | ManifestRegistry | 256 |
| `nova/core/uiState.py` | UiStateManager, checkpoints | 326 |
| `nova/ui/js/entities.js` | Shield tree, online status | 230 |
| `test/test_phase7_ui_plane.py` | 22 Phase 7 tests | 602 |

### 5.2 Modified Files
| File | Changes |
|------|---------|
| `nova/core/events.py` | Added UiUpdate, UiCheckpoint classes |
| `nova/core/ingest.py` | UiStateManager hook for checkpoint generation |
| `nova/core/database.py` | SQLite optimizations, uiEvents table |
| `nova/main.py` | ManifestRegistry init, periodicCheckpoint task |
| `nova/ui/js/cards.js` | Manifest-driven rendering, deep merge |
| `nova/ui/js/display.js` | Event routing to shields + cards |
| `nova/ui/js/timeline.js` | lanes parameter in stream request |
| `nova/ui/js/websocket.js` | Debug logging for lane distribution |
| `sdk/hardwareService/devices/ubxDevice.py` | nav_sig fix (cno only) |

### 5.3 Deleted Files
None — Phase 7 is additive.

---

## 6. As-Implemented Architecture

### 6.1 Data Flow Diagram
```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ hardwareService │     │      NOVA       │     │    Browser      │
│                 │     │                 │     │                 │
│  ubxDevice.py   │     │   ingest.py     │     │  websocket.js   │
│  sbfDevice.py   │     │   database.py   │     │  timeline.js    │
│                 │     │   streaming.py  │     │  display.js     │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │ UiUpdate (ui lane)    │                       │
         ├──────────────────────▶│                       │
         │                       │                       │
         │ *Descriptor (meta)    │ ┌─────────────────┐   │
         ├──────────────────────▶│ │ UiStateManager  │   │
         │                       │ │ ─ accumulate    │   │
         │                       │ │ ─ checkpoint    │   │
         │                       │ └─────────────────┘   │
         │                       │                       │
         │                       │ streamChunk (events)  │
         │                       ├──────────────────────▶│
         │                       │                       │
         │                       │                       │ ┌─────────────┐
         │                       │                       │ │  entities   │
         │                       │                       ├▶│ (shields)   │
         │                       │                       │ └─────────────┘
         │                       │                       │
         │                       │                       │ ┌─────────────┐
         │                       │                       │ │   cards     │
         │                       │                       ├▶│ (uiState)   │
         │                       │                       │ └─────────────┘
```

### 6.2 Event Type Routing

| Event Type | Lane | Handler | Destination |
|------------|------|---------|-------------|
| `*Descriptor` | metadata | `processEntityEvent()` | shields tree |
| `ManifestPublished` | metadata | (stored, not displayed) | registry |
| `UiUpdate` | ui | `processEvent()` | cards.uiState |
| `UiCheckpoint` | ui | `processEvent()` | cards.uiState (replace) |
| `CommandRequest/Progress/Result` | command | `processEvent()` | toast feedback |

---

## 7. Bloat/Drift Audit

### 7.1 Debug Logging (TO REMOVE before production)
| File | Lines | Issue |
|------|-------|-------|
| `nova/ui/js/cards.js` line 113 | ~5 | `console.log('[Cards] renderCard...')` |
| `nova/ui/js/cards.js` lines 349-358 | ~10 | `console.log('[Cards] renderSvTableWidget...')` |
| `nova/ui/js/cards.js` lines 740-748 | ~8 | `console.log('[Cards] svInfo structure...')` |
| `nova/ui/js/websocket.js` lines 195-200 | ~5 | `console.log('[WS] Stream chunk...')` |

**Cleanup Action**: Remove or gate behind `DEBUG` flag.

### 7.2 Duplicate Code in cards.js
`nova/ui/js/cards.js` lines 799-816 — configUpload result handling duplicated:
```javascript
// Track configUpload results for display in card actions area
if (event.commandType === 'configUpload' && resultData) {
    // ... identical block appears TWICE
}
```
**Cleanup Action**: Remove duplicate block.

### 7.3 Schema Drift
None detected — all lanes match `nova architecture.md` definitions.

### 7.4 Archive Pollution
`archive/` folder contains old implementations but is properly gitignored. No action needed.

---

## 8. Full Repository Code Structure

### 8.1 Core NOVA (`nova/`)
```
nova/
├── main.py                  # Entry point, ManifestRegistry init, periodicCheckpoint
├── config.json              # Runtime configuration
├── requirements.txt         # Python dependencies
│
├── core/
│   ├── __init__.py
│   ├── canonical_json.py    # RFC 8785 JCS for EventId stability
│   ├── commands.py          # Command routing + REWIND blocking
│   ├── contract.py          # Lane, Timebase enums (single source of truth)
│   ├── contracts.py         # Architectural contracts documentation
│   ├── database.py          # SQLite truth database (849 lines)
│   ├── events.py            # Event envelope classes (1022 lines)
│   ├── export.py            # Export functionality
│   ├── fileWriter.py        # Real-time file output (Phase 6)
│   ├── ingest.py            # Ingest pipeline with UiStateManager hook (297 lines)
│   ├── ipc.py               # Inter-process communication
│   ├── ordering.py          # Event ordering contracts
│   ├── query.py             # Database query API
│   ├── streaming.py         # Server-paced streaming playback
│   ├── subjects.py          # NATS subject patterns
│   ├── transportManager.py  # Transport abstraction
│   ├── uiState.py           # UiStateManager (326 lines) - PHASE 7
│   │
│   ├── drivers/
│   │   ├── __init__.py
│   │   ├── base.py          # Driver base class
│   │   ├── positionCsv.py   # CSV position driver
│   │   ├── rawBinary.py     # Raw binary driver
│   │   └── registry.py      # Driver registry
│   │
│   └── manifests/           # PHASE 7 - Manifest system
│       ├── __init__.py      # Package exports
│       ├── base.py          # FieldType, FieldDef, Manifest (~200 lines)
│       ├── cards.py         # CardManifest definitions (197 lines)
│       ├── registry.py      # ManifestRegistry (256 lines)
│       └── telemetry.py     # Telemetry manifests (~150 lines)
│
├── server/
│   ├── __init__.py
│   ├── auth.py              # Authentication
│   ├── ipc.py               # Server IPC
│   └── server.py            # WebSocket/HTTP server
│
├── ui/
│   ├── css/
│   │   └── styles.css       # UI styles
│   ├── html/
│   │   └── index.html       # Main HTML
│   └── js/
│       ├── auth.js          # Authentication
│       ├── cards.js         # Card rendering (954 lines) - PHASE 7
│       ├── display.js       # Event routing (~60 lines)
│       ├── entities.js      # Shield tree (230 lines) - PHASE 7
│       ├── export.js        # Export functionality
│       ├── init.js          # Initialization
│       ├── split-setup.js   # Split pane setup
│       ├── timeline.js      # Timeline controller (448 lines)
│       └── websocket.js     # WebSocket client (~240 lines)
│
├── data/                    # Runtime data directory
├── exports/                 # Export output directory
└── logs/                    # Log files
```

### 8.2 SDK (`sdk/`)
```
sdk/
├── __init__.py
├── hardware_config_defaults.py  # Default hardware configurations
├── hardware_config_loader.py    # Hardware config loading
├── pyproject.toml              # Package metadata
├── requirements.txt            # SDK dependencies
├── subjects.py                 # NATS subject patterns
│
├── events/
│   ├── __init__.py
│   ├── eventBuilder.py         # Event construction helpers
│   └── eventEnvelope.py        # Event envelope utilities
│
├── globe/                      # Globe visualization utilities
├── hardwareService/
│   └── devices/
│       ├── __init__.py
│       ├── baseDevice.py       # Base device class
│       ├── ubxDevice.py        # UBX GNSS driver (718 lines) - FIXED
│       ├── sbfDevice.py        # Septentrio SBF driver
│       ├── analogOscopeDevice.py
│       └── digitalOscopeDevice.py
│
├── logging/
│   ├── __init__.py
│   ├── context.py              # Logging context
│   └── logger.py               # Structured logger
│
├── parsers/                    # Protocol parsers (UBX, NMEA, SBF)
├── streams/                    # Stream utilities
└── transport/
    ├── __init__.py
    ├── natsTransport.py        # NATS transport
    ├── nngTransport.py         # NNG transport
    ├── transportBase.py        # Transport base class
    └── transportFactory.py     # Transport factory
```

### 8.3 Tests (`test/`)
```
test/
├── check_db.py                 # Database diagnostic
├── check_ui_events.py          # UI event diagnostic
├── diagnose_flow.py            # Data flow diagnostic
├── quick_ws_test.py            # WebSocket quick test
├── test_phase5_architecture.py # Phase 5 tests
├── test_phase6_drivers.py      # Phase 6 tests
├── test_phase7_ui_plane.py     # Phase 7 tests (602 lines, 22 tests)
├── test_phases_1_to_5.py       # Phases 1-5 regression
└── test_replay_flow.py         # Replay flow test
```

### 8.4 SVS (`svs/`) — Legacy Web UI
```
svs/
├── api.py, backend.py, config.py, svs.py  # Core SVS
├── webPage.py, webPage_aiohttp.py         # Web servers
├── devices/                                # SVS device drivers
├── plugins/                                # SVS plugins
└── static/, templates/                     # Web assets
```

---

## 9. Minimal Demo/Validation Checklist

### 9.1 Pre-requisites
- [ ] NOVA running (`python nova/main.py`)
- [ ] hardwareService running with UBX device connected
- [ ] Browser open to `http://localhost:8080`

### 9.2 Shield Discovery
- [ ] After ~5 seconds, shield tree shows `hardwareService → <containerId> → <deviceId>`
- [ ] Shield shows correct icon (📡 for GNSS)
- [ ] Shield online indicator is GREEN when data flowing

### 9.3 Card Display
- [ ] Click shield to open card
- [ ] Card shows displayName and uniqueId
- [ ] Position table shows lat/lon/alt/time
- [ ] Primary section shows fixType, numSv, C/N₀₄
- [ ] Online indicator matches shield status

### 9.4 Satellite Table
- [ ] Expand "Satellites" section
- [ ] Table shows constellation groupings (GPS, GLONASS, etc.)
- [ ] Each SV row shows: SV#, C/N₀, El°, Az°
- [ ] El/Az values are populated (not "—")

### 9.5 Commands (LIVE mode only)
- [ ] Hot/Warm/Cold buttons visible
- [ ] Clicking button shows toast feedback
- [ ] In REWIND mode, buttons show "REWIND" badge

### 9.6 Timeline
- [ ] Play/Pause toggles correctly
- [ ] LIVE button returns to live tail
- [ ] Speed change switches to REWIND mode
- [ ] Slider scrub works in REWIND

---

## 10. Test Coverage Summary

| Phase | Test File | Tests | Status |
|-------|-----------|-------|--------|
| 1-5 | `test_phases_1_to_5.py` | 40 | ✅ |
| 6 | `test_phase6_drivers.py` | varies | ✅ |
| 7 | `test_phase7_ui_plane.py` | 22 | ✅ |

**Phase 7 Test Breakdown**:
| Category | Count |
|----------|-------|
| ManifestRegistry | 7 |
| UiUpdate event | 2 |
| UiCheckpoint event | 2 |
| UiStateManager | 4 |
| State-at-time query | 2 |
| Field validation | 3 |
| Event serialization | 2 |

---

## 11. Cleanup Action Items

| Priority | Item | File | Action |
|----------|------|------|--------|
| P1 | Remove debug logging | cards.js | Delete console.log statements |
| P1 | Remove duplicate block | cards.js lines 799-816 | Delete second configUpload handler |
| P2 | Gate remaining logs | websocket.js | Add DEBUG flag check |
| P3 | Document nav_sig fix | ubxDevice.py | Add comment explaining cno-only publish |

---

**Phase 7 Complete** — UI Plane fully implemented with manifest-driven cards, shield hierarchy, and deterministic checkpointing.
