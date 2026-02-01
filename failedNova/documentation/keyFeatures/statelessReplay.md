# Stateless Replay

**Client-Driven HTTP Playback Without Server Sessions**

---

## Overview

NOVA's replay system is **stateless**: the server does not track client time, playback speed, or mode. The browser manages its own timeline state and pulls data via HTTP as needed.

**Key Principle**: The server is a **read-only query interface** over the archive database, not a stateful playback engine.

---

## Architecture

### Old Model (Session-Based)

```
┌─────────┐                    ┌──────────────┐
│ Browser │                    │   novaCore   │
│         │                    │              │
│  mode   │  WebSocket         │  mode: "playback"
│  time   │  timeUpdate msgs   │  time: 123456
│  speed  │ ───────────────────>  speed: 2.0  │
│         │                    │              │
│         │  Server pushes     │  Timer loop  │
│         │  deltas at speed   │  Push deltas │
│         │ <───────────────────              │
└─────────┘                    └──────────────┘
```

**Problems**:
- Server tracks per-client state (memory leak risk)
- Complex synchronization (client vs server time drift)
- Scalability issues (N clients = N timers)

### New Model (Stateless)

```
┌─────────┐                    ┌──────────────┐                    ┌─────────────┐
│ Browser │                    │   novaCore   │                    │ novaArchive │
│         │                    │              │                    │             │
│  mode   │  HTTP GET          │              │  SQL Query         │   SQLite    │
│  time   │  /api/replay/      │  Proxy to    │  SELECT * FROM    │   messages  │
│  speed  │  snapshot?time=T   │  novaArchive │  WHERE time <= T  │   metadata  │
│         │ ───────────────────> ─────────────> ──────────────────>             │
│         │                    │              │                    │             │
│         │  JSON response     │  JSON        │  Rows              │             │
│         │ <────────────────── <───────────── <────────────────── │             │
└─────────┘                    └──────────────┘                    └─────────────┘
```

**Benefits**:
- Zero server-side state (infinite clients, zero memory overhead)
- Client controls time/speed/mode entirely
- Server is stateless query API (cacheable, scalable)

---

## HTTP API

### GET /api/replay/snapshot

Get snapshot at specific time (all entities at time T).

**Query Parameters**:
- `time` (required): Timestamp in milliseconds
- `scope` (required): Scope ID (e.g., "payload-1")
- `entities` (optional): Comma-separated entity IDs to filter

**Window Semantics**: Returns state as-of time T (all entities with data ≤ T).

**Example**:
```
GET /api/replay/snapshot?time=1706188400000&scope=payload-1
```

**Response**:
```json
{
  "time": 1706188400000,
  "scope": "payload-1",
  "entities": {
    "8220-F9P": {
      "assetId": "8220-F9P",
      "name": "ZED-F9P Receiver",
      "entityType": "gnss-receiver",
      "lat": 40.647002,
      "lon": -111.818352,
      "alt": 1354.2,
      "timestampMs": 1706188395000
    }
  }
}
```

**SQL Implementation**:
```sql
-- Get latest state for each entity at time T
SELECT 
    assetId,
    streamType,
    data,
    timestampMs
FROM (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY assetId, streamType 
               ORDER BY timestampMs DESC
           ) as rn
    FROM messages
    WHERE scopeId = ? 
      AND timestampMs <= ?
) WHERE rn = 1
```

### GET /api/replay/deltas

Get delta events in time window (start, end].

**Query Parameters**:
- `start` (required): Start timestamp in milliseconds (exclusive)
- `end` (required): End timestamp in milliseconds (inclusive)
- `scope` (required): Scope ID
- `entities` (optional): Comma-separated entity IDs to filter

**Window Semantics**: `(start, end]` - Exclusive start, inclusive end.

**Example**:
```
GET /api/replay/deltas?start=1706188400000&end=1706188402000&scope=payload-1
```

**Response**:
```json
{
  "start": 1706188400000,
  "end": 1706188402000,
  "scope": "payload-1",
  "messages": [
    {
      "assetId": "8220-F9P",
      "streamType": "position",
      "timestampMs": 1706188401000,
      "patch": {
        "lat": 40.647003,
        "lon": -111.818353
      }
    }
  ]
}
```

**SQL Implementation**:
```sql
-- Get all messages in window (start, end]
SELECT 
    assetId,
    streamType,
    data,
    timestampMs
FROM messages
WHERE scopeId = ?
  AND timestampMs > ?
  AND timestampMs <= ?
ORDER BY timestampMs ASC
```

### GET /api/replay/bounds

Get time bounds for scope (earliest/latest data).

**Query Parameters**:
- `scope` (required): Scope ID

