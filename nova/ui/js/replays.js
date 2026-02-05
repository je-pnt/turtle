/**
 * NOVA Replays - Run/Replay Management
 * 
 * Phase 11: Per-user runs for export windows + UI convenience
 * Mirrors Streams pattern: shields + cards
 * 
 * Runs are NOT truth - they are user artifacts stored in:
 *   data/users/<username>/runs/{runNumber}. {sanitizedRunName}/
 * 
 * Run types are manifest-driven (plugin discovery):
 *   nova/core/manifests/runs/*.runManifest.py
 */

// State
const replays = {
    runs: new Map(),           // runNumber → run definition
    runManifests: new Map(),   // runType → manifest (loaded from /config)
    settings: {},              // User run settings (defaultRunType, lastRunName, etc.)
    clampedRun: null,          // Currently clamped run (for timeline)
    nextRunNumber: 1           // Next available run number
};

/**
 * Initialize replays module
 */
async function initReplays() {
    // Load run manifests from config
    await loadRunManifests();
    
    // Load user settings
    await loadRunSettings();
    
    // Load runs
    await loadRuns();
    
    // Setup tab collapse handlers
    setupTabCollapse();
    
    console.log('[Replays] Initialized');
}

/**
 * Load run manifests from /config endpoint
 */
async function loadRunManifests() {
    try {
        const response = await fetch('/config');
        if (!response.ok) return;
        
        const config = await response.json();
        replays.runManifests.clear();
        
        for (const manifest of config.runManifests || []) {
            replays.runManifests.set(manifest.runType, manifest);
        }
        
        console.log(`[Replays] Loaded ${replays.runManifests.size} run manifests`);
    } catch (e) {
        console.error('[Replays] Failed to load run manifests:', e);
    }
}

/**
 * Get available run types for dropdown
 */
function getRunTypes() {
    const types = [];
    for (const [runType, manifest] of replays.runManifests) {
        types.push({
            value: runType,
            label: manifest.title,
            icon: manifest.icon,
            description: manifest.description
        });
    }
    return types;
}

/**
 * Get manifest for a run type
 */
function getRunManifest(runType) {
    return replays.runManifests.get(runType) || replays.runManifests.get('generic');
}

/**
 * Setup collapsible sidebar tabs
 */
function setupTabCollapse() {
    document.querySelectorAll('.tab-header').forEach(header => {
        header.addEventListener('click', () => {
            const tab = header.closest('.sidebar-tab');
            if (tab) {
                tab.classList.toggle('collapsed');
                // Save collapse state to localStorage
                const section = header.dataset.section;
                if (section) {
                    localStorage.setItem(`sidebar:tab:${section}:collapsed`, tab.classList.contains('collapsed'));
                }
            }
        });
    });
    
    // Restore collapse state from localStorage
    document.querySelectorAll('.sidebar-tab').forEach(tab => {
        const header = tab.querySelector('.tab-header');
        if (header) {
            const section = header.dataset.section;
            const collapsed = localStorage.getItem(`sidebar:tab:${section}:collapsed`);
            if (collapsed === 'true') {
                tab.classList.add('collapsed');
            }
        }
    });
}

/**
 * Load user's run settings
 */
async function loadRunSettings() {
    try {
        const response = await fetch('/api/runs/settings');
        if (response.ok) {
            const data = await response.json();
            replays.settings = data.settings || {};
        }
    } catch (e) {
        console.error('[Replays] Failed to load settings:', e);
    }
}

/**
 * Save user's run settings
 */
async function saveRunSettings() {
    try {
        await fetch('/api/runs/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(replays.settings)
        });
    } catch (e) {
        console.error('[Replays] Failed to save settings:', e);
    }
}

/**
 * Load runs from API
 */
async function loadRuns() {
    try {
        const response = await fetch('/api/runs');
        if (!response.ok) return;
        
        const data = await response.json();
        
        replays.runs.clear();
        let maxRunNumber = 0;
        for (const run of data.runs || []) {
            replays.runs.set(run.runNumber, run);
            if (run.runNumber > maxRunNumber) {
                maxRunNumber = run.runNumber;
            }
        }
        replays.nextRunNumber = maxRunNumber + 1;
        
        renderReplaysList();
        updateReplaysCount();
    } catch (e) {
        console.error('[Replays] Error loading runs:', e);
    }
}

