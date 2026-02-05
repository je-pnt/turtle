# Phase 10 Summary: Multi-Scope Presentation System

## Overview

**What Phase 10 Added:**
This phase implements the multi-scope presentation and scope authority system:
- Removes hardcoded scopes from UI
- Server resolves effective scopes per-user
- Supports multi-scope users (ground station aggregation)
- Real-time cross-session sync via WebSocket broadcast
- Per-user scope assignment by admins

**What Was Already Implemented (Pre-Phase 10):**
- UiCheckpoint + bounded seek (Phase 7)
- ManifestPublished events (Phase 8)
- Cesium geospatial rendering (Phase 7)
- Local-only assets (Phase 7)

This summary focuses on **scope authority and presentation changes only**.

---

## Architecture Decisions

### Presentation Storage: JSON Files (Not Metadata Lane)

**Decision:** Presentation overrides stored as JSON files under `data/users/` and `data/presentation/defaults/`.

**Rationale:**
- Presentation is **per-user view customization**, not truth
- NOT replay-deterministic (user A can name an entity "Alpha" while user B sees "Bravo")
- Does NOT affect telemetry semantics
- Simpler than Metadata lane events for this use case

**Trade-off:** Deviates from architecture doc's "presentation-truth events" language, but matches the actual requirement: view-only, per-user, non-deterministic customization.

---

## Architecture Invariants Preserved

1. **Server is authority for scope** - UI never decides what scopes exist; server provides them
2. **Presentation is view-only** - Never modifies truth, stored separately from telemetry
3. **No parallel code paths** - Old `{scopeId}` path routes replaced, not duplicated
4. **Explicit over inference** - Scope resolution rules are deterministic

---

## Changes Made

### 1. User Scope Storage (`nova/server/userStore.py`)

**Why:** Users need per-user scope assignments. Admins can restrict operators to specific scopes, or grant `ALL` for ground station users.

**Changes:**
- Added `allowedScopes` field to user schema
- Migration: existing admins get `['ALL']`, operators get `[]`
- Added `updateScopes(userId, scopes)` method
- `updateRole()` now grants `['ALL']` when promoting to admin

**Schema:**
```python
{
    "userId": "uuid",
    "username": "string",
    "role": "admin|operator",
    "allowedScopes": ["scope1", "scope2"] | ["ALL"],
    "status": "pending|active|disabled",
    "tokenVersion": 1,
    ...
}
```

---

### 2. Auth Token Includes Scopes (`nova/server/auth.py`)

**Why:** `validateToken()` needs to return current `allowedScopes` so server can compute effective scopes.

**Changes:**
- `validateToken()` now fetches fresh user data and includes `allowedScopes` in return
- Anonymous user (auth disabled) gets `['ALL']` and admin role

---

### 3. Scope Resolution Logic (`nova/server/server.py`)

**Why:** Central logic to compute what scopes a user can access and resolve request scope.

**New Methods:**

```python
def _getEffectiveScopes(self, user) -> set:
    """
    effectiveScopes = userAllowedScopes ∩ serverAllowedScopes
    'ALL' means unrestricted access to all server scopes.
    """

def _resolveRequestScope(self, request, user, requireForWrite=False) -> (scopeId, errorResponse):
    """
    Rules:
    - If ?scopeId= provided: validate it's in effectiveScopes
    - If no scopeId and single scope: use it
    - If no scopeId and multi scope:
      - GET: return None (caller aggregates)
      - PUT/DELETE: return 400 "scopeId required"
    """
```

---

### 4. Presentation API Route Changes (`nova/server/server.py`)

**Why:** Remove `{scopeId}` from URL path. Server resolves scope, client passes `?scopeId=` only when needed.

