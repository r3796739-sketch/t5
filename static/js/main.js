// =================================================================
// 1. GLOBALLY ACCESSIBLE FUNCTIONS & CONFIG
// =================================================================

let userCurrency = 'INR'; // Default currency

/**
 * Helper function to safely escape HTML special characters from text.
 * (Consolidated from ask.js and app.js)
 */
const escapeHtml = (text) => {
    if (typeof text !== 'string') return '';
    const p = document.createElement('p');
    p.textContent = text;
    return p.innerHTML;
};

const showBannerNotice = (message, showUpgradeButton = true) => {
    const notice = document.getElementById('c-banner-notice');
    if (!notice) return;

    const noticeText = document.getElementById('c-banner-notice-text');
    const noticeBtn = document.getElementById('c-banner-notice-btn');

    if (noticeText) {
        noticeText.innerHTML = message;
    }
    if (noticeBtn) {
        noticeBtn.style.display = showUpgradeButton ? 'inline-block' : 'none';
    }

    notice.style.display = 'flex';
};


// =================================================================
// 2. CURRENCY LOCALIZATION & PRICING
// (From app.js)
// =================================================================

/**
 * Detects the user's country via a free API and sets the currency.
 * Defaults to USD for any country that is not India.
 */
async function localizeCurrency() {
    try {
        const response = await fetch('https://ipapi.co/json/');
        if (!response.ok) throw new Error('Failed to fetch geo data');
        const data = await response.json();

        if (data.country_code && data.country_code !== 'IN') {
            userCurrency = 'USD';
        }
    } catch (error) {
        console.warn('Could not detect user location, defaulting to INR.', error);
        userCurrency = 'INR';
    } finally {
        updatePricingDisplay();
    }
}

/**
 * Updates the pricing display on the landing and pricing modal pages.
 */
function updatePricingDisplay() {
    const prices = {
        personal: { INR: '₹299', USD: '$12.99' },
        creator: { INR: '₹1,499', USD: '$29.99' }
    };

    document.querySelectorAll('[data-plan-type="personal"] .price').forEach(el => {
        el.innerHTML = `${prices.personal[userCurrency]} <span>/ month</span>`;
    });
    document.querySelectorAll('[data-plan-type="creator"] .price').forEach(el => {
        el.innerHTML = `${prices.creator[userCurrency]} <span>/ month</span>`;
    });
}


// =================================================================
// 3. SUBSCRIPTION & PAYMENT
// (From app.js, modified)
// =================================================================

function buySubscription(planType, buttonElement) {
    if (buttonElement.disabled) return;

    const originalContent = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = `<div class="button-spinner"></div><span>Processing...</span>`;

    if (!IS_USER_LOGGED_IN) {
        localStorage.setItem('action_after_login', JSON.stringify({ type: 'buy_subscription', planType: planType }));
        showLoginPopup();
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalContent;
        return;
    }

    fetch('/create_razorpay_subscription', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_type: planType, currency: userCurrency })
    })
    .then(res => res.ok ? res.json() : res.json().then(err => Promise.reject(err)))
    .then(data => {
        if (data.status === 'success') {
            const options = {
                key: data.razorpay_key_id,
                subscription_id: data.subscription_id,
                name: 'YoppyChat AI',
                description: `Subscription for ${data.plan_name} plan`,
                handler: function (response) {
                    showNotification('Payment successful! Your plan has been updated.', 'success');
                    buttonElement.disabled = false;
                    buttonElement.innerHTML = originalContent;
                    setTimeout(() => window.location.reload(), 2000);
                },
                prefill: { name: data.user_name || "", email: data.user_email || "" },
                theme: { color: "#ff9a56" },
                modal: {
                    ondismiss: function() {
                        showNotification('Payment was cancelled.', 'info');
                        buttonElement.disabled = false;
                        buttonElement.innerHTML = originalContent;
                    }
                },
                config: {
                  display: {
                    blocks: {
                      upi: { name: "Pay with UPI", instruments: [{ method: "upi" }, { method: "intent" }] },
                      card: { name: "Pay with Card", instruments: [{ method: "card" }] }
                    },
                    sequence: ["block.upi", "block.card"],
                    preferences: { show_default_blocks: true }
                  }
                }
            };
            const rzp = new Razorpay(options);
            rzp.open();
        } else {
            throw new Error(data.message);
        }
    })
    .catch(error => {
        showNotification(error.message || 'An error occurred.', 'error');
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalContent;
    });
}


// =================================================================
// 4. SPA-LIKE CHANNEL NAVIGATION
// (From app.js, with dependencies from ask.js now available)
// =================================================================

