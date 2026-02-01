# NOVA 2.3 Architecture Review (Missing/Incorrect + Recommendations)

Source reviewed: nova2.3.txt (from nova2.3.docx)

---

## Missing or Incorrect

### 1) Transport scope is inconsistent with earlier end-state
- Doc says “one transport” with **NATS binding for all traffic**, and “no bespoke HTTP/WebSocket data planes.”
- Target end-state still requires HTTP/WS for browser access (even if behind transport later). This is a mismatch with the current NOVA API contract which exposes HTTP/WS for UI and replay. The doc must explicitly allow HTTP/WS at the edge while keeping internal transport unified.

### 2) Replay model conflicts with “no server-side per-client state” vs “session/timeline control”
- The document correctly forbids server-side client sessions, but later references timeline control and APIs that resemble sessions (e.g., “timeline reads + resume token” could be misread as server-managed state). It must explicitly define **stateless HTTP queries only** (snapshot/deltas/bounds) with client-side cursor, no server session descriptors.

### 3) “One DB per NOVA instance” leaves ground/payload truth ambiguity
- It allows “one truth DB per NOVA node” and “ground is unified via mirroring/sync,” which can reintroduce multiple truth stores. The end-state must be explicit: **novaArchive is the authoritative truth store; other nodes are read-only mirrors or caches** (no competing truth or independent writes).

### 4) Two timebases (sourceTruthTime vs canonicalTruthTime) needs hard guardrails
- It introduces dual timebases but doesn’t define strict rules for **which timebase is used in which API** (UI, export, plot, TCP replay). Without explicit usage constraints, this risks inconsistent ordering and replay mismatches.

### 5) Subject schema is defined conceptually but not concretely
- Lane model specifies required fields but does not lock **exact subject namespaces**, example patterns, and versioning strategy (e.g., `stream.truth.*`, `archive.*`, `command.*`). Lack of explicit subjects invites drift and parallel schemas.

### 6) UI update lane relies on UiCheckpoint policy without specifying storage/query semantics
- UiCheckpoint creation is defined, but **how UiCheckpoint is stored, queried, and merged with UiUpdate** at time T is not formalized. Needs explicit query algorithm and ordering contract at the storage/API level.

### 7) Command replay behavior needs operational enforcement points
- It states commands during replay must not execute, but does not define **where enforcement occurs** (client, producer, archive) nor how it is recorded (e.g., CommandAnnotation). This is a repeat of earlier replay safety drift.

### 8) Driver system is powerful but under-specified for failure semantics
- It defines driver selection and binding, but lacks **failure behavior** (DB write vs file write mismatch, retry policy, verification/hashes). This was a known source of architecture drift.

### 9) “Single unified interface” vs “NOVA-owned UI definitions”
- It states UI definitions are NOVA-owned but does not define **ownership boundaries** for producer-provided UI metadata vs NOVA manifests. Without explicit boundaries, producers may start shipping UI logic again.

### 10) Diagrams are referenced but missing
- The document expects diagrams but they are empty, so implementers lack the required authoritative flow visuals. This creates interpretation drift.

---

## Recommendations (Add/Adjust)

### A) Add explicit “Edge vs Core” transport rule
- **Rule**: Internal system transport must use `/sdk/transport` exclusively. Edge-facing UI uses HTTP/WS via novaCore proxy **only**. No other HTTP/WS paths are allowed.

### B) Define stateless replay API exactly (no sessions)
- Add definitive endpoints and semantics:
  - `GET /api/replay/snapshot?time=T&scope=X`
  - `GET /api/replay/deltas?start=T0&end=T1&scope=X`
  - `GET /api/replay/bounds?scope=X`
- State: **no server session IDs, no playback loops, no server cursor**.

### C) Make truth authority unambiguous
- **Rule**: Only novaArchive writes truth. All other nodes are read-only proxies/caches.
- If replication exists, it is **append-only mirror** of the authoritative archive, never a peer truth source.

### D) Lock subject schema with examples + versioning
- Provide a hard subject table with examples:
  - `stream.raw.{scopeId}.{entityId}`
  - `stream.truth.{streamType}.{scopeId}.{entityId}`
  - `stream.ui.{streamType}.{scopeId}.{entityId}`
  - `archive.{scopeId}.ui.{streamType}`
  - `archive.{scopeId}.firehose.{streamType}`
  - `command.{verb}.{entityId}`
- Add versioning rule for schema changes.

### E) Specify UiCheckpoint query algorithm
- Define exact algorithm for state-at-time(T):
  - Fetch latest UiCheckpoint ≤ T per (assetId, viewId, manifestVersion)
  - Apply ordered UiUpdates (by canonical ordering rules) up to T
- Include SQL examples and ordering tie-breaks.

### F) Enforce replay safety with 3-layer policy
- Add explicit enforcement:
  1. **Client** disables controls in replay.
  2. **Producer** rejects commands with `isReplay=true`.
  3. **Archive** tags replay commands and never dispatches them.

### G) Define driver failure and verification rules
- **Required**: DB write must succeed before any file write.
- File-write failures must not block ingest; they must be queued and retried.
- Add verification policy (hash + count checks per window).

### H) Clarify producer vs NOVA UI ownership
- Producers provide **identity + capabilities only**.
- NOVA owns **all UI definitions** (cards, shields, commands). No UI logic from producers.

### I) Fill missing diagrams
- Add three required diagrams:
  1. End-to-end data flow (live + replay)
  2. Storage + query pipeline (DB truth + drivers)
  3. Command flow (live vs replay, safety blocks)

---

## Outcome

If the above gaps are filled, nova2.3 becomes implementation-ready and avoids the prior drift: no session-based replay, no parallel transport/subjects, no ambiguous truth ownership, and no duplicated command paths.