**Before:**
```
GET    /api/presentation/{scopeId}
PUT    /api/presentation/{scopeId}/{uniqueId}
DELETE /api/presentation/{scopeId}/{uniqueId}
GET    /api/presentation/defaults/{scopeId}
PUT    /api/presentation/defaults/{scopeId}/{uniqueId}
DELETE /api/presentation/defaults/{scopeId}/{uniqueId}
```

**After:**
```
GET    /api/presentation?scopeId=          (optional, aggregates if multi-scope)
PUT    /api/presentation/{uniqueId}?scopeId=    (required if multi-scope)
DELETE /api/presentation/{uniqueId}?scopeId=
GET    /api/presentation-default?scopeId=
PUT    /api/presentation-default/{uniqueId}?scopeId=
DELETE /api/presentation-default/{uniqueId}?scopeId=
```

**Handler Changes:**
- `handleGetPresentation()`: Aggregates across effective scopes if no scopeId specified
- `handleSetPresentation()`: Requires scopeId for multi-scope users (400 error)
- `handleDeletePresentation()`: Same as set
- Same pattern for defaults handlers
- All responses include `scopeId` in each item

---

### 5. Admin Scope Management Endpoints (`nova/server/server.py`)

**Why:** Admins need to view server scopes and assign scopes to users.

**New Routes:**
```
GET /api/admin/scopes                    → list server's allowed scopes
PUT /api/admin/users/{userId}/scopes     → set user's allowedScopes array
```

---

### 6. WebSocket Presentation Broadcast (`nova/server/server.py`)

**Why:** Real-time sync across sessions. When user A changes presentation, user B sees it immediately.

**New Method:**
```python
async def _broadcastPresentationUpdate(self, scopeId, uniqueId, data, username, isDefault=False, deleted=False):
    """Broadcast to all connected clients."""
    msg = {
        'type': 'presentationUpdate',
        'scopeId': scopeId,
        'uniqueId': uniqueId,
        'data': data,
        'username': username,
        'isDefault': isDefault,
        'deleted': deleted
    }
    for client in self.connections.values():
        await client.sendMessage(msg)
```

**Called from:**
- `handleSetPresentation()` - after successful save
- `handleDeletePresentation()` - after successful delete
- `handleSetPresentationDefaults()` - after admin default save
- `handleDeletePresentationDefaults()` - after admin default delete

---

### 7. UI: Remove Hardcoded Scope (`nova/ui/js/map.js`)

**Why:** UI should not know about scopes. Server resolves based on user.

**Before:**
```javascript
async function loadAllPresentations() {
    const defaultScopes = ['hardwareService|Payload'];  // HARDCODED - BAD
    for (const scopeId of defaultScopes) {
        await loadPresentationsForScope(scopeId);
    }
}
```

**After:**
```javascript
async function loadAllPresentations() {
    // Server resolves scope based on user's effective scopes
    const response = await fetch('/api/presentation', { credentials: 'same-origin' });
    if (response.ok) {
        const data = await response.json();
        // Group by scopeId for cache structure
        for (const [uniqueId, pres] of Object.entries(data.overrides)) {
            const scopeId = pres.scopeId || 'default';
            if (!presentationCache.has(scopeId)) {
                presentationCache.set(scopeId, {});
            }
            presentationCache.get(scopeId)[uniqueId] = pres;
        }
    }
}
```

---

### 8. UI: Handle WebSocket Presentation Updates (`nova/ui/js/map.js`, `nova/ui/js/websocket.js`)

**Why:** Receive real-time updates from server when other sessions change presentations.

**websocket.js - new case:**
```javascript
case 'presentationUpdate':
    if (window.NovaMap && window.NovaMap.handlePresentationUpdate) {
        window.NovaMap.handlePresentationUpdate(msg);
    }
    break;
```