function initializeSpaNavigation() {
    document.body.addEventListener('click', function(event) {
        const link = event.target.closest('.channel-link');
        if (!link) return;

        const chatContainer = document.getElementById('conversation-history');
        if (!chatContainer) return;

        event.preventDefault();
        const channelUrl = link.href;
        const channelName = new URL(channelUrl).pathname.split('/').pop();

        fetch(`/api/channel_details/${channelName}`)
            .then(response => response.ok ? response.json() : Promise.reject('Failed to load channel details.'))
            .then(data => {
                updateChannelShell(data);
                // Hide the notice banner when navigating
                const notice = document.getElementById('c-banner-notice');
                if (notice) {
                    notice.style.display = 'none';
                }
                history.pushState({channel: channelName}, '', channelUrl);
                return fetch(`/api/chat_history/${channelName}`);
            })
            .then(response => response.ok ? response.json() : Promise.reject('Failed to load chat history.'))
            .then(data => renderChatHistory(data.history))
            .catch(error => {
                console.error('Error loading channel:', error);
                if (chatContainer) chatContainer.innerHTML = `<p class="error-message">Could not load channel data.</p>`;
            });
    });
}

function updateChannelShell(data) {
    const { current_channel } = data;

    // Mobile Header
    const mobileHeader = document.querySelector('.mobile-header-channel');
    if (mobileHeader) {
        mobileHeader.innerHTML = `
            <img src="${escapeHtml(current_channel.channel_thumbnail)}" alt="${escapeHtml(current_channel.channel_name)}" class="mobile-channel-avatar">
            <div class="mobile-channel-text-details">
                <span class="mobile-channel-title">${escapeHtml(current_channel.channel_name)}</span>
            </div>`;
    }

    // Desktop Right Sidebar
    const desktopSidebar = document.querySelector('.chat-sidebar-right');
    if (desktopSidebar) {
        const topicsHTML = current_channel.topics && current_channel.topics.length > 0
            ? `<div class="profile-topics"><h3 class="topics-title">Popular Topics</h3><div class="topics-tags">${current_channel.topics.map(topic => `<span class="tag" onclick="askTopic(this)">${escapeHtml(topic)}</span>`).join('')}</div></div>`
            : '';

        const actionsHTML = `
            <div class="profile-actions-new">
                 <button onclick="openShareModal()" class="action-btn-primary">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>
                    <span>Share</span>
                </button>
                <div class="secondary-actions">
                    <a href="${escapeHtml(current_channel.channel_url) || '#'}" target="_blank" rel="noopener noreferrer" class="action-btn-secondary" data-tooltip="View on YouTube">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.42a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .54 5.33A2.78 2.78 0 0 0 3.46 19c1.72.42 8.6.42 8.6.42s6.88 0 8.6-.42a2.78 2.78 0 0 0 1.94-2A29 29 0 0 0 23 11.75a29 29 0 0 0-.46-5.33z"></path><polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02"></polygon></svg>
                    </a>
                    <button onclick="refreshChannel('${current_channel.id}', this);" class="action-btn-secondary" data-tooltip="Update">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/></svg>
                    </button>
                    <button onclick="clearChat('${escapeHtml(current_channel.channel_name)}');" class="action-btn-secondary" data-tooltip="Clear Chat">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18m-2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>`;

        desktopSidebar.innerHTML = `
            <div class="channel-profile-card">
                <div class="profile-header">
                    <div class="profile-avatar"><img src="${escapeHtml(current_channel.channel_thumbnail)}" alt="${escapeHtml(current_channel.channel_name)}"><span class="ai-badge">AI</span></div>
                    <div class="profile-info"><h2 class="profile-name">${escapeHtml(current_channel.channel_name)}</h2><p class="profile-description">${escapeHtml(current_channel.summary) || ''}</p></div>
                </div>
                ${actionsHTML}
                ${topicsHTML}
            </div>`;
    }

    // Update active state in left sidebar
    document.querySelectorAll('.channel-item-wrapper').forEach(link => {
        link.classList.remove('active');
        if (link.dataset.channelId == current_channel.id) {
            link.classList.add('active');
        }
    });

    // Show loading skeleton
    const chatContainer = document.getElementById('conversation-history');
    if (chatContainer) {
        chatContainer.innerHTML = `
            <div class="qna-pair"><div class="question-box skeleton" style="width: 60%;"><div class="skeleton-line"></div></div>
            <div class="answer-box"><div class="answer-header"><div class="skeleton-avatar skeleton"></div><div class="skeleton-line skeleton" style="width: 100px;"></div></div><div class="skeleton-line skeleton" style="width: 90%;"></div><div class="skeleton-line skeleton" style="width: 80%; margin-top: 8px;"></div></div></div>`;
    }

    const pageDataContainer = document.getElementById('chat-page-data');
    if (pageDataContainer) {
        pageDataContainer.dataset.channelName = current_channel.channel_name;
        pageDataContainer.dataset.channelThumbnail = current_channel.channel_thumbnail;
    }
}

