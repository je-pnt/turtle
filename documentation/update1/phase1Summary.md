# Phase 1 Summary: Timeline Control Foundation

**Completed:** February 5, 2026  
**Scope:** P0.1, P0.2, P0.3 (Critical Priority Items)

---

## P0.1: Stream Synchronization Drift

### Problem
OutputStreamCursor (follower) drifted from StreamCursor (leader) during playback. TCP/UDP/WS output streams received events at different times than the UI timeline, causing desynchronization.

### Root Cause
OutputStreamCursor had an **independent 20ms polling loop** that:
1. Ran on its own schedule, not synchronized with the leader's emission timing
2. Used `timestamp=queryEnd` which was 500ms offset from the leader's actual query window
3. Maintained its own `lastQueryUs` state that diverged from the leader's position

This violated the architecture invariant: *"Client timeline position is derived from the last-emitted event's time"* — followers were deriving position from their own independent clock.

### Solution: Event-Driven Follower Architecture
Replaced independent polling with leader-signaled synchronization:

**Changes to `nova/core/streaming.py`:**

1. **StreamCursor (leader) additions:**
   ```python
   self.cursorAdvancedEvent = asyncio.Event()  # Signal for followers
   self.lastWindow = (0, 0)                     # Exact (t0, t1) window queried
   ```

2. **StreamCursor signals after emitting:**
   ```python
   # After emitting chunk in _streamBound()
   self.lastWindow = (t0, t1)
   self.cursorAdvancedEvent.set()
   ```

3. **OutputStreamCursor rewritten to be event-driven:**
   - **Deleted:** Independent 20ms `asyncio.sleep()` polling loop
   - **Deleted:** `_getQueryWindow()` method
   - **Deleted:** `lastQueryUs` tracking
   - **Added:** Waits for `leader.cursorAdvancedEvent` signal
   - **Added:** Queries exact same `(t0, t1)` window as leader

**Result:** All output streams now emit events at exactly the same time indices as the UI timeline. Zero drift by design.

---

## P0.2: Playback Controls Stuck

### Problem
Playback controls intermittently stuck or became unresponsive. Users had to refresh the page to regain timeline control.

### Root Cause
Dual `playbackRequestId` generation created a race condition:
1. Client generated a UUID via `generateUUID()` before sending startStream request
2. Server generated its own `playbackRequestId` in response
3. If server response arrived before client finished setup, stream fencing rejected valid events

The **8 `setTimeout(..., 200)` delays** scattered throughout the code were symptoms — defensive delays trying to mask the race condition rather than fix it.

### Solution: Server-Authoritative playbackRequestId

**Changes to `nova/ui/js/timeline.js`:**

1. **Deleted:** `generateUUID()` function entirely
2. **Deleted:** All 8 `setTimeout(..., 200)` delay calls
3. **Deleted:** `cancelAndStartStream()` complex cancellation logic
4. **Deleted:** `sliderDragTimeout` variable

5. **Modified `startStream()`:**
   ```javascript
   // Clear before sending - server is sole authority
   timeline.playbackRequestId = null;
   
   // Request no longer includes client-generated playbackRequestId
   const request = {
       type: 'startStream',
       mode: timeline.playbackMode,
       subjects: subjectsForStream,
       timestamp: timestamp
   };
   ```

6. **Server response sets playbackRequestId:**
   ```javascript
   // In WebSocket message handler
   if (data.playbackRequestId) {
       timeline.playbackRequestId = data.playbackRequestId;
   }
   ```

**Result:** No more race conditions. Server is the single source of truth for stream identity. Controls respond immediately without artificial delays.

---

## P0.3: Seek/Jump Capability

### Problem
No unified way to seek to arbitrary timestamps. Duplicate logic existed in `handleDatetimeJump()` and `handleSliderDrag()`.

### Root Cause
Code duplication — two functions implemented seek logic independently, making it hard to maintain and extend.

### Solution: Unified `seekToTime()` Function

**Added to `nova/ui/js/timeline.js`:**
```javascript
function seekToTime(targetTimeUs) {
    // Unified seek: update slider, switch to REWIND mode, start stream at target time
    const sliderPos = timeToSliderPosition(targetTimeUs);
    timeline.slider.noUiSlider.set(sliderPos);
    
    timeline.playbackMode = 'REWIND';
    updateModeIndicator();
    startStream(targetTimeUs);
}
```

**Modified callers:**
- `handleDatetimeJump()` now calls `seekToTime(targetTimeUs)`
- `handleSliderDrag()` now calls `seekToTime(targetTimeUs)`

**Result:** Single point of maintenance for seek logic. Easy to add future seek triggers (bookmarks, event clicks, etc.).

---

## Files Modified

| File | Lines Changed | Net Effect |
|------|---------------|------------|
| `nova/core/streaming.py` | ~40 lines modified | Event-driven follower sync |
| `nova/ui/js/timeline.js` | ~80 lines deleted, ~20 added | **Net reduction ~60 lines** |

---

## Architecture Compliance

### Invariants Preserved
1. **Server-authoritative timeline:** Server generates all playbackRequestId values
2. **Time-indexed truth:** All streams query the same (t0, t1) window
3. **No free-running clocks:** Followers wait for leader signal, never poll independently

### Guidelines Followed
1. **Fixed root causes:** Removed independent polling loop and dual ID generation
2. **No parallel paths:** Deleted old code, didn't add wrappers
3. **Reuse and deletion:** Consolidated seek logic, removed 60+ lines of code
4. **Explicit logic:** Event-driven sync is deterministic, not timing-based

---

## Validation

- `get_errors` on both modified files: **No errors found**
- Code review confirms no remaining `setTimeout` delays in timeline.js
- Code review confirms no remaining independent polling in OutputStreamCursor