**map.js - new handler:**
```javascript
function handlePresentationUpdate(msg) {
    const { scopeId, uniqueId, data, deleted } = msg;
    
    if (deleted) {
        // Remove from cache
        if (presentationCache.has(scopeId)) {
            delete presentationCache.get(scopeId)[uniqueId];
        }
    } else if (data) {
        // Update cache and re-render
        if (!presentationCache.has(scopeId)) {
            presentationCache.set(scopeId, {});
        }
        presentationCache.get(scopeId)[uniqueId] = { ...data, scopeId };
        
        // Re-render entity if visible
        const entityState = entities.get(uniqueId);
        if (entityState) {
            updateEntityPresentation(`${scopeId}|${uniqueId}`, data);
        }
    }
}
```

---

### 9. UI: Update API Calls (`nova/ui/js/presentation.js`, `nova/ui/js/cards.js`)

**Why:** Match new API route structure with `?scopeId=` query param.

**presentation.js changes:**
- `loadPresentation()`: `GET /api/presentation?scopeId=`
- `savePresentation()`: `PUT /api/presentation/{uniqueId}?scopeId=`
- `clearUserOverride()`: `DELETE /api/presentation/{uniqueId}?scopeId=&key=`

**cards.js changes:**
- Inline name save: `PUT /api/presentation/{uniqueId}?scopeId=`

---

## New Files Created

None. All changes were modifications to existing files per guideline C (prefer reuse and deletion over new code).

---

## Files Modified

| File | Purpose | Changes |
|------|---------|---------|
| `nova/server/userStore.py` | User persistence | Added `allowedScopes` field, migration, `updateScopes()` |
| `nova/server/auth.py` | JWT auth | `validateToken()` returns `allowedScopes` |
| `nova/server/server.py` | WebSocket server | Scope resolution, route refactor, admin endpoints, broadcast |
| `nova/ui/js/map.js` | Cesium map | Removed hardcoded scope, added `handlePresentationUpdate()` |
| `nova/ui/js/websocket.js` | WebSocket client | Added `presentationUpdate` message handler |
| `nova/ui/js/presentation.js` | Presentation editor | Updated API calls |
| `nova/ui/js/cards.js` | Entity cards | Updated presentation save API call |

---

## Nova Codebase Structure

```
nova/
├── config.json                 # Server configuration (scopeId, auth, tcp, ui settings)
├── main.py                     # Entry point - spawns Core + Server processes
├── requirements.txt            # Python dependencies
│
├── core/                       # Core process - truth authority
│   ├── __init__.py
│   ├── core.py                 # Main Core class - IPC handler, DB access
│   ├── contracts.py            # Shared contracts (TimelineMode, EventType)
│   ├── database.py             # SQLite truth database access
│   ├── ipc.py                  # Inter-process communication (Core side)
│   ├── parser.py               # Event parsing/normalization
│   ├── manifests/              # Card manifest system
│   │   ├── __init__.py
│   │   ├── cards.py            # Card manifest loader
│   │   └── cards/              # Individual card manifests (JSON)
│   └── drivers/                # Protocol drivers
│       ├── __init__.py
│       └── base.py             # Base driver class
│
├── server/                     # Server process - WebSocket edge
│   ├── __init__.py
│   ├── server.py               # Main server - routes, WebSocket, presentation API
│   ├── auth.py                 # JWT authentication, cookie management
│   ├── ipc.py                  # Inter-process communication (Server side)
│   ├── userStore.py            # User persistence (JSON file)
│   ├── streamStore.py          # TCP stream definitions (SQLite)
│   ├── streams.py              # Multi-protocol stream manager
│   └── presentationStore.py    # Presentation overrides storage
│
├── data/                       # Runtime data (gitignored)
│   ├── nova_truth.db           # Truth database
│   ├── users.json              # User records
│   ├── streams.db              # Stream definitions
│   ├── users/                  # Per-user data
│   │   └── {username}/
│   │       └── presentation.json   # User's presentation overrides
│   └── presentation/
│       └── defaults/           # Admin default presentations
│           └── {scopeId}.json
│
├── exports/                    # Export downloads (temporary)
│
├── logs/                       # Server logs
│
└── ui/                         # Web UI (static files)
    ├── index.html              # Main app page
    ├── login.html              # Login page
    ├── register.html           # Registration page
    ├── approval-pending.html   # Pending approval page
    ├── admin.html              # Admin panel
    │
    ├── css/
    │   ├── main.css            # Core styles
    │   ├── admin.css           # Admin panel styles
    │   ├── cards.css           # Entity card styles
    │   ├── chat.css            # Chat panel styles
    │   ├── login.css           # Auth page styles
    │   ├── presentation.css    # Presentation editor styles
    │   ├── streams.css         # Stream panel styles
    │   └── timeline.css        # Timeline control styles
    │
    ├── js/
    │   ├── init.js             # App bootstrap, auth check
    │   ├── auth.js             # Auth state management
    │   ├── websocket.js        # WebSocket client, message routing
    │   ├── timeline.js         # Timeline control, playback
    │   ├── display.js          # Event display, formatting
    │   ├── cards.js            # Entity cards rendering
    │   ├── map.js              # Cesium geospatial map
    │   ├── presentation.js     # Presentation editor modal
    │   ├── streams.js          # TCP stream panel
    │   ├── chat.js             # Chat functionality
    │   └── admin.js            # Admin panel logic
    │
    └── assets/
        ├── models/             # 3D models (.gltf, .glb)
        └── textures/           # Map textures (local imagery)
```

