# Phase 9: Authentication - Complete Specification

## Why This Document Exists

Phase 9 was botched because:
1. We started coding before fully specifying requirements
2. We modified working code (timeline, streaming) instead of adding auth on top
3. We kept "fixing" bugs introduced by previous fixes
4. We never documented what "working" looked like before we broke it

**This document specifies EVERYTHING needed to implement Phase 9 correctly.**

---

## PART 1: What Must NOT Change (Pre-Phase 9 Baseline)

### 1.1 Timeline Behavior (CRITICAL - CONTRACT-ALIGNED)

**Core Contract** (from nova architecture.md):
- Client must NOT free-run a local clock
- Timeline position is derived from last-emitted event time in the chosen timebase
- Server-paced streaming, no interleaving
- One stream per connection, implicit cancel on new startStream
- Fencing via playbackRequestId

The timeline has TWO modes:

#### LIVE Mode
- Cursor is pinned at RIGHT edge (100%)
- Window slides forward as new data arrives
- Time display shows **server truth time** from latest chunk (NOT local wall-clock)
- Rate is fixed at 1.0
- If user changes rate to anything other than 1 → immediately switch to REWIND at that rate

#### REWIND Mode  
- User drags cursor OR clicks on timeline OR changes rate from 1 → enters REWIND
- Cursor position shows playback position within window
- Window shifts automatically as needed:
  - During playback: if cursor approaches edge, window shifts to keep cursor visible
  - During drag: if user drags past left edge, window shifts to show older times
- If playing forward (rate > 0) and cursor reaches current server time → **auto-switch to LIVE mode**
- Rate can be negative (reverse), fractional, or zero (paused)
- Mode indicator shows rate (e.g., "-2x", "0.5x", "PAUSED")

#### Time Display
- Always shows **server truth time** from the stream, never a free-running local clock
- During gaps (no events for a period), display continues based on server chunk timestamps
- During drag: updates in real-time showing the preview time under the cursor

#### Drag Behavior (CRITICAL - THIS WAS BROKEN)
1. User presses mouse on cursor/track
2. User drags → cursor follows mouse, time display shows preview time
3. User releases mouse → client sends `startStream` with that time and current rate
4. Server cancels prior stream (implicit), starts new stream with new playbackRequestId
5. UI ignores any old chunks (mismatched playbackRequestId)
6. Playback continues immediately at the SAME rate - NO "jump to LIVE", NO disconnect flicker, NO rate reset

#### Play/Pause Button
- In LIVE mode: Pause → switch to REWIND at current time, rate=0
- In REWIND mode: Toggle between rate=0 (paused) and previous non-zero rate

#### LIVE Button
- Click → immediately jump to LIVE mode
- Cursor snaps to right edge
- Stream switches to real-time (rate=1)

#### Rate Control
- Input field showing current rate (e.g., "1", "-2", "0.5")
- **In LIVE mode**: Changing to any value other than 1 → switch to REWIND at that rate
- **In REWIND mode**: Adjusts playback speed immediately
- Negative = reverse playback
- Zero = paused

