# additionalArchitecture.md
_Last updated: 2026-01-25 (America/Denver)_

This document is an **add-on** to the existing Nova documentation set (`nova architecture`, `nova api`, `gem architecture`, plus lane/truth/replay docs).  
Its purpose is to capture **missing/under-specified architecture decisions** we have already discussed and to make the documentation “build-complete.”

---

## Legend

- **REQUIREMENT**: must be true for the system to meet our intended design.
- **SUGGESTION**: a workable approach that helps reach the end-state; **not mandatory** (open to change).

---

## 0) Non‑negotiable architecture truths

### REQUIREMENT: Single authoritative truth
- **`novaArchive` is the single truth source** for:
  - streams (raw + parsed),
  - time-versioned metadata,
  - UI deltas/snapshots (so UI replay is complete),
  - command request/progress/result audit events,
  - replay/range queries for all consumers.
- **`novaCore` has no truth database.** It is a UI/interaction layer and (at most) a proxy/gateway.

### REQUIREMENT: DB + drivers (division of responsibility)
- Use the **truth database** for replay/pulling/range queries and for serving consumers (UI, PlotJuggler, etc.).
- Use **drivers** for writing daily cold files (e.g., raw `.bin` and parsed CSVs) and for exporting “truth files” for any requested time window.

### REQUIREMENT: Stateless server for playback
- The system must not create per-client “replay sessions” on the server.
- The **client timeline is the conductor**: it determines *what time window to pull* and *how to pace rendering/byte playback*.

### REQUIREMENT: Replay safety
- **Replay must never execute hardware commands.**
- Replay only replays **recorded** command requests/progress/results.

### REQUIREMENT: Scope model is the network control primitive
- `scopeId` controls visibility and network spread.
- A local payload publishes with its own `scopeId`.
- A ground/aggregator node subscribes broadly across scopes and therefore sees all.
- Subjects are not “local/global” — **scope subscriptions + validation** control reach.

---

## 1) Cold storage layout + driver system (archive file outputs)

### REQUIREMENT: Daily folder structure and canonical filenames
Document the exact cold file structure used by `novaArchive`, including:
- Folder-per-day organization (and **timezone rule** for “day” rollover).
- Raw byte artifacts:
  - `.bin` files per receiver/stream as designed.
- Parsed artifacts:
  - e.g., `oscope.csv`, `f{receiverName} llas.csv`, etc.
- Naming, rotation, and rollover rules (midnight handling, file boundaries).

### REQUIREMENT: Driver contracts (ingest + write + export)
Drivers must be documented with explicit contracts:
- **Ingest**: how raw/parsed messages are delivered to drivers.
- **Write**: how drivers write/rotate daily files and how failures are handled.
- **Export**: how drivers generate “truth parsed data files” for `(startTime, stopTime)` using the same logic as realtime.

### SUGGESTION: Separate driver roles (keep clean boundaries)
Split into two classes (possibly in one file) inheriting from a common base:
- `StreamDriver`:
  - raw ingest (bytes) + parsed ingest (typed JSON),
  - file writing,
  - export for time windows.
- `CommandAdapter`:
  - command -> raw bytes encoding,
  - optional acknowledgement parsing / progress decoding.

_(This is a suggestion. The only hard requirement is that file/export logic and command/protocol logic do not become entangled and untestable.)_

### REQUIREMENT: Failure semantics and verification
Document:
- What happens if DB write succeeds but file write fails (retry, mark incomplete, queue, etc.).
- How cold files are verified against DB truth for a window (hashes, counts, sampling, etc.).

---

## 2) Replication / ground station sync (append-only + hashes)

### REQUIREMENT: Distributed pull model
We intend to support a ground/archive station periodically pulling updates from remote nodes using:
- append-only artifacts,
- file/hash manifests,
- last-known sync watermarks,
- and a runtime “archive payload” configuration variable that controls what is pulled/replicated.

Document:
- What is replicated:
  - DB updates, cold files, or both (goal: one authoritative truth DB plus cold files written by drivers).
- How the sync watermark is defined (time, monotonic ingest id, hash chain head, etc.).
- Verification:
  - manifest hashing,
  - replay determinism checks,
  - gap detection.

### REQUIREMENT: Scope-aware replication
Replication must respect `scopeId`:
- What a node is allowed to pull (per scope).
- How “globally scoped” ground station nodes are assigned at runtime and subscribe/pull across scopes.

### SUGGESTION: Treat replication as “log shipping”
Consider a manifest that lists append-only segments:
- segment id, time range, hash, size,
- enabling resumable pulls and integrity verification.

_(Suggestion only; any approach is acceptable if it is append-only, deterministic, and verifiable.)_

---

## 3) Transport plan (/transport abstraction in SDK)

