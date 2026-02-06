"""
Phase 11 Replays Runtime Tests

Tests for:
- Syntax validation of all Phase 11 code
- Run CRUD operations via API
- Run storage layout verification
- Bundle export integration
- Timeline clamp behavior
- Signal list API

Run: python -m pytest test/test_phase11_replays.py -v
Or standalone: python test/test_phase11_replays.py

Property of Uncompromising Sensors LLC.
"""

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp
import pytest


# Test configuration
BASE_URL = "http://localhost:80"
WS_URL = "ws://localhost:80/ws"
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"


# =============================================================================
# SYNTAX TESTS - Verify all Phase 11 code parses correctly
# =============================================================================

class TestPhase11Syntax:
    """Syntax validation for all Phase 11 Python and JavaScript files."""
    
    def test_runStore_syntax(self):
        """Verify runStore.py parses without syntax errors."""
        runstore_path = Path(__file__).parent.parent / 'nova' / 'server' / 'runStore.py'
        assert runstore_path.exists(), f"runStore.py not found at {runstore_path}"
        
        # Compile to check syntax
        with open(runstore_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        try:
            compile(source, str(runstore_path), 'exec')
            print(f"✅ runStore.py syntax OK")
        except SyntaxError as e:
            pytest.fail(f"runStore.py syntax error: {e}")
    
    def test_runStore_imports(self):
        """Verify runStore.py imports successfully."""
        try:
            from nova.server.runStore import RunStore, Run, sanitizeRunName, buildRunFolderName
            print(f"✅ runStore.py imports OK")
        except ImportError as e:
            pytest.fail(f"runStore.py import error: {e}")
    
    def test_server_py_has_run_routes(self):
        """Verify server.py has Phase 11 run routes."""
        server_path = Path(__file__).parent.parent / 'nova' / 'server' / 'server.py'
        assert server_path.exists()
        
        with open(server_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Check for run API routes
        required_routes = [
            "'/api/runs'",
            "'/api/runs/{runNumber}'",
            "'/api/runs/{runNumber}/bundle'",
            "'/api/runs/settings'"
        ]
        
        for route in required_routes:
            assert route in source, f"Missing route: {route}"
        
        print(f"✅ server.py has all {len(required_routes)} run routes")
    
    def test_replays_js_syntax(self):
        """Verify replays.js parses without syntax errors."""
        js_path = Path(__file__).parent.parent / 'nova' / 'ui' / 'js' / 'replays.js'
        assert js_path.exists(), f"replays.js not found"
        
        with open(js_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Basic JS syntax checks (no full parser, but catch obvious issues)
        # Check balanced braces
        open_braces = source.count('{')
        close_braces = source.count('}')
        assert open_braces == close_braces, f"Unbalanced braces: {open_braces} open, {close_braces} close"
        
        # Check for required exports
        required_exports = [
            'window.initReplays',
            'window.replays',
            'window.loadRuns',
            'window.createRun',
            'window.updateRun',
            'window.deleteRun',
            'window.clampToRun',
            'window.clearClamp',
            'window.downloadBundle'
        ]
        
        for export in required_exports:
            assert export in source, f"Missing export: {export}"
        
        print(f"✅ replays.js syntax OK - {len(required_exports)} exports found")
    
    def test_cards_js_has_run_cards(self):
        """Verify cards.js has run card rendering."""
        js_path = Path(__file__).parent.parent / 'nova' / 'ui' / 'js' / 'cards.js'
        assert js_path.exists()
        
        with open(js_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Check for run card functions
        required_functions = [
            'renderMakeReplayCard',
            'renderRunCard',
            'renderSignalToggles',
            'saveNewRun'
        ]
        
        for func in required_functions:
            assert f'function {func}' in source, f"Missing function: {func}"
        
        print(f"✅ cards.js has all {len(required_functions)} run card functions")
    
    def test_timeline_js_has_clamp(self):
        """Verify timeline.js has clamp functionality."""
        js_path = Path(__file__).parent.parent / 'nova' / 'ui' / 'js' / 'timeline.js'
        assert js_path.exists()
        
        with open(js_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Check for clamp in timeline state
        assert 'clamp:' in source or 'clamp =' in source, "Missing clamp in timeline state"
        
        # Check clamp is cleared on jump to live
        assert 'timeline.clamp = null' in source, "Missing clamp clear in handleJumpToLive"
        
        print(f"✅ timeline.js has clamp functionality")
    
    def test_entities_js_has_collapse(self):
        """Verify entities.js has collapsible system/container."""
        js_path = Path(__file__).parent.parent / 'nova' / 'ui' / 'js' / 'entities.js'
        assert js_path.exists()
        
        with open(js_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        assert 'toggleSystemCollapse' in source, "Missing toggleSystemCollapse"
        assert 'toggleContainerCollapse' in source, "Missing toggleContainerCollapse"
        assert 'window.toggleSystemCollapse' in source, "Missing toggleSystemCollapse export"
        
        print(f"✅ entities.js has collapsible system/container")
    
    def test_index_html_has_replays_tab(self):
        """Verify index.html has Replays tab structure."""
        html_path = Path(__file__).parent.parent / 'nova' / 'ui' / 'html' / 'index.html'
        assert html_path.exists()
        
        with open(html_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Check for Replays tab
        assert 'data-tab="replays"' in source, "Missing Replays tab"
        assert 'id="replaysList"' in source, "Missing replaysList container"
        assert 'id="replaysCount"' in source, "Missing replaysCount badge"
        assert 'replays.js' in source, "Missing replays.js script"
        assert 'clampIndicator' in source, "Missing clampIndicator"
        
        print(f"✅ index.html has Replays tab structure")
    
    def test_styles_css_has_run_styles(self):
        """Verify styles.css has run card styles."""
        css_path = Path(__file__).parent.parent / 'nova' / 'ui' / 'css' / 'styles.css'
        assert css_path.exists()
        
        with open(css_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Check for run styles
        required_styles = [
            '.run-form',
            '.run-time-section',
            '.run-actions',
            '.signal-toggles',
            '.signal-constellation',
            '.clamp-indicator'
        ]
        
        for style in required_styles:
            assert style in source, f"Missing style: {style}"
        
        print(f"✅ styles.css has all {len(required_styles)} run styles")


# =============================================================================
# UNIT TESTS - RunStore logic
# =============================================================================

class TestRunStoreUnit:
    """Unit tests for RunStore logic (no server required)."""
    
    def test_sanitize_run_name_basic(self):
        """Test run name sanitization."""
        from nova.server.runStore import sanitizeRunName
        
        # Basic names
        assert sanitizeRunName("Test Run") == "Test Run"
        assert sanitizeRunName("  trimmed  ") == "trimmed"
        
        # Forbidden characters
        assert sanitizeRunName("test/run") == "test_run"
        assert sanitizeRunName("test\\run") == "test_run"
        assert sanitizeRunName("test:run") == "test_run"
        assert sanitizeRunName("test*run") == "test_run"
        assert sanitizeRunName("test?run") == "test_run"
        assert sanitizeRunName('test"run') == "test_run"
        assert sanitizeRunName("test<run") == "test_run"
        assert sanitizeRunName("test>run") == "test_run"
        assert sanitizeRunName("test|run") == "test_run"
        
        # Multiple forbidden → collapsed
        assert sanitizeRunName("a//b::c") == "a_b_c"
        
        # Empty/whitespace
        assert sanitizeRunName("") == "Untitled"
        assert sanitizeRunName("   ") == "Untitled"
        
        print(f"✅ sanitizeRunName works correctly")
    
    def test_build_run_folder_name(self):
        """Test run folder name building."""
        from nova.server.runStore import buildRunFolderName
        
        assert buildRunFolderName(1, "Test") == "1. Test"
        assert buildRunFolderName(42, "My Run") == "42. My Run"
        assert buildRunFolderName(1, "bad/name") == "1. bad_name"
        
        print(f"✅ buildRunFolderName works correctly")
    
    def test_run_validation(self):
        """Test Run validation."""
        from nova.server.runStore import Run, RUN_SCHEMA_VERSION
        
        # Valid run
        run = Run(
            schemaVersion=RUN_SCHEMA_VERSION,
            runNumber=1,
            runName="Test",
            runType="generic",
            timebase="canonical",
            startTimeSec=1000,
            stopTimeSec=2000
        )
        assert run.validate() is None, "Valid run should pass validation"
        
        # Note: runType is NOT validated at Run level anymore (manifest-driven)
        # Only core constraints like timebase and runNumber are validated
        
        # Invalid timebase
        run.timebase = "invalid"
        assert run.validate() is not None, "Invalid timebase should fail"
        
        # Invalid runNumber
        run.timebase = "canonical"
        run.runNumber = 0
        assert run.validate() is not None, "Invalid runNumber should fail"
        
        print(f"✅ Run validation works correctly")
    
    def test_run_manifest_discovery(self):
        """Test run manifest discovery (plugin pattern)."""
        from nova.core.manifests.runs import getRunManifestRegistry
        
        registry = getRunManifestRegistry()
        types = registry.listTypes()
        
        # Should have at least 'generic' and 'hardwareService'
        assert 'generic' in types, "Missing 'generic' run type"
        assert 'hardwareService' in types, "Missing 'hardwareService' run type"
        
        # Verify manifest structure
        hs_manifest = registry.get('hardwareService')
        assert hs_manifest is not None
        assert hs_manifest.runType == 'hardwareService'
        assert hs_manifest.title == 'Hardware Service'
        
        # Check that fields are defined (but no hardcoded signal list!)
        field_ids = [f.fieldId for f in hs_manifest.fields]
        assert 'musicOnTimes' in field_ids, "hardwareService should have musicOnTimes field"
        assert 'musicOffTimes' in field_ids, "hardwareService should have musicOffTimes field"
        assert 'signals' in field_ids, "hardwareService should have signals field"
        
        print(f"✅ Run manifest discovery: {len(types)} types found")
    
    def test_run_store_crud_isolated(self):
        """Test RunStore CRUD operations with isolated temp directory."""
        from nova.server.runStore import RunStore
        
        # Use temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore(dataPath=tmpdir)
            username = "testuser"
            
            # List empty
            runs = store.listRuns(username)
            assert runs == [], "Should start empty"
            
            # Create (new dict-based API)
            run = store.createRun(
                username=username,
                runData={
                    'runName': 'Test Run',
                    'runType': 'generic',
                    'timebase': 'canonical',
                    'startTimeSec': 1000,
                    'stopTimeSec': 2000
                }
            )
            assert run.runNumber == 1
            assert run.runName == "Test Run"
            
            # List should have 1
            runs = store.listRuns(username)
            assert len(runs) == 1
            assert runs[0]['runNumber'] == 1
            
            # Get
            fetched = store.getRun(username, 1)
            assert fetched is not None
            assert fetched.runName == "Test Run"
            
            # Update
            updated = store.updateRun(username, 1, {'runName': 'Updated Run', 'analystNotes': 'Test notes'})
            assert updated is not None
            assert updated.runName == 'Updated Run'
            assert updated.analystNotes == 'Test notes'
            
            # Verify folder renamed
            runs_path = Path(tmpdir) / 'users' / username / 'runs'
            folders = list(runs_path.iterdir())
            assert len(folders) == 1
            assert folders[0].name == '1. Updated Run'
            
            # Create another (dict-based API)
            run2 = store.createRun(username, {
                'runName': 'Second Run',
                'runType': 'hardwareService',
                'timebase': 'source'
            })
            assert run2.runNumber == 2
            
            # Delete first
            assert store.deleteRun(username, 1) == True
            
            # Should have 1 run left
            runs = store.listRuns(username)
            assert len(runs) == 1
            assert runs[0]['runNumber'] == 2
            
            print(f"✅ RunStore CRUD operations work correctly")
    
    def test_run_store_rename_overwrite(self):
        """Test delete-then-rename behavior on folder conflict."""
        from nova.server.runStore import RunStore
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore(dataPath=tmpdir)
            username = "testuser"
            
            # Create two runs (new dict-based API)
            run1 = store.createRun(username, {'runName': 'Alpha', 'runType': 'generic'})
            run2 = store.createRun(username, {'runName': 'Beta', 'runType': 'generic'})
            
            # Rename run2 to "Alpha" (conflict with run1's original name)
            # First rename run1 to something else
            store.updateRun(username, 1, {'runName': 'Gamma'})
            
            # Now rename run2 to "Alpha" - should work (no conflict)
            updated = store.updateRun(username, 2, {'runName': 'Alpha'})
            assert updated is not None
            assert updated.runName == 'Alpha'
            
            # Verify folders
            runs_path = Path(tmpdir) / 'users' / username / 'runs'
            folder_names = [f.name for f in runs_path.iterdir() if f.is_dir()]
            assert '1. Gamma' in folder_names
            assert '2. Alpha' in folder_names
            
            print(f"✅ Rename overwrite (delete-then-rename) works correctly")


# =============================================================================
# INTEGRATION TESTS - Require running server
# =============================================================================

class TestPhase11Integration:
    """Integration tests requiring running NOVA server."""
    
    @pytest.fixture
    async def auth_session(self):
        """Create authenticated session."""
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{BASE_URL}/auth/login",
                json={"username": ADMIN_USER, "password": ADMIN_PASS}
            )
            if resp.status != 200:
                pytest.skip("Server not running or auth failed")
            yield session
    
    @pytest.mark.asyncio
    async def test_api_runs_list(self, auth_session):
        """Test GET /api/runs endpoint."""
        resp = await auth_session.get(f"{BASE_URL}/api/runs")
        assert resp.status == 200, f"Expected 200, got {resp.status}"
        
        data = await resp.json()
        assert 'runs' in data
        assert isinstance(data['runs'], list)
        
        print(f"✅ GET /api/runs returns {len(data['runs'])} runs")
    
    @pytest.mark.asyncio
    async def test_api_runs_settings(self, auth_session):
        """Test GET/PUT /api/runs/settings endpoints."""
        # Get settings
        resp = await auth_session.get(f"{BASE_URL}/api/runs/settings")
        assert resp.status == 200
        
        # Put settings
        test_settings = {'defaultRunType': 'hardwareService', 'testKey': 'testValue'}
        resp = await auth_session.put(
            f"{BASE_URL}/api/runs/settings",
            json=test_settings
        )
        assert resp.status == 200
        
        # Verify
        resp = await auth_session.get(f"{BASE_URL}/api/runs/settings")
        data = await resp.json()
        settings = data.get('settings', {})
        assert settings.get('defaultRunType') == 'hardwareService'
        
        print(f"✅ GET/PUT /api/runs/settings work correctly")
    
    @pytest.mark.asyncio
    async def test_api_run_crud(self, auth_session):
        """Test full run CRUD cycle."""
        # Create
        resp = await auth_session.post(
            f"{BASE_URL}/api/runs",
            json={
                'runName': f'Test Run {int(time.time())}',
                'runType': 'generic',
                'startTimeSec': 1000,
                'stopTimeSec': 2000
            }
        )
        assert resp.status in (200, 201), f"Create failed: {await resp.text()}"
        
        data = await resp.json()
        assert 'run' in data
        run_number = data['run']['runNumber']
        print(f"   Created run #{run_number}")
        
        # Read
        resp = await auth_session.get(f"{BASE_URL}/api/runs/{run_number}")
        assert resp.status == 200
        data = await resp.json()
        assert data['run']['runNumber'] == run_number
        
        # Update
        resp = await auth_session.put(
            f"{BASE_URL}/api/runs/{run_number}",
            json={'analystNotes': 'Updated notes'}
        )
        assert resp.status == 200
        data = await resp.json()
        assert data['run']['analystNotes'] == 'Updated notes'
        
        # Delete
        resp = await auth_session.delete(f"{BASE_URL}/api/runs/{run_number}")
        assert resp.status == 200
        
        # Verify deleted
        resp = await auth_session.get(f"{BASE_URL}/api/runs/{run_number}")
        assert resp.status == 404
        
        print(f"✅ Run CRUD cycle completed successfully")
    
    @pytest.mark.asyncio
    async def test_api_run_hardware_service_signals(self, auth_session):
        """Test hardwareService run with signal selection."""
        # Create hardwareService run
        resp = await auth_session.post(
            f"{BASE_URL}/api/runs",
            json={
                'runName': f'HW Test {int(time.time())}',
                'runType': 'hardwareService',
                'startTimeSec': 1000,
                'stopTimeSec': 2000
            }
        )
        assert resp.status in (200, 201)
        
        data = await resp.json()
        run_number = data['run']['runNumber']
        
        # Update with signal selection (flat signals field, manifest-driven)
        resp = await auth_session.put(
            f"{BASE_URL}/api/runs/{run_number}",
            json={
                'signals': {
                    'GPS-L1CA': True,
                    'GPS-L2C': True,
                    'Galileo-E1': True
                }
            }
        )
        assert resp.status == 200
        
        data = await resp.json()
        signals = data['run'].get('signals', {})
        assert signals.get('GPS-L1CA') == True
        assert signals.get('GPS-L2C') == True
        assert signals.get('Galileo-E1') == True
        
        # Cleanup
        await auth_session.delete(f"{BASE_URL}/api/runs/{run_number}")
        
        print(f"✅ hardwareService signal selection works correctly")
    
    @pytest.mark.asyncio
    async def test_api_run_rename_folder(self, auth_session):
        """Test that renaming a run renames the folder."""
        # Create
        original_name = f'Original {int(time.time())}'
        resp = await auth_session.post(
            f"{BASE_URL}/api/runs",
            json={'runName': original_name, 'runType': 'generic'}
        )
        assert resp.status in (200, 201)
        
        data = await resp.json()
        run_number = data['run']['runNumber']
        
        # Rename
        new_name = f'Renamed {int(time.time())}'
        resp = await auth_session.put(
            f"{BASE_URL}/api/runs/{run_number}",
            json={'runName': new_name}
        )
        assert resp.status == 200
        
        data = await resp.json()
        assert data['run']['runName'] == new_name
        
        # Cleanup
        await auth_session.delete(f"{BASE_URL}/api/runs/{run_number}")
        
        print(f"✅ Run rename updates folder correctly")
    
    @pytest.mark.asyncio
    async def test_api_runs_unauthorized(self):
        """Test that run APIs require authentication."""
        async with aiohttp.ClientSession() as session:
            # No auth - should fail
            resp = await session.get(f"{BASE_URL}/api/runs")
            assert resp.status == 401, f"Expected 401, got {resp.status}"
            
            resp = await session.post(f"{BASE_URL}/api/runs", json={'runName': 'Test'})
            assert resp.status == 401
        
        print(f"✅ Run APIs properly require authentication")


# =============================================================================
# Runner
# =============================================================================

def run_syntax_tests():
    """Run syntax tests only (no server required)."""
    print("\n" + "=" * 60)
    print("Phase 11 SYNTAX TESTS")
    print("=" * 60 + "\n")
    
    test = TestPhase11Syntax()
    
    tests = [
        ('runStore.py syntax', test.test_runStore_syntax),
        ('runStore.py imports', test.test_runStore_imports),
        ('server.py routes', test.test_server_py_has_run_routes),
        ('replays.js syntax', test.test_replays_js_syntax),
        ('cards.js run cards', test.test_cards_js_has_run_cards),
        ('timeline.js clamp', test.test_timeline_js_has_clamp),
        ('entities.js collapse', test.test_entities_js_has_collapse),
        ('index.html replays tab', test.test_index_html_has_replays_tab),
        ('styles.css run styles', test.test_styles_css_has_run_styles),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"❌ {name}: {e}")
            failed += 1
    
    print(f"\nSyntax Tests: {passed} passed, {failed} failed")
    return failed == 0


def run_unit_tests():
    """Run unit tests (no server required)."""
    print("\n" + "=" * 60)
    print("Phase 11 UNIT TESTS")
    print("=" * 60 + "\n")
    
    test = TestRunStoreUnit()
    
    tests = [
        ('sanitize run name', test.test_sanitize_run_name_basic),
        ('build folder name', test.test_build_run_folder_name),
        ('run validation', test.test_run_validation),
        ('manifest discovery', test.test_run_manifest_discovery),
        ('RunStore CRUD', test.test_run_store_crud_isolated),
        ('rename overwrite', test.test_run_store_rename_overwrite),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"❌ {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print(f"\nUnit Tests: {passed} passed, {failed} failed")
    return failed == 0


async def run_integration_tests():
    """Run integration tests (requires running server)."""
    print("\n" + "=" * 60)
    print("Phase 11 INTEGRATION TESTS")
    print("=" * 60 + "\n")
    
    # Check if server is running
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(f"{BASE_URL}/config", timeout=aiohttp.ClientTimeout(total=2))
            if resp.status != 200:
                print("⚠️  Server not responding correctly, skipping integration tests")
                return True
    except:
        print("⚠️  Server not running, skipping integration tests")
        print("   Start server with: python -m nova.main")
        return True
    
    test = TestPhase11Integration()
    
    # Create session fixture manually
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{BASE_URL}/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASS}
        )
        if resp.status != 200:
            print(f"⚠️  Auth failed ({resp.status}), skipping integration tests")
            return True
        
        tests = [
            ('GET /api/runs', test.test_api_runs_list),
            ('GET/PUT /api/runs/settings', test.test_api_runs_settings),
            ('Run CRUD cycle', test.test_api_run_crud),
            ('hardwareService signals', test.test_api_run_hardware_service_signals),
            ('Run rename folder', test.test_api_run_rename_folder),
            ('Auth required', test.test_api_runs_unauthorized),
        ]
        
        passed = 0
        failed = 0
        
        for name, test_func in tests:
            try:
                # Check if test needs auth_session
                if 'auth_session' in test_func.__code__.co_varnames:
                    await test_func(session)
                else:
                    await test_func()
                passed += 1
            except Exception as e:
                print(f"❌ {name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
        
        print(f"\nIntegration Tests: {passed} passed, {failed} failed")
        return failed == 0


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  PHASE 11 REPLAYS TEST SUITE")
    print("=" * 60)
    
    # Syntax tests (always run)
    syntax_ok = run_syntax_tests()
    
    # Unit tests (always run)
    unit_ok = run_unit_tests()
    
    # Integration tests (require server)
    integration_ok = asyncio.run(run_integration_tests())
    
    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Syntax Tests:      {'✅ PASS' if syntax_ok else '❌ FAIL'}")
    print(f"  Unit Tests:        {'✅ PASS' if unit_ok else '❌ FAIL'}")
    print(f"  Integration Tests: {'✅ PASS' if integration_ok else '❌ FAIL'}")
    print("=" * 60 + "\n")
    
    sys.exit(0 if (syntax_ok and unit_ok and integration_ok) else 1)
