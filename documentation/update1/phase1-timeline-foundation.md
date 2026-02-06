# Phase 1: Timeline Control Foundation (P0 Issues)

> **Objective**: Fix the core timeline control system so streams stay synchronized, playback controls never get stuck, and seek/jump works reliably.

> **Implementation flexibility**: File references, function names, and refactor sketches are guidance. Any approach that meets Required Behavior + invariants and reduces code is acceptable.

---

## Architecture Invariants (From nova architecture.md)

Before any work, confirm these contracts are preserved:

1. **Server-authoritative playback cursor**: "Client timeline position is derived from the last-emitted event's time... Client must not free-run a clock; client may interpolate cosmetically between chunks but must correct to server truth on each chunk."

2. **Stream fencing**: "Any change (mode/timebase/rate/scope) must cancel + restart with new playbackRequestId... NOVA must never interleave outputs from two playback requests for the same client connection."

3. **Ephemeral state**: "NOVA may maintain ephemeral per-connection playback state (cursor/rate) required to stream data, which is discarded on disconnect or stream restart."

4. **Deterministic ordering**: Same ordering for all consumers (UI, TCP loopback, exports).

---

## P0.1 Stream Synchronization Drift

### Investigation Required

**Read and understand before implementing:**
- `nova/core/streaming.py` — How does `StreamCursor` maintain cursor position? How do bound TCP streams (OutputStreamCursor) relate to the main stream?
- `nova/server/streams/` — How are TCP output streams bound to a client session? What is the "leader" concept?
- `timeErrorLog.txt` — Identify specific playbackRequestIds where drift was observed.

### Root Cause Hypothesis

Multiple independent cursors advance at their own pace. The "bound" relationship exists but may not enforce synchronized cursor time.

### Questions to Answer (During Implementation)

1. Is there ONE authoritative cursor per session, or does each stream have its own?
2. When the main stream cursor advances, do bound streams receive that cursor time or calculate their own?
3. What happens to bound stream timing when the main stream rate changes?

### Required Behavior (Architecture Contract)

All streams bound to the same session MUST share the same authoritative cursor time. The cursor time is server-derived from the last-emitted event in the chosen timebase.

**Server-side unified pacing** (root cause prevention):
- Server maintains ONE authoritative session cursor and ONE pacing scheduler per playbackRequestId
- All bound outputs (UI stream + TCP output streams) emit according to that shared schedule
- No per-output independent timers, sleeps, or buffering that could cause drift
- Bound outputs do not compute their own "when to emit" — they receive emit signals from the unified pacer

This prevents drift at the source rather than correcting it after the fact.

**Falsifiable Invariant** (verification): For a given `playbackRequestId`, all bound streams MUST emit chunks whose cursor timestamp matches the leader stream's cursor timestamp within `syncToleranceUs`. If any bound stream's chunk timestamp deviates beyond tolerance, that is a sync failure.

**Tolerance**: Very high — assume awful network conditions. Suggested: `syncToleranceUs = 5_000_000` (5 seconds). This accommodates network jitter, buffering delays, and TCP retransmits without false-positive drift alerts.

**Recovery** (fallback for network-induced drift): When drift exceeds tolerance despite unified pacing, bound stream MUST re-sync to leader cursor (not just log). Recovery mechanism:
1. Detect drift: `|boundCursor - leaderCursor| > syncToleranceUs`
2. Pause bound stream output
3. Re-anchor bound stream to leader's current cursor
4. Resume from leader position

This invariant is testable: inject artificial delay, verify recovery occurs.

### Approach

1. **Map the current flow**: Trace how `StreamCursor.currentTime` relates to `OutputStreamCursor` cursor times.
2. **Identify the divergence point**: Where do they start computing independently?
3. **Implement drift detection**: Add cursor comparison on each bound stream emit.
4. **Implement recovery**: When drift detected, re-anchor bound stream to leader.
5. **Delete redundant cursor logic** in bound streams if they should simply follow.

---

## P0.2 Playback Controls Stuck During Unbound Replay

### Investigation Required

