/**
 * NOVA Cards - Multi-card exploration panel
 * 
 * Architecture (uiDataPlan.md):
 * - Cards are manifest-driven
 * - Multiple cards visible, scrollable, closeable
 * - Actions trigger commands directly (no confirmation)
 * - Commands blocked in REWIND mode
 */

const cards = {
    manifests: new Map(),           // cardType → manifest
    entityTypeToCard: new Map(),    // entityType → cardType
    uiState: new Map(),             // entityKey → UI data
    openCards: [],                  // Array of entityKeys in display order
    commands: new Map(),            // commandId → status
    tableStates: {},                // stateKey → collapsed boolean
    configResults: new Map(),       // entityKey → {filename, successCount, totalCount}
    collapsedCards: new Set(),      // entityKeys of collapsed cards
    dragState: null                 // Current drag operation state
};

// ============================================================================
// Initialization
// ============================================================================

async function initCards() {
    try {
        const response = await fetch('/config');
        if (response.ok) {
            const config = await response.json();
            if (config.cardManifests) {
                for (const manifest of config.cardManifests) {
                    cards.manifests.set(manifest.cardType, manifest);
                    for (const entityType of manifest.entityTypes || []) {
                        cards.entityTypeToCard.set(entityType, manifest.cardType);
                    }
                }
                console.log(`[Cards] Loaded ${cards.manifests.size} manifests`);
            }
        }
    } catch (e) {
        console.warn('[Cards] Failed to load manifests:', e);
    }
    
    // Event delegation for card buttons - capture phase for priority
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        
        const action = btn.dataset.action;
        const entityKey = btn.dataset.entityKey;
        
        console.log('[Cards] Button clicked:', action, 'for entity:', entityKey);
        
        if (!entityKey) {
            console.warn('[Cards] No entityKey on button');
            return;
        }
        
        e.stopPropagation();
        e.preventDefault();
        
        console.log('[Cards] Handling action:', action);
        
        switch (action) {
            case 'edit-name':
                editCardName(entityKey);
                break;
            case 'open-presentation':
                openPresentation(entityKey);
                break;
            case 'fly-to-map':
                if (window.NovaMap) window.NovaMap.flyToEntity(entityKey);
                break;
            case 'close-card':
                closeCard(entityKey);
                break;
            case 'toggle-collapse':
                toggleCardCollapse(entityKey);
                break;
            case 'drag-handle':
                // Don't toggle collapse when clicking drag handle
                e.target.closest('.entity-card').draggable = true;
                break;
            default:
                console.warn('[Cards] Unknown action:', action);
        }
        
        return false;
    }, true); // Use capture phase
    
    console.log('[Cards] Event delegation initialized');
}

// ============================================================================
// Card Management
// ============================================================================

/**
 * Open a card for entity (adds to panel if not already open)
 */
