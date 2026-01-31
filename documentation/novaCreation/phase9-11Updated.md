# NOVA Phase 9–11 Implementation Plan (Canonical)

**Scope:** This document is the single implementation plan for all remaining work in **Phases 9–11** (post–Phase 8/8.1), as discussed in our chat. It is intended to be complete and implementable without guessing.


**Phase scope note (redefined):**
- **Phase 9:** Auth + admin approval + roles + replayable chat
- **Phase 10:** Admin controls + user-defined presentations/overrides + Cesium geospatial background
- **Phase 11:** Replay service (runs/manifests/bundle export)
- **Phase 12:** Ground/Archive mode (moved out of this plan; intentionally not covered here)

This plan intentionally redefines scope vs any older phase numbering. Ground/Archive mode work is deferred to Phase 12.

---

## Global invariants (do not violate)

1) **Server is stateless with respect to client sessions**
- No durable per-connection / per-instance state (e.g., “which websocket is bound”) is persisted.
- Runtime bindings and open sockets exist only in-process while connections are open.

2) **Single authoritative time**
- The **NOVA cursor time** (server-authoritative) is the timebase for:
  - UI replay/live
  - Cesium clock
  - run “On/Off” capture (sets start/stop)
  - chat replay highlighting

3) **Replay safety**
- Replay must not trigger **hardware/C2 effects**.
- Serving data (UI updates, TCP/UDP/WS stream-out, exports) is allowed.

4) **UI lane drives the webpage UI**
- The geospatial layer and operator UI are driven from **UI lane** messages.
- Metadata is available by subscription/request, but we do not require it for the base geospatial rendering.

5) **Last write wins**
- For concurrent edits (presentation overrides, run definitions, etc.), last write wins.

---

## Integration surfaces (what implementors build against)

### A) Browser “consumer apps” (preferred for plotting / viewing)
- Served **same-origin** under NOVA (same scheme/host/port), at a route such as: `/apps/<name>`
- Inherits NOVA auth cookies and can use the same WebSocket + HTTP APIs.
- Uses WebSocket stream-out for parsed lane when desired.

### B) Stream-out transports (already implemented in Phase 8/8.1; referenced here for Phase 9–11 behavior)
- **TCP, UDP, WebSocket stream-out** behave as *dumb pipes* for selected data.
- Output format options (per stream definition):
  - **payloadOnly:** send only the payload bytes/JSON payload as-is.
  - **identityWrapped:** send a flat dictionary/object that includes identity keys plus the payload.


Alignment note:
- TCP is the baseline; UDP and WebSocket are alternate transports using the same stream-out definition shape and auth gating.
- Transport choice must not introduce a separate control plane or alter selection/timeline-binding semantics.
> Note: Metadata integration for stream-out (e.g., “also subscribe to metadata”) is **future** and is not required in Phases 9–11.

---

# Phase 9 — Auth + Admin approval + Roles + Replayable Chat

## 9.1 Accounts and roles

### Roles
- `admin`
- `operator`

Viewer role is intentionally omitted (removed from scope).

### Signup + approval workflow
1) **Sign up**
   - User chooses username + password.
   - Account is created in **PENDING** state.
2) **Admin approval**
   - Admin approves → account becomes **ENABLED** (`operator` by default).
   - Admin can also deny/delete pending accounts.
3) **Login**
   - Only ENABLED users can log in.

### Bootstrap admin credentials (required)
- Provide one or more default admin credentials in a local config file (or equivalent configuration mechanism).
- On first server start (or if no admins exist), bootstrap these admin user(s) into the users table.

### Auth mechanism (simple + good UX)
- Use **JWT in httpOnly cookie** for same-origin browser usage.
- **Persisted sign-in**: cookie is persistent across browser restarts.
- **No server-side session store** required.

Recommended defaults (safe, simple):
- Access JWT expiry: **12–24 hours**
- Cookie max-age (persisted login): **7–30 days**
- On expiration: user is prompted to log in again (keep it simple).
- Revocation/reset support without sessions:
  - Store a `tokenVersion` (or `passwordVersion`) on the user record.
  - Include it in JWT claims.
  - Reject JWT if versions mismatch (supports logout-all, password resets).

### Authorization gates
- Admin-only:
  - approve/deny/disable/delete users
  - promote/demote roles
  - reset passwords
- Operator:
  - normal UI, replays, exports, stream management (as already implemented)

## 9.2 Admin UI (complete, minimal)

Admin page must support:
- View user list with state: `PENDING`, `ENABLED`, `DISABLED`
- Actions:
  - Approve (PENDING → ENABLED)
  - Disable / Enable
  - Reset password (admin sets temp password)
  - Promote / Demote (admin/operator)
  - Delete user

Acceptance criteria:
- Pending users cannot log in.
- Disabled users cannot log in.
- Admin actions are enforced server-side (not just UI).

## 9.3 Chat (metadata truth; replayable)

### Chat scope
- Chat is **per scopeId**.
- Admin can view chats across all scopes (scope selector).

### UI behavior (live)
- Chat panel visible to all logged-in users.
- Each message displays:
  - **username**
  - **timestamp** (smaller, under username)
  - message body
