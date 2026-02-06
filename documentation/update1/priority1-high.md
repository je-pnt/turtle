# Priority 1 — High (Core Functionality and Session Behavior)

> **Preface**: This document describes observed problems and required behaviors. It is not prescribing implementation or refactoring steps. Examples and identifiers use camelCase. Priorities are ranked by true, repeatable timeline control and synchronized dataflow first.

---

## P1.1 Unified Online/Offline and Cleanup Algorithm (Entities)

**Observed Behavior**:
- Online/offline indication and entity cleanup use different logic or thresholds.
- There is no clear single algorithm for activity-based state management.
- Cleanup behavior is inconsistent or missing entirely.

**Required Behavior**:
- Online/offline and cleanup are the same underlying client-side activity algorithm with two thresholds:
  - `onlineWindowSeconds`: determines online vs offline indication
  - `cleanupWindowSeconds`: triggers removal of entity from UI
- "Seen" means any incoming data event for that uniqueId. No ambiguity; no alternate definitions.
- No tombstones; online/offline indication is the first line of defense.
- Cleanup removes everything tied to the timed-out uniqueId:
  - Card state
  - Shield entry
  - Associated client-side caches
- In replay, entities/shields are recreated as data appears and metadata is requested.

**Why Important**: Inconsistent activity tracking causes stale UI state and confusion about what is "live."

**Notes for Investigation**:
- Both thresholds must come from a single configuration source.
- entities.js contains onlineTtlMs logic that needs alignment.

---

## P1.2 Streams Require Activity-Based Online/Offline and Cleanup (Like Entities)

**Observed Behavior**:
- Streams can remain present/bound after they stop producing data.
- Stream online/offline is not computed the same way as entities.
- It is unclear when a stream should be considered inactive or removed.

**Required Behavior**:
- Stream online/offline must be computed the same way as entities (relative to cursorTime when not live).
- Streams must be cleaned up client-side when inactive long enough, using the same underlying activity detection mechanism (different timeout thresholds permitted).
- No tombstones: online/offline is the primary mechanism; cleanup is the long-timeout follow-on.
- Stream cleanup is based on stream activity, not derived from entity cleanup.

**Why Important**: Without stream lifecycle management, bound streams can persist in invalid states.

---

## P1.3 Default Entity List is Windowed to Recent Activity

**Observed Behavior**:
- All entities are loaded into the page regardless of when they were last seen.
- Stale entities (inactive for longer than the configured window) appear in the shields list.

**Required Behavior**:
- On page load and metadata query, only load entities seen within the configured activity window.
- The activity window threshold must come from a single configuration source.
- "Seen" means any incoming data event for that uniqueId.

**Why Important**: Loading stale entities clutters the UI and causes confusion about what is active.

**Notes for Investigation**:
- Config value: ui.historyTimeoutSeconds (currently 120).
- Entity loading occurs in the metadata query response handler.

---

## P1.4 Timeline Visual Advancement Without Data

**Observed Behavior**:
- The timeline clock freezes when no data arrives.
- In live mode, it appears as if "time stopped" even though wall-clock time continues.

**Required Behavior**:
- Timeline must visually advance as estimated true time, even when no events arrive.
- When data events arrive, actual data time anchors replace/override estimates.
- The UI must never imply data continuity or fabricate truth when no data exists.
- When paused (rate = 0), the clock must freeze.

**Why Important**: Users expect time-normal behavior; a frozen clock suggests system failure.

**Constraint**: No fake data events or synthetic truth. This is purely UI estimation anchored by real data.

---

## P1.5 Online/Offline Indicator Incorrect in Replay

**Observed Behavior**:
- Online/offline currently compares lastSeen to wall-clock time (Date.now()).
- In replay, entities show offline as soon as the cursor is more than a few seconds behind wall-clock.
- This is incorrect; it should show whether the entity was online at cursor time.

**Required Behavior**:
- Online/offline must represent "was this entity online at cursorTime," not "is it online now."
- Comparison baseline is timeline.currentTimeUs, not wall-clock.