function openCard(entity) {
    const entityKey = buildEntityKey(entity);
    
    // Store entity reference - PRESERVE existing uiData
    if (!cards.uiState.has(entityKey)) {
        cards.uiState.set(entityKey, { _entity: entity });
    } else {
        // Preserve all existing uiData, just update entity reference
        cards.uiState.get(entityKey)._entity = entity;
    }
    
    // Add to open cards if not already there
    if (!cards.openCards.includes(entityKey)) {
        cards.openCards.unshift(entityKey);
    }
    
    renderAllCards();
    
    // Scroll new card into view
    setTimeout(() => {
        const cardEl = document.querySelector(`[data-entity-key="${entityKey}"]`);
        if (cardEl) cardEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
}

function closeCard(entityKey) {
    const idx = cards.openCards.indexOf(entityKey);
    if (idx !== -1) {
        cards.openCards.splice(idx, 1);
        renderAllCards();
    }
}

function renderAllCards() {
    const container = document.getElementById('detailedPanelContent');
    if (!container) return;
    
    // Initialize drag-drop once
    if (!dragDropInitialized) {
        initCardDragDrop();
        dragDropInitialized = true;
    }
    
    if (cards.openCards.length === 0) {
        container.innerHTML = `
            <div class="card-placeholder">
                <div class="placeholder-icon">📦</div>
                <div class="placeholder-text">Click a shield to open a card</div>
            </div>
        `;
        return;
    }
    
    const isRewind = window.timeline?.mode === 'REWIND';
    
    container.innerHTML = cards.openCards.map(entityKey => {
        const uiData = cards.uiState.get(entityKey) || {};
        const entity = uiData._entity;
        if (!entity) return '';
        
        const manifest = getCardManifest(entity);
        if (!manifest) return '';
        
        return renderCard(entity, manifest, uiData, isRewind);
    }).join('');
}

function renderCard(entity, manifest, uiData, isRewind) {
    const entityKey = buildEntityKey(entity);
    const isCollapsed = cards.collapsedCards.has(entityKey);
    
    // Custom card rendering for setup-streams
    if (entity.entityType === 'setup-streams') {
        return renderSetupStreamsCard(entity, entityKey, isCollapsed, manifest);
    }
    
    // Custom card rendering for streams (all protocols)
    if (entity.entityType === 'stream' || entity.entityType === 'tcp-stream') {
        return renderTcpStreamCard(entity, entityKey, isCollapsed, manifest);
    }
    
    // Custom card rendering for make-replay
    if (entity.entityType === 'make-replay') {
        return renderMakeReplayCard(entity, entityKey, isCollapsed);
    }
    
    // Custom card rendering for individual runs
    if (entity.entityType === 'run') {
        return renderRunCard(entity, entityKey, isCollapsed);
    }
    
    // Get fresh entity from shields for accurate lastSeen, displayName from presentation layer
    const freshEntity = window.shields?.byKey?.get(entityKey) || entity;
    const displayName = window.getDisplayName ? window.getDisplayName(entityKey) : (freshEntity.displayName || freshEntity.uniqueId);
    
    // Group widgets by section
    const positionWidgets = manifest.widgets.filter(w => w.config?.section === 'position');
    const primaryWidgets = manifest.widgets.filter(w => w.config?.section === 'primary');
    const secondaryWidgets = manifest.widgets.filter(w => w.config?.section === 'secondary');
    const tableWidgets = manifest.widgets.filter(w => w.config?.section === 'tables');
    const otherWidgets = manifest.widgets.filter(w => !w.config?.section);
    
    return `
        <div class="entity-card ${isCollapsed ? 'collapsed' : ''}" data-entity-key="${entityKey}" draggable="false" style="--card-color: ${window.getCardColor ? window.getCardColor(entityKey, manifest.color) : manifest.color}">
            <div class="card-header" data-action="toggle-collapse" data-entity-key="${entityKey}"> 
                <div class="card-drag-handle" data-action="drag-handle" data-entity-key="${entityKey}" title="Drag to reorder">⋮⋮</div>
                <div class="card-header-main">
                    <div class="card-title-row">
                        <span class="card-title" data-entity-key="${entityKey}">${displayName}</span>
                        <button type="button" class="card-edit-name-btn" data-action="edit-name" data-entity-key="${entityKey}" title="Edit display name">✏️</button>
                    </div>
                    <div class="card-identity-row">
                        <span class="card-identity">${freshEntity.uniqueId}</span>
                        ${manifest.onlineIndicator ? renderOnlineIndicator(freshEntity) : ''}
                    </div>
                </div>
                <div class="card-header-controls">
                    <button type="button" class="card-pres-btn" data-action="open-presentation" data-entity-key="${entityKey}" title="Edit presentation (model, color, scale)">🎨</button>
                    <button type="button" class="card-map-btn" data-action="fly-to-map" data-entity-key="${entityKey}" title="Fly to on map">🌍</button>
                    <span class="collapse-indicator">${isCollapsed ? '▶' : '▼'}</span>
                    <button type="button" class="card-close" data-action="close-card" data-entity-key="${entityKey}" title="Close">×</button>
                </div>
            </div>
            
            ${!isCollapsed ? `
            <div class="card-body">
                ${positionWidgets.length > 0 ? `
                    <div class="card-section card-position">
                        ${positionWidgets.map(w => renderPositionTableWidget(w, uiData)).join('')}
                    </div>
                ` : ''}
                
                ${primaryWidgets.length > 0 ? `
                    <div class="card-section card-primary">
                        ${primaryWidgets.map(w => renderWidget(w, uiData)).join('')}
                    </div>
                ` : ''}
                
                ${secondaryWidgets.length > 0 ? `
                    <div class="card-section card-secondary">
                        ${secondaryWidgets.map(w => renderWidget(w, uiData)).join('')}
                    </div>
                ` : ''}
                
                ${tableWidgets.length > 0 ? `
                    <div class="card-section card-tables">
                        ${tableWidgets.map(w => renderWidget(w, uiData)).join('')}
                    </div>
                ` : ''}
                
                ${otherWidgets.map(w => renderWidget(w, uiData)).join('')}
            </div>
            
            ${manifest.actions.length > 0 ? `
                <div class="card-actions">
                    ${isRewind ? '<div class="rewind-notice">REWIND</div>' : ''}
                    <div class="actions-row">
                        ${renderConfigStatus(entityKey)}
                        <div class="actions-grid">
                            ${manifest.actions.map(a => renderAction(a, entityKey, isRewind)).join('')}
                        </div>
                    </div>
                </div>
            ` : ''}
            ` : ''}
        </div>
    `;
}

// ============================================================================
// Widget Rendering
// ============================================================================

function renderWidget(widget, uiData) {
    switch (widget.widgetType) {
        case 'table':
            return renderTableWidget(widget, uiData);
        case 'svTable':
            return renderSvTableWidget(widget, uiData);
        case 'timestamp':
            return renderTimestampWidget(widget, uiData);
        default:
            return renderDefaultWidget(widget, uiData);
    }
}

function renderDefaultWidget(widget, uiData) {
    const value = uiData[widget.binding];
    const displayValue = formatWidgetValue(widget, value);
    
    return `
        <div class="widget" data-binding="${widget.binding}">
            <div class="widget-label">${widget.label}</div>
            <div class="widget-value">${displayValue}</div>
        </div>
    `;
}

function renderTimestampWidget(widget, uiData) {
    const value = uiData[widget.binding];
    let displayValue = '<span class="no-data">—</span>';
    
    if (value) {
        try {
            const date = new Date(value);
            if (!isNaN(date.getTime())) {
                // Compact format: "Jan 29 14:30:45.123"
                const mon = date.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
                const day = date.getUTCDate();
                const hh = date.getUTCHours().toString().padStart(2, '0');
                const mm = date.getUTCMinutes().toString().padStart(2, '0');
                const ss = date.getUTCSeconds().toString().padStart(2, '0');
                const ms = date.getUTCMilliseconds().toString().padStart(3, '0');
                displayValue = `<span class="timestamp-date">${mon} ${day}</span> <span class="timestamp-time">${hh}:${mm}:${ss}.${ms}</span>`;
            }
        } catch (e) {
            displayValue = String(value);
        }
    }
    
    return `
        <div class="widget widget-timestamp" data-binding="${widget.binding}">
            <div class="widget-label">${widget.label}</div>
            <div class="widget-value">${displayValue}</div>
        </div>
    `;
}

/**
 * Render 2x2 position table (Time, Lat, Lon, Alt)
 * Fixed-width coordinates for readability
 */
function renderPositionTableWidget(widget, uiData) {
    const rows = widget.config?.rows || [];
    
    const formatCell = (row) => {
        const val = uiData[row.binding];
        if (val === undefined || val === null) return '—';
        
        if (row.type === 'timestamp') {
            try {
                const date = new Date(val);
                if (!isNaN(date.getTime())) {
                    const mon = date.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
                    const day = date.getUTCDate().toString().padStart(2, '0');
                    const year = date.getUTCFullYear();
                    const hh = date.getUTCHours().toString().padStart(2, '0');
                    const mm = date.getUTCMinutes().toString().padStart(2, '0');
                    const ss = date.getUTCSeconds().toString().padStart(2, '0');
                    return `${mon} ${day}, ${year} ${hh}:${mm}:${ss}`;
                }
            } catch (e) {}
            return String(val);
        }
        
        if (row.type === 'coord') {
            const num = parseFloat(val);
            if (isNaN(num)) return '—';
            return num.toFixed(row.precision || 8) + '°';
        }
        
        if (row.type === 'number') {
            const num = parseFloat(val);
            if (isNaN(num)) return '—';
            return num.toFixed(row.precision || 2) + (row.unit || '');
        }
        
        return String(val);
    };
    
    // Build 2x2 grid
    return `
        <table class="position-table" data-binding="${widget.binding}">
            <tr>
                <td class="pos-label">${rows[0]?.label || ''}</td>
                <td class="pos-value">${formatCell(rows[0] || {})}</td>
                <td class="pos-label">${rows[1]?.label || ''}</td>
                <td class="pos-value">${formatCell(rows[1] || {})}</td>
            </tr>
            <tr>
                <td class="pos-label">${rows[2]?.label || ''}</td>
                <td class="pos-value">${formatCell(rows[2] || {})}</td>
                <td class="pos-label">${rows[3]?.label || ''}</td>
                <td class="pos-value">${formatCell(rows[3] || {})}</td>
            </tr>
        </table>
    `;
}

function renderTableWidget(widget, uiData) {
    const rows = widget.config?.rows || [];
    
    return `
        <div class="widget widget-table" data-binding="${widget.binding}">
            <div class="widget-label">${widget.label}</div>
            <table class="mini-table">
                ${rows.map(row => {
                    const val = uiData[row.binding];
                    const display = val !== undefined && val !== null
                        ? (typeof val === 'number' ? val.toFixed(row.precision ?? 2) : val) + (row.unit || '')
                        : '—';
                    return `<tr><td class="table-label">${row.label}</td><td class="table-value">${display}</td></tr>`;
                }).join('')}
            </table>
        </div>
    `;
}

function renderSvTableWidget(widget, uiData) {
    // Handle sigInfo differently - Signals table
    if (widget.binding === 'sigInfo') {
        return renderSignalsTable(widget, uiData);
    }
    
    const svInfo = uiData[widget.binding] || uiData.svInfo;
    const collapsible = widget.config?.collapsible;
    const stateKey = `${widget.binding}_collapsed`;
    const isCollapsed = cards.tableStates?.[stateKey] ?? true;  // Default collapsed
    
    if (!svInfo || typeof svInfo !== 'object') {
        return `
            <div class="widget widget-sv-table" data-binding="${widget.binding}">
                <div class="widget-label ${collapsible ? 'collapsible' : ''}" ${collapsible ? `onclick="toggleTableCollapse('${stateKey}')"` : ''}>
                    ${collapsible ? `<span class="collapse-icon">${isCollapsed ? '▶' : '▼'}</span>` : ''}
                    ${widget.label}
                </div>
                <div class="sv-content ${isCollapsed && collapsible ? 'collapsed' : ''}">
                    <div class="no-data">No satellite data</div>
                </div>
            </div>
        `;
    }
    
    // svInfo: { GPS: {svId: {cno, elev, azim}, ...}, GLONASS: {...}, ... }
    const constellations = Object.keys(svInfo).filter(k => typeof svInfo[k] === 'object');
    
    if (constellations.length === 0) {
        return `
            <div class="widget widget-sv-table" data-binding="${widget.binding}">
                <div class="widget-label ${collapsible ? 'collapsible' : ''}" ${collapsible ? `onclick="toggleTableCollapse('${stateKey}')"` : ''}>
                    ${collapsible ? `<span class="collapse-icon">${isCollapsed ? '▶' : '▼'}</span>` : ''}
                    ${widget.label}
                </div>
                <div class="sv-content ${isCollapsed && collapsible ? 'collapsed' : ''}">
                    <div class="no-data">No satellite data</div>
                </div>
            </div>
        `;
    }
    
    return `
        <div class="widget widget-sv-table" data-binding="${widget.binding}">
            <div class="widget-label ${collapsible ? 'collapsible' : ''}" ${collapsible ? `onclick="toggleTableCollapse('${stateKey}')"` : ''}>
                ${collapsible ? `<span class="collapse-icon">${isCollapsed ? '▶' : '▼'}</span>` : ''}
                ${widget.label}
                <span class="sv-count">${constellations.reduce((sum, c) => sum + Object.keys(svInfo[c]).length, 0)} SVs</span>
            </div>
            <div class="sv-content ${isCollapsed && collapsible ? 'collapsed' : ''}">
                <div class="sv-tables">
                    ${constellations.map(const_ => {
                        const sats = svInfo[const_];
                        const svIds = Object.keys(sats).slice(0, 12);  // Show more satellites
                        return `
                            <div class="sv-constellation">
                                <div class="const-name">${const_}</div>
                                <table class="sv-table">
                                    <tr><th>SV</th><th>C/N₀</th><th>El</th><th>Az</th></tr>
                                    ${svIds.map(sv => {
                                        const d = sats[sv] || {};
                                        return `<tr>
                                            <td>${sv}</td>
                                            <td>${d.cno ?? '—'}</td>
                                            <td>${d.elev !== undefined ? d.elev + '°' : '—'}</td>
                                            <td>${d.azim !== undefined ? d.azim + '°' : '—'}</td>
                                        </tr>`;
                                    }).join('')}
                                </table>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        </div>
    `;
}

/**
 * Render Signals table - shows signal name and CN0 only (no El/Az since it matches satellite)
 * sigInfo: {GPS: {svId: {L1C/A: {cno, quality}, L2C: {...}}, ...}, ...}
 */
function renderSignalsTable(widget, uiData) {
    const sigInfo = uiData[widget.binding] || uiData.sigInfo;
    const collapsible = widget.config?.collapsible;
    const stateKey = `${widget.binding}_collapsed`;
    const isCollapsed = cards.tableStates?.[stateKey] ?? true;
    
    if (!sigInfo || typeof sigInfo !== 'object') {
        return `
            <div class="widget widget-sv-table" data-binding="${widget.binding}">
                <div class="widget-label ${collapsible ? 'collapsible' : ''}" ${collapsible ? `onclick="toggleTableCollapse('${stateKey}')"` : ''}>
                    ${collapsible ? `<span class="collapse-icon">${isCollapsed ? '▶' : '▼'}</span>` : ''}
                    ${widget.label}
                </div>
                <div class="sv-content ${isCollapsed && collapsible ? 'collapsed' : ''}">
                    <div class="no-data">No signal data</div>
                </div>
            </div>
        `;
    }
    
    const constellations = Object.keys(sigInfo).filter(k => typeof sigInfo[k] === 'object');
    
    if (constellations.length === 0) {
        return `
            <div class="widget widget-sv-table" data-binding="${widget.binding}">
                <div class="widget-label ${collapsible ? 'collapsible' : ''}" ${collapsible ? `onclick="toggleTableCollapse('${stateKey}')"` : ''}>
                    ${collapsible ? `<span class="collapse-icon">${isCollapsed ? '▶' : '▼'}</span>` : ''}
                    ${widget.label}
                </div>
                <div class="sv-content ${isCollapsed && collapsible ? 'collapsed' : ''}">
                    <div class="no-data">No signal data</div>
                </div>
            </div>
        `;
    }
    
    // Build flat list of signals: [{const, sv, sig, cno}, ...]
    let signalRows = [];
    for (const const_ of constellations) {
        for (const [svId, signals] of Object.entries(sigInfo[const_])) {
            if (typeof signals === 'object') {
                for (const [sigName, sigData] of Object.entries(signals)) {
                    if (typeof sigData === 'object' && sigData.cno !== undefined) {
                        signalRows.push({const: const_, sv: svId, sig: sigName, cno: sigData.cno});
                    }
                }
            }
        }
    }
    
    // Sort by constellation, then signal name, then SV ID
    signalRows.sort((a, b) => {
        if (a.const !== b.const) return a.const.localeCompare(b.const);
        if (a.sig !== b.sig) return a.sig.localeCompare(b.sig);
        return parseInt(a.sv) - parseInt(b.sv);
    });
    signalRows = signalRows.slice(0, 24);  // Show top 24 signals
    
    return `
        <div class="widget widget-sv-table" data-binding="${widget.binding}">
            <div class="widget-label ${collapsible ? 'collapsible' : ''}" ${collapsible ? `onclick="toggleTableCollapse('${stateKey}')"` : ''}>
                ${collapsible ? `<span class="collapse-icon">${isCollapsed ? '▶' : '▼'}</span>` : ''}
                ${widget.label}
                <span class="sv-count">${signalRows.length} signals</span>
            </div>
            <div class="sv-content ${isCollapsed && collapsible ? 'collapsed' : ''}">
                <table class="sv-table signals-table">
                    <tr><th>Const</th><th>SV</th><th>Signal</th><th>C/N₀</th></tr>
                    ${signalRows.map(r => `<tr>
                        <td>${r.const.substring(0, 3)}</td>
                        <td>${r.sv}</td>
                        <td>${r.sig}</td>
                        <td>${r.cno ?? '—'}</td>
                    </tr>`).join('')}
                </table>
            </div>
        </div>
    `;
}

function toggleTableCollapse(stateKey) {
    if (!cards.tableStates) cards.tableStates = {};
    cards.tableStates[stateKey] = !cards.tableStates[stateKey];
    renderAllCards();
}

function formatWidgetValue(widget, value) {
    if (value === undefined || value === null) {
        return '<span class="no-data">—</span>';
    }
    
    switch (widget.widgetType) {
        case 'status':
            if (widget.config?.mapping) {
                return widget.config.mapping[value] || String(value);
            }
            return String(value);
            
        case 'number':
            const precision = widget.config?.precision ?? 2;
            const unit = widget.config?.unit || '';
            const numValue = typeof value === 'number' ? value.toFixed(precision) : value;
            return `${numValue}${unit ? ' ' + unit : ''}`;
            
        case 'position':
            if (typeof value === 'object') {
                return `${value.lat?.toFixed(6) || '—'}, ${value.lon?.toFixed(6) || '—'}`;
            }
            return String(value);
            
        default:
            return String(value);
    }
}

// ============================================================================
// Actions
// ============================================================================

/**
 * Render config upload status (filename + success/total count)
 */
function renderConfigStatus(entityKey) {
    const result = cards.configResults.get(entityKey);
    if (!result) return '';
    
    const { filename, successCount, totalCount, pending } = result;
    
    if (pending) {
        return `<div class="config-status pending">
            <div class="config-line"><span class="config-label">cfg file:</span> <span class="config-value">${filename}</span></div>
            <div class="config-line"><span class="config-label">status:</span> <span class="config-value">sending...</span></div>
        </div>`;
    }
    
    const allSuccess = successCount === totalCount;
    const statusClass = allSuccess ? 'success' : 'partial';
    
    return `<div class="config-status ${statusClass}">
        <div class="config-line"><span class="config-label">cfg file:</span> <span class="config-value">${filename}</span></div>
        <div class="config-line"><span class="config-label">ack/total:</span> <span class="config-value">${successCount}/${totalCount}</span></div>
    </div>`;
}

// SVG icons for action buttons
const ACTION_ICONS = {
    configure: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14 12.94c.04-.31.06-.63.06-.94 0-.31-.02-.63-.06-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.04.31-.06.63-.06.94s.02.63.06.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>`,
    uploadConfig: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14 12.94c.04-.31.06-.63.06-.94 0-.31-.02-.63-.06-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.04.31-.06.63-.06.94s.02.63.06.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>`,
    hotStart: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.5.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14c0 4.42 3.58 8 8 8s8-3.58 8-8C20 8.61 17.41 3.8 13.5.67zM11.71 19c-1.78 0-3.22-1.4-3.22-3.14 0-1.62 1.05-2.76 2.81-3.12 1.77-.36 3.6-1.21 4.62-2.58.39 1.29.59 2.65.59 4.04 0 2.65-2.15 4.8-4.8 4.8z"/></svg>`,
    warmStart: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 6v2M12 16v2M6 12h2M16 12h2" stroke="currentColor" stroke-width="1.5"/></svg>`,
    coldReset: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22 11h-4.17l3.24-3.24-1.41-1.42L15 11h-2V9l4.66-4.66-1.42-1.41L13 6.17V2h-2v4.17L7.76 2.93 6.34 4.34 11 9v2H9L4.34 6.34 2.93 7.76 6.17 11H2v2h4.17l-3.24 3.24 1.41 1.42L9 13h2v2l-4.66 4.66 1.42 1.41L11 17.83V22h2v-4.17l3.24 3.24 1.42-1.41L13 15v-2h2l4.66 4.66 1.41-1.42L17.83 13H22z"/></svg>`
};

function renderAction(action, entityKey, isRewind) {
    const disabled = isRewind ? 'disabled' : '';
    const icon = ACTION_ICONS[action.commandType] || ACTION_ICONS[action.actionId] || action.icon || '▶';
    const isIconSvg = icon.startsWith('<svg');
    
    return `
        <button class="action-btn ${disabled}" 
                data-entity-key="${entityKey}"
                data-command-type="${action.commandType}"
                onclick="handleActionClick(this)"
                title="${action.label}"
                ${disabled}>
            <span class="action-icon ${isIconSvg ? 'svg-icon' : ''}">${icon}</span>
        </button>
    `;
}

function handleActionClick(button) {
    if (button.disabled) return;
    
    const entityKey = button.dataset.entityKey;
    const commandType = button.dataset.commandType;
    
    const uiData = cards.uiState.get(entityKey);
    const entity = uiData?._entity;
    
    if (!entity) return;
    
    // Handle uploadConfig action - show file picker
    if (commandType === 'uploadConfig') {
        showConfigUploadDialog(entity);
        return;
    }
    
    // Handle UI actions (not server commands)
    if (commandType === 'showConfig') {
        // Open SVS config page in new tab (per uiDataPlan.md Section 5.3)
        window.open('/svs', '_blank');
        return;
    }
    
    if (window.timeline?.mode === 'REWIND') return;
    
    // Send command directly - no confirmation
    submitCommand(entity, commandType);
}

/**
 * Show config file upload dialog for receiver configuration
 */
function showConfigUploadDialog(entity) {
    // Create hidden file input
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.txt,.cfg,.ubx,.hex';
    input.style.display = 'none';
    
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        try {
            const content = await file.text();
            const lines = content.split('\n').filter(l => l.trim() && !l.trim().startsWith('#'));
            
            if (lines.length === 0) {
                showToast('No commands found in file', 'error');
                return;
            }
            
            showToast(`Sending ${lines.length} commands...`, 'info');
            
            // Track filename for display after result
            const entityKey = buildEntityKey(entity);
            cards.configResults.set(entityKey, {
                filename: file.name,
                successCount: 0,
                totalCount: lines.length,
                pending: true
            });
            
            // Send the config commands
            submitCommand(entity, 'configUpload', { 
                filename: file.name,
                commands: lines 
            });
            
        } catch (err) {
            showToast(`Error reading file: ${err.message}`, 'error');
        }
        
        document.body.removeChild(input);
    };
    
    document.body.appendChild(input);
    input.click();
}

function showToast(message, type = 'info') {
    // Simple toast notification
    const existing = document.querySelector('.card-toast');
    if (existing) existing.remove();
    
    const toast = document.createElement('div');
    toast.className = `card-toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function submitCommand(entity, commandType, payload = {}) {
    const commandId = `cmd_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    
    const request = {
        type: 'command',
        commandId: commandId,
        targetId: entity.uniqueId,
        commandType: commandType,
        payload: payload,
        timelineMode: (window.timeline?.mode || 'LIVE').toLowerCase()  // Server expects lowercase
    };
    
    if (window.sendWebSocketMessage) {
        window.sendWebSocketMessage(request);
        console.log(`[Cards] Command: ${commandType} → ${entity.uniqueId}`);
    }
    
    // Track command with filename if configUpload
    const cmdEntry = { commandId, commandType, status: 'pending' };
    if (commandType === 'configUpload' && payload.filename) {
        cmdEntry.filename = payload.filename;
    }
    cards.commands.set(commandId, cmdEntry);
}

// ============================================================================
// Event Processing
// ============================================================================

function processEvent(event) {
    if (!event.systemId || !event.containerId || !event.uniqueId) return;
    
    const entityKey = `${event.systemId}|${event.containerId}|${event.uniqueId}`;
    
    // Store UI lane data with deep merge for nested structures
    if (event.lane === 'ui' && event.data) {
        const existing = cards.uiState.get(entityKey) || {};
        for (const [key, value] of Object.entries(event.data)) {
            if (value === null) {
                delete existing[key];
            } else if (key === 'svInfo' || key === 'sigInfo') {
                // Deep merge svInfo/sigInfo: {constellation: {svId: {field: value}}}
                // Only assign non-null values to avoid overwriting existing data
                if (!existing[key]) existing[key] = {};
                for (const [const_, svs] of Object.entries(value)) {
                    if (!existing[key][const_]) existing[key][const_] = {};
                    for (const [svId, fields] of Object.entries(svs)) {
                        if (!existing[key][const_][svId]) existing[key][const_][svId] = {};
                        for (const [field, fieldValue] of Object.entries(fields)) {
                            if (fieldValue !== null) {
                                existing[key][const_][svId][field] = fieldValue;
                            }
                        }
                    }
                }
            } else {
                existing[key] = value;
            }
        }
        cards.uiState.set(entityKey, existing);
        
        // Update only changed widgets in open cards (not full re-render)
        if (cards.openCards.includes(entityKey)) {
            updateCardValues(entityKey, Object.keys(event.data));
        }
    }
    
    // Track command events and show feedback
    if (event.lane === 'command' && event.commandId) {
        const cmd = cards.commands.get(event.commandId) || { commandId: event.commandId };
        if (event.messageType === 'CommandRequest') {
            cmd.status = 'requested';
        } else if (event.messageType === 'CommandProgress') {
            cmd.status = 'in-progress';
        } else if (event.messageType === 'CommandResult') {
            cmd.status = event.payload?.status || 'completed';
            
            // Show toast feedback for command completion
            const resultData = event.payload?.result || event.payload?.resultData || event.payload;
            if (resultData?.message) {
                const isError = cmd.status === 'failure' || cmd.status === 'error';
                showToast(resultData.message, isError ? 'error' : 'success');
            } else if (event.payload?.errorMessage) {
                showToast(event.payload.errorMessage, 'error');
            }
            
            // Track configUpload results for display in card actions area
            if (event.commandType === 'configUpload' && resultData) {
                // Find entity key for this target
                for (const [entityKey, uiData] of cards.uiState.entries()) {
                    const entity = uiData?._entity;
                    if (entity && entity.uniqueId === event.targetId) {
                        cards.configResults.set(entityKey, {
                            filename: cmd.filename || 'config',
                            successCount: resultData.successCount || 0,
                            totalCount: (resultData.successCount || 0) + (resultData.failureCount || 0)
                        });
                        renderAllCards();  // Re-render to show config result
                        break;
                    }
                }
            }
        }
        cards.commands.set(event.commandId, cmd);
    }
}

/**
 * Update only the changed widgets in a card without full DOM rebuild.
 * Finds widgets by data-binding attribute and re-renders just those widgets.
 */
function updateCardValues(entityKey, changedFields) {
    const cardEl = document.querySelector(`[data-entity-key="${entityKey}"]`);
    if (!cardEl) return;
    
    const uiData = cards.uiState.get(entityKey) || {};
    const entity = uiData._entity;
    if (!entity) return;
    
    const manifest = getCardManifest(entity);
    if (!manifest || !manifest.widgets) return;
    
    // Build set of widgets that need updating based on changed fields
    const widgetsToUpdate = new Set();
    
    for (const widget of manifest.widgets) {
        // Direct binding match
        if (widget.binding && changedFields.includes(widget.binding)) {
            widgetsToUpdate.add(widget);
            continue;
        }
        
        // Compound widget: check config.rows bindings (position table, mini-table)
        if (widget.config?.rows) {
            for (const row of widget.config.rows) {
                if (row.binding && changedFields.includes(row.binding)) {
                    widgetsToUpdate.add(widget);
                    break;
                }
            }
        }
    }
    
    // Re-render each affected widget
    for (const widget of widgetsToUpdate) {
        const binding = widget.binding || widget.config?.rows?.[0]?.binding;
        if (!binding) continue;
        
        // Find the widget element in DOM by data-binding
        const widgetEl = cardEl.querySelector(`[data-binding="${binding}"]`);
        if (!widgetEl) continue;
        
        // Re-render this widget using the same render function
        const newHtml = renderWidget(widget, uiData);
        
        // Replace just this widget
        widgetEl.outerHTML = newHtml;
    }
}

// ============================================================================
// Helpers
// ============================================================================

function buildEntityKey(entity) {
    return `${entity.systemId}|${entity.containerId}|${entity.uniqueId}`;
}

function getCardManifest(entity) {
    const entityType = entity.entityType || entity.payload?.entityType || entity.payload?.deviceType;
    if (entityType) {
        const cardType = cards.entityTypeToCard.get(entityType);
        if (cardType) return cards.manifests.get(cardType);
    }
    return cards.manifests.get('default-card');
}

function renderOnlineIndicator(entity) {
    // Use EXACTLY the same logic as shield - one line, no card-specific logic
    const isOnline = window.isEntityOnline ? window.isEntityOnline(entity) : false;
    return `<span class="online-indicator ${isOnline ? 'online' : 'offline'}"><span class="indicator-dot"></span>${isOnline ? 'Online' : 'Offline'}</span>`;
}

// Backwards compat
function updateCard(entity) {
    openCard(entity);
}

/**
 * Toggle card collapse state
 */
function toggleCardCollapse(entityKey) {
    if (cards.collapsedCards.has(entityKey)) {
        cards.collapsedCards.delete(entityKey);
    } else {
        cards.collapsedCards.add(entityKey);
    }
    renderAllCards();
}

/**
 * Initialize drag-and-drop for card reordering
 * Simple approach: drag enabled via onmousedown on handle, standard drag events
 */
function initCardDragDrop() {
    const container = document.getElementById('detailedPanelContent');
    if (!container) return;
    
    container.addEventListener('dragstart', (e) => {
        const card = e.target.closest('.entity-card');
        if (!card) return;
        cards.dragState = card.dataset.entityKey;
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', card.dataset.entityKey);
    });
    
    container.addEventListener('dragend', (e) => {
        const card = e.target.closest('.entity-card');
        if (card) {
            card.classList.remove('dragging');
            card.draggable = false;  // Reset draggable after drag ends
        }
        cards.dragState = null;
        document.querySelectorAll('.entity-card.drag-over').forEach(c => c.classList.remove('drag-over'));
    });
    
    container.addEventListener('dragover', (e) => {
        e.preventDefault();
        const card = e.target.closest('.entity-card');
        if (!card || card.dataset.entityKey === cards.dragState) return;
        
        document.querySelectorAll('.entity-card.drag-over').forEach(c => c.classList.remove('drag-over'));
        card.classList.add('drag-over');
    });
    
    container.addEventListener('drop', (e) => {
        e.preventDefault();
        const targetCard = e.target.closest('.entity-card');
        if (!targetCard || !cards.dragState) return;
        
        const draggedKey = cards.dragState;
        const targetKey = targetCard.dataset.entityKey;
        if (draggedKey === targetKey) return;
        
        // Reorder openCards array
        const draggedIdx = cards.openCards.indexOf(draggedKey);
        const targetIdx = cards.openCards.indexOf(targetKey);
        if (draggedIdx === -1 || targetIdx === -1) return;
        
        cards.openCards.splice(draggedIdx, 1);
        cards.openCards.splice(targetIdx, 0, draggedKey);
        
        renderAllCards();
    });
}

// Initialize drag-drop on first render
let dragDropInitialized = false;

// ============================================================================
// Stream Card Rendering (Phase 8)
// ============================================================================

/**
 * Render Setup Streams card - concise form for creating streams
 */
function renderSetupStreamsCard(entity, entityKey, isCollapsed, manifest) {
    const streamIcon = '<img src="/ui/icons/stream.svg" class="card-svg-icon" alt="">';
    return `
        <div class="entity-card ${isCollapsed ? 'collapsed' : ''}" data-entity-key="${entityKey}" style="--card-color: ${window.getCardColor ? window.getCardColor(entityKey, manifest.color) : manifest.color}">
            <div class="card-header" data-action="toggle-collapse" data-entity-key="${entityKey}">
                <div class="card-drag-handle" data-action="drag-handle" data-entity-key="${entityKey}">⋮⋮</div>
                <div class="card-header-main">
                    <div class="card-title">${streamIcon} Setup Streams</div>
                </div>
                <div class="card-header-controls">
                    <span class="collapse-indicator">${isCollapsed ? '▶' : '▼'}</span>
                    <button type="button" class="card-close" data-action="close-card" data-entity-key="${entityKey}" title="Close">×</button>
                </div>
            </div>
            ${!isCollapsed ? `
            <div class="card-body">
                <div class="stream-form">
                    <div class="form-row">
                        <input type="text" id="newStreamName" placeholder="Stream name" class="form-input">
                        <select id="newStreamProtocol" class="form-input form-input-sm" onchange="onProtocolChange()">
                            <option value="tcp">TCP</option>
                            <option value="websocket">WebSocket</option>
                            <option value="udp">UDP</option>
                        </select>
                    </div>
                    <div class="form-row">
                        <input type="number" id="newStreamPort" placeholder="Port" min="81" class="form-input form-input-sm">
                        <input type="text" id="newStreamPath" placeholder="Path (e.g. mystream)" class="form-input form-input-sm" style="display:none;">
                        <select id="newStreamLane" class="form-input">
                            <option value="raw">Raw</option>
                            <option value="parsed">Parsed</option>
                            <option value="metadata">Metadata</option>
                            <option value="ui">UI</option>
                        </select>
                        <select id="newStreamFormat" class="form-input">
                            <option value="hierarchyPerMessage">With Identity</option>
                            <option value="payloadOnly">Payload Only</option>
                        </select>
                    </div>
                    <div class="form-row">
                        <select id="newStreamSystemFilter" class="form-input">${window.buildIdentityOptions ? buildIdentityOptions('system') : '<option value="">Any</option>'}</select>
                        <select id="newStreamContainerFilter" class="form-input">${window.buildIdentityOptions ? buildIdentityOptions('container') : '<option value="">Any</option>'}</select>
                        <select id="newStreamUniqueFilter" class="form-input">${window.buildIdentityOptions ? buildIdentityOptions('unique') : '<option value="">Any</option>'}</select>
                    </div>
                    <div class="form-row">
                        <button class="card-action primary" onclick="createStream()">Create</button>
                        <span id="streamError" class="form-error"></span>
                    </div>
                </div>
                ${renderStreamsList()}
            </div>
            ` : ''}
        </div>
    `;
}

/**
 * Handle protocol dropdown change - toggle port vs path input and update placeholder
 */
function onProtocolChange() {
    var protocol = document.getElementById('newStreamProtocol')?.value || 'tcp';
    var portInput = document.getElementById('newStreamPort');
    var pathInput = document.getElementById('newStreamPath');
    
    if (protocol === 'websocket') {
        if (portInput) portInput.style.display = 'none';
        if (pathInput) pathInput.style.display = '';
    } else if (protocol === 'udp') {
        if (portInput) {
            portInput.style.display = '';
            portInput.placeholder = 'host:port or port';
            portInput.type = 'text';  // Allow host:port
        }
        if (pathInput) pathInput.style.display = 'none';
    } else {
        // TCP
        if (portInput) {
            portInput.style.display = '';
            portInput.placeholder = 'Port';
            portInput.type = 'number';
        }
        if (pathInput) pathInput.style.display = 'none';
    }
}

/**
 * Get endpoint display string for a stream
 */
function getStreamEndpointDisplay(stream) {
    if (stream.protocol === 'websocket') {
        return '/ws/streams/' + stream.endpoint;
    } else if (stream.protocol === 'udp') {
        // UDP is host:port or just port (show as → target)
        return '→ ' + stream.endpoint;
    } else {
        return ':' + stream.endpoint;
    }
}

/**
 * Get protocol icon
 */
function getProtocolIcon(protocol) {
    switch (protocol) {
        case 'tcp': return '🔌';
        case 'websocket': return '🌐';
        case 'udp': return '📡';
        default: return '🔌';
    }
}

/**
 * Render streams list inside Setup Streams card
 */
function renderStreamsList() {
    if (!window.streams || window.streams.definitions.size === 0) {
        return '<div class="stream-list-empty">No streams</div>';
    }
    
    let html = '<div class="stream-list">';
    window.streams.definitions.forEach(function(stream) {
        const status = stream.running ? '🟢' : '⚪';
        const bind = stream.bound ? '🔗' : '';
        const protoIcon = getProtocolIcon(stream.protocol);
        const endpoint = getStreamEndpointDisplay(stream);
        html += `<div class="stream-list-item" onclick="openStreamCard('${stream.streamId}')">
            <span>${status} ${protoIcon} ${stream.name} ${endpoint} ${bind}</span>
            <span class="stream-conns">${stream.connectionCount || 0}</span>
        </div>`;
    });
    html += '</div>';
    return html;
}

/**
 * Render individual Stream card (TCP, WebSocket, UDP)
 */
function renderTcpStreamCard(entity, entityKey, isCollapsed, manifest) {
    const stream = entity;
    const statusText = stream.running ? 'Running' : 'Stopped';
    const statusClass = stream.running ? 'status-on' : 'status-off';
    const streamIcon = '<img src="/ui/icons/stream.svg" class="card-svg-icon" alt="">';
    const protoIcon = getProtocolIcon(stream.protocol || 'tcp');
    const endpoint = getStreamEndpointDisplay(stream);
    const protoUpper = (stream.protocol || 'tcp').toUpperCase();
    
    // Connection count label varies by protocol
    const connLabel = stream.protocol === 'udp' ? 'Targets' : 'Conns';
    
    return `
        <div class="entity-card ${isCollapsed ? 'collapsed' : ''}" data-entity-key="${entityKey}" style="--card-color: ${window.getCardColor ? window.getCardColor(entityKey, manifest.color) : manifest.color}">
            <div class="card-header" data-action="toggle-collapse" data-entity-key="${entityKey}">
                <div class="card-drag-handle" data-action="drag-handle" data-entity-key="${entityKey}">⋮⋮</div>
                <div class="card-header-main">
                    <div class="card-title-row">
                        <span class="card-title" data-entity-key="${entityKey}">${streamIcon} ${window.getDisplayName ? window.getDisplayName(entityKey) : (entity.displayName || stream.name || stream.uniqueId)}</span>
                        <button type="button" class="card-edit-name-btn" data-action="edit-name" data-entity-key="${entityKey}" title="Edit name">✏️</button>
                    </div>
                    <div class="card-identity-row">
                        <span class="card-identity">${protoIcon} ${protoUpper} ${endpoint}</span>
                        <span class="card-status ${statusClass}">${statusText}</span>
                    </div>
                </div>
                <div class="card-header-controls">
                    <span class="collapse-indicator">${isCollapsed ? '▶' : '▼'}</span>
                    <button type="button" class="card-close" data-action="close-card" data-entity-key="${entityKey}" title="Close">×</button>
                </div>
            </div>
            ${!isCollapsed ? `
            <div class="card-body">
                <div class="widget-grid">
                    <div class="widget"><span class="widget-label">Lane</span><span class="widget-value">${stream.lane}</span></div>
                    <div class="widget"><span class="widget-label">Format</span><span class="widget-value">${stream.outputFormat === 'payloadOnly' ? 'Payload' : 'Identity'}</span></div>
                    <div class="widget"><span class="widget-label">${connLabel}</span><span class="widget-value">${stream.connectionCount || 0}</span></div>
                    <div class="widget"><span class="widget-label">Bound</span><span class="widget-value">${stream.bound ? '🔗 Yes' : 'No'}</span></div>
                </div>
                ${stream.systemIdFilter || stream.containerIdFilter || stream.uniqueIdFilter ? `
                <div class="widget-filters">
                    ${stream.systemIdFilter ? `<span class="filter-tag">sys:${stream.systemIdFilter}</span>` : ''}
                    ${stream.containerIdFilter ? `<span class="filter-tag">cont:${stream.containerIdFilter}</span>` : ''}
                    ${stream.uniqueIdFilter ? `<span class="filter-tag">uniq:${stream.uniqueIdFilter}</span>` : ''}
                </div>
                ` : ''}
            </div>
            <div class="card-actions">
                <div class="actions-row">
                    <div class="actions-grid">
                        ${stream.running 
                            ? `<button class="card-action" onclick="stopOutputStream('${stream.streamId}')">⏹ Stop</button>`
                            : `<button class="card-action primary" onclick="startOutputStream('${stream.streamId}')">▶ Start</button>`}
                        ${stream.bound
                            ? `<button class="card-action" onclick="unbindStream('${stream.streamId}')">🔓 Unbind</button>`
                            : `<button class="card-action" onclick="bindStream('${stream.streamId}')">🔗 Bind</button>`}
                        <button class="card-action danger" onclick="deleteStream('${stream.streamId}')">🗑</button>
                    </div>
                </div>
            </div>
            ` : ''}
        </div>
    `;
}

// ============================================================================
// Presentation Functions
// ============================================================================

function openPresentation(entityKey) {
    console.log('[Cards] Opening presentation for:', entityKey);
    if (window.NovaPres) {
        window.NovaPres.open(entityKey);
    } else {
        console.error('[Cards] NovaPres not available');
    }
}

function editCardName(entityKey) {
    console.log('[Cards] Editing name for:', entityKey);
    const titleEl = document.querySelector(`.card-title[data-entity-key="${entityKey}"]`);
    if (!titleEl) {
        console.error('[Cards] Title element not found for:', entityKey);
        return;
    }
    
    // Get current display name (strip emoji prefix)
    let currentName = titleEl.textContent;
    let emojiPrefix = '';
    const emojiMatch = currentName.match(/^(\S+\s)/);
    if (emojiMatch && /\p{Emoji}/u.test(emojiMatch[1])) {
        emojiPrefix = emojiMatch[1];
        currentName = currentName.substring(emojiPrefix.length);
    }
    
    const input = document.createElement('input');
    input.type = 'text';
    input.value = currentName;
    input.className = 'card-title-input';
    input.dataset.entityKey = entityKey;
    
    // Check if this is a run card
    const isRunCard = entityKey.startsWith('replay|runs|run-');
    const runNumberMatch = entityKey.match(/run-(\d+)$/);
    const runNumber = runNumberMatch ? parseInt(runNumberMatch[1]) : null;
    
    const saveName = async () => {
        const newName = input.value.trim();
        if (newName && newName !== currentName) {
            if (isRunCard && runNumber) {
                // For run cards, use updateRun
                try {
                    await updateRun(runNumber, { runName: newName });
                    console.log('[Cards] Run name saved:', newName);
                } catch (e) {
                    console.error('[Cards] Failed to save run name:', e);
                }
            } else {
                // Save via presentation API - need to split entityKey into scopeId/uniqueId
                const parts = entityKey.split('|');
                const scopeId = parts.length === 3 ? `${parts[0]}|${parts[1]}` : 'default';
                const uniqueId = parts.length === 3 ? parts[2] : entityKey;
                
                try {
                    const url = `/api/presentation/${encodeURIComponent(uniqueId)}?scopeId=${encodeURIComponent(scopeId)}`;
                    const response = await fetch(url, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ displayName: newName })
                    });
                    if (response.ok) {
                        console.log('[Cards] Name saved:', newName);
                        // Update presentation layer — single source of truth
                        var pres = window.shields?.presentation?.get(entityKey) || {};
                        pres.displayName = newName;
                        window.shields?.presentation?.set(entityKey, pres);
                        // Re-render everything that reads presentation
                        if (window.renderShields) window.renderShields();
                        if (window.renderAllCards) window.renderAllCards();
                        if (window.renderStreamsList) window.renderStreamsList();
                        if (window.NovaMap) window.NovaMap.updateEntityPresentation(entityKey, { displayName: newName });
                    }
                } catch (e) {
                    console.error('[Cards] Failed to save name:', e);
                }
            }
        }
        // Replace input with span
        const span = document.createElement('span');
        span.className = 'card-title';
        span.dataset.entityKey = entityKey;
        span.textContent = emojiPrefix + (input.value.trim() || currentName);
        input.replaceWith(span);
    };
    
    input.addEventListener('blur', saveName);
    input.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter') {
            e.preventDefault();
            input.blur(); // triggers saveName via blur handler
        } else if (e.key === 'Escape') {
            e.preventDefault();
            // Remove blur handler before replacing to avoid double-save
            input.removeEventListener('blur', saveName);
            const span = document.createElement('span');
            span.className = 'card-title';
            span.dataset.entityKey = entityKey;
            span.textContent = emojiPrefix + currentName;
            input.replaceWith(span);
        }
    });
    input.addEventListener('click', (e) => e.stopPropagation());
    
    titleEl.replaceWith(input);
    input.focus();
    input.select();
}

