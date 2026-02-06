"""
NOVA Server process - WebSocket edge handler.

Handles client connections, auth, message routing.
Forwards requests to Core via IPC.

Architecture invariants:
- Server is stateless (no persistent session storage)
- Ephemeral per-connection state only (active streams)
- Core is authoritative (all validation, all DB access)
- TCP streams use separate streams.db (not truth DB)
- Stream definitions persist, sessions/bindings are ephemeral

Property of Uncompromising Sensors LLC.
"""

import asyncio
import orjson
import time
import uuid
from aiohttp import web, WSMsgType
from typing import Dict, Any, Optional
from pathlib import Path
from multiprocessing import Queue

from nova.server.auth import AuthManager, COOKIE_NAME
from nova.server.ipc import ServerIPCClient
from nova.server.streamStore import StreamStore, StreamDefinition
from nova.server.streams import StreamManager
from nova.server.presentationStore import PresentationStore
from nova.server.runStore import RunStore
from nova.core.contracts import TimelineMode
from nova.core.manifests.cards import getAllCardManifestsDict
from nova.core.manifests.runs import getRunManifestRegistry
from sdk.logging import getLogger


class ClientConnection:
    """
    Ephemeral client connection state.
    
    Exists only while WebSocket is open.
    Tracks active stream playbackRequestId for fencing.
    """
    
    def __init__(self, connId: str, ws: web.WebSocketResponse, 
                 userId: Optional[str] = None, username: Optional[str] = None, role: Optional[str] = None):
        self.connId = connId
        self.ws = ws
        self.userId = userId
        self.username = username
        self.role = role
        self.activePlaybackId: Optional[str] = None
        self.log = getLogger()
    
    async def sendMessage(self, message: Dict[str, Any]):
        """Send JSON message to client"""
        if not self.ws.closed:
            await self.ws.send_json(message)
    
    async def sendError(self, error: str, requestId: Optional[str] = None):
        """Send error message to client"""
        await self.sendMessage({
            'type': 'error',
            'error': error,
            'requestId': requestId
        })
    
    def setActiveStream(self, playbackRequestId: str):
        """Set active stream playbackRequestId for fencing"""
        self.activePlaybackId = playbackRequestId
        self.log.info(f"[Conn {self.connId}] Active playback: {playbackRequestId}")
    
    def clearActiveStream(self):
        """Clear active stream"""
        self.activePlaybackId = None
        self.log.info(f"[Conn {self.connId}] Stream cleared")
    
    def shouldDiscardChunk(self, playbackRequestId: str) -> bool:
        """Check if chunk should be discarded (stale playbackRequestId)"""
        return self.activePlaybackId != playbackRequestId