**Applies To**:
- Entity online/offline indicator
- Stream shield online/offline indicator (same semantics)

---

## P1.6 Bind/Unbind Stream Changes Require Refresh

**Observed Behavior**:
- Binding or unbinding a stream to a card requires re-fetching/reloading the card to see the change.
- State does not update through the standard UI update pathway.

**Required Behavior**:
- Bind/unbind must reflect immediately without page refresh or card re-fetch.
- State changes must flow through the same update path as other UI updates.

**Cross-Reference**: This is a symptom of non-unified update paths; see P2.1.

---

## P1.7 Chat Does Not Track Cursor in Replay

**Observed Behavior**:
- The chat UI does not highlight the "current" message while in replay.
- Chat appears disconnected from the timeline cursor.

**Required Behavior**:
- Chat must track the same timeline/cursor concept as the rest of the UI.
- Messages near cursor time should be highlighted or indicated.

---

## P1.8 Presentation Override Persistence Failure

**Observed Behavior**:
- User renamed a card, reloaded page, and the name reverted.
- Presentation overrides (name, color, model, scale) do not consistently persist.

**Required Behavior**:
- All presentation overrides must persist across page reloads.
- All override types must use the same persistence pathway.

**Why P1 (not P0)**: This is a presentation persistence defect, not a truth/timeline issue. User overrides are metadata, not truth data.

**Cross-Reference**: Strong signal of non-unified update paths; see P2.1.

---

## P1.9 Scope UI/Approval Flow Must Be Clear; Filtering Deferred

**Observed Behavior**:
- Two "scope" concepts exist (payload scope and user scopes) and the relationship is confusing in the UI.
- Scope assignment is not part of user acceptance flow.

**Required Behavior (This Round)**:
- Admin approval assigns users to "allScopes" by default.
- User acceptance/approval flow must include scope assignment (even if always "all" for now).
- Scope concept must be clearly labeled in UI.

**Deferred**:
- Per-user scope filtering (users seeing only specific scopes) is not required this round.
- Detailed scope intersection rules are deferred until filtering is implemented.

---

## P1.10 Admin User Management Missing

**Observed Behavior**:
- Admins cannot remove users.
- Admins cannot change or reset user passwords.
- User acceptance flow does not include scope assignment.

**Required Behavior**:
- Admin can remove users.
- Admin can change/reset user passwords.
- User acceptance includes scope assignment.

---

## P1.H — hardwareService Integration Issues

> These issues are tracked separately but at P1 because they affect core card functionality. They should be validated during the P2 manifest-driven cleanup work.

### P1.H1 Satellite Display Regression (svInfo)

**Observed Behavior**:
- ublox card should show azimuth/elevation of satellites but display appears broken.
- Suspected regression in svInfo message handling after client-side parsing changes.

**Required Behavior**:
- Satellite azimuth/elevation must display correctly.
- svInfo should be emitted as a uiUpdate message consistent with manifest-driven approach.

### P1.H2 Card Layout Regression Under Live Updates

**Observed Behavior**:
- The intended ublox card table layout (2x2: time/altitude, lat/lon) collapses into "one row per value" layout when data flows.
- Layout is unstable under live updates.

**Required Behavior**:
- Card layout must remain stable as defined, regardless of data flow.
- Layout should be manifest-defined, not dynamically transformed by frontend logic.

**Cross-Reference**: Validation target for P2.1 manifest-driven cleanup.

### P1.H3 Mosaic Command Acknowledgement Incorrect

**Observed Behavior**:
- hardwareService acknowledges ubx commands even when they fail.
- Example failure response contains "$R? ... Invalid command!" but is still acknowledged as success.

**Required Behavior**:
- "Acknowledged" must reflect actual command success, not just "response received."
- Failure patterns (e.g., "$R?", "Invalid command") must be detected and reported as failures.

**Note**: This is a hardwareService fix, not nova core.
