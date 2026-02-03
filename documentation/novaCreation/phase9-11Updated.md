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

# Phase 10 — Presentation (view-only) + Cesium geospatial background (local-only)

## Phase 10 guiding rules (do not violate)
- **Presentation is NOT truth.** It is a per-user, timeless, stateless override dictionary used only for UI and export labeling/appearance. It must not affect telemetry truth, identity, ordering, or determinism.
- **Manifests are published once** (when loaded at server/session start) and **UiUpdates must not carry manifest refs** (no stream bloat). During live and replay, interpret UiUpdates using the **last published manifest** at or before the current cursor/playhead time.
- **Seek reconstruction is bounded:** load the **last UiCheckpoint** then apply **recent UiUpdates within timeout** (default 120s), not full-history scans.
- **All rate/cadence knobs are config-driven** (no hardcoding in code paths). Defaults are defined below.

---

## 10.1 Presentation system (view-only; per-user overrides + admin defaults)

### Purpose
Provide user-friendly names and map styling without changing truth data:
- Rename shields/entities for the current user
- Select local 3D models
- Choose colors
- Adjust scale

### What presentation is (and is not)
- **Is:** a per-user dictionary of overrides applied at render/export time.
- **Is NOT:** a truth lane, not a time-aligned stream, not replay-deterministic, not used for telemetry semantics.

### Identity model (explicit)
- `userId` is the user's **unique username** (NOVA ensures uniqueness).
- `scopeId` is the **literal scope string** used in subjects and API calls (use it exactly as-is).
- Entity identity for overrides is keyed by the entity's stable `uniqueId`.

### Allowed keys (Phase 10 minimal key set — no expansion)
Each entity (by `uniqueId`) may have:
- `displayName`: string (default = `uniqueId`)
- `modelRef`: enum from dropdown (must resolve to an existing, valid local model file)
- `color`: RGB triple (from selector)
- `scale`: float

No other keys are permitted in Phase 10.

### Inheritance / layering (simple, deterministic)
For each key, effective value resolves as:
1) **User override** (per-user, per-scope)
2) **Admin default** (scope-wide)
3) **Factory default** (static base default; minimal)

Rule:
- Highest available layer wins per key.
- Absence means “inherit.”
- Last write wins within the same layer.

### UI surfaces
- **Admin UI:** edit scope-wide defaults (non-destructive to user overrides).
- **User UI:** edit personal overrides (timeless; may change during live or replay at any time).

### Model selection rules (local-only)
- Implementation must follow the working `/svs` reference pattern (locally hosted Cesium, imagery, models).
- Dropdown options are produced from locally hosted assets.
- Only **`.gltf`** models are allowed in Phase 10 and must be type-checked before presenting as an option.

Acceptance criteria:
- User can rename an entity during replay and see it update immediately (view-only).
- Admin defaults apply where the user has not overridden.
- No presentation change modifies telemetry truth or breaks replay determinism (presentation is strictly view-layer).

---

## 10.1B UI lane replay contract (manifest-published; checkpoint + recent deltas)

### Manifest publishing (no UiUpdate bloat)
- On server/session start (when the UI manifest is loaded), the server emits exactly one truth event:
  - **ManifestPublished(scopeId, manifestId, manifestVersion/hash)**
- Manifests rarely change. If they do, the server publishes a new ManifestPublished event at the time it is loaded.

**Interpretation rule (explicit):**
- During live and replay, interpret UiUpdates using the **most recent ManifestPublished** event at or before the current cursor/playhead time.
- UiUpdates must not include manifest references per event.

### UiCheckpoint cadence (config-driven)
- Emit a **UiCheckpoint** at session start.
- Emit periodic UiCheckpoint events every `uiCheckpointIntervalSeconds`.
  - Default: **500 seconds**
  - Config-driven (not hardcoded).

### Seek / load reconstruction rule (bounded, explicit)
When the playback time changes (seek/scrub) or a client loads:
1) Load the **last known UiCheckpoint** for the target scope.
2) Load and apply **UiUpdate** events within `uiHistoryTimeoutSeconds` of the target time.
   - Default: **120 seconds**
   - Config-driven (not hardcoded).

Thus, reconstruction is:
> **checkpoint + recent UiUpdates within timeout**

No full-history reconstruction is required in Phase 10.

### UiUpdate rate guidance (config-driven; simplest batching only)
- `uiUpdateMaxHz` default: **5 Hz** (config-driven).
- If (and only if) it is trivially simple, the server may coalesce updates to meet the max rate:
  - Coalesce by `(scopeId, uniqueId)`
  - Keep only the most recent update per entity per window
  - Do not change semantics; persist what is served

If batching becomes complex, skip it and keep pass-through.

---

## 10.2 Cesium geospatial view (page background; local-only)

### Layout
- Cesium is the **full-page background** behind panels and chat.
- UI panels overlay on top.

### Data source
- Geospatial updates come from **UI lane** messages.
- Minimum fields (Phase 10): `lat`, `lon`, `alt`
- Identity for each entity must be stable (use established NOVA identity scheme).

### Clock control (low overhead, low drift)
- Cesium time is driven by **cursor time**.
- Use `SampledPositionProperty` for entity positions.
- Interpolation mode: linear.
- The UI must not free-run Cesium time away from cursor time.

### Local-only assets (zero internet calls)
- Cesium JS served locally by NOVA (no CDN).
- Models served locally (Phase 10: `.gltf` only; type-checked before listing).
- Imagery served locally (follow `/svs` reference); no external fetches.

Acceptance criteria:
- No external network calls (verified).
- Live mode: entities update correctly.
- Replay: scrub updates positions correctly with cursor time.
- Presentation overrides change map visuals immediately without changing truth.


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
