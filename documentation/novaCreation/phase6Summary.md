# Phase 6 Summary: Drivers and Export Parity

**Status**: ✅ Complete
**Tests**: 19 passing (40 total with Phases 1-5)
**Date**: Phase 6 implementation complete

---

## Overview

Phase 6 implements the driver plugin system for file/folder writes and exports, ensuring real-time and replay use the same codepath (export parity guarantee).

### Key Deliverables
1. ✅ Driver plugin architecture with registry
2. ✅ RawBinaryDriver for raw lane → `raw.bin`
3. ✅ PositionCsvDriver for Position messageType → `llas.csv`
4. ✅ FileWriter for real-time file writing (ingest only)
5. ✅ Export using same driver codepath (parity)
6. ✅ DriverBinding metadata events
7. ✅ Correct folder hierarchy for replay
8. ✅ **Binding-at-time(T) resolution for exports**
9. ✅ **Ingest-order (rowid) for export parity**

---

## Critical Parity Contracts

### ⚠️ Two Separate Ordering Contracts

NOVA has **two distinct ordering contracts** for different purposes:

| Contract | Purpose | Ordering Rule |
|----------|---------|---------------|
| **Global Truth Ordering** | Queries, Streaming, UI display | timebase + lane priority + eventId |
| **File/Export Parity Ordering** | Driver file writes, exports | Ingest order (rowid) |

**These are NOT interchangeable.** The global truth ordering (Phase 4) remains the authoritative contract for all query/stream/UI operations. The file parity ordering is a **sub-contract specific to Phase 6** for matching real-time files with exports.

### File/Export Parity Ordering (Phase 6 Sub-Contract)

**Files and exports use INGEST ORDER (rowid), NOT timestamp order.**

This is a **narrow sub-contract** that applies ONLY to:
- FileWriter writing events to disk
- Export generating files for download

It does NOT apply to:
- Database queries for UI display
- Streaming to connected clients
- Replay/playback ordering

- FileWriter writes as events arrive (implicitly ingest order)
- Export uses `ingestOrder=True` to match real-time writes
- This is the ONLY ordering that can match real-time files

**Why not timestamp order for files?**
Events frequently arrive out-of-order. If payload sends events at T1, T2, T3 but network delivers them as T2, T1, T3:
- Daily file order: T2, T1, T3 (arrival)
- Timestamp order: T1, T2, T3
- **Parity breaks!**

```python
# Export uses ingest order for FILE PARITY ONLY
events = db.queryEvents(startTime, stopTime, ingestOrder=True)

# Normal queries use timebase ordering (Global Truth Contract)
events = db.queryEvents(startTime, stopTime, timebase=Timebase.CANONICAL)
```

### Binding Resolution Contract

**DriverBinding is authoritative once it exists.**

- FileWriter emits DriverBinding on first write per stream
- Export resolves binding-at-time(eventTime) first
- If no binding exists, falls back to `registry.selectDriver()`
- This ensures historical exports use historical driver mappings

**Why binding-at-time(T)?**
If we add a new driver in 2027 that claims `Lane.RAW`, exports of 2026 data would suddenly use the NEW driver. Historical exports become non-reproducible.

```python
# Export resolution order:
1. Look up DriverBinding for (targetId, lane) at event time
2. If binding exists and effectiveTime <= eventTime, use that driver
3. Otherwise fall back to registry.selectDriver()
```

---

## Architecture

### Folder Hierarchy
```
{outputDir}/{YYYY-MM-DD}/{systemId}/{containerId}/{uniqueId}/{filename}

Example:
  nova/data/files/2026-01-26/ubx-m10/payload-1/gnss/llas.csv
  nova/data/files/2026-01-26/ubx-m10/payload-1/gnss/raw.bin
```

This structure enables:
- **Replay**: Reprocess a day by feeding files back through parsers
- **Traceability**: Clear mapping from file → source entity
- **Isolation**: Different entities never share files

