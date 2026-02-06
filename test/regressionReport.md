# NOVA Regression Report

- Date: 2026-02-06T03:29:48.831035+00:00
- Base URL: http://localhost:80
- Results: PASS=25 FAIL=4 SKIP=6 PENDING=0

## Tests

| ID | Name | Category | Status | Description | Details |
| --- | --- | --- | --- | --- | --- |
| off-001 | phase1to5Unit | offline | PASS | Phase 1-5 core DB/ingest/query/ordering unit tests | pytest exitCode=0 |
| off-002 | driversAndExport | offline | PASS | Phase 6 driver and export parity tests | pytest exitCode=0 |
| off-003 | uiState | offline | FAIL | Phase 7 UI lane and checkpoint tests | pytest exitCode=1 |
| off-004 | tcpManifests | offline | FAIL | Phase 8 TCP and manifest discovery tests | pytest exitCode=1 |
| off-005 | phase11Syntax | offline | PASS | Phase 11 syntax and run store unit tests | pytest exitCode=0 |
| on-001 | phase5Runtime | online | PASS | Phase 5 command plane runtime tests | pytest exitCode=0 |
| on-002 | phase9Auth | online | PASS | Phase 9 auth runtime tests | pytest exitCode=0 |
| on-003 | replayFlow | online | FAIL | Replay stream flow runtime test | pytest exitCode=1 |
| api-001 | health | online | PASS | GET /health returns ok | status=ok |
| api-002 | config | online | PASS | GET /config returns UI config | keys ok |
| api-003 | login | online | PASS | POST /auth/login accepts admin credentials | role=admin |
| api-004 | authMe | online | PASS | GET /auth/me returns current user | user=admin |
| api-005 | adminUsers | online | PASS | GET /api/admin/users returns users | count=8 |
| api-006 | listStreams | online | PASS | GET /api/streams returns list | count=1 |
| api-007 | getPresentation | online | PASS | GET /api/presentation returns overrides | ok |
| api-008 | getPresentationDefaults | online | PASS | GET /api/presentation-default returns defaults | ok |
| api-009 | listModels | online | PASS | GET /api/presentation/models returns models | count=4 |
| api-010 | listRuns | online | PASS | GET /api/runs returns list | count=2 |
| api-011 | setPresentation | online | SKIP | PUT /api/presentation/{uniqueId} sets override | write tests disabled |
| api-012 | deletePresentation | online | SKIP | DELETE /api/presentation/{uniqueId} clears override | write tests disabled |
| api-013 | createStream | online | SKIP | POST /api/streams creates stream | write tests disabled |
| api-014 | deleteStream | online | SKIP | DELETE /api/streams/{streamId} removes stream | write tests disabled |
| api-015 | createRun | online | SKIP | POST /api/runs creates run | write tests disabled |
| api-016 | deleteRun | online | SKIP | DELETE /api/runs/{runNumber} removes run | write tests disabled |
| ws-001 | wsAuth | online | PASS | WebSocket auth via cookie | user=admin |
| ws-002 | startStream | online | PASS | Start live stream | streamStarted |
| ws-003 | replayBlock | online | PASS | Command blocked in replay | Commands not allowed in REPLAY mode |
| ws-004 | chat | online | PASS | Chat broadcast echoes to client | ok |
| ui-001 | Timeline controls | manual | PASS | Play/Pause, Jump to Live, seek to time; verify cursor follows server and no drift | user reported |
| ui-002 | Replay blocking | manual | PASS | Switch to REPLAY and confirm command buttons are disabled and server rejects commands | user reported |
| ui-003 | UI lane rendering | manual | PASS | Verify cards/shields update only from UI lane (UiUpdate/UiCheckpoint) | user reported |
| ui-004 | Chat replay | manual | FAIL | Send chat in live, scrub timeline, verify highlight and replay behavior | user reported |
| ui-005 | Presentation overrides | manual | PASS | Set displayName/model/color and verify view-only change without altering telemetry | user reported |
| ui-006 | Runs/Replays tab | manual | PASS | Create run, clamp timeline, download bundle, delete run | user reported |
| ui-007 | TCP streams | manual | PASS | Create stream, start/stop, bind to timeline, verify state updates | user reported |

## Manual UI Tests

- ui-001 Timeline controls: PASS - Play/Pause, Jump to Live, seek to time; verify cursor follows server and no drift
- ui-002 Replay blocking: PASS - Switch to REPLAY and confirm command buttons are disabled and server rejects commands
- ui-003 UI lane rendering: PASS - Verify cards/shields update only from UI lane (UiUpdate/UiCheckpoint)
- ui-004 Chat replay: FAIL - Send chat in live, scrub timeline, verify highlight and replay behavior
- ui-005 Presentation overrides: PASS - Set displayName/model/color and verify view-only change without altering telemetry
- ui-006 Runs/Replays tab: PASS - Create run, clamp timeline, download bundle, delete run
- ui-007 TCP streams: PASS - Create stream, start/stop, bind to timeline, verify state updates
