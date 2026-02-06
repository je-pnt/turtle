# Phase 2: Lifecycle, State, and Persistence (P1 Issues)

> **Objective**: Fix online/offline detection, entity/stream lifecycle, timeline visual advancement, and presentation persistence. Establish unified client-side activity tracking.

> **Prerequisite**: Phase 1 complete (timeline controls work reliably).

> **Implementation flexibility**: File references, function names, and refactor sketches are guidance. Any approach that meets Required Behavior + invariants and reduces code is acceptable.

---

## Architecture Invariants (From nova architecture.md)

1. **Client interpolation**: "Client may interpolate cosmetically between chunks but must correct to server truth on each chunk." — Timeline visual advancement is allowed as interpolation, not fabricated truth.

2. **Per-user overrides**: "Overrides are presentation-truth events (Metadata lane); they affect rendering/export labeling only (never telemetry truth)." — Name/color/model overrides must persist as metadata.

3. **Replay semantics**: "Replay is served from truth with the same ordering and UI surfaces." — Online/offline in replay must reflect historical state, not current wall-clock.

---

## P1.1 + P1.2 + P1.3 + P1.5: Unified Activity Tracking (Entities + Streams)

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/entities.js` — How does `updateOnlineStatuses()` work? What is `onlineTtlMs`?
- `nova/ui/js/streams.js` — How are stream shields rendered? Is there online/offline tracking?
- `nova/ui/js/websocket.js` — How are incoming events routed to update `lastSeen`?
- `nova/config.json` — Where are timeout thresholds configured?

### Root Cause Analysis

Current implementation:
1. Online/offline compares `lastSeen` to `Date.now()` — wrong in replay (should compare to cursor time)
2. Entities have `lastSeen` tracking; streams may not
3. No cleanup mechanism removes stale entities/streams
4. Thresholds may be hardcoded in multiple places

### Required Behavior

**Single activity algorithm** with two thresholds:
- `onlineWindowSeconds` (e.g., 3s): online vs offline indication
- `cleanupWindowSeconds` (e.g., 120s): removal from UI

**"Seen" definition**: Any incoming data event for that `uniqueId` (entities) or stream.

**lastSeen source**: Always `chunk.timestamp` from server data (microseconds) — never wall-clock at receive time. This ensures replay uses the same timestamps as live.

**Comparison baseline**: Always `timeline.currentTimeUs` (microseconds) — in BOTH modes.

> **Why not Date.now() in LIVE?** `lastSeen` is in server-time-microseconds. `Date.now()` is browser-milliseconds in a different epoch. Comparing them directly is unit/timebase inconsistent and will produce wrong online/offline results. Instead, `timeline.currentTimeUs` advances via interpolation in LIVE mode (anchored to server time), so it's always in the same domain as `lastSeen`. Wall-clock is only used to compute interpolation *deltas*, never as a direct comparison value.

**Applies to**: Entities AND streams (same algorithm).

### Approach

1. **Single baseline**: `getActivityBaseline()` always returns `timeline.currentTimeUs` (no mode branching).
2. **Unify lastSeen tracking**: Both entities and streams update `lastSeen` from incoming events.
3. **Single online/offline calculation**: `isOnline(lastSeen) = (baseline - lastSeen) < onlineWindowSeconds`
4. **Single cleanup check**: `shouldCleanup(lastSeen) = (baseline - lastSeen) > cleanupWindowSeconds`
5. **Config source**: Both thresholds from `config.ui` — no hardcoded values.
6. **Delete duplicate logic**: Remove separate online/offline implementations.

---

## P1.3: Default Entity List Windowed to Recent Activity

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/timeline.js` — `queryEntityMetadata()` — what query does it send?
- `nova/server/server.py` or `ipc.py` — How does metadata query filter results?
- Where does initial entity list population happen?

### Root Cause Analysis

On page load, the metadata query returns ALL entities ever seen, not filtered by recent activity.

### Required Behavior

On page load, only load entities with `lastSeen >= (timeline.currentTimeUs - cleanupWindowUs)`.

> **Explicit**: "now" is always `timeline.currentTimeUs`, not wall-clock. This ensures unbound replay correctly shows entities that were active at the cursor position, not entities active at browser wall-clock time.

### Approach

**Client-side filtering only**: Filter query results before populating shields.

> Why not server-side? Server doesn't know the client's selected timebase or cursor position. Filtering server-side risks hiding entities that are valid for the client's current view. Client has full context to filter correctly.

---

## P1.4: Timeline Visual Advancement Without Data

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/timeline.js` — `updateDisplay()` — how does it calculate displayed time?
- `suggestedClockTick.txt` — Recommended implementation approach.

### Root Cause Analysis

Current: `timeline.currentTimeUs` only updates when data arrives. When no data, display freezes.

Architecture allows: "Client may interpolate cosmetically between chunks."

### Required Behavior

**Connection/session truth clamp** (prerequisite for all interpolation):
- Clock advances ONLY while: websocket connected AND active playback session running
- If websocket disconnects: freeze display, show "disconnected" state
- If stream is canceled or no active session: freeze display
- This is NOT the same as "stall detection" — this is connection/session state, not data flow state

**LIVE mode**: Display advances with wall-clock while connected + session active. Formula: `displayTime = anchorTime + (wallClock - anchorWall)`.

**REWIND mode**: Display interpolates from last anchor at current rate. Formula: `displayTime = anchorTime + (wallClock - anchorWall) × rate`.

**On data arrival**: Reset anchor (`anchorTimeUs = chunk.timestamp`, `anchorWallMs = Date.now()`).

**When paused** (rate = 0): Display freezes at current anchor.

**Stall detection** (additional constraint): If no chunk has arrived for `stallWindowSeconds` (e.g., 5s) while playback is active:
1. Clamp display to last anchor (stop interpolating)
2. Surface "stalled" state in UI (visual indicator)
3. Do NOT continue advancing — that would imply data flow when none exists

This prevents the exact P0.2 failure mode: UI looks healthy while streams are stuck.

**Never fabricate data events** — interpolation is purely cosmetic and must halt when data stops.

### Approach

1. **Add anchor state**: `timeline.anchorTimeUs`, `timeline.anchorWallMs`
2. **Update anchor on data**: `appendEvents` sets anchor to chunk timestamp
3. **Interpolate display**: `updateDisplay()` computes `anchorTime + (now - anchorWall) * rate`
4. **Handle pause**: When rate = 0, don't interpolate (display stays at anchor)

---

## P1.6: Bind/Unbind Stream Changes Require Refresh

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/cards.js` — How are bind/unbind actions handled?
- `nova/ui/js/websocket.js` — What message is sent/received for bind/unbind?
- Is there a state update after bind/unbind that should trigger re-render?

