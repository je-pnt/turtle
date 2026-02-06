# Phase 3: Consolidation, Manifest-Driven UI, and Polish (P2 + P3)

> **Objective**: Eliminate bespoke UI logic, unify update paths, consolidate duplicate code, and apply visual polish. End with LESS code and MORE consistency.

> **Prerequisite**: Phase 1 + Phase 2 complete.

> **Implementation flexibility**: File references, function names, and refactor sketches are guidance. Any approach that meets Required Behavior + invariants and reduces code is acceptable.

---

## Architecture Invariants (From nova architecture.md)

1. **Manifest/View ownership**: "NOVA-owned UI definitions (cards/shields/commands). Client-specific presentation (names/colors) may override display only."

2. **UiUpdate contract**: "UI meaning is NOVA-owned and manifest-defined. UiUpdate is partial upsert... Keys are manifest-defined."

3. **One way to do everything**: "No one-off integrations, regex catch paths, or alternate codepaths."

4. **Guidelines.md**: "Prefer small, well-named abstractions and inheritance/plugin patterns... Prefer reuse and deletion over new code."

---

## P2.1: Non-Unified UI Update Paths and Manifest-Driven Gaps

### Investigation Required

**Read and understand before implementing:**
- `nova/core/manifests/*.py` — What do manifests define? What fields are declared?
- `nova/ui/js/cards.js` — How does `renderCard()` work? Where is entity-specific logic?
- `nova/ui/js/websocket.js` — How are uiUpdate events routed to cards?
- hardwareService card code — Where is gnss-specific parsing/rendering?

### Root Cause Analysis

Multiple problems stem from non-unified update paths:
1. Frontend contains entity-specific rendering logic (not manifest-driven)
2. Multiple state update mechanisms (some reactive, some not)
3. hardwareService cards have bespoke JS logic instead of manifest-defined fields

### Required Behavior

- Cards render ONLY manifest-declared fields
- Frontend contains NO hardware-specific or entity-specific logic; only **generic, manifest-declared rendering + generic transforms** (unit conversion, table formatting, list sorting, map bindings — all driven by manifest metadata, not hardcoded entity types)
- All UI state changes flow through ONE update pathway
- Third parties define cards via manifest only (no JS edits)

> **Clarification**: The rule is not "no parsing in frontend" — that's unworkable. The rule is "no *hardware/entity-specific* logic." Generic transforms (e.g., `if (field.format === 'degrees')`) are fine; type-specific logic (e.g., `if (entityType === 'gnss')`) is forbidden.

### Approach

1. **Audit manifests**: List all fields declared in each manifest.
2. **Audit card rendering**: Find all hardcoded/entity-specific rendering logic.
3. **Map the gap**: What fields are rendered that aren't in manifests? What manifest fields aren't rendered?
4. **Expand manifests**: Add missing field declarations.
5. **Genericize rendering**: Replace entity-specific code with manifest-driven loops.
6. **Delete bespoke logic**: Remove all `if (entityType === 'gnss')` style code.

### Validation Targets (P1.H1, P1.H2)

- svInfo/azEl display: Must work via manifest-defined fields, not custom parsing
- Card layout stability: Layout from manifest, not dynamic JS transformation

---

## P2.2: Presentation Features Inconsistent Across Card Types

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/cards.js` — Which card types support which presentation features?
- `nova/ui/js/presentation.js` — What does MapPresEditor handle?
- Manifests — Is there a `presentationType` or similar field?

### Root Cause Analysis

- Presentation button hardcoded for entity cards only
- Stream cards lack edit-name
- No manifest-level declaration of "this card type supports map presentation"

### Required Behavior

- Manifest declares `presentationType` (`'none'`, `'map'`, future: `'diagram'`)
- Presentation button shown only when `presentationType !== 'none'`
- All card types with headers support edit-name
- Presentation color influences card name text color (visual link to map)

### Approach

1. **Add presentationType to manifests**: Default `'none'` for runs, `'map'` for entities
2. **Conditional presentation button**: Check `manifest.presentationType` in render
3. **Shared header**: All card types use same header with edit-name (see P2.3)
4. **Color application**: When presentation color set, apply to card name text

---

## P2.3: UI Duplication Causing Divergent Behavior

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/js/cards.js` — `renderCard()`, `renderRunCard()`, `renderTcpStreamCard()` — how similar are they?
- `nova/ui/js/entities.js`, `streams.js`, `replays.js` — How do they render shield items?

### Root Cause Analysis

- Card headers duplicated across card types → inconsistent features (stream lacks edit-name)
- Shield items duplicated across entity types → inconsistent styling/behavior

### Required Behavior

- Common UI patterns behave identically regardless of card/shield type
- One change to header/shield logic applies everywhere

### Approach

**Extract shared functions (but do NOT invent new APIs — refactor existing):**

1. **Shared card header**:
   - Find common header elements: drag handle, title, edit-name, collapse, close, presentation button
   - Extract into shared function that all card renderers call
   - Parameters: entity, entityKey, options (presentationType, etc.)

2. **Shared shield item**:
   - Find common shield elements: icon, name, status indicator, click handler
   - Extract into shared function used by entities.js, streams.js, replays.js
   - Parameters: key, icon, name, statusIndicator, onClick

3. **Delete duplicate code**: After extraction, delete the inlined versions

### Code Reduction Target

Should significantly reduce lines in cards.js, entities.js, streams.js, replays.js.

