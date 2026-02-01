# Lane Architecture

**Multi-Rate Views Over Single Truth Database**

---

## Overview

NOVA implements a **lane architecture**: multiple filtered views over a single authoritative database. Each lane serves different use cases with different rate/fidelity requirements.

**Key Principle**: One truth database, multiple read-optimized views.

---

## Three-Lane Model

```
┌─────────────────────────────────────────────────────────────┐
│                         Producer (GEM)                       │
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐             │
│  │ Raw Lane │    │Truth Lane│    │ UI Lane  │             │
│  │ (binary) │    │ (10 Hz)  │    │ (1-2 Hz) │             │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘             │
└───────┼───────────────┼───────────────┼────────────────────┘
        │               │               │
        │     NATS      │               │
        │               │               │
        ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────┐
│                       novaArchive                            │
│                                                              │
│     Ingest All Lanes → Single SQLite Database (Truth)       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  messages table (all lanes stored, lane tag preserved) │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│              Republish (Two-Lane Output)                     │
│                                                              │
│  ┌──────────────────┐          ┌──────────────────┐        │
│  │  Firehose Lane   │          │    UI Lane       │        │
│  │  (truth rate)    │          │   (1-2 Hz)       │        │
│  └────────┬─────────┘          └────────┬─────────┘        │
└───────────┼──────────────────────────────┼──────────────────┘
            │                              │
            │           NATS               │
            │                              │
            ▼                              ▼
    ┌──────────────┐              ┌──────────────┐
    │ PlotJuggler  │              │   novaCore   │
    │ (analysis)   │              │   (UI proxy) │
    └──────────────┘              └──────┬───────┘
                                         │
                                         ▼
                                  ┌──────────────┐
                                  │   Browser    │
                                  │  (timeline)  │
                                  └──────────────┘
```

---

## Lane Definitions

### Raw Lane

**Purpose**: Binary passthrough for TCP replay (forensic analysis).

**Rate**: Native device rate (1-100 Hz, depends on device).

**Format**: Binary (raw protocol bytes).

**Subject**: `stream.raw.{scopeId}.{entityId}`

**Producer**: GEM (passthrough from hardwareService)

**Consumer**: novaArchive (ingests for TCP replay)

**Use Cases**:
- Exact protocol replay for debugging
- Bit-level forensic analysis
- Parser validation

**Example**:
```
Subject: stream.raw.payload-1.8220-F9P
Payload: <binary UBX-NAV-PVT message>
```

### Truth Lane

**Purpose**: High-fidelity typed messages (native rate).

**Rate**: Native device rate (10 Hz typical for GNSS).

**Format**: JSON (deterministic, alphabetically ordered).

**Subject**: `stream.truth.{streamType}.{scopeId}.{entityId}`

**Producer**: GEM

**Consumer**: novaArchive (ingests to messages table)

**Use Cases**:
- High-rate analysis (PlotJuggler, MATLAB)
- Ground truth for scientific analysis
- Full-fidelity replay

**Example**:
```json
{
  "assetId": "8220-F9P",
  "patch": {
    "lat": 40.647002,
    "lon": -111.818352,
    "alt": 1354.2,
    "heading": 90.0
  },
  "scopeId": "payload-1",
  "sequenceNum": 42,
  "streamType": "position",
  "timestampMs": 1706188496789,
  "version": 1
}
```

### UI Lane

**Purpose**: Rate-limited messages for operator displays.

**Rate**: 1-2 Hz (configurable).

**Format**: JSON (same as truth lane).

**Subject**: `stream.ui.{streamType}.{scopeId}.{entityId}`

**Producer**: GEM (rate-limited from truth lane)

**Consumer**: novaArchive (ingests to messages table)

**Use Cases**:
- Live operator displays
- Low-bandwidth telemetry
- UI responsiveness

**Example**: Same format as truth lane, but rate-limited.

---

## Database Storage

### Single Table (All Lanes)