// ============================================================================
// Run / Replay Cards (Phase 11)
// ============================================================================

/**
 * Format seconds (Unix timestamp) to UTC datetime string matching timeline format
 * Format: YYYY-MM-DD HH:MM:SS UTC
 */
function formatDatetimeUTC(sec) {
    if (!sec) return '';
    const date = new Date(sec * 1000);
    const yyyy = date.getUTCFullYear();
    const mm = String(date.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(date.getUTCDate()).padStart(2, '0');
    const hh = String(date.getUTCHours()).padStart(2, '0');
    const min = String(date.getUTCMinutes()).padStart(2, '0');
    const ss = String(date.getUTCSeconds()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss}`;
}

/**
 * Format seconds (Unix timestamp) to datetime-local input value (UTC)
 * Note: datetime-local displays in browser's local timezone, but we store/process UTC
 */
function formatDatetimeLocal(sec) {
    if (!sec) return '';
    const date = new Date(sec * 1000);
    // Format as UTC for datetime-local input: YYYY-MM-DDTHH:MM:SS
    const yyyy = date.getUTCFullYear();
    const mm = String(date.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(date.getUTCDate()).padStart(2, '0');
    const hh = String(date.getUTCHours()).padStart(2, '0');
    const min = String(date.getUTCMinutes()).padStart(2, '0');
    const ss = String(date.getUTCSeconds()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}T${hh}:${min}:${ss}`;
}

/**
 * Parse datetime-local input value to seconds (Unix timestamp)
 * Treats input as UTC (matching the format we display)
 */
function parseDatetimeLocal(value) {
    if (!value) return 0;
    // Input format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM
    // Parse as UTC by adding 'Z' suffix
    const date = new Date(value + 'Z');
    return Math.floor(date.getTime() / 1000);
}

/**
 * Render "Make Replay" card - entry form to create new runs
 */
function renderMakeReplayCard(entity, entityKey, isCollapsed) {
    const settings = window.replays?.settings || {};
    const defaultType = settings.defaultRunType || 'generic';
    const lastRunName = settings.lastRunName || 'Untitled Run';
    const nextRunNumber = (window.replays?.nextRunNumber || 1);
    
    // Get run types from manifests
    const runTypes = window.getRunTypes ? window.getRunTypes() : [
        { value: 'generic', label: 'Generic', icon: '🎬' }
    ];
    
    // Default times: use CURRENT WALL-CLOCK time, not timeline cursor
    // This ensures "now" is actually now, even when timeline is in replay mode
    const wallClockTimeSec = Math.floor(Date.now() / 1000);
    const defaultStart = formatDatetimeLocal(wallClockTimeSec);
    const defaultStop = formatDatetimeLocal(wallClockTimeSec + 3600); // +1 hour
    
    // Build run type options from manifests
    const typeOptions = runTypes.map(t => 
        `<option value="${t.value}" ${defaultType === t.value ? 'selected' : ''}>${t.icon || ''} ${t.label}</option>`
    ).join('');
    
    return `
        <div class="entity-card ${isCollapsed ? 'collapsed' : ''}" data-entity-key="${entityKey}" style="--card-color: #9c27b0">
            <div class="card-header" data-action="toggle-collapse" data-entity-key="${entityKey}">
                <div class="card-drag-handle" data-action="drag-handle" data-entity-key="${entityKey}">⋮⋮</div>
                <div class="card-header-main">
                    <div class="card-title">🎬 Make Replay</div>
                </div>
                <div class="card-header-controls">
                    <span class="collapse-indicator">${isCollapsed ? '▶' : '▼'}</span>
                    <button type="button" class="card-close" data-action="close-card" data-entity-key="${entityKey}" title="Close">×</button>
                </div>
            </div>
            ${!isCollapsed ? `
            <div class="card-body">
                <div class="run-form">
                    <div class="form-row">
                        <label>Run #:</label>
                        <input type="number" id="newRunNumber" class="form-input form-input-small" value="${nextRunNumber}" min="1">
                    </div>
                    <div class="form-row">
                        <label>Name:</label>
                        <input type="text" id="newRunName" placeholder="Run name" class="form-input" value="${escapeAttr(lastRunName)}">
                    </div>
                    <div class="form-row">
                        <label>Type:</label>
                        <select id="newRunType" class="form-input">
                            ${typeOptions}
                        </select>
                    </div>
                    <div class="form-row">
                        <label>Start Time (UTC):</label>
                        <input type="datetime-local" id="newRunStart" class="form-input" step="1" value="${defaultStart}">
                    </div>
                    <div class="form-row">
                        <label>Stop Time (UTC):</label>
                        <input type="datetime-local" id="newRunStop" class="form-input" step="1" value="${defaultStop}">
                    </div>
                    <div class="form-row">
                        <label>Notes:</label>
                        <textarea id="newRunNotes" class="form-input" rows="2" placeholder="Analyst notes..."></textarea>
                    </div>
                    <div class="form-row form-actions">
                        <button class="card-action primary" onclick="saveNewRun()">💾 Save Replay</button>
                        <span id="runError" class="form-error"></span>
                    </div>
                </div>
            </div>
            ` : ''}
        </div>
    `;
}

/**
 * Render individual run card - shows run details, music on/off times, optional signal toggles
 */
function renderRunCard(entity, entityKey, isCollapsed) {
    const run = entity;  // Entity IS the run
    const isClamped = window.replays?.clampedRun === run.runNumber;
    const timebase = run.timebase || 'canonical';
    
    // Format times for display
    const formatTimeDisplay = (sec) => {
        if (!sec) return '—';
        const date = new Date(sec * 1000);
        const mon = date.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
        const day = date.getUTCDate().toString().padStart(2, '0');
        const hh = date.getUTCHours().toString().padStart(2, '0');
        const mm = date.getUTCMinutes().toString().padStart(2, '0');
        const ss = date.getUTCSeconds().toString().padStart(2, '0');
        return `${mon} ${day} ${hh}:${mm}:${ss}Z`;
    };
    
    const startFormatted = formatTimeDisplay(run.startTimeSec);
    const stopFormatted = formatTimeDisplay(run.stopTimeSec);
    const durationSec = (run.stopTimeSec && run.startTimeSec) 
        ? Math.max(0, run.stopTimeSec - run.startTimeSec)
        : 0;
    const durationStr = durationSec > 0 ? formatDuration(durationSec) : '—';
    
    return `
        <div class="entity-card ${isCollapsed ? 'collapsed' : ''}" data-entity-key="${entityKey}" style="--card-color: #9c27b0">
            <div class="card-header" data-action="toggle-collapse" data-entity-key="${entityKey}">
                <div class="card-drag-handle" data-action="drag-handle" data-entity-key="${entityKey}">⋮⋮</div>
                <div class="card-header-main">
                    <div class="card-title-row">
                        <span class="card-title" data-entity-key="${entityKey}">🎬 ${escapeHtml(run.runName || 'Run ' + run.runNumber)}</span>
                    </div>
                    <div class="card-identity-row">
                        <span class="card-identity">Run #${run.runNumber} · ${run.runType} · ${timebase}</span>
                        ${isClamped ? '<span class="run-clamped">🔒 CLAMPED</span>' : ''}
                    </div>
                </div>
                <div class="card-header-controls">
                    <button type="button" class="card-edit-name-btn" data-action="edit-name" data-entity-key="${entityKey}" title="Edit name">✏️</button>
                    <span class="collapse-indicator">${isCollapsed ? '▶' : '▼'}</span>
                    <button type="button" class="card-close" data-action="close-card" data-entity-key="${entityKey}" title="Close">×</button>
                </div>
            </div>
            ${!isCollapsed ? `
            <div class="card-body">
                <!-- Time Window Summary (core fields - always present) -->
                <div class="run-time-summary">
                    <div class="time-summary-row">
                        <span class="time-label">Window:</span>
                        <span class="time-value">${startFormatted} → ${stopFormatted}</span>
                    </div>
                    <div class="time-summary-row">
                        <span class="time-label">Duration:</span>
                        <span class="time-value">${durationStr}</span>
                    </div>
                </div>
                
                <!-- Editable Start/Stop Times (core fields - always present) -->
                <div class="run-time-edit">
                    <div class="time-edit-row">
                        <label>Start (UTC):</label>
                        <input type="datetime-local" step="1" value="${formatDatetimeLocal(run.startTimeSec)}" 
                               onchange="updateRunStartTime(${run.runNumber}, this.value)" class="form-input">
                        <button class="btn-small btn-icon" onclick="setRunStartToCursor(${run.runNumber})" title="Set to cursor">⏱️</button>
                    </div>
                    <div class="time-edit-row">
                        <label>Stop (UTC):</label>
                        <input type="datetime-local" step="1" value="${formatDatetimeLocal(run.stopTimeSec)}"
                               onchange="updateRunStopTime(${run.runNumber}, this.value)" class="form-input">
                        <button class="btn-small btn-icon" onclick="setRunStopToCursor(${run.runNumber})" title="Set to cursor">⏱️</button>
                    </div>
                </div>
                
                <!-- Manifest-defined fields -->
                ${renderManifestFields(run)}
                
                <!-- Analyst Notes (core field - always present) -->
                <div class="run-field">
                    <label>Notes:</label>
                    <textarea id="runNotes-${run.runNumber}" class="form-input" rows="2" placeholder="Analyst notes..." onchange="updateRunNotes(${run.runNumber}, this.value)">${escapeHtml(run.analystNotes || '')}</textarea>
                </div>
                
                <!-- Actions -->
                <div class="run-actions">
                    <button class="card-action ${isClamped ? '' : 'primary'}" onclick="${isClamped ? 'clearClamp()' : `clampToRun(${run.runNumber})`}">
                        ${isClamped ? '🔓 Unclamp' : '🔒 Clamp Timeline'}
                    </button>
                    <button class="card-action" onclick="downloadBundle(${run.runNumber})">
                        📦 Download Bundle
                    </button>
                </div>
                <div class="run-actions danger-zone">
                    <button class="card-action danger" onclick="deleteRun(${run.runNumber})">🗑️ Delete</button>
                </div>
            </div>
            ` : ''}
        </div>
    `;
}

/**
 * Format duration in human-readable form
 */
function formatDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return s > 0 ? `${m}m ${s}s` : `${m}m`;
    }
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/**
 * Render manifest-defined fields for a run card.
 * Looks up the run's manifest and renders each field based on its type.
 */
