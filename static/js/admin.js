/**
 * Show notification toast message
 * @param {string} message - The message to display
 * @param {string} type - Type: 'success', 'error', 'info', 'warning'
 */
function showNotification(message, type = 'info') {
    // Check if notification container exists
    let container = document.getElementById('notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notification-container';
        container.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10000;
            max-width: 400px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        `;
        document.body.appendChild(container);
    }

    // Create notification element
    const notification = document.createElement('div');
    const bgColor = {
        success: '#dcfce7',
        error: '#fee2e2',
        warning: '#fef3c7',
        info: '#dbeafe'
    }[type] || '#dbeafe';

    const borderColor = {
        success: '#86efac',
        error: '#fca5a5',
        warning: '#fcd34d',
        info: '#93c5fd'
    }[type] || '#93c5fd';

    const textColor = {
        success: '#15803d',
        error: '#991b1b',
        warning: '#b45309',
        info: '#1e40af'
    }[type] || '#1e40af';

    notification.style.cssText = `
        background: ${bgColor};
        border: 1px solid ${borderColor};
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 10px;
        color: ${textColor};
        font-weight: 500;
        font-size: 0.9rem;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        animation: slideIn 0.3s ease-out;
    `;
    notification.textContent = message;
    container.appendChild(notification);

    // Add animation
    if (!document.getElementById('notification-styles')) {
        const style = document.createElement('style');
        style.id = 'notification-styles';
        style.textContent = `
            @keyframes slideIn {
                from {
                    transform: translateX(400px);
                    opacity: 0;
                }
                to {
                    transform: translateX(0);
                    opacity: 1;
                }
            }
            @keyframes slideOut {
                from {
                    transform: translateX(0);
                    opacity: 1;
                }
                to {
                    transform: translateX(400px);
                    opacity: 0;
                }
            }
        `;
        document.head.appendChild(style);
    }

    // Auto-remove after 4 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-in forwards';
        setTimeout(() => notification.remove(), 300);
    }, 4000);
}

document.addEventListener('DOMContentLoaded', function() {
    // --- START: NEW Action Menu Logic ---
    // Close menus if user clicks outside
    window.addEventListener('click', function(e) {
        document.querySelectorAll('.action-menu').forEach(function(menu) {
            // If the click is outside the menu's parent card-actions, close it
            const cardActions = menu.closest('.card-actions');
            if (cardActions && !cardActions.contains(e.target)) {
                menu.style.display = 'none';
            }
        });
    });
    // --- END: NEW Action Menu Logic ---

    const payoutSearchForm = document.getElementById('payoutSearchForm');
    if (payoutSearchForm) {
        payoutSearchForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const searchInput = document.getElementById('payoutSearchInput');
            const query = searchInput.value.trim();
            // Reload the page with the search query as a URL parameter
            window.location.href = `/admin/dashboard?q=${encodeURIComponent(query)}`;
        });
    }
    const createPlanForm = document.getElementById('createPlanForm');
    if (createPlanForm) {
        createPlanForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());
            
            fetch('/api/admin/create_plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(response => response.json())
            .then(result => {
                if (result.status === 'success') {
                    showNotification(result.message, 'success');
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    throw new Error(result.message);
                }
            })
            .catch(error => showNotification(error.toString(), 'error'));
        });
    }
});

// --- START: NEW Function to toggle menus ---
function toggleActionMenu(button) {
    const menu = button.nextElementSibling;
    // Close all other open menus first
    document.querySelectorAll('.action-menu').forEach(function(openMenu) {
        if (openMenu !== menu && openMenu.style.display !== 'none') {
            openMenu.style.display = 'none';
        }
    });
    // Then toggle the current one
    const isHidden = menu.style.display === 'none' || !menu.style.display;
    menu.style.display = isHidden ? 'block' : 'none';
}
// --- END: NEW Function ---


function setCurrentPlan(id, type, planId) {
    fetch('/api/admin/set_current_plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id, type: type, plan_id: planId })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            setTimeout(() => window.location.reload(), 1500);
        } else {
            throw new Error(data.message);
        }
    })
    .catch(error => showNotification(error.toString(), 'error'));
}

function removePlan(id, type) {
    if (!confirm(`Are you sure you want to remove the current plan for this ${type}? This will revert it to the default plan.`)) {
        return;
    }
    fetch('/api/admin/remove_plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id, type: type })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            setTimeout(() => window.location.reload(), 1500);
        } else {
            throw new Error(data.message);
        }
    })
    .catch(error => showNotification(error.toString(), 'error'));
}

function deleteUser(userId, userEmail) {
    if (!confirm(`Are you absolutely sure you want to delete the user "${userEmail}"?\n\nThis action is permanent and will delete all their channels, chat history, and other associated data.`)) {
        return;
    }
    fetch(`/api/admin/delete_user/${userId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            setTimeout(() => window.location.reload(), 1500);
        } else {
            throw new Error(data.message);
        }
    })
    .catch(error => showNotification(error.toString(), 'error'));
}
function markPayoutAsPaid(payoutId, buttonElement) {
    buttonElement.disabled = true;
    buttonElement.textContent = 'Processing...';

    fetch(`/api/admin/complete_payout/${payoutId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            // Reload the page to see the updated status
            setTimeout(() => window.location.reload(), 1500);
        } else {
            throw new Error(data.message);
        }
    })
    .catch(error => {
        showNotification(error.toString(), 'error');
        buttonElement.disabled = false;
        buttonElement.textContent = 'Mark as Paid';
    });
}


// NOTE: The setDefaultPlan function is no longer used in this new UI, 
// as setting defaults can be a separate, less frequent action. 
// You can remove it or keep it if you plan to add a dedicated "Set Default" section later.