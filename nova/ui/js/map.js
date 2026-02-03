/**
 * NOVA Map - Cesium Geospatial Background
 * 
 * Phase 10 (phase9-11Updated.md):
 * - Cesium is full-page background behind panels
 * - Geospatial updates from UI lane (lat, lon, alt)
 * - Clock driven by cursor time (server-authoritative)
 * - Local-only assets (no external network calls)
 * - Uses SampledPositionProperty for smooth interpolation
 * 
 * Architecture (nova architecture.md):
 * - UI lane drives geospatial rendering
 * - Presentation overrides (displayName, color, model, scale) are view-only
 * - Replay/live behavior is deterministic
 */

(function() {
    'use strict';

    // ============================================================================
    // State
    // ============================================================================
    
    let viewer = null;
    const entities = new Map();  // uniqueId → {entity, position, lastUpdate}
    const presentationCache = new Map();  // scopeId → { uniqueId → presentation }
    let clockStart = null;
    let clockStop = null;
    
    // Config (interpolation settings for smooth motion)
    const config = {
        interpolationDegree: 1,
        extrapolationDuration: 2.0,
        trailTime: 60,
        defaultColor: [0, 212, 255],  // NOVA accent color
        defaultModel: 'Falcon.glb'    // Default 3D model for entities
    };
    
    // Cesium helpers (initialized after library loads)
    let JD = null;

    // ============================================================================
    // Presentation Cache
    // ============================================================================
    
    async function loadAllPresentations() {
        // Load presentations from server - no scope needed
        // Server resolves based on user's effective scopes
        try {
            const response = await fetch('/api/presentation', { credentials: 'same-origin' });
            if (response.ok) {
                const data = await response.json();
                if (data.overrides) {
                    // Group by scopeId for cache structure
                    for (const [uniqueId, pres] of Object.entries(data.overrides)) {
                        const scopeId = pres.scopeId || 'default';
                        if (!presentationCache.has(scopeId)) {
                            presentationCache.set(scopeId, {});
                        }
                        presentationCache.get(scopeId)[uniqueId] = pres;
                    }
                    console.log('[Map] Loaded presentations:', Object.keys(data.overrides).length, 'entities');
                }
            }
        } catch (e) {
            console.warn('[Map] Failed to load presentations:', e);
        }
    }
    
    function getPresentationForEntity(uniqueId) {
        // Search all cached scopes for this uniqueId
        for (const [scopeId, overrides] of presentationCache) {
            if (overrides[uniqueId]) {
                return overrides[uniqueId];
            }
        }
        return null;
    }
    
    function cachePresentationUpdate(entityKey, presentation) {
        // Update cache when user changes presentation
        const parts = entityKey.split('|');
        if (parts.length === 3) {
            const scopeId = `${parts[0]}|${parts[1]}`;
            const uniqueId = parts[2];
            if (!presentationCache.has(scopeId)) {
                presentationCache.set(scopeId, {});
            }
            const scope = presentationCache.get(scopeId);
            scope[uniqueId] = { ...scope[uniqueId], ...presentation };
        }
    }

    // ============================================================================
    // Initialization
    // ============================================================================
    
    function initMap() {
        // Wait for Cesium library
        if (typeof Cesium === 'undefined') {
            console.warn('[Map] Cesium not loaded yet, retrying...');
            setTimeout(initMap, 100);
            return;
        }
        
        console.log('[Map] Initializing Cesium...');
        
        // Initialize Cesium helpers
        JD = Cesium.JulianDate;
        
        // Disable Ion (local-only)
        Cesium.Ion.defaultAccessToken = '';
        
        // Create viewer in the map container
        const container = document.getElementById('mapContainer');
        if (!container) {
            console.error('[Map] mapContainer not found');
            return;
        }
        
        try {
            viewer = new Cesium.Viewer(container, {
                animation: false,           // We use NOVA timeline
                timeline: false,            // We use NOVA timeline
                baseLayerPicker: false,
                sceneModePicker: false,
                geocoder: false,
                homeButton: false,
                navigationHelpButton: false,
                infoBox: true,
                selectionIndicator: true,
                shouldAnimate: true,
                imageryProvider: false,     // We add our own
                terrainProvider: new Cesium.EllipsoidTerrainProvider({}),
                requestRenderMode: true,    // Render only when needed
                maximumRenderTimeChange: Infinity
            });
            
            // Remove all default imagery
            viewer.imageryLayers.removeAll();
            
            // Style
            viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#0a0a0a');
            viewer.scene.globe.enableLighting = false;
            viewer.cesiumWidget.creditContainer.style.display = 'none';
            
            // Initialize clock
            const now = JD.now();
            const start = JD.addMinutes(now, -10, new JD());
            const stop = JD.addMinutes(now, 10, new JD());
            setClockWindow(start, stop, now);
            
            // Load local basemap
            loadBasemap('/ui/assets/imagery/world.jpg');
            
            // Entity selection handler
            viewer.selectedEntityChanged.addEventListener(onEntitySelected);
            
            console.log('[Map] Cesium initialized successfully');
            
            // Load user's presentation overrides
            loadAllPresentations();
            
            // Request initial render
            viewer.scene.requestRender();
            
        } catch (e) {
            console.error('[Map] Failed to initialize Cesium:', e);
        }
    }
    
    function loadBasemap(url) {
        const fallbackDataUrl = 
            'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAQAAADq3Q0NAAAADElEQVQImWP4z8DAAAIHAK2H8p1rAAAAAElFTkSuQmCC';
        
        const addProvider = (imgUrl) => {
            try {
                const provider = new Cesium.SingleTileImageryProvider({
                    url: imgUrl,
                    rectangle: Cesium.Rectangle.fromDegrees(-180.0, -90.0, 180.0, 90.0)
                });
                viewer.imageryLayers.addImageryProvider(provider);
                console.log('[Map] Basemap loaded:', imgUrl);
                viewer.scene.requestRender();
            } catch (e) {
                console.error('[Map] Failed to add basemap:', e);
            }
        };
        
        // Test image load
        const img = new Image();
        img.onload = () => addProvider(url);
        img.onerror = () => {
            console.warn('[Map] Basemap load failed, using fallback');
            addProvider(fallbackDataUrl);
        };
        img.src = url;
    }

    // ============================================================================
    // Clock Management
    // ============================================================================
    
    function setClockWindow(start, stop, currentTime) {
        if (!viewer) return;
        
        clockStart = start;
        clockStop = stop;
        viewer.clock.startTime = start.clone();
        viewer.clock.stopTime = stop.clone();
        viewer.clock.currentTime = currentTime ? currentTime.clone() : start.clone();
        viewer.clock.clockRange = Cesium.ClockRange.CLAMPED;
        viewer.clock.multiplier = 1;
        viewer.clock.shouldAnimate = false;  // NOVA controls animation
    }
    
    function setCursorTime(timeUs) {
        if (!viewer || !JD) return;
        
        // Convert microseconds to JulianDate
        const timeMs = timeUs / 1000;
        const julianTime = JD.fromDate(new Date(timeMs));
        
        // Extend clock window if needed
        if (!clockStart || JD.lessThan(julianTime, clockStart)) {
            clockStart = JD.addSeconds(julianTime, -60, new JD());
        }
        if (!clockStop || JD.greaterThan(julianTime, clockStop)) {
            clockStop = JD.addSeconds(julianTime, 60, new JD());
        }
        
        viewer.clock.startTime = clockStart.clone();
        viewer.clock.stopTime = clockStop.clone();
        viewer.clock.currentTime = julianTime.clone();
        
        viewer.scene.requestRender();
    }

    // ============================================================================
    // Entity Management
    // ============================================================================
    
    function updateEntity(uniqueId, data, presentation) {
        if (!viewer || !JD) return;
        
        const lat = data.lat;
        const lon = data.lon;
        const alt = data.alt || 0;
        
        if (lat === undefined || lon === undefined) return;
        if (typeof lat !== 'number' || typeof lon !== 'number') return;
        
        // Use provided presentation, or look up from cache
        const pres = presentation || getPresentationForEntity(uniqueId);
        
        // Get or create entity record
        let record = entities.get(uniqueId);
        
        if (!record) {
            // Create new entity
            const position = new Cesium.SampledPositionProperty();
            position.setInterpolationOptions({
                interpolationDegree: config.interpolationDegree,
                interpolationAlgorithm: Cesium.LinearApproximation
            });
            position.forwardExtrapolationType = Cesium.ExtrapolationType.EXTRAPOLATE;
            position.forwardExtrapolationDuration = config.extrapolationDuration;
            
            const color = toCesiumColor(pres?.color || config.defaultColor);
            const displayName = pres?.displayName || uniqueId;
            const scale = pres?.scale || 1.0;
            
            const entityOptions = {
                id: uniqueId,
                name: displayName,
                position: position,
                label: {
                    text: displayName,
                    font: '12px Segoe UI',
                    fillColor: color,
                    outlineColor: Cesium.Color.BLACK,
                    outlineWidth: 2,
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -28)
                },
                path: {
                    leadTime: 0,
                    trailTime: config.trailTime,
                    width: 2,
                    material: new Cesium.PolylineOutlineMaterialProperty({
                        color: color.withAlpha(0.8),
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 1
                    })
                },
                model: {
                    uri: `/ui/assets/models/${pres?.modelRef || config.defaultModel}`,
                    scale: scale,
                    color: color,
                    colorBlendMode: Cesium.ColorBlendMode.MIX,
                    colorBlendAmount: 0.5,
                    minimumPixelSize: 48
                }
            };
            
            const entity = viewer.entities.add(entityOptions);
            record = { entity, position, lastUpdate: 0 };
            entities.set(uniqueId, record);
            
            console.log('[Map] Created entity:', uniqueId, 'displayName:', displayName, 'model:', entityOptions.model.uri);
        }
        
        // Add position sample at current time
        const currentTime = viewer.clock.currentTime.clone();
        const cartesian = Cesium.Cartesian3.fromDegrees(lon, lat, alt);
        record.position.addSample(currentTime, cartesian);
        record.lastUpdate = Date.now();
        
        // Request render
        viewer.scene.requestRender();
    }
    
    function updateEntityPresentation(entityKey, presentation) {
        if (!viewer) return;
        
        // Cache the update for persistence
        cachePresentationUpdate(entityKey, presentation);
        
        // Extract uniqueId from entityKey (systemId|containerId|uniqueId)
        const parts = entityKey.split('|');
        const uniqueId = parts.length === 3 ? parts[2] : entityKey;
        
        const record = entities.get(uniqueId);
        if (!record) {
            console.log('[Map] Entity not found for presentation update:', uniqueId);
            return;
        }
        
        const entity = record.entity;
        
        // Handle displayName update
        if (presentation?.displayName !== undefined) {
            entity.name = presentation.displayName;
            if (entity.label) {
                entity.label.text = presentation.displayName;
            }
            console.log('[Map] Updated displayName:', uniqueId, '->', presentation.displayName);
        }
        
        // Handle visual presentation updates
        if (presentation?.color || presentation?.scale || presentation?.modelRef) {
            const color = toCesiumColor(presentation?.color || config.defaultColor);
            const scale = presentation?.scale || 1.0;
            const modelRef = presentation?.modelRef || config.defaultModel;
            
            console.log('[Map] Updating visual presentation:', uniqueId, 'color:', presentation?.color, 'scale:', scale, 'model:', modelRef);
            
            // Update label color
            if (entity.label) {
                entity.label.fillColor = color;
            }
            
            // Update path color
            if (entity.path) {
                entity.path.material = new Cesium.PolylineOutlineMaterialProperty({
                    color: color.withAlpha(0.8),
                    outlineColor: Cesium.Color.BLACK,
                    outlineWidth: 1
                });
            }
            
            // Update model
            entity.model = new Cesium.ModelGraphics({
                uri: `/ui/assets/models/${modelRef}`,
                scale: scale,
                color: color,
                colorBlendMode: Cesium.ColorBlendMode.MIX,
                colorBlendAmount: 0.5,
                minimumPixelSize: 48
            });
        }
        
        viewer.scene.requestRender();
    }
    
    function removeEntity(uniqueId) {
        if (!viewer) return;
        
        const record = entities.get(uniqueId);
        if (record) {
            viewer.entities.remove(record.entity);
            entities.delete(uniqueId);
            viewer.scene.requestRender();
        }
    }
    
    function flyToEntity(uniqueId) {
        if (!viewer) return;
        
        const record = entities.get(uniqueId);
        if (record) {
            viewer.flyTo(record.entity, {
                duration: 1.5,
                offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), 5000)
            });
        }
    }
    
    function onEntitySelected() {
        // Could dispatch event for UI coordination
        if (viewer.selectedEntity) {
            const uniqueId = viewer.selectedEntity.id;
            window.dispatchEvent(new CustomEvent('nova:mapEntitySelected', { 
                detail: { uniqueId } 
            }));
        }
    }

    // ============================================================================
    // Real-time Presentation Sync
    // ============================================================================
    
    function handlePresentationUpdate(msg) {
        // Handle WebSocket presentation update from server
        const { scopeId, uniqueId, data, deleted, isDefault } = msg;
        
        if (deleted) {
            // Remove from cache
            if (presentationCache.has(scopeId)) {
                delete presentationCache.get(scopeId)[uniqueId];
            }
        } else if (data) {
            // Update cache
            if (!presentationCache.has(scopeId)) {
                presentationCache.set(scopeId, {});
            }
            const scope = presentationCache.get(scopeId);
            scope[uniqueId] = { ...scope[uniqueId], ...data, scopeId };
            
            // Re-render entity if it exists
            const entityState = entities.get(uniqueId);
            if (entityState) {
                // Build entityKey for updateEntityPresentation
                const entityKey = `${scopeId}|${uniqueId}`;
                updateEntityPresentation(entityKey, data);
            }
        }
        
        console.log('[Map] Presentation update:', scopeId, uniqueId, deleted ? '(deleted)' : '');
    }

    // ============================================================================
    // Helpers
    // ============================================================================
    
    function toCesiumColor(rgb) {
        if (!rgb || !Array.isArray(rgb) || rgb.length < 3) {
            return Cesium.Color.CYAN;
        }
        const r = Math.max(0, Math.min(255, rgb[0])) / 255.0;
        const g = Math.max(0, Math.min(255, rgb[1])) / 255.0;
        const b = Math.max(0, Math.min(255, rgb[2])) / 255.0;
        return new Cesium.Color(r, g, b, 1.0);
    }
    
    function requestRender() {
        if (viewer) {
            viewer.scene.requestRender();
        }
    }

    // ============================================================================
    // Public API
    // ============================================================================
    
    window.NovaMap = {
        init: initMap,
        setCursorTime: setCursorTime,
        updateEntity: updateEntity,
        updateEntityPresentation: updateEntityPresentation,
        removeEntity: removeEntity,
        flyToEntity: flyToEntity,
        requestRender: requestRender,
        handlePresentationUpdate: handlePresentationUpdate,
        getViewer: () => viewer
    };

})();