function renderManifestFields(run) {
    const manifest = window.getRunManifest ? window.getRunManifest(run.runType) : null;
    if (!manifest || !manifest.fields || manifest.fields.length === 0) {
        return '';
    }
    
    let html = '';
    for (const field of manifest.fields) {
        html += renderManifestField(run, field);
    }
    return html;
}

/**
 * Render a single manifest-defined field
 */
function renderManifestField(run, field) {
    const { fieldId, label, fieldType, config } = field;
    const value = run[fieldId];
    
    switch (fieldType) {
        case 'string':
            return `
                <div class="run-field">
                    <label>${escapeHtml(label)}:</label>
                    <input type="text" class="form-input" value="${escapeAttr(value || '')}"
                           onchange="updateRunField(${run.runNumber}, '${fieldId}', this.value)">
                </div>
            `;
        
        case 'text':
            return `
                <div class="run-field">
                    <label>${escapeHtml(label)}:</label>
                    <textarea class="form-input" rows="${config?.rows || 2}"
                              onchange="updateRunField(${run.runNumber}, '${fieldId}', this.value)">${escapeHtml(value || '')}</textarea>
                </div>
            `;
        
        case 'number':
            return `
                <div class="run-field">
                    <label>${escapeHtml(label)}:</label>
                    <input type="number" class="form-input" value="${value || 0}"
                           onchange="updateRunField(${run.runNumber}, '${fieldId}', parseFloat(this.value))">
                </div>
            `;
        
        case 'datetime':
            return `
                <div class="run-field">
                    <label>${escapeHtml(label)}:</label>
                    <input type="datetime-local" step="1" class="form-input" value="${formatDatetimeLocal(value)}"
                           onchange="updateRunField(${run.runNumber}, '${fieldId}', parseDatetimeLocal(this.value))">
                </div>
            `;
        
        case 'boolean':
            return `
                <div class="run-field run-field-inline">
                    <label>
                        <input type="checkbox" ${value ? 'checked' : ''}
                               onchange="updateRunField(${run.runNumber}, '${fieldId}', this.checked)">
                        ${escapeHtml(label)}
                    </label>
                </div>
            `;
        
        case 'array':
            return renderArrayField(run, field);
        
        case 'signals':
            return renderSignalsField(run, field);
        
        default:
            return `<div class="run-field"><em>Unknown field type: ${fieldType}</em></div>`;
    }
}

