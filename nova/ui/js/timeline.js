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
    isDragging: false,       // True when user is dragging slider (prevents reset)
    rate: 1.0,               // Playback rate (+ forward, - backward)
    timebase: 'canonical',   // 'source' or 'canonical' (loaded from config)
    currentTimeUs: 0,        // Current cursor position (microseconds)
    lastDataTimeUs: null,    // Last event time we actually received (for REWIND)
    playbackRequestId: null, // Active stream fence token
    
    // Time window: LHS = session start, RHS = now (real-time)
    // Linear mapping: slider 0% = windowStartUs, 100% = now
    windowStartUs: 0,        // Session start (extends if cursor goes earlier)
    windowEndUs: 0,
    
    // Clamp (Phase 11 - UI-only restriction to a run's time window)
    clamp: null  // {startTimeSec, stopTimeSec, timebase} or null
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
    
    // Initialize timeline: RHS = now, LHS = now (grows as time passes or user rewinds)
    const nowUs = Date.now() * 1000;
    timeline.currentTimeUs = nowUs;
    timeline.windowEndUs = nowUs;
    timeline.windowStartUs = nowUs;
    
    // Attach event listeners
    document.getElementById('playPauseBtn').addEventListener('click', handlePlayPause);
    document.getElementById('liveBtn').addEventListener('click', handleJumpToLive);
    document.getElementById('speedInput').addEventListener('change', handleSpeedChange);
    document.getElementById('timebaseSelect').addEventListener('change', handleTimebaseChange);
    document.getElementById('datetimeInput').addEventListener('change', handleDatetimeJump);
    
    // Use 'change' instead of 'input' for slider to avoid rapid firing
    const slider = document.getElementById('timeSlider');
    slider.addEventListener('input', (event) => {
        // Mark as dragging to prevent updateDisplay from resetting position
        timeline.isDragging = true;
        // Update cursor visual immediately
        const position = parseFloat(event.target.value);
        const windowRange = timeline.windowEndUs - timeline.windowStartUs;
        const timeUs = timeline.windowStartUs + (position / 100) * windowRange;
        timeline.currentTimeUs = timeUs;
        // Update time display but NOT slider (we're dragging it)
        const date = new Date(timeline.currentTimeUs / 1000);
        const hours = String(date.getUTCHours()).padStart(2, '0');
        const minutes = String(date.getUTCMinutes()).padStart(2, '0');
        const seconds = String(date.getUTCSeconds()).padStart(2, '0');
        const ms = String(date.getMilliseconds()).padStart(3, '0');
        document.getElementById('timeDisplay').textContent = `${hours}:${minutes}:${seconds}.${ms}`;
        document.getElementById('timelineCursor').style.left = position + '%';
    });
    slider.addEventListener('change', (event) => {
        // Drag complete
        timeline.isDragging = false;
        // On release, actually jump to the time
        handleSliderDrag(event);
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
            // Resume: restart stream at current position
            startStream();
        } else {
            // Pause: cancel stream (architecture requires cancel+restart for any change)
            cancelStream();
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
    
    // Exit clamp (Phase 11)
    timeline.clamp = null;
    if (window.clearClamp) window.clearClamp();
    // windowStartUs stays — represents earliest known time
    // windowEndUs will be updated to now by updateDisplay
    
    // Update UI inputs
    document.getElementById('speedInput').value = '1.0';
    document.getElementById('datetimeInput').value = '';
    document.getElementById('timeSlider').value = '100';
    
    // Start new stream directly (server handles implicit cancel)
    startStream();
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
        }
        // Window stays: startUs = session start, endUs = now
    }
    
    if (timeline.isPlaying) {
        // Start new stream (server handles implicit cancel)
        startStream();
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
        startStream();
    }
    updateUI();
}

function handleDatetimeJump(event) {
    const dateStr = event.target.value;
    if (!dateStr) return;
    
    // datetime-local gives us local time, but timeline uses UTC
    // Parse as UTC by appending 'Z' or manually constructing UTC timestamp
    const targetDate = new Date(dateStr + 'Z');  // Interpret as UTC
    const targetTimeUs = targetDate.getTime() * 1000;
    
    seekToTime(targetTimeUs);
}

function handleSliderDrag(event) {
    const position = parseFloat(event.target.value);
    
    if (position >= 99) {
        // Dragged to far right → jump to LIVE
        handleJumpToLive();
    } else {
        // Calculate time from slider position
        const timeUs = timeline.windowStartUs + (position / 100) * (timeline.windowEndUs - timeline.windowStartUs);
        seekToTime(timeUs);
    }
}

/**
 * Seek to a specific time (microseconds).
 * Unified function for datetime input and slider drag.
 */
function seekToTime(targetTimeUs) {
    timeline.mode = 'REWIND';
    timeline.currentTimeUs = targetTimeUs;
    
    // Extend windowStartUs if seeking before current start
    if (targetTimeUs < timeline.windowStartUs) {
        timeline.windowStartUs = targetTimeUs;
    }
    
    if (timeline.isPlaying) {
        startStream();
    }
    updateUI();
}