/**
 * Update replays count badge
 */
function updateReplaysCount() {
    const countEl = document.getElementById('replaysCount');
    if (countEl) {
        countEl.textContent = replays.runs.size;
    }
}

/**
 * Render replays list in sidebar
 */
function renderReplaysList() {
    const container = document.getElementById('replaysList');
    if (!container) return;
    
    let html = '';
    const replayIcon = '🎬';
    
    // "Make Replay" shield (entry point)
    html += '<div class="shield-item" data-key="make-replay" onclick="openMakeReplayCard()">';
    html += '<span class="shield-icon">➕</span>';
    html += '<span class="shield-name">Make Replay</span>';
    html += '</div>';
    
    // Per-run shields
    replays.runs.forEach((run, runNumber) => {
        const runKey = `run-${runNumber}`;
        const isActive = replays.clampedRun === runNumber;
        
        html += `<div class="shield-item ${isActive ? 'selected' : ''}" data-key="${runKey}" `;
        html += `onclick="openRunCard(${runNumber})">`;
        html += `<span class="shield-icon">${replayIcon}</span>`;
        html += `<span class="shield-name">${escapeHtml(run.runName || 'Run ' + runNumber)}</span>`;
        if (run.hasBundleZip) {
            html += '<span class="shield-status online" title="Bundle ready"></span>';
        }
        html += '</div>';
    });
    
    container.innerHTML = html;
}

/**
 * Open "Make Replay" card
 */
function openMakeReplayCard() {
    const entity = {
        systemId: 'replay',
        containerId: 'system',
        uniqueId: 'makeReplay',
        displayName: 'Make Replay',
        entityType: 'make-replay'
    };
    openCard(entity);
}

/**
 * Open individual run card
 */
function openRunCard(runNumber) {
    const run = replays.runs.get(runNumber);
    if (!run) return;
    
    const entity = {
        systemId: 'replay',
        containerId: 'runs',
        uniqueId: `run-${runNumber}`,
        displayName: run.runName || `Run ${runNumber}`,
        entityType: 'run',
        ...run
    };
    openCard(entity);
}

/**
 * Create a new run (simple version - uses current cursor for times)
 */
async function createRun(runName, runType) {
    const errorDiv = document.getElementById('runError');
    if (errorDiv) errorDiv.textContent = '';
    
    // Get current cursor time for default start/stop
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    
    return createRunFull({
        runName: runName || replays.settings.lastRunName || 'Untitled Run',
        runType: runType || replays.settings.defaultRunType || 'generic',
        startTimeSec: cursorTimeSec,
        stopTimeSec: cursorTimeSec + 3600  // Default 1 hour
    });
}

/**
 * Create a new run with full parameters
 */