/**
 * Render an array field
 * 
 * Supports two modes based on config:
 * - itemFields: array of objects with sub-fields (collapsible)
 * - itemType: array of simple values like datetime (inline with button)
 */
function renderArrayField(run, field) {
    const { fieldId, label, config } = field;
    const items = run[fieldId] || [];
    const itemFields = config?.itemFields;
    const itemType = config?.itemType;
    const addLabel = config?.addLabel || 'Add';
    const isInline = config?.inline || false;
    const isCollapsed = config?.defaultCollapsed || false;
    
    // Simple value array (inline mode) - e.g., array of datetimes
    if (itemType && !itemFields) {
        return renderSimpleArrayField(run, field, items, itemType, label, addLabel, isInline);
    }
    
    // Object array (collapsible mode) - e.g., array of {onSec, offSec}
    let html = `
        <div class="array-field-section" data-field="${fieldId}">
            <div class="section-header collapsible" onclick="toggleArrayFieldCollapse(${run.runNumber}, '${fieldId}')">
                <span class="collapse-icon" id="${fieldId}Collapse-${run.runNumber}">${isCollapsed ? '▶' : '▼'}</span>
                <span>${escapeHtml(label)} (${items.length})</span>
                <button class="btn-small btn-add" onclick="event.stopPropagation(); addArrayFieldItem(${run.runNumber}, '${fieldId}')" title="${addLabel}">+</button>
            </div>
            <div class="array-field-content ${isCollapsed ? 'collapsed' : ''}" id="${fieldId}Content-${run.runNumber}">
    `;
    
    if (items.length === 0) {
        html += `<div class="no-items">No items. Click + to add.</div>`;
    } else {
        items.forEach((item, idx) => {
            html += `<div class="array-field-item" data-idx="${idx}">`;
            html += `<button class="btn-small btn-remove" onclick="removeArrayFieldItem(${run.runNumber}, '${fieldId}', ${idx})" title="Remove">🗑️</button>`;
            
            for (const itemField of (itemFields || [])) {
                const itemValue = item[itemField.fieldId];
                if (itemField.fieldType === 'datetime') {
                    html += `
                        <div class="item-field-row datetime-row">
                            <button class="btn-small btn-set-now" onclick="setArrayFieldItemToCursor(${run.runNumber}, '${fieldId}', ${idx}, '${itemField.fieldId}')" title="Set to current time">${escapeHtml(itemField.label)}</button>
                            <input type="datetime-local" step="1" class="form-input datetime-edit" value="${formatDatetimeLocal(itemValue)}"
                                   onchange="updateArrayFieldItem(${run.runNumber}, '${fieldId}', ${idx}, '${itemField.fieldId}', parseDatetimeLocal(this.value))">
                        </div>
                    `;
                } else {
                    html += `
                        <div class="item-field-row">
                            <label>${escapeHtml(itemField.label)}:</label>
                            <input type="text" class="form-input" value="${escapeAttr(itemValue || '')}"
                                   onchange="updateArrayFieldItem(${run.runNumber}, '${fieldId}', ${idx}, '${itemField.fieldId}', this.value)">
                        </div>
                    `;
                }
            }
            html += `</div>`;
        });
    }
    
    html += `</div></div>`;
    return html;
}