function renderChatHistory(history) {
    const chatContainer = document.getElementById('conversation-history');
    const pageData = document.getElementById('chat-page-data').dataset;
    const channelName = pageData.channelName;
    const channelThumbnail = pageData.channelThumbnail;

    if (!chatContainer) return;
    chatContainer.innerHTML = '';

    if (history && Array.isArray(history) && history.length > 0) {
        history.forEach((qa, index) => {
            const isLast = index === history.length - 1;
            const qnaPair = document.createElement('div');
            qnaPair.className = 'qna-pair';
            const avatarHtml = channelThumbnail ? `<div class="answer-avatar-container avatar-container"><img src="${channelThumbnail}" alt="${channelName}" class="answer-avatar"><span class="ai-badge">AI</span></div>` : `<div class="answer-avatar-container avatar-container"><div class="answer-avatar-placeholder">🤖</div><span class="ai-badge">AI</span></div>`;
            const answerLabel = channelName ? `${escapeHtml(channelName)}` : 'Answer';
            let sourcesButtonHtml = '';
            let sourcesListHtml = '';
            if (qa.sources && qa.sources.length > 0) {
                const sourceLinks = qa.sources.map(s => `<div class="source-item"><a href="${escapeHtml(s.url)}" target="_blank" class="source-link"><span class="source-title">${escapeHtml(s.title)}</span></a></div>`).join('');
                sourcesButtonHtml = `<button class="toggle-sources-btn" onclick="toggleSources(this)"><svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M4,6H2V20a2,2 0 0,0 2,2H18V18H4V6M20,2H8A2,2 0 0,0 6,4V16a2,2 0 0,0 2,2H20a2,2 0 0,0 2-2V4a2,2 0 0,0-2-2Z"></path></svg>Sources (${qa.sources.length}) <span class="toggle-indicator">▼</span></button>`;
                sourcesListHtml = `<div class="sources-list" style="display: none;">${sourceLinks}</div>`;
            }
            let regenerateHtml = isLast ? `<button class="toggle-sources-btn regenerate-btn-js" onclick="regenerateAnswer(this)"><svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>Regenerate</button>` : '';
            const copyButtonHtml = `<button class="copy-answer-btn" onclick="copyAnswer(this)" data-tooltip="Copy answer"><svg class="icon-copy-default" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg><svg class="icon-copy-check" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg></button>`;
            const sourcesSectionHtml = `<div class="sources-section"><div class="source-buttons">${sourcesButtonHtml}${regenerateHtml}${copyButtonHtml}</div>${sourcesListHtml}</div>`;
            qnaPair.innerHTML = `<div class="question-box"><div class="question-content">${escapeHtml(qa.question)}</div></div><div class="answer-box"><div class="answer-header">${avatarHtml}<span class="answer-label">${answerLabel}</span></div><div class="answer-content">${window.marked ? window.marked.parse(qa.answer || '') : qa.answer}</div><div class="typing-container"><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>${sourcesSectionHtml}</div>`;
            chatContainer.appendChild(qnaPair);
        });
    } else {
        chatContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted);">No conversation history yet. Ask a question to get started!</p>`;
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;
}


// =================================================================
// 5. CHAT & CHANNEL ACTIONS
// (From ask.js)
// =================================================================

