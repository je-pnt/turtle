# Phase 9 Summary: Authentication & Chat System

**Property of Uncompromising Sensors LLC**  
**Date:** February 1, 2026  
**Status:** Complete ✅

---

## Overview

Phase 9 implemented a complete authentication system for NOVA with:
- **httpOnly cookie-based JWT authentication** (no localStorage token storage)
- **tokenVersion for JWT revocation** (logout-all, password reset invalidates tokens)
- **Admin password reset** functionality
- **Chat messages as metadata truth events** (replayable with timeline)
- **Replay mode chat behavior** (highlight current message, follow toggle with auto-disable on scroll)

---

## 1. Authentication Architecture

### 1.1 Cookie-Based Auth Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Browser   │────>│   Server    │────>│  AuthManager │
│ (login.js)  │     │ (server.py) │     │  (auth.py)   │
└─────────────┘     └─────────────┘     └──────┬───────┘
      ▲                    │                    │
      │              Set-Cookie:          ┌────▼─────┐
      │              httpOnly             │UserStore │
      └────────────────────┘              └────┬─────┘
         Cookie sent                           │
         automatically                    users.json
```

**Key Design Decisions:**
- JWT stored in **httpOnly cookie** (not accessible to JavaScript)
- No `Authorization` header needed (same-origin cookie is automatic)
- **tokenVersion** in JWT claims for revocation
- Session storage for user info display only (not auth token)
- WebSocket auth via same cookie (sent on upgrade request)

### 1.2 Token Structure

JWT payload includes:
```json
{
  "userId": "uuid",
  "username": "string",
  "role": "admin|operator",
  "tokenVersion": 1,
  "exp": 1738450000
}
```

### 1.3 Token Revocation (tokenVersion)

- Each user has a `tokenVersion` field (starts at 1)
- JWT includes `tokenVersion` at time of issuance
- On validation, if JWT's tokenVersion < user's tokenVersion, token is invalid
- Password reset and `revokeUserTokens()` increment tokenVersion
- Enables "logout all devices" functionality

### 1.4 Cookie Settings

```python
COOKIE_NAME = 'nova_token'
COOKIE_MAX_AGE_DAYS = 7
# Set via response.set_cookie() with:
#   httponly=True
#   secure=config.get('secureCookie', False)
#   samesite='Strict'
```

---

## 2. User Management

### 2.1 User States

| State | Description |
|-------|-------------|
| `pending` | Newly registered, awaiting admin approval |
| `active` | Approved and can log in |
| `disabled` | Blocked from logging in |

### 2.2 User Roles

| Role | Permissions |
|------|-------------|
| `admin` | Full access: read, write, command, admin functions, password reset |
| `operator` | Standard access: read, write, command |

### 2.3 Password Reset (Admin)

New endpoint: `POST /api/admin/users/{userId}/reset-password`
- Admin provides temporary password
- User's `tokenVersion` is incremented (invalidates existing sessions)
- User logs in with temp password, should change immediately

---

## 3. Chat as Truth Events

### 3.1 Architecture

Chat messages are now stored as **metadata truth events** in the database:

```
lane: metadata
messageType: ChatMessage
systemId: nova-server
containerId: chat
uniqueId: {channel}  (e.g., "ops")
payload: {text, username, userId, channel}
```

This makes chat messages:
- **Replayable**: appear during timeline replay at correct time
- **Persistent**: stored in database like all other truth
- **Ordered**: follow the same ordering contract as all events

### 3.2 Message Flow

**Live Mode:**
1. Client sends `{type: 'chat', channel, text}` via WebSocket
2. Server calls `ipcClient.ingestMetadata()` to store as truth event
3. Core creates MetadataEvent, computes eventId, stores in DB
4. Server broadcasts to all connected clients

**Replay Mode:**
1. Chat events arrive via normal stream (MetadataEvent lane)
2. UI filters by `messageType === 'ChatMessage'`
3. Messages displayed only if timestamp <= cursor
4. Current message highlighted, follow mode auto-scrolls

### 3.3 IPC Contract Addition

New request type for Server→Core:
```python
class RequestType(str, Enum):
    ...
    INGEST_METADATA = "ingestMetadata"  # Phase 9: chat messages

