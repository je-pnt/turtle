# uiDataPlan.md (NOVA UI + Data Plane) — Draft v3

> **Intent:** describe the *end-state UI behaviors*, the *data/command/chat contracts that drive the UI*, and the *implementation plan* to build it **without referencing any legacy/failed architecture**.
>
> This document is **suggestive** where choices are still open. Items that still require a decision are labeled **TBD** (none of the prior TBDs remain except where explicitly noted).

---

## 0) Non‑negotiable architecture invariants (what this UI plan must not break)

- **One truth DB**: all truth (raw/parsed/ui/commands/descriptors/chat/overrides) is append-only and served by timeline queries.
- **Planes are inseparable**: data + metadata + commands + UI updates share the same timeline model; **only external side effects are disabled in replay**.
- **Command replay rule**: command *records* exist; live *dispatch* executes only in LIVE (blocked in REPLAY at UI + Server + Core).
- **UI must not “invent time”**: the UI cursor/time display is disciplined by server-issued cursor/time so the timeline never drifts away from the data stream.
- **Transport is an abstraction**: producers integrate via the public NOVA subject/envelope contract and may or may not use the SDK; SDK just provides a binding.
- **Server is a data server**: no durable per-client “online state” or session state; any durable shared state must be represented as truth events.

---

## 1) Pipeline alignment (hardwareService → NOVA UI)

### 1.1 End-to-end flow (no legacy references)

1) **hardwareService** runs device I/O + execution.
2) **novaAdapter** (in hardwareService) converts device outputs into NOVA envelopes:
   - **RAW** chunks (preserve original chunk boundaries)
   - **PARSED** events (typed/normalized)
   - **METADATA** descriptors (identity + capabilities + stream descriptors)
   - **UI** lane events (optional earlier; eventually UiUpdate/UiCheckpoint as truth)
   - **COMMAND** events (request/progress/result)
3) **/transport** publishes those envelopes to subjects defined by the **public subject contract**.
4) **NOVA Core** subscribes, validates, and appends to the truth DB using the ordering contract.
5) **NOVA Server** hosts the Web UI and exposes a single timeline streaming/query surface over WebSocket.
6) **Web UI** renders shields/cards/chat/overrides from the events it receives (and only from those events).

**Key rule:** the UI is driven by **Metadata lane + UI lane** (+ **Command lane** for controls/feedback). The UI lane (UiUpdate/UiCheckpoint events) is the **only** lane used for rendering card/shield telemetry data—Raw and Parsed lanes are not streamed to the UI.

---

## 2) End UI behaviors (what the operator experiences)

### 2.1 Page layout (high level)

- **Left:** *Shields panel* — discovered entities in a strict hierarchy (system → container → uniqueId).
- **Main:** *Cards panel* — one or more cards for the selected shield (telemetry/status/controls).
- **Optional panels:** map, oscope/spectrum, logs (not required to prove timeline + command correctness).
- **Bottom:** *Timeline controls* (Section 2.2).

Modern dark theme. Keep styling consistent and minimal.

### 2.2 Timeline controls (behavior contract)

Bottom bar containing, left → right:

1) **Play/Pause**
   - Button shows “Pause” when playing, “Play” when paused.
   - Pausing while LIVE immediately transitions to REPLAY (cursor freezes at current server cursor time).

2) **Jump to Live**
   - Switches to LIVE policy (cursor tracks near-now).
   - Clears rewind state.
   - Resets rate to **1**.

3) **Rate** (signed + magnitude)
   - Sign: `+` forward, `-` backward.
   - Magnitude: playback speed multiplier.
   - Rate affects **server pacing**. Client never free-runs its own playback clock.

4) **Date/Time input**
   - Entering a time jumps cursor to that time.
   - If playing: playback begins at that time using current rate.
   - If paused: cursor jumps but stays paused.

5) **Timeline track + draggable cursor**
   - Shows ~1 hour window (max, configurable).
   - On load: cursor at far right (near-now); visible window grows/slides with time.
   - Dragging cursor updates cursor time; on release, Date/Time input updates to match.