### 1.2 UI Layout (Pre-Phase 9)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ HEADER                                                                  │
│ ┌──────┐                                        ┌─────────────────────┐ │
│ │ NOVA │  [Status: Connected]                   │ [User Menu Button]  │ │
│ │ Logo │                                        │                     │ │
│ └──────┘                                        └─────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────┤
│ MAIN CONTENT AREA                                                       │
│ ┌───────────────────┐ ┌───────────────────────────────────────────────┐ │
│ │ SHIELDS PANEL     │ │ CARDS PANEL                                   │ │
│ │                   │ │                                               │ │
│ │ ▼ hardwareService │ │ ┌─────────────────────────────────────────┐   │ │
│ │   ▼ Payload       │ │ │ Card: mosaic-X5                         │   │ │
│ │     • mosaic-X5   │ │ │ [Collapse] [Close]                      │   │ │
│ │     • X20P        │ │ │                                         │   │ │
│ │     • F9P         │ │ │ Position: 37.7749, -122.4194            │   │ │
│ │                   │ │ │ Satellites: 12                          │   │ │
│ │ [Setup Streams]   │ │ │ Fix: 3D                                 │   │ │
│ │                   │ │ │                                         │   │ │
│ │                   │ │ │ [Actions: Reset, Configure...]          │   │ │
│ │                   │ │ └─────────────────────────────────────────┘   │ │
│ │                   │ │                                               │ │
│ │                   │ │ ┌─────────────────────────────────────────┐   │ │
│ │                   │ │ │ Card: X20P                              │   │ │
│ │                   │ │ │ ...                                     │   │ │
│ └───────────────────┘ └───────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────┤
│ TIMELINE BAR                                                            │
│ ┌───┐ ┌──────┐ ┌────────────────────────────────────────┐ ┌──────────┐ │
│ │ ▶ │ │ LIVE │ │████████████████████████████████████████●│ │ 14:32:05 │ │
│ │   │ │      │ │ [timeline track with draggable cursor] │ │ [rate:1] │ │
│ └───┘ └──────┘ └────────────────────────────────────────┘ └──────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
│ CHAT (collapsible overlay, bottom-right corner)                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Button/Icon Specifications

#### Play/Pause Button
- Playing: Show ⏸ (pause icon)
- Paused: Show ▶ (play icon)
- Style: Dark background, light icon, rounded

#### LIVE Button
- Active (in LIVE mode): Green background, white text "LIVE"
- Inactive: Gray background, white text "LIVE"
- Click: Jump to live

#### Mode Indicator (next to time display)
- LIVE mode: "LIVE" in green
- REWIND playing: Show rate like "2x" or "-1x"
- REWIND paused: "PAUSED" in yellow/orange

#### Rate Input
- Number input field
- Shows current rate
- Editable to change playback speed
- Default: 1

#### Timeline Track
- Dark gray background track
- Green progress fill from left to cursor
- White/bright cursor (vertical line or circle)
- Cursor is draggable
- Click anywhere on track to jump to that time

### 1.4 Shields Panel

- Tree hierarchy: System → Container → Asset
- Each level collapsible with ▼/▶ indicators
- Click on asset → opens card in Cards Panel
- "Setup Streams" button at bottom (styled like a shield icon)

### 1.5 Cards Panel

- Multiple cards can be open
- Cards are closeable (X button)
- Cards are collapsible (minimize)
- Cards are draggable to reorder
- Card content is manifest-driven
- **Actions in REWIND**: Buttons are visible but blacked out/disabled with error message (already implemented)

### 1.6 Streaming Architecture

```
Client                          Server
  │                               │
  │──── startStream ─────────────▶│  (mode: live/replay, startTime, rate)
  │                               │
  │◀──── streamStarted ──────────│  (playbackRequestId)
  │                               │
  │◀──── streamChunk ────────────│  (events[], timestamp)
  │◀──── streamChunk ────────────│
  │◀──── streamChunk ────────────│
  │         ...                   │
  │                               │
  │──── startStream ─────────────▶│  (NEW stream = implicit cancel of previous)
  │                               │
  │◀──── streamStarted ──────────│  (new playbackRequestId)
  │◀──── streamChunk ────────────│
  │         ...                   │
```

**Key principle**: Server handles ONE stream per connection. New `startStream` implicitly cancels previous. Client does NOT need to send explicit `cancelStream` for seeks/jumps.

### 1.7 Fencing (Stale Chunk Protection)

Each stream has a `playbackRequestId`. Client ignores chunks with mismatched IDs.

```javascript
if (msg.playbackRequestId !== timeline.playbackRequestId) {
    return; // Ignore stale chunk
}
```

---

## PART 2: What Phase 9 Adds (Authentication)