@dataclass
class IngestMetadataRequest:
    requestId: str
    clientConnId: str
    scopeId: str
    messageType: str  # 'ChatMessage'
    effectiveTime: str  # ISO8601
    sourceTruthTime: str  # ISO8601
    systemId: str  # 'nova-server'
    containerId: str  # 'chat'
    uniqueId: str  # channel name
    payload: Dict[str, Any]
```

---

## 4. Files Modified

### 4.1 Server (Python)

| File | Changes |
|------|---------|
| `nova/server/userStore.py` | Added `tokenVersion` field, `resetPassword()`, `incrementTokenVersion()` |
| `nova/server/auth.py` | Cookie-based auth, `getCookieSettings()`, `resetPassword()`, `revokeUserTokens()`, tokenVersion validation |
| `nova/server/server.py` | Cookie handling in login/logout, `/auth/me` endpoint, password reset endpoint, WebSocket reads cookie |
| `nova/server/ipc.py` | Added `ingestMetadata()` method for chat truth events |
| `nova/core/contracts.py` | Added `RequestType.INGEST_METADATA`, `IngestMetadataRequest` dataclass |
| `nova/core/ipc.py` | Added `_handleIngestMetadata()` handler |

### 4.2 Client (JavaScript)

| File | Changes |
|------|---------|
| `nova/ui/js/auth.js` | Complete rewrite: no localStorage, cookie-based, `/auth/me` verification |
| `nova/ui/js/login.js` | Uses sessionStorage for user info only, `credentials: 'same-origin'` |
| `nova/ui/js/register.js` | Redirects to approval-pending page on success |
| `nova/ui/js/admin.js` | Removed Authorization header, uses `credentials: 'same-origin'` |
| `nova/ui/js/websocket.js` | Removed query param token, cookie sent automatically |
| `nova/ui/js/init.js` | Uses async `NovaAuth.requireAuth()` |
| `nova/ui/js/chat.js` | Replay mode: follow toggle with auto-disable on user scroll, current message highlight, disabled input |
| `nova/ui/js/display.js` | Dispatches `nova:metadataEvent` for metadata lane events |

### 4.3 HTML Pages

| File | Description |
|------|-------------|
| `nova/ui/html/login.html` | Login page (centered card design) |
| `nova/ui/html/register.html` | Registration page with password confirmation |
| `nova/ui/html/admin.html` | Admin panel with user table and actions |
| `nova/ui/html/approval-pending.html` | Shown after registration while awaiting approval |

### 4.4 CSS

| File | Changes |
|------|---------|
| `nova/ui/css/auth.css` | `.chat-message.current`, `.chat-follow-toggle`, disabled input styles, `.pending-message` styles |

### 4.5 Tests

| File | Description |
|------|-------------|
| `test/test_phase9_auth.py` | Runtime tests for E.1-E.5 (WS auth, login flow, bcrypt, token revocation, CSRF) |

---

## 5. API Endpoints

### 5.1 Authentication

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login` | Login, returns user info, sets httpOnly cookie |
| POST | `/auth/logout` | Clears cookie |
| GET | `/auth/me` | Returns current user from cookie (for verification) |
| POST | `/auth/register` | Create pending user account |

### 5.2 Pages

| Path | Description |
|------|-------------|
| `/login` | Login page |
| `/register` | Registration page |
| `/approval-pending` | Shown after registration |
| `/admin` | Admin user management panel |
| `/` | Main NOVA UI (requires auth) |