window.copyAnswer = function(buttonElement) {
    const answerBox = buttonElement.closest('.answer-box');
    if (!answerBox) return;
    const answerContent = answerBox.querySelector('.answer-content');
    if (!answerContent) return;
    navigator.clipboard.writeText(answerContent.innerText).then(() => {
        const iconCopy = buttonElement.querySelector('.icon-copy-default');
        const iconCheck = buttonElement.querySelector('.icon-copy-check');
        if(iconCopy) iconCopy.style.display = 'none';
        if(iconCheck) iconCheck.style.display = 'inline-block';
        if (window.showNotification) window.showNotification('Answer copied to clipboard!', 'success');
        setTimeout(() => {
            if(iconCopy) iconCopy.style.display = 'inline-block';
            if(iconCheck) iconCheck.style.display = 'none';
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy text: ', err);
        if (window.showNotification) window.showNotification('Failed to copy text.', 'error');
    });
}

window.showCustomConfirm = function(title, message, confirmText, onConfirm) {
    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog';
    dialog.innerHTML = `<div class="confirm-content"><h3>${title}</h3><p>${message}</p><div class="confirm-actions"><button class="cancel-btn">Cancel</button><button class="confirm-btn">${confirmText}</button></div></div>`;
    document.body.appendChild(dialog);
    dialog.style.display = 'flex';
    const confirmBtn = dialog.querySelector('.confirm-btn');
    const cancelBtn = dialog.querySelector('.cancel-btn');
    const closeDialog = () => document.body.removeChild(dialog);
    cancelBtn.onclick = closeDialog;
    confirmBtn.onclick = () => { onConfirm(); closeDialog(); };
}

window.clearChat = function(channel) {
    window.showCustomConfirm('Clear Chat History', 'Are you sure you want to clear this chat? This action cannot be undone.', 'Clear', () => {
        const formData = new FormData();
        formData.append('channel_name', channel);
        fetch('/clear_chat', { method: 'POST', body: formData })
            .then(res => { if (res.ok) window.location.reload(); else Promise.reject('Failed to clear chat.'); })
            .catch(err => { if (window.showNotification) window.showNotification(err.toString(), 'error'); });
    });
}

window.setDefaultChannel = function(channelId, buttonElement) {
    if (!buttonElement || buttonElement.disabled) return;
    const originalContent = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = `<div class="button-spinner" style="width:16px;height:16px;border-width:2px;"></div><span>Setting...</span>`;
    fetch(`/set-default-channel/${channelId}`, { method: 'POST' })
        .then(res => res.ok ? res.json() : res.json().then(err => Promise.reject(err)))
        .then(data => {
            if (window.showNotification) window.showNotification(data.message, 'success');
            setTimeout(() => window.location.reload(), 1500);
        })
        .catch(err => {
            if (window.showNotification) window.showNotification(err.message || 'An error occurred.', 'error');
            buttonElement.disabled = false;
            buttonElement.innerHTML = originalContent;
        });
}

window.toggleChannelPrivacy = function(channelId, isShared) {
    const toggle = document.getElementById('shareToggle');
    if(toggle) toggle.disabled = true;
    fetch(`/api/toggle_channel_privacy/${channelId}`, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    .then(res => res.ok ? res.json() : res.json().then(err => Promise.reject(err)))
    .then(data => {
        if (window.showNotification) window.showNotification(data.message, 'success');
        setTimeout(() => window.location.reload(), 1500);
    })
    .catch(err => {
        if (window.showNotification) window.showNotification(err.message || 'An error occurred.', 'error');
        if(toggle) toggle.checked = !isShared;
    })
    .finally(() => { if(toggle && !document.querySelector('.notification.success')) toggle.disabled = false; });
}

window.toggleSources = function(btn) {
    const sourcesSection = btn.closest('.sources-section');
    if (!sourcesSection) return;
    const list = sourcesSection.querySelector('.sources-list');
    const indicator = btn.querySelector('.toggle-indicator');
    if (!list) return;
    const isExpanded = list.style.display === 'block';
    list.style.display = isExpanded ? 'none' : 'block';
    if (indicator) indicator.style.transform = isExpanded ? 'rotate(0deg)' : 'rotate(180deg)';
}

window.refreshChannel = function(channelId, buttonElement) {
    if (!buttonElement || buttonElement.disabled) return;
    const originalContent = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = `<div class="button-spinner" style="width:20px;height:20px;border-width:2px;"></div><span>Refreshing...</span>`;
    fetch(`/refresh_channel/${channelId}`, { method: 'POST' })
        .then(response => response.ok ? response.json() : response.json().then(err => Promise.reject(err)))
        .then(data => {
            if (data.status === 'success' && data.task_id) checkTaskStatus(data.task_id, buttonElement, originalContent);
            else throw new Error(data.message || 'Failed to start refresh task.');
        })
        .catch(error => {
            if (window.showNotification) window.showNotification(error.message || 'Refresh failed to start.', 'error');
            restoreButton(buttonElement, originalContent);
        });
}

function checkTaskStatus(taskId, buttonElement, originalContent) {
    const interval = setInterval(() => {
        fetch(`/task_result/${taskId}`)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'complete' || data.status === 'failed') {
                    clearInterval(interval);
                    if (window.showNotification && data.message) {
                        window.showNotification(data.message, data.status === 'complete' ? 'success' : 'error');
                    }
                    restoreButton(buttonElement, originalContent);
                    if (data.status === 'complete') setTimeout(() => window.location.reload(), 1500);
                }
            })
            .catch(error => {
                clearInterval(interval);
                if (window.showNotification) window.showNotification('Error checking task status.', 'error');
                restoreButton(buttonElement, originalContent);
            });
    }, 3000);
}

function restoreButton(button, originalHTML) {
    if (!button) return;
    button.innerHTML = originalHTML;
    button.disabled = false;
}

window.askExample = function(element) {
    const questionText = document.getElementById('questionText');
    const questionForm = document.getElementById('questionForm');
    if (questionText && questionForm) {
        questionText.value = element.textContent;
        questionText.dispatchEvent(new Event('input', { bubbles: true }));
        questionForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    }
}