class NovaServer:
    """
    NOVA Server process.
    
    Handles WebSocket connections from Web UI.
    Handles TCP stream management (Phase 8.1).
    Forwards requests to Core via IPC.
    """
    
    def __init__(self, config: Dict[str, Any], requestQueue: Queue, responseQueue: Queue):
        self.config = config
        self.log = getLogger()
        
        # Auth
        authConfig = config.get('auth', {'enabled': False})
        self.authManager = AuthManager(authConfig)
        
        # IPC client
        self.ipcClient = ServerIPCClient(requestQueue, responseQueue)
        
        # Active connections: connId → ClientConnection
        self.connections: Dict[str, ClientConnection] = {}
        
        # Timeline mode (LIVE/REPLAY)
        self._timelineMode = TimelineMode.LIVE
        
        # Stream store (SQLite-based persistence)
        self.streamStore = StreamStore()
        
        # Stream manager (multi-protocol: tcp, websocket, udp)
        self.streamManager = StreamManager(
            dataCallback=self._streamDataCallback,
            host=config.get('tcp', {}).get('host', '0.0.0.0')
        )
        
        # Presentation store (Phase 10: per-user overrides + admin defaults)
        self.presentationStore = PresentationStore()
        
        # Run store (Phase 11: per-user runs/replays)
        self.runStore = RunStore()
        
        # aiohttp app
        self.app = web.Application()
        self._setupRoutes()
        
        self._runner = None
        self._site = None
    
    def _setupRoutes(self):
        """Setup aiohttp routes"""
        self.app.router.add_get('/ws', self.handleWebSocket)
        self.app.router.add_get('/ws/streams/{path}', self.handleWsStream)
        self.app.router.add_get('/health', self.handleHealth)
        self.app.router.add_get('/config', self.handleConfig)
        
        # Auth endpoints (cookie-based)
        self.app.router.add_post('/auth/login', self.handleLogin)
        self.app.router.add_post('/auth/logout', self.handleLogout)
        self.app.router.add_post('/auth/register', self.handleRegister)
        self.app.router.add_get('/auth/me', self.handleAuthMe)
        
        # Admin API endpoints (require admin role)
        self.app.router.add_get('/api/admin/users', self.handleListUsers)
        self.app.router.add_post('/api/admin/users/{userId}/approve', self.handleApproveUser)
        self.app.router.add_post('/api/admin/users/{userId}/disable', self.handleDisableUser)
        self.app.router.add_post('/api/admin/users/{userId}/enable', self.handleEnableUser)
        self.app.router.add_post('/api/admin/users/{userId}/role', self.handleSetUserRole)
        self.app.router.add_post('/api/admin/users/{userId}/reset-password', self.handleResetPassword)
        self.app.router.add_delete('/api/admin/users/{userId}', self.handleDeleteUser)
        
        # Stream API endpoints
        self.app.router.add_get('/api/streams', self.handleListStreams)
        self.app.router.add_post('/api/streams', self.handleCreateStream)
        self.app.router.add_get('/api/streams/{streamId}', self.handleGetStream)
        self.app.router.add_put('/api/streams/{streamId}', self.handleUpdateStream)
        self.app.router.add_delete('/api/streams/{streamId}', self.handleDeleteStream)
        self.app.router.add_post('/api/streams/{streamId}/start', self.handleStartStream)
        self.app.router.add_post('/api/streams/{streamId}/stop', self.handleStopStream)
        self.app.router.add_post('/api/streams/{streamId}/bind', self.handleBindStream)
        self.app.router.add_post('/api/streams/{streamId}/unbind', self.handleUnbindStream)
        
        # Presentation API endpoints (Phase 10)
        # No scopeId in path - server resolves from user's effective scopes
        self.app.router.add_get('/api/presentation/models', self.handleListModels)
        self.app.router.add_get('/api/presentation', self.handleGetPresentation)
        self.app.router.add_put('/api/presentation/{uniqueId}', self.handleSetPresentation)
        self.app.router.add_delete('/api/presentation/{uniqueId}', self.handleDeletePresentation)
        self.app.router.add_get('/api/presentation-default', self.handleGetPresentationDefaults)
        self.app.router.add_put('/api/presentation-default/{uniqueId}', self.handleSetPresentationDefaults)
        self.app.router.add_delete('/api/presentation-default/{uniqueId}', self.handleDeletePresentationDefaults)
        
        # Admin scope management
        self.app.router.add_get('/api/admin/scopes', self.handleListScopes)
        self.app.router.add_put('/api/admin/users/{userId}/scopes', self.handleSetUserScopes)
        
        # Run API endpoints (Phase 11: per-user runs/replays)
        self.app.router.add_get('/api/runs', self.handleListRuns)
        self.app.router.add_post('/api/runs', self.handleCreateRun)
        self.app.router.add_get('/api/runs/{runNumber}', self.handleGetRun)
        self.app.router.add_put('/api/runs/{runNumber}', self.handleUpdateRun)
        self.app.router.add_delete('/api/runs/{runNumber}', self.handleDeleteRun)
        self.app.router.add_post('/api/runs/{runNumber}/bundle', self.handleCreateBundle)
        self.app.router.add_get('/api/runs/settings', self.handleGetRunSettings)
        self.app.router.add_put('/api/runs/settings', self.handleSetRunSettings)
        
        # Export download endpoint
        self.app.router.add_get('/exports/{exportId}.zip', self.handleExportDownload)
        
        # Static UI files
        uiPath = Path(__file__).parent.parent / 'ui'
        self.app.router.add_get('/', self._serveIndexHtml)
        self.app.router.add_get('/login', self._serveLoginHtml)
        self.app.router.add_get('/register', self._serveRegisterHtml)
        self.app.router.add_get('/approval-pending', self._serveApprovalPendingHtml)
        self.app.router.add_get('/admin', self._serveAdminHtml)
        self.app.router.add_static('/ui', uiPath, name='ui', show_index=False)
    
    async def start(self):
        """Start Server"""
        self.log.info("[Server] Starting...")
        
        # Start IPC client
        await self.ipcClient.start()
        
        # Start enabled streams from persistence
        await self._startPersistedStreams()
        
        # Start aiohttp app
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        
        host = self.config.get('host', '0.0.0.0')
        port = self.config.get('port', 8080)
        
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        
        self.log.info(f"[Server] Listening on {host}:{port}")
    
    async def _startPersistedStreams(self):
        """Start all enabled streams from persistence on server startup"""
        definitions = self.streamStore.list()
        for defn in definitions:
            if defn.enabled:
                success, err = await self.streamManager.startStream(defn)
                if success:
                    if defn.protocol == 'websocket':
                        self.log.info(f"[Server] Started persisted stream: {defn.name} at /ws/streams/{defn.endpoint}")
                    else:
                        self.log.info(f"[Server] Started persisted stream: {defn.name} on port {defn.endpoint}")
                else:
                    self.log.error(f"[Server] Failed to start persisted stream {defn.name}: {err}")
    
    async def stop(self):
        """Stop Server"""
        self.log.info("[Server] Stopping...")
        
        # Close all connections
        for conn in list(self.connections.values()):
            await conn.ws.close()
        
        # Stop all streams
        await self.streamManager.stopAll()
        
        # Stop IPC client
        await self.ipcClient.stop()
        
        # Stop aiohttp
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        
        self.log.info("[Server] Stopped")
    
    # =========================================================================
    # HTTP Handlers
    # =========================================================================
    
    async def handleHealth(self, request: web.Request) -> web.Response:
        """Health check endpoint"""
        return web.json_response({'status': 'ok'})
    
    async def handleConfig(self, request: web.Request) -> web.Response:
        """UI configuration endpoint"""
        nodeMode = self.config.get('mode', 'payload')
        defaultTimebase = 'source' if nodeMode == 'payload' else 'canonical'
        
        # Get run manifest registry
        runManifestRegistry = getRunManifestRegistry()
        
        uiConfig = {
            'mode': nodeMode,
            'defaultTimebase': defaultTimebase,
            'defaultRate': self.config.get('ui', {}).get('defaultRate', 1.0),
            'defaultMode': self.config.get('ui', {}).get('defaultMode', 'live'),
            'onlineWindowSeconds': self.config.get('ui', {}).get('onlineWindowSeconds', 5),
            'cleanupWindowSeconds': self.config.get('ui', {}).get('cleanupWindowSeconds', 120),
            'stallWindowSeconds': self.config.get('ui', {}).get('stallWindowSeconds', 5),
            'authEnabled': self.authManager.enabled,
            'cardManifests': getAllCardManifestsDict(),
            'runManifests': runManifestRegistry.toConfigDict()
        }
        
        return web.json_response(uiConfig)
    
    async def handleLogin(self, request: web.Request) -> web.Response:
        """
        HTTP login endpoint.
        
        Sets JWT in httpOnly cookie for same-origin browser auth.
        """
        try:
            data = await request.json()
            username = data.get('username')
            password = data.get('password')
            
            if not username or not password:
                return web.json_response({'error': 'Username and password required'}, status=400)
            
            if not self.authManager.enabled:
                # Auth disabled - set anonymous cookie and return
                response = web.json_response({
                    'userId': 'anonymous',
                    'username': 'anonymous',
                    'role': 'operator'
                })
                return response
            
            result = self.authManager.login(username, password)
            if not result:
                return web.json_response({'error': 'Invalid credentials or account not active'}, status=401)
            
            # Set httpOnly cookie with JWT
            token = result.pop('token')  # Remove token from response body
            cookieSettings = self.authManager.getCookieSettings()
            
            response = web.json_response({
                'userId': result['userId'],
                'username': result['username'],
                'role': result['role']
            })
            
            response.set_cookie(
                cookieSettings['name'],
                token,
                max_age=cookieSettings['max_age'],
                httponly=cookieSettings['httponly'],
                secure=cookieSettings['secure'],
                samesite=cookieSettings['samesite'],
                path=cookieSettings['path']
            )
            
            return response
        
        except Exception as e:
            self.log.error(f"[Server] Login error: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def handleLogout(self, request: web.Request) -> web.Response:
        """
        HTTP logout endpoint.
        
        Clears the auth cookie.
        """
        cookieSettings = self.authManager.getCookieSettings()
        response = web.json_response({'message': 'Logged out'})
        
        # Clear cookie by setting max_age=0
        response.del_cookie(cookieSettings['name'], path=cookieSettings['path'])
        
        return response
    
    async def handleAuthMe(self, request: web.Request) -> web.Response:
        """
        Get current authenticated user info from cookie.
        
        Used by UI to check auth status on page load.
        """
        payload = self._getAuthFromCookie(request)
        if not payload:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        
        return web.json_response({
            'userId': payload.get('userId'),
            'username': payload.get('username'),
            'role': payload.get('role')
        })
    
    async def handleRegister(self, request: web.Request) -> web.Response:
        """HTTP registration endpoint"""
        try:
            data = await request.json()
            username = data.get('username')
            password = data.get('password')
            
            if not username or not password:
                return web.json_response({'error': 'Username and password required'}, status=400)
            
            if len(username) < 3:
                return web.json_response({'error': 'Username must be at least 3 characters'}, status=400)
            
            if len(password) < 6:
                return web.json_response({'error': 'Password must be at least 6 characters'}, status=400)
            
            if not self.authManager.enabled:
                return web.json_response({'error': 'Authentication is disabled'}, status=400)
            
            user = self.authManager.register(username, password)
            if not user:
                return web.json_response({'error': 'Username already exists'}, status=409)
            
            return web.json_response({
                'message': 'Registration successful. Please wait for admin approval.',
                'user': user
            }, status=201)
        
        except Exception as e:
            self.log.error(f"[Server] Register error: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    # Auth helpers
    
    def _getAuthFromCookie(self, request: web.Request) -> Optional[Dict[str, Any]]:
        """Extract and validate auth from httpOnly cookie"""
        from nova.server.auth import COOKIE_NAME
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        return self.authManager.validateToken(token)
    
    # Admin API handlers
    
    def _checkAdminAuth(self, request: web.Request) -> Optional[Dict[str, Any]]:
        """Validate admin auth from httpOnly cookie"""
        payload = self._getAuthFromCookie(request)
        if not payload or payload.get('role') != 'admin':
            return None
        return payload
    
    async def handleListUsers(self, request: web.Request) -> web.Response:
        """List all users (admin only)"""
        if not self._checkAdminAuth(request):
            return web.json_response({'error': 'Admin access required'}, status=403)
        
        users = self.authManager.listUsers()
        return web.json_response({'users': users})
    
    async def handleApproveUser(self, request: web.Request) -> web.Response:
        """Approve a pending user (admin only)"""
        if not self._checkAdminAuth(request):
            return web.json_response({'error': 'Admin access required'}, status=403)
        
        userId = request.match_info.get('userId')
        user = self.authManager.approveUser(userId)
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        return web.json_response({'user': user})
    
    async def handleDisableUser(self, request: web.Request) -> web.Response:
        """Disable a user (admin only)"""
        if not self._checkAdminAuth(request):
            return web.json_response({'error': 'Admin access required'}, status=403)
        
        userId = request.match_info.get('userId')
        user = self.authManager.disableUser(userId)
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        return web.json_response({'user': user})
    
    async def handleEnableUser(self, request: web.Request) -> web.Response:
        """Re-enable a disabled user (admin only)"""
        if not self._checkAdminAuth(request):
            return web.json_response({'error': 'Admin access required'}, status=403)
        
        userId = request.match_info.get('userId')
        user = self.authManager.enableUser(userId)
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        return web.json_response({'user': user})
    
    async def handleSetUserRole(self, request: web.Request) -> web.Response:
        """Set user role (admin only)"""
        if not self._checkAdminAuth(request):
            return web.json_response({'error': 'Admin access required'}, status=403)
        
        userId = request.match_info.get('userId')
        try:
            data = await request.json()
            role = data.get('role')
        except:
            return web.json_response({'error': 'Invalid request body'}, status=400)
        
        if role not in ('admin', 'operator'):
            return web.json_response({'error': 'Invalid role'}, status=400)
        
        user = self.authManager.setUserRole(userId, role)
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        return web.json_response({'user': user})
    
    async def handleDeleteUser(self, request: web.Request) -> web.Response:
        """Delete a user (admin only)"""
        if not self._checkAdminAuth(request):
            return web.json_response({'error': 'Admin access required'}, status=403)
        
        userId = request.match_info.get('userId')
        if self.authManager.deleteUser(userId):
            return web.json_response({'message': 'User deleted'})
        else:
            return web.json_response({'error': 'User not found'}, status=404)
    
    async def handleResetPassword(self, request: web.Request) -> web.Response:
        """Reset user password (admin only)"""
        if not self._checkAdminAuth(request):
            return web.json_response({'error': 'Admin access required'}, status=403)
        
        userId = request.match_info.get('userId')
        try:
            data = await request.json()
            newPassword = data.get('password')
        except:
            return web.json_response({'error': 'Invalid request body'}, status=400)
        
        if not newPassword or len(newPassword) < 6:
            return web.json_response({'error': 'Password must be at least 6 characters'}, status=400)
        
        user = self.authManager.resetPassword(userId, newPassword)
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        return web.json_response({'user': user, 'message': 'Password reset successfully'})
    
    async def _serveIndexHtml(self, request: web.Request) -> web.Response:
        """Serve index.html from UI directory. Redirect to /login if auth enabled and not authenticated."""
        # Server-side auth gate: prevents main page flash before JS redirect
        if self.authManager.enabled:
            user = self._getAuthFromCookie(request)
            if not user:
                raise web.HTTPFound('/login?redirect=%2F')
        uiPath = Path(__file__).parent.parent / 'ui' / 'html' / 'index.html'
        if not uiPath.exists():
            return web.Response(status=404, text='Not found')
        return web.FileResponse(uiPath)
    
    async def _serveLoginHtml(self, request: web.Request) -> web.Response:
        """Serve login.html"""
        uiPath = Path(__file__).parent.parent / 'ui' / 'html' / 'login.html'
        if not uiPath.exists():
            return web.Response(status=404, text='Not found')
        return web.FileResponse(uiPath)
    
    async def _serveRegisterHtml(self, request: web.Request) -> web.Response:
        """Serve register.html"""
        uiPath = Path(__file__).parent.parent / 'ui' / 'html' / 'register.html'
        if not uiPath.exists():
            return web.Response(status=404, text='Not found')
        return web.FileResponse(uiPath)
    
    async def _serveAdminHtml(self, request: web.Request) -> web.Response:
        """Serve admin.html. Redirect to /login if not authenticated or not admin."""
        if self.authManager.enabled:
            user = self._getAuthFromCookie(request)
            if not user:
                raise web.HTTPFound('/login?redirect=%2Fadmin')
            if user.get('role') != 'admin':
                raise web.HTTPFound('/')
        uiPath = Path(__file__).parent.parent / 'ui' / 'html' / 'admin.html'
        if not uiPath.exists():
            return web.Response(status=404, text='Not found')
        return web.FileResponse(uiPath)
    
    async def _serveApprovalPendingHtml(self, request: web.Request) -> web.Response:
        """Serve approval-pending.html"""
        uiPath = Path(__file__).parent.parent / 'ui' / 'html' / 'approval-pending.html'
        if not uiPath.exists():
            return web.Response(status=404, text='Not found')
        return web.FileResponse(uiPath)
    
    async def handleExportDownload(self, request: web.Request) -> web.Response:
        """Handle export file download"""
        exportId = request.match_info.get('exportId')
        exportDir = Path(self.config.get('exportDir', './nova/exports'))
        zipPath = exportDir / f"{exportId}.zip"
        
        if not zipPath.exists():
            return web.Response(status=404, text='Export not found')
        
        return web.FileResponse(
            zipPath,
            headers={'Content-Disposition': f'attachment; filename="{exportId}.zip"'}
        )
    
    # =========================================================================
    # Stream API Handlers (Phase 8.1 - Multi-protocol)
    # =========================================================================
    
    async def handleListStreams(self, request: web.Request) -> web.Response:
        """
        List all stream definitions with runtime status.
        
        Returns stream shields for UI discovery.
        """
        definitions = self.streamStore.list()
        
        streams = []
        for defn in definitions:
            status = self.streamManager.getStatus(defn.streamId)
            streams.append({
                # Shield identity
                'systemId': 'stream',
                'containerId': 'streams',
                'uniqueId': defn.streamId,
                'entityType': 'stream',
                # Definition
                **defn.toDict(),
                # Runtime status
                'running': status['running'] if status else False,
                'connectionCount': status['connectionCount'] if status else 0,
                'bound': status['bound'] if status else False,
                'boundInstance': status['boundInstance'] if status else None,
                'selectionSummary': defn.selectionSummary()
            })
        
        # Add Setup Streams system entity
        setupStreams = {
            'systemId': 'stream',
            'containerId': 'system',
            'uniqueId': 'setupStreams',
            'entityType': 'setup-streams',
            'displayName': 'Setup Streams',
            'description': 'Create and manage output streams'
        }
        
        return web.json_response({
            'streams': streams,
            'setupStreams': setupStreams
        })
    
    async def handleCreateStream(self, request: web.Request) -> web.Response:
        """Create a new stream definition"""
        try:
            data = await request.json()
            
            # Validate required fields
            if not data.get('name'):
                return web.json_response({'error': 'Name is required'}, status=400)
            
            protocol = data.get('protocol', 'tcp')
            validProtocols = ('tcp', 'websocket', 'udp')
            if protocol not in validProtocols:
                return web.json_response({'error': f'Invalid protocol. Must be one of: {validProtocols}'}, status=400)
            
            # Get endpoint (port for tcp/udp, path for websocket)
            endpoint = data.get('endpoint') or data.get('port')  # Support legacy 'port' field
            if not endpoint:
                if protocol == 'websocket':
                    return web.json_response({'error': 'WebSocket path is required'}, status=400)
                else:
                    return web.json_response({'error': 'Port is required'}, status=400)
            
            endpoint = str(endpoint)
            
            # Validate endpoint based on protocol
            if protocol in ('tcp', 'udp'):
                try:
                    port = int(endpoint)
                    if port <= 80:
                        return web.json_response({'error': 'Port must be greater than 80'}, status=400)
                except ValueError:
                    return web.json_response({'error': 'Port must be a valid number'}, status=400)
            
            # Check endpoint availability
            if not self.streamStore.isEndpointAvailable(protocol, endpoint):
                if protocol == 'websocket':
                    return web.json_response({'error': f'WebSocket path "{endpoint}" is already in use'}, status=400)
                else:
                    return web.json_response({'error': f'Port {endpoint} is already in use by another {protocol.upper()} stream'}, status=400)
            
            # Validate lane
            lane = data.get('lane', 'raw')
            validLanes = ('raw', 'parsed', 'metadata', 'ui', 'command')
            if not lane or lane not in validLanes:
                return web.json_response({'error': f'Invalid lane. Must be one of: {validLanes}'}, status=400)
            
            definition = StreamDefinition(
                streamId=str(uuid.uuid4())[:8],
                name=data['name'],
                protocol=protocol,
                endpoint=endpoint,
                lane=lane,
                systemIdFilter=data.get('systemIdFilter'),
                containerIdFilter=data.get('containerIdFilter'),
                uniqueIdFilter=data.get('uniqueIdFilter'),
                messageTypeFilter=data.get('messageTypeFilter'),
                outputFormat=data.get('outputFormat', 'payloadOnly'),
                backpressure=data.get('backpressure', 'catchUp'),
                enabled=data.get('enabled', True),
                createdBy=data.get('createdBy', 'system'),
                visibility=data.get('visibility', 'private')
            )
            
            # Save to store
            definition = self.streamStore.create(definition)
            
            # Start if enabled
            if definition.enabled:
                success, err = await self.streamManager.startStream(definition)
                if not success:
                    return web.json_response({
                        'error': f'Stream created but failed to start: {err}',
                        'streamId': definition.streamId
                    }, status=500)
            
            return web.json_response({
                'success': True,
                'streamId': definition.streamId,
                'stream': definition.toDict()
            })
        
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except Exception as e:
            self.log.error(f"[Server] Create stream error: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def handleGetStream(self, request: web.Request) -> web.Response:
        """Get a stream definition with runtime status"""
        streamId = request.match_info.get('streamId')
        
        definition = self.streamStore.get(streamId)
        if not definition:
            return web.json_response({'error': 'Stream not found'}, status=404)
        
        status = self.streamManager.getStatus(streamId)
        
        return web.json_response({
            'systemId': 'stream',
            'containerId': 'streams',
            'uniqueId': streamId,
            'entityType': 'stream',
            **definition.toDict(),
            'running': status['running'] if status else False,
            'connectionCount': status['connectionCount'] if status else 0,
            'bound': status['bound'] if status else False,
            'boundInstance': status['boundInstance'] if status else None,
            'selectionSummary': definition.selectionSummary()
        })
    
    async def handleUpdateStream(self, request: web.Request) -> web.Response:
        """Update a stream definition"""
        streamId = request.match_info.get('streamId')
        
        definition = self.streamStore.get(streamId)
        if not definition:
            return web.json_response({'error': 'Stream not found'}, status=404)
        
        try:
            data = await request.json()
            
            # Update fields
            if 'name' in data:
                definition.name = data['name']
            if 'port' in data:
                port = int(data['port'])
                if port <= 80:
                    return web.json_response({'error': 'Port must be greater than 80'}, status=400)
                if not self.streamStore.isPortAvailable(port, excludeStreamId=streamId):
                    return web.json_response({'error': f'Port {port} is already in use'}, status=400)
                definition.port = port
            if 'lane' in data:
                definition.lane = data['lane']
            if 'systemIdFilter' in data:
                definition.systemIdFilter = data['systemIdFilter']
            if 'containerIdFilter' in data:
                definition.containerIdFilter = data['containerIdFilter']
            if 'uniqueIdFilter' in data:
                definition.uniqueIdFilter = data['uniqueIdFilter']
            if 'messageTypeFilter' in data:
                definition.messageTypeFilter = data['messageTypeFilter']
            if 'outputFormat' in data:
                definition.outputFormat = data['outputFormat']
            if 'backpressure' in data:
                definition.backpressure = data['backpressure']
            if 'enabled' in data:
                definition.enabled = data['enabled']
            if 'visibility' in data:
                definition.visibility = data['visibility']
            
            # Save to store
            definition = self.streamStore.update(definition)
            
            # Restart if running
            if self.streamManager.getStream(streamId):
                success, err = await self.streamManager.restartStream(definition)
                if not success:
                    return web.json_response({
                        'error': f'Stream updated but failed to restart: {err}'
                    }, status=500)
            
            return web.json_response({
                'success': True,
                'stream': definition.toDict()
            })
        
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except Exception as e:
            self.log.error(f"[Server] Update stream error: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def handleDeleteStream(self, request: web.Request) -> web.Response:
        """Delete a stream definition"""
        streamId = request.match_info.get('streamId')
        
        # Stop if running
        await self.streamManager.stopStream(streamId)
        
        # Delete from store
        deleted = self.streamStore.delete(streamId)
        if not deleted:
            return web.json_response({'error': 'Stream not found'}, status=404)
        
        return web.json_response({'success': True})
    
    async def handleStartStream(self, request: web.Request) -> web.Response:
        """Start a stream"""
        streamId = request.match_info.get('streamId')
        
        definition = self.streamStore.get(streamId)
        if not definition:
            return web.json_response({'error': 'Stream not found'}, status=404)
        
        success, err = await self.streamManager.startStream(definition)
        if not success:
            return web.json_response({'error': err}, status=400)
        
        # Update enabled state
        definition.enabled = True
        self.streamStore.update(definition)
        
        return web.json_response({'success': True})
    
    async def handleStopStream(self, request: web.Request) -> web.Response:
        """Stop a stream"""
        streamId = request.match_info.get('streamId')
        
        definition = self.streamStore.get(streamId)
        if not definition:
            return web.json_response({'error': 'Stream not found'}, status=404)
        
        success, err = await self.streamManager.stopStream(streamId)
        if not success:
            return web.json_response({'error': err}, status=400)
        
        # Update enabled state
        definition.enabled = False
        self.streamStore.update(definition)
        
        return web.json_response({'success': True})
    
    async def handleBindStream(self, request: web.Request) -> web.Response:
        """Bind a stream to a WebSocket instance's timeline"""
        streamId = request.match_info.get('streamId')
        
        try:
            data = await request.json()
            instanceId = data.get('instanceId')
            
            if not instanceId:
                return web.json_response({'error': 'instanceId required'}, status=400)
            
            success = self.streamManager.bindToTimeline(streamId, instanceId)
            if not success:
                return web.json_response({'error': 'Stream not found or not running'}, status=404)
            
            return web.json_response({'success': True, 'boundInstance': instanceId})
        
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)
    
    async def handleUnbindStream(self, request: web.Request) -> web.Response:
        """Unbind a stream from timeline"""
        streamId = request.match_info.get('streamId')
        
        success = self.streamManager.unbindFromTimeline(streamId)
        if not success:
            return web.json_response({'error': 'Stream not found'}, status=404)
        
        return web.json_response({'success': True})
    
    # =========================================================================
    # WebSocket Handler
    # =========================================================================
    
    async def handleWsStream(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket stream connection (output stream protocol)"""
        path = request.match_info['path']
        
        server = self.streamManager.getWsStream(path)
        if not server:
            raise web.HTTPNotFound(text=f"WebSocket stream '{path}' not found")
        
        if not server._running:
            raise web.HTTPServiceUnavailable(text=f"WebSocket stream '{path}' is not running")
        
        return await server.handleConnection(request)
    
    async def handleWebSocket(self, request: web.Request) -> web.WebSocketResponse:
        """
        Handle WebSocket connection from client.
        
        Auth is via httpOnly cookie (same-origin, set during login).
        Cookie is automatically sent by browser on WebSocket upgrade request.
        """
        connId = str(uuid.uuid4())
        self.log.info(f"[Server] WebSocket connection: {connId} from {request.remote}")
        
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        conn = ClientConnection(connId, ws)
        self.connections[connId] = conn
        
        # Authenticate via httpOnly cookie (same-origin)
        from nova.server.auth import COOKIE_NAME
        token = request.cookies.get(COOKIE_NAME)
        
        userId, username, role, authError = await self._authenticate(token)
        
        if authError:
            await ws.send_json({'type': 'authResponse', 'success': False, 'error': authError})
            await ws.close(code=4401, message=authError.encode())
            return ws
        
        conn.userId = userId
        conn.username = username
        conn.role = role
        
        self.log.info(f"[Server] Authenticated via cookie: {connId}, user={username}, role={role}")
        
        await ws.send_json({
            'type': 'authResponse',
            'success': True,
            'connId': connId,
            'username': username,
            'userId': userId,
            'role': role
        })
        
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handleMessage(conn, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    self.log.error(f"[Server] WebSocket error: {ws.exception()}")
        
        except Exception as e:
            self.log.error(f"[Server] Error in WebSocket loop: {e}", exc_info=True)
        
        finally:
            await self._cleanupConnection(connId)
            self.log.info(f"[Server] Disconnected: {connId}")
        
        return ws
    
    async def _authenticate(self, token: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Authenticate client. Returns: (userId, username, role, error)"""
        if not self.authManager.enabled:
            return 'anonymous', 'anonymous', 'operator', None
        
        if not token:
            return None, None, None, 'Authentication required'
        
        payload = self.authManager.validateToken(token)
        if not payload:
            return None, None, None, 'Invalid or expired token'
        
        return payload.get('userId'), payload.get('username'), payload.get('role'), None
    
    async def _handleMessage(self, conn: ClientConnection, data: str):
        """Handle incoming message from client"""
        try:
            message = orjson.loads(data)
            msgType = message.get('type')
            
            if msgType == 'query':
                await self._handleQuery(conn, message)
            elif msgType == 'startStream':
                await self._handleStartStream(conn, message)
            elif msgType == 'cancelStream':
                await self._handleCancelStream(conn, message)
            elif msgType == 'command':
                await self._handleCommand(conn, message)
            elif msgType == 'chat':
                await self._handleChat(conn, message)
            elif msgType == 'export':
                await self._handleExport(conn, message)
            elif msgType == 'listExports':
                await self._handleListExports(conn, message)
            else:
                await conn.sendError(f"Unknown message type: {msgType}")
        
        except Exception as e:
            self.log.error(f"[Server] Error handling message: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    async def _handleQuery(self, conn: ClientConnection, message: Dict[str, Any]):
        """Handle query request"""
        try:
            startTime = message['startTime']
            stopTime = message['stopTime']
            timelineMode = TimelineMode(message.get('timelineMode', 'live'))
            timebase = message.get('timebase', 'canonical')
            filters = message.get('filters')
            
            response = await self.ipcClient.query(
                clientConnId=conn.connId,
                startTime=startTime,
                stopTime=stopTime,
                timelineMode=timelineMode,
                timebase=timebase,
                filters=filters
            )
            
            response['type'] = 'queryResponse'
            await conn.sendMessage(response)
        
        except Exception as e:
            self.log.error(f"[Server] Query error: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    async def _handleStartStream(self, conn: ClientConnection, message: Dict[str, Any]):
        """Handle startStream request"""
        try:
            playbackRequestId = str(uuid.uuid4())
            startTime = message['startTime']
            stopTime = message.get('stopTime')
            rate = message.get('rate', 1.0)
            timelineMode = TimelineMode(message.get('timelineMode', 'live'))
            timebase = message.get('timebase', 'canonical')
            filters = message.get('filters')
            
            conn.setActiveStream(playbackRequestId)
            
            # Notify bound output streams to restart with the new leader cursor
            self.streamManager.onInstanceStreamRestart(conn.connId)
            
            async def chunkHandler(chunk: Dict[str, Any]):
                chunkPlaybackId = chunk.get('playbackRequestId')
                if conn.shouldDiscardChunk(chunkPlaybackId):
                    return
                chunk['type'] = 'streamChunk'
                await conn.sendMessage(chunk)
            
            await self.ipcClient.startStream(
                clientConnId=conn.connId,
                playbackRequestId=playbackRequestId,
                startTime=startTime,
                stopTime=stopTime,
                rate=rate,
                timelineMode=timelineMode,
                timebase=timebase,
                filters=filters,
                chunkHandler=chunkHandler
            )
            
            await conn.sendMessage({
                'type': 'streamStarted',
                'playbackRequestId': playbackRequestId
            })
        
        except Exception as e:
            self.log.error(f"[Server] StartStream error: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    async def _handleCancelStream(self, conn: ClientConnection, message: Dict[str, Any]):
        """Handle cancelStream request"""
        try:
            conn.clearActiveStream()
            await self.ipcClient.cancelStream(conn.connId)
            await conn.sendMessage({'type': 'streamCanceled'})
        except Exception as e:
            self.log.error(f"[Server] CancelStream error: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    async def _handleChat(self, conn: ClientConnection, message: Dict[str, Any]):
        """
        Handle chat message - store as metadata truth event, broadcast to clients.
        
        Phase 9: Chat messages are stored as MetadataEvent(messageType='ChatMessage')
        in the Metadata lane, making them replayable like all other truth events.
        """
        try:
            from datetime import datetime, timezone
            
            channel = message.get('channel', 'ops')
            text = message.get('text', '')
            
            if not text:
                return
            
            # Generate timestamp
            now = datetime.now(timezone.utc)
            timestampMs = int(now.timestamp() * 1000)
            timestampIso = now.isoformat()
            
            # Chat payload (stored in Metadata lane)
            chatPayload = {
                'text': text,
                'username': conn.username,
                'userId': conn.userId,
                'channel': channel
            }
            
            # Ingest to Core as metadata truth event
            response = await self.ipcClient.ingestMetadata(
                clientConnId=conn.connId,
                scopeId=self.config.get('scopeId', 'default'),
                messageType='ChatMessage',
                effectiveTime=timestampIso,
                sourceTruthTime=timestampIso,
                systemId='nova-server',
                containerId='chat',
                uniqueId=channel,  # Channel name is the identity
                payload=chatPayload
            )
            
            eventId = response.get('eventId', f"{timestampMs}-{conn.connId[:8]}")
            
            # Broadcast to all connected clients (for realtime display)
            chatMsg = {
                'type': 'chat',
                'messageId': eventId,
                'channel': channel,
                'text': text,
                'username': conn.username,
                'userId': conn.userId,
                'timestamp': timestampMs
            }
            
            for connId, client in self.connections.items():
                try:
                    await client.sendMessage(chatMsg)
                except Exception as e:
                    self.log.warning(f"[Server] Failed to send chat to {connId}: {e}")
            
            self.log.info(f"[Server] Chat stored: {conn.username} in #{channel}: {text[:50]}...")
            
        except Exception as e:
            self.log.error(f"[Server] Chat error: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    async def _handleCommand(self, conn: ClientConnection, message: Dict[str, Any]):
        """Handle command request"""
        try:
            commandId = message['commandId']
            targetId = message['targetId']
            commandType = message['commandType']
            payload = message['payload']
            timelineMode = TimelineMode(message.get('timelineMode', 'live'))
            
            if not self.authManager.checkPermission(conn.role, 'command'):
                await conn.sendError("Permission denied", message.get('requestId'))
                return
            
            if timelineMode == TimelineMode.REPLAY:
                await conn.sendError("Commands not allowed in REPLAY mode", message.get('requestId'))
                return
            
            response = await self.ipcClient.submitCommand(
                clientConnId=conn.connId,
                commandId=commandId,
                targetId=targetId,
                commandType=commandType,
                payload=payload,
                timelineMode=timelineMode,
                userId=conn.userId
            )
            
            response['type'] = 'commandResponse'
            await conn.sendMessage(response)
        
        except Exception as e:
            self.log.error(f"[Server] Command error: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    async def _handleExport(self, conn: ClientConnection, message: Dict[str, Any]):
        """Handle export request"""
        try:
            startTime = message['startTime']
            stopTime = message['stopTime']
            timebase = message.get('timebase', 'canonical')
            filters = message.get('filters')
            
            if not self.authManager.checkPermission(conn.role, 'command'):
                await conn.sendError("Permission denied", message.get('requestId'))
                return
            
            response = await self.ipcClient.export(
                clientConnId=conn.connId,
                startTime=startTime,
                stopTime=stopTime,
                timebase=timebase,
                filters=filters
            )
            
            response['type'] = 'exportResponse'
            await conn.sendMessage(response)
        
        except Exception as e:
            self.log.error(f"[Server] Export error: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    async def _handleListExports(self, conn: ClientConnection, message: Dict[str, Any]):
        """Handle listExports request"""
        try:
            response = await self.ipcClient.listExports(conn.connId)
            response['type'] = 'exportsListResponse'
            await conn.sendMessage(response)
        except Exception as e:
            self.log.error(f"[Server] ListExports error: {e}", exc_info=True)
            await conn.sendError(str(e))
    
    # =========================================================================
    # Stream Data Callback (multi-protocol)
    # =========================================================================
    
    async def _streamDataCallback(self, streamId: str, lane: str,
                                systemIdFilter: Optional[str] = None,
                                containerIdFilter: Optional[str] = None,
                                uniqueIdFilter: Optional[str] = None,
                                messageTypeFilter: Optional[str] = None,
                                bindingCallback: Optional[callable] = None):
        """
        Stream data from Core for output streams (TCP, WebSocket, UDP).
        
        Yields events as they arrive from Core.
        Uses scopeId='stream' for stream operations.
        
        If bound via bindingCallback, follows the bound WebSocket's cursor.
        Otherwise, LIVE-follow.
        """
        filters = {'lanes': [lane]}
        if systemIdFilter:
            filters['systemId'] = systemIdFilter
        if containerIdFilter:
            filters['containerId'] = containerIdFilter
        if uniqueIdFilter:
            filters['uniqueId'] = uniqueIdFilter
        if messageTypeFilter:
            filters['messageType'] = messageTypeFilter
        
        # Get bound instance ID if binding callback provided
        boundInstanceId = bindingCallback() if bindingCallback else None
        
        self.log.info(f"[Server] Stream callback: stream={streamId}, lane={lane}, bound={boundInstanceId or 'LIVE'}")
        
        chunkCount = 0
        eventCount = 0
        lastLogTime = time.perf_counter()
        
        # Stream from Core using proper IPC signature
        async for chunk in self.ipcClient.streamRaw(
            scopeId=f'stream-{streamId}',
            filters=filters,
            boundInstanceId=boundInstanceId
        ):
            chunkEvents = chunk.get('events', [])
            chunkCount += 1
            eventCount += len(chunkEvents)
            
            for event in chunkEvents:
                yield event
            
            # Log throughput periodically
            now = time.perf_counter()
            if now - lastLogTime >= 10.0:  # Log every 10 seconds
                elapsed = now - lastLogTime
                evtRate = eventCount / elapsed
                chunkRate = chunkCount / elapsed
                self.log.info(f"[Server] TCP {streamId}: {evtRate:.1f} evt/s, {chunkRate:.1f} chunks/s")
                eventCount = 0
                chunkCount = 0
                lastLogTime = now
    
    async def _cleanupConnection(self, connId: str):
        """Cleanup connection resources"""
        conn = self.connections.pop(connId, None)
        if not conn:
            return
        
        # Cancel any active stream
        if conn.activePlaybackId:
            try:
                await self.ipcClient.cancelStream(connId)
            except Exception as e:
                self.log.error(f"[Server] Error canceling stream: {e}")
        
        # Unbind any streams bound to this connection
        self.streamManager.onInstanceDisconnect(connId)
        
        self.log.info(f"[Server] Cleaned up connection: {connId}")
    
    def _getTimelineMode(self) -> TimelineMode:
        """Get current timeline mode"""
        return self._timelineMode
    
    def setTimelineMode(self, mode: TimelineMode):
        """Set timeline mode (called by Core via IPC)"""
        self._timelineMode = mode
        self.log.info(f"[Server] Timeline mode: {mode.value}")
    
    # =========================================================================
    # Presentation API Handlers (Phase 10)
    # =========================================================================
    
    async def handleListModels(self, request: web.Request) -> web.Response:
        """List available .gltf/.glb models for presentation overrides."""
        models = self.presentationStore.getAvailableModels()
        return web.json_response({'models': models})
    
    async def handleGetPresentation(self, request: web.Request) -> web.Response:
        """
        Get presentation overrides for current user.
        
        Query params:
        - scopeId: optional, filter to specific scope
        
        If single effective scope: returns that scope's data
        If multi scope + no scopeId: aggregates all (each item includes scopeId)
        If multi scope + scopeId: returns that scope's data
        If user has 'ALL' access: returns all overrides across all scopes
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        scopeId, error = self._resolveRequestScope(request, user, requireForWrite=False)
        if error:
            return error
        
        if scopeId:
            # Single scope requested
            overrides = self.presentationStore.getUserOverrides(user['username'], scopeId)
            result = {
                uniqueId: {**pres.toDict(), 'scopeId': scopeId}
                for uniqueId, pres in overrides.items()
            }
            return web.json_response({'overrides': result})
        else:
            # Multi-scope aggregation
            effectiveScopes = self._getEffectiveScopes(user)
            result = {}
            
            # Check if user has 'ALL' access - load all scopes
            if 'ALL' in effectiveScopes:
                allOverrides = self.presentationStore.getAllUserOverrides(user['username'])
                for scope, overrides in allOverrides.items():
                    for uniqueId, pres in overrides.items():
                        result[uniqueId] = {**pres.toDict(), 'scopeId': scope}
            else:
                for scope in effectiveScopes:
                    overrides = self.presentationStore.getUserOverrides(user['username'], scope)
                    for uniqueId, pres in overrides.items():
                        result[uniqueId] = {**pres.toDict(), 'scopeId': scope}
            
            return web.json_response({'overrides': result})
    
    async def handleSetPresentation(self, request: web.Request) -> web.Response:
        """
        Set user presentation override for an entity.
        
        Query params:
        - scopeId: required if user has multiple scopes
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        # Debug logging
        requestedScope = request.query.get('scopeId')
        effectiveScopes = self._getEffectiveScopes(user)
        self.log.info(f"[Presentation] SET user={user.get('username')} requestedScope={requestedScope} effectiveScopes={effectiveScopes}")
        
        scopeId, error = self._resolveRequestScope(request, user, requireForWrite=True)
        if error:
            return error
        
        uniqueId = request.match_info['uniqueId']
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        success = self.presentationStore.setUserOverride(
            username=user['username'],
            scopeId=scopeId,
            uniqueId=uniqueId,
            overrides=data
        )
        
        if success:
            # Broadcast update to all connected clients
            await self._broadcastPresentationUpdate(scopeId, uniqueId, data, user['username'])
            return web.json_response({'status': 'ok', 'scopeId': scopeId})
        else:
            return web.json_response({'error': 'Invalid override data'}, status=400)
    
    async def handleDeletePresentation(self, request: web.Request) -> web.Response:
        """
        Delete user presentation override.
        
        Query params:
        - scopeId: required if user has multiple scopes
        - key: optional, delete only specific key
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        scopeId, error = self._resolveRequestScope(request, user, requireForWrite=True)
        if error:
            return error
        
        uniqueId = request.match_info['uniqueId']
        key = request.query.get('key')
        
        success = self.presentationStore.deleteUserOverride(
            username=user['username'],
            scopeId=scopeId,
            uniqueId=uniqueId,
            key=key
        )
        
        if success:
            # Broadcast deletion
            await self._broadcastPresentationUpdate(scopeId, uniqueId, None, user['username'], deleted=True)
        
        return web.json_response({'status': 'ok' if success else 'not found', 'scopeId': scopeId})
    
    async def handleGetPresentationDefaults(self, request: web.Request) -> web.Response:
        """
        Get admin default presentations.
        
        Query params:
        - scopeId: optional, filter to specific scope
        
        If user has 'ALL' access and no scopeId: returns all defaults across all scopes
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        scopeId, error = self._resolveRequestScope(request, user, requireForWrite=False)
        if error:
            return error
        
        if scopeId:
            # Single scope requested
            defaults = self.presentationStore.getAdminDefaults(scopeId)
            result = {
                uniqueId: {**pres.toDict(), 'scopeId': scopeId}
                for uniqueId, pres in defaults.items()
            }
            return web.json_response({'defaults': result})
        else:
            # Multi-scope aggregation
            effectiveScopes = self._getEffectiveScopes(user)
            result = {}
            
            # Check if user has 'ALL' access - load all admin defaults
            if 'ALL' in effectiveScopes:
                allDefaults = self.presentationStore.getAllAdminDefaults()
                for scope, defaults in allDefaults.items():
                    for uniqueId, pres in defaults.items():
                        result[uniqueId] = {**pres.toDict(), 'scopeId': scope}
            else:
                for scope in effectiveScopes:
                    defaults = self.presentationStore.getAdminDefaults(scope)
                    for uniqueId, pres in defaults.items():
                        result[uniqueId] = {**pres.toDict(), 'scopeId': scope}
            
            return web.json_response({'defaults': result})
    
    async def handleSetPresentationDefaults(self, request: web.Request) -> web.Response:
        """
        Set admin default presentation (admin only).
        
        Query params:
        - scopeId: required if admin has multiple scopes
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        if user.get('role') != 'admin':
            return web.json_response({'error': 'Admin required'}, status=403)
        
        scopeId, error = self._resolveRequestScope(request, user, requireForWrite=True)
        if error:
            return error
        
        uniqueId = request.match_info['uniqueId']
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        success = self.presentationStore.setAdminDefault(
            scopeId=scopeId,
            uniqueId=uniqueId,
            overrides=data
        )
        
        if success:
            # Broadcast admin default update to all clients
            await self._broadcastPresentationUpdate(scopeId, uniqueId, data, user['username'], isDefault=True)
            return web.json_response({'status': 'ok', 'scopeId': scopeId})
        else:
            return web.json_response({'error': 'Invalid override data'}, status=400)
    
    async def handleDeletePresentationDefaults(self, request: web.Request) -> web.Response:
        """
        Delete admin default presentation (admin only).
        
        Query params:
        - scopeId: required if admin has multiple scopes
        - key: optional, delete only specific key
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        if user.get('role') != 'admin':
            return web.json_response({'error': 'Admin required'}, status=403)
        
        scopeId, error = self._resolveRequestScope(request, user, requireForWrite=True)
        if error:
            return error
        
        uniqueId = request.match_info['uniqueId']
        key = request.query.get('key')
        
        success = self.presentationStore.deleteAdminDefault(
            scopeId=scopeId,
            uniqueId=uniqueId,
            key=key
        )
        
        if success:
            await self._broadcastPresentationUpdate(scopeId, uniqueId, None, user['username'], isDefault=True, deleted=True)
        
        return web.json_response({'status': 'ok' if success else 'not found', 'scopeId': scopeId})
    
    async def handleListScopes(self, request: web.Request) -> web.Response:
        """List available scopes (admin only)."""
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        if user.get('role') != 'admin':
            return web.json_response({'error': 'Admin required'}, status=403)
        
        serverScopes = self.config.get('allowedScopes', [self.config.get('scopeId', 'default')])
        return web.json_response({'scopes': serverScopes})
    
    async def handleSetUserScopes(self, request: web.Request) -> web.Response:
        """Set user's allowed scopes (admin only)."""
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        if user.get('role') != 'admin':
            return web.json_response({'error': 'Admin required'}, status=403)
        
        userId = request.match_info['userId']
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        scopes = data.get('scopes')
        if not isinstance(scopes, list):
            return web.json_response({'error': 'scopes must be array'}, status=400)
        
        updated = self.authManager.userStore.updateScopes(userId, scopes)
        if updated:
            return web.json_response({'status': 'ok', 'user': updated})
        else:
            return web.json_response({'error': 'User not found'}, status=404)
    
    # =========================================================================
    # Run API Handlers (Phase 11: per-user runs/replays)
    # =========================================================================
    
    async def handleListRuns(self, request: web.Request) -> web.Response:
        """
        List all runs for current user.
        
        GET /api/runs → list summary
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        username = user.get('username')
        runs = self.runStore.listRuns(username)
        
        return web.json_response({'runs': runs})
    
    async def handleCreateRun(self, request: web.Request) -> web.Response:
        """
        Create a new run.
        
        POST /api/runs → create run (server assigns runNumber if not provided)
        
        Body: any run data (core fields + manifest-defined fields)
        Server sets timebase based on node mode.
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        username = user.get('username')
        
        # Server sets timebase (from config, based on node mode)
        nodeMode = self.config.get('mode', 'payload')
        data['timebase'] = 'source' if nodeMode == 'payload' else 'canonical'
        
        run = self.runStore.createRun(username=username, runData=data)
        
        return web.json_response({'run': run.toDict()}, status=201)
    
    async def handleGetRun(self, request: web.Request) -> web.Response:
        """
        Get a specific run.
        
        GET /api/runs/{runNumber}
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        username = user.get('username')
        
        try:
            runNumber = int(request.match_info['runNumber'])
        except ValueError:
            return web.json_response({'error': 'Invalid runNumber'}, status=400)
        
        run = self.runStore.getRun(username, runNumber)
        if not run:
            return web.json_response({'error': 'Run not found'}, status=404)
        
        return web.json_response({'run': run.toDict()})
    
    async def handleUpdateRun(self, request: web.Request) -> web.Response:
        """
        Update a run (overwrite run.json).
        
        PUT /api/runs/{runNumber} → overwrite run.json (last write wins)
        
        If runName changes, folder is renamed.
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        username = user.get('username')
        
        try:
            runNumber = int(request.match_info['runNumber'])
        except ValueError:
            return web.json_response({'error': 'Invalid runNumber'}, status=400)
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        # Client cannot change timebase in Phase 11
        if 'timebase' in data:
            del data['timebase']
        
        run = self.runStore.updateRun(username, runNumber, data)
        if not run:
            return web.json_response({'error': 'Run not found or validation failed'}, status=404)
        
        return web.json_response({'run': run.toDict()})
    
    async def handleDeleteRun(self, request: web.Request) -> web.Response:
        """
        Delete a run folder.
        
        DELETE /api/runs/{runNumber}
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        username = user.get('username')
        
        try:
            runNumber = int(request.match_info['runNumber'])
        except ValueError:
            return web.json_response({'error': 'Invalid runNumber'}, status=400)
        
        success = self.runStore.deleteRun(username, runNumber)
        if not success:
            return web.json_response({'error': 'Run not found'}, status=404)
        
        return web.json_response({'status': 'ok'})
    
    async def handleCreateBundle(self, request: web.Request) -> web.Response:
        """
        Generate and return bundle.zip for a run.
        
        POST /api/runs/{runNumber}/bundle → regenerate bundle.zip and return it
        
        Always regenerates (never reuses existing zip).
        Uses Phase 6 driver export pipeline for [startTime, stopTime] in run's timebase.
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        username = user.get('username')
        
        try:
            runNumber = int(request.match_info['runNumber'])
        except ValueError:
            return web.json_response({'error': 'Invalid runNumber'}, status=400)
        
        # Get run
        run = self.runStore.getRun(username, runNumber)
        if not run:
            return web.json_response({'error': 'Run not found'}, status=404)
        
        # Validate times
        if not run.startTimeSec or not run.stopTimeSec:
            return web.json_response({'error': 'Run must have start and stop times'}, status=400)
        
        if run.stopTimeSec <= run.startTimeSec:
            return web.json_response({'error': 'Stop time must be after start time'}, status=400)
        
        # Get bundle path
        bundlePath = self.runStore.setBundlePath(username, runNumber)
        if not bundlePath:
            return web.json_response({'error': 'Failed to get bundle path'}, status=500)
        
        # Convert seconds to microseconds for Core IPC
        startTimeUs = int(run.startTimeSec * 1_000_000)
        stopTimeUs = int(run.stopTimeSec * 1_000_000)
        
        try:
            # Use the existing export method on ipcClient
            response = await self.ipcClient.export(
                clientConnId=f"bundle-{username}-{runNumber}",
                startTime=startTimeUs,
                stopTime=stopTimeUs,
                timebase=run.timebase,
                filters=None
            )
            
            if response.get('error'):
                return web.json_response({'error': response['error']}, status=500)
            
            # The export creates a zip in exports/ directory
            exportId = response.get('exportId')
            if not exportId:
                return web.json_response({'error': 'Export failed - no exportId'}, status=500)
            
            # Copy the export zip to the run's bundle.zip
            exportZipPath = Path(f"./nova/exports/{exportId}.zip")
            
            import shutil
            import zipfile
            
            if exportZipPath.exists():
                shutil.copy(exportZipPath, bundlePath)
            else:
                # Create empty zip if export has no data
                with zipfile.ZipFile(bundlePath, 'w') as zf:
                    pass
            
            # Always add run.json to the bundle (even if no data events)
            # Get the run folder to find run.json
            runFolder = self.runStore._findRunFolder(username, runNumber)
            if runFolder:
                runJsonPath = runFolder / 'run.json'
                if runJsonPath.exists():
                    # Add run.json to the zip
                    with zipfile.ZipFile(bundlePath, 'a') as zf:
                        zf.write(runJsonPath, 'run.json')
            
            # Return the zip file
            if not bundlePath.exists():
                return web.json_response({'error': 'Bundle generation failed'}, status=500)
            
            return web.FileResponse(
                bundlePath,
                headers={'Content-Disposition': f'attachment; filename="run{runNumber}_bundle.zip"'}
            )
            
        except asyncio.TimeoutError:
            return web.json_response({'error': 'Export timed out'}, status=504)
        except Exception as e:
            self.log.error(f"[Server] Bundle export failed: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def handleGetRunSettings(self, request: web.Request) -> web.Response:
        """
        Get user's run settings (default runType, last runName, etc.).
        
        GET /api/runs/settings
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        username = user.get('username')
        settings = self.runStore.getUserRunSettings(username)
        return web.json_response({'settings': settings})
    
    async def handleSetRunSettings(self, request: web.Request) -> web.Response:
        """
        Save user's run settings.
        
        PUT /api/runs/settings
        """
        user = self._getAuthUser(request)
        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        username = user.get('username')
        self.runStore.setUserRunSettings(username, data)
        return web.json_response({'status': 'ok'})

    async def _broadcastPresentationUpdate(
        self, 
        scopeId: str, 
        uniqueId: str, 
        data: Optional[Dict[str, Any]], 
        username: str,
        isDefault: bool = False,
        deleted: bool = False
    ):
        """Broadcast presentation change to all connected clients."""
        msg = {
            'type': 'presentationUpdate',
            'scopeId': scopeId,
            'uniqueId': uniqueId,
            'data': data,
            'username': username,
            'isDefault': isDefault,
            'deleted': deleted
        }
        
        for connId, client in self.connections.items():
            try:
                # Only send to clients who can access this scope
                clientScopes = self._getEffectiveScopes({
                    'allowedScopes': client.role == 'admin' and ['ALL'] or []  
                })
                # For simplicity, broadcast to all - UI can filter
                await client.sendMessage(msg)
            except Exception as e:
                self.log.warning(f"[Server] Failed to broadcast to {connId}: {e}")
    
    def _getAuthUser(self, request: web.Request) -> Optional[Dict[str, Any]]:
        """Get authenticated user from request cookie."""
        if not self.authManager.enabled:
            return {'username': 'anonymous', 'role': 'admin', 'allowedScopes': ['ALL']}
        
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        
        return self.authManager.validateToken(token)
    
    def _getEffectiveScopes(self, user: Dict[str, Any]) -> set:
        """
        Compute effective scopes for user.
        
        effectiveScopes = userAllowedScopes ∩ serverAllowedScopes
        'ALL' means unrestricted access to all scopes (no filtering).
        """
        userScopes = user.get('allowedScopes', [])
        
        # ALL means unrestricted access - return special marker
        if 'ALL' in userScopes:
            return {'ALL'}  # Special marker meaning "any scope is allowed"
        
        serverScopes = self.config.get('allowedScopes', [self.config.get('scopeId', 'default')])
        
        # Intersection
        return set(userScopes) & set(serverScopes)
    
    def _resolveRequestScope(
        self, 
        request: web.Request, 
        user: Dict[str, Any],
        requireForWrite: bool = False
    ) -> tuple[Optional[str], Optional[web.Response]]:
        """
        Resolve scope for a request.
        
        Returns (scopeId, None) on success, or (None, errorResponse) on failure.
        
        Rules:
        - If user has 'ALL' access, any scopeId is allowed
        - If request has ?scopeId=, validate it's in effectiveScopes
        - If no scopeId and single scope: use it
        - If no scopeId and multi scope:
          - GET: return None (caller aggregates)
          - PUT/DELETE: return 400 "scopeId required"
        """
        effectiveScopes = self._getEffectiveScopes(user)
        
        if not effectiveScopes:
            return None, web.json_response({'error': 'No accessible scopes'}, status=403)
        
        requestedScope = request.query.get('scopeId')
        
        # 'ALL' marker means any scope is allowed
        hasAllAccess = 'ALL' in effectiveScopes
        
        if requestedScope:
            # Validate requested scope (unless user has ALL access)
            if not hasAllAccess and requestedScope not in effectiveScopes:
                return None, web.json_response({'error': f'Scope not accessible: {requestedScope}'}, status=403)
            return requestedScope, None
        
        # No scope specified
        if hasAllAccess:
            # User with ALL access must specify scope for writes
            if requireForWrite:
                return None, web.json_response(
                    {'error': 'scopeId required for write operations'}, 
                    status=400
                )
            # For reads, return None to signal aggregation
            return None, None
        
        if len(effectiveScopes) == 1:
            return next(iter(effectiveScopes)), None
        
        # Multi-scope, no explicit scope
        if requireForWrite:
            return None, web.json_response(
                {'error': 'scopeId required', 'availableScopes': list(effectiveScopes)}, 
                status=400
            )
        
        # GET with multi-scope: return None to signal aggregation needed
        return None, None