6) **LIVE/REPLAY indicator + time readout**
   - Shows LIVE vs REPLAY.
   - Shows cursor time to max 0.1s displayed resolution.

### 2.3 Anti-drift rule (timeline must match data)

- Server is authoritative for: **current cursor time**, **stream parameters**, and **pacing**.
- UI displays server cursor time and renders timeline from it.
- UI may interpolate cosmetically between updates but must correct to server truth (no accumulating drift).

Implementation implication: each stream chunk includes cursor time (or server sends periodic cursor updates).

---

## 3) Identity + shield hierarchy contract (locked)

### 3.1 Identity fields (required)

**Public/external identity is always**: `scopeId + lane + systemId + containerId + uniqueId`

- `systemId`: **the data system that produced the truth** (e.g., `hardwareService`, `adsb`, `thirdPartyX`).
- `containerId`: **the node/payload/site/vehicle/rig/platform instance** the producer belongs to (e.g., `node1`, `payloadA`, `truck7`).
- `uniqueId`: **the entity identifier within that system+container** (deviceId/taskId/etc). Required for anything renderable.

**messageType** is required for non-raw lanes and is the lane-internal message identity (the \"name under the lane\"; e.g., Parsed: `gnss.navPvt`; Metadata: `ChatMessage`; UI: `UiUpdate`).

Also required:
- `displayName`: human label (defaults to `uniqueId`).
- `scopeId`: alphanumeric; used for subscription filtering.

### 3.2 Shields hierarchy (required)

Shields are rendered in a strict hierarchy:

`systemId → containerId → uniqueId`

Search/filter:
- Must support search by `systemId`, `containerId`, `uniqueId`.
- Optional filters: type/category (searchable), but hierarchy remains primary.

### 3.3 ID defaulting / derivation rule

Producers may omit `systemId`, `containerId`, `uniqueId` in envelope when encoded in subject (or configured per connection). NOVA derives missing IDs from subject/config; if both are provided and conflict, NOVA **rejects** the event (no silent mutation).

---

## 4) UI data contracts (truth fields the UI expects)

These are minimum semantics; they are produced by novaAdapter (or other producers) and recorded as truth.

### 4.1 Status fields (client-only computation)

**No server-side online state.** The client computes online/last-seen from truth timestamps and the current timeline cursor.

Recommended UI algorithm (example):
- Determine `lastSeenTime` for an entity by scanning received events for that entity (timebase-selected timestamp).
- Compute `online = (cursorNow - lastSeenTime) <= onlineTtlMs`.
- `onlineTtlMs` is a UI config value.

### 4.2 Position + GNSS fields (as available; locked sources)

- Position: `lat`, `lon`, `alt` (if applicable)
- GNSS timing:
  - UBX: NAV-PVT (converted to UTC) provides `gnssTime` / `itow`
  - SBF: MeasEpoch (converted to UTC) provides `gnssTime` / `itow`

`itow` exists as a practical “proof the pipe works” signal for live vs replay validation.

### 4.3 Map model + color (presentation fields)

Entities can have:
- `modelRef` (or model key) and
- `color`

These are **presentation overrides** (Section 8), not producer truth requirements.

---

## 5) Commands in the UI (buttons, uploads, results)

### 5.1 End-state behavior

- Cards expose actions (eventually manifest-defined).
- Clicking an action produces a `CommandRequest` truth event and triggers live dispatch.
- Progress/results are streamed back as Command lane truth.

### 5.2 Replay safety (hard rule)

In REPLAY:
- UI disables execution controls.
- Server rejects execution attempts.
- Core rejects execution attempts.
- Command history still displays (truth).

### 5.3 Config upload actions (required)

Config upload exists and must follow the **/svs flow** (do not invent a new bespoke upload path).
- The upload results in a normal `CommandRequest` truth record.
- Any file transfer mechanism must remain consistent with the single command surface (use existing /svs upload mechanism; do not add a second command surface).

---

## 6) Chat is truth (locked)

- Chat messages are truth events.
- Chat is **global-per-scope** (not per-entity).
- Chat is replayable on the timeline; in REPLAY it shows messages in the selected time window.
- Chat uses `authorDisplayName` (resolved via overrides; Section 8).

---

## 7) Map + advanced panels (deferred priority)

Map and oscope/spectrum can be implemented whenever it best fits workflow.
Priority remains:
1) data play/replay accuracy,
2) parsing correctness,
3) command control correctness.

