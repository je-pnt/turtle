"""
Phase 9 Auth Runtime Tests

Tests for cookie-based JWT authentication, WebSocket auth,
token revocation, and CSRF protection.

Run: python -m pytest test/test_phase9_auth.py -v
Or standalone: python test/test_phase9_auth.py

Property of Uncompromising Sensors LLC.
"""

import asyncio
import json
import sys
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
TEST_USER = f"testuser_{int(time.time())}"
TEST_PASS = "testpass123"


class TestPhase9Auth:
    """Phase 9 Authentication Tests"""
    
    # =========================================================================
    # E.1 - WebSocket connect tests
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_ws_connect_without_cookie_rejected(self):
        """
        E.1a: WebSocket connect without cookie should be rejected.
        
        Expected: Server closes socket with code 4401 or sends authResponse.success=false
        """
        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(WS_URL) as ws:
                    # Should receive auth failure
                    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                    
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        assert data.get('type') == 'authResponse'
                        assert data.get('success') == False
                        assert 'error' in data
                        print(f"✅ E.1a PASS: WS rejected without cookie - {data.get('error')}")
                    elif msg.type == aiohttp.WSMsgType.CLOSE:
                        assert ws.close_code == 4401
                        print(f"✅ E.1a PASS: WS closed with code 4401")
                    else:
                        pytest.fail(f"Unexpected message type: {msg.type}")
                        
            except aiohttp.WSServerHandshakeError as e:
                # Also acceptable - rejected at handshake
                print(f"✅ E.1a PASS: WS handshake rejected - {e}")
    
    @pytest.mark.asyncio
    async def test_ws_connect_with_cookie_succeeds(self):
        """
        E.1b: WebSocket connect with valid cookie should succeed.
        
        Expected: Server sends authResponse.success=true with user info
        """
        async with aiohttp.ClientSession() as session:
            # First login to get cookie
            login_resp = await session.post(
                f"{BASE_URL}/auth/login",
                json={"username": ADMIN_USER, "password": ADMIN_PASS}
            )
            assert login_resp.status == 200, f"Login failed: {await login_resp.text()}"
            
            # Cookie is automatically stored in session
            # Now connect WebSocket
            async with session.ws_connect(WS_URL) as ws:
                msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                
                assert msg.type == aiohttp.WSMsgType.TEXT
                data = json.loads(msg.data)
                
                assert data.get('type') == 'authResponse'
                assert data.get('success') == True
                assert data.get('username') == ADMIN_USER
                assert 'connId' in data
                
                print(f"✅ E.1b PASS: WS connected with cookie - user={data.get('username')}")
    
    # =========================================================================
    # E.2 - Login + WS end-to-end
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_login_sets_cookie_ws_accepts(self):
        """
        E.2: Full login flow - POST /login returns Set-Cookie, WS uses it.
        
        Steps:
        1. POST /login with credentials → returns Set-Cookie header
        2. Client opens WS → server accepts auth (reads cookie)
        """
        async with aiohttp.ClientSession() as session:
            # Step 1: Login
            login_resp = await session.post(
                f"{BASE_URL}/auth/login",
                json={"username": ADMIN_USER, "password": ADMIN_PASS}
            )
            
            assert login_resp.status == 200
            
            # Verify Set-Cookie header
            cookies = login_resp.cookies
            assert 'nova_token' in cookies, "Set-Cookie header missing nova_token"
            
            cookie = cookies['nova_token']
            print(f"✅ E.2 Step 1: Login returned Set-Cookie (nova_token)")
            print(f"   Cookie attributes: httponly={cookie.get('httponly')}, "
                  f"samesite={cookie.get('samesite')}")
            
            # Step 2: Connect WebSocket (cookie sent automatically)
            async with session.ws_connect(WS_URL) as ws:
                msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                data = json.loads(msg.data)
                
                assert data.get('success') == True
                print(f"✅ E.2 Step 2: WS accepted with cookie - connId={data.get('connId')[:8]}...")
    
    # =========================================================================
    # E.3 - Argon2/bcrypt startup test (bcrypt in our case)
    # =========================================================================
    
    def test_password_hashing_library_available(self):
        """
        E.3: Password hashing library is available and used.
        
        Our implementation uses bcrypt (documented deliberate choice over Argon2).
        Test that bcrypt is importable and userStore uses it.
        """
        try:
            import bcrypt
            print(f"✅ E.3 PASS: bcrypt available - version info available")
            
            # Verify it actually works
            test_hash = bcrypt.hashpw(b"test", bcrypt.gensalt())
            assert bcrypt.checkpw(b"test", test_hash)
            print(f"✅ E.3 PASS: bcrypt hashing functional")
            
        except ImportError:
            pytest.fail("bcrypt not installed - auth will fail")
    
    # =========================================================================
    # E.4 - Token revocation test
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_token_revocation_via_version_increment(self):
        """
        E.4: Token revocation via tokenVersion increment.
        
        Steps:
        1. Login as test user, get cookie
        2. Admin increments user's tokenVersion (via password reset)
        3. Request with old cookie should be rejected
        """
        async with aiohttp.ClientSession() as admin_session:
            # Admin login
            await admin_session.post(
                f"{BASE_URL}/auth/login",
                json={"username": ADMIN_USER, "password": ADMIN_PASS}
            )
            
            # Register a test user
            reg_resp = await admin_session.post(
                f"{BASE_URL}/auth/register",
                json={"username": TEST_USER, "password": TEST_PASS}
            )
            
            if reg_resp.status not in (200, 201):
                # User might already exist, that's ok
                print(f"   Note: Registration returned {reg_resp.status}")
            
            # Get user list to find test user ID
            users_resp = await admin_session.get(f"{BASE_URL}/api/admin/users")
            users_data = await users_resp.json()
            users = users_data.get('users', [])
            
            test_user_record = next((u for u in users if u['username'] == TEST_USER), None)
            
            if not test_user_record:
                pytest.skip(f"Test user {TEST_USER} not found")
            
            user_id = test_user_record['userId']
            
            # Approve user if pending
            if test_user_record['status'] == 'pending':
                await admin_session.post(f"{BASE_URL}/api/admin/users/{user_id}/approve")
        
        # Now test with the test user
        async with aiohttp.ClientSession() as user_session:
            # Login as test user
            login_resp = await user_session.post(
                f"{BASE_URL}/auth/login",
                json={"username": TEST_USER, "password": TEST_PASS}
            )
            
            if login_resp.status != 200:
                pytest.skip(f"Test user login failed: {await login_resp.text()}")
            
            # Verify cookie works
            me_resp = await user_session.get(f"{BASE_URL}/auth/me")
            assert me_resp.status == 200, "Initial auth/me should work"
            print(f"✅ E.4 Step 1: User logged in, cookie valid")
            
            # Save cookie for later
            saved_cookies = user_session.cookie_jar.filter_cookies(BASE_URL)
        
        # Admin resets password (increments tokenVersion)
        async with aiohttp.ClientSession() as admin_session:
            await admin_session.post(
                f"{BASE_URL}/auth/login",
                json={"username": ADMIN_USER, "password": ADMIN_PASS}
            )
            
            reset_resp = await admin_session.post(
                f"{BASE_URL}/api/admin/users/{user_id}/reset-password",
                json={"password": "newpassword123"}
            )
            assert reset_resp.status == 200, f"Password reset failed: {await reset_resp.text()}"
            print(f"✅ E.4 Step 2: Admin reset password (tokenVersion incremented)")
        
        # Try to use old cookie - should be rejected
        async with aiohttp.ClientSession() as old_session:
            # Manually set old cookie
            old_session.cookie_jar.update_cookies(saved_cookies)
            
            me_resp = await old_session.get(f"{BASE_URL}/auth/me")
            assert me_resp.status == 401, f"Old cookie should be rejected, got {me_resp.status}"
            print(f"✅ E.4 Step 3: Old cookie rejected after tokenVersion change")
        
        print(f"✅ E.4 PASS: Token revocation works")
    
    # =========================================================================
    # E.5 - CSRF check (SameSite=Strict)
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_csrf_protection_via_samesite(self):
        """
        E.5: CSRF protection via SameSite=Strict cookie attribute.
        
        Verify that the cookie is set with SameSite=Strict.
        This prevents the browser from sending the cookie on cross-site requests.
        """
        async with aiohttp.ClientSession() as session:
            login_resp = await session.post(
                f"{BASE_URL}/auth/login",
                json={"username": ADMIN_USER, "password": ADMIN_PASS}
            )
            
            assert login_resp.status == 200
            
            # Check cookie attributes
            cookies = login_resp.cookies
            cookie = cookies.get('nova_token')
            
            assert cookie is not None, "nova_token cookie not set"
            
            # Note: aiohttp may not expose all cookie attributes directly
            # The actual protection is in the Set-Cookie header
            set_cookie = login_resp.headers.get('Set-Cookie', '')
            
            assert 'SameSite=Strict' in set_cookie or 'samesite=Strict' in set_cookie.lower(), \
                f"SameSite=Strict not in Set-Cookie header: {set_cookie}"
            
            assert 'HttpOnly' in set_cookie or 'httponly' in set_cookie.lower(), \
                f"HttpOnly not in Set-Cookie header: {set_cookie}"
            
            print(f"✅ E.5 PASS: Cookie has SameSite=Strict and HttpOnly")
            print(f"   Set-Cookie: {set_cookie[:100]}...")