**Schema**:
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assetId TEXT NOT NULL,
    scopeId TEXT NOT NULL,
    streamType TEXT NOT NULL,
    lane TEXT NOT NULL,              -- 'raw', 'truth', 'ui'
    data TEXT,                        -- JSON (truth/ui) or hex (raw)
    timestampMs INTEGER NOT NULL,     -- Receive-time authority
    deviceTimestampMs INTEGER,        -- Device timestamp (auxiliary)
    sequenceNum INTEGER,
    version INTEGER,
    hash TEXT,                        -- SHA-256 of deterministic JSON
    ingestTime REAL NOT NULL
);

CREATE INDEX idx_messages_query ON messages(scopeId, timestampMs, streamType, assetId);
CREATE INDEX idx_messages_lane ON messages(lane, scopeId, timestampMs);
```

**Lane Tag**: `lane` column distinguishes raw/truth/ui.

**Query by Lane**:
```sql
-- Get truth lane messages only
SELECT * FROM messages WHERE lane = 'truth' AND timestampMs BETWEEN ? AND ?

-- Get UI lane messages only
SELECT * FROM messages WHERE lane = 'ui' AND timestampMs BETWEEN ? AND ?
```

---

## Republish (Archive → Consumers)

### Two-Lane Output

After ingestion, novaArchive republishes to two lanes:

1. **Firehose Lane**: `archive.{scopeId}.firehose.{streamType}` (truth rate)
2. **UI Lane**: `archive.{scopeId}.ui.{streamType}` (1-2 Hz)

**Why republish?** Archive is authoritative. Republishing ensures receive-time timestamps and deterministic ordering.

### Firehose Lane (Archive → Analysis Tools)

**Subject**: `archive.{scopeId}.firehose.{streamType}`

**Rate**: Matches truth lane (10 Hz).

**Format**: JSON (passthrough from truth lane).

**Consumer**: PlotJuggler, MATLAB, Python notebooks.

**Purpose**: Full-fidelity analysis without overwhelming UI.

**Example**:
```javascript
// PlotJuggler subscription
nats.subscribe('archive.payload-1.firehose.position', (msg) => {
  const data = JSON.parse(msg.data)
  plotPosition(data.patch.lat, data.patch.lon)
})
```

### UI Lane (Archive → novaCore)

**Subject**: `archive.{scopeId}.ui.{streamType}`

**Rate**: 1-2 Hz (from UI lane ingestion).

**Format**: JSON (passthrough from UI lane).

**Consumer**: novaCore → Browser (WebSocket).

**Purpose**: Live operator displays without overwhelming browser.

**Example**:
```javascript
// novaCore subscribes
nats.subscribe('archive.payload-1.ui.position', async (msg) => {
  const data = JSON.parse(msg.data)
  await websocket.send(JSON.stringify({
    type: 'delta',
    streamType: 'position',
    assetId: data.assetId,
    timestampMs: data.timestampMs,
    patch: data.patch
  }))
})
```

---

## Rate Limiting (Producer Side)

### UI Lane Rate Limiter

**Implementation** (GEM):
```python
class RateLimiter:
    def __init__(self, rate_hz):
        self.rate_hz = rate_hz
        self.interval = 1.0 / rate_hz
        self.last_publish = {}  # key -> timestamp
    
    def should_publish(self, key):
        now = time.time()
        last = self.last_publish.get(key, 0)
        
        if now - last >= self.interval:
            self.last_publish[key] = now
            return True
        return False

# Usage
ui_rate_limiter = RateLimiter(2.0)  # 2 Hz

async def publish_ui_message(device, parsed_msg):
    key = f"{device.asset_id}:{parsed_msg['messageType']}"
    
    if not ui_rate_limiter.should_publish(key):
        return  # Skip this message
    
    # Publish to UI lane
    await nats.publish(
        f"stream.ui.{parsed_msg['messageType']}.{device.scope_id}.{device.asset_id}",
        json.dumps(envelope, sort_keys=True).encode()
    )
```

**Key**: Rate limiting is per (assetId, streamType) pair.

**Example**: 8220-F9P publishes position at 10 Hz (truth lane), but only 2 Hz (UI lane).

---

## Use Case: Playback

### Problem

Operator wants to review mission at 10x speed. Rendering 100 Hz of data (10 Hz × 10x speed) overwhelms browser.

### Solution

**Live Mode**: Browser subscribes to UI lane (1-2 Hz).  
**Playback Mode**: Browser queries UI lane via HTTP (still 1-2 Hz).

**Implementation**:
```javascript
// Live mode
ws = new WebSocket('ws://localhost:8080/ws/live')
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data)
  timeline.applyDelta(msg)  // UI lane (1-2 Hz)
}