### REQUIREMENT: /transport is the abstraction layer
All messaging paths should be described through `/transport` (in `/sdk`) so underlying transport is abstracted.

### REQUIREMENT: Current bindings and intended evolution
Document the current transport bindings:
- Local: **IPC** for `hardwareService -> GEM -> novaArchive`.
- Network-wide: **NATS** for `remote novaArchive -> ground novaArchive`.
- UI: currently HTTP/WS, but desired direction is to migrate UI traffic behind `/transport` where feasible.

### SUGGESTION: Provide a transport “matrix” + migration steps
Add a small section listing:
- which components talk using which binding today,
- which flows will migrate first,
- what compatibility strategy is used during migration.

---

## 4) Stream plan (raw + parsed + UI lanes) and consumers

### REQUIREMENT: Raw + parsed streams from GEM to archive
GEM -> novaArchive must publish both:
- **Raw stream**: raw bytes (always bytes)  
  - used for `.bin` storage and TCP loopback replay.
- **Parsed stream**: typed JSON (for now)  
  - used for truth firehose (PlotJuggler) and replay and UI.

### REQUIREMENT: UI snapshots/deltas are complete and stored
All UI-visible fields must be produced from:
- UI lane snapshots/deltas that are **stored in the truth DB** and replayable.
- No “unrecorded UI-only state” that can’t be reconstructed from DB truth.

### REQUIREMENT: “File write commands” vs “UI update commands” are distinct flows
Document separate flows for:
- UI updates / commands / confirmations,
- file writing/export control (if any exists).

---

## 5) Stateless playback: client timeline as conductor (adaptive pulling)

### REQUIREMENT: Pull-based timeline replay for all consumers
For UI, PlotJuggler, and raw TCP replay, the end goal is that consumption can be determined by:
- client timeline cursor,
- playback rate,
- and time-window pulls from the truth DB.

### REQUIREMENT: Immediate seek and rate-change responsiveness
The client algorithm must:
- jump cursor immediately on seek,
- adjust pacing immediately on rate changes,
- clear/rebuild buffers immediately after seek.

### SUGGESTION: Buffer windows around cursor
A workable approach:
- Maintain a buffer around cursor:
  - UI parsed lane: `cursor ± (back/forward seconds)`
  - raw bytes: fetch forward chunks for short horizons.
- Adapt window sizes based on RTT, event density, and render budget.

_(Suggestion only; any client approach is acceptable if it is smooth, deterministic, and does not require server session state.)_

### SUGGESTION: Resumption token / watermark for deterministic pulls
Consider supporting an `afterToken` (or `(lastTruthTime, lastSeq, lastHash)` tuple) so repeated pulls do not duplicate/miss events.

_(Suggestion only; not mandatory so long as determinism and dedupe are achieved.)_

### REQUIREMENT: “Live” can be unified with replay (optional end-state)
We are considering a “single mode” end-state:
- “Live” = cursor follows `dbTruthNow - smallLag`.
- Realtime becomes “near now” and uses the same pull mechanism as replay.

Document this as an **open design option** and ensure the docs do not lock us into a push-only live mode.

---

## 6) Raw TCP loopback replay (total fidelity)

### REQUIREMENT: TCP stream carries raw bytes only
- TCP replay must emit **raw bytes only**.
- Bytes must be served in the **same chunk boundaries** they arrived in.

### REQUIREMENT: Client timeline controls pacing and requested windows
- The client timeline determines which time window is requested and the pacing.
- Server remains stateless with respect to playback pacing.

### REQUIREMENT: Storage model must preserve chunk boundaries
Document how chunk boundaries are stored/indexed:
- whether DB rows represent byte-chunks with `(truthTimeStart, truthTimeEnd, bytes)`,
- how `.bin` storage aligns with DB indexing.

---

## 7) Subject naming / scoping contract (open-to-change, but must be documented)

### REQUIREMENT: Document current subject construction
The documentation must include the **current** subject naming scheme as implemented today.

### REQUIREMENT: Mark subject scheme as open-to-change (*)
Add an asterisk note:
- “This reflects the current implementation / suggested approach and is open to change.”

### REQUIREMENT: Prevent mixing live/replay flows
Even without `sessionId`, the system must prevent mixing replay/live flows in a way that confuses UI state.

We are exploring two directions:
1) **DB-pull unification** (no live vs replay subjects; all consumers pull DB by cursor), or
2) A formal subject namespace split (if we keep pub/sub streaming).

Document both as options if the design is not finalized.

---

## 8) Commands (connectionless) and audit trail

### REQUIREMENT: Connectionless command protocol
- Button press sends a command event to `novaArchive`.
- `novaArchive` routes the command to producers (via `/transport`) and records:
  - request,
  - progress (optional),
  - result/ack.

### REQUIREMENT: Replay command behavior
- During replay, commands are **never executed**.
- UI shows recorded command stream as it happened.