### 5.3 Admin API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/users` | List all users |
| POST | `/api/admin/users/{id}/approve` | Approve pending user |
| POST | `/api/admin/users/{id}/disable` | Disable user |
| POST | `/api/admin/users/{id}/enable` | Re-enable user |
| POST | `/api/admin/users/{id}/role` | Change user role |
| POST | `/api/admin/users/{id}/reset-password` | Admin sets temp password |
| DELETE | `/api/admin/users/{id}` | Delete user |

---

## 6. Configuration

```json
{
  "authEnabled": true,
  "jwtSecret": "your-secret-key",
  "tokenExpireHours": 24,
  "secureCookie": false,
  "adminUser": {
    "username": "admin",
    "password": "adminpass"
  }
}
```

---

## 7. Architecture Compliance

### 7.1 Invariants Preserved

| Invariant | How Phase 9 Maintains It |
|-----------|-------------------------|
| One way to do everything | Single auth path: cookie-based JWT (no localStorage fallback) |
| Timeline truth is complete | Chat stored as metadata truth events, replayable |
| Core owns DB | Chat ingested via IPC to Core, not written by Server |
| No parallel code paths | Removed Bearer header, query param token paths |

### 7.2 Security Properties

- **httpOnly cookie**: XSS cannot steal token
- **SameSite=Strict**: CSRF protection
- **tokenVersion**: Session revocation without token expiry change
- **bcrypt**: Password hashing

---

## 8. Testing Notes

### 8.1 Manual Testing

1. **Login Flow**: Login → verify cookie set → access protected pages
2. **Cookie Auth**: F12 → Application → Cookies → verify `nova_token` is httpOnly
3. **Logout**: Click logout → verify cookie cleared → redirected to login
4. **WebSocket**: Verify WS connects without query param (cookie automatic)
5. **Token Revocation**: Admin resets password → user's sessions invalidate
6. **Chat Replay**: Record chat → replay timeline → verify messages appear at correct time

### 8.2 Edge Cases

- Token expired while page open → 401 → redirect to login
- Network disconnect → WS reconnect uses cookie (automatic)
- Multiple tabs → share cookie, all authenticated

---

## 9. Security Design Decisions

### 9.1 Password Hashing: bcrypt (deliberate choice)

We use **bcrypt** instead of Argon2:
- bcrypt is battle-tested since 1999, still considered secure
- Argon2 (PHC winner 2015) is more GPU-resistant via memory-hardness
- For user auth (not cryptocurrency), bcrypt provides equivalent practical security
- Requirement: `bcrypt>=4.0.0` in requirements.txt

### 9.2 CSRF Protection: SameSite=Strict

We use **SameSite=Strict** cookie attribute instead of CSRF tokens:
- Browser refuses to send cookie on cross-site requests
- Attacker's form submission arrives without auth cookie
- Combined with httpOnly, this provides complete XSS/CSRF protection
- No additional CSRF token infrastructure needed

### 9.3 Runtime Tests

Test file: `test/test_phase9_auth.py`
- E.1: WebSocket connect without cookie rejected, with cookie succeeds
- E.2: Login + WS end-to-end flow
- E.3: bcrypt library availability
- E.4: Token revocation via tokenVersion increment
- E.5: CSRF protection via SameSite verification

Run: `python test/test_phase9_auth.py` (standalone) or `pytest test/test_phase9_auth.py -v`

---

## 10. Future Considerations

- **Refresh tokens**: For longer sessions without re-login
- **OAuth integration**: SSO with external providers
- **Rate limiting**: Prevent brute force on login
- **Audit logging**: Track admin actions as truth events

---

**Phase 9 Complete.** ✅

All tests passing:
```
✅ E.1a PASS: WS rejected without cookie
✅ E.1b PASS: WS connected with cookie
✅ E.2 PASS: Login + WS end-to-end
✅ E.3 PASS: bcrypt hashing functional
✅ E.4 PASS: Token revocation works
✅ E.5 PASS: Cookie has SameSite=Strict and HttpOnly
```
