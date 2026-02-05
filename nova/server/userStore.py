"""
User Store - JSON-based user persistence for Phase 9 Auth.

Simple file-based storage following novaCore patterns.
Users stored in a JSON file with bcrypt password hashes.

Phase 9 Complete:
- tokenVersion for JWT revocation (increments on password reset/revoke)
- Password reset functionality

Property of Uncompromising Sensors LLC.
"""

import json
import uuid
import bcrypt
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone

from sdk.logging import getLogger


class UserStore:
    """
    JSON-based user storage.
    
    User record structure:
    {
        "userId": "uuid",
        "username": "string",
        "passwordHash": "bcrypt hash",
        "role": "admin|operator",
        "status": "pending|active|disabled",
        "allowedScopes": ["scope1", "scope2"] | ["ALL"],  # Scopes user can access
        "tokenVersion": 1,  # Incremented on password reset/revoke for JWT invalidation
        "createdAt": "ISO timestamp",
        "updatedAt": "ISO timestamp"
    }
    """
    
    def __init__(self, filePath: str = './nova/data/users.json'):
        self.filePath = Path(filePath)
        self.log = getLogger()
        self._users: Dict[str, Dict[str, Any]] = {}
        self._load()
    
    def _load(self):
        """Load users from JSON file"""
        if self.filePath.exists():
            try:
                with open(self.filePath, 'r') as f:
                    data = json.load(f)
                    self._users = {u['userId']: u for u in data.get('users', [])}
                # Migrate existing users
                for user in self._users.values():
                    if 'tokenVersion' not in user:
                        user['tokenVersion'] = 1
                    # Migrate allowedScopes: admin gets ALL, others get empty (must be assigned)
                    if 'allowedScopes' not in user:
                        user['allowedScopes'] = ['ALL'] if user.get('role') == 'admin' else []
                self.log.info(f"[UserStore] Loaded {len(self._users)} users")
            except Exception as e:
                self.log.error(f"[UserStore] Failed to load users: {e}")
                self._users = {}
        else:
            self.log.info("[UserStore] No users file, starting empty")
            self._users = {}
    
    def _save(self):
        """Save users to JSON file"""
        self.filePath.parent.mkdir(parents=True, exist_ok=True)
        data = {'users': list(self._users.values())}
        with open(self.filePath, 'w') as f:
            json.dump(data, f, indent=2)
    
    def getByUsername(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username"""
        for user in self._users.values():
            if user['username'] == username:
                return user
        return None
    
    def getById(self, userId: str) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        return self._users.get(userId)
    
    def list(self) -> List[Dict[str, Any]]:
        """List all users (sanitized)"""
        return [self._sanitize(u) for u in self._users.values()]
    
    def create(self, username: str, password: str, role: str = 'operator', 
               status: str = 'pending') -> Dict[str, Any]:
        """
        Create a new user.
        
        Returns the created user record (without password hash).
        """
        if self.getByUsername(username):
            raise ValueError(f"Username already exists: {username}")
        
        userId = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        # Hash password with bcrypt
        passwordHash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        
        # Default all users to ALL scopes until admin assigns specific scopes
        defaultScopes = ['ALL']
        
        user = {
            'userId': userId,
            'username': username,
            'passwordHash': passwordHash.decode('utf-8'),
            'role': role,
            'status': status,
            'allowedScopes': defaultScopes,
            'tokenVersion': 1,
            'createdAt': now,
            'updatedAt': now
        }
        
        self._users[userId] = user
        self._save()
        
        self.log.info(f"[UserStore] Created user: {username}, role={role}, status={status}")
        
        # Return without password hash
        return self._sanitize(user)
    
    def verifyPassword(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Verify username and password.
        
        Returns user record (without hash) if valid, None otherwise.
        """
        user = self.getByUsername(username)
        if not user:
            return None
        
        if bcrypt.checkpw(password.encode('utf-8'), user['passwordHash'].encode('utf-8')):
            return self._sanitize(user)
        return None
    
    def updateStatus(self, userId: str, status: str) -> Optional[Dict[str, Any]]:
        """Update user status (pending/active/disabled)"""
        user = self._users.get(userId)
        if not user:
            return None
        
        user['status'] = status
        user['updatedAt'] = datetime.now(timezone.utc).isoformat()
        self._save()
        
        self.log.info(f"[UserStore] Updated user {user['username']} status to {status}")
        return self._sanitize(user)
    
    def updateRole(self, userId: str, role: str) -> Optional[Dict[str, Any]]:
        """Update user role (admin/operator)"""
        user = self._users.get(userId)
        if not user:
            return None
        
        user['role'] = role
        # Admin role grants ALL scopes
        if role == 'admin' and 'ALL' not in user.get('allowedScopes', []):
            user['allowedScopes'] = ['ALL']
        user['updatedAt'] = datetime.now(timezone.utc).isoformat()
        self._save()
        
        self.log.info(f"[UserStore] Updated user {user['username']} role to {role}")
        return self._sanitize(user)
    
    def updateScopes(self, userId: str, scopes: List[str]) -> Optional[Dict[str, Any]]:
        """Update user's allowed scopes. Pass ['ALL'] for unrestricted access."""
        user = self._users.get(userId)
        if not user:
            return None
        
        user['allowedScopes'] = scopes
        user['updatedAt'] = datetime.now(timezone.utc).isoformat()
        self._save()
        
        self.log.info(f"[UserStore] Updated user {user['username']} scopes to {scopes}")
        return self._sanitize(user)
    
    def resetPassword(self, userId: str, newPassword: str) -> Optional[Dict[str, Any]]:
        """
        Reset user password and increment tokenVersion.
        
        Incrementing tokenVersion invalidates all existing JWTs for this user.
        """
        user = self._users.get(userId)
        if not user:
            return None
        
        # Hash new password
        passwordHash = bcrypt.hashpw(newPassword.encode('utf-8'), bcrypt.gensalt())
        user['passwordHash'] = passwordHash.decode('utf-8')
        
        # Increment tokenVersion to invalidate existing tokens
        user['tokenVersion'] = user.get('tokenVersion', 0) + 1
        user['updatedAt'] = datetime.now(timezone.utc).isoformat()
        
        self._save()
        
        self.log.info(f"[UserStore] Reset password for {user['username']}, tokenVersion={user['tokenVersion']}")
        return self._sanitize(user)
    
    def incrementTokenVersion(self, userId: str) -> Optional[Dict[str, Any]]:
        """
        Increment tokenVersion to revoke all existing tokens.
        
        Used for logout-all functionality.
        """
        user = self._users.get(userId)
        if not user:
            return None
        
        user['tokenVersion'] = user.get('tokenVersion', 0) + 1
        user['updatedAt'] = datetime.now(timezone.utc).isoformat()
        self._save()
        
        self.log.info(f"[UserStore] Revoked tokens for {user['username']}, tokenVersion={user['tokenVersion']}")
        return self._sanitize(user)
    
    def delete(self, userId: str) -> bool:
        """Delete a user"""
        if userId in self._users:
            username = self._users[userId]['username']
            del self._users[userId]
            self._save()
            self.log.info(f"[UserStore] Deleted user: {username}")
            return True
        return False
    
    def bootstrapAdmin(self, username: str, password: str):
        """
        Bootstrap admin user from config.
        
        Creates admin if no users exist or admin doesn't exist.
        """
        if self.getByUsername(username):
            self.log.info(f"[UserStore] Bootstrap admin already exists: {username}")
            return
        
        # Create admin with active status (no approval needed)
        self.create(username, password, role='admin', status='active')
        self.log.info(f"[UserStore] Bootstrapped admin user: {username}")
    
    def _sanitize(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """Return user record without password hash"""
        return {k: v for k, v in user.items() if k != 'passwordHash'}
