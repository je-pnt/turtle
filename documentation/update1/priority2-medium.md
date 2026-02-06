# Priority 2 — Medium (Architecture and Code Consolidation)

> **Preface**: This document describes observed problems and required behaviors. It is not prescribing implementation or refactoring steps. Examples and identifiers use camelCase. Priorities are ranked by true, repeatable timeline control and synchronized dataflow first.

---

## P2.1 Non-Unified UI Update Paths and Manifest-Driven Gaps

**Observed Behavior**:
- Too much bespoke UI logic exists in card rendering, causing inconsistent behavior and integration fragility.
- Some cards (e.g., gnssReceiver) rely on frontend parsing/render logic that is not clearly third-party integratable.
- Multiple state update paths exist, causing:
  - Presentation override persistence failures (P1.7)
  - Bind/unbind requiring refresh (P1.5)
  - hardwareService card layout regressions (P1.H2)
- Behavior diverges because there is no single update pathway for UI state changes.
- The manifest system is not fully manifest-driven; frontend code contains entity-specific logic.

**Required Behavior**:
- Cards must be data-driven via manifest-defined fields.
- Frontend should render manifest-specified fields and display values from uiUpdate events.
- Frontend must not contain hardware-specific or entity-specific decoding/render logic; rendering and interpretation must be generic and manifest-declared.
- All UI state changes (data updates, presentation overrides, bind/unbind) must flow through one consistent pathway.
- Third-party integrations must be able to define cards purely through manifests without editing UI code.

**Why Medium Priority**: This is architectural debt causing multiple P1 symptoms. Fixing the root consolidates fixes for P1.5, P1.7, and P1.H2.

**Cross-Reference**: hardwareService card regressions (P1.H1, P1.H2) are validation targets—fixing them confirms the manifest-driven approach works.

---

## P2.2 Presentation Features Inconsistent Across Card Types

**Observed Behavior**:
- User presentation features (color, model, scale, name) exist but behavior is inconsistent across card types.
- Not all card types support all presentation features (e.g., streams may lack edit-name).
- Presentation color does not visually link card to map entity.

**Required Behavior**:
- Presentation features must be consistent across card types that support them.
- The manifest should declare which presentation features apply to a given card type.
- Presentation color should influence card name text color to visually link card to map entity.

---

## P2.3 UI Duplication Causing Divergent Behavior

**Observed Behavior**:
- Card header rendering logic is duplicated across different card types.
- Shield item rendering is duplicated across entities, streams, and replays.
- Duplication causes inconsistent behavior and makes changes error-prone.

**Required Behavior**:
- Common UI patterns must behave consistently regardless of card/shield type.
- Changes to shared patterns must apply uniformly.

**Note**: The specific consolidation approach is an implementation decision, not a requirement.

---

## P2.4 Sidebar Naming Inconsistency

**Observed Behavior**:
- UI refers to "main sidebar" and "detailed sidebar."
- Codebase and API use "shields" and "cards" terminology.

**Required Behavior**:
- UI labels, code comments, and variable names must use consistent terminology.
- "Shields" = left/main list; "Cards" = right/detailed panels.