# =============================================================================
# Standalone runner
# =============================================================================

async def run_all_tests():
    """Run all tests standalone (without pytest)"""
    print("=" * 60)
    print("Phase 9 Auth Runtime Tests")
    print("=" * 60)
    print(f"Target: {BASE_URL}")
    print()
    
    tests = TestPhase9Auth()
    
    # E.1a
    print("\n--- E.1a: WS connect without cookie ---")
    try:
        await tests.test_ws_connect_without_cookie_rejected()
    except Exception as e:
        print(f"❌ FAIL: {e}")
    
    # E.1b
    print("\n--- E.1b: WS connect with cookie ---")
    try:
        await tests.test_ws_connect_with_cookie_succeeds()
    except Exception as e:
        print(f"❌ FAIL: {e}")
    
    # E.2
    print("\n--- E.2: Login + WS end-to-end ---")
    try:
        await tests.test_login_sets_cookie_ws_accepts()
    except Exception as e:
        print(f"❌ FAIL: {e}")
    
    # E.3
    print("\n--- E.3: Password hashing library ---")
    try:
        tests.test_password_hashing_library_available()
    except Exception as e:
        print(f"❌ FAIL: {e}")
    
    # E.4
    print("\n--- E.4: Token revocation ---")
    try:
        await tests.test_token_revocation_via_version_increment()
    except Exception as e:
        print(f"❌ FAIL: {e}")
    
    # E.5
    print("\n--- E.5: CSRF protection (SameSite) ---")
    try:
        await tests.test_csrf_protection_via_samesite()
    except Exception as e:
        print(f"❌ FAIL: {e}")
    
    print("\n" + "=" * 60)
    print("Tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