async function createRunFull(params) {
    const errorDiv = document.getElementById('runError');
    if (errorDiv) errorDiv.textContent = '';
    
    try {
        const response = await fetch('/api/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        
        const result = await response.json();
        if (!response.ok) {
            showRunError(result.error || 'Failed to create run');
            return null;
        }
        
        // Update settings
        replays.settings.lastRunName = result.run.runName;
        replays.settings.defaultRunType = result.run.runType;
        await saveRunSettings();
        
        // Reload runs and open the new run card
        await loadRuns();
        openRunCard(result.run.runNumber);
        
        return result.run;
    } catch (e) {
        showRunError(e.message);
        return null;
    }
}

/**
 * Update a run
 */
async function updateRun(runNumber, data) {
    try {
        const response = await fetch(`/api/runs/${runNumber}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        const result = await response.json();
        if (!response.ok) {
            showRunError(result.error || 'Failed to update run');
            return null;
        }
        
        // Update local state
        replays.runs.set(runNumber, result.run);
        renderReplaysList();
        
        return result.run;
    } catch (e) {
        showRunError(e.message);
        return null;
    }
}

/**
 * Delete a run
 */
async function deleteRun(runNumber) {
    if (!confirm('Delete this run?')) return;
    
    try {
        await fetch(`/api/runs/${runNumber}`, { method: 'DELETE' });
        
        // Clear clamp if this was the clamped run
        if (replays.clampedRun === runNumber) {
            clearClamp();
        }
        
        // Reload and close card
        await loadRuns();
        closeCard(`replay|runs|run-${runNumber}`);
    } catch (e) {
        showRunError(e.message);
    }
}

/**
 * Set start time to current cursor (On button)
 */
async function setRunStartTime(runNumber) {
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    await updateRun(runNumber, { startTimeSec: cursorTimeSec });
    
    // Re-render the card
    const run = replays.runs.get(runNumber);
    if (run) {
        openRunCard(runNumber);
    }
}

/**
 * Set stop time to current cursor (Off button)
 */
async function setRunStopTime(runNumber) {
    const cursorTimeSec = Math.floor((window.timeline?.currentTimeUs || Date.now() * 1000) / 1_000_000);
    await updateRun(runNumber, { stopTimeSec: cursorTimeSec });
    
    // Re-render the card
    const run = replays.runs.get(runNumber);
    if (run) {
        openRunCard(runNumber);
    }
}

/**
 * Clamp timeline to run's time window (UI-only)
 */
function clampToRun(runNumber) {
    const run = replays.runs.get(runNumber);
    if (!run) return;
    
    replays.clampedRun = runNumber;
    
    // Set timeline clamp (UI-only, see timeline.js)
    if (window.timeline) {
        window.timeline.clamp = {
            startTimeSec: run.startTimeSec,
            stopTimeSec: run.stopTimeSec,
            timebase: run.timebase
        };
        
        // Jump to start of run and switch to REWIND mode
        const startTimeUs = run.startTimeSec * 1_000_000;
        window.timeline.currentTimeUs = startTimeUs;
        window.timeline.mode = 'REWIND';
        window.timeline.isPlaying = true;
        
        // Use cancelAndStartStream for proper stream restart
        if (window.cancelAndStartStream) {
            window.cancelAndStartStream();
        } else if (window.startStream) {
            window.startStream();
        }
    }
    
    renderReplaysList();
    console.log(`[Replays] Clamped to run ${runNumber}: ${run.startTimeSec} - ${run.stopTimeSec}`);
}

/**
 * Clear timeline clamp
 */
function clearClamp() {
    replays.clampedRun = null;
    
    if (window.timeline) {
        window.timeline.clamp = null;
    }
    
    renderReplaysList();
    console.log('[Replays] Clamp cleared');
}

/**
 * Download bundle for a run
 */
async function downloadBundle(runNumber) {
    const statusEl = document.getElementById(`bundleStatus-${runNumber}`);
    if (statusEl) statusEl.textContent = 'Generating...';
    
    try {
        const response = await fetch(`/api/runs/${runNumber}/bundle`, {
            method: 'POST'
        });
        
        if (!response.ok) {
            const err = await response.json();
            if (statusEl) statusEl.textContent = err.error || 'Failed';
            return;
        }
        
        // Download the file
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `run${runNumber}_bundle.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        if (statusEl) statusEl.textContent = 'Downloaded';
        
        // Reload to update hasBundleZip status
        await loadRuns();
    } catch (e) {
        if (statusEl) statusEl.textContent = e.message;
    }
}

/**
 * Show error message
 */
function showRunError(msg) {
    const errorDiv = document.getElementById('runError');
    if (errorDiv) errorDiv.textContent = msg;
    console.error('[Replays]', msg);
}

/**
 * Escape HTML for safe rendering
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Exports
window.initReplays = initReplays;
window.replays = replays;
window.loadRuns = loadRuns;
window.openMakeReplayCard = openMakeReplayCard;
window.openRunCard = openRunCard;
window.createRun = createRun;
window.createRunFull = createRunFull;
window.updateRun = updateRun;
window.deleteRun = deleteRun;
window.setRunStartTime = setRunStartTime;
window.setRunStopTime = setRunStopTime;
window.clampToRun = clampToRun;
window.clearClamp = clearClamp;
window.downloadBundle = downloadBundle;
window.getRunTypes = getRunTypes;
window.getRunManifest = getRunManifest;