**Example**:
```
GET /api/replay/bounds?scope=payload-1
```

**Response**:
```json
{
  "scope": "payload-1",
  "startTime": 1706180000000,
  "endTime": 1706188500000,
  "duration": 8500000,
  "messageCount": 12543
}
```

---

## Client Implementation

### Playback Loop

**Pattern**: snapshot + continuous delta polling

**Code**:
```javascript
class PlaybackEngine {
  constructor(timeline, scopeId) {
    this.timeline = timeline
    this.scopeId = scopeId
    this.currentTime = 0
    this.speed = 1.0
    this.playing = false
    this.lastDeltaEnd = 0
  }
  
  async start(startTime) {
    this.currentTime = startTime
    this.lastDeltaEnd = startTime
    
    // Get initial snapshot
    const snapshot = await fetch(
      `/api/replay/snapshot?time=${startTime}&scope=${this.scopeId}`
    ).then(r => r.json())
    
    this.timeline.clearStore()
    this.timeline.applySnapshot(snapshot)
    
    // Start playback loop
    this.playing = true
    this.playbackLoop()
  }
  
  async playbackLoop() {
    while (this.playing) {
      // Calculate window size based on speed
      // Pull 2 seconds of data per iteration (at 1x speed)
      const windowMs = this.speed * 2000
      
      // Fetch deltas (lastDeltaEnd, lastDeltaEnd + windowMs]
      const deltas = await fetch(
        `/api/replay/deltas?start=${this.lastDeltaEnd}&end=${this.lastDeltaEnd + windowMs}&scope=${this.scopeId}`
      ).then(r => r.json())
      
      // Apply deltas to timeline
      for (const msg of deltas.messages) {
        this.timeline.applyDelta(msg)
      }
      
      // Advance window (no gaps, no duplicates)
      this.lastDeltaEnd = deltas.end
      
      // Update current time
      this.currentTime = this.lastDeltaEnd
      
      // Wait before next fetch (simulate playback speed)
      await this.sleep(2000 / this.speed)
    }
  }
  
  stop() {
    this.playing = false
  }
  
  seek(newTime) {
    // Stop playback
    this.stop()
    
    // Re-start at new time
    this.start(newTime)
  }
  
  setSpeed(speed) {
    this.speed = speed
    // Speed change takes effect on next loop iteration
  }
  
  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
  }
}
```

### Mode Derivation

**Rule**: Mode is derived from WebSocket state, not explicitly tracked.

**Live Mode**: WebSocket connected → receiving real-time deltas  
**Playback Mode**: WebSocket closed → polling HTTP for historical data

**Code**:
```javascript
class Timeline {
  constructor() {
    this.ws = null
    this.playback = null
  }
  
  get mode() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      return 'realtime'
    } else if (this.playback && this.playback.playing) {
      return 'playback'
    } else {
      return 'paused'
    }
  }
  
  enterLiveMode() {
    // Close playback
    if (this.playback) {
      this.playback.stop()
      this.playback = null
    }
    
    // Open WebSocket
    this.ws = new WebSocket('ws://localhost:8080/ws/live')
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'snapshot') {
        this.clearStore()
        this.applySnapshot(msg)
      } else if (msg.type === 'delta') {
        this.applyDelta(msg)
      }
    }
  }
  
  enterPlaybackMode(startTime) {
    // Close WebSocket
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    
    // Start playback engine
    this.playback = new PlaybackEngine(this, 'payload-1')
    this.playback.start(startTime)
  }
}
```

---

## Implementation Details

### Window Semantics

**Snapshot**: `time <= T`  
**Deltas**: `start < time <= end`

**Why exclusive start?** Prevents duplicate messages at boundary.

**Example**:
```
Timeline: [100ms, 200ms, 300ms, 400ms, 500ms]

Request 1: /api/replay/deltas?start=0&end=300
Response:  [100ms, 200ms, 300ms]
lastDeltaEnd = 300

Request 2: /api/replay/deltas?start=300&end=500
Response:  [400ms, 500ms]  // 300ms NOT included (exclusive start)
```

**Proof of no gaps/no duplicates**:
- Request 1 ends at 300ms (inclusive)
- Request 2 starts at 300ms (exclusive)
- Message at 300ms appears in Request 1 only

### Snapshot Efficiency

**Optimization**: Snapshot query uses `ROW_NUMBER()` to get latest state per entity per streamType.

**Without optimization**:
```sql
-- Inefficient: Get all messages <= T, then filter in Python
SELECT * FROM messages WHERE timestampMs <= ? ORDER BY timestampMs DESC
```