**Read and understand before implementing:**
- `nova/core/streaming.py` — `StreamingManager.startStream()`, `cancelStream()`, `setRate()` — what state do they manipulate?
- `nova/server/server.py` or `ClientConnection` — How does the server track active streams per connection?
- `nova/ui/js/timeline.js` — `handlePlayPause()`, `handleSpeedChange()`, `handleJumpToLive()` — what sequences of calls occur?
- `timeErrorLog.txt` — Find the specific sequence of playbackRequestIds and state transitions during failure.

### Root Cause Hypothesis

State machine has incomplete transitions. When unbound replay (rate < 0, stopTime=null) is active and user changes rate or jumps to live, some state (either client-side `timeline` object or server-side stream state) gets into an inconsistent condition.

### Questions to Answer (During Implementation)

1. What happens to the old stream when `startStream()` is called? Is it implicitly canceled?
2. Are there race conditions with the 200ms `setTimeout` delays in timeline.js?
3. What state variables can get "stuck" (e.g., `timeline.playbackRequestId` not cleared, stream not actually canceled)?

### Required Behavior (Architecture Contract)

"A new Stream Read request for the same client connection automatically terminates the prior stream." — This must be enforced server-side, not reliant on client calling cancel first.

**Fencing validation (client-side)**: Client MUST validate `playbackRequestId` on every incoming chunk. If `chunk.playbackRequestId !== timeline.playbackRequestId`, drop the chunk silently. This prevents stale data from a canceled stream corrupting the new stream's state.

### Approach

1. **Reproduce the failure**: Create a test sequence that triggers the stuck state.
2. **Trace the state**: Log state variables at each transition point.
3. **Fix the state machine**: Ensure every transition path results in a valid state.
4. **Remove defensive delays**: The 200ms `setTimeout` calls suggest uncertainty about state — fix the root cause instead.

---

## P0.3 Seek/Jump Capability

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/timeline.js` — `handleDatetimeJump()`, `handleSliderDrag()` — what do they actually do?
- `nova/core/streaming.py` — How does `startStream` with a specific `startTime` work?
- HTML datetime input — Does it support seconds? What format does it produce?

### Root Cause Hypothesis

1. Datetime input doesn't accept seconds (HTML `datetime-local` limitation).
2. `handleSliderDrag` exists but may not trigger on drag-release (only on change).
3. Bound TCP streams may not restart with new startTime when main stream seeks.

### Questions to Answer (During Implementation)

1. When user enters datetime, does `handleDatetimeJump` call `startStream` with correct startTime?
2. When user drags slider, does release trigger a seek or just visual update?
3. Do bound streams receive the new startTime or continue from their old position?

### Required Behavior

- Time entry with per-second precision → seek to that time
- Cursor drag-release → seek to that time
- ALL streams (main + bound) restart from the new time

### Approach

1. **Fix time input**: Either enhance HTML input or add manual seconds field.
2. **Implement drag-release**: Ensure slider `change` event (not just `input`) triggers seek.
3. **Unify seek function**: Create single `seekToTime(timeUs)` that both inputs call.
4. **Ensure bound streams restart**: When main stream restarts with new startTime, bound streams must also restart.

---

## Phase 1 Deliverables

1. **Stream synchronization**: All bound streams follow the leader cursor. No independent drift.
2. **Playback state machine**: No stuck states. Every transition completes or reverts cleanly.
3. **Seek/jump**: Datetime entry and cursor drag both work. All streams restart at new time.

## Phase 1 Code Reduction Targets

- Remove redundant cursor calculation logic in bound streams (if they should follow leader)
- Remove defensive `setTimeout` delays in timeline.js (fix root cause instead)
- Consolidate multiple "start stream" code paths into one

## Files Likely Modified

| File | Expected Changes |
|------|------------------|
| `nova/core/streaming.py` | Cursor synchronization, state machine fixes |
| `nova/server/streams/base.py` | Bound stream restart on seek |
| `nova/ui/js/timeline.js` | Seek function, remove delays, state machine |
| `nova/ui/html/*.html` | Datetime input with seconds |

## Validation

- [ ] Change rate rapidly during unbound replay — controls stay responsive
- [ ] Seek via datetime entry — all streams jump to new time
- [ ] Observe multiple bound streams — cursor times stay aligned
- [ ] No stuck states after any sequence of: play, pause, seek, rate change, jump to live
