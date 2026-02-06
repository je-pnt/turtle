/**
 * NOVA Streams - TCP Stream Management
 * 
 * Renders stream shields in #streamsList using same pattern as entities.
 * Uses existing card system from cards.js for stream cards.
 * No custom card rendering - reuses cards.js renderCard().
 */

// State
const streams = {
    definitions: new Map()  // streamId → definition
};

/**
 * Initialize streams module
 */
function initStreams() {
    loadStreams();
    setInterval(loadStreams, 5000);
    console.log('[Streams] Initialized');
}

/**
 * Load streams from API
 */
async function loadStreams() {
    try {
        var response = await fetch('/api/streams');
        if (!response.ok) return;
        
        var data = await response.json();
        
        streams.definitions.clear();
        for (var stream of data.streams) {
            streams.definitions.set(stream.streamId, stream);
        }
        
        renderStreamsList();
    } catch (e) {
        console.error('[Streams] Error:', e);
    }
}

/**
 * Render streams in sidebar using shield pattern
 */
function renderStreamsList() {
    var container = document.getElementById('streamsList');
    if (!container) return;
    
    var html = '';
    var streamIcon = '<img src="/ui/icons/stream.svg" class="shield-svg-icon" alt="">';
    
    // Setup Streams shield (entry point)
    html += '<div class="shield-item" data-key="setup-streams" onclick="openSetupStreamsCard()">';
    html += '<span class="shield-icon">' + streamIcon + '</span>';
    html += '<span class="shield-name">Setup Streams</span>';
    html += '</div>';
    
    // Individual stream shields
    streams.definitions.forEach(function(stream) {
        var statusClass = stream.running ? 'online' : 'offline';
        var bindIcon = stream.bound ? ' 🔗' : '';
        
        html += '<div class="shield-item" data-key="stream-' + stream.streamId + '" ';
        html += 'onclick="openStreamCard(\'' + stream.streamId + '\')">';
        html += '<span class="shield-icon">' + streamIcon + '</span>';
        html += '<span class="shield-name">' + escapeHtml(stream.name) + bindIcon + '</span>';
        html += '<span class="shield-status ' + statusClass + '"></span>';
        html += '</div>';
    });
    
    container.innerHTML = html;
}

/**
 * Build identity options HTML from shields tree
 */
function buildIdentityOptions(type) {
    var options = '<option value="">Any</option>';
    var seen = new Set();
    
    if (window.shields && window.shields.tree) {
        shields.tree.forEach(function(containers, sysId) {
            if (type === 'system' && !seen.has(sysId)) {
                seen.add(sysId);
                options += '<option value="' + sysId + '">' + sysId + '</option>';
            }
            containers.forEach(function(entities, contId) {
                if (type === 'container' && !seen.has(contId)) {
                    seen.add(contId);
                    options += '<option value="' + contId + '">' + contId + '</option>';
                }
                entities.forEach(function(entity, uniqId) {
                    if (type === 'unique' && !seen.has(uniqId)) {
                        seen.add(uniqId);
                        options += '<option value="' + uniqId + '">' + uniqId + '</option>';
                    }
                });
            });
        });
    }
    return options;
}

/**
 * Open Setup Streams card using existing card system
 */
function openSetupStreamsCard() {
    var entity = {
        systemId: 'stream',
        containerId: 'system', 
        uniqueId: 'setupStreams',
        displayName: 'Setup Streams',
        entityType: 'setup-streams'
    };
    openCard(entity);
}

/**
 * Open individual stream card
 */
function openStreamCard(streamId) {
    var stream = streams.definitions.get(streamId);
    if (!stream) return;
    
    var entity = {
        systemId: 'stream',
        containerId: 'streams',
        uniqueId: streamId,
        displayName: stream.name,
        entityType: 'stream',
        ...stream
    };
    openCard(entity);
}

/**
 * Create a new stream (called from card action)
 */
