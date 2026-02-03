/**
 * NOVA Display - Event routing to shields, cards, and map
 * 
 * Architecture (nova architecture.md):
 * - Events route to shields (entity discovery) and cards (UI state)
 * - Server cursor is authoritative for timeline position
 * - UI lane with lat/lon/alt routes to Cesium map (Phase 10)
 * 
 * Design (guidelines.md):
 * - Single routing path
 * - No legacy/parallel code
 * - Clear logging
 */

function initDisplay() {
    console.log('[Display] Initializing...');
    if (window.initEntities) window.initEntities();
    if (window.initCards) window.initCards();
    if (window.NovaMap) window.NovaMap.init();
    console.log('[Display] Initialized');
}

/**
 * Append events - SINGLE entry point for all events
 * Routes to shields, cards, and map
 */
function appendEvents(events) {
    if (!events || events.length === 0) return;
    

    
    for (const event of events) {
        // Route to shields (entity discovery from Descriptors)
        if (window.processEntityEvent) {
            window.processEntityEvent(event);
        }
        
        // Route to cards (UI state updates)
        if (window.processEvent) {
            window.processEvent(event);
        }
        
        // Route UI lane geospatial data to map (Phase 10)
        if (event.lane === 'ui' && window.NovaMap) {
            const data = event.data || {};
            if (data.lat !== undefined && data.lon !== undefined) {
                const uniqueId = event.uniqueId;
                if (uniqueId) {
                    window.NovaMap.updateEntity(uniqueId, data);
                }
            }
        }
        
        // Dispatch metadata events for chat and other listeners (Phase 9)
        if (event.lane === 'metadata') {
            window.dispatchEvent(new CustomEvent('nova:metadataEvent', { detail: event }));
        }
        
        // Server-authoritative cursor (anti-drift)
        if (window.timeline && event._serverCursor) {
            const oldCursor = window.timeline.currentTimeUs;
            window.timeline.currentTimeUs = event._serverCursor;
            // Log significant cursor jumps (> 5 seconds)
            if (oldCursor && Math.abs(event._serverCursor - oldCursor) > 5_000_000) {
                console.log('[Display] Cursor jump:', 
                    new Date(oldCursor / 1000).toISOString(), '→',
                    new Date(event._serverCursor / 1000).toISOString());
            }
            // Sync map cursor (Phase 10)
            if (window.NovaMap) {
                window.NovaMap.setCursorTime(event._serverCursor);
            }
        }
        
        // Track last data time for LIVE mode
        const timeStr = event.canonicalTruthTime || event.sourceTruthTime;
        if (timeStr && window.timeline) {
            try {
                const dt = new Date(timeStr);
                window.timeline.lastDataTimeUs = dt.getTime() * 1000;
                
                if (window.timeline.mode === 'LIVE') {
                    window.timeline.currentTimeUs = window.timeline.lastDataTimeUs;
                }
            } catch (e) {
                // Ignore parse errors
            }
        }
    }
}

// Exports
window.initDisplay = initDisplay;
window.appendEvents = appendEvents;