### REQUIREMENT: For now, open permissions (don’t lock into RBAC)
- For now, assume “everyone can see/do everything.”
- Keep `scopeId` in place and avoid architecture decisions that block future admin roles.

### SUGGESTION: Make command events a first-class stream type
Treat command request/progress/result as a stream with deterministic schema and ordering.

---

## 9) Metadata lifecycle protocol (connect/change/reconnect + backstop)

### REQUIREMENT: Time-versioned metadata (upserts only)
- Metadata is stored as time-versioned upserts/changes.
- Metadata drives hierarchy (system/container/asset) and card selection (with NOVA overrides).

### REQUIREMENT: Connect/change/reconnect resend protocol
Document exact expected flows:
- initial connect/announce,
- change events,
- reconnect resend behavior (what must be resent and why).

### REQUIREMENT: On-demand metadata request (backstop)
- Archive must serve metadata instantly from storage on demand.
- Responses must be time-aware (respect requested cursor time).

### REQUIREMENT: Cross-lane ordering favors metadata on collisions
When timestamps collide, metadata ordering wins (metadata is rarer).
Tie-break rules must be deterministic.

---

## 10) Lane policy and replication policy (hard constraints)

### REQUIREMENT: Lane invariants (“must never contain”)
Document for each lane:
- Raw bytes lane:
  - bytes only, never parsed JSON.
- Parsed truth lane:
  - typed JSON, full fidelity, recorded immediately.
- UI snapshot/delta lane:
  - rate-limited snapshots/deltas but **complete** for display; stored for replay.

### REQUIREMENT: Who consumes what
- UI uses UI lane (+ metadata).
- PlotJuggler uses parsed truth lane (and/or DB-backed firehose).
- TCP loopback uses raw bytes lane.

---

## 11) Deterministic ordering algorithm (make it unit-testable)

### REQUIREMENT: Truth time definition
- Truth time = archive **receive-time** (device time is auxiliary).

### REQUIREMENT: Tie-break chain is explicit
Define an ordering key, e.g.:
1) `truthTime`
2) lane priority (metadata favored on collisions)
3) sequence number (if present)
4) stable hash/ingest id

(Exact fields may differ; requirement is determinism and testability.)

---

## 12) Operational “build-complete” items (deployment, retention, backup)

### REQUIREMENT: Deployment topology is explicit
Document:
- Standard node runs: `hardwareService -> GEM -> novaArchive -> novaCore`.
- Ground station assignment at runtime:
  - globally scoped subscription across scopes.

### REQUIREMENT: Retention and storage sizing guidance
At minimum:
- retention policies by lane,
- daily folder growth expectations,
- compaction/archive strategy if any.

### REQUIREMENT: Backup/restore strategy aligns DB + cold files
Document:
- what is backed up (DB + cold files + manifests),
- how consistency is ensured,
- restore verification procedure.

---

## 13) Testing/verification matrix (prove the system works)

### REQUIREMENT: Determinism tests
- Same ingest -> same DB state -> same export outputs.

### REQUIREMENT: Replay correctness tests
- Generate truth files for `(T0,T1)` from DB/driver exports and compare to golden outputs/hashes.

### REQUIREMENT: Safety tests
- Replay never executes commands to hardware.
- Scope leakage prevention (ground sees all; local sees own scope only).

---

## 14) Notes on open items (*)
The following items should be documented as **current approach / open-to-change** where applicable:
- Subject construction strategy (especially if we adopt DB-pull unification).
- Whether UI traffic migrates fully behind `/transport` and what the browser binding looks like.
- Exact resumption token/watermark mechanism for DB pulls.
- Exact chunk storage/indexing mechanism for raw TCP replay (DB vs file index division).

---

## 15) Summary checklist (quick “did we document it?”)

- [ ] Single truth DB in archive; core has no DB.
- [ ] Driver system + cold file layout fully specified.
- [ ] Replication/sync via append-only + hashes + watermarks documented.
- [ ] /transport bindings (IPC vs NATS) documented + UI migration direction.
- [ ] Raw + parsed stream plan documented (and consumer mapping).
- [ ] UI deltas completeness + storage for replay documented.
- [ ] Stateless pull-based replay algorithm documented (seek + rate adapt).
- [ ] Raw TCP replay contract documented (bytes-only, chunk-preserving).
- [ ] Subject naming + scoping documented and marked *open-to-change if needed*.
- [ ] Command flow: connectionless to archive; replay non-executing; audit trail recorded.
- [ ] Metadata lifecycle protocol documented (connect/change/reconnect/backstop).
- [ ] Deterministic ordering algorithm written as rules.
- [ ] Ops: deployment + retention + backup/restore documented.
- [ ] Tests: determinism + replay correctness + safety + scope leakage.

---

_End._
