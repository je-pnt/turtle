/**
 * NOVA Timeline Controller - Deterministic LIVE/REWIND
 * 
 * State machine with two modes:
 * - LIVE: Tracks live tail, rate=1, cursor at right edge
 * - REWIND: DB-backed playback from specific time, any rate
 * 
 * Invariants:
 * - Mode transitions always cancel+restart stream
 * - Cursor position reflects actual playback time
 * - No silent fallbacks or parallel pipelines
 */

// Timeline state - single source of truth
const timeline = {
    mode: 'LIVE',           // 'LIVE' or 'REWIND'
    isPlaying: true,         // Playing or paused
    rate: 1.0,               // Playback rate (+ forward, - backward)
    timebase: 'canonical',   // 'source' or 'canonical' (loaded from config)
    currentTimeUs: 0,        // Current cursor position (microseconds)
    lastDataTimeUs: null,    // Last event time we actually received (for REWIND)
    playbackRequestId: null, // Active stream fence token
    
    // Time window (1 hour max visible)
    windowStartUs: 0,
    windowEndUs: 0,
    windowSizeUs: 3600 * 1_000_000  // 1 hour in microseconds
};

async function initTimeline() {
    // Fetch role-based configuration from server
    try {
        const response = await fetch('/config');
        if (response.ok) {
            const config = await response.json();
            timeline.timebase = config.defaultTimebase || 'canonical';
            timeline.rate = config.defaultRate || 1.0;
            
            // Set UI selects to match config
            const timebaseSelect = document.getElementById('timebaseSelect');
            if (timebaseSelect) {
                timebaseSelect.value = timeline.timebase;
            }
        } else {
            console.warn('[Timeline] Failed to load config, using defaults');
        }
    } catch (error) {
        console.warn('[Timeline] Config fetch error, using defaults:', error);
    }
    
    // Initialize timeline at current time (LIVE will query backward to find data)
    const nowUs = Date.now() * 1000;
    timeline.currentTimeUs = nowUs;
    timeline.windowEndUs = nowUs;
    timeline.windowStartUs = nowUs - timeline.windowSizeUs;
    
    // Attach event listeners
    document.getElementById('playPauseBtn').addEventListener('click', handlePlayPause);
    document.getElementById('liveBtn').addEventListener('click', handleJumpToLive);
    document.getElementById('speedInput').addEventListener('change', handleSpeedChange);
    document.getElementById('timebaseSelect').addEventListener('change', handleTimebaseChange);
    document.getElementById('datetimeInput').addEventListener('change', handleDatetimeJump);
    
    // Use 'change' instead of 'input' for slider to avoid rapid firing
    const slider = document.getElementById('timeSlider');
    let sliderDragTimeout = null;
    slider.addEventListener('input', (event) => {
        // Update cursor visual immediately
        const position = parseFloat(event.target.value);
        const windowRange = timeline.windowEndUs - timeline.windowStartUs;
        const timeUs = timeline.windowStartUs + (position / 100) * windowRange;
        timeline.currentTimeUs = timeUs;
        updateDisplay();
    });
    slider.addEventListener('change', (event) => {
        // On release, actually jump to the time
        clearTimeout(sliderDragTimeout);
        sliderDragTimeout = setTimeout(() => handleSliderDrag(event), 300);
    });
    
    // Update display every 100ms (10 Hz)
    setInterval(updateDisplay, 100);
    updateDisplay();
    
    // On connect: query for entity metadata, then start stream
    const checkConnection = setInterval(() => {
        if (window.wsState?.connected) {
            clearInterval(checkConnection);
            // Query for entity descriptors first (shields need this)
            queryEntityMetadata();
            // Then start the live/replay stream
            startStream();
        }
    }, 100);
}

/**
 * Query for entity metadata (Descriptors) on connect
 * Architecture: Use query API to fetch metadata needed for shields
 */