- Sending:
  - **Enter** sends
  - **Shift+Enter** inserts newline
  - Send button sends
- On send: message draft clears.

### Storage + streaming
- Each chat message is stored as a **standard metadata truth event** using the system’s normal **truth envelope contract** (e.g., lane=metadata, messageType, sourceTruthTime, identity fields, ordering contract).
- Chat payload fields (minimum): `username`, `userId`, `messageText`.
- Identity: use the project’s established **scope-global identity** convention (e.g., uniqueId="__scope__" or equivalent).
- Ordering/determinism: rely on the standard truth ordering contract; do not introduce custom sorting.
- On page load:
  - Load recent history (e.g., last N messages; pick a reasonable N such as 200).
- Live updates:
  - New messages appear in real time for all users in scope.

### Replay behavior (highlight + autoscroll)
- When in replay mode:
  - Show the full chat history that is loaded.
  - Highlight the “current” message at/near cursor time (nearest <= cursor, or nearest overall).
  - Autoscroll to keep the highlighted message in view **until the user manually scrolls**.
  - If the user scrolls, stop autoscrolling (standard chat behavior). Provide a “follow cursor” toggle to re-enable.

Acceptance criteria:
- Reload shows history.
- Two users see the same live messages.
- Replay scrub updates highlight position deterministically.
- Autoscroll stops after user scrolls.

---

# Phase 10 — Presentation system + Cesium geospatial background (local-only)

## 10.1 Presentation system (defaults + overrides; extensible but controlled)

### Goals
- Admin sets **system defaults** that apply to other users **only if** they haven’t set their own overrides.
- Users set personal overrides that apply across their sessions/instances in that scope.

### Layers (simple, deterministic)
For each key:
1) **User override** (per-user, scope)
2) **Admin default** (scope-wide)
3) **Producer hint** (if provided; optional)
4) **Factory default** (static file in repo)

Resolution rule:
- Highest available layer wins **per key**.

### Extensible keys without bloat
- Create a single **PresentationSchema registry** (one file / one module) that defines:
  - allowed keys
  - types (string, color, modelRef, etc.)
  - applicable target types (entity, stream, run, event, etc.)
  - validation rules and fallbacks

Keys can grow, but only by adding them to this registry.

### Persistence + propagation
- Admin defaults and user overrides are persisted as **metadata truth events** (replayable, time-aligned).
- Changes propagate immediately:
  - Admin changes → all users in scope (except overridden keys)
  - User changes → all instances for that user
- Conflict: last write wins.

### UI surfaces
- Admin UI: edit defaults, apply immediately to scope (non-destructive to user overrides)
- User UI: edit personal overrides

Acceptance criteria:
- Admin default change updates all users who have not overridden that key.
- User override persists and takes precedence.
- Updates propagate across multiple sessions/instances.

## 10.1B UI lane replay contract (required)

UI state must remain replayable and deterministic:
- UI lane emits **UiUpdate** and **UiCheckpoint** events that reference **ManifestId/ManifestVersion**.
- Manifests are published as truth (e.g., **ManifestPublished**) and checkpoints reference a published version.
- Cesium and UI rendering consume UI lane updates that are derived from this replayable UI state.

## 10.2 Cesium geospatial view (page background)

### Layout
- Cesium is the **full-page background** behind panels and chat.
- UI panels overlay on top.

### Data source
- Geospatial updates come from **UI lane** messages.
- Minimum fields (Phase 10): `lat`, `lon`, `alt`
- Identity for each entity must be stable (whatever identity scheme is already used).

### Clock control (low overhead, low drift)
- Cesium time is driven by **cursor time**.
- Use `SampledPositionProperty` for entity positions.
- **Interpolation mode:** linear.
- **Rate guidance:**
  - Target a **max effective update of 5 Hz** for Cesium positioning inputs.
  - Keep NOVA server-side UI updates well below that where possible.
  - Downsample if upstream updates exceed this (to avoid memory bloat).

> Implementation note: Cesium can render smoothly between samples using interpolation; do not allow Cesium’s internal clock to free-run away from cursor time.

### Local-only assets (no internet calls)
- Cesium JS must be served locally by NOVA (no CDN).
- Models served locally:
  - Provide a local folder of GLTF/GLB models.
  - `modelRef` resolves to local model assets.
- Imagery/tiles: **not implemented in Phase 10**
  - Architect Cesium initialization so imagery providers can be added later without refactor.
  - Ensure no part of Cesium setup triggers external fetches.
  - (Reference: novaCore handled fully-local Cesium well; follow that pattern.)

### Map abstraction (future readiness)
- Provide a minimal adapter boundary so map implementation isn’t fused to app logic:
  - `setTime(cursorTime)`
  - `upsertPose(identity, lat, lon, alt)`
  - `applyPresentation(identity, displayName, color, modelRef)`
  - `remove(identity)`

Acceptance criteria:
- No external network calls (verified).
- Live mode: entities update correctly.
- Replay: scrub updates positions and labels correctly with cursor time.
- Presentation changes update map visuals immediately.

---

# Phase 11 — Replays tab + Run manifests + Driver bundle downloads

