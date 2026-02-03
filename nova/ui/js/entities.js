/**
 * NOVA Shields - Minimum viable shield contract
 * 
 * Shield Contract (minimum required fields):
 *   - key: string (systemId|containerId|uniqueId)
 *   - displayName: string
 * 
 * Shield is created from: Metadata lane + messageType ends with "Descriptor"
 * Shield identity: event.systemId + event.containerId + event.uniqueId
 */

// State
const shields = {
    byKey: new Map(),      // key → {key, displayName, systemId, containerId, uniqueId}
    tree: new Map(),       // systemId → containerId → uniqueId → entity
    selected: null,
    onlineTtlMs: 5000
};

// Initialize
function initEntities() {
    setInterval(updateOnlineStatuses, 1000);
    console.log('[Shields] Initialized');
}

// Process event - SINGLE PATH
function processEntityEvent(event) {
    // Step 1: Read fields directly from event object
    var sysId = event['systemId'];
    var contId = event['containerId'];
    var uniqId = event['uniqueId'];
    var lane = event['lane'];
    var msgType = event['messageType'];
    
    // Step 2: Validate - must have all three identity fields
    if (!sysId || !contId || !uniqId) {
        return; // Skip events without complete identity
    }
    
    // Step 3: Skip internal NOVA events
    if (sysId === 'nova') {
        return;
    }
    
    // Step 4: Build key
    var key = sysId + '|' + contId + '|' + uniqId;
    
    // Step 5: Create shield only from Descriptor metadata events
    if (lane === 'metadata' && msgType && msgType.endsWith('Descriptor')) {        
        // Get displayName from payload, fallback to uniqueId
        var displayName = uniqId;
        if (event.payload && event.payload.displayName) {
            displayName = event.payload.displayName;
        }
        
        // Create entity object
        var entity = {
            key: key,
            systemId: sysId,
            containerId: contId,
            uniqueId: uniqId,
            displayName: displayName,
            entityType: event.payload ? event.payload.entityType : null,
            lastSeen: event.sourceTruthTime || event.canonicalTruthTime
        };
        
        // Store in flat map
        shields.byKey.set(key, entity);
        
        // Store in tree
        if (!shields.tree.has(sysId)) {
            shields.tree.set(sysId, new Map());
        }
        var containers = shields.tree.get(sysId);
        if (!containers.has(contId)) {
            containers.set(contId, new Map());
        }
        containers.get(contId).set(uniqId, entity);
        
        // Render
        renderShields();
        if (shields.byKey.size === 1) {
            selectEntity(key);
        }
        return;
    }
    
    // Step 6: Update lastSeen for existing shields
    var existing = shields.byKey.get(key);
    if (existing) {
        var newTime = event.sourceTruthTime || event.canonicalTruthTime;
        if (newTime) {
            existing.lastSeen = newTime;
        }
    }
}

// Select entity and show detailed panel
function selectEntity(key) {
    shields.selected = key;
    renderShields();
    var entity = shields.byKey.get(key);
    if (entity && window.updateCard) {
        window.updateCard(entity);
        // Auto-expand detailed panel if collapsed
        var panel = document.getElementById('detailedPanel');
        if (panel && panel.classList.contains('hidden')) {
            panel.classList.remove('hidden');
            localStorage.setItem('sidebar:right:visible', 'true');
        }
    }
}

// Get icon
function getEntityIcon(entityType) {
    if (!entityType) return '📦';
    if (window.cards && window.cards.manifests) {
        for (var manifest of window.cards.manifests.values()) {
            if (manifest.entityTypes && manifest.entityTypes.includes(entityType)) {
                return manifest.icon;
            }
        }
    }
    return '📦';
}

// Online status - used by both shields and cards
function isEntityOnline(entity) {
    if (!entity.lastSeen) return false;
    var lastSeenMs = new Date(entity.lastSeen).getTime();
    return (Date.now() - lastSeenMs) < shields.onlineTtlMs;
}

// Expose for cards to use
window.isEntityOnline = isEntityOnline;

function updateOnlineStatuses() {
    // Update shield indicators
    var indicators = document.querySelectorAll('.shield-status');
    indicators.forEach(function(indicator) {
        var item = indicator.closest('.shield-item');
        if (!item) return;
        var key = item.dataset.key;
        if (!key) return;
        var entity = shields.byKey.get(key);
        if (!entity) return;
        var online = isEntityOnline(entity);
        indicator.className = 'shield-status ' + (online ? 'online' : 'offline');
    });
    
    // Update card indicators to match shield status - ONE LINE
    document.querySelectorAll('.online-indicator').forEach(function(ind) {
        var card = ind.closest('.entity-card');
        if (card && card.dataset.entityKey) {
            var entity = shields.byKey.get(card.dataset.entityKey);
            if (entity) {
                var online = isEntityOnline(entity);
                ind.className = 'online-indicator ' + (online ? 'online' : 'offline');
            }
        }
    });
}

// Render
function renderShields() {
    var container = document.getElementById('entitiesList');
    if (!container) return;
    
    var html = '';
    
    shields.tree.forEach(function(containers, systemId) {
        html += '<div class="shield-system">';
        html += '<div class="system-header"><span class="system-chevron">▼</span>';
        html += '<span class="system-name">' + systemId + '</span></div>';
        html += '<div class="system-children">';
        
        containers.forEach(function(entities, containerId) {
            html += '<div class="shield-container">';
            html += '<div class="container-header"><span class="container-chevron">▼</span>';
            html += '<span class="container-name">' + containerId + '</span></div>';
            html += '<div class="container-children">';
            
            entities.forEach(function(entity, uniqueId) {
                var isSelected = shields.selected === entity.key;
                var isOnline = isEntityOnline(entity);
                var icon = getEntityIcon(entity.entityType);
                
                html += '<div class="shield-item ' + (isSelected ? 'selected' : '') + '" ';
                html += 'data-key="' + entity.key + '" ';
                html += 'onclick="selectEntity(\'' + entity.key + '\')">';
                html += '<span class="shield-icon">' + icon + '</span>';
                html += '<span class="shield-name">' + entity.displayName + '</span>';
                html += '<span class="shield-status ' + (isOnline ? 'online' : 'offline') + '"></span>';
                html += '</div>';
            });
            
            html += '</div></div>';
        });
        
        html += '</div></div>';
    });
    
    if (html === '') {
        html = '<div class="shields-empty">';
        html += '<div class="empty-icon">📡</div>';
        html += '<div class="empty-text">Waiting for entities...</div>';
        html += '</div>';
    }
    
    container.innerHTML = html;
}

function getSelectedEntity() {
    if (!shields.selected) return null;
    return shields.byKey.get(shields.selected);
}

// Exports
window.initEntities = initEntities;
window.processEntityEvent = processEntityEvent;
window.selectEntity = selectEntity;
window.getSelectedEntity = getSelectedEntity;
window.shields = shields;
