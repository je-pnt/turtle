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
    onlineWindowUs: 5 * 1_000_000,   // default 5s in microseconds, overwritten by config
    cleanupWindowUs: 120 * 1_000_000  // default 120s in microseconds, overwritten by config
};

// Initialize
function initEntities() {
    // Load config thresholds (set by initTimeline from /config response)
    // Thresholds are applied when config arrives via applyActivityConfig()
    setInterval(updateOnlineStatuses, 1000);
    console.log('[Shields] Initialized');
}

/**
 * Apply activity thresholds from server config.
 * Called by timeline.js after fetching /config.
 */
function applyActivityConfig(config) {
    if (config.onlineWindowSeconds) {
        shields.onlineWindowUs = config.onlineWindowSeconds * 1_000_000;
    }
    if (config.cleanupWindowSeconds) {
        shields.cleanupWindowUs = config.cleanupWindowSeconds * 1_000_000;
    }
    console.log('[Shields] Activity config: online=' + (shields.onlineWindowUs / 1_000_000) + 's, cleanup=' + (shields.cleanupWindowUs / 1_000_000) + 's');
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
            lastSeenUs: parseTimeToUs(event.sourceTruthTime || event.canonicalTruthTime)
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
    
    // Step 6: Update lastSeenUs for existing shields
    var existing = shields.byKey.get(key);
    if (existing) {
        var newTimeUs = parseTimeToUs(event.sourceTruthTime || event.canonicalTruthTime);
        if (newTimeUs) {
            existing.lastSeenUs = newTimeUs;
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

/**
 * Parse ISO timestamp or numeric time to microseconds.
 * Returns 0 if unparseable.
 */
function parseTimeToUs(timeVal) {
    if (!timeVal) return 0;
    if (typeof timeVal === 'number') return timeVal;
    var dt = new Date(timeVal);
    if (isNaN(dt.getTime())) return 0;
    return dt.getTime() * 1000;
}

/**
 * Get activity baseline: always timeline.currentTimeUs.
 * Same domain as lastSeenUs (microseconds, server time).
 * No mode branching — works in LIVE and REWIND identically.
 */
function getActivityBaseline() {
    return (window.timeline && window.timeline.currentTimeUs) ? window.timeline.currentTimeUs : Date.now() * 1000;
}

// Online status — single algorithm for shields and cards
function isEntityOnline(entity) {
    if (!entity.lastSeenUs) return false;
    var baseline = getActivityBaseline();
    return (baseline - entity.lastSeenUs) < shields.onlineWindowUs;
}

// Cleanup check — entity should be removed from UI
function shouldCleanup(entity) {
    if (!entity.lastSeenUs) return false;
    var baseline = getActivityBaseline();
    return (baseline - entity.lastSeenUs) > shields.cleanupWindowUs;
}

// Expose for cards to use
window.isEntityOnline = isEntityOnline;

function updateOnlineStatuses() {
    // Cleanup stale entities
    var staleKeys = [];
    shields.byKey.forEach(function(entity, key) {
        if (shouldCleanup(entity)) {
            staleKeys.push(key);
        }
    });
    if (staleKeys.length > 0) {
        for (var i = 0; i < staleKeys.length; i++) {
            var staleKey = staleKeys[i];
            var entity = shields.byKey.get(staleKey);
            shields.byKey.delete(staleKey);
            // Remove from tree
            if (entity) {
                var containers = shields.tree.get(entity.systemId);
                if (containers) {
                    var entities = containers.get(entity.containerId);
                    if (entities) {
                        entities.delete(entity.uniqueId);
                        if (entities.size === 0) containers.delete(entity.containerId);
                    }
                    if (containers.size === 0) shields.tree.delete(entity.systemId);
                }
            }
            // Also close the corresponding card
            if (window.closeCard) {
                window.closeCard(staleKey);
            }
        }
        renderShields();
    }

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
    
    // Update card indicators to match shield status
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
        var sysCollapsed = localStorage.getItem('shield:system:' + systemId + ':collapsed') === 'true';
        html += '<div class="shield-system' + (sysCollapsed ? ' collapsed' : '') + '" data-system="' + systemId + '">';
        html += '<div class="system-header" onclick="toggleSystemCollapse(\'' + systemId + '\')"><span class="system-chevron">' + (sysCollapsed ? '▶' : '▼') + '</span>';
        html += '<span class="system-name">' + systemId + '</span></div>';
        html += '<div class="system-children">';
        
        containers.forEach(function(entities, containerId) {
            var contCollapsed = localStorage.getItem('shield:container:' + systemId + ':' + containerId + ':collapsed') === 'true';
            html += '<div class="shield-container' + (contCollapsed ? ' collapsed' : '') + '" data-container="' + containerId + '">';
            html += '<div class="container-header" onclick="toggleContainerCollapse(\'' + systemId + '\', \'' + containerId + '\')"><span class="container-chevron">' + (contCollapsed ? '▶' : '▼') + '</span>';
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

/**
 * Toggle system collapse state (Phase 11)
 */
function toggleSystemCollapse(systemId) {
    var key = 'shield:system:' + systemId + ':collapsed';
    var isCollapsed = localStorage.getItem(key) === 'true';
    localStorage.setItem(key, !isCollapsed);
    renderShields();
}

/**
 * Toggle container collapse state (Phase 11)
 */
function toggleContainerCollapse(systemId, containerId) {
    var key = 'shield:container:' + systemId + ':' + containerId + ':collapsed';
    var isCollapsed = localStorage.getItem(key) === 'true';
    localStorage.setItem(key, !isCollapsed);
    renderShields();
}

/**
 * Apply presentation overrides (displayName) to shields after initial load.
 * Fetches all presentation data and applies displayName to matching shields.
 * Called once after entity metadata query populates shields.
 */
async function applyPresentationOverrides() {
    if (shields.byKey.size === 0) return;
    
    // Group shields by scopeId to minimize API calls
    var scopeIds = new Set();
    shields.byKey.forEach(function(entity) {
        var scopeId = entity.systemId + '|' + entity.containerId;
        scopeIds.add(scopeId);
    });
    
    for (var scopeId of scopeIds) {
        try {
            var response = await fetch('/api/presentation?scopeId=' + encodeURIComponent(scopeId));
            if (!response.ok) continue;
            var data = await response.json();
            var overrides = data.overrides || {};
            
            // Apply displayName overrides to matching shields
            for (var uniqueId in overrides) {
                var key = scopeId + '|' + uniqueId;
                var entity = shields.byKey.get(key);
                if (entity && overrides[uniqueId].displayName) {
                    entity.displayName = overrides[uniqueId].displayName;
                }
            }
        } catch (e) {
            console.warn('[Shields] Failed to load presentation for scope:', scopeId, e);
        }
    }
    
    // Re-render with updated names
    if (shields.byKey.size > 0) {
        renderShields();
        if (window.renderAllCards) window.renderAllCards();
    }
    console.log('[Shields] Presentation overrides applied');
}

// Exports
window.initEntities = initEntities;
window.processEntityEvent = processEntityEvent;
window.selectEntity = selectEntity;
window.getSelectedEntity = getSelectedEntity;
window.toggleSystemCollapse = toggleSystemCollapse;
window.toggleContainerCollapse = toggleContainerCollapse;
window.applyActivityConfig = applyActivityConfig;
window.applyPresentationOverrides = applyPresentationOverrides;
window.renderShields = renderShields;
window.shields = shields;