async function createStream() {
    var name = document.getElementById('newStreamName')?.value?.trim();
    var protocol = document.getElementById('newStreamProtocol')?.value || 'tcp';
    var lane = document.getElementById('newStreamLane')?.value || 'raw';
    var format = document.getElementById('newStreamFormat')?.value || 'hierarchyPerMessage';
    var systemFilter = document.getElementById('newStreamSystemFilter')?.value || null;
    var containerFilter = document.getElementById('newStreamContainerFilter')?.value || null;
    var uniqueFilter = document.getElementById('newStreamUniqueFilter')?.value || null;
    
    // Get endpoint based on protocol
    var endpoint;
    if (protocol === 'websocket') {
        endpoint = document.getElementById('newStreamPath')?.value?.trim();
    } else {
        endpoint = document.getElementById('newStreamPort')?.value;
    }
    
    var errorDiv = document.getElementById('streamError');
    if (errorDiv) errorDiv.textContent = '';
    
    if (!name) { showStreamError('Name required'); return; }
    
    // Validate endpoint based on protocol
    if (protocol === 'websocket') {
        if (!endpoint) { showStreamError('Path required'); return; }
        if (!/^[a-zA-Z0-9_-]+$/.test(endpoint)) { showStreamError('Path must be alphanumeric'); return; }
    } else if (protocol === 'udp') {
        // UDP allows host:port or just port
        if (!endpoint) { showStreamError('Target required (host:port or port)'); return; }
        if (endpoint.includes(':')) {
            var parts = endpoint.split(':');
            var port = parseInt(parts[parts.length - 1], 10);
            if (!port || port <= 0 || port > 65535) { showStreamError('Invalid port'); return; }
        } else {
            var port = parseInt(endpoint, 10);
            if (!port || port <= 0 || port > 65535) { showStreamError('Invalid port'); return; }
        }
    } else {
        // TCP
        var port = parseInt(endpoint, 10);
        if (!port || port <= 80) { showStreamError('Port must be > 80'); return; }
        endpoint = String(port);
    }
    
    if (format === 'payloadOnly' && (!systemFilter || !containerFilter || !uniqueFilter)) {
        showStreamError('Payload Only requires all identity filters');
        return;
    }
    
    try {
        var response = await fetch('/api/streams', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name, protocol: protocol, endpoint: endpoint, lane: lane, outputFormat: format,
                systemIdFilter: systemFilter, containerIdFilter: containerFilter, uniqueIdFilter: uniqueFilter
            })
        });
        
        var result = await response.json();
        if (!response.ok) { showStreamError(result.error || 'Failed'); return; }
        
        await loadStreams();
        if (document.getElementById('newStreamName')) document.getElementById('newStreamName').value = '';
        if (document.getElementById('newStreamPort')) document.getElementById('newStreamPort').value = '';
        if (document.getElementById('newStreamPath')) document.getElementById('newStreamPath').value = '';
        
    } catch (e) { showStreamError(e.message); }
}

async function startOutputStream(streamId) {
    try {
        var response = await fetch('/api/streams/' + streamId + '/start', { method: 'POST' });
        var result = await response.json();
        if (!response.ok) {
            alert('Start failed: ' + (result.error || 'Unknown error'));
            return;
        }
    } catch (e) {
        alert('Start failed: ' + e.message);
        return;
    }
    await loadStreams();
    refreshStreamCard(streamId);
}

async function stopOutputStream(streamId) {
    try {
        var response = await fetch('/api/streams/' + streamId + '/stop', { method: 'POST' });
        var result = await response.json();
        if (!response.ok) {
            alert('Stop failed: ' + (result.error || 'Unknown error'));
            return;
        }
    } catch (e) {
        alert('Stop failed: ' + e.message);
        return;
    }
    await loadStreams();
    refreshStreamCard(streamId);
}

async function deleteStream(streamId) {
    if (!confirm('Delete this stream?')) return;
    await fetch('/api/streams/' + streamId, { method: 'DELETE' });
    await loadStreams();
    closeCard('stream|streams|' + streamId);
}

async function bindStream(streamId) {
    var instanceId = (window.wsState && window.wsState.connId) || 'unknown';
    await fetch('/api/streams/' + streamId + '/bind', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instanceId: instanceId })
    });
    await loadStreams();
    refreshStreamCard(streamId);
}

async function unbindStream(streamId) {
    await fetch('/api/streams/' + streamId + '/unbind', { method: 'POST' });
    await loadStreams();
    refreshStreamCard(streamId);
}

/**
 * Re-render an open stream card with updated definition data.
 * After bind/unbind/start/stop, the card needs fresh data from streams.definitions.
 */
function refreshStreamCard(streamId) {
    var stream = streams.definitions.get(streamId);
    if (!stream) return;
    var entityKey = 'stream|streams|' + streamId;
    var uiData = window.cards?.uiState?.get(entityKey);
    if (uiData) {
        // Update entity reference with fresh stream data
        uiData._entity = Object.assign({}, uiData._entity, stream, {
            systemId: 'stream',
            containerId: 'streams',
            uniqueId: streamId,
            displayName: stream.name,
            entityType: 'stream'
        });
        if (window.renderAllCards) window.renderAllCards();
    }
}

function showStreamError(msg) {
    var errorDiv = document.getElementById('streamError');
    if (errorDiv) errorDiv.textContent = msg;
}

function escapeHtml(text) {
    if (!text) return '';
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Exports
window.initStreams = initStreams;
window.streams = streams;
window.openSetupStreamsCard = openSetupStreamsCard;
window.openStreamCard = openStreamCard;
window.createStream = createStream;
window.startOutputStream = startOutputStream;
window.stopOutputStream = stopOutputStream;
window.deleteStream = deleteStream;
window.bindStream = bindStream;
window.unbindStream = unbindStream;
window.buildIdentityOptions = buildIdentityOptions;
