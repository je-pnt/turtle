/**
 * NOVA Presentation UI - View-only customization panel
 * 
 * Phase 10 (phase9-11Updated.md):
 * - Per-user overrides > admin defaults > factory defaults
 * - Allowed keys: displayName, modelRef, color, scale
 * - Changes are view-only, do not affect truth
 * 
 * Architecture (nova architecture.md):
 * - Presentation is NOT stored in truth lanes
 * - Fetched via REST API, applied client-side
 */

(function() {
    'use strict';

    // ============================================================================
    // State
    // ============================================================================
    
    let availableModels = [];
    let currentEntityKey = null;
    let currentPresentation = {};
    let isAdmin = false;

    // ============================================================================
    // API
    // ============================================================================
    
    async function loadModels() {
        try {
            const response = await fetch('/api/presentation/models');
            if (response.ok) {
                const data = await response.json();
                availableModels = data.models || [];
                console.log('[Presentation] Loaded', availableModels.length, 'models');
            }
        } catch (e) {
            console.warn('[Presentation] Failed to load models:', e);
        }
    }
    
    // Helper to split entityKey into scopeId and uniqueId
    function splitEntityKey(entityKey) {
        const parts = entityKey.split('|');
        if (parts.length === 3) {
            return {
                scopeId: `${parts[0]}|${parts[1]}`,
                uniqueId: parts[2]
            };
        }
        // Fallback for simple keys
        return { scopeId: 'default', uniqueId: entityKey };
    }
    
    async function loadPresentation(entityKey) {
        try {
            const { scopeId, uniqueId } = splitEntityKey(entityKey);
            console.log('[Presentation] Loading for scopeId:', scopeId, 'uniqueId:', uniqueId);
            const response = await fetch(`/api/presentation/${encodeURIComponent(scopeId)}`);
            if (response.ok) {
                const data = await response.json();
                // Server returns { scopeId, overrides: { uniqueId: presentation } }
                currentPresentation = data.overrides?.[uniqueId] || {};
                console.log('[Presentation] Loaded:', currentPresentation);
                return currentPresentation;
            }
        } catch (e) {
            console.warn('[Presentation] Failed to load:', e);
        }
        return {};
    }
    
    async function savePresentation(entityKey, overrides, isDefault = false) {
        const { scopeId, uniqueId } = splitEntityKey(entityKey);
        const url = isDefault 
            ? `/api/presentation/defaults/${encodeURIComponent(scopeId)}/${encodeURIComponent(uniqueId)}`
            : `/api/presentation/${encodeURIComponent(scopeId)}/${encodeURIComponent(uniqueId)}`;
        
        try {
            const response = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(overrides)
            });
            if (response.ok) {
                console.log('[Presentation] Saved:', entityKey);
                return true;
            }
        } catch (e) {
            console.warn('[Presentation] Failed to save:', e);
        }
        return false;
    }
    
    async function clearUserOverride(entityKey, key) {
        try {
            const { scopeId, uniqueId } = splitEntityKey(entityKey);
            const response = await fetch(`/api/presentation/${encodeURIComponent(scopeId)}/${encodeURIComponent(uniqueId)}?key=${key}`, {
                method: 'DELETE'
            });
            return response.ok;
        } catch (e) {
            console.warn('[Presentation] Failed to clear:', e);
            return false;
        }
    }

    // ============================================================================
    // UI
    // ============================================================================
    
    function openPresentationEditor(entityKey) {
        console.log('[Presentation] Opening editor for:', entityKey);
        currentEntityKey = entityKey;
        
        // Create modal if doesn't exist
        let modal = document.getElementById('presentationModal');
        if (!modal) {
            console.log('[Presentation] Creating modal element');
            modal = document.createElement('div');
            modal.id = 'presentationModal';
            modal.className = 'presentation-modal';
            document.body.appendChild(modal);
        }
        
        // Load data and render
        Promise.all([
            loadPresentation(entityKey),
            availableModels.length === 0 ? loadModels() : Promise.resolve()
        ]).then(() => {
            console.log('[Presentation] Rendering editor, models:', availableModels.length);
            renderEditor(modal);
            modal.classList.add('open');
        }).catch(e => {
            console.error('[Presentation] Failed to open:', e);
        });
    }
    
    function closePresentationEditor() {
        const modal = document.getElementById('presentationModal');
        if (modal) {
            modal.classList.remove('open');
        }
        currentEntityKey = null;
        currentPresentation = {};
    }
    
    function renderEditor(modal) {
        const p = currentPresentation;
        const colorRgb = p.color || [0, 212, 255];
        const colorHex = rgbToHex(colorRgb);
        
        modal.innerHTML = `
            <div class="presentation-content">
                <div class="presentation-header">
                    <span>Edit Presentation</span>
                    <button class="presentation-close" onclick="window.NovaPres.close()">&times;</button>
                </div>
                <div class="presentation-entity">${currentEntityKey}</div>
                
                <div class="presentation-form">
                    <label>Color</label>
                    <div class="color-row">
                        <input type="color" id="presColor" value="${colorHex}">
                        <span class="color-preview" style="background-color: ${colorHex}"></span>
                        <button type="button" class="reset-btn" onclick="window.NovaPres.resetColor()">Reset</button>
                    </div>
                    
                    <label>Scale</label>
                    <input type="number" id="presScale" min="0.1" max="10" step="0.1" value="${p.scale || 1.0}" class="scale-input">
                    
                    <label>Model</label>
                    <select id="presModel">
                        <option value="">(default)</option>
                        ${availableModels.map(m => 
                            `<option value="${m}" ${p.modelRef === m ? 'selected' : ''}>${m}</option>`
                        ).join('')}
                    </select>
                </div>
                
                <div class="presentation-actions">
                    <button type="button" class="btn-secondary" onclick="window.NovaPres.close()">Cancel</button>
                    <button type="button" class="btn-primary" onclick="window.NovaPres.save()">Save</button>
                </div>
                
                ${isAdmin ? `
                <div class="presentation-admin">
                    <hr>
                    <button type="button" class="btn-admin" onclick="window.NovaPres.saveAsDefault()">Save as Admin Default</button>
                </div>
                ` : ''}
            </div>
        `;
        
        // Bind events
        document.getElementById('presColor').addEventListener('input', (e) => {
            document.querySelector('.color-preview').style.backgroundColor = e.target.value;
        });
    }
    
    async function save() {
        if (!currentEntityKey) return;
        
        const overrides = gatherFormValues();
        const success = await savePresentation(currentEntityKey, overrides, false);
        
        if (success) {
            // Apply to map immediately
            if (window.NovaMap) {
                window.NovaMap.updateEntityPresentation(currentEntityKey, overrides);
            }
            closePresentationEditor();
        }
    }
    
    async function saveAsDefault() {
        if (!currentEntityKey) return;
        
        const overrides = gatherFormValues();
        const success = await savePresentation(currentEntityKey, overrides, true);
        
        if (success) {
            closePresentationEditor();
        }
    }
    
    function gatherFormValues() {
        const overrides = {};
        
        const colorHex = document.getElementById('presColor').value;
        overrides.color = hexToRgb(colorHex);
        
        const scale = parseFloat(document.getElementById('presScale').value);
        if (scale && scale !== 1.0) overrides.scale = scale;
        
        const modelRef = document.getElementById('presModel').value;
        if (modelRef) overrides.modelRef = modelRef;
        
        return overrides;
    }
    
    function resetColor() {
        const defaultColor = '#00d4ff';
        document.getElementById('presColor').value = defaultColor;
        document.querySelector('.color-preview').style.backgroundColor = defaultColor;
    }

    // ============================================================================
    // Helpers
    // ============================================================================
    
    function rgbToHex(rgb) {
        if (!Array.isArray(rgb) || rgb.length < 3) return '#00d4ff';
        const r = Math.max(0, Math.min(255, Math.round(rgb[0])));
        const g = Math.max(0, Math.min(255, Math.round(rgb[1])));
        const b = Math.max(0, Math.min(255, Math.round(rgb[2])));
        return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
    }
    
    function hexToRgb(hex) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? [
            parseInt(result[1], 16),
            parseInt(result[2], 16),
            parseInt(result[3], 16)
        ] : [0, 212, 255];
    }
    
    function escapeHtml(str) {
        if (!str) return '';
        return str.replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    // ============================================================================
    // Public API
    // ============================================================================
    
    window.NovaPres = {
        open: openPresentationEditor,
        close: closePresentationEditor,
        save: save,
        saveAsDefault: saveAsDefault,
        resetColor: resetColor,
        setAdmin: (val) => { isAdmin = val; }
    };

})();
