/**
 * NOVA WebSocket Client
 * 
 * Architecture (nova architecture.md):
 * - Single WebSocket connection to NOVA server
 * - Handles auth, queries, streams, commands
 * - Routes events to display module
 * 
 * Design (guidelines.md):
 * - Single message handling path
 * - No legacy/parallel code
 * - Clear logging at each stage
 * 
 * Phase 9: Cookie-based auth
 * - No token in query params or messages
 * - Cookie is sent automatically on WebSocket upgrade (same-origin)
 * - Server reads cookie and validates JWT
 */

const wsState = {
    ws: null,
    connected: false,
    connId: null,
    reconnecting: false,
    reconnectTimer: null
};

function initWebSocket() {
    // Connect triggered by init.js after auth check
}

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    
    // Cookie is sent automatically on same-origin WebSocket connection
    const wsUrl = `${protocol}//${host}/ws`;
    
    console.log('[WS] Connecting to:', wsUrl);
    
    try {
        wsState.ws = new WebSocket(wsUrl);
        
        wsState.ws.onopen = () => {
            updateConnectionStatus(true);
            wsState.reconnecting = false;
            // Auth is handled via httpOnly cookie (sent with upgrade request)
            updateStatus('Connected', 'success');
        };
        
        wsState.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleMessage(msg);
            } catch (e) {
                console.error('[WS] Parse error:', e);
            }
        };
        
        wsState.ws.onerror = (error) => {
            console.error('[WS] Error:', error);
            updateStatus('WebSocket error', 'error');
        };
        
        wsState.ws.onclose = (event) => {
            updateConnectionStatus(false);
            updateStatus('Disconnected', 'warning');
            wsState.reconnecting = true;
            
            // Don't reconnect if auth failure (4401 or 4403)
            if (event.code === 4401 || event.code === 4403) {
                console.log('[WS] Auth failure, not reconnecting');
                if (window.NovaAuth) {
                    window.NovaAuth.clearAuth();
                }
                window.location.href = '/login';
                return;
            }
            
            // Reconnect if authenticated
            if (window.NovaAuth && window.NovaAuth.isAuthenticated()) {
                wsState.reconnectTimer = setTimeout(connectWebSocket, 3000);
            }
        };
    } catch (e) {
        console.error('[WS] Connection failed:', e);
        updateStatus('Connection failed', 'error');
    }
}

function disconnectWebSocket() {
    if (wsState.reconnectTimer) {
        clearTimeout(wsState.reconnectTimer);
        wsState.reconnectTimer = null;
    }
    
    if (wsState.ws) {
        wsState.ws.close();
        wsState.ws = null;
    }
    
    wsState.connected = false;
    wsState.connId = null;
    updateConnectionStatus(false);
}

/**
 * Handle incoming WebSocket message
 * SINGLE entry point for all messages
 */
function handleMessage(msg) {
    switch (msg.type) {
        case 'authResponse':
            handleAuthResponse(msg);
            break;
            
        case 'queryResponse':
            handleQueryResponse(msg);
            break;
            
        case 'streamChunk':
            handleStreamChunk(msg);
            break;
            
        case 'streamComplete':
            updateStatus('Stream complete', 'info');
            if (window.timeline) {
                window.timeline.isPlaying = false;
                window.updateTimelineUI && window.updateTimelineUI();
            }
            break;
            
        case 'streamStarted':
            if (window.timeline) {
                window.timeline.playbackRequestId = msg.playbackRequestId;
            }
            break;
            
        case 'streamCanceled':
        case 'ack':
            break;
        
        case 'chat':
            // Handle incoming chat message
            if (window.NovaChat) {
                window.NovaChat.handleMessage(msg);
            }
            break;
        
        case 'commandResponse':
            // Show feedback for command results
            if (msg.error) {
                updateStatus(`Command error: ${msg.error}`, 'error');
                if (window.showToast) showToast(msg.error, 'error');
            } else if (msg.result) {
                // Command completed with result data
                const result = msg.result;
                if (result.message) {
                    updateStatus(result.message, result.status === 'error' ? 'error' : 'success');
                    if (window.showToast) showToast(result.message, result.status === 'error' ? 'error' : 'success');
                } else {
                    updateStatus('Command completed', 'success');
                }
            } else {
                updateStatus('Command sent', 'success');
            }
            break;
        
        case 'presentationUpdate':
            // Real-time presentation sync from another session/user
            if (window.NovaMap && window.NovaMap.handlePresentationUpdate) {
                window.NovaMap.handlePresentationUpdate(msg);
            }
            break;
            
        case 'error':
            updateStatus(`Error: ${msg.error || msg.message}`, 'error');
            break;
            
        default:
            console.warn('[WS] Unknown message type:', msg.type);
    }
}