function queryEntityMetadata() {
    if (!window.wsState?.connected) return;
    
    // Query all Descriptors from the last 24 hours (they're rare)
    const nowMs = Date.now();
    const oneDayAgoMs = nowMs - (24 * 60 * 60 * 1000);
    
    const request = {
        type: 'query',
        startTime: oneDayAgoMs * 1000,  // Microseconds
        stopTime: nowMs * 1000,
        timebase: timeline.timebase,
        filters: {
            lanes: ['metadata']
        }
    };
    
    window.sendWebSocketMessage(request);
    console.log('[Timeline] Queried entity metadata');
}

function handlePlayPause() {
    if (timeline.mode === 'LIVE' && timeline.isPlaying) {
        // Pause in LIVE → switch to REWIND (paused at current time)
        timeline.mode = 'REWIND';
        timeline.isPlaying = false;
        timeline.currentTimeUs = Date.now() * 1000;  // Snapshot current time
        cancelStream();  // Mode switch requires restart
        updateUI();
    } else if (timeline.mode === 'REWIND') {
        // Toggle play/pause in REWIND
        timeline.isPlaying = !timeline.isPlaying;
        
        if (timeline.isPlaying) {
            // Resume: restart stream at current position with rate=1
            setTimeout(() => {
                if (window.wsState?.connected && !window.wsState?.reconnecting) {
                    startStream();
                }
            }, 200);
        } else {
            // Pause: change rate to 0 instead of canceling
            // This keeps cursor position for bound output streams
            setPlaybackRate(0);
        }
        updateUI();
    }
}

function handleJumpToLive() {
    timeline.mode = 'LIVE';
    timeline.isPlaying = true;
    timeline.rate = 1.0;  // Reset to 1x forward
    // timebase unchanged - maintains role-based default from config
    timeline.currentTimeUs = Date.now() * 1000;
    timeline.windowEndUs = timeline.currentTimeUs;
    timeline.windowStartUs = timeline.currentTimeUs - timeline.windowSizeUs;
    
    // Update UI inputs
    document.getElementById('speedInput').value = '1.0';
    document.getElementById('datetimeInput').value = '';
    document.getElementById('timeSlider').value = '100';
    
    // Start new stream directly (server handles implicit cancel)
    setTimeout(() => {
        if (window.wsState?.connected && !window.wsState?.reconnecting) {
            startStream();
        }
    }, 200);
    updateUI();
}

function handleSpeedChange(event) {
    const newRate = parseFloat(event.target.value);
    if (isNaN(newRate) || newRate === 0) {
        event.target.value = timeline.rate;
        return;
    }
    
    timeline.rate = newRate;
    
    // Rate change always switches to REWIND mode
    if (timeline.mode === 'LIVE') {
        timeline.mode = 'REWIND';
        // Use last data time if available, otherwise current cursor position
        if (timeline.lastDataTimeUs) {
            timeline.currentTimeUs = timeline.lastDataTimeUs;
        } else {
        }
        // Keep cursor where it is (don't jump to center)
        timeline.windowEndUs = timeline.currentTimeUs + (timeline.windowSizeUs / 2);
        timeline.windowStartUs = timeline.currentTimeUs - (timeline.windowSizeUs / 2);
    }
    
    if (timeline.isPlaying) {
        // Don't cancel, just start new stream (server handles implicit cancel)
        setTimeout(() => {
            if (window.wsState?.connected && !window.wsState?.reconnecting) {
                startStream();
            }
        }, 200);
    }
    updateUI();
}

function handleTimebaseChange(event) {
    const newTimebase = event.target.value;
    if (newTimebase !== 'source' && newTimebase !== 'canonical') {
        console.error('[Timeline] Invalid timebase:', newTimebase);
        event.target.value = timeline.timebase;
        return;
    }
    
    timeline.timebase = newTimebase;
    
    // Timebase change requires cancel + restart stream (different ordering)
    if (timeline.isPlaying) {
        setTimeout(() => {
            if (window.wsState?.connected && !window.wsState?.reconnecting) {
                startStream();
            }
        }, 200);
    }
    updateUI();
}