---

## 7.1) TCP Stream Discovery (Phase 8.1)

TCP streams are **output forks** (not truth sources). Stream definitions are operational config stored in `streams.db`, not truth events.

### Stream Shields in UI

Stream shields come from **API**, not truth events:

1. UI calls `GET /api/streams` or sends WebSocket `listStreams` message.
2. Response includes stream definitions with `entityType: tcp-stream` or `setup-streams`.
3. Manifest lookup works normally: `tcp-stream` → `tcp-stream-card`, `setup-streams` → `setup-streams-card`.
4. **Setup Streams** shield is always present (system entity).

### Shield Identity (Phase 7 rule compliance)

| Shield | systemId | containerId | uniqueId | EntityType |
|--------|----------|-------------|----------|------------|
| Setup Streams | `tcpStream` | `system` | `setupStreams` | `setup-streams` |
| Per-stream | `tcpStream` | `streams` | `<streamId>` | `tcp-stream` |

**Note**: `systemId=tcpStream` (not `nova`) ensures shields appear under Phase 7 external-system rule.

### At-a-Glance Fields (Per-Stream Shield)

- **Name**: stream display name
- **Port**: TCP port
- **Mode**: `LIVE` or `bound:instanceX`
- **Format**: `payloadOnly` | `hierarchyPerMessage`
- **Selection**: lane + filter summary

### Stream Card Behaviors

**Setup Streams Card**:
- Create new stream definition (name, port, lane, filters, format)
- List existing streams (table with status, actions: Open, Delete)

**TCP Stream Card** (per-stream):
- **Persisted fields**: name, port, selection (lane + filters), output format, backpressure policy
- **Runtime controls**: Enabled/Disabled toggle, "Tie to my timeline" toggle
- **Status (read-only)**: connection count, bound instance info
- **Actions**: Start/Stop, Delete

### Binding Behavior (Timeline Control)

- **Default**: LIVE-follow (stream tails real-time data, no WebSocket required)
- **Optional**: "Tie to my timeline" binds stream to user's WebSocket cursor
- **Last-binder-wins**: only bound instance's cursor controls output
- **Disconnect fallback**: bound instance disconnect → stream reverts to LIVE-follow

### Output Formats

| Format | Output | Constraint |
|--------|--------|------------|
| `payloadOnly` | Raw bytes or JSON payload only | Selection must resolve to single identity |
| `hierarchyPerMessage` | `{"s":"...","c":"...","u":"...","t":"...","p":{...}}` | Multi-identity safe |

**UI validation**: `payloadOnly` warns/blocks on multi-identity selection at create/edit time.

---

## 8) Presentation overrides (user overrides + admin suggested defaults) — locked

This section answers:
- “Everything should have a name, default to uniqueId.”
- “Per-user overrides across all their instances.”
- “Admin suggested defaults broadcast to others if they haven’t customized.”
- “Overrides influence export naming/labels later.”

### 8.1 Override layers (merge order)

For any renderable attribute (name/model/color, etc.), clients compute:

1) **Admin suggested defaults** (truth events published by admin; scope-wide)
2) **User overrides** (truth events authored by that user; user-scoped)
3) **Inherent fallback** (producer descriptor fields; then uniqueId)

Merge rule:
- **User overrides always win** over admin defaults.
- Admin defaults apply **if and only if** the user has not set an override for that key.

### 8.2 Admin suggested defaults (broadcast behavior)

