# Priority 3 — Low (UI Polish and Styling)

> **Preface**: This document describes observed problems and required behaviors. It is not prescribing implementation or refactoring steps. Examples and identifiers use camelCase. Priorities are ranked by true, repeatable timeline control and synchronized dataflow first.

---

## P3.1 Live Button Icon Unclear

**Observed Behavior**:
- The live/realtime button icon does not clearly communicate "realtime mode."
- Users may not understand what the button does.

**Required Behavior**:
- Live button iconography must clearly indicate realtime mode (e.g., clock with checkmark, broadcast symbol, or similar universally recognized indicator).

---

## P3.2 Sidebar and Timeline Layout Issues

**Observed Behavior**:
- Sidebars do not extend down to the top of the timeline bar; there is a visual gap.
- Panel toggle buttons hide labels or overlap top card content.
- Layout feels misaligned.

**Required Behavior**:
- Sidebars must extend to meet the timeline bar cleanly.
- Toggle buttons must not obscure content; consider relocating to header edges.
- Layout must feel intentional and aligned.

---

## P3.3 Timeline Visual Polish

**Observed Behavior**:
- Timeline bar is visually heavy.
- Mode indicators (LIVE/REPLAY/PAUSED) are not prominently differentiated.
- Timebase selector is in the timeline UI but should come from config.

**Required Behavior**:
- Timeline should be thinner and longer (more horizontal real estate for scrubbing).
- Mode indicators (LIVE, REPLAY, PAUSED) should be prominently displayed with distinct colors.
- Timebase selection should not be in the timeline UI (it is role-based, set in config).

**Note**: Per-second time entry accuracy is part of P0.3 (seek capability), not polish.

---

## P3.4 CSS Bloat and Lack of Inheritance

**Observed Behavior**:
- CSS is bloated with duplicate styles across multiple files.
- There is insufficient use of shared primitives and inheritance.
- Styling is inconsistent across components.

**Required Behavior**:
- UI styling must use shared primitives and inheritance to reduce duplication.
- Styling should match the failedNova look and feel (colors, modern aesthetic).
- Code structure from failedNova should NOT be copied; only presentation values should be matched.

**Note**: This is a separate track from the manifest-driven cleanup (P2.1). CSS cleanup addresses presentation consistency; P2.1 addresses behavioral consistency.

**Examples of Shared Primitives Needed**:
- Base panel styling
- Panel header patterns
- Toggle button patterns
- Resize behavior patterns
