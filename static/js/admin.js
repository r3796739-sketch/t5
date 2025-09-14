document.addEventListener('DOMContentLoaded', function() {
    // --- START: NEW Action Menu Logic ---
    // Close menus if user clicks outside
    window.addEventListener('click', function(e) {
        document.querySelectorAll('.action-menu.show').forEach(function(menu) {
            // If the click is outside the menu's parent card-actions, close it
            if (!menu.closest('.card-actions').contains(e.target)) {
                menu.classList.remove('show');
            }
        });
    });
    // --- END: NEW Action Menu Logic ---


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
    document.querySelectorAll('.action-menu.show').forEach(function(openMenu) {
        if (openMenu !== menu) {
            openMenu.classList.remove('show');
        }
    });
    // Then toggle the current one
    menu.classList.toggle('show');
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