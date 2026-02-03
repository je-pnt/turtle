/**
 * NOVA Registration Page Handler
 * 
 * Handles registration form submission.
 * New users are created with 'pending' status awaiting admin approval.
 */

(function() {
    'use strict';
    
    document.addEventListener('DOMContentLoaded', () => {
        const form = document.getElementById('registerForm');
        const usernameInput = document.getElementById('username');
        const passwordInput = document.getElementById('password');
        const confirmPasswordInput = document.getElementById('confirmPassword');
        const errorMessage = document.getElementById('errorMessage');
        const successMessage = document.getElementById('successMessage');
        
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const username = usernameInput.value.trim();
            const password = passwordInput.value;
            const confirmPassword = confirmPasswordInput.value;
            
            showError('');
            showSuccess('');
            
            // Validation
            if (!username || !password || !confirmPassword) {
                showError('All fields are required');
                return;
            }
            
            if (username.length < 3) {
                showError('Username must be at least 3 characters');
                return;
            }
            
            if (password.length < 6) {
                showError('Password must be at least 6 characters');
                return;
            }
            
            if (password !== confirmPassword) {
                showError('Passwords do not match');
                return;
            }
            
            try {
                const response = await fetch('/auth/register', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    showError(data.error || 'Registration failed');
                    return;
                }
                
                console.log('[Register] Success:', data);
                
                // Redirect to approval-pending page
                window.location.href = '/approval-pending';
                
            } catch (err) {
                console.error('[Register] Error:', err);
                showError('Connection error. Please try again.');
            }
        });
    });
    
    function showError(message) {
        const errorMessage = document.getElementById('errorMessage');
        const successMessage = document.getElementById('successMessage');
        errorMessage.textContent = message;
        if (message) {
            successMessage.textContent = '';
        }
    }
    
    function showSuccess(message) {
        const errorMessage = document.getElementById('errorMessage');
        const successMessage = document.getElementById('successMessage');
        successMessage.textContent = message;
        if (message) {
            errorMessage.textContent = '';
        }
    }
})();