### Driver Selection Flow
```
Event → FileWriter → DriverRegistry.getDriver(lane, messageType?)
                              ↓
                     [RawBinaryDriver | PositionCsvDriver | None]
                              ↓
                     Driver.write(event) → file path
```

### Export Parity Guarantee
```
Real-time:  Ingest → DB + FileWriter(Drivers) → files
Export:     Query[T0..T1] → Drivers → export files

Same drivers, same DriverBinding resolution → identical output
```

---

## Files Implemented

### nova/core/drivers/

| File | Purpose |
|------|---------|
| `__init__.py` | Module exports |
| `base.py` | Abstract BaseDriver class with DriverCapabilities |
| `rawBinary.py` | Raw lane → raw.bin (preserves byte boundaries) |
| `positionCsv.py` | Position messageType → llas.csv |
| `registry.py` | Driver plugin registry, deterministic selection |

### nova/core/

| File | Purpose |
|------|---------|
| `fileWriter.py` | Real-time file writing from ingest |
| `export.py` | Export execution using same drivers |

### nova/server/

| File | Purpose |
|------|---------|
| `server.py` | Export WebSocket handler + download endpoint |
| `ipc.py` | Export IPC messaging |

### nova/ui/js/

| File | Purpose |
|------|---------|
| `export.js` | Export dialog UI, download handling |

---

## Driver Details

### BaseDriver (base.py)

```python
@dataclass
class DriverCapabilities:
    driverId: str           # Unique identifier
    version: str            # Semantic version
    supportedLanes: List[Lane]  # Which lanes this driver handles
    supportedMessageTypes: Optional[List[str]]  # None = all
    description: str

class BaseDriver(ABC):
    @abstractmethod
    def write(event: Dict) -> Optional[Path]
    
    @abstractmethod
    def finalize() -> None
    
    def _buildPath(event, filename) -> Path:
        # Returns: {outputDir}/{date}/{systemId}/{containerId}/{uniqueId}/{filename}
```

### RawBinaryDriver (rawBinary.py)

- **Capabilities**:
  - `driverId`: "raw-binary-driver"
  - `version`: "1.0.0"
  - `supportedLanes`: [Lane.RAW]
  - `supportedMessageTypes`: None (all)
  
- **Output**: `raw.bin`
- **Behavior**: Appends base64-decoded bytes, preserves byte boundaries

### PositionCsvDriver (positionCsv.py)

- **Capabilities**:
  - `driverId`: "position-csv-driver"
  - `version`: "1.0.0"
  - `supportedLanes`: [Lane.PARSED]
  - `supportedMessageTypes`: ["Position"]
  
- **Output**: `llas.csv`
- **Columns**: `sourceTruthTime (UTC), iTOW (ms), latitude (deg), longitude (deg), altitude (HAE-m), fixType`
- **Behavior**: Only accepts Position messageType, rejects all others

---

## FileWriter (fileWriter.py)

### Responsibility
- Runs ONLY on ingest (never on replay/query/stream)
- Emits DriverBinding metadata on first write per stream
- Uses drivers to write events to daily files

### Key Logic

```python
def writeEvent(event: Dict[str, Any]) -> Optional[Path]:
    # Get lane
    lane = Lane(event.get('lane'))
    
    # Get driver (messageType for parsed events)
    messageType = event.get('messageType') if lane == Lane.PARSED else None
    driver = registry.getDriver(lane, messageType)
    
    if driver is None:
        return None  # No driver for this combination
    
    # Emit DriverBinding on first write
    streamKey = f"{systemId}/{containerId}/{uniqueId}/{lane}"
    if streamKey not in _boundStreams:
        _emitDriverBinding(event, driver)
        _boundStreams.add(streamKey)
    
    # Write via driver
    return driver.write(event)
```

### DriverBinding Emission

On first write for a stream, FileWriter emits a DriverBinding metadata event:

