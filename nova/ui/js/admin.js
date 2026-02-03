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
        
        // Load users
        loadUsers();
    });
    
    async function loadUsers() {
        const tbody = document.getElementById('usersTableBody');
        const userCount = document.getElementById('userCount');
        
        tbody.innerHTML = '<tr><td colspan="5">Loading...</td></tr>';
        
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
                tbody.innerHTML = `<tr><td colspan="5">Error: ${data.error}</td></tr>`;
                return;
            }
            
            const users = data.users || [];
            userCount.textContent = `${users.length} user${users.length !== 1 ? 's' : ''}`;
            
            if (users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5">No users found</td></tr>';
                return;
            }
            
            tbody.innerHTML = users.map(user => renderUserRow(user)).join('');
            
            // Attach event handlers
            attachActionHandlers();
            
        } catch (err) {
            console.error('[Admin] Load users error:', err);
            tbody.innerHTML = '<tr><td colspan="5">Connection error</td></tr>';
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
        
        // Build action buttons based on status
        let actions = '';
        
        if (user.status === 'pending') {
            actions = `
                <button class="action-btn approve-btn" data-userid="${user.userId}" data-action="approve">Approve</button>
            `;
        } else if (user.status === 'active') {
            actions = `
                <button class="action-btn disable-btn" data-userid="${user.userId}" data-action="disable">Disable</button>
            `;
        } else if (user.status === 'disabled') {
            actions = `
                <button class="action-btn enable-btn" data-userid="${user.userId}" data-action="enable">Enable</button>
            `;
        }
        
        // Role selector (can't change own role)
        const roleSelector = user.userId !== currentUser.userId ? `
            <select class="role-selector" data-userid="${user.userId}">
                <option value="operator" ${user.role === 'operator' ? 'selected' : ''}>operator</option>
                <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>admin</option>
            </select>
        ` : `<span class="badge ${roleClass}">${user.role}</span>`;
        
        return `
            <tr>
                <td>${escapeHtml(user.username)}</td>
                <td>${roleSelector}</td>
                <td><span class="badge ${statusClass}">${user.status}</span></td>
                <td>${createdDate}</td>
                <td class="actions-cell">${actions}</td>
            </tr>
        `;
    }
    
    function attachActionHandlers() {
        // Action buttons (approve, disable, enable)
        document.querySelectorAll('.action-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const userId = btn.dataset.userid;
                const action = btn.dataset.action;
                await performAction(userId, action);
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
    
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
})();