/**
 * Render a simple array field (array of primitive values, not objects)
 * Used for inline display with button + editable values
 */
function renderSimpleArrayField(run, field, items, itemType, label, addLabel, isInline) {
    const { fieldId } = field;
    
    let html = `<div class="simple-array-field ${isInline ? 'inline' : ''}" data-field="${fieldId}">`;
    html += `<button class="btn-small btn-set-now" onclick="addSimpleArrayItem(${run.runNumber}, '${fieldId}', '${itemType}')" title="${addLabel}">${escapeHtml(label)}</button>`;
    
    if (items.length > 0) {
        html += `<div class="simple-array-items">`;
        items.forEach((value, idx) => {
            if (itemType === 'datetime') {
                html += `
                    <div class="simple-array-item">
                        <span class="item-number">${idx + 1}.</span>
                        <input type="datetime-local" step="1" class="form-input datetime-edit" value="${formatDatetimeLocal(value)}"
                               onchange="updateSimpleArrayItem(${run.runNumber}, '${fieldId}', ${idx}, parseDatetimeLocal(this.value))">
                        <button class="btn-small btn-remove-inline" onclick="removeSimpleArrayItem(${run.runNumber}, '${fieldId}', ${idx})" title="Remove">×</button>
                    </div>
                `;
            } else {
                html += `
                    <div class="simple-array-item">
                        <span class="item-number">${idx + 1}.</span>
                        <input type="text" class="form-input" value="${escapeAttr(value || '')}"
                               onchange="updateSimpleArrayItem(${run.runNumber}, '${fieldId}', ${idx}, this.value)">
                        <button class="btn-small btn-remove-inline" onclick="removeSimpleArrayItem(${run.runNumber}, '${fieldId}', ${idx})" title="Remove">×</button>
                    </div>
                `;
            }
        });
        html += `</div>`;
    }
    
    html += `</div>`;
    return html;
}