// Playback mode (10x speed)
setInterval(async () => {
  const windowMs = speed * 2000  // 20 seconds of data per fetch (at 10x)
  const deltas = await fetch(
    `/api/replay/deltas?start=${lastDeltaEnd}&end=${lastDeltaEnd + windowMs}&scope=payload-1&lane=ui`
  ).then(r => r.json())
  
  for (const msg of deltas.messages) {
    timeline.applyDelta(msg)  // Still 1-2 Hz (UI lane)
  }
  
  lastDeltaEnd = deltas.end
}, 2000 / speed)  // Fetch every 200 ms (at 10x speed)
```

**Result**: Browser renders 1-2 Hz regardless of playback speed.

---

## Use Case: Analysis Tools

### Problem

Data scientist wants to analyze GNSS signals at full fidelity (10 Hz) without affecting UI performance.

### Solution

**Analysis Tool**: Subscribe to firehose lane (`archive.payload-1.firehose.gnss-signals`).  
**Browser UI**: Subscribe to UI lane (`archive.payload-1.ui.gnss-signals`).

**Implementation** (PlotJuggler):
```python
import nats
import json

async def plot_signals():
    nc = await nats.connect("nats://localhost:4222")
    
    # Subscribe to firehose lane (10 Hz)
    async def message_handler(msg):
        data = json.loads(msg.data.decode())
        plot_cn0(data['patch']['avgCn0'])  # Real-time plot
    
    await nc.subscribe("archive.payload-1.firehose.gnss-signals", cb=message_handler)
```

**Result**: Analysis tool gets full 10 Hz data, browser UI stays responsive at 1-2 Hz.

---

## Use Case: Forensic TCP Replay

### Problem

GNSS receiver exhibited anomalous behavior. Engineer needs exact protocol bytes to reproduce in lab.

### Solution

**Export Raw Lane**: Query raw lane from archive, export to TCP stream.

**Implementation**:
```python
import sqlite3
import socket

def export_tcp_replay(asset_id, start_time, end_time, output_port):
    conn = sqlite3.connect('archive.db')
    cursor = conn.cursor()
    
    # Query raw lane
    cursor.execute('''
        SELECT data, timestampMs FROM messages
        WHERE assetId = ? AND lane = 'raw'
          AND timestampMs BETWEEN ? AND ?
        ORDER BY timestampMs ASC
    ''', (asset_id, start_time, end_time))
    
    # Open TCP server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('localhost', output_port))
    server.listen(1)
    
    print(f"Waiting for connection on port {output_port}...")
    client, addr = server.accept()
    print(f"Connected: {addr}")
    
    # Replay bytes
    for row in cursor:
        data_hex, timestamp = row
        data_bytes = bytes.fromhex(data_hex)
        client.send(data_bytes)
        time.sleep(0.1)  # 10 Hz replay
    
    client.close()
    server.close()