window.askTopic = function(element) {
    const topic = element.textContent;
    const question = `Tell me more about ${topic}`;
    const questionText = document.getElementById('questionText');
    const questionForm = document.getElementById('questionForm');
    if (questionText && questionForm) {
        questionText.value = question;
        questionText.dispatchEvent(new Event('input', { bubbles: true }));
        questionForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    }
}


// =================================================================
// 6. SHARE MODAL
// (Consolidated from app.js and ask.js)
// =================================================================

let shareHistoryId = null;

function openShareModal() {
    const channelName = document.getElementById('chat-page-data')?.dataset.channelName;
    if (channelName === undefined) { // Check for undefined for general chat page
        if(window.showNotification) showNotification('Sharing is only available for specific channels.', 'info');
        return;
    }
    const modal = document.getElementById('shareModal');
    if (!modal) return;

    const input = document.getElementById('shareLinkInput');
    const toggle = document.getElementById('includeHistoryToggle');
    const wrapper = document.getElementById('linkCopyWrapper');
    const subtitle = document.getElementById('shareModalSubtitle');

    if (!input || !toggle || !wrapper || !subtitle) return;

    const baseUrl = channelName ? `${window.location.origin}/c/${encodeURIComponent(channelName)}` : window.location.href;
    toggle.checked = false;
    shareHistoryId = null;
    input.value = baseUrl;

    wrapper.classList.remove('copied');
    wrapper.querySelector('.copy-text').textContent = 'Copy';
    wrapper.querySelector('.icon-copy-default').style.display = 'block';
    wrapper.querySelector('.icon-copy-check').style.display = 'none';
    subtitle.textContent = 'Share a link to this AI assistant.';
    modal.style.display = 'flex';
}

function closeShareModal() {
    const modal = document.getElementById('shareModal');
    if (modal) modal.style.display = 'none';
}

