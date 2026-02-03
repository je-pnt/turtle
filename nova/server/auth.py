"""
Authentication and authorization for Server.

Phase 9 Auth (Complete):
- JWT token-based authentication with httpOnly cookies
- tokenVersion in JWT claims for revocation support
- User storage in JSON file (UserStore)
- Roles: admin, operator
- User states: pending, active, disabled
- Bootstrap admin from config
- Admin password reset

Property of Uncompromising Sensors LLC.
"""

import jwt
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from sdk.logging import getLogger
from nova.server.userStore import UserStore

# Cookie configuration
COOKIE_NAME = 'nova_token'
COOKIE_MAX_AGE_DAYS = 7  # Persistent login duration


class AuthManager:
    """
    Authentication manager with user storage.
    
    Phase 9: Full user management with registration/approval workflow.
    Uses httpOnly cookies for JWT storage (same-origin, secure).
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.log = getLogger()
        
        # Auth config
        self.enabled = config.get('enabled', False)
        self.secret = config.get('secret', 'dev-secret-change-in-production')
        self.tokenExpiry = config.get('tokenExpirySeconds', 86400)  # 24 hours default
        self.cookieMaxAge = config.get('cookieMaxAgeDays', COOKIE_MAX_AGE_DAYS) * 86400  # Convert to seconds
        self.secure = config.get('secureCookies', False)  # Set True in production with HTTPS
        
        # User store
        usersPath = config.get('usersPath', './nova/data/users.json')
        self.userStore = UserStore(usersPath)
        
        # Bootstrap admin from config if specified
        bootstrapAdmin = config.get('bootstrapAdmin')
        if bootstrapAdmin and self.enabled:
            username = bootstrapAdmin.get('username')
            password = bootstrapAdmin.get('password')
            if username and password:
                self.userStore.bootstrapAdmin(username, password)
        
        self.log.info(f"[Auth] Initialized: enabled={self.enabled}, "
                     f"users={len(self.userStore.list())}")
    
    def login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Authenticate user and generate token.
        
        Returns dict with token and user info, or None if auth fails.
        """
        if not self.enabled:
            return None
        
        # Verify password
        user = self.userStore.verifyPassword(username, password)
        if not user:
            self.log.warning(f"[Auth] Login failed - invalid credentials: {username}")
            return None
        
        # Check user status
        if user['status'] != 'active':
            self.log.warning(f"[Auth] Login failed - user not active: {username} ({user['status']})")
            return None
        
        # Generate token with tokenVersion
        token = self._generateToken(user)
        
        self.log.info(f"[Auth] Login success: {username}, role={user['role']}")
        
        return {
            'token': token,
            'userId': user['userId'],
            'username': user['username'],
            'role': user['role']
        }
    
    def register(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Register a new user.
        
        New users start with 'pending' status until admin approves.
        Returns user record or None if registration fails.
        """
        if not self.enabled:
            return None
        
        try:
            user = self.userStore.create(username, password, role='operator', status='pending')
            self.log.info(f"[Auth] Registration: {username} (pending approval)")
            return user
        except ValueError as e:
            self.log.warning(f"[Auth] Registration failed: {e}")
            return None
    
    def _generateToken(self, user: Dict[str, Any]) -> str:
        """Generate JWT token for user with tokenVersion"""
        expiresAt = datetime.now(timezone.utc) + timedelta(seconds=self.tokenExpiry)
        
        payload = {
            'userId': user['userId'],
            'username': user['username'],
            'role': user['role'],
            'tokenVersion': user.get('tokenVersion', 1),
            'exp': expiresAt
        }
        
        return jwt.encode(payload, self.secret, algorithm='HS256')
    
    def validateToken(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validate JWT token including tokenVersion check.
        
        Returns payload dict if valid, None otherwise.
        """
        if not self.enabled:
            # Auth disabled - return anonymous user
            return {
                'userId': 'anonymous',
                'username': 'anonymous',
                'role': 'operator'
            }
        
        if not token:
            return None
        
        try:
            payload = jwt.decode(token, self.secret, algorithms=['HS256'])
            
            # Check user still exists and is active
            user = self.userStore.getById(payload.get('userId'))
            if not user or user['status'] != 'active':
                self.log.warning(f"[Auth] Token user invalid or inactive: {payload.get('username')}")
                return None
            
            # Check tokenVersion matches (for revocation support)
            tokenVersion = payload.get('tokenVersion', 0)
            userTokenVersion = user.get('tokenVersion', 1)
            if tokenVersion != userTokenVersion:
                self.log.warning(f"[Auth] Token version mismatch for {payload.get('username')}: "
                               f"token={tokenVersion}, user={userTokenVersion}")
                return None
            
            return payload
            
        except jwt.ExpiredSignatureError:
            self.log.warning("[Auth] Token expired")
            return None
        except jwt.InvalidTokenError as e:
            self.log.warning(f"[Auth] Invalid token: {e}")
            return None
    
    def getCookieSettings(self) -> Dict[str, Any]:
        """Get cookie configuration for Set-Cookie header"""
        return {
            'name': COOKIE_NAME,
            'max_age': self.cookieMaxAge,
            'httponly': True,
            'secure': self.secure,
            'samesite': 'Strict',
            'path': '/'
        }
    
    # Admin functions
    
    def listUsers(self) -> List[Dict[str, Any]]:
        """List all users (admin function)"""
        return self.userStore.list()
    
    def approveUser(self, userId: str) -> Optional[Dict[str, Any]]:
        """Approve a pending user (admin function)"""
        return self.userStore.updateStatus(userId, 'active')
    
    def disableUser(self, userId: str) -> Optional[Dict[str, Any]]:
        """Disable a user (admin function)"""
        return self.userStore.updateStatus(userId, 'disabled')
    
    def enableUser(self, userId: str) -> Optional[Dict[str, Any]]:
        """Re-enable a disabled user (admin function)"""
        return self.userStore.updateStatus(userId, 'active')
    
    def setUserRole(self, userId: str, role: str) -> Optional[Dict[str, Any]]:
        """Set user role (admin function)"""
        if role not in ('admin', 'operator'):
            return None
        return self.userStore.updateRole(userId, role)
    
    def resetPassword(self, userId: str, newPassword: str) -> Optional[Dict[str, Any]]:
        """
        Reset user password (admin function).
        
        This also increments tokenVersion, invalidating all existing JWTs.
        """
        if len(newPassword) < 6:
            return None
        return self.userStore.resetPassword(userId, newPassword)
    
    def revokeUserTokens(self, userId: str) -> Optional[Dict[str, Any]]:
        """
        Revoke all tokens for a user (admin function).
        
        Used for logout-all / force re-authentication.
        """
        return self.userStore.incrementTokenVersion(userId)
    
    def deleteUser(self, userId: str) -> bool:
        """Delete a user (admin function)"""
        return self.userStore.delete(userId)
    
    def checkPermission(self, role: str, action: str) -> bool:
        """
        Check if role has permission for action.
        
        When auth disabled, all actions allowed.
        Roles: admin (all), operator (read/write/command)
        """
        if not self.enabled:
            return True
        
        permissions = {
            'admin': ['read', 'write', 'command', 'admin'],
            'operator': ['read', 'write', 'command']
        }
        
        allowedActions = permissions.get(role, [])
        return action in allowedActions
