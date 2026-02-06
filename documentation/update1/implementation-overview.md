# Update 1: Implementation Plan Overview

> **Goal**: Fix all identified problems while reducing code, increasing abstraction, and following guidelines.md strictly.

---

## Guiding Principles (From guidelines.md)

1. **Fix root causes, not symptoms** — No defensive code, no try/catch workarounds
2. **No parallel code paths** — Refactor old path to call new, or delete old path
3. **Prefer reuse and deletion over new code** — Identify what exists, generalize it, remove duplicates
4. **Explicit, minimal, consistent** — Small abstractions, inheritance patterns, camelCase

## Architecture Compliance (From nova architecture.md)

Before every change:
1. Restate relevant invariants/contracts
2. Confirm work matches end-goal architecture
3. If mismatch, stop and explain

---

## Phase Summary

| Phase | Focus | Problems Addressed | Expected Outcome |
|-------|-------|-------------------|------------------|
| **Phase 1** | Timeline Control Foundation | P0.1, P0.2, P0.3 | Streams stay synced, controls never stuck, seek works |
| **Phase 2** | Lifecycle, State, Persistence | P1.1-P1.10 | Unified activity tracking, visual timeline, persistence fixed |
| **Phase 3** | Consolidation and Polish | P2.1-P2.4, P3.1-P3.4 | Manifest-driven UI, shared components, CSS cleanup |

---

## Phase 1: Timeline Control Foundation

**File**: [phase1-timeline-foundation.md](phase1-timeline-foundation.md)

**Problems**:
- P0.1: Stream synchronization drift
- P0.2: Playback controls stuck during unbound replay
- P0.3: Seek/jump capability incomplete

**Key Investigation Areas**:
- `nova/core/streaming.py` — StreamCursor, OutputStreamCursor, bound stream relationship
- `nova/ui/js/timeline.js` — State machine, seek functions, setTimeout delays
- `timeErrorLog.txt` — Evidence of failure sequences

**Code Reduction Targets**:
- Remove redundant cursor logic in bound streams
- Remove defensive setTimeout delays
- Consolidate stream start paths

---

## Phase 2: Lifecycle, State, Persistence

**File**: [phase2-lifecycle-persistence.md](phase2-lifecycle-persistence.md)

**Problems**:
- P1.1: Unified online/offline + cleanup (entities)
- P1.2: Streams activity tracking (like entities)
- P1.3: Entity list windowed to recent activity
- P1.4: Timeline visual advancement without data
- P1.5: Online/offline incorrect in replay
- P1.6: Bind/unbind requires refresh
- P1.7: Chat doesn't track cursor in replay
- P1.8: Presentation override persistence failure
- P1.9: Scope UI/approval flow
- P1.10: Admin user management
- P1.H1-H3: hardwareService issues (document, defer fix to Phase 3)

**Key Investigation Areas**:
- `nova/ui/js/entities.js` — Online/offline current implementation
- `nova/ui/js/streams.js` — Compare to entities implementation
- `nova/ui/js/cards.js` — Presentation save paths
- `nova/ui/js/presentation.js` — Working save path

**Code Reduction Targets**:
- Delete separate online/offline implementations (unify)
- Delete hardcoded timeout values
- Delete non-working presentation save path

---

## Phase 3: Consolidation and Polish

**File**: [phase3-consolidation-polish.md](phase3-consolidation-polish.md)

**Problems**:
- P2.1: Non-unified UI update paths, manifest gaps
- P2.2: Presentation features inconsistent
- P2.3: UI duplication causing divergent behavior
- P2.4: Sidebar naming inconsistency
- P3.1: Live button icon unclear
- P3.2: Sidebar and timeline layout
- P3.3: Timeline visual polish
- P3.4: CSS bloat

**Key Investigation Areas**:
- `nova/core/manifests/*.py` — Current manifest field definitions
- `nova/ui/js/cards.js` — Card rendering, header duplication
- `nova/ui/js/entities.js`, `streams.js`, `replays.js` — Shield item duplication
- All CSS files — Duplicate patterns

**Code Reduction Targets**:
- cards.js: -30% (shared header, generic rendering)
- entities/streams/replays.js: -40% (shared shield item)
- CSS total: -30-50% (primitives, deduplication)

---

## Implementation Approach

### For Each Phase

1. **Read the code** — Don't assume; trace actual flows
2. **Understand the problem** — Root cause, not symptoms
3. **Check architecture** — Does fix comply with invariants?
4. **Identify reuse** — What exists that can be generalized?
5. **Implement minimally** — Smallest change that fixes root cause
6. **Delete aggressively** — Remove old/duplicate code immediately
7. **Validate** — Check all listed validation items

### Reading the Code

Every phase includes "Investigation Required" sections. These are NOT optional. Before implementing:

1. Read the listed files
2. Answer the listed questions
3. Update understanding if different from hypothesis
4. Adjust approach based on actual code structure

### No Guessing

If investigation reveals something unexpected:
- Document the finding
- Reassess the approach
- Do NOT add defensive code to "handle both cases"
- Fix the actual root cause

---

## Problem Tracking

| ID | Problem | Phase | Status |
|----|---------|-------|--------|
| P0.1 | Stream sync drift | 1 | Not started |
| P0.2 | Controls stuck in unbound replay | 1 | Not started |
| P0.3 | Seek/jump incomplete | 1 | Not started |
| P1.1 | Entity online/offline + cleanup | 2 | Not started |
| P1.2 | Stream online/offline + cleanup | 2 | Not started |
| P1.3 | Entity list windowed | 2 | Not started |
| P1.4 | Timeline visual advancement | 2 | Not started |
| P1.5 | Online/offline wrong in replay | 2 | Not started |
| P1.6 | Bind/unbind needs refresh | 2 | Not started |
| P1.7 | Chat doesn't track cursor | 2 | Not started |
| P1.8 | Presentation persistence | 2 | Not started |
| P1.9 | Scope UI clarity | 2 | Not started |
| P1.10 | Admin user management | 2 | Not started |
| P1.H1 | svInfo display broken | 3 | Not started |
| P1.H2 | Card layout regression | 3 | Not started |
| P1.H3 | Mosaic ack incorrect | 3 | Not started (hardwareService) |
| P2.1 | Non-unified update paths | 3 | Not started |
| P2.2 | Presentation inconsistent | 3 | Not started |
| P2.3 | UI duplication | 3 | Not started |
| P2.4 | Sidebar naming | 3 | Not started |
| P3.1 | Live button icon | 3 | Not started |
| P3.2 | Layout issues | 3 | Not started |
| P3.3 | Timeline polish | 3 | Not started |
| P3.4 | CSS bloat | 3 | Not started |

---

## Success Criteria

### Phase 1 Complete When:
- [ ] Streams stay synchronized during all playback modes
- [ ] No stuck state after any control sequence
- [ ] Seek works via datetime entry and cursor drag
- [ ] All bound streams restart on seek

### Phase 2 Complete When:
- [ ] Online/offline works correctly in replay (cursor-relative)
- [ ] Entities and streams use same activity algorithm
- [ ] Stale entities cleaned up
- [ ] Timeline advances visually without data
- [ ] All presentation overrides persist

### Phase 3 Complete When:
- [ ] Cards render via manifest only (no entity-specific JS)
- [ ] Shared header/shield components extracted
- [ ] CSS significantly reduced
- [ ] hardwareService cards work correctly
- [ ] UI polish items complete

### Overall Success:
- [ ] Total codebase lines REDUCED
- [ ] No duplicate implementations remain
- [ ] All problems verified fixed
- [ ] Architecture compliance confirmed
