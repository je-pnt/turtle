# Priority 0 — Critical (Timeline Integrity and Synchronized Dataflow)

> **Preface**: This document describes observed problems and required behaviors. It is not prescribing implementation or refactoring steps. Examples and identifiers use camelCase. Priorities are ranked by true, repeatable timeline control and synchronized dataflow first.

---

## P0.1 Stream Synchronization Drift

**Observed Behavior**:
- Multiple streams that should share one cursor timebase do not remain aligned.
- During unbound replay (especially negative rates), different streams appear to run at slightly different effective times.
- Users observe different effective cursor times across UI panels (e.g., main stream vs bound TCP stream).

**Required Behavior**:
- All active streams bound to the same session must share one authoritative cursor time.
- Stream times must remain aligned throughout playback, including unbound replay at any rate.
- Drift must not accumulate; streams must stay synchronized.

**Why Critical**: This is the highest priority because it breaks synchronized dataflow repeatability. If streams diverge, the UI cannot show a consistent "truth at time T."

**Notes for Investigation**:
- Examine how each stream cursor relates to the leader cursor in streaming.py.
- Review timeErrorLog.txt for evidence of diverging stream start times during rapid transitions.

---

## P0.2 Playback Controls Stuck During Unbound Replay

**Observed Behavior**:
- During unbound replay (open-ended playback with negative rates like -1), after changing rate and/or jumping to live and returning to unbound replay, the system enters a broken control state.
- Timeline and streams feel disconnected; UI cannot reliably change rate, time, or play/pause.
- Recovery requires jumping to live (or similar reset action).

**Required Behavior**:
- Playback controls must remain responsive through all state transitions.
- Mode/rate changes during unbound replay must not leave the system in an unrecoverable state.
- If a transition fails, the system must either complete it or revert cleanly with user feedback.

**Why Critical**: Non-deterministic control state breaks repeatability and user trust.

**Notes for Investigation**:
- timeErrorLog.txt shows rapid cancel/restart cycles with different playbackRequestIds during the failure window.
- Focus on state transitions when startTime is set but stopTime is null (unbound) with negative rate.

---

## P0.3 Seek/Jump Capability Incomplete

**Observed Behavior**:
- The timeline cursor does not fully behave as an authoritative "where we are" indicator and control.
- Date/time entry exists but does not reliably jump the cursor and restart all streams at the entered time.
- Timeline cursor cannot be dragged to seek; there is no drag-and-release jump behavior.
- Time entry does not accept per-second precision.

**Required Behavior**:
- The system must support jumping to a specific point in time and restarting all streams from that point at the current rate.
- The cursor must always indicate the current timeline position; it cannot be ambiguous or stale.
- Both time entry and cursor drag-release are inputs to the same jump capability.
- Time entry must support per-second accuracy.

**Why Critical**: Without reliable seek, users cannot navigate timeline truth or verify replay correctness.