```json
{
  "lane": "Metadata",
  "messageType": "DriverBinding",
  "payload": {
    "targetId": "{systemId}/{containerId}/{uniqueId}",
    "targetLane": "Raw",
    "driverId": "raw-binary-driver",
    "version": "1.0.0",
    "effectiveTime": "2026-01-26T10:00:00Z"
  }
}
```

This enables:
- Audit trail of which driver wrote which file
- Future driver version migration
- Export to use correct driver for historical data

---

## Export (export.py)

### Flow
1. Receive ExportRequest with time window
2. Query events from DB (bounded read, **ingest order**)
3. **Pre-load DriverBindings for binding-at-time resolution**
4. Create DriverRegistry for export folder
5. For each event, **resolve driver via binding-at-time(T) or registry fallback**
6. Write event via resolved driver
7. Finalize drivers (close files)
8. Create zip archive
9. Return download link

### Binding-at-time Resolution

```python
def _resolveDriver(self, targetId, lane, event, bindings, registry):
    # 1. Build binding key
    bindingKey = f"{targetId}|{lane.value}"
    
    # 2. Check for binding at event time
    binding = bindings.get(bindingKey)
    if binding:
        eventTime = event.get('canonicalTruthTime')
        effectiveTime = binding.get('effectiveTime')
        
        if effectiveTime <= eventTime:
            driverId = binding.get('driverId')
            driver = registry.getDriver(driverId)
            if driver:
                return driver
    
    # 3. Fall back to registry selection
    return registry.selectDriver(lane, messageType)
```

### Parity Test

The export parity test verifies:
```python
def test_export_matches_realtime():
    # 1. Ingest events via FileWriter → files (ingest order)
    # 2. Export same time window with ingestOrder=True → export files
    # 3. Compare file contents → must be identical
```

---

## Tests (test_phase6_drivers.py)

### TestDriverRegistry (6 tests)
- `test_load_builtin_drivers` - Registry loads both drivers
- `test_select_raw_driver` - Raw lane → RawBinaryDriver
- `test_select_position_driver` - Parsed + Position → PositionCsvDriver
- `test_no_driver_for_other_parsed` - Parsed + non-Position → None
- `test_deterministic_selection` - Same inputs → same driver
- `test_no_driver_for_unknown_lane` - Unknown lane → None

### TestRawBinaryDriver (2 tests)
- `test_write_preserves_bytes` - Binary data integrity
- `test_appends_to_daily_file` - Multiple writes append

### TestPositionCsvDriver (2 tests)
- `test_write_creates_csv` - CSV with correct columns
- `test_only_accepts_position_messageType` - Rejects non-Position

### TestFileWriter (3 tests)
- `test_start_stop` - Lifecycle management
- `test_write_raw_event` - Raw event → file
- `test_skips_no_driver_lanes` - No driver → no file

### TestExportParity (2 tests)
- `test_export_raw_matches_realtime` - Raw export = real-time
- `test_export_position_csv_matches_realtime` - CSV export = real-time

### TestFileWriterNotOnReplay (2 tests)
- `test_ingest_triggers_filewriter` - Ingest → FileWriter called
- `test_query_does_not_trigger_filewriter` - Query → no FileWriter

### TestBindingAtTime (1 test) - NEW
- `test_export_uses_historical_binding` - Export uses DriverBinding even if registry would pick different driver today

### TestIngestOrderParity (1 test) - NEW
- `test_out_of_order_events_have_parity` - Inject out-of-order events, prove daily files and export match

---

## Configuration

### nova/config.json additions

```json
{
  "dataDir": "./nova/data/files",   // FileWriter output
  "exportDir": "./nova/exports"      // Export output
}
```

### Default Paths
- **FileWriter output**: `./nova/data/files/{date}/{systemId}/{containerId}/{uniqueId}/`
- **Export output**: `./nova/exports/{exportId}/{date}/{systemId}/{containerId}/{uniqueId}/`

