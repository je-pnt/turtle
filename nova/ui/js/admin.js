/**
 * NOVA Admin Panel Handler
 * 
 * User management for administrators.
 * Cookie-based auth (Phase 9) - no Authorization headers needed.
 */

(function() {
    'use strict';
    
    const AUTH_USER_KEY = 'novaAuthUser';
    
    let currentUser = null;
    let availableScopes = [];  // Fetched from server
    
    document.addEventListener('DOMContentLoaded', async () => {
        // Check auth via /auth/me endpoint
        try {
            const meResponse = await fetch('/auth/me', { credentials: 'same-origin' });
            if (!meResponse.ok) {
                window.location.href = '/login?redirect=/admin';
                return;
            }
            currentUser = await meResponse.json();
        } catch (e) {
            window.location.href = '/login?redirect=/admin';
            return;
        }
        
        // Check admin role
        if (currentUser.role !== 'admin') {
            alert('Admin access required');
            window.location.href = '/';
            return;
        }
        
        // Display admin user
        document.getElementById('adminUser').textContent = `Logged in as: ${currentUser.username}`;
        
        // Setup refresh button
        document.getElementById('refreshBtn').addEventListener('click', loadUsers);
        
        // Fetch available scopes then load users
        await fetchAvailableScopes();
        loadUsers();
    });
    
    async function fetchAvailableScopes() {
        try {
            const response = await fetch('/api/admin/scopes', { credentials: 'same-origin' });
            if (response.ok) {
                const data = await response.json();
                availableScopes = data.scopes || [];
            }
        } catch (e) {
            console.warn('[Admin] Failed to fetch scopes:', e);
        }
    }
    
    async function loadUsers() {
        const tbody = document.getElementById('usersTableBody');
        const userCount = document.getElementById('userCount');
        
        tbody.innerHTML = '<tr><td colspan="6">Loading...</td></tr>';
        
        try {
            const response = await fetch('/api/admin/users', {
                credentials: 'same-origin'
            });
            
            if (response.status === 401 || response.status === 403) {
                sessionStorage.removeItem(AUTH_USER_KEY);
                window.location.href = '/login?redirect=/admin';
                return;
            }
            
            const data = await response.json();
            
            if (!response.ok) {
                tbody.innerHTML = `<tr><td colspan="6">Error: ${data.error}</td></tr>`;
                return;
            }
            
            const users = data.users || [];
            userCount.textContent = `${users.length} user${users.length !== 1 ? 's' : ''}`;
            
            if (users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6">No users found</td></tr>';
                return;
            }
            
            tbody.innerHTML = users.map(user => renderUserRow(user)).join('');
            
            // Attach event handlers
            attachActionHandlers();
            
        } catch (err) {
            console.error('[Admin] Load users error:', err);
            tbody.innerHTML = '<tr><td colspan="6">Connection error</td></tr>';
        }
    }
    
    function renderUserRow(user) {
        const statusClass = {
            'pending': 'status-pending',
            'active': 'status-active',
            'disabled': 'status-disabled'
        }[user.status] || '';
        
        const roleClass = user.role === 'admin' ? 'role-admin' : 'role-operator';
        
        const createdDate = user.createdAt ? new Date(user.createdAt).toLocaleDateString() : '-';
        
        const isSelf = user.userId === currentUser.userId;
        
        // Scope dropdown (multi-select via <select>)
        const userScopes = user.allowedScopes || [];
        const hasAll = userScopes.includes('ALL');
        let scopeCell;
        if (isSelf) {
            scopeCell = `<span>${hasAll ? 'ALL' : userScopes.join(', ') || '(none)'}</span>`;
        } else {
            let scopeOptions = `<option value="ALL" ${hasAll ? 'selected' : ''}>ALL</option>`;
            for (const s of availableScopes) {
                if (s === 'ALL') continue;
                const sel = userScopes.includes(s) ? 'selected' : '';
                scopeOptions += `<option value="${escapeHtml(s)}" ${sel}>${escapeHtml(s)}</option>`;
            }
            scopeCell = `<select class="scope-select" data-userid="${user.userId}" multiple size="1" title="Select scopes (Ctrl+click for multiple)">${scopeOptions}</select>`;
        }
        
        // Role selector (can't change own role)
        const roleSelector = !isSelf ? `
            <select class="role-selector" data-userid="${user.userId}">
                <option value="operator" ${user.role === 'operator' ? 'selected' : ''}>operator</option>
                <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>admin</option>
            </select>
        ` : `<span class="badge ${roleClass}">${user.role}</span>`;
        
        // Compact action buttons — icons with title tooltips
        let actions = '';
        if (user.status === 'pending') {
            actions += `<button class="icon-btn approve-btn" data-userid="${user.userId}" data-action="approve" title="Approve">✓</button>`;
        } else if (user.status === 'active' && !isSelf) {
            actions += `<button class="icon-btn disable-btn" data-userid="${user.userId}" data-action="disable" title="Disable">⊘</button>`;
        } else if (user.status === 'disabled') {
            actions += `<button class="icon-btn enable-btn" data-userid="${user.userId}" data-action="enable" title="Enable">↻</button>`;
        }

        if (!isSelf) {
            actions += `<button class="icon-btn pwd-btn" data-userid="${user.userId}" data-username="${escapeHtml(user.username)}" title="Reset password">🔑</button>`;
            actions += `<button class="icon-btn del-btn" data-userid="${user.userId}" data-username="${escapeHtml(user.username)}" title="Delete user">✕</button>`;
        }
        
        return `
            <tr>
                <td>${escapeHtml(user.username)}</td>
                <td>${roleSelector}</td>
                <td><span class="badge ${statusClass}">${user.status}</span></td>
                <td>${scopeCell}</td>
                <td>${createdDate}</td>
                <td class="actions-cell">${actions}</td>
            </tr>
        `;
    }
    
    function attachActionHandlers() {
        // Action buttons (approve, disable, enable)
        document.querySelectorAll('.icon-btn[data-action]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const userId = btn.dataset.userid;
                const action = btn.dataset.action;
                await performAction(userId, action);
            });
        });
        
        // Reset password buttons
        document.querySelectorAll('.pwd-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const userId = btn.dataset.userid;
                const username = btn.dataset.username;
                await resetPassword(userId, username);
            });
        });
        
        // Delete buttons
        document.querySelectorAll('.del-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const userId = btn.dataset.userid;
                const username = btn.dataset.username;
                await deleteUser(userId, username);
            });
        });
        
        // Role selectors
        document.querySelectorAll('.role-selector').forEach(select => {
            select.addEventListener('change', async () => {
                const userId = select.dataset.userid;
                const role = select.value;
                await setRole(userId, role);
            });
        });
        
        // Scope selects (save on change)
        document.querySelectorAll('.scope-select').forEach(select => {
            select.addEventListener('change', async () => {
                const userId = select.dataset.userid;
                const selected = Array.from(select.selectedOptions).map(o => o.value);
                // If ALL is selected, send just ['ALL']
                const scopes = selected.includes('ALL') ? ['ALL'] : selected;
                await setScopes(userId, scopes);
            });
        });
    }
    
    async function performAction(userId, action) {
        try {
            const response = await fetch(`/api/admin/users/${userId}/${action}`, {
                method: 'POST',
                credentials: 'same-origin'
            });
            
            if (response.status === 401 || response.status === 403) {
                sessionStorage.removeItem(AUTH_USER_KEY);
                window.location.href = '/login?redirect=/admin';
                return;
            }
            
            if (!response.ok) {
                const data = await response.json();
                alert(`Error: ${data.error}`);
                return;
            }
            
            // Reload users
            loadUsers();
            
        } catch (err) {
            console.error(`[Admin] ${action} error:`, err);
            alert('Connection error');
        }
    }
    
    async function setRole(userId, role) {
        try {
            const response = await fetch(`/api/admin/users/${userId}/role`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'same-origin',
                body: JSON.stringify({ role })
            });
            
            if (response.status === 401 || response.status === 403) {
                sessionStorage.removeItem(AUTH_USER_KEY);
                window.location.href = '/login?redirect=/admin';
                return;
            }
            
            if (!response.ok) {
                const data = await response.json();
                alert(`Error: ${data.error}`);
                loadUsers(); // Reset selector
                return;
            }
            
            console.log(`[Admin] Role updated: ${userId} → ${role}`);
            
        } catch (err) {
            console.error('[Admin] Set role error:', err);
            alert('Connection error');
            loadUsers();
        }
    }
    
    async function resetPassword(userId, username) {
        const newPassword = prompt(`Enter new password for "${username}" (min 6 characters):`);
        if (!newPassword) return;
        if (newPassword.length < 6) {
            alert('Password must be at least 6 characters');
            return;
        }
        
        try {
            const response = await fetch(`/api/admin/users/${userId}/reset-password`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ password: newPassword })
            });
            
            if (response.status === 401 || response.status === 403) {
                sessionStorage.removeItem(AUTH_USER_KEY);
                window.location.href = '/login?redirect=/admin';
                return;
            }
            
            const data = await response.json();
            
            if (!response.ok) {
                alert(`Reset failed: ${data.error || 'Unknown error'}`);
                return;
            }
            
            alert(`Password reset for "${username}". They can now log in with the new password.\n\nNote: Any existing sessions for this user have been invalidated.`);
        } catch (err) {
            console.error('[Admin] Reset password error:', err);
            alert('Connection error: ' + err.message);
        }
    }
    
    async function deleteUser(userId, username) {
        if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return;
        
        try {
            const response = await fetch(`/api/admin/users/${userId}`, {
                method: 'DELETE',
                credentials: 'same-origin'
            });
            
            if (response.status === 401 || response.status === 403) {
                sessionStorage.removeItem(AUTH_USER_KEY);
                window.location.href = '/login?redirect=/admin';
                return;
            }
            
            if (!response.ok) {
                const data = await response.json();
                alert(`Error: ${data.error}`);
                return;
            }
            
            loadUsers();
        } catch (err) {
            console.error('[Admin] Delete user error:', err);
            alert('Connection error');
        }
    }
    
    async function setScopes(userId, scopes) {
        try {
            const response = await fetch(`/api/admin/users/${userId}/scopes`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ scopes: scopes })
            });
            
            if (response.status === 401 || response.status === 403) {
                sessionStorage.removeItem(AUTH_USER_KEY);
                window.location.href = '/login?redirect=/admin';
                return;
            }
            
            if (!response.ok) {
                const data = await response.json();
                alert(`Error: ${data.error}`);
                loadUsers();
                return;
            }
            
            console.log(`[Admin] Scopes updated for ${userId}`);
        } catch (err) {
            console.error('[Admin] Set scopes error:', err);
            alert('Connection error');
            loadUsers();
        }
    }
    
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
})();
