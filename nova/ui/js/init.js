/**
 * NOVA UI Initialization
 * Bootstrap all UI modules when DOM is ready
 * 
 * Phase 9: Cookie-based auth
 * - NovaAuth.init() verifies cookie with server
 * - requireAuth() redirects if not authenticated
 */

document.addEventListener('DOMContentLoaded', async () => {
    // Check server config first to see if auth is enabled
    let authEnabled = true;
    try {
        const response = await fetch('/config', { credentials: 'same-origin' });
        if (response.ok) {
            const config = await response.json();
            authEnabled = config.authEnabled !== false;
            if (!authEnabled) {
                console.log('[Init] Auth disabled, proceeding as anonymous');
                initApp();
                return;
            }
        }
    } catch (e) {
        console.warn('[Init] Failed to check server config:', e);
    }
    
    // Auth is enabled - verify authentication via cookie
    const authenticated = await window.NovaAuth.requireAuth();
    if (!authenticated) {
        // requireAuth() handles redirect to login
        return;
    }
    
    // User is authenticated - initialize app
    initApp();
});

function initApp() {
    // Setup user info display
    const user = window.NovaAuth.getUser();
    if (user) {
        const usernameEl = document.getElementById('username');
        const adminLink = document.getElementById('adminLink');
        const usernameBtn = document.getElementById('usernameBtn');
        const userDropdown = document.getElementById('userDropdown');
        const logoutBtn = document.getElementById('logoutBtn');
        
        if (usernameEl) {
            usernameEl.textContent = user.username;
        }
        
        // Show/hide admin link based on role
        if (adminLink) {
            adminLink.style.display = user.role === 'admin' ? 'block' : 'none';
        }
        
        // User dropdown toggle
        if (usernameBtn && userDropdown) {
            usernameBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                userDropdown.classList.toggle('show');
            });
            
            // Close dropdown when clicking outside
            document.addEventListener('click', () => {
                userDropdown.classList.remove('show');
            });
        }
        
        // Logout handler
        if (logoutBtn) {
            logoutBtn.addEventListener('click', () => {
                window.NovaAuth.logout();
            });
        }
        
        console.log('[Init] Authenticated as:', user.username, 'role:', user.role);
    }
    
    // Initialize modules
    initWebSocket();
    initTimeline();
    initDisplay();
    initStreams();
    initPanelResizing();
    
    // Initialize chat if available
    if (window.NovaChat) {
        window.NovaChat.init();
    }
    
    // Connect WebSocket
    connectWebSocket();
}