/**
 * Add item to simple array field
 */
async function addSimpleArrayItem(runNumber, fieldId, itemType) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const items = [...(run[fieldId] || [])];
    
    // Default value based on type
    let newValue;
    if (itemType === 'datetime') {
        newValue = Math.floor(Date.now() / 1000);  // Current time
    } else {
        newValue = '';
    }
    
    items.push(newValue);
    await updateRun(runNumber, { [fieldId]: items });
    openRunCard(runNumber);
}

/**
 * Update item in simple array field
 */
async function updateSimpleArrayItem(runNumber, fieldId, idx, value) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const items = [...(run[fieldId] || [])];
    if (idx >= 0 && idx < items.length) {
        items[idx] = value;
        await updateRun(runNumber, { [fieldId]: items });
    }
}

/**
 * Remove item from simple array field
 */
async function removeSimpleArrayItem(runNumber, fieldId, idx) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const items = [...(run[fieldId] || [])];
    if (idx >= 0 && idx < items.length) {
        items.splice(idx, 1);
        await updateRun(runNumber, { [fieldId]: items });
        openRunCard(runNumber);
    }
}

/**
 * Cache for available signals per field.
 * Populated from run manifest config when rendering signals fields.
 * Key: fieldId, Value: { constellation: [signals] }
 */
const _signalsFieldCache = {};

/**
 * Render a signals field - expandable/collapsible table with checkboxes
 * 
 * Shows all available GNSS signals grouped by constellation.
 * Signals are stored as { signalId: true/false } in the run's data.
 * Available signals are read from field.config.availableSignals (from manifest).
 */
function renderSignalsField(run, field) {
    const { fieldId, label, config } = field;
    const selectedSignals = run[fieldId] || {};
    const isCollapsed = config?.defaultCollapsed !== false;  // Default to collapsed
    
    // Get available signals from manifest config, cache for helper functions
    const availableSignals = config?.availableSignals || {};
    _signalsFieldCache[fieldId] = availableSignals;
    
    let html = `
        <div class="signals-field-section">
            <div class="section-header collapsible" onclick="toggleSignalsFieldCollapse(${run.runNumber}, '${fieldId}')">
                <span class="collapse-icon" id="${fieldId}Collapse-${run.runNumber}">${isCollapsed ? '▶' : '▼'}</span>
                <span>${escapeHtml(label)}</span>
            </div>
            <div class="signals-field-content ${isCollapsed ? 'collapsed' : ''}" id="${fieldId}Content-${run.runNumber}">
    `;
    
    // Render each constellation as collapsible section with checkboxes
    for (const [constellation, signals] of Object.entries(availableSignals)) {
        // Count enabled in this constellation
        const constSignals = signals.map(sig => `${constellation}-${sig}`);
        const constEnabled = constSignals.filter(sig => selectedSignals[sig] === true).length;
        // Always start collapsed - user must click signals header to see constellations
        const constExpanded = false;
        
        html += `
            <div class="signal-constellation">
                <div class="const-header collapsible" onclick="toggleConstellationCollapse(this)">
                    <span class="collapse-icon">${constExpanded ? '▼' : '▶'}</span>
                    <span class="const-name">${escapeHtml(constellation)}</span>
                    <label class="const-select-all" onclick="event.stopPropagation()">
                        <input type="checkbox" 
                               ${constEnabled === signals.length ? 'checked' : ''} 
                               ${constEnabled > 0 && constEnabled < signals.length ? 'indeterminate' : ''}
                               onchange="selectAllConstellation(${run.runNumber}, '${fieldId}', '${constellation}', this.checked)">
                        <span>All</span>
                    </label>
                </div>
                <div class="signal-grid ${constExpanded ? '' : 'collapsed'}">`;
        
        for (const sig of signals) {
            const sigId = `${constellation}-${sig}`;
            const isEnabled = selectedSignals[sigId] === true;
            html += `
                <label class="signal-toggle ${isEnabled ? 'signal-enabled' : ''}">
                    <input type="checkbox" ${isEnabled ? 'checked' : ''}
                           onchange="updateSignalField(${run.runNumber}, '${fieldId}', '${sigId}', this.checked)">
                    <span class="signal-name">${escapeHtml(sig)}</span>
                </label>`;
        }
        
        html += `</div></div>`;
    }
    
    html += `</div></div>`;
    return html;
}

/**
 * Toggle collapse of a constellation section within signals field
 */
function toggleConstellationCollapse(headerEl) {
    const icon = headerEl.querySelector('.collapse-icon');
    const grid = headerEl.nextElementSibling;
    if (grid && grid.classList.contains('signal-grid')) {
        const isCollapsed = grid.classList.toggle('collapsed');
        icon.textContent = isCollapsed ? '▶' : '▼';
    }
}

/**
 * Select/deselect all signals in a constellation
 */
async function selectAllConstellation(runNumber, fieldId, constellation, checked) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const signals = { ...(run[fieldId] || {}) };
    const availableSignals = _signalsFieldCache[fieldId] || {};
    const constSignals = availableSignals[constellation] || [];
    
    for (const sig of constSignals) {
        const sigId = `${constellation}-${sig}`;
        signals[sigId] = checked;
    }
    
    await updateRun(runNumber, { [fieldId]: signals });
    openRunCard(runNumber);  // Refresh to update UI
}

/**
 * Add a signal to a run's signals field
 */
async function addSignalToRun(runNumber, fieldId, signalId) {
    signalId = signalId?.trim();
    if (!signalId) return;
    
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const signals = { ...(run[fieldId] || {}), [signalId]: true };
    await updateRun(runNumber, { [fieldId]: signals });
    
    // Clear input and refresh card
    const input = document.getElementById(`newSignal-${runNumber}`);
    if (input) input.value = '';
    openRunCard(runNumber);
}

/**
 * Render music on/off times section (multiple entries supported)
 * DEPRECATED: Use renderArrayField with manifest instead
 */
function renderMusicTimesSection(run) {
    const musicTimes = run.musicTimes || [];
    
    let html = `
        <div class="music-times-section">
            <div class="section-header collapsible" onclick="toggleMusicTimesCollapse(${run.runNumber})">
                <span class="collapse-icon" id="musicCollapse-${run.runNumber}">▼</span>
                <span>Music Times (${musicTimes.length})</span>
                <button class="btn-small btn-add" onclick="event.stopPropagation(); addMusicTime(${run.runNumber})" title="Add music time">+</button>
            </div>
            <div class="music-times-content" id="musicContent-${run.runNumber}">
    `;
    
    if (musicTimes.length === 0) {
        html += `<div class="no-music-times">No music times defined. Click + to add.</div>`;
    } else {
        musicTimes.forEach((entry, idx) => {
            html += `
                <div class="music-time-entry" data-run="${run.runNumber}" data-idx="${idx}">
                    <div class="music-time-row">
                        <label>Music On:</label>
                        <input type="datetime-local" step="1" value="${formatDatetimeLocal(entry.onSec)}"
                               onchange="updateMusicTime(${run.runNumber}, ${idx}, 'onSec', this.value)" class="form-input">
                        <button class="btn-small btn-icon" onclick="setMusicOnToCursor(${run.runNumber}, ${idx})" title="Set to cursor">⏱️</button>
                    </div>
                    <div class="music-time-row">
                        <label>Music Off:</label>
                        <input type="datetime-local" step="1" value="${formatDatetimeLocal(entry.offSec)}"
                               onchange="updateMusicTime(${run.runNumber}, ${idx}, 'offSec', this.value)" class="form-input">
                        <button class="btn-small btn-icon" onclick="setMusicOffToCursor(${run.runNumber}, ${idx})" title="Set to cursor">⏱️</button>
                    </div>
                    <button class="btn-small btn-remove" onclick="removeMusicTime(${run.runNumber}, ${idx})" title="Remove">🗑️</button>
                </div>
            `;
        });
    }
    
    html += `</div></div>`;
    return html;
}

/**
 * Render signal toggles for hardwareService runs (collapsible, organized by constellation)
 */
function renderSignalToggles(run) {
    const signals = window.getSignalsByConstellation ? window.getSignalsByConstellation() : {};
    const runSignals = run.hardwareService?.signals || {};
    
    if (Object.keys(signals).length === 0) {
        return '<div class="no-signals">No signals configured</div>';
    }
    
    // Count enabled signals
    const enabledCount = Object.values(runSignals).filter(v => v === true).length;
    const totalCount = Object.values(signals).reduce((sum, arr) => sum + arr.length, 0);
    
    let html = `
        <div class="signal-toggles-section">
            <div class="section-header collapsible" onclick="toggleSignalTogglesCollapse(${run.runNumber})">
                <span class="collapse-icon" id="signalCollapse-${run.runNumber}">▶</span>
                <span>Signals (${enabledCount}/${totalCount})</span>
            </div>
            <div class="signal-toggles-content collapsed" id="signalContent-${run.runNumber}">
    `;
    
    for (const [constellation, signalList] of Object.entries(signals)) {
        html += `<div class="signal-constellation">`;
        html += `<div class="const-name">${constellation}</div>`;
        html += `<div class="signal-grid">`;
        for (const signalId of signalList) {
            const isEnabled = runSignals[signalId] === true;
            html += `
                <label class="signal-toggle">
                    <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleRunSignal(${run.runNumber}, '${signalId}', this.checked)">
                    <span class="signal-name">${signalId}</span>
                </label>
            `;
        }
        html += `</div></div>`;
    }
    
    html += '</div></div>';
    return html;
}

