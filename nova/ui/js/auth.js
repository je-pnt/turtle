/**
 * NOVA Auth Client
 * 
 * Cookie-based authentication for main application.
 * Following Phase 9 plan:
 * - JWT stored in httpOnly cookie (set by server)
 * - No localStorage token storage (cookies are automatic)
 * - No Authorization header needed (same-origin cookie is sent automatically)
 * - 401 responses trigger redirect to /login
 * - WebSocket auth via same cookie (automatic on upgrade request)
 */

const NovaAuth = (function() {
    'use strict';
    
    // Only store user info locally (token is in httpOnly cookie, not accessible to JS)
    const AUTH_USER_KEY = 'novaAuthUser';
    
    let _user = null;
    let _initialized = false;
    let _authChecked = false;
    
    /**
     * Initialize auth module
     * Called on page load to check existing auth via /auth/me endpoint
     */
    async function init() {
        if (_initialized) return _authChecked;
        _initialized = true;
        
        // Try to restore user from sessionStorage (for page refreshes)
        const userJson = sessionStorage.getItem(AUTH_USER_KEY);
        if (userJson) {
            try {
                _user = JSON.parse(userJson);
            } catch (e) {
                _user = null;
            }
        }
        
        // Verify with server that cookie is still valid
        try {
            const response = await fetch('/auth/me', { credentials: 'same-origin' });
            if (response.ok) {
                const data = await response.json();
                _user = {
                    userId: data.userId,
                    username: data.username,
                    role: data.role
                };
                sessionStorage.setItem(AUTH_USER_KEY, JSON.stringify(_user));
                _authChecked = true;
                console.log('[Auth] Authenticated:', _user.username);
            } else {
                // Cookie expired or invalid
                _user = null;
                sessionStorage.removeItem(AUTH_USER_KEY);
                _authChecked = false;
                console.log('[Auth] Not authenticated');
            }
        } catch (e) {
            console.error('[Auth] Error checking auth:', e);
            _authChecked = false;
        }
        
        // Install fetch interceptor for 401 handling
        interceptFetch();
        
        return _authChecked;
    }
    
    /**
     * Intercept fetch to handle 401 responses
     * No need to add Authorization header - httpOnly cookie is sent automatically
     */
    function interceptFetch() {
        const originalFetch = window.fetch;
        
        window.fetch = function(url, options = {}) {
            // Ensure credentials are included for same-origin requests
            if (!options.credentials) {
                options.credentials = 'same-origin';
            }
            
            return originalFetch.apply(this, [url, options])
                .then(response => {
                    // Handle 401 - redirect to login
                    if (response.status === 401 && _user) {
                        console.log('[Auth] 401 received, redirecting to login');
                        clearAuth();
                        const currentPath = window.location.pathname + window.location.search;
                        window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`;
                    }
                    return response;
                });
        };
    }
    
    /**
     * Check if user is authenticated
     */
    function isAuthenticated() {
        return !!_user;
    }
    
    /**
     * Get current user
     */
    function getUser() {
        return _user;
    }
    
    /**
     * Save user info after login (token is in httpOnly cookie)
     */
    function saveAuth(user) {
        _user = user;
        _authChecked = true;
        sessionStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
    }
    
    /**
     * Clear auth on logout
     */
    function clearAuth() {
        _user = null;
        _authChecked = false;
        sessionStorage.removeItem(AUTH_USER_KEY);
    }
    
    /**
     * Logout - calls server to clear cookie, then redirects
     */
    async function logout() {
        try {
            await fetch('/auth/logout', { 
                method: 'POST',
                credentials: 'same-origin'
            });
        } catch (e) {
            console.error('[Auth] Logout error:', e);
        }
        clearAuth();
        window.location.href = '/login';
    }
    
    /**
     * Check auth and redirect to login if not authenticated
     * Call this at start of protected pages
     */
    async function requireAuth() {
        const authed = await init();
        if (!authed) {
            const currentPath = window.location.pathname + window.location.search;
            window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`;
            return false;
        }
        return true;
    }
    
    // Export public API
    return {
        init,
        isAuthenticated,
        getUser,
        saveAuth,
        clearAuth,
        logout,
        requireAuth
    };
})();

// Export for global access
window.NovaAuth = NovaAuth;