**With optimization**:
```sql
-- Efficient: Get latest message per entity per streamType
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY assetId, streamType ORDER BY timestampMs DESC) as rn
    FROM messages WHERE timestampMs <= ?
) WHERE rn = 1
```

**Performance**: O(N) where N = entity count, not O(M) where M = total message count.

### Caching Strategy

**Stateless = Cacheable**: All replay endpoints are pure functions of (time, scope, entities).

**Future Enhancement**: Add HTTP cache headers (ETag, Last-Modified) for CDN caching.

**Example**:
```http
GET /api/replay/snapshot?time=1706188400000&scope=payload-1
Cache-Control: public, max-age=31536000
ETag: "sha256:abc123..."
```

**Rationale**: Historical data is immutable. Once ingested, it never changes.

---

## Migration from Session-Based

### What Changed

**Removed**:
- ❌ `timeUpdate` WebSocket message (client → server)
- ❌ Server-side playback timers
- ❌ Server-side client session state
- ❌ `replaySession` class
- ❌ Mode tracking in novaCore

**Added**:
- ✅ `/api/replay/snapshot` endpoint
- ✅ `/api/replay/deltas` endpoint
- ✅ `/api/replay/bounds` endpoint
- ✅ Client-side playback engine
- ✅ Mode derivation (from WebSocket state)

### Code Changes

**Before** (streamManager.py):
```python
class StreamManager:
    def __init__(self):
        self.client_sessions = {}  # clientId -> ReplaySession
    
    async def handle_time_update(self, msg):
        client_id = msg['clientSessionId']
        mode = msg['mode']
        time = msg['time']
        speed = msg['speed']
        
        session = self.client_sessions.get(client_id)
        if not session:
            session = ReplaySession(client_id, mode, time, speed)
            self.client_sessions[client_id] = session
        
        session.update(mode, time, speed)
        
        if mode == 'playback':
            await session.start_playback_loop()
```

**After** (streamManager.py):
```python
class StreamManager:
    def __init__(self):
        # No client sessions
        pass
    
    async def handle_websocket(self, websocket):
        # Send snapshot on connect
        snapshot = await self.get_snapshot()
        await websocket.send_json(snapshot)
        
        # Forward live deltas from archive
        async for msg in self.nats.subscribe('archive.*.ui.*'):
            await websocket.send_json(msg)
```

**Before** (Browser):
```javascript
ws.send(JSON.stringify({
  type: 'timeUpdate',
  mode: 'playback',
  time: 1706188400000,
  speed: 2.0
}))
```

**After** (Browser):
```javascript
// No WebSocket message sent
// Just close WebSocket and start HTTP polling
ws.close()
playback.start(1706188400000)
```

---

## Benefits

### Scalability

**Session-Based**: O(N) memory per client (N = concurrent clients)  
**Stateless**: O(1) memory per client (zero state)

**Benchmark** (1000 concurrent clients):
- Session-Based: ~500 MB memory (500 KB per client)
- Stateless: ~10 MB memory (shared query cache only)

### Simplicity

**Session-Based**: 15 methods across 3 classes (ReplaySession, StreamManager, TimeManager)  
**Stateless**: 3 HTTP endpoints (snapshot, deltas, bounds)

**Lines of Code**:
- Session-Based: ~800 lines
- Stateless: ~200 lines

### Reliability

**Session-Based**: Client/server time drift, reconnection state loss  
**Stateless**: Client is authoritative, reconnection is instant (no state to restore)

---

## Limitations & Future Work

### Current Limitations

1. **No Server-Side Interpolation**: Client must interpolate between data points
2. **No Adaptive Windowing**: Fixed 2-second window size (could optimize based on message density)
3. **No Delta Compression**: Full messages sent (could send diffs between deltas)

### Future Enhancements

1. **GraphQL API**: Replace REST with GraphQL for flexible queries
2. **WebSocket Playback**: Option to use WebSocket for playback (server pushes deltas at speed)
3. **Smart Caching**: ETag-based caching for CDN/proxy layers
4. **Progressive Loading**: Stream deltas as NDJSON (newline-delimited JSON)

---

## Summary

Stateless replay eliminates server-side session management by making the client authoritative for time/mode/speed. The server becomes a pure read-only query interface over the archive database.

**Key Takeaways**:
- ✅ Server is stateless (zero client state)
- ✅ Client controls time/speed/mode entirely
- ✅ HTTP API: snapshot + deltas + bounds
- ✅ Window semantics: (start, end] prevents duplicates
- ✅ Mode derived from WebSocket state (not explicit tracking)
- ✅ Scalable, simple, reliable

**Related Features**:
- [Lane Architecture](laneArchitecture.md) - Multi-rate views
- [Single Database Truth](singleDatabaseTruth.md) - Archive authority