# Usage
export_tcp_replay('8220-F9P', 1706180000000, 1706188500000, 9000)
```

**Result**: Exact protocol replay for debugging parsers or device firmware.

---

## Benefits

### Separation of Concerns

**Operators**: See UI lane (1-2 Hz) - responsive, low latency.  
**Analysts**: See firehose lane (10 Hz) - full fidelity.  
**Engineers**: See raw lane (binary) - exact protocol.

**No Conflicts**: Each consumer gets the data they need without affecting others.

### Scalability

**Without Lanes**: All consumers receive 10 Hz (100 clients × 10 Hz = 1000 msg/s).  
**With Lanes**: UI clients receive 1-2 Hz (100 clients × 2 Hz = 200 msg/s).

**80% reduction** in NATS message volume for UI clients.

### Replay Efficiency

**Without Lanes**: Playback at 10x speed requires filtering 100 Hz in client (CPU overhead).  
**With Lanes**: Playback queries UI lane (already filtered to 1-2 Hz).

**Query Time**: O(N) where N = UI lane message count, not O(10N) where 10N = truth lane message count.

---

## Implementation Details

### Lane Selection (Producer)

**GEM publishes to all three lanes**:
```python
async def handle_parsed_message(device, parsed_msg):
    # Raw lane (binary passthrough)
    await nats.publish(
        f"stream.raw.{device.scope_id}.{device.asset_id}",
        raw_bytes  # Binary
    )
    
    # Truth lane (always)
    await nats.publish(
        f"stream.truth.{parsed_msg['messageType']}.{device.scope_id}.{device.asset_id}",
        json.dumps(envelope, sort_keys=True).encode()
    )
    
    # UI lane (rate-limited)
    if ui_rate_limiter.should_publish(f"{device.asset_id}:{parsed_msg['messageType']}"):
        await nats.publish(
            f"stream.ui.{parsed_msg['messageType']}.{device.scope_id}.{device.asset_id}",
            json.dumps(envelope, sort_keys=True).encode()
        )
```

### Lane Selection (Consumer)

**PlotJuggler** (analysis tool):
```python
# Subscribe to firehose lane (truth rate)
await nats.subscribe('archive.payload-1.firehose.position', handler)
```

**Browser** (UI):
```javascript
// Subscribe to UI lane (1-2 Hz)
ws = new WebSocket('ws://localhost:8080/ws/live')
// novaCore automatically subscribes to 'archive.payload-1.ui.*'
```

### Lane Query (Replay)

**HTTP API**:
```
GET /api/replay/deltas?start=T1&end=T2&scope=X&lane=ui
```

**Implementation** (replayApi.py):
```python
@app.get('/api/replay/deltas')
async def get_deltas(start: int, end: int, scope: str, lane: str = 'ui'):
    cursor.execute('''
        SELECT assetId, streamType, data, timestampMs
        FROM messages
        WHERE scopeId = ? AND lane = ?
          AND timestampMs > ? AND timestampMs <= ?
        ORDER BY timestampMs ASC
    ''', (scope, lane, start, end))
    
    messages = []
    for row in cursor:
        asset_id, stream_type, data_json, timestamp = row
        messages.append({
            'assetId': asset_id,
            'streamType': stream_type,
            'timestampMs': timestamp,
            'patch': json.loads(data_json)['patch']
        })
    
    return {'start': start, 'end': end, 'scope': scope, 'messages': messages}
```

---

## Future Work

### Adaptive Lane Switching

**Idea**: Client dynamically switches lanes based on playback speed.

**Example**:
- Playback at 1x speed → UI lane (1-2 Hz)
- Playback at 0.1x speed → Truth lane (10 Hz)
- Playback at 10x speed → UI lane (1-2 Hz)

**Implementation**:
```javascript
function selectLane(speed) {
  if (speed <= 0.5) {
    return 'truth'  // Slow playback, show all data
  } else {
    return 'ui'  // Fast playback, show filtered data
  }
}
```

### Custom Lane Definitions

**Idea**: User-defined lanes with custom rate limits.

**Example**:
```json
{
  "lanes": [
    {"name": "telemetry", "rateHz": 0.1},  // 1 message per 10 seconds
    {"name": "ui", "rateHz": 2.0},
    {"name": "truth", "rateHz": null}      // No limit
  ]
}
```

---

## Summary

Lane architecture provides multi-rate views over a single truth database. Producers publish to three lanes (raw, truth, UI). Archive ingests all lanes, republishes to two lanes (firehose, UI). Consumers choose the lane that fits their use case.

**Key Takeaways**:
- ✅ One truth database, multiple views
- ✅ Three producer lanes: raw (binary), truth (10 Hz), UI (1-2 Hz)
- ✅ Two consumer lanes: firehose (10 Hz), UI (1-2 Hz)
- ✅ Rate limiting at producer (GEM) and archive (republish)
- ✅ Query by lane in replay API
- ✅ Separation of concerns: operators, analysts, engineers

**Related Features**:
- [Stateless Replay](statelessReplay.md) - Client-driven playback
- [Single Database Truth](singleDatabaseTruth.md) - Archive authority