## 11.1 Replays tab UI (mirrors Streams)

### Structure
- Always-visible **Make Replay** shield opens a template card.
- Each created run/replay becomes:
  - its own shield
  - its own card for editing/actions

### Per-user default run manifest
- User chooses a default run manifest type via dropdown in template card.
- New runs default to that manifest type until changed.
- Persist this preference in the user’s run settings file.

## 11.2 Run manifests (extensible, manifest-driven)

### Manifest types (Phase 11)
1) **Generic run**
- Contains the shared fields: start/stop, runNumber, runName, analystNotes, On/Off, etc.

2) **hardwareService run**
- Inherits from generic and includes:
  - On/Off buttons
  - Signal selection toggles grouped by constellation
  - (For now: same base fields as generic + domain-specific fields. Keep inheritance path for future run types.)

> The manifest system should drive the UI rendering of these forms (consistent with the overall cards/shields approach).

## 11.3 Run fields (generic)

- `startTime` (second precision)
- `stopTime` (second precision)
- `runNumber`:
  - default = previous runNumber + 1 (for that user)
  - editable
- `runName`:
  - default = last entered runName (for that user)
  - editable
- `analystNotes` (text)
- “On” / “Off” behavior:
  - clicking **On** sets startTime to current cursor time
  - clicking **Off** sets stopTime to current cursor time
  - after clicking, show normal editable datetime inputs

## 11.4 hardwareService signal selection (hardcoded list for now)

- Provide a table of toggles/checkmarks:
  - Organized by constellation
  - Includes every sub-signal (from `sdk/parsers/sbf`)
- For now: the list is **hardcoded into the manifest** (copied in once).
- Default selection:
  - auto-populate from last-selected configuration for that user (last write wins).

## 11.5 Storage layout (per-user run folders)

### Architectural note: runs are **not truth**
- Runs are **user artifacts/config**, not truth.
- Creating/editing runs must **not** emit truth events that alter replay-visible system state.
- Runs only drive: (a) UI convenience (clamp window), and (b) export/bundle generation.
- Exports are generated from the truth DB; run files merely define the requested window/config.


### Directory layout
- Each user has a folder: `data/users/<username>/`
- Runs folder: `data/users/<username>/runs/`
- One folder per run:
  - name: `"{runNumber}. {runName}"`

### Contents per run folder
- `run.json` (or similar) containing:
  - run definition fields
  - selected manifest type
  - signal selection (if applicable)
- Optional: cached export outputs and/or zip artifacts

### Naming + sanitization
- Define and implement filename sanitization rules for `runName` (filesystem safe).
- Decide whether folder names are immutable or can be renamed when runName changes; record the canonical runName inside run.json regardless.

Concurrency:
- last write wins.

## 11.6 Replay playback + export

### Playback behavior
- Selecting a run clamps the user’s playback window to `[startTime, stopTime]`.
- All standard playback controls operate within the window.
- Jump to live exits clamp.

### Bundle export (Phase 6 drivers; zipped)
- “Download bundle” triggers the Phase 6 driver export pipeline for `[startTime, stopTime]`.
- Zip includes **exactly** the folder/file structure the drivers would have written.
- Delivered through NOVA.

Acceptance criteria:
- Runs persist across reload/restart.
- Auto-increment runNumber and default runName work.
- Signal selection persists and defaults correctly.
- Clamp playback works.
- Export zip matches driver outputs for the same window.

---

## End-to-end acceptance checklist (Phase 9–11)

### Phase 9
- [ ] Signup creates pending user.
- [ ] Admin approves pending user; user can log in.
- [ ] JWT cookie persists across browser restarts (good UX).
- [ ] Admin can reset password and revoke old JWTs (via tokenVersion).
- [ ] Chat works live, shows username + small timestamp.
- [ ] Replay mode highlights current chat message and autoscrolls until user scrolls.

### Phase 10
- [ ] Presentation defaults/overrides work: user overrides > admin defaults > producer hint > factory.
- [ ] Admin default changes propagate immediately across scope (non-destructive).
- [ ] User overrides propagate across user’s instances immediately.
- [ ] Cesium background renders entities from UI lane lat/lon/alt.
- [ ] Cesium makes zero internet calls.
- [ ] Cesium time follows cursor time with low drift; effective update target <= 5 Hz.

### Phase 11
- [ ] Replays tab exists; Make Replay shield + per-run shields/cards.
- [ ] Run manifest dropdown selects between generic/hardwareService; persists user default.
- [ ] On/Off sets start/stop from cursor time, then editable.
- [ ] Signals list is hardcoded into manifest; defaults to last selection.
- [ ] Runs stored under `data/users/<user>/runs/{runNumber}. {runName}/`.
- [ ] Download bundle zips driver outputs for the run’s time window.

---

## Notes / explicitly deferred items
- Audit logging/events (login/admin actions/export requests) — deferred to a later phase.
- Offline imagery/tiles integration for Cesium (architecture ready; implement later).
- Metadata subscription requirements for stream-out clients (future).
- Additional presentation keys beyond those registered in PresentationSchema (future; add intentionally).