### 2.1 Auth Scope (MINIMAL)

Phase 9 ONLY adds:
1. Login page gate
2. Cookie-based session
3. User display in header
4. WebSocket auth check

Phase 9 does NOT:
- Change timeline behavior
- Change streaming logic
- Change UI layout
- Add role-based permissions (that's Phase 10+)

### 2.2 Login Flow

```
User visits /           User visits /login
     │                        │
     ▼                        ▼
Has novaAuth cookie? ──No──▶ Show login form
     │                        │
    Yes                   Submit credentials
     │                        │
     ▼                        ▼
Cookie valid? ──No─────────▶ POST /api/auth/login
     │                        │
    Yes                   Validate credentials
     │                        │
     ▼                        ▼
Show main UI              Set novaAuth cookie
                              │
                              ▼
                          Redirect to /
```

### 2.3 Cookie Specification

- **Name**: `novaAuth` (camelCase per guidelines)
- **Value**: JWT token containing `{userId, username, role, exp}`
- **Attributes**: 
  - `HttpOnly` - not accessible via JavaScript
  - `SameSite=Lax` - allows WebSocket upgrade requests
  - `Path=/` - available for all routes
  - `Secure` - only over HTTPS (in production)
- **Expiry**: 24 hours (configurable)

### 2.4 WebSocket Authentication

```
Client                          Server
  │                               │
  │──── WebSocket upgrade ───────▶│  (Cookie header contains novaAuth)
  │                               │
  │                               │  Server validates cookie
  │                               │
  │◀──── authResponse ───────────│  {success: true, connId, username}
  │                               │     OR
  │◀──── authResponse ───────────│  {success: false, error: "..."}
  │                               │
  │     (if success)              │
  │──── query/startStream ───────▶│
  │         ...                   │
```

### 2.5 User Display (Header)

```
┌─────────────────────────────────────────────────┐
│                              ┌────────────────┐ │
│                              │ 👤 admin    ▼ │ │
│                              └────────────────┘ │
│                                    │            │
│                              ┌─────▼──────────┐ │
│                              │ Profile        │ │
│                              │ Settings       │ │
│                              │ ────────────── │ │
│                              │ Logout         │ │
│                              └────────────────┘ │
```

- Dropdown menu on click
- Logout clears cookie, redirects to /login

### 2.6 API Endpoints

#### POST /api/auth/login
Request:
```json
{"username": "admin", "password": "secret"}
```
Response (success):
```json
{"success": true, "username": "admin", "role": "admin"}
```
Response (failure):
```json
{"success": false, "error": "Invalid credentials"}
```
Side effect: Sets `novaAuth` cookie

#### POST /api/auth/logout
Clears `novaAuth` cookie
Response:
```json
{"success": true}
```

#### GET /api/auth/me
Returns current user info (from cookie)
```json
{"username": "admin", "role": "admin"}
```

---

## PART 3: Implementation Approach

### 3.1 Files to CREATE (New)

| File | Purpose |
|------|---------|
| `nova/ui/html/login.html` | Login page |
| `nova/ui/js/login.js` | Login form handler |
| `nova/ui/css/login.css` | Login page styles |
| `nova/server/auth.py` | Token generation/validation |

### 3.2 Files to MODIFY (Carefully)

| File | Change |
|------|--------|
| `nova/server/server.py` | Add login endpoint, cookie check on WS upgrade |
| `nova/ui/js/websocket.js` | Handle authResponse, set connected=true only after auth |
| `nova/ui/html/index.html` | Add user dropdown in header |
| `nova/ui/js/auth.js` | User display, logout handler |
| `nova/ui/css/styles.css` | User dropdown styles |

### 3.3 Files to NOT MODIFY

| File | Reason |
|------|--------|
| `timeline.js` | Timeline behavior must be preserved exactly |
| `display.js` | Event routing must be preserved |
| `entities.js` | Shield discovery must be preserved |
| `cards.js` | Card rendering must be preserved |
| `streams.js` | Stream definitions must be preserved |

### 3.4 Implementation Order

1. **Server auth** - auth.py with token functions
2. **Login endpoint** - server.py POST /api/auth/login
3. **Login page** - login.html, login.js, login.css
4. **WebSocket auth** - server.py check cookie on upgrade
5. **Client auth** - websocket.js handle authResponse
6. **User display** - index.html header, auth.js dropdown
7. **Test everything** - login, logout, WS auth, existing features

---

## PART 4: Acceptance Criteria

### Must Pass
- [ ] Visiting / without auth → redirects to /login
- [ ] Login with valid credentials → redirects to /
- [ ] Login with invalid credentials → shows error
- [ ] WebSocket connects and authenticates
- [ ] Username shows in header
- [ ] Logout clears session, redirects to /login
- [ ] **Timeline LIVE mode works exactly as before**
- [ ] **Timeline REWIND mode works exactly as before**
- [ ] **Drag-to-seek works smoothly without disconnects**
- [ ] **Rate control works (positive, negative, zero)**
- [ ] **Shields panel works exactly as before**
- [ ] **Cards panel works exactly as before**
- [ ] **All buttons/icons display correctly**

### Must NOT Happen
- [ ] No stream restart loops
- [ ] No timeline mode confusion
- [ ] No broken icons/buttons
- [ ] No layout shifts
- [ ] No disconnects on seek

---

## PART 5: Recovery Steps

Before implementing this spec:

1. **Git reset** timeline.js, display.js to pre-Phase 9 state
2. **Verify** baseline UI works (LIVE, REWIND, drag, rate)
3. **Then** implement auth per this spec
4. **Test** after each step

---

## Appendix A: Message Type Reference

### Client → Server
| Type | Fields | Purpose |
|------|--------|---------|
| `startStream` | clientConnId, playbackRequestId, startTime, stopTime, rate, timelineMode, timebase, filters | Start/seek stream |
| `setPlaybackRate` | rate | Change rate mid-stream |
| `cancelStream` | clientConnId | Explicit cancel (rarely needed) |
| `query` | startTime, stopTime, timebase, filters | One-time data fetch |

### Server → Client  
| Type | Fields | Purpose |
|------|--------|---------|
| `authResponse` | success, connId, username, error | Auth result |
| `streamStarted` | playbackRequestId | Confirm stream started |
| `streamChunk` | playbackRequestId, events[], timestamp | Data chunk |
| `streamComplete` | playbackRequestId | Stream ended |
| `queryResponse` | events[], totalCount | Query result |

---

## Appendix B: Timeline State Machine

```
                    ┌─────────────────┐
                    │                 │
        ┌──────────▶│   LIVE MODE     │◀──────────┐
        │           │ (rate=1, cursor │           │
        │           │  at right edge) │           │
        │           └────────┬────────┘           │
        │                    │                    │
   [LIVE btn]           [Pause] or           [Cursor reaches
        │              [Drag/Click] or        current time in
        │              [Rate changed          REWIND with rate>0]
        │               to != 1]                  │
        │                    │                    │
        │                    ▼                    │
        │           ┌─────────────────┐           │
        │           │                 │           │
        └───────────│  REWIND MODE    │───────────┘
                    │ (rate variable, │
                    │ cursor movable) │
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
                    ▼                 ▼
            ┌───────────┐     ┌───────────┐
            │  PLAYING  │◀───▶│  PAUSED   │
            │ (rate≠0)  │     │ (rate=0)  │
            └───────────┘     └───────────┘
                    [Play/Pause toggle]
```

**Mode Transitions:**
- LIVE → REWIND: Pause, drag, click track, or change rate to != 1
- REWIND → LIVE: Click LIVE button, or cursor reaches current server time while playing forward (rate > 0)