function updateDisplay() {
    // RHS of timeline is always real-time now
    timeline.windowEndUs = Date.now() * 1000;
    
    if (timeline.mode === 'LIVE' && timeline.isPlaying) {
        // In LIVE mode, cursor = now (right edge)
        if (timeline.lastDataTimeUs) {
            timeline.currentTimeUs = timeline.lastDataTimeUs;
        } else {
            timeline.currentTimeUs = timeline.windowEndUs;
        }
    } else if (timeline.mode === 'REWIND' && timeline.isPlaying) {
        // Server-driven cursor: updated by appendEvents from chunk metadata
        // Extend windowStartUs if cursor rewinds past it
        if (timeline.currentTimeUs < timeline.windowStartUs) {
            timeline.windowStartUs = timeline.currentTimeUs;
        }
    }
    
    // Update time display (UTC)
    const date = new Date(timeline.currentTimeUs / 1000);
    const hours = String(date.getUTCHours()).padStart(2, '0');
    const minutes = String(date.getUTCMinutes()).padStart(2, '0');
    const seconds = String(date.getUTCSeconds()).padStart(2, '0');
    const ms = String(date.getMilliseconds()).padStart(3, '0');
    
    document.getElementById('timeDisplay').textContent = `${hours}:${minutes}:${seconds}.${ms}`;
    
    // Update date display (UTC)
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const dateStr = `${months[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()}`;
    document.getElementById('timelineDate').textContent = `${dateStr} UTC`;
    
    // Update datetime input to show current cursor time in UTC (YYYY-MM-DDTHH:MM:SS format)
    const year = date.getUTCFullYear();
    const month = String(date.getUTCMonth() + 1).padStart(2, '0');
    const day = String(date.getUTCDate()).padStart(2, '0');
    const datetimeValue = `${year}-${month}-${day}T${hours}:${minutes}:${seconds}`;
    document.getElementById('datetimeInput').value = datetimeValue;
    
    // Update mode indicator
    const modeEl = document.getElementById('timeMode');
    if (timeline.mode === 'LIVE') {
        modeEl.textContent = 'LIVE';
        modeEl.classList.remove('paused');
    } else {
        modeEl.textContent = timeline.isPlaying ? 'REWIND' : 'PAUSED';
        modeEl.classList.toggle('paused', !timeline.isPlaying);
    }
    
    // Update clamp indicator (Phase 11)
    const clampIndicator = document.getElementById('clampIndicator');
    if (clampIndicator) {
        if (timeline.clamp) {
            clampIndicator.textContent = '🔒';
            clampIndicator.title = `Clamped: ${formatClampTime(timeline.clamp.startTimeSec)} - ${formatClampTime(timeline.clamp.stopTimeSec)}`;
            clampIndicator.style.display = 'inline';
        } else {
            clampIndicator.style.display = 'none';
        }
    }
    
    // Disable command buttons in REWIND mode (Phase 5: replay safety)
    const commandBtn = document.getElementById('submitCommandBtn');
    if (commandBtn) {
        commandBtn.disabled = timeline.mode !== 'LIVE';
    }
    
    // Update slider position (skip if user is dragging)
    if (!timeline.isDragging) {
        if (timeline.mode === 'LIVE') {
            document.getElementById('timeSlider').value = '100';
            document.getElementById('timelineCursor').style.left = '100%';
        } else {
            const windowRange = timeline.windowEndUs - timeline.windowStartUs;
            const position = windowRange > 0
                ? ((timeline.currentTimeUs - timeline.windowStartUs) / windowRange) * 100
                : 50;
            const clamped = Math.max(0, Math.min(100, position));
            document.getElementById('timeSlider').value = clamped;
            document.getElementById('timelineCursor').style.left = clamped + '%';
        }
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
    // Server handles implicit cancel when new stream starts
    startStream();
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
    
    // playbackRequestId is generated by server and returned in streamStarted
    // Keep old ID until streamStarted arrives — prevents fencing race
    // (old stream chunks stop naturally via server implicit cancel)
    
    // Calculate start/stop times based on mode and clamp
    let startTime, stopTime;
    
    if (timeline.mode === 'LIVE') {
        // LIVE mode: no bounds, server streams from now
        startTime = null;
        stopTime = null;
    } else if (timeline.clamp) {
        // REWIND mode with clamp: use full clamp bounds
        // Server StreamCursor decides where to start based on rate direction:
        //   rate > 0: starts at startTime, moves toward stopTime
        //   rate < 0: starts at stopTime, moves toward startTime
        startTime = timeline.clamp.startTimeSec * 1_000_000;
        stopTime = timeline.clamp.stopTimeSec * 1_000_000;
    } else {
        // REWIND mode without clamp: start from cursor, no stop
        startTime = timeline.currentTimeUs;
        stopTime = null;
    }
    
    const request = {
        type: 'startStream',
        clientConnId: window.wsState.connId,
        startTime: startTime,
        stopTime: stopTime,
        rate: timeline.rate,
        timelineMode: timeline.mode === 'LIVE' ? 'live' : 'replay',  // Map to server enum
        timebase: timeline.timebase,  // Use role-based default from config
        filters: { lanes: ['metadata', 'ui', 'command'] }  // Server expects filters.lanes
    };
    
    const startDate = startTime ? new Date(startTime / 1000) : new Date();
    const stopDate = stopTime ? new Date(stopTime / 1000) : null;
    console.log('[Timeline] Starting stream:', startDate.toISOString(), '→', stopDate?.toISOString() || 'LIVE', 'rate=', timeline.rate);
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

/**
 * Format seconds timestamp for clamp display
 */
function formatClampTime(sec) {
    if (!sec) return '—';
    const date = new Date(sec * 1000);
    const hh = String(date.getUTCHours()).padStart(2, '0');
    const mm = String(date.getUTCMinutes()).padStart(2, '0');
    const ss = String(date.getUTCSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
}

// Export for use by other modules
window.initTimeline = initTimeline;
window.updateTimelineUI = updateUI;
window.timeline = timeline;
window.startStream = startStream;
window.cancelStream = cancelStream;
window.seekToTime = seekToTime;