/**
 * Toggle music times section collapse
 */
function toggleMusicTimesCollapse(runNumber) {
    const content = document.getElementById(`musicContent-${runNumber}`);
    const icon = document.getElementById(`musicCollapse-${runNumber}`);
    if (content && icon) {
        content.classList.toggle('collapsed');
        icon.textContent = content.classList.contains('collapsed') ? '▶' : '▼';
    }
}

/**
 * Toggle signal toggles section collapse
 */
function toggleSignalTogglesCollapse(runNumber) {
    const content = document.getElementById(`signalContent-${runNumber}`);
    const icon = document.getElementById(`signalCollapse-${runNumber}`);
    if (content && icon) {
        content.classList.toggle('collapsed');
        icon.textContent = content.classList.contains('collapsed') ? '▶' : '▼';
    }
}

/**
 * Save new run from Make Replay form
 */
function saveNewRun() {
    const numberEl = document.getElementById('newRunNumber');
    const nameEl = document.getElementById('newRunName');
    const typeEl = document.getElementById('newRunType');
    const startEl = document.getElementById('newRunStart');
    const stopEl = document.getElementById('newRunStop');
    const notesEl = document.getElementById('newRunNotes');
    
    const runNumber = parseInt(numberEl?.value) || undefined;
    const runName = nameEl?.value?.trim() || 'Untitled Run';
    const runType = typeEl?.value || 'generic';
    const startTimeSec = parseDatetimeLocal(startEl?.value);
    const stopTimeSec = parseDatetimeLocal(stopEl?.value);
    const analystNotes = notesEl?.value?.trim() || '';
    
    // Validate
    if (!startTimeSec || !stopTimeSec) {
        showRunError('Start and stop times are required');
        return;
    }
    if (stopTimeSec <= startTimeSec) {
        showRunError('Stop time must be after start time');
        return;
    }
    
    // Call createRun with all fields
    createRunFull({
        runNumber,
        runName,
        runType,
        startTimeSec,
        stopTimeSec,
        analystNotes
    });
}

/**
 * Show run error
 */
function showRunError(msg) {
    const errorEl = document.getElementById('runError');
    if (errorEl) errorEl.textContent = msg;
}

/**
 * Update run start time from datetime input
 */
function updateRunStartTime(runNumber, value) {
    const sec = parseDatetimeLocal(value);
    if (sec > 0) {
        updateRun(runNumber, { startTimeSec: sec });
    }
}

/**
 * Update run stop time from datetime input
 */
function updateRunStopTime(runNumber, value) {
    const sec = parseDatetimeLocal(value);
    if (sec > 0) {
        updateRun(runNumber, { stopTimeSec: sec });
    }
}

/**
 * Set run start time to current cursor
 */
function setRunStartToCursor(runNumber) {
    setRunStartTime(runNumber);
}

/**
 * Set run stop time to current cursor
 */
function setRunStopToCursor(runNumber) {
    setRunStopTime(runNumber);
}

/**
 * Add a new music time entry
 */
async function addMusicTime(runNumber) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    const musicTimes = [...(run.musicTimes || []), { onSec: cursorTimeSec, offSec: cursorTimeSec + 60 }];
    
    await updateRun(runNumber, { musicTimes });
    openRunCard(runNumber);
}

/**
 * Update a music time entry field
 */
async function updateMusicTime(runNumber, idx, field, value) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const musicTimes = [...(run.musicTimes || [])];
    if (idx >= 0 && idx < musicTimes.length) {
        musicTimes[idx] = { ...musicTimes[idx], [field]: parseDatetimeLocal(value) };
        await updateRun(runNumber, { musicTimes });
    }
}

/**
 * Remove a music time entry
 */
async function removeMusicTime(runNumber, idx) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const musicTimes = [...(run.musicTimes || [])];
    if (idx >= 0 && idx < musicTimes.length) {
        musicTimes.splice(idx, 1);
        await updateRun(runNumber, { musicTimes });
        openRunCard(runNumber);
    }
}

/**
 * Set music on time to cursor
 */
async function setMusicOnToCursor(runNumber, idx) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    const musicTimes = [...(run.musicTimes || [])];
    if (idx >= 0 && idx < musicTimes.length) {
        musicTimes[idx] = { ...musicTimes[idx], onSec: cursorTimeSec };
        await updateRun(runNumber, { musicTimes });
        openRunCard(runNumber);
    }
}

/**
 * Set music off time to cursor
 */
async function setMusicOffToCursor(runNumber, idx) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    const musicTimes = [...(run.musicTimes || [])];
    if (idx >= 0 && idx < musicTimes.length) {
        musicTimes[idx] = { ...musicTimes[idx], offSec: cursorTimeSec };
        await updateRun(runNumber, { musicTimes });
        openRunCard(runNumber);
    }
}

/**
 * Update run name (triggered by card name editing)
 */
function updateRunName(runNumber, newName) {
    updateRun(runNumber, { runName: newName.trim() });
}

/**
 * Update run notes
 */
function updateRunNotes(runNumber, newNotes) {
    updateRun(runNumber, { analystNotes: newNotes });
}

/**
 * Update a manifest-defined field on a run
 */
async function updateRunField(runNumber, fieldId, value) {
    await updateRun(runNumber, { [fieldId]: value });
}

/**
 * Toggle array field section collapse
 */
function toggleArrayFieldCollapse(runNumber, fieldId) {
    const content = document.getElementById(`${fieldId}Content-${runNumber}`);
    const icon = document.getElementById(`${fieldId}Collapse-${runNumber}`);
    if (content && icon) {
        content.classList.toggle('collapsed');
        icon.textContent = content.classList.contains('collapsed') ? '▶' : '▼';
    }
}

/**
 * Toggle signals field section collapse
 */
function toggleSignalsFieldCollapse(runNumber, fieldId) {
    toggleArrayFieldCollapse(runNumber, fieldId);
}

/**
 * Add item to an array field
 */
async function addArrayFieldItem(runNumber, fieldId) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    // Get the manifest to find default item structure
    const manifest = window.getRunManifest ? window.getRunManifest(run.runType) : null;
    const field = manifest?.fields?.find(f => f.fieldId === fieldId);
    const itemFields = field?.config?.itemFields || [];
    
    // Build default item based on item fields
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    const newItem = {};
    for (const itemField of itemFields) {
        if (itemField.fieldType === 'datetime') {
            newItem[itemField.fieldId] = cursorTimeSec;
        } else {
            newItem[itemField.fieldId] = itemField.default || '';
        }
    }
    
    const items = [...(run[fieldId] || []), newItem];
    await updateRun(runNumber, { [fieldId]: items });
    openRunCard(runNumber);
}

/**
 * Remove item from an array field
 */
async function removeArrayFieldItem(runNumber, fieldId, idx) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const items = [...(run[fieldId] || [])];
    if (idx >= 0 && idx < items.length) {
        items.splice(idx, 1);
        await updateRun(runNumber, { [fieldId]: items });
        openRunCard(runNumber);
    }
}

/**
 * Update a field in an array item
 */
async function updateArrayFieldItem(runNumber, fieldId, idx, itemFieldId, value) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const items = [...(run[fieldId] || [])];
    if (idx >= 0 && idx < items.length) {
        items[idx] = { ...items[idx], [itemFieldId]: value };
        await updateRun(runNumber, { [fieldId]: items });
    }
}

/**
 * Set an array field item's datetime field to current cursor time
 */
async function setArrayFieldItemToCursor(runNumber, fieldId, idx, itemFieldId) {
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    await updateArrayFieldItem(runNumber, fieldId, idx, itemFieldId, cursorTimeSec);
    openRunCard(runNumber);
}

/**
 * Update a signal in the signals field
 */
async function updateSignalField(runNumber, fieldId, signalId, enabled) {
    const run = window.replays?.runs?.get(runNumber);
    if (!run) return;
    
    const signals = { ...(run[fieldId] || {}), [signalId]: enabled };
    await updateRun(runNumber, { [fieldId]: signals });
}

/**
 * Escape HTML attribute values
 */
function escapeAttr(text) {
    if (!text) return '';
    return String(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * Escape HTML content
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================================
// Exports
// ============================================================================

window.initCards = initCards;
window.openCard = openCard;
window.closeCard = closeCard;
window.updateCard = updateCard;
window.processEvent = processEvent;
window.handleActionClick = handleActionClick;
window.toggleTableCollapse = toggleTableCollapse;
window.toggleCardCollapse = toggleCardCollapse;
window.showConfigUploadDialog = showConfigUploadDialog;
window.onProtocolChange = onProtocolChange;
window.getStreamEndpointDisplay = getStreamEndpointDisplay;
window.openPresentation = openPresentation;
window.editCardName = editCardName;
window.getProtocolIcon = getProtocolIcon;
// Run/Replay card functions
window.saveNewRun = saveNewRun;
window.updateRunName = updateRunName;
window.updateRunNotes = updateRunNotes;
window.updateRunField = updateRunField;
window.updateRunStartTime = updateRunStartTime;
window.updateRunStopTime = updateRunStopTime;
window.setRunStartToCursor = setRunStartToCursor;
window.setRunStopToCursor = setRunStopToCursor;
// Array field functions (manifest-driven)
window.toggleArrayFieldCollapse = toggleArrayFieldCollapse;
window.toggleSignalsFieldCollapse = toggleSignalsFieldCollapse;
window.addArrayFieldItem = addArrayFieldItem;
window.removeArrayFieldItem = removeArrayFieldItem;
window.updateArrayFieldItem = updateArrayFieldItem;
window.setArrayFieldItemToCursor = setArrayFieldItemToCursor;
// Simple array field functions (for itemType arrays)
window.addSimpleArrayItem = addSimpleArrayItem;
window.updateSimpleArrayItem = updateSimpleArrayItem;
window.removeSimpleArrayItem = removeSimpleArrayItem;
// Signal field functions
window.updateSignalField = updateSignalField;
window.addSignalToRun = addSignalToRun;
window.toggleConstellationCollapse = toggleConstellationCollapse;
window.selectAllConstellation = selectAllConstellation;
// Legacy music time functions (backward compat)
window.addMusicTime = addMusicTime;
window.updateMusicTime = updateMusicTime;
window.removeMusicTime = removeMusicTime;
window.setMusicOnToCursor = setMusicOnToCursor;
window.setMusicOffToCursor = setMusicOffToCursor;
window.toggleMusicTimesCollapse = toggleMusicTimesCollapse;
window.toggleSignalTogglesCollapse = toggleSignalTogglesCollapse;
window.showRunError = showRunError;
window.renderAllCards = renderAllCards;
window.cards = cards;
