/**
 * NOVA Panel Resizing
 * Handles collapsible and resizable panels following novaCore UI pattern
 */

function initPanelResizing() {
    const leftSidebar = document.getElementById('sidebar');
    const detailedPanel = document.getElementById('detailedPanel');
    const centerContainer = document.getElementById('centerContainer');
    const timeline = document.getElementById('timelineBar');
    
    const toggleBtn = document.getElementById('toggleBtn');
    const detailedPanelToggleBtn = document.getElementById('detailedPanelToggleBtn');
    
    // Default widths
    const DEFAULT_LEFT_WIDTH = 300;
    const DEFAULT_RIGHT_WIDTH = 350;
    const MIN_WIDTH = 200;
    const MAX_WIDTH = 600;
    
    // Initialize from localStorage or defaults
    // Default: left sidebar visible, right panel visible (to show shields and cards)
    let leftWidth = parseInt(localStorage.getItem('sidebar:left:width')) || DEFAULT_LEFT_WIDTH;
    let rightWidth = parseInt(localStorage.getItem('sidebar:right:width')) || DEFAULT_RIGHT_WIDTH;
    let leftVisible = localStorage.getItem('sidebar:left:visible') !== 'false';  // Default true
    let rightVisible = localStorage.getItem('sidebar:right:visible') !== 'false'; // Default true (changed)
    
    // Apply initial state - ALWAYS set class based on visibility (override HTML defaults)
    if (leftVisible) {
        leftSidebar.classList.remove('hidden');
        toggleBtn.setAttribute('aria-expanded', 'true');
    } else {
        leftSidebar.classList.add('hidden');
        toggleBtn.setAttribute('aria-expanded', 'false');
    }
    if (rightVisible) {
        detailedPanel.classList.remove('hidden');
        detailedPanelToggleBtn.setAttribute('aria-expanded', 'true');
    } else {
        detailedPanel.classList.add('hidden');
        detailedPanelToggleBtn.setAttribute('aria-expanded', 'false');
    }
    
    leftSidebar.style.width = `${leftWidth}px`;
    detailedPanel.style.width = `${rightWidth}px`;
    
    // Update center container position
    const updateCenterBounds = () => {
        const leftOffset = leftVisible ? leftWidth : 0;
        const rightOffset = rightVisible ? rightWidth : 0;
        centerContainer.style.left = `${leftOffset}px`;
        centerContainer.style.right = `${rightOffset}px`;
    };
    
    updateCenterBounds();
    
    // Left sidebar toggle
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            leftVisible = !leftVisible;
            if (leftVisible) {
                leftSidebar.classList.remove('hidden');
                toggleBtn.setAttribute('aria-expanded', 'true');
            } else {
                leftSidebar.classList.add('hidden');
                toggleBtn.setAttribute('aria-expanded', 'false');
            }
            localStorage.setItem('sidebar:left:visible', leftVisible);
            updateCenterBounds();
            updateLeftGutter();
            // Notify chat panel of sidebar change
            window.dispatchEvent(new CustomEvent('nova:sidebarResize'));
        });
    }
    
    // Right detailed panel toggle
    if (detailedPanelToggleBtn) {
        detailedPanelToggleBtn.addEventListener('click', () => {
            rightVisible = !rightVisible;
            if (rightVisible) {
                detailedPanel.classList.remove('hidden');
                detailedPanelToggleBtn.setAttribute('aria-expanded', 'true');
            } else {
                detailedPanel.classList.add('hidden');
                detailedPanelToggleBtn.setAttribute('aria-expanded', 'false');
            }
            localStorage.setItem('sidebar:right:visible', rightVisible);
            updateCenterBounds();
            updateRightGutter();
            // Notify chat panel of sidebar change
            window.dispatchEvent(new CustomEvent('nova:sidebarResize'));
        });
    }
    
    // Left sidebar resize gutter
    const leftGutter = document.createElement('div');
    leftGutter.className = 'resize-gutter resize-gutter-left';
    leftGutter.style.cssText = `
        position: fixed;
        top: 50px;
        bottom: 80px;
        width: 10px;
        background: transparent;
        cursor: ew-resize;
        z-index: 11000;
        transition: background 0.2s;
    `;
    document.body.appendChild(leftGutter);
    
    const updateLeftGutter = () => {
        if (!leftVisible) {
            leftGutter.style.display = 'none';
        } else {
            leftGutter.style.display = 'block';
            leftGutter.style.left = `${leftWidth - 5}px`;
        }
    };
    updateLeftGutter();
    
    leftGutter.addEventListener('mouseenter', () => {
        leftGutter.style.background = 'rgba(0, 212, 255, 0.6)';
    });
    leftGutter.addEventListener('mouseleave', () => {
        leftGutter.style.background = 'transparent';
    });
    
    let isDraggingLeft = false;
    let startX, startWidth;
    
    leftGutter.addEventListener('mousedown', (e) => {
        if (!leftVisible) return;
        isDraggingLeft = true;
        startX = e.clientX;
        startWidth = leftWidth;
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });
    
    // Right sidebar resize gutter
    const rightGutter = document.createElement('div');
    rightGutter.className = 'resize-gutter resize-gutter-right';
    rightGutter.style.cssText = `
        position: fixed;
        top: 50px;
        bottom: 80px;
        width: 10px;
        background: transparent;
        cursor: ew-resize;
        z-index: 11000;
        transition: background 0.2s;
    `;
    document.body.appendChild(rightGutter);
    
    const updateRightGutter = () => {
        if (!rightVisible) {
            rightGutter.style.display = 'none';
        } else {
            rightGutter.style.display = 'block';
            rightGutter.style.right = `${rightWidth - 5}px`;
        }
    };
    updateRightGutter();
    
    rightGutter.addEventListener('mouseenter', () => {
        rightGutter.style.background = 'rgba(0, 212, 255, 0.6)';
    });
    rightGutter.addEventListener('mouseleave', () => {
        rightGutter.style.background = 'transparent';
    });
    
    let isDraggingRight = false;
    
    rightGutter.addEventListener('mousedown', (e) => {
        if (!rightVisible) return;
        isDraggingRight = true;
        startX = e.clientX;
        startWidth = rightWidth;
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });
    
    // Mouse move handler for both gutters
    document.addEventListener('mousemove', (e) => {
        if (isDraggingLeft) {
            const delta = e.clientX - startX;
            leftWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startWidth + delta));
            leftSidebar.style.width = `${leftWidth}px`;
            updateLeftGutter();
            updateCenterBounds();
            // Notify chat panel during drag
            window.dispatchEvent(new CustomEvent('nova:sidebarResize'));
        } else if (isDraggingRight) {
            const delta = startX - e.clientX;
            rightWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startWidth + delta));
            detailedPanel.style.width = `${rightWidth}px`;
            updateRightGutter();
            updateCenterBounds();
            // Notify chat panel during drag
            window.dispatchEvent(new CustomEvent('nova:sidebarResize'));
        }
    });
    
    // Mouse up handler
    document.addEventListener('mouseup', () => {
        if (isDraggingLeft) {
            isDraggingLeft = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('sidebar:left:width', leftWidth);
            window.dispatchEvent(new CustomEvent('nova:sidebarResize'));
        } else if (isDraggingRight) {
            isDraggingRight = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('sidebar:right:width', rightWidth);
            window.dispatchEvent(new CustomEvent('nova:sidebarResize'));
        }
    });
}

// Export for init.js
window.initPanelResizing = initPanelResizing;