function handleDatetimeJump(event) {
    const dateStr = event.target.value;
    if (!dateStr) return;
    
    const targetDate = new Date(dateStr);
    const targetTimeUs = targetDate.getTime() * 1000;
    
    timeline.mode = 'REWIND';
    timeline.currentTimeUs = targetTimeUs;
    timeline.windowEndUs = targetTimeUs + (timeline.windowSizeUs / 2);
    timeline.windowStartUs = targetTimeUs - (timeline.windowSizeUs / 2);
    
    if (timeline.isPlaying) {
        setTimeout(() => {
            if (window.wsState?.connected && !window.wsState?.reconnecting) {
                startStream();
            }
        }, 200);
    }
    updateUI();
}

function handleSliderDrag(event) {
    const position = parseFloat(event.target.value);
    
    if (position >= 99) {
        // Dragged to far right → jump to LIVE
        handleJumpToLive();
    } else {
        // Calculate time from slider position
        const timeUs = timeline.windowStartUs + (position / 100) * (timeline.windowEndUs - timeline.windowStartUs);
        
        timeline.mode = 'REWIND';
        timeline.currentTimeUs = timeUs;
        
        if (timeline.isPlaying) {
            setTimeout(() => {
                if (window.wsState?.connected && !window.wsState?.reconnecting) {
                    startStream();
                }
            }, 200);
        }
        updateUI();
    }
}

function updateDisplay() {
    if (timeline.mode === 'LIVE' && timeline.isPlaying) {
        // In LIVE mode, cursor follows actual data time (set by appendEvents)
        // Only update if we haven't received data yet
        if (!timeline.lastDataTimeUs) {
            timeline.currentTimeUs = Date.now() * 1000;
        }
        timeline.windowEndUs = timeline.currentTimeUs + (timeline.windowSizeUs / 2);
        timeline.windowStartUs = timeline.currentTimeUs - (timeline.windowSizeUs / 2);
    } else if (timeline.mode === 'REWIND' && timeline.isPlaying) {
        // Server-driven cursor: timeline.currentTimeUs is updated by appendEvents from chunk metadata
        // Client only adjusts window if cursor goes out of bounds (no simulation needed)
        
        // Adjust window if cursor goes out of bounds
        if (timeline.currentTimeUs > timeline.windowEndUs) {
            const shift = timeline.currentTimeUs - timeline.windowEndUs;
            timeline.windowStartUs += shift;
            timeline.windowEndUs += shift;
        } else if (timeline.currentTimeUs < timeline.windowStartUs) {
            const shift = timeline.windowStartUs - timeline.currentTimeUs;
            timeline.windowStartUs -= shift;
            timeline.windowEndUs -= shift;
        }
    }
    
    // Update time display
    const date = new Date(timeline.currentTimeUs / 1000);
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    const ms = String(date.getMilliseconds()).padStart(3, '0');
    
    document.getElementById('timeDisplay').textContent = `${hours}:${minutes}:${seconds}.${ms}`;
    
    // Update date display
    const dateStr = date.toLocaleDateString('en-US', { 
        month: 'short', 
        day: 'numeric', 
        year: 'numeric' 
    });
    document.getElementById('timelineDate').textContent = dateStr;
    
    // Update mode indicator
    const modeEl = document.getElementById('timeMode');
    if (timeline.mode === 'LIVE') {
        modeEl.textContent = 'LIVE';
        modeEl.classList.remove('paused');
    } else {
        modeEl.textContent = timeline.isPlaying ? 'REWIND' : 'PAUSED';
        modeEl.classList.toggle('paused', !timeline.isPlaying);
    }
    
    // Disable command buttons in REWIND mode (Phase 5: replay safety)
    const commandBtn = document.getElementById('submitCommandBtn');
    if (commandBtn) {
        commandBtn.disabled = timeline.mode !== 'LIVE';
    }
    
    // Update slider position
    if (timeline.mode === 'LIVE') {
        document.getElementById('timeSlider').value = '100';
        document.getElementById('timelineCursor').style.left = '100%';
    } else {
        const windowRange = timeline.windowEndUs - timeline.windowStartUs;
        const position = ((timeline.currentTimeUs - timeline.windowStartUs) / windowRange) * 100;
        document.getElementById('timeSlider').value = position;
        document.getElementById('timelineCursor').style.left = position + '%';
    }
}