### Root Cause Analysis

Bind/unbind action completes but card state doesn't update reactively. User must refresh to see change.

### Required Behavior

Bind/unbind should update local state and re-render card immediately.

### Approach

1. **Trace the flow**: What happens after bind/unbind request is sent?
2. **Identify missing update**: Is there a response that should trigger re-render?
3. **Add reactive update**: On bind/unbind confirmation, update card state and re-render.

---

## P1.7: Chat Does Not Track Cursor in Replay

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/chat.js` — How are chat messages displayed? Is there a "current" concept?
- How does chat receive messages during replay?

### Root Cause Analysis

Chat displays messages but doesn't highlight which message corresponds to current cursor time.

### Required Behavior

In replay, highlight chat messages near cursor time.

### Approach

1. **Store message timestamps**: Each chat message has a timestamp.
2. **Calculate "current"**: Message is current if `|messageTime - cursorTime| < threshold`.
3. **Apply highlight**: Add CSS class to current message(s).

---

## P1.8: Presentation Override Persistence Failure

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/cards.js` — `editCardName()` function — how does it save?
- `nova/ui/js/presentation.js` — How do color/model/scale save?
- `nova/server/presentationStore.py` — How are overrides persisted?
- Are name saves going through the same path as other overrides?

### Root Cause Analysis

Rename uses a different save path than color/model/scale. One persists, one doesn't.

### Required Behavior

All presentation overrides (name, color, model, scale) use the same persistence pathway.

### Approach

1. **Identify the working path**: How do color/model/scale persist successfully?
2. **Identify the broken path**: How does name try to persist?
3. **Unify to working path**: Make name use the same save mechanism.
4. **Delete broken path**: Remove the non-working save code.

---

## P1.9 + P1.10: Scope and Admin User Management

### Required Behavior

- User acceptance flow includes scope assignment (default: "allScopes")
- Admin can remove users
- Admin can reset passwords

### Approach

1. **Add scope field to user acceptance**: Default to "allScopes"
2. **Add admin remove user API + UI**
3. **Add admin password reset API + UI**

(Detailed investigation deferred — these are additive features, not bug fixes)

---

## P1.H: hardwareService Integration Issues

> These are validation targets for Phase 3 manifest-driven cleanup. Document current behavior here; fix in Phase 3.

### P1.H1 Satellite Display (svInfo)

- Investigate: Is svInfo emitted as uiUpdate? Where does azEl parsing happen?
- Document the current flow before Phase 3.

### P1.H2 Card Layout Regression

- Investigate: What frontend logic transforms the layout?
- This is a symptom of non-manifest-driven rendering — Phase 3 fix.

### P1.H3 Mosaic Command Ack

- This is a hardwareService fix, not nova core.
- Document for hardwareService maintainer.

---

## Phase 2 Deliverables

1. **Unified activity tracking**: One algorithm for online/offline + cleanup, entities + streams
2. **Cursor-relative online/offline**: Works correctly in replay
3. **Visual timeline advancement**: Clock ticks without data (interpolation only)
4. **Entity windowing**: Initial load only shows recent entities
5. **Bind/unbind reactive**: Changes reflect immediately
6. **Chat cursor tracking**: Highlight current messages in replay
7. **Presentation persistence**: All overrides persist uniformly
8. **Scope + admin basics**: Scope in approval flow, admin user management

## Phase 2 Code Reduction Targets

- Delete separate online/offline implementations (entities vs streams)
- Delete hardcoded timeout values (use config)
- Delete non-working presentation save path
- Consolidate activity tracking into shared module

## Files Likely Modified

| File | Expected Changes |
|------|------------------|
| `nova/ui/js/entities.js` | Use shared activity tracking |
| `nova/ui/js/streams.js` | Use shared activity tracking, add lastSeen |
| `nova/ui/js/timeline.js` | Add anchor-based interpolation |
| `nova/ui/js/cards.js` | Unify presentation save, reactive bind/unbind |
| `nova/ui/js/presentation.js` | May consolidate with cards.js |
| `nova/ui/js/chat.js` | Add cursor tracking highlight |
| `nova/server/auth.py` | Admin user management |
| `nova/config.json` | Ensure timeout thresholds defined |

## Validation

- [ ] In replay, online/offline reflects cursor time, not wall-clock
- [ ] Streams show online/offline like entities
- [ ] Stale entities cleaned up after timeout
- [ ] Timeline clock advances smoothly without data
- [ ] Renamed card persists after reload
- [ ] Bind/unbind reflects immediately
- [ ] Chat highlights current message in replay