---

## Design Decisions

### 1. Position Emitted by Device Parser (Not NovaAdapter)

**Problem**: NovaAdapter had protocol-aware code that inspected parsed messages to emit Position messageType.

**Solution**: Moved Position emission to `ubxDevice.py` where the actual parsing happens. NovaAdapter is now protocol-agnostic.

**Benefits**:
- Clean separation of concerns
- Easier to add new devices with different position formats
- NovaAdapter focuses only on transport

### 2. PositionCsvDriver Only Accepts Position

**Problem**: Originally wrote all parsed events to CSV.

**Solution**: Driver explicitly checks `messageType == "Position"` and returns None for others.

**Benefits**:
- Clean llas.csv with only position data
- Other parsed events can have their own drivers later
- Explicit contract (no surprise files)

### 3. DriverBinding on First Write

**Problem**: Need to track which driver wrote which file for audit/replay.

**Solution**: FileWriter tracks `_boundStreams` and emits DriverBinding metadata on first write per stream.

**Benefits**:
- Complete audit trail
- Enables driver version migration
- Export can use historical driver mappings

### 4. Same Drivers for Real-time and Export

**Problem**: Risk of drift between real-time files and exports.

**Solution**: Both FileWriter and Export use DriverRegistry with identical drivers.

**Benefits**:
- Guaranteed parity
- Single codepath to maintain
- Byte-for-byte identical output

---

## Exit Criteria Verification

From implementation plan:

| Criteria | Status |
|----------|--------|
| Export parity test: export matches ordered DB query | ✅ `test_export_raw_matches_realtime`, `test_export_position_csv_matches_realtime` |
| Driver registry loads plugins correctly | ✅ `test_load_builtin_drivers` |
| Deterministic driver selection | ✅ `test_deterministic_selection` |
| FileWriter triggered on ingest only | ✅ `test_ingest_triggers_filewriter`, `test_query_does_not_trigger_filewriter` |

---

## API Contracts

### Position Event (from device parser)

```json
{
  "lane": "Parsed",
  "messageType": "Position",
  "sourceTruthTime": "2026-01-26T10:00:00.000Z",
  "payload": {
    "latitude": 37.7749,
    "longitude": -122.4194,
    "altitude": 10.5,
    "fixType": 3,
    "iTOW": 123456789
  },
  "systemId": "ubx-m10",
  "containerId": "payload-1",
  "uniqueId": "gnss"
}
```

### llas.csv Output

```csv
sourceTruthTime (UTC),iTOW (ms),latitude (deg),longitude (deg),altitude (HAE-m),fixType
2026-01-26T10:00:00.000Z,123456789,37.7749,-122.4194,10.5,3
```

---

## Next Phase

Phase 7: UI Plane (Manifests, UiUpdate, UiCheckpoint)

- Manifest-driven UI updates
- UiCheckpoint for fast seek
- Time-versioned UI state

---

## Appendix: File Structure After Phase 6

```
nova/
  main.py                     # ✅ Wires FileWriter + emitDriverBinding
  config.json                 # ✅ Has dataDir, exportDir
  core/
    database.py
    ingest.py                 # ✅ Triggers FileWriter
    fileWriter.py             # ✅ NEW: Real-time writing
    export.py                 # ✅ NEW: Export execution
    drivers/
      __init__.py             # ✅ NEW
      base.py                 # ✅ NEW: BaseDriver + DriverCapabilities
      rawBinary.py            # ✅ NEW: Raw → raw.bin
      positionCsv.py          # ✅ NEW: Position → llas.csv
      registry.py             # ✅ NEW: Driver selection
  server/
    server.py                 # ✅ Has export handlers
    ipc.py                    # ✅ Has export IPC
  ui/
    js/
      export.js               # ✅ NEW: Export UI
```

Note: Implementation puts export handlers in `server.py` rather than a separate `exports.py` file, following the minimal-file principle from guidelines.md.
