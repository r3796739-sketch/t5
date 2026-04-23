document.addEventListener('DOMContentLoaded', () => {

    // --- Close menus if user clicks outside ---
    window.addEventListener('click', function(e) {
        document.querySelectorAll('.action-menu.show').forEach(function(menu) {
            if (!menu.closest('.bot-actions').contains(e.target)) {
                menu.classList.remove('show');
            }
        });
    });

    // --- Modal Controls ---
    const createBotModal = document.getElementById('createBotModal');
    const editBotModal = document.getElementById('editBotModal');
    const shareBotModal = document.getElementById('shareBotModal');

    window.openCreateBotModal = () => createBotModal.style.display = 'flex';
    window.closeCreateBotModal = () => createBotModal.style.display = 'none';
    window.openEditBotModal = (botId) => {
        document.getElementById('edit_bot_id').value = botId;
        editBotModal.style.display = 'flex';
    };
    window.closeEditBotModal = () => editBotModal.style.display = 'none';
    window.openBotShareModal = (inviteLink, botName) => {
        document.getElementById('shareBotModalTitle').textContent = `Invite "${botName}"`;
        document.getElementById('shareLinkInput').value = inviteLink;
        document.getElementById('inviteLinkBtn').href = inviteLink;
        shareBotModal.style.display = 'flex';
    };
    window.closeBotShareModal = () => shareBotModal.style.display = 'none';

    // Close modals on overlay click
    [createBotModal, editBotModal, shareBotModal].forEach(modal => {
        if(modal) {
            modal.addEventListener('click', (e) => {
                if(e.target === modal) modal.style.display = 'none';
            });
        }
    });
    
    // --- Form Submission Logic ---
    const createBotForm = document.getElementById('createBotForm');
    if (createBotForm) {
        createBotForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const form = e.target;
            const channelUrlInput = document.getElementById('youtube_channel_url');
            if (!channelUrlInput.value) {
                showNotification('Please select a channel first.', 'error');
                return;
            }
            const formData = new FormData(form);
            const createBtn = form.querySelector('button[type="submit"]');
            createBtn.disabled = true;
            createBtn.textContent = 'Creating...';

            fetch("/integrations/discord/create", { method: 'POST', body: formData })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    showNotification('Bot creation started! It will appear shortly.', 'success');
                    closeCreateBotModal();
                    form.reset();
                    pollBotStatuses();
                } else { throw new Error(data.message); }
            })
            .catch(err => showNotification(`Error: ${err.message}`, 'error'))
            .finally(() => {
                createBtn.disabled = false;
                createBtn.textContent = 'Create Bot';
            });
        });
    }

    const editBotForm = document.getElementById('editBotForm');
    if(editBotForm) {
        editBotForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const form = e.target;
            const formData = new FormData(form);
            const botId = document.getElementById('edit_bot_id').value;
            const editBtn = form.querySelector('button[type="submit"]');
            editBtn.disabled = true;
            editBtn.textContent = 'Saving...';

            fetch(`/integrations/discord/update/${botId}`, { method: 'POST', body: formData })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    showNotification('Bot updated successfully! It will restart shortly.', 'success');
                    closeEditBotModal();
                    form.reset();
                    // Poll immediately to show 'connecting' state
                    pollBotStatuses();
                } else { throw new Error(data.message); }
            })
            .catch(err => showNotification(`Error: ${err.message}`, 'error'))
            .finally(() => {
                editBtn.disabled = false;
                editBtn.textContent = 'Save Changes';
            });
        });
    }

    // --- Channel Selector Logic ---
    const channelSelector = document.querySelector('.channel-selector');
    if (channelSelector) {
        const channelUrlInput = document.getElementById('youtube_channel_url');
        const channelCards = channelSelector.querySelectorAll('.channel-card');
        channelCards.forEach(card => {
            card.addEventListener('click', () => {
                channelCards.forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                channelUrlInput.value = card.dataset.url;
            });
        });
    }

    // --- Bot Action and Polling Logic ---
    if (document.querySelector('.bot-dashboard')) {
        pollBotStatuses(); 
        const pollingInterval = setInterval(pollBotStatuses, 5000);
    }
});

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

window.copyShareLink = function() {
    const input = document.getElementById('shareLinkInput');
    input.select();
    navigator.clipboard.writeText(input.value).then(() => {
        showNotification('Invite link copied!', 'success');
    }, () => {
        showNotification('Failed to copy link.', 'error');
    });
}

function pollBotStatuses() {
    if (!document.querySelector('.bot-dashboard')) return;

    fetch("/api/discord_bots/status")
        .then(res => res.json())
        .then(bots => {
            const grid = document.querySelector('.bot-dashboard .grid');
            if (!grid) return;
            
            bots.forEach(bot => {
                let card = grid.querySelector(`.bot-card[data-bot-id='${bot.id}']`);
                if (card && card.dataset.status !== bot.status) {
                    updateCardStatus(card, bot.status);
                }
            });
        })
        .catch(err => console.error("Polling error:", err));
}

function updateCardStatus(card, status) {
    card.dataset.status = status;
    const dot = card.querySelector('.status-dot');
    const text = card.querySelector('.status-text');
    const button = card.querySelector('.action-menu a:first-child');

    if (dot) dot.className = 'status-dot ' + status;
    if (text) text.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    
    if (button) {
        button.innerHTML = (status === 'online') ? 'Deactivate' : 'Activate';
    }
}


function toggleBot(botId, buttonEl) {
    const card = document.querySelector(`.bot-card[data-bot-id='${botId}']`);
    const isActivating = buttonEl.textContent.trim().toLowerCase() === 'activate';
    
    if (card) updateCardStatus(card, 'connecting');

    fetch(`/integrations/discord/toggle_bot/${botId}`, { method: 'POST' })
    .then(res => res.json())
    .then(data => {
        if (data.status !== 'success') throw new Error(data.message);
        showNotification(data.message, 'info');
        // Let the poller handle the final state update.
    })
    .catch(err => {
        showNotification(`Error: ${err.message}`, 'error');
        if (card) {
            // Revert to original state on failure
            const originalStatus = isActivating ? 'offline' : 'online';
            updateCardStatus(card, originalStatus);
        }
    });
}

function confirmDeleteBot(botId, botName, buttonEl) {
    if (!confirm(`Are you sure you want to permanently delete the bot "${botName}"? This action cannot be undone.`)) {
        return;
    }
    deleteBot(botId, buttonEl);
}

function deleteBot(botId, buttonEl) {
    const card = document.querySelector(`.bot-card[data-bot-id='${botId}']`);
    if(card) card.style.opacity = '0.5';

    fetch(`/integrations/discord/delete/${botId}`, { method: 'POST' })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            if (card) {
                card.remove();
            }
        } else { throw new Error(data.message); }
    })
    .catch(err => {
        showNotification(`Error: ${err.message}`, 'error');
        if(card) card.style.opacity = '1';
    });
}