function updateUI() {
    // Update play/pause button
    const playBtn = document.getElementById('playPauseBtn');
    playBtn.textContent = timeline.isPlaying ? '⏸' : '▶';
    
    // Update live button active state
    const liveBtn = document.getElementById('liveBtn');
    liveBtn.classList.toggle('active', timeline.mode === 'LIVE');
    
    updateDisplay();
}

function cancelAndStartStream() {
    cancelStream();
    // Delay to ensure cancel is processed and websocket is still open
    setTimeout(() => {
        // Double-check websocket is still connected before starting
        if (window.wsState?.connected && !window.wsState?.reconnecting) {
            startStream();
        } else {
            console.warn('[Timeline] Skipping startStream - websocket not ready');
        }
    }, 500);
}

function startStream() {
    if (!window.wsState?.connected || window.wsState?.reconnecting) {
        console.warn('[Timeline] Cannot start stream - not connected or reconnecting');
        return;
    }
    
    // Check if websocket is open before sending
    if (window.wsState.ws?.readyState !== WebSocket.OPEN) {
        console.warn('[Timeline] Cannot start stream - websocket not open');
        return;
    }
    
    timeline.playbackRequestId = generateUUID();
    
    // Calculate start/stop times based on mode
    let startTime, stopTime;
    
    if (timeline.mode === 'LIVE') {
        // LIVE mode: no startTime = server streams from now
        startTime = null;
        stopTime = null;
    } else {
        // REWIND mode: start from current cursor position
        startTime = timeline.currentTimeUs;
        stopTime = null;
    }
    
    const request = {
        type: 'startStream',
        clientConnId: window.wsState.connId,
        playbackRequestId: timeline.playbackRequestId,
        startTime: startTime,
        stopTime: stopTime,
        rate: timeline.rate,
        timelineMode: timeline.mode === 'LIVE' ? 'live' : 'replay',  // Map to server enum
        timebase: timeline.timebase,  // Use role-based default from config
        filters: { lanes: ['metadata', 'ui', 'command'] }  // Server expects filters.lanes
    };
    
    const startDate = new Date(startTime / 1000);
    console.log('[Timeline] Starting stream at', startDate.toISOString(), 'rate=', timeline.rate);
    window.sendWebSocketMessage(request);
}

function setPlaybackRate(rate) {
    if (!window.wsState?.connected || !timeline.playbackRequestId || window.wsState?.reconnecting) {
        console.warn('[Timeline] Cannot set rate - not connected or no active stream');
        return;
    }
    
    // Check if websocket is open before sending
    if (window.wsState.ws?.readyState !== WebSocket.OPEN) {
        console.warn('[Timeline] Cannot set rate - websocket not open');
        return;
    }
    
    timeline.rate = rate;
    
    const request = {
        type: 'setPlaybackRate',
        rate: rate
    };
    
    console.log('[Timeline] Setting playback rate:', rate);
    window.sendWebSocketMessage(request);
}

function cancelStream() {
    if (!window.wsState?.connected || !timeline.playbackRequestId || window.wsState?.reconnecting) {
        return;
    }
    
    // Check if websocket is open before sending
    if (window.wsState.ws?.readyState !== WebSocket.OPEN) {
        console.warn('[Timeline] Cannot cancel - websocket not open');
        timeline.playbackRequestId = null;
        return;
    }
    
    const request = {
        type: 'cancelStream',
        clientConnId: window.wsState.connId
    };
    
    window.sendWebSocketMessage(request);
    timeline.playbackRequestId = null;
}

function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Export for use by other modules
window.initTimeline = initTimeline;
window.updateTimelineUI = updateUI;
window.timeline = timeline;

// Export for other modules
window.timeline = timeline;
window.startStream = startStream;
window.cancelStream = cancelStream;