Admin can publish a suggested defaults set that:
- is written as truth (Metadata lane) and
- becomes visible immediately to all users.

**UI requirement:** when an admin edits defaults, the UI presents a **"Broadcast to other users"** checkbox.
- Default state: **off**.
- If checked, the update is published as **Admin suggested defaults** (scope-wide).
- If unchecked, the update remains **local to the current user** (per-user override only; no broadcast).

Clients apply it immediately **only for keys they have not customized**.

Suggested event (illustrative name):
- `PresentationDefaultsPublished`
  - keyed by `(scopeId, systemId, containerId, uniqueId)`
  - contains `displayName`, `modelRef`, `color`, and optional other presentation keys.

### 8.3 Per-user overrides (sync across all instances)

When a user edits a name/model/color:
- the client emits a per-user override truth event (Metadata lane),
- all sessions belonging to that user subscribe and update immediately (same scope),
- changes are definitive and consistent across that user’s browsers/instances.

Suggested event (illustrative name):
- `UserPresentationOverrideUpserted`
  - keyed by `(userId, scopeId, systemId, containerId, uniqueId)`
  - contains the overridden keys (displayName/modelRef/color)

**Server behavior:** purely records + streams these truth events; it does not maintain a separate per-user state store.

### 8.4 Export naming alignment (future, but contract now)

When the user requests exports (KML/CSV/etc later phases):
- the client sends its computed overrides dictionary with the export request,
- exports use those names/labels for filenames and attributes.

---

## 9) Implementation plan mapping (how this fits the master implementation plan)

- **Phase 5:** basic shields/cards + command path; no manifest engine yet; basic chat may be started as truth.
- **Phase 7:** manifest-driven shields/cards/layout; UiCheckpoint; remove any remaining hardcoded card paths.
- **Phase 9:** admin identity/roles; this is where admin UI for publishing defaults becomes official, but the **data contract** for defaults can be established earlier as Metadata truth.

**Note:** per-user overrides require `userId` identity. If Phase 9 (auth) is not implemented yet, you can still:
- model the events and
- keep overrides local-only until auth lands,
but the end-state contract remains as above.

---

## 9.1 Admin roles + admin page (end-state, required)

This is the **end-state admin UX** and **role behaviors** that must be implemented without adding new auth mechanisms or side channels.

### Admin roles (capabilities)

- **User approval workflow**: admin reviews pending users and approves/denies access.
- **Role management**: admin can promote other users to admin or demote them.
- **Password management**: admin can reset/change user passwords.
- **User removal**: admin can delete users.

**Rule:** admins do **not** control authentication mechanisms or auth configuration; they only manage users and roles.

### Admin page (UI requirements)

- Separate **admin-only page** linked from the main UI (visible only to admins).
- Shows a **clear, concise user table**: status (pending/active/disabled), role, last seen, actions.
- Actions on the same page: **Approve**, **Promote/Demote**, **Reset Password**, **Delete User**.

### Admin defaults broadcast (UI requirement)

When admins edit defaults (models, names, colors, etc.), the UI must provide:
- **"Broadcast to other users"** checkbox (default: off).
- If checked: publish Admin suggested defaults (Metadata lane).
- If unchecked: only write the admin's per-user overrides.

---

## 10) Do-not-do list (to avoid poisoning with old architecture)

- Do not add GEM concepts, dual-truth DBs, or hidden subject routing.
- Do not add parallel UI pipelines (hardcoded cards and manifest cards) without refactoring the old path to call the new one or deleting it.
- Do not let UI run a free-running timeline clock independent of server cursor truth.
- Do not make schemas/config “dynamic” in a way that invites schema creep.

---

## 11) Open questions (only what remains)

1) **svs config upload shape:** what is the canonical message/transport shape we must follow (exact fields)?
2) **UserId source before Phase 9:** until auth is complete, do you want:
   - (A) local-only overrides (no sync), or
   - (B) a temporary dev userId in config for sync testing?