---

## Scope Resolution Flow

```
User Request
     │
     ▼
┌─────────────────────────┐
│  _getAuthUser(request)  │ ← Get user from JWT cookie
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  _getEffectiveScopes(user)      │
│                                 │
│  userScopes = user.allowedScopes│
│  serverScopes = config.allowedScopes │
│                                 │
│  if 'ALL' in userScopes:        │
│      return serverScopes        │
│  else:                          │
│      return userScopes ∩ serverScopes │
└───────────┬─────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  _resolveRequestScope(...)      │
│                                 │
│  if ?scopeId in query:          │
│      validate scopeId ∈ effective │
│      return scopeId             │
│                                 │
│  if len(effective) == 1:        │
│      return only scope          │
│                                 │
│  if multi-scope:                │
│      GET: return None (aggregate) │
│      PUT/DELETE: return 400     │
└─────────────────────────────────┘
```

---

## Completion Status

### ✅ Implemented in This Phase
- Multi-scope user support (allowedScopes, aggregation, scope resolution)
- Presentation storage (JSON files: user overrides + admin defaults)
- WebSocket real-time sync across sessions
- Admin scope management endpoints
- Removed hardcoded UI scope

### ✅ Already Existed (Not Changed)
- UiCheckpoint generation (every 500s, configurable)
- Bounded seek (checkpoint + updates within 120s timeout)
- ManifestPublished events at startup
- Cesium local-only rendering (Ion disabled, local assets)
- Cursor-driven time (no free-running clock)

### Testing Checklist
- [ ] Single-scope user can GET/PUT/DELETE without `?scopeId=`
- [ ] Multi-scope user gets aggregated GET
- [ ] Multi-scope user gets 400 on PUT/DELETE without `?scopeId=`
- [ ] Admin can list scopes via `/api/admin/scopes`
- [ ] Admin can set user scopes via `PUT /api/admin/users/{id}/scopes`
- [ ] WebSocket broadcast on presentation change
- [ ] Other sessions receive and apply `presentationUpdate`
- [ ] New users get correct default scopes (admin=ALL, operator=[])

---

## Known Deviations from Architecture Doc

1. **Presentation stored as JSON files, not Metadata lane events**
   - Architecture doc says: "presentation-truth events in Metadata lane"
   - Implementation: JSON files under `data/users/` and `data/presentation/defaults/`
   - Justification: Presentation is per-user, non-deterministic, view-only - doesn't belong in truth timeline
- [ ] Existing users migrated correctly on server start