function handleAuthResponse(msg) {
    if (msg.success) {
        wsState.connected = true;
        wsState.connId = msg.connId;
        updateStatus(`Authenticated as ${msg.username}`, 'success');
        console.log('[WS] Authenticated, connId:', msg.connId);
    } else {
        updateStatus(`Auth failed: ${msg.error}`, 'error');
        disconnectWebSocket();
        clearAuth();
    }
}

/**
 * Handle query response
 * Log what we receive, then route to display
 */
function handleQueryResponse(msg) {
    console.log('[WS] === QUERY RESPONSE ===');
    console.log('[WS] Total events:', msg.totalCount);
    
    if (msg.events && msg.events.length > 0) {
        // Log first few events to see structure
        console.log('[WS] Sample event structure:', JSON.stringify(msg.events[0], null, 2).substring(0, 500));
        
        // Log Descriptor events specifically
        const descriptors = msg.events.filter(e => e.messageType && e.messageType.endsWith('Descriptor'));
        console.log('[WS] Descriptor events found:', descriptors.length);
        
        for (const e of descriptors) {
            console.log('[WS] Descriptor event:', {
                lane: e.lane,
                messageType: e.messageType,
                systemId: e.systemId,
                containerId: e.containerId,
                uniqueId: e.uniqueId,
                'payload.displayName': e.payload?.displayName
            });
        }
        
        // Route to display
        appendEvents(msg.events);
        updateStatus(`Query: ${msg.totalCount} events`, 'success');
    } else {
        updateStatus('Query: no events found', 'info');
    }
}

/**
 * Handle stream chunk
 * Check fencing, then route to display
 */
function handleStreamChunk(msg) {
    // Fencing: ignore stale chunks
    if (window.timeline && msg.playbackRequestId !== window.timeline.playbackRequestId) {
        console.warn('[WS] Stale chunk ignored (playbackId mismatch)');
        return;
    }
    
    if (msg.events && msg.events.length > 0) {
        // Reduced logging - only log periodically
        wsState.chunkCount = (wsState.chunkCount || 0) + 1;
        if (wsState.chunkCount % 50 === 1) {
            const laneCounts = {};
            msg.events.forEach(e => {
                laneCounts[e.lane] = (laneCounts[e.lane] || 0) + 1;
            });
            if (msg.timestamp) {
                const cursorDate = new Date(msg.timestamp / 1000);
                const gapMs = wsState.lastChunkTimestamp 
                    ? (msg.timestamp - wsState.lastChunkTimestamp) / 1000 
                    : 0;
                console.log('[WS] Stream chunk #' + wsState.chunkCount + ':', msg.events.length, 'events, lanes:', laneCounts, 
                           'cursor:', cursorDate.toISOString(), 'gap:', gapMs.toFixed(0) + 'ms');
            }
        }
        if (msg.timestamp) {
            wsState.lastChunkTimestamp = msg.timestamp;
        }
        
        // Attach server cursor for timeline sync
        if (msg.timestamp) {
            for (const event of msg.events) {
                event._serverCursor = msg.timestamp;
            }
        }
        appendEvents(msg.events);
    }
}

function sendWebSocketMessage(msg) {
    if (!wsState.ws || !wsState.connected || wsState.reconnecting) {
        console.warn('[WS] Cannot send - not connected');
        return;
    }
    
    if (wsState.ws.readyState !== WebSocket.OPEN) {
        console.warn('[WS] Cannot send - socket not open');
        return;
    }
    
    try {
        wsState.ws.send(JSON.stringify(msg));
    } catch (e) {
        console.error('[WS] Send failed:', e);
        updateStatus('Send failed', 'error');
    }
}

/**
 * Generic message sender for any WebSocket message
 */
function sendWsMessage(msg) {
    if (!wsState.ws || wsState.ws.readyState !== WebSocket.OPEN) {
        console.warn('[WS] Cannot send, not connected');
        return false;
    }
    
    try {
        wsState.ws.send(JSON.stringify(msg));
        return true;
    } catch (e) {
        console.error('[WS] Send failed:', e);
        return false;
    }
}

// Export for chat module
window.sendWsMessage = sendWsMessage;

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('connectionStatus');
    if (statusEl) {
        statusEl.textContent = connected ? '🟢 Connected' : '⚫ Disconnected';
        statusEl.className = connected ? 'connected' : 'disconnected';
    }
}

function updateStatus(message, level = 'info') {
    const prefix = { success: '✓', error: '✗', warning: '⚠', info: 'ℹ' }[level] || 'ℹ';
    console.log(`[WS] ${prefix} ${message}`);
    
    const statusEl = document.getElementById('statusMessage');
    if (statusEl) {
        statusEl.textContent = message;
        statusEl.className = `status-${level}`;
    }
}

// Exports
window.wsState = wsState;
window.connectWebSocket = connectWebSocket;
window.disconnectWebSocket = disconnectWebSocket;
window.sendWebSocketMessage = sendWebSocketMessage;