---

## P2.4: Sidebar Naming Inconsistency

### Required Behavior

- UI labels match code terminology: "Shields" (left), "Cards" (right)

### Approach

1. Find all user-visible labels for sidebars
2. Rename to "Shields" and "Cards"
3. Update any code comments that use old terminology

---

## P3.1: Live Button Icon

### Required Behavior

- Icon clearly indicates "realtime" (clock+checkmark, broadcast symbol, etc.)

### Approach

- Replace current icon with clearer alternative
- Keep same button behavior

---

## P3.2: Sidebar and Timeline Layout

### Investigation Required

**Read and understand before implementing:**
- `nova/ui/css/styles.css` — Sidebar and timeline positioning
- `nova/ui/html/*.html` — Toggle button placement

### Required Behavior

- Sidebars extend to meet timeline bar (no gap)
- Toggle buttons don't obscure content (move to header edges)

### Approach

1. Adjust sidebar CSS height to account for timeline
2. Move toggle buttons to header left/right edges
3. Test at various window sizes

---

## P3.3: Timeline Visual Polish

### Required Behavior

- Timeline thinner and longer
- Mode indicators (LIVE/REPLAY/PAUSED) prominent with distinct colors
- Timebase selector removed from UI (comes from config)

### Approach

1. Reduce timeline height in CSS
2. Expand timeline width (or make sidebars narrower)
3. Style mode indicators: LIVE=green, REPLAY=blue, PAUSED=yellow/orange
4. Remove timebase dropdown from HTML (already using config value)

---

## P3.4: CSS Bloat and Lack of Inheritance

### Investigation Required

**Read and understand before implementing:**
- All CSS files: auth.css, chat.css, map.css, presentation.css, styles.css
- Find duplicate rules and similar patterns

### Root Cause Analysis

- No shared CSS primitives
- Same button/panel/header styles repeated
- No CSS custom property strategy

### Required Behavior

- Shared primitives for panels, headers, buttons, toggles
- Significantly fewer total lines of CSS
- Consistent look matching "failedNova" aesthetic (colors/style only, not structure)

### Approach

1. **Audit for duplicates**: List all repeated patterns across CSS files
2. **Define primitives**: `.nova-panel`, `.nova-panel-header`, `.nova-button`, `.nova-toggle`, `.nova-resizable`
3. **Apply primitives**: Replace inline styles with class references
4. **Delete duplicates**: Remove now-unused CSS rules
5. **Color refresh**: Update custom properties to match failedNova palette

### Code Reduction Target

Aim for 30-50% reduction in total CSS lines.

---

## Phase 3 Deliverables

1. **Manifest-driven cards**: No entity-specific frontend logic
2. **Unified update path**: All state changes through one mechanism
3. **Shared UI components**: Card header, shield item extracted and reused
4. **Consistent presentation**: All supporting cards have edit-name, color link to map
5. **Terminology alignment**: Shields/Cards naming throughout
6. **Visual polish**: Live icon, layout fixes, timeline slimmed, mode colors
7. **CSS consolidation**: Shared primitives, reduced duplication

## Phase 3 Code Reduction Targets

| Area | Target |
|------|--------|
| cards.js | -30% (shared header, remove entity-specific logic) |
| entities.js + streams.js + replays.js | -40% (shared shield item) |
| CSS total | -30-50% (primitives, dedupe) |
| Overall | Fewer files, fewer lines, more consistency |

## Files Likely Modified

| File | Expected Changes |
|------|------------------|
| `nova/core/manifests/*.py` | Add presentationType, ensure all fields declared |
| `nova/ui/js/cards.js` | Extract shared header, genericize rendering |
| `nova/ui/js/entities.js` | Use shared shield item |
| `nova/ui/js/streams.js` | Use shared shield item |
| `nova/ui/js/replays.js` | Use shared shield item |
| `nova/ui/css/styles.css` | Shared primitives, layout fixes |
| `nova/ui/css/*.css` | Consolidate into styles.css where possible |
| `nova/ui/html/*.html` | Remove timebase selector, fix button placement |

## Validation

- [ ] gnssReceiver card displays correctly via manifest only (no custom JS)
- [ ] Card layout stable under live data (P1.H2 resolved)
- [ ] svInfo/azEl displays correctly (P1.H1 resolved)
- [ ] Stream cards have edit-name button
- [ ] Presentation color shows on card name text
- [ ] CSS total lines reduced by target amount
- [ ] All sidebars labeled "Shields" / "Cards"
- [ ] Live button icon clearly indicates realtime
- [ ] No visual gaps between sidebars and timeline

---

## Final Cleanup Pass

After all three phases:

1. **Run linter/formatter** on all modified JS/CSS/Python
2. **Search for dead code**: Functions no longer called, CSS classes no longer used
3. **Search for TODO/FIXME**: Resolve or document
4. **Update comments**: Remove outdated comments, add where clarity needed
5. **Verify no duplicate implementations** remain

## Success Metrics (Overall)

| Metric | Before | After (Target) |
|--------|--------|----------------|
| Total JS lines | TBD | -20% |
| Total CSS lines | TBD | -35% |
| Entity-specific UI code | Multiple places | Zero |
| Online/offline implementations | 2+ | 1 |
| Presentation save paths | 2+ | 1 |
| Card header implementations | 3+ | 1 |
| Shield item implementations | 3 | 1 |