window.copyShareLinkFromModal = function(wrapperElement) {
    const input = wrapperElement.querySelector('#shareLinkInput');
    const copyText = wrapperElement.querySelector('.copy-text');
    const iconDefault = wrapperElement.querySelector('.icon-copy-default');
    const iconCheck = wrapperElement.querySelector('.icon-copy-check');

    if (!input || wrapperElement.classList.contains('copied')) return;

    input.select();
    navigator.clipboard.writeText(input.value).then(() => {
        wrapperElement.classList.add('copied');
        if(copyText) copyText.textContent = 'Copied!';
        if(iconDefault) iconDefault.style.display = 'none';
        if(iconCheck) iconCheck.style.display = 'block';
        setTimeout(() => {
            if(copyText) copyText.textContent = 'Copy';
            if(iconDefault) iconDefault.style.display = 'block';
            if(iconCheck) iconCheck.style.display = 'none';
            wrapperElement.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        if (window.showNotification) window.showNotification('Failed to copy link.', 'error');
    });
}


// =================================================================
// 7. DOMContentLoaded - MAIN INITIALIZATION LOGIC
// (Consolidated from app.js and ask.js)
// =================================================================
document.addEventListener('DOMContentLoaded', function() {

    // --- A. Global Initializations ---
    localizeCurrency();
    initializeSpaNavigation();

    // Resume action after login
    if (typeof IS_USER_LOGGED_IN !== 'undefined' && IS_USER_LOGGED_IN) {
        const pendingActionJSON = localStorage.getItem('action_after_login');
        if (pendingActionJSON) {
            const pendingAction = JSON.parse(pendingActionJSON);
            localStorage.removeItem('action_after_login');
            if (pendingAction.type === 'buy_subscription' && pendingAction.planType) {
                // We need to find a button to pass to the function.
                // This is a bit tricky, we'll find the first matching button.
                const button = document.querySelector(`.btn-pricing[onclick*="'${pendingAction.planType}'"]`);
                if (button) buySubscription(pendingAction.planType, button);
            }
        }
    }

    // --- B. Chat Page Specific Initializations ---
    const questionForm = document.getElementById('questionForm');
    if (questionForm) {

        // --- NEW: Banner Notice Logic ---
        if (typeof NOTICE_DATA !== 'undefined' && NOTICE_DATA && NOTICE_DATA.message) {
            showBannerNotice(NOTICE_DATA.message, NOTICE_DATA.show_upgrade);
        } else if (typeof IS_TEMPORARY_SESSION !== 'undefined' && IS_TEMPORARY_SESSION) {
            // Fallback for client-side only notice
            showBannerNotice("You've reached your channel limit. You can ask 5 questions in this temporary session.", true);
        }

        const noticeBanner = document.getElementById('c-banner-notice');
        if (noticeBanner) {
            const closeBtn = noticeBanner.querySelector('.notice-close-btn');
            if (closeBtn) {
                closeBtn.addEventListener('click', () => {
                    noticeBanner.style.display = 'none';
                });
            }
        }
        // --- END: Banner Notice Logic ---

        // We are on a page with a chat form, so initialize all chat logic.
        const questionText = document.getElementById('questionText');
        const submitBtn = document.getElementById('submitBtn');
        const conversationHistory = document.getElementById('conversation-history');
        const charCount = document.querySelector('.char-count');
        const includeHistoryToggle = document.getElementById('includeHistoryToggle');
        const shareLinkInput = document.getElementById('shareLinkInput');
        const shareModal = document.getElementById('shareModal');

        // Restore pending question after login
        if (typeof IS_USER_LOGGED_IN !== 'undefined' && IS_USER_LOGGED_IN && localStorage.getItem('pending_question')) {
            const pendingQuestion = localStorage.getItem('pending_question');
            localStorage.removeItem('pending_question');
            if (questionText && questionForm && pendingQuestion) {
                questionText.value = pendingQuestion;
                questionText.dispatchEvent(new Event('input', { bubbles: true }));
                questionForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }
        }

        const adjustTextareaHeight = () => {
            if (!questionText) return;
            questionText.style.height = 'auto';
            questionText.style.height = `${Math.min(questionText.scrollHeight, 120)}px`;
        };

        const updateCharCount = () => {
            if (!questionText || !charCount) return;
            const count = questionText.value.length;
            charCount.textContent = `${count}/1000`;
            charCount.classList.toggle('near-limit', count > 800);
        };

        const addMessageToChat = (role, text) => {
            const pageData = document.getElementById('chat-page-data').dataset;
            const channelName = pageData.channelName;
            const channelThumbnail = pageData.channelThumbnail;
            const qnaPair = document.createElement('div');
            qnaPair.className = 'qna-pair';
            let html = '';
            if (role === 'user') {
                html = `<div class="question-box"><div class="question-content">${escapeHtml(text)}</div></div>`;
            } else {
                const avatarHtml = channelThumbnail ? `<div class="answer-avatar-container avatar-container"><img src="${channelThumbnail}" alt="Avatar" class="answer-avatar"><span class="ai-badge">AI</span></div>` : `<div class="answer-avatar-container avatar-container"><div class="answer-avatar-placeholder">🤖</div><span class="ai-badge">AI</span></div>`;
                const label = channelName ? `${escapeHtml(channelName)}` : 'Answer';
                html = `<div class="answer-box"><div class="answer-header">${avatarHtml}<span class="answer-label">${label}</span></div><div class="answer-content"></div><div class="typing-container"><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div><div class="sources-section"></div></div>`;
            }
            qnaPair.innerHTML = html;
            conversationHistory.appendChild(qnaPair);
            conversationHistory.scrollTop = conversationHistory.scrollHeight;
            return role === 'ai' ? qnaPair.querySelector('.answer-box') : null;
        };

        const renderSourcesAndActions = (sources, answerBoxElement) => {
            if (!answerBoxElement) return;
            let sourcesSection = answerBoxElement.querySelector('.sources-section');
            if (!sourcesSection) return;
            sourcesSection.innerHTML = ''; // Clear previous content

            let sourcesButtonHTML = '';
            let sourcesListHTML = '';
            if (sources && sources.length > 0) {
                sourcesButtonHTML = `<button class="toggle-sources-btn" onclick="toggleSources(this)"><svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M4,6H2V20a2,2 0 0,0 2,2H18V18H4V6M20,2H8A2,2 0 0,0 6,4V16a2,2 0 0,0 2,2H20a2,2 0 0,0 2-2V4a2,2 0 0,0-2-2Z"></path></svg>Sources (${sources.length})<span class="toggle-indicator">▼</span></button>`;
                const sourceLinks = sources.map(s => `<div class="source-item"><a href="${escapeHtml(s.url)}" target="_blank" class="source-link"><span class="source-title">${escapeHtml(s.title)}</span></a></div>`).join('');
                sourcesListHTML = `<div class="sources-list" style="display: none;">${sourceLinks}</div>`;
            }

            const regenerateButtonHTML = `<button class="toggle-sources-btn regenerate-btn-js" onclick="regenerateAnswer(this)"><svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>Regenerate</button>`;
            const copyButtonHTML = `<button class="copy-answer-btn" onclick="copyAnswer(this)" data-tooltip="Copy answer"><svg class="icon-copy-default" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg><svg class="icon-copy-check" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg></button>`;

            // Correctly structure the HTML
            sourcesSection.innerHTML = `
                <div class="source-buttons">
                    ${sourcesButtonHTML}
                    ${regenerateButtonHTML}
                    ${copyButtonHTML}
                </div>
                ${sourcesListHTML}
            `;
        };

        const processStream = (reader, answerContent, typingContainer, aiAnswerBox) => {
            const decoder = new TextDecoder();
            let fullAnswerText = '';
            let foundSources = [];
            let buffer = ''; // Buffer to store incomplete chunks
        
            const push = () => {
                reader.read().then(({ done, value }) => {
                    if (done) {
                        if (typingContainer) typingContainer.classList.remove('active');
                        renderSourcesAndActions(foundSources, aiAnswerBox);
                        return;
                    }
                
                    // Append new data to the buffer
                    buffer += decoder.decode(value, { stream: true });
                    
                    // Process all complete messages in the buffer
                    let boundary = buffer.lastIndexOf('\n\n');
                    if (boundary !== -1) {
                        const completeMessages = buffer.substring(0, boundary);
                        const lines = completeMessages.split('\n\n');
                    
                        lines.forEach(line => {
                            if (line.startsWith('data: ')) {
                                const data = line.substring(6);
                                if (data.trim() === '[DONE]') return;
                            
                                // *** THIS IS THE FIX ***
                                // Only try to parse if the data is not empty
                                if (data.trim()) { 
                                    try {
                                        const parsed = JSON.parse(data);
                                        if (parsed.error) throw new Error(parsed.message || 'An unknown error occurred.');
                                        if (parsed.answer) {
                                            fullAnswerText += parsed.answer;
                                            answerContent.innerHTML = marked.parse(fullAnswerText);
                                        }
                                        if (parsed.sources) foundSources = parsed.sources;
                                        if (parsed.updated_query_string) {
                                            const planQueriesElement = document.querySelector('.plan-queries');
                                            if (planQueriesElement) planQueriesElement.innerHTML = parsed.updated_query_string;
                                        }
                                    } catch (e) { 
                                        console.error('Error parsing stream data:', e, 'Problematic data:', data); 
                                        // We throw the error to be caught by the outer catch block
                                        throw e; 
                                    }
                                }
                            }
                        });
                    
                        // Keep the incomplete part for the next chunk
                        buffer = buffer.substring(boundary + 2);
                    }
                    
                    conversationHistory.scrollTop = conversationHistory.scrollHeight;
                    push();
                
                }).catch(error => {
                    console.error("Stream reading error:", error);
                    answerContent.innerHTML = `<p class="error-message">${escapeHtml(error.message || 'An error occurred during generation.')}</p>`;
                    if (typingContainer) typingContainer.classList.remove('active');
                    renderSourcesAndActions([], aiAnswerBox);
                    if (reader) reader.cancel();
                });
            };
            push();
        };

        const handleFormSubmit = (event) => {
            event.preventDefault();
            if (typeof IS_USER_LOGGED_IN !== 'undefined' && !IS_USER_LOGGED_IN) {
                const question = questionText.value.trim();
                if (question) localStorage.setItem('pending_question', question);
                showLoginPopup();
                return;
            }
            const question = questionText.value.trim();
            if (!question) return;
            document.querySelectorAll('.regenerate-btn-js').forEach(btn => btn.remove());
            submitBtn.disabled = true;
            submitBtn.classList.remove('active');
            questionText.value = '';
            adjustTextareaHeight();
            updateCharCount();
            addMessageToChat('user', question);
            const aiAnswerBox = addMessageToChat('ai', '');
            const answerContent = aiAnswerBox.querySelector('.answer-content');
            const typingContainer = aiAnswerBox.querySelector('.typing-container');
            typingContainer.classList.add('active');
            const formData = new FormData();
            formData.append('question', question);
            formData.append('channel_name', document.getElementById('chat-page-data').dataset.channelName);
            formData.append('is_regenerating', 'false');
            fetch('/stream_answer', { method: 'POST', body: formData })
                .then(response => response.ok ? response.body.getReader() : response.json().then(err => Promise.reject(err)))
                .then(reader => { if (reader) processStream(reader, answerContent, typingContainer, aiAnswerBox); })
                .catch(err => {
                    console.error('Fetch error:', err);
                    const message = err.message || 'Failed to send question. Please try again.';
                    if (err.status === 'limit_reached') {
                        showBannerNotice(message);
                        // Clean up the temporary answer box that was created
                        if(aiAnswerBox.closest('.qna-pair')) {
                            aiAnswerBox.closest('.qna-pair').remove();
                        }
                    } else {
                        answerContent.innerHTML = `<p class="error-message">${escapeHtml(message)}</p>`;
                    }
                    typingContainer.classList.remove('active');
                });
        };

        questionForm.addEventListener('submit', handleFormSubmit);

        if (questionText) {
            questionText.addEventListener('input', () => {
                const hasText = questionText.value.trim().length > 0;
                if(submitBtn) {
                    submitBtn.disabled = !hasText;
                    submitBtn.classList.toggle('active', hasText);
                }
                adjustTextareaHeight();
                updateCharCount();
            });
            questionText.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    if (submitBtn && !submitBtn.disabled) questionForm.requestSubmit();
                }
            });
        }

        window.regenerateAnswer = function(btn) {
            const lastQnaPair = btn.closest('.qna-pair');
            if (!lastQnaPair) return;
            const questionContent = lastQnaPair.querySelector('.question-box .question-content');
            const answerBox = lastQnaPair.querySelector('.answer-box');
            if (!questionContent || !answerBox) return;
            const lastQuestion = questionContent.textContent;
            btn.disabled = true;
            btn.innerHTML = 'Regenerating...';
            document.querySelectorAll('.regenerate-btn-js').forEach(b => { if (b !== btn) b.remove(); });
            const answerContent = answerBox.querySelector('.answer-content');
            const sourcesSection = answerBox.querySelector('.sources-section');
            const typingContainer = answerBox.querySelector('.typing-container');
            answerContent.innerHTML = '';
            if (sourcesSection) sourcesSection.innerHTML = '';
            if (typingContainer) typingContainer.classList.add('active');
            const formData = new FormData();
            formData.append('question', lastQuestion);
            formData.append('channel_name', document.getElementById('chat-page-data').dataset.channelName || '');
            formData.append('is_regenerating', 'true');
            fetch('/stream_answer', { method: 'POST', body: formData })
                .then(response => response.ok ? response.body.getReader() : response.json().then(err => Promise.reject(err)))
                .then(reader => processStream(reader, answerContent, typingContainer, answerBox))
                .catch(err => {
                    answerContent.innerHTML = `<p class="error-message">Failed to regenerate: ${err.message || 'Unknown error'}</p>`;
                    if (typingContainer) typingContainer.classList.remove('active');
                    renderSourcesAndActions([], answerBox);
                });
        };

        if (includeHistoryToggle && shareLinkInput) {
            const subtitle = document.getElementById('shareModalSubtitle');
            includeHistoryToggle.addEventListener('change', async (event) => {
                const channelName = document.getElementById('chat-page-data').dataset.channelName;
                const baseUrl = `${window.location.origin}/c/${encodeURIComponent(channelName)}`;
                if (subtitle) subtitle.textContent = event.target.checked ? 'Anyone with the link can view this chat.' : 'Share a link to this AI assistant.';
                if (event.target.checked) {
                    const history = Array.from(document.querySelectorAll('#conversation-history .qna-pair')).map(pair => ({
                        question: pair.querySelector('.question-content')?.textContent || '',
                        answer: pair.querySelector('.answer-content')?.innerHTML || '',
                        sources: Array.from(pair.querySelectorAll('.source-link')).map(s => ({ title: s.textContent, url: s.href }))
                    })).filter(qa => qa.question && qa.answer);
                    if (history.length === 0) {
                        showNotification('There is no chat history to share.', 'info');
                        event.target.checked = false;
                        if (subtitle) subtitle.textContent = 'Share a link to this AI assistant.';
                        return;
                    }
                    try {
                        const response = await fetch('/api/share_chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ history }) });
                        const data = await response.json();
                        if (data.status === 'success') {
                            shareHistoryId = data.history_id;
                            shareLinkInput.value = `${baseUrl}?history_id=${shareHistoryId}`;
                        } else throw new Error(data.message);
                    } catch (error) {
                        showNotification(error.message || 'Could not create share link.', 'error');
                        event.target.checked = false;
                        if (subtitle) subtitle.textContent = 'Share a link to this AI assistant.';
                    }
                } else {
                    shareLinkInput.value = baseUrl;
                    shareHistoryId = null;
                }
            });
        }

        if (shareModal) {
            shareModal.addEventListener('click', function(event) {
                if (event.target === shareModal) closeShareModal();
            });
        }

        const initChatPage = () => {
            if (questionText) {
                questionText.focus();
                adjustTextareaHeight();
                updateCharCount();
            }
            if (conversationHistory) conversationHistory.scrollTop = conversationHistory.scrollHeight;
        };
        initChatPage();
    } // End of chat page specific initializations

    // --- C. Mobile Menu Initializations ---
    const kebabBtn = document.getElementById('kebab-menu-btn-mobile');
    const kebabMenu = document.getElementById('dropdown-menu-mobile');
    if (kebabBtn && kebabMenu) {
        kebabBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            kebabMenu.classList.toggle('show');
        });
        window.addEventListener('click', () => {
            kebabMenu.classList.remove('show');
        });
    }

    const hamburgerBtnMobile = document.getElementById('hamburgerBtnMobile');
    const mainHamburger = document.getElementById('hamburgerBtn');
    if (hamburgerBtnMobile && mainHamburger) {
        hamburgerBtnMobile.addEventListener('click', () => mainHamburger.click());
    }
});
