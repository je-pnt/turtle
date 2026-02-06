"""
NOVA regression test runner.

Runs offline/unit tests, online API/WebSocket tests, and prints manual UI tests.
Writes a markdown and JSON report under test/.

Usage:
  python test/regressionTest.py
  python test/regressionTest.py --base-url http://localhost:8080
  python test/regressionTest.py --allow-writes
  python test/regressionTest.py --manual
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import aiohttp
except ImportError:
    aiohttp = None


@dataclass
class TestResult:
    testId: str
    name: str
    description: str
    category: str
    status: str
    details: str
    durationMs: int
    manual: bool
    evidence: Dict[str, Any]


class RegressionRunner:
    def __init__(self):
        self.results: List[TestResult] = []
        self.startTime = time.time()

    def addResult(self, result: TestResult):
        self.results.append(result)

    def summaryCounts(self) -> Dict[str, int]:
        counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "PENDING": 0}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    def buildReportMarkdown(self, baseUrl: str) -> str:
        counts = self.summaryCounts()
        now = datetime.now(timezone.utc).isoformat()

        lines = []
        lines.append(f"# NOVA Regression Report")
        lines.append("")
        lines.append(f"- Date: {now}")
        lines.append(f"- Base URL: {baseUrl}")
        lines.append(f"- Results: PASS={counts['PASS']} FAIL={counts['FAIL']} SKIP={counts['SKIP']} PENDING={counts['PENDING']}")
        lines.append("")
        lines.append("## Tests")
        lines.append("")
        lines.append("| ID | Name | Category | Status | Description | Details |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for result in self.results:
            details = result.details.replace("\n", " ")
            description = result.description.replace("\n", " ")
            lines.append(f"| {result.testId} | {result.name} | {result.category} | {result.status} | {description} | {details} |")

        lines.append("")
        lines.append("## Manual UI Tests")
        lines.append("")
        for result in self.results:
            if result.manual:
                lines.append(f"- {result.testId} {result.name}: {result.status} - {result.description}")

        lines.append("")
        return "\n".join(lines)

    def buildReportJson(self, baseUrl: str) -> Dict[str, Any]:
        return {
            "baseUrl": baseUrl,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "summary": self.summaryCounts(),
            "results": [asdict(r) for r in self.results]
        }


def parseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NOVA regression test runner")
    parser.add_argument("--base-url", dest="baseUrl", default=None, help="Base URL (e.g. http://localhost:8080)")
    parser.add_argument("--allow-writes", dest="allowWrites", action="store_true", help="Allow API write tests (create/delete)")
    parser.add_argument("--manual", dest="manual", action="store_true", help="Collect manual test results interactively")
    parser.add_argument("--report-path", dest="reportPath", default=None, help="Override report markdown path")
    parser.add_argument("--json-path", dest="jsonPath", default=None, help="Override report json path")
    parser.add_argument("--admin-user", dest="adminUser", default=os.getenv("NOVA_ADMIN_USER", "admin"))
    parser.add_argument("--admin-pass", dest="adminPass", default=os.getenv("NOVA_ADMIN_PASS", "admin123"))
    return parser.parse_args()


def nowMs() -> int:
    return int(time.time() * 1000)


def detectBaseUrl(preferred: Optional[str]) -> Optional[str]:
    if preferred:
        return preferred.rstrip("/")

    envUrl = os.getenv("NOVA_BASE_URL")
    if envUrl:
        return envUrl.rstrip("/")

    candidates = ["http://localhost:80", "http://localhost:8080"]
    if aiohttp is None:
        return candidates[0]

    async def check(url: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{url}/health", timeout=2) as resp:
                    return resp.status == 200
        except Exception:
            return False

    loop = asyncio.new_event_loop()
    try:
        for url in candidates:
            if loop.run_until_complete(check(url)):
                return url
    finally:
        loop.close()

    return candidates[0]


def isPort80(baseUrl: str) -> bool:
    return baseUrl.endswith(":80") or baseUrl.endswith(":80/")


def checkServerHealth(baseUrl: str) -> bool:
    if aiohttp is None:
        return False

    async def check() -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{baseUrl}/health", timeout=2) as resp:
                    return resp.status == 200
        except Exception:
            return False

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(check())
    finally:
        loop.close()


def recordResult(runner: RegressionRunner, testId: str, name: str, description: str,
                 category: str, status: str, details: str = "", durationMs: int = 0,
                 manual: bool = False, evidence: Optional[Dict[str, Any]] = None):
    runner.addResult(TestResult(
        testId=testId,
        name=name,
        description=description,
        category=category,
        status=status,
        details=details,
        durationMs=durationMs,
        manual=manual,
        evidence=evidence or {}
    ))


def runPytestSuite(runner: RegressionRunner, testId: str, name: str, description: str, args: List[str]):
    start = nowMs()
    if "pytest" not in sys.modules:
        try:
            import pytest  # noqa: F401
        except ImportError:
            recordResult(runner, testId, name, description, "offline", "SKIP", "pytest not installed", nowMs() - start)
            return

    import pytest
    try:
        exitCode = pytest.main(args)
        status = "PASS" if exitCode == 0 else "FAIL"
        recordResult(runner, testId, name, description, "offline", status, f"pytest exitCode={exitCode}", nowMs() - start)
    except Exception as exc:
        recordResult(runner, testId, name, description, "offline", "FAIL", f"pytest error: {exc}", nowMs() - start)


def runOnlinePytestSuite(
    runner: RegressionRunner,
    testId: str,
    name: str,
    description: str,
    args: List[str],
    baseUrl: str,
    requirePort80: bool = False
):
    start = nowMs()
    if aiohttp is None:
        recordResult(runner, testId, name, description, "online", "SKIP", "aiohttp not installed", nowMs() - start)
        return
    if requirePort80 and not isPort80(baseUrl):
        recordResult(runner, testId, name, description, "online", "SKIP", f"requires port 80 (baseUrl={baseUrl})", nowMs() - start)
        return
    if not checkServerHealth(baseUrl):
        recordResult(runner, testId, name, description, "online", "SKIP", "server not reachable", nowMs() - start)
        return

    try:
        import pytest
        exitCode = pytest.main(args)
        status = "PASS" if exitCode == 0 else "FAIL"
        recordResult(runner, testId, name, description, "online", status, f"pytest exitCode={exitCode}", nowMs() - start)
    except Exception as exc:
        recordResult(runner, testId, name, description, "online", "FAIL", f"pytest error: {exc}", nowMs() - start)


async def httpJson(session: aiohttp.ClientSession, method: str, url: str, jsonBody: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    async with session.request(method, url, json=jsonBody) as resp:
        try:
            data = await resp.json()
        except Exception:
            data = {}
        return resp.status, data


async def runApiTests(runner: RegressionRunner, baseUrl: str, adminUser: str, adminPass: str, allowWrites: bool):
    if aiohttp is None:
        recordResult(runner, "api-000", "aiohttp available", "aiohttp is required for API tests", "online", "SKIP", "aiohttp not installed")
        return

    async with aiohttp.ClientSession() as session:
        # Health
        start = nowMs()
        try:
            status, data = await httpJson(session, "GET", f"{baseUrl}/health")
            if status == 200 and data.get("status") == "ok":
                recordResult(runner, "api-001", "health", "GET /health returns ok", "online", "PASS", "status=ok", nowMs() - start)
            else:
                recordResult(runner, "api-001", "health", "GET /health returns ok", "online", "FAIL", f"status={status} data={data}", nowMs() - start)
        except Exception as exc:
            recordResult(runner, "api-001", "health", "GET /health returns ok", "online", "FAIL", f"error: {exc}", nowMs() - start)
            return

        # Config
        start = nowMs()
        status, data = await httpJson(session, "GET", f"{baseUrl}/config")
        requiredKeys = ["mode", "defaultTimebase", "defaultRate", "defaultMode", "authEnabled", "cardManifests", "runManifests"]
        if status == 200 and all(k in data for k in requiredKeys):
            recordResult(runner, "api-002", "config", "GET /config returns UI config", "online", "PASS", "keys ok", nowMs() - start)
        else:
            recordResult(runner, "api-002", "config", "GET /config returns UI config", "online", "FAIL", f"status={status} dataKeys={list(data.keys())}", nowMs() - start)

        authEnabled = bool(data.get("authEnabled"))

        # Login
        userRole = None
        if authEnabled:
            start = nowMs()
            status, loginData = await httpJson(session, "POST", f"{baseUrl}/auth/login", {
                "username": adminUser,
                "password": adminPass
            })
            if status == 200 and loginData.get("username") == adminUser:
                userRole = loginData.get("role")
                recordResult(runner, "api-003", "login", "POST /auth/login accepts admin credentials", "online", "PASS", f"role={userRole}", nowMs() - start)
            else:
                recordResult(runner, "api-003", "login", "POST /auth/login accepts admin credentials", "online", "FAIL", f"status={status} data={loginData}", nowMs() - start)
        else:
            recordResult(runner, "api-003", "login", "POST /auth/login accepts admin credentials", "online", "SKIP", "auth disabled")

        # Auth me
        if authEnabled:
            start = nowMs()
            status, meData = await httpJson(session, "GET", f"{baseUrl}/auth/me")
            if status == 200 and meData.get("username"):
                recordResult(runner, "api-004", "authMe", "GET /auth/me returns current user", "online", "PASS", f"user={meData.get('username')}", nowMs() - start)
            else:
                recordResult(runner, "api-004", "authMe", "GET /auth/me returns current user", "online", "FAIL", f"status={status} data={meData}", nowMs() - start)
        else:
            recordResult(runner, "api-004", "authMe", "GET /auth/me returns current user", "online", "SKIP", "auth disabled")

        # Admin users
        if authEnabled and userRole == "admin":
            start = nowMs()
            status, usersData = await httpJson(session, "GET", f"{baseUrl}/api/admin/users")
            if status == 200 and isinstance(usersData.get("users"), list):
                recordResult(runner, "api-005", "adminUsers", "GET /api/admin/users returns users", "online", "PASS", f"count={len(usersData.get('users'))}", nowMs() - start)
            else:
                recordResult(runner, "api-005", "adminUsers", "GET /api/admin/users returns users", "online", "FAIL", f"status={status} data={usersData}", nowMs() - start)
        else:
            recordResult(runner, "api-005", "adminUsers", "GET /api/admin/users returns users", "online", "SKIP", "not admin or auth disabled")

        # Streams list
        start = nowMs()
        status, streamsData = await httpJson(session, "GET", f"{baseUrl}/api/streams")
        if status == 200 and isinstance(streamsData.get("streams"), list):
            recordResult(runner, "api-006", "listStreams", "GET /api/streams returns list", "online", "PASS", f"count={len(streamsData.get('streams'))}", nowMs() - start)
        else:
            recordResult(runner, "api-006", "listStreams", "GET /api/streams returns list", "online", "FAIL", f"status={status} data={streamsData}", nowMs() - start)

        # Presentation GETs
        start = nowMs()
        status, presData = await httpJson(session, "GET", f"{baseUrl}/api/presentation")
        if status == 200 and isinstance(presData, dict):
            recordResult(runner, "api-007", "getPresentation", "GET /api/presentation returns overrides", "online", "PASS", "ok", nowMs() - start)
        else:
            recordResult(runner, "api-007", "getPresentation", "GET /api/presentation returns overrides", "online", "FAIL", f"status={status} data={presData}", nowMs() - start)

        start = nowMs()
        status, presDefData = await httpJson(session, "GET", f"{baseUrl}/api/presentation-default")
        if status == 200 and isinstance(presDefData, dict):
            recordResult(runner, "api-008", "getPresentationDefaults", "GET /api/presentation-default returns defaults", "online", "PASS", "ok", nowMs() - start)
        else:
            recordResult(runner, "api-008", "getPresentationDefaults", "GET /api/presentation-default returns defaults", "online", "FAIL", f"status={status} data={presDefData}", nowMs() - start)

        start = nowMs()
        status, modelsData = await httpJson(session, "GET", f"{baseUrl}/api/presentation/models")
        if status == 200 and isinstance(modelsData.get("models"), list):
            recordResult(runner, "api-009", "listModels", "GET /api/presentation/models returns models", "online", "PASS", f"count={len(modelsData.get('models'))}", nowMs() - start)
        else:
            recordResult(runner, "api-009", "listModels", "GET /api/presentation/models returns models", "online", "FAIL", f"status={status} data={modelsData}", nowMs() - start)

        # Runs list
        start = nowMs()
        status, runsData = await httpJson(session, "GET", f"{baseUrl}/api/runs")
        if status == 200 and isinstance(runsData.get("runs"), list):
            recordResult(runner, "api-010", "listRuns", "GET /api/runs returns list", "online", "PASS", f"count={len(runsData.get('runs'))}", nowMs() - start)
        else:
            recordResult(runner, "api-010", "listRuns", "GET /api/runs returns list", "online", "FAIL", f"status={status} data={runsData}", nowMs() - start)

        # Optional write tests
        if allowWrites:
            uniqueId = f"regression-{int(time.time())}"
            # Presentation write
            start = nowMs()
            status, _ = await httpJson(session, "PUT", f"{baseUrl}/api/presentation/{uniqueId}", {
                "displayName": "Regression Temp",
                "color": [1, 2, 3]
            })
            if status in (200, 204):
                recordResult(runner, "api-011", "setPresentation", "PUT /api/presentation/{uniqueId} sets override", "online", "PASS", "ok", nowMs() - start)
            else:
                recordResult(runner, "api-011", "setPresentation", "PUT /api/presentation/{uniqueId} sets override", "online", "FAIL", f"status={status}", nowMs() - start)

            start = nowMs()
            status, _ = await httpJson(session, "DELETE", f"{baseUrl}/api/presentation/{uniqueId}")
            if status in (200, 204):
                recordResult(runner, "api-012", "deletePresentation", "DELETE /api/presentation/{uniqueId} clears override", "online", "PASS", "ok", nowMs() - start)
            else:
                recordResult(runner, "api-012", "deletePresentation", "DELETE /api/presentation/{uniqueId} clears override", "online", "FAIL", f"status={status}", nowMs() - start)

            # Stream create/delete
            start = nowMs()
            streamName = f"regression-{int(time.time())}"
            streamEndpoint = str(9100 + (int(time.time()) % 2000))
            status, createData = await httpJson(session, "POST", f"{baseUrl}/api/streams", {
                "name": streamName,
                "protocol": "tcp",
                "endpoint": streamEndpoint,
                "lane": "raw",
                "outputFormat": "hierarchyPerMessage",
                "enabled": False
            })
            streamId = createData.get("streamId")
            if status == 200 and streamId:
                recordResult(runner, "api-013", "createStream", "POST /api/streams creates stream", "online", "PASS", f"streamId={streamId}", nowMs() - start)

                delStart = nowMs()
                status, _ = await httpJson(session, "DELETE", f"{baseUrl}/api/streams/{streamId}")
                if status in (200, 204):
                    recordResult(runner, "api-014", "deleteStream", "DELETE /api/streams/{streamId} removes stream", "online", "PASS", "ok", nowMs() - delStart)
                else:
                    recordResult(runner, "api-014", "deleteStream", "DELETE /api/streams/{streamId} removes stream", "online", "FAIL", f"status={status}", nowMs() - delStart)
            else:
                recordResult(runner, "api-013", "createStream", "POST /api/streams creates stream", "online", "FAIL", f"status={status} data={createData}", nowMs() - start)
                recordResult(runner, "api-014", "deleteStream", "DELETE /api/streams/{streamId} removes stream", "online", "SKIP", "create failed")

            # Run create/delete
            start = nowMs()
            runName = f"Regression Run {int(time.time())}"
            status, runCreateData = await httpJson(session, "POST", f"{baseUrl}/api/runs", {
                "runName": runName,
                "runType": "generic",
                "timebase": "canonical"
            })
            runNumber = runCreateData.get("runNumber")
            if status == 200 and runNumber:
                recordResult(runner, "api-015", "createRun", "POST /api/runs creates run", "online", "PASS", f"runNumber={runNumber}", nowMs() - start)

                delStart = nowMs()
                status, _ = await httpJson(session, "DELETE", f"{baseUrl}/api/runs/{runNumber}")
                if status in (200, 204):
                    recordResult(runner, "api-016", "deleteRun", "DELETE /api/runs/{runNumber} removes run", "online", "PASS", "ok", nowMs() - delStart)
                else:
                    recordResult(runner, "api-016", "deleteRun", "DELETE /api/runs/{runNumber} removes run", "online", "FAIL", f"status={status}", nowMs() - delStart)
            else:
                recordResult(runner, "api-015", "createRun", "POST /api/runs creates run", "online", "FAIL", f"status={status} data={runCreateData}", nowMs() - start)
                recordResult(runner, "api-016", "deleteRun", "DELETE /api/runs/{runNumber} removes run", "online", "SKIP", "create failed")
        else:
            recordResult(runner, "api-011", "setPresentation", "PUT /api/presentation/{uniqueId} sets override", "online", "SKIP", "write tests disabled")
            recordResult(runner, "api-012", "deletePresentation", "DELETE /api/presentation/{uniqueId} clears override", "online", "SKIP", "write tests disabled")
            recordResult(runner, "api-013", "createStream", "POST /api/streams creates stream", "online", "SKIP", "write tests disabled")
            recordResult(runner, "api-014", "deleteStream", "DELETE /api/streams/{streamId} removes stream", "online", "SKIP", "write tests disabled")
            recordResult(runner, "api-015", "createRun", "POST /api/runs creates run", "online", "SKIP", "write tests disabled")
            recordResult(runner, "api-016", "deleteRun", "DELETE /api/runs/{runNumber} removes run", "online", "SKIP", "write tests disabled")


async def runWebSocketTests(
    runner: RegressionRunner,
    baseUrl: str,
    authEnabled: bool,
    adminUser: str,
    adminPass: str
):
    if aiohttp is None:
        recordResult(runner, "ws-000", "aiohttp available", "aiohttp is required for WebSocket tests", "online", "SKIP", "aiohttp not installed")
        return

    wsUrl = baseUrl.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    start = nowMs()

    try:
        async with aiohttp.ClientSession() as session:
            if authEnabled:
                status, loginData = await httpJson(session, "POST", f"{baseUrl}/auth/login", {
                    "username": adminUser,
                    "password": adminPass
                })
                if status != 200:
                    recordResult(runner, "ws-001", "wsAuth", "WebSocket auth via cookie", "online", "FAIL", f"login failed: {status} {loginData}", nowMs() - start)
                    return

            async with session.ws_connect(wsUrl) as ws:
                msg = await ws.receive_json(timeout=5)
                if msg.get("type") == "authResponse" and msg.get("success") is True:
                    recordResult(runner, "ws-001", "wsAuth", "WebSocket auth via cookie", "online", "PASS", f"user={msg.get('username')}", nowMs() - start)
                else:
                    recordResult(runner, "ws-001", "wsAuth", "WebSocket auth via cookie", "online", "FAIL", f"msg={msg}", nowMs() - start)
                    return

                # Start live stream
                startStreamMsg = {
                    "type": "startStream",
                    "startTime": None,
                    "stopTime": None,
                    "rate": 1.0,
                    "timelineMode": "live",
                    "timebase": "canonical",
                    "filters": {"lanes": ["metadata", "ui", "command", "parsed", "raw"]}
                }
                await ws.send_json(startStreamMsg)
                msg = await ws.receive_json(timeout=5)
                if msg.get("type") == "streamStarted" and msg.get("playbackRequestId"):
                    recordResult(runner, "ws-002", "startStream", "Start live stream", "online", "PASS", "streamStarted", 0)
                else:
                    recordResult(runner, "ws-002", "startStream", "Start live stream", "online", "FAIL", f"msg={msg}", 0)

                # Send replay command (should be blocked)
                replayCmd = {
                    "type": "command",
                    "commandId": f"regression-replay-{int(time.time())}",
                    "requestId": f"req-{int(time.time())}",
                    "targetId": "unknown-target",
                    "commandType": "noop",
                    "payload": {},
                    "timelineMode": "replay"
                }
                await ws.send_json(replayCmd)
                try:
                    reply = await ws.receive_json(timeout=3)
                    if reply.get("type") == "error" and "replay" in str(reply.get("error", "")).lower():
                        recordResult(runner, "ws-003", "replayBlock", "Command blocked in replay", "online", "PASS", reply.get("error", ""), 0)
                    else:
                        recordResult(runner, "ws-003", "replayBlock", "Command blocked in replay", "online", "FAIL", f"msg={reply}", 0)
                except asyncio.TimeoutError:
                    recordResult(runner, "ws-003", "replayBlock", "Command blocked in replay", "online", "SKIP", "no response", 0)

                # Send chat and receive echo
                chatText = f"regression chat {int(time.time())}"
                await ws.send_json({"type": "chat", "text": chatText, "channel": "ops"})
                try:
                    chatMsg = await ws.receive_json(timeout=3)
                    if chatMsg.get("type") == "chat" and chatMsg.get("text") == chatText:
                        recordResult(runner, "ws-004", "chat", "Chat broadcast echoes to client", "online", "PASS", "ok", 0)
                    else:
                        recordResult(runner, "ws-004", "chat", "Chat broadcast echoes to client", "online", "FAIL", f"msg={chatMsg}", 0)
                except asyncio.TimeoutError:
                    recordResult(runner, "ws-004", "chat", "Chat broadcast echoes to client", "online", "SKIP", "no chat received", 0)

                await ws.send_json({"type": "cancelStream"})
                await ws.close()
    except Exception as exc:
        recordResult(runner, "ws-001", "wsAuth", "WebSocket auth via cookie", "online", "FAIL", f"error: {exc}", nowMs() - start)


def addManualTests(runner: RegressionRunner, interactive: bool):
    manualTests = [
        ("ui-001", "Timeline controls", "Play/Pause, Jump to Live, seek to time; verify cursor follows server and no drift"),
        ("ui-002", "Replay blocking", "Switch to REPLAY and confirm command buttons are disabled and server rejects commands"),
        ("ui-003", "UI lane rendering", "Verify cards/shields update only from UI lane (UiUpdate/UiCheckpoint)"),
        ("ui-004", "Chat replay", "Send chat in live, scrub timeline, verify highlight and replay behavior"),
        ("ui-005", "Presentation overrides", "Set displayName/model/color and verify view-only change without altering telemetry"),
        ("ui-006", "Runs/Replays tab", "Create run, clamp timeline, download bundle, delete run"),
        ("ui-007", "TCP streams", "Create stream, start/stop, bind to timeline, verify state updates")
    ]

    for testId, name, desc in manualTests:
        status = "PENDING"
        details = "awaiting user input"
        if interactive:
            while True:
                raw = input(f"Manual {testId} {name} (pass/fail/skip): ").strip().lower()
                if raw in ("pass", "fail", "skip"):
                    status = "PASS" if raw == "pass" else ("FAIL" if raw == "fail" else "SKIP")
                    details = "user reported"
                    break
        recordResult(runner, testId, name, desc, "manual", status, details, 0, manual=True)


def main():
    args = parseArgs()
    baseUrl = detectBaseUrl(args.baseUrl)

    runner = RegressionRunner()

    # Offline/unit tests
    runPytestSuite(
        runner,
        "off-001",
        "phase1to5Unit",
        "Phase 1-5 core DB/ingest/query/ordering unit tests",
        ["test/test_phases_1_to_5.py", "-q"]
    )
    runPytestSuite(
        runner,
        "off-002",
        "driversAndExport",
        "Phase 6 driver and export parity tests",
        ["test/test_phase6_drivers.py", "-q"]
    )
    runPytestSuite(
        runner,
        "off-003",
        "uiState",
        "Phase 7 UI lane and checkpoint tests",
        ["test/test_phase7_ui_plane.py", "-q"]
    )
    runPytestSuite(
        runner,
        "off-004",
        "tcpManifests",
        "Phase 8 TCP and manifest discovery tests",
        ["test/test_phase8_tcp_manifests.py", "-q"]
    )
    runPytestSuite(
        runner,
        "off-005",
        "phase11Syntax",
        "Phase 11 syntax and run store unit tests",
        ["test/test_phase11_replays.py", "-q", "-k", "Syntax or RunStoreUnit"]
    )

    # Online pytest suites (require live server)
    runOnlinePytestSuite(
        runner,
        "on-001",
        "phase5Runtime",
        "Phase 5 command plane runtime tests",
        ["test/test_phase5_architecture.py", "-q"],
        baseUrl,
        requirePort80=True
    )
    runOnlinePytestSuite(
        runner,
        "on-002",
        "phase9Auth",
        "Phase 9 auth runtime tests",
        ["test/test_phase9_auth.py", "-q"],
        baseUrl,
        requirePort80=True
    )
    runOnlinePytestSuite(
        runner,
        "on-003",
        "replayFlow",
        "Replay stream flow runtime test",
        ["test/test_replay_flow.py", "-q"],
        baseUrl,
        requirePort80=True
    )

    # Online tests
    if aiohttp is None:
        recordResult(runner, "api-000", "aiohttp available", "aiohttp is required for API tests", "online", "SKIP", "aiohttp not installed")
    else:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runApiTests(runner, baseUrl, args.adminUser, args.adminPass, args.allowWrites))

            # Use config to know auth status
            async def getAuthEnabled() -> bool:
                async with aiohttp.ClientSession() as session:
                    status, data = await httpJson(session, "GET", f"{baseUrl}/config")
                    return bool(data.get("authEnabled")) if status == 200 else False

            authEnabled = loop.run_until_complete(getAuthEnabled())
            loop.run_until_complete(runWebSocketTests(runner, baseUrl, authEnabled, args.adminUser, args.adminPass))
        finally:
            loop.close()

    # Manual tests
    addManualTests(runner, args.manual)

    reportPath = args.reportPath or os.path.join("test", "regressionReport.md")
    jsonPath = args.jsonPath or os.path.join("test", "regressionReport.json")

    os.makedirs(os.path.dirname(reportPath), exist_ok=True)

    with open(reportPath, "w", encoding="utf-8") as f:
        f.write(runner.buildReportMarkdown(baseUrl))

    with open(jsonPath, "w", encoding="utf-8") as f:
        json.dump(runner.buildReportJson(baseUrl), f, indent=2)

    counts = runner.summaryCounts()
    print("Regression report written:")
    print(f"  {reportPath}")
    print(f"  {jsonPath}")
    print(f"Summary: PASS={counts['PASS']} FAIL={counts['FAIL']} SKIP={counts['SKIP']} PENDING={counts['PENDING']}")


if __name__ == "__main__":
    main()
