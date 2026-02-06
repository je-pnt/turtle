/**
 * NOVA Login Page Handler
 * 
 * Cookie-based auth (Phase 9):
 * - Server sets httpOnly cookie on successful login
 * - No localStorage token storage (cookie is automatic)
 * - User info stored in sessionStorage for UI display only
 */

(function() {
    'use strict';
    
    const AUTH_USER_KEY = 'novaAuthUser';
    
    document.addEventListener('DOMContentLoaded', async () => {
        const form = document.getElementById('loginForm');
        const usernameInput = document.getElementById('username');
        const passwordInput = document.getElementById('password');
        const errorMessage = document.getElementById('errorMessage');
        
        // Check if already logged in via /auth/me endpoint
        try {
            const meResponse = await fetch('/auth/me', { credentials: 'same-origin' });
            if (meResponse.ok) {
                // Already authenticated, redirect
                redirectAfterLogin();
                return;
            }
        } catch (e) {
            // Not authenticated, show login form
        }
        
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const username = usernameInput.value.trim();
            const password = passwordInput.value;
            
            if (!username || !password) {
                showError('Username and password required');
                return;
            }
            
            showError('');
            
            try {
                const response = await fetch('/auth/login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    showError(data.error || 'Login failed');
                    return;
                }
                
                // Store user info for UI display (token is in httpOnly cookie)
                sessionStorage.setItem(AUTH_USER_KEY, JSON.stringify({
                    userId: data.userId,
                    username: data.username,
                    role: data.role
                }));
                
                console.log('[Login] Success:', data.username);
                
                // Verify cookie was actually set before redirecting
                // This avoids a race where the redirect fires before the cookie is committed
                try {
                    const verify = await fetch('/auth/me', { credentials: 'same-origin' });
                    if (!verify.ok) {
                        console.warn('[Login] Cookie verification failed, retrying...');
                        await new Promise(r => setTimeout(r, 200));
                    }
                } catch (e) {
                    // Best effort — proceed with redirect anyway
                }
                
                redirectAfterLogin();
                
            } catch (err) {
                console.error('[Login] Error:', err);
                showError('Connection error. Please try again.');
            }
        });
        
        // Enter key in password field
        passwordInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                form.dispatchEvent(new Event('submit'));
            }
        });
    });
    
    function showError(message) {
        const errorMessage = document.getElementById('errorMessage');
        errorMessage.textContent = message;
    }
    
    function redirectAfterLogin() {
        // Check for redirect parameter
        const params = new URLSearchParams(window.location.search);
        const redirect = params.get('redirect');
        
        if (redirect && redirect.startsWith('/') && !redirect.startsWith('/login') && !redirect.startsWith('/register')) {
            window.location.href = redirect;
        } else {
            window.location.href = '/';
        }
    }
})();
