/**
 * NOVA Chat Module
 * 
 * Collapsible chat panel above timeline.
 * Phase 9: Chat messages are metadata truth events - replayable!
 * 
 * Architecture:
 * - Live mode: receive chat via WebSocket broadcast, display immediately
 * - Replay mode: chat events come via stream (MetadataEvent messageType=ChatMessage)
 *   - Filter messages by timeline cursor (only show messages <= currentTime)
 *   - Highlight "current" message (closest to cursor) during playback
 *   - Autoscroll with follow toggle
 * 
 * Following novaCore pattern:
 * - Messages stored per channel
 * - Timeline integration (realtime/replay mode)
 * - Collapse toggle with unread badge
 * - Resizable via top handle (expands upward only)
 * - Positioned between sidebars dynamically
 */

const NovaChat = (function() {
    'use strict';
    
    // State
    let _currentChannel = 'ops';
    let _messages = {};  // channel → messages array
    let _collapsed = true;
    let _unreadCount = 0;
    let _timeMode = 'realtime';  // 'realtime' or 'replay'
    let _replayCursor = null;
    let _followMode = true;  // Auto-scroll during replay
    let _currentMessageId = null;  // Currently highlighted message
    let _panelHeight = 200;  // Default height in px
    let _minHeight = 120;
    let _maxHeight = 500;
    let _isResizing = false;
    
    // Elements
    let _panel, _toggle, _badge, _channelSelect;
    let _messagesContainer, _input, _sendBtn, _resizeHandle, _followToggle;
    
    /**
     * Initialize chat module
     */
    function init() {
        _panel = document.getElementById('chatPanel');
        _toggle = document.getElementById('chatToggle');
        _badge = document.getElementById('chatBadge');
        _channelSelect = document.getElementById('chatChannel');
        _messagesContainer = document.getElementById('chatMessages');
        _input = document.getElementById('chatInput');
        _sendBtn = document.getElementById('chatSend');
        
        if (!_panel) {
            console.log('[Chat] Chat panel not found');
            return;
        }
        
        // Create resize handle if not present
        _resizeHandle = _panel.querySelector('.chat-resize-handle');
        if (!_resizeHandle) {
            _resizeHandle = document.createElement('div');
            _resizeHandle.className = 'chat-resize-handle';
            _panel.insertBefore(_resizeHandle, _panel.firstChild);
        }
        
        // Create follow toggle for replay mode
        _followToggle = document.getElementById('chatFollowToggle');
        if (!_followToggle) {
            const header = _panel.querySelector('.chat-header');
            if (header) {
                _followToggle = document.createElement('button');
                _followToggle.id = 'chatFollowToggle';
                _followToggle.className = 'chat-follow-toggle active';
                _followToggle.title = 'Auto-scroll to current message';
                _followToggle.innerHTML = '⬇';
                _followToggle.style.display = 'none';  // Hidden in live mode
                header.appendChild(_followToggle);
                
                _followToggle.addEventListener('click', toggleFollow);
            }
        }
        
        // Event listeners
        _toggle.addEventListener('click', togglePanel);
        _channelSelect.addEventListener('change', changeChannel);
        _sendBtn.addEventListener('click', sendMessage);
        _input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
        
        // Resize handle events
        _resizeHandle.addEventListener('mousedown', startResize);
        
        // Listen for timeline mode changes
        window.addEventListener('nova:timeMode', (e) => {
            const wasReplay = _timeMode === 'replay';
            _timeMode = e.detail.mode;
            
            if (_timeMode === 'replay') {
                // Convert microseconds (timeline) to milliseconds (chat timestamps)
                _replayCursor = e.detail.cursor / 1000;
                // Show follow toggle in replay mode
                if (_followToggle) _followToggle.style.display = '';
                // Disable input in replay
                if (_input) _input.disabled = true;
                if (_sendBtn) _sendBtn.disabled = true;
            } else {
                // Live mode - hide follow toggle, enable input
                if (_followToggle) _followToggle.style.display = 'none';
                if (_input) _input.disabled = false;
                if (_sendBtn) _sendBtn.disabled = false;
                _currentMessageId = null;
            }
            renderMessages();
        });
        
        // Listen for time updates (for replay filtering and highlighting)
        window.addEventListener('nova:timeUpdate', (e) => {
            if (_timeMode === 'replay') {
                // Convert microseconds (timeline) to milliseconds (chat timestamps)
                _replayCursor = e.detail.time / 1000;
                renderMessages();
            }
        });
        
        // Listen for metadata events during stream (replay chat messages)
        window.addEventListener('nova:metadataEvent', (e) => {
            const event = e.detail;
            if (event.messageType === 'ChatMessage') {
                handleChatEvent(event);
            }
        });
        
        // Listen for sidebar resize events
        window.addEventListener('nova:sidebarResize', updatePosition);
        window.addEventListener('resize', updatePosition);
        
        // Initial position update
        updatePosition();
        
        // Restore saved height
        const savedHeight = localStorage.getItem('nova-chat-height');
        if (savedHeight) {
            _panelHeight = Math.min(_maxHeight, Math.max(_minHeight, parseInt(savedHeight, 10)));
        }
        updatePanelHeight();
        
        // Setup scroll detection for auto-disabling follow mode (Phase 9)
        setupScrollDetection();
        
        console.log('[Chat] Initialized');
    }
    
    /**
     * Toggle follow mode (auto-scroll during replay)
     */
    function toggleFollow() {
        _followMode = !_followMode;
        if (_followToggle) {
            _followToggle.classList.toggle('active', _followMode);
        }
        if (_followMode) {
            renderMessages();  // Re-render to scroll to current
        }
    }
    
    /**
     * Detect user manual scroll to disable follow mode.
     * Once the user scrolls manually, follow stays off until they click ⬇.
     * Works in both live and replay modes.
     */
    function setupScrollDetection() {
        if (!_messagesContainer) return;
        
        // Track programmatic scrolls so we don't confuse them with user scrolls
        let _programmaticScroll = false;
        
        // Wrap our own scroll calls
        window._chatAutoScroll = function(el) {
            _programmaticScroll = true;
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // Reset flag after smooth scroll completes (generous delay)
            setTimeout(() => { _programmaticScroll = false; }, 600);
        };
        window._chatScrollToBottom = function() {
            _programmaticScroll = true;
            _messagesContainer.scrollTop = _messagesContainer.scrollHeight;
            setTimeout(() => { _programmaticScroll = false; }, 600);
        };
        
        _messagesContainer.addEventListener('scroll', () => {
            if (_programmaticScroll) return;
            // User scrolled manually — disable follow
            if (_followMode) {
                _followMode = false;
                if (_followToggle) _followToggle.classList.remove('active');
                console.log('[Chat] Follow mode disabled (user scrolled)');
            }
        });
    }
    
    /**
     * Update panel position based on sidebar widths
     */
    function updatePosition() {
        // Get sidebar elements (using actual IDs from index.html)
        const leftSidebar = document.getElementById('sidebar');
        const rightSidebar = document.getElementById('detailedPanel');
        
        let leftWidth = 0;  // Default to 0 if hidden
        let rightWidth = 0;  // Default to 0 if hidden
        
        if (leftSidebar && !leftSidebar.classList.contains('hidden')) {
            const rect = leftSidebar.getBoundingClientRect();
            leftWidth = rect.width;
        }
        
        if (rightSidebar && !rightSidebar.classList.contains('hidden')) {
            const rect = rightSidebar.getBoundingClientRect();
            rightWidth = rect.width;
        }
        
        // Set CSS variables
        document.documentElement.style.setProperty('--chat-left', `${leftWidth}px`);
        document.documentElement.style.setProperty('--chat-right', `${rightWidth}px`);
    }
    
    /**
     * Start resize operation
     */
    function startResize(e) {
        if (_collapsed) return;
        
        e.preventDefault();
        _isResizing = true;
        _resizeHandle.classList.add('dragging');
        
        const startY = e.clientY;
        const startHeight = _panelHeight;
        
        function onMouseMove(e) {
            // Calculate new height (dragging up increases height)
            const delta = startY - e.clientY;
            const newHeight = Math.min(_maxHeight, Math.max(_minHeight, startHeight + delta));
            _panelHeight = newHeight;
            updatePanelHeight();
        }
        
        function onMouseUp() {
            _isResizing = false;
            _resizeHandle.classList.remove('dragging');
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            
            // Save height preference
            localStorage.setItem('nova-chat-height', String(_panelHeight));
        }
        
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    }
    
    /**
     * Update panel height CSS variable
     */
    function updatePanelHeight() {
        document.documentElement.style.setProperty('--chat-height', `${_panelHeight}px`);
    }
    
    /**
     * Toggle chat panel collapsed state
     */
    function togglePanel() {
        _collapsed = !_collapsed;
        _panel.classList.toggle('collapsed', _collapsed);
        
        if (!_collapsed) {
            // Clear unread badge when opening
            _unreadCount = 0;
            updateBadge();
            _input.focus();
        }
    }
    
    /**
     * Change active channel
     */
    function changeChannel() {
        _currentChannel = _channelSelect.value;
        renderMessages();
    }
    
    /**
     * Send a chat message (live mode only)
     */
    function sendMessage() {
        if (_timeMode === 'replay') {
            console.log('[Chat] Cannot send messages in replay mode');
            return;
        }
        
        const text = _input.value.trim();
        if (!text) return;
        
        const user = window.NovaAuth ? window.NovaAuth.getUser() : { username: 'anonymous' };
        
        // Send via WebSocket
        if (window.sendWsMessage) {
            window.sendWsMessage({
                type: 'chat',
                channel: _currentChannel,
                text: text,
                username: user.username
            });
        }
        
        _input.value = '';
    }
    
    /**
     * Handle incoming chat message (from WebSocket broadcast - live mode)
     */
    function handleMessage(msg) {
        const channel = msg.channel || 'ops';
        
        if (!_messages[channel]) {
            _messages[channel] = [];
        }
        
        // Check for duplicate (by messageId if present)
        if (msg.messageId) {
            const exists = _messages[channel].some(m => m.messageId === msg.messageId);
            if (exists) return;
        }
        
        _messages[channel].push({
            messageId: msg.messageId || `${msg.timestamp}-${msg.username}`,
            username: msg.username,
            text: msg.text,
            timestamp: msg.timestamp
        });
        
        // Sort by timestamp
        _messages[channel].sort((a, b) => a.timestamp - b.timestamp);
        
        // Update unread if panel collapsed and it's current channel
        if (_collapsed && channel === _currentChannel) {
            _unreadCount++;
            updateBadge();
        }
        
        // Re-render if viewing this channel
        if (channel === _currentChannel) {
            renderMessages();
        }
    }
    
    /**
     * Handle chat event from metadata stream (replay mode)
     * 
     * During replay, chat messages arrive as MetadataEvent with messageType='ChatMessage'.
     * This is the replay-friendly path - messages are stored in DB and replayed with timeline.
     */
    function handleChatEvent(event) {
        const payload = event.payload || {};
        const channel = payload.channel || event.uniqueId || 'ops';
        
        if (!_messages[channel]) {
            _messages[channel] = [];
        }
        
        // Use eventId as messageId for deduplication
        const messageId = event.eventId;
        
        // Check for duplicate
        const exists = _messages[channel].some(m => m.messageId === messageId);
        if (exists) return;
        
        // Parse timestamp from effectiveTime (ISO8601) or sourceTruthTime
        let timestamp;
        if (event.effectiveTime) {
            timestamp = new Date(event.effectiveTime).getTime();
        } else if (event.sourceTruthTime) {
            timestamp = new Date(event.sourceTruthTime).getTime();
        } else {
            timestamp = Date.now();
        }
        
        _messages[channel].push({
            messageId: messageId,
            username: payload.username || 'unknown',
            text: payload.text || '',
            timestamp: timestamp
        });
        
        // Sort by timestamp
        _messages[channel].sort((a, b) => a.timestamp - b.timestamp);
        
        // Re-render if viewing this channel
        if (channel === _currentChannel) {
            renderMessages();
        }
    }
    
    /**
     * Update unread badge
     */
    function updateBadge() {
        if (_unreadCount > 0) {
            _badge.classList.remove('hidden');
            _badge.textContent = _unreadCount > 9 ? '9+' : String(_unreadCount);
        } else {
            _badge.classList.add('hidden');
        }
    }
    
    /**
     * Render messages for current channel
     * 
     * All messages are always visible. In replay mode:
     * - Highlight the "current" message (closest to cursor without exceeding)
     * - Auto-scroll to current if _followMode is on
     * Live mode scrolls to bottom unless user scrolled away.
     */
    function renderMessages() {
        const channelMessages = _messages[_currentChannel] || [];
        
        // Always show all messages — never filter/delete
        let currentMsgIndex = -1;
        
        if (_timeMode === 'replay' && _replayCursor) {
            // Binary-search-style: find last message with timestamp <= cursor
            for (let i = channelMessages.length - 1; i >= 0; i--) {
                if (channelMessages[i].timestamp <= _replayCursor) {
                    currentMsgIndex = i;
                    break;
                }
            }
            _currentMessageId = currentMsgIndex >= 0 ? channelMessages[currentMsgIndex].messageId : null;
        } else {
            _currentMessageId = null;
        }
        
        _messagesContainer.innerHTML = channelMessages.map((msg, index) => {
            const time = new Date(msg.timestamp).toLocaleTimeString();
            const isCurrent = (_timeMode === 'replay' && index === currentMsgIndex);
            const currentClass = isCurrent ? ' current' : '';
            return `
                <div class="chat-message${currentClass}" data-msgid="${msg.messageId}">
                    <span class="chat-username">${escapeHtml(msg.username)}</span>
                    <span class="chat-time">${time}</span>
                    <div class="chat-text">${escapeHtml(msg.text)}</div>
                </div>
            `;
        }).join('');
        
        // Scroll behavior
        if (_timeMode === 'replay' && _followMode && currentMsgIndex >= 0) {
            // In replay follow mode, scroll to current (closest) message
            const currentEl = _messagesContainer.querySelector('.chat-message.current');
            if (currentEl && window._chatAutoScroll) {
                window._chatAutoScroll(currentEl);
            }
        } else if (_timeMode !== 'replay' && _followMode) {
            // Live mode with follow: scroll to bottom
            if (window._chatScrollToBottom) window._chatScrollToBottom();
        }
        // If follow mode is off, don't auto-scroll at all
    }
    
    /**
     * Escape HTML for XSS prevention
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    /**
     * Clear all messages (e.g., on disconnect)
     */
    function clear() {
        _messages = {};
        _unreadCount = 0;
        updateBadge();
        renderMessages();
    }
    
    /**
     * Scroll to a specific message and highlight it
     * @param {string} messageId - The message ID to scroll to
     */
    function scrollToMessage(messageId) {
        // Ensure panel is expanded
        if (_collapsed) {
            togglePanel();
        }
        
        // Find which channel has this message
        for (const channel of Object.keys(_messages)) {
            const msgIndex = _messages[channel].findIndex(m => m.messageId === messageId);
            if (msgIndex >= 0) {
                // Switch to that channel if needed
                if (channel !== _currentChannel) {
                    _currentChannel = channel;
                    if (_channelSelect) {
                        _channelSelect.value = channel;
                    }
                }
                renderMessages();
                
                // Find and highlight the message element
                setTimeout(() => {
                    const msgElements = _messagesContainer.querySelectorAll('.chat-message');
                    if (msgElements[msgIndex]) {
                        msgElements[msgIndex].classList.add('highlighted');
                        if (window._chatAutoScroll) window._chatAutoScroll(msgElements[msgIndex]);
                        else msgElements[msgIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
                        
                        // Remove highlight after 3 seconds
                        setTimeout(() => {
                            msgElements[msgIndex].classList.remove('highlighted');
                        }, 3000);
                    }
                }, 100);
                return;
            }
        }
    }
    
    // Export public API
    return {
        init,
        handleMessage,
        clear,
        togglePanel,
        scrollToMessage,
        updatePosition
    };
})();

// Export for global access
window.NovaChat = NovaChat;
