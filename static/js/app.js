document.addEventListener('DOMContentLoaded', function() {
    // This function adds the SPA-like navigation to the channel links
    function initializeSpaNavigation() {
        // We need to re-run this logic if new links are added to the page,
        // so we attach it to the document.
        document.body.addEventListener('click', function(event) {
            // Find the link that was clicked, if it's a channel link
            const link = event.target.closest('.channel-link');
            if (!link) {
                return;
            }

            // Check if we are already on a chat page by looking for the chat history container.
            const chatContainer = document.getElementById('conversation-history');
            if (!chatContainer) {
                // If we are NOT on a chat page (e.g., we're on /channel),
                // let the link perform its normal, full-page load.
                return;
            }

            // If we ARE on a chat page, prevent the reload and handle navigation with JS.
            event.preventDefault();

            const channelUrl = link.href;
            const channelName = new URL(channelUrl).pathname.split('/').pop();

            // 1. Immediately call the FAST API for channel details
            fetch(`/api/channel_details/${channelName}`)
                .then(response => {
                    if (!response.ok) throw new Error('Failed to load channel details.');
                    return response.json();
                })
                .then(data => {
                    // 2. As soon as we get details, update the page shell instantly
                    updateChannelShell(data);

                    // 3. Update the browser's URL bar
                    history.pushState({channel: channelName}, '', channelUrl);

                    // 4. NOW, start the SLOW fetch for the chat history
                    return fetch(`/api/chat_history/${channelName}`);
                })
                .then(response => {
                    if (!response.ok) throw new Error('Failed to load chat history.');
                    return response.json();
                })
                .then(data => {
                    // 5. When the history arrives, render it into the page
                    renderChatHistory(data.history);
                })
                .catch(error => {
                    console.error('Error loading channel:', error);
                    if (chatContainer) {
                        chatContainer.innerHTML = `<p class="error-message">Could not load channel data.</p>`;
                    }
                });
        });
    }
    
    // Run the function to attach the event listener
    initializeSpaNavigation();

    // --- THIS IS THE NEWLY ADDED BLOCK ---
    // This part handles resuming an action after a user logs in
    if (typeof IS_USER_LOGGED_IN !== 'undefined' && IS_USER_LOGGED_IN) {
        const pendingActionJSON = localStorage.getItem('action_after_login');
        if (pendingActionJSON) {
            const pendingAction = JSON.parse(pendingActionJSON);
            localStorage.removeItem('action_after_login'); // Clear the action

            // Check if the pending action was to buy a subscription
            if (pendingAction.type === 'buy_subscription' && pendingAction.planId) {
                // If so, call the buySubscription function immediately
                buySubscription(pendingAction.planId);
            }
            // You can add more 'else if' blocks here for other actions in the future
        }
    }
    // --- END OF NEW BLOCK ---
});

/**
 * Instantly updates the main UI shell and shows a loading state.
 */
function updateChannelShell(data) {
    const { current_channel } = data;

    const escapeHTML = (str) => {
        const p = document.createElement('p');
        p.textContent = str || '';
        return p.innerHTML;
    };

    // Update Mobile Header
    const mobileHeader = document.querySelector('.mobile-header-channel');
    if (mobileHeader) {
        mobileHeader.innerHTML = `
            <img src="${escapeHTML(current_channel.channel_thumbnail)}" alt="${escapeHTML(current_channel.channel_name)}" class="mobile-channel-avatar">
            <div class="mobile-channel-text-details">
                <span class="mobile-channel-title">${escapeHTML(current_channel.channel_name)}</span>
            </div>`;
    }

    // Update Desktop Right Sidebar
    const desktopSidebar = document.querySelector('.chat-sidebar-right');
    if (desktopSidebar) {
        const topicsHTML = current_channel.topics && current_channel.topics.length > 0 
            ? `<div class="profile-topics"><h3 class="topics-title">Popular Topics</h3><div class="topics-tags">${current_channel.topics.map(topic => `<span class="tag">${escapeHTML(topic)}</span>`).join('')}</div></div>`
            : '';

        // This re-creates the action buttons for the newly selected channel
        const actionsHTML = `
            <div class="profile-actions">
                <a href="#" onclick="refreshChannel('${current_channel.id}', this); return false;" class="action-btn">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/></svg>
                    <span>Refresh</span>
                </a>
                <a href="#" onclick="clearChat('${escapeHTML(current_channel.channel_name)}'); return false;" class="action-btn">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18m-2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    <span>Clear Chat</span>
                </a>
                <a href="/channel/${current_channel.id}/connect_group" class="action-btn">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
                    <span>Telegram</span>
                </a>
                <a href="${escapeHTML(current_channel.channel_url) || '#'}" target="_blank" rel="noopener noreferrer" class="action-btn">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.46a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .46 5.33A2.78 2.78 0 0 0 3.4 19c1.72.46 8.6.46 8.6.46s6.88 0 8.6-.46a2.78 2.78 0 0 0 1.94-2A29 29 0 0 0 23 11.75a29 29 0 0 0-.46-5.33z"></path><polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02"></polygon></svg>
                    <span>YouTube</span>
                </a>
            </div>
        `;

        desktopSidebar.innerHTML = `
            <div class="channel-profile-card">
                <div class="profile-header">
                    <div class="profile-avatar"><img src="${escapeHTML(current_channel.channel_thumbnail)}" alt="${escapeHTML(current_channel.channel_name)}"><span class="ai-badge">AI</span></div>
                    <div class="profile-info"><h2 class="profile-name">${escapeHTML(current_channel.channel_name)}</h2><p class="profile-description">${escapeHTML(current_channel.summary) || ''}</p></div>
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

    // Show loading indicator in chat
    const chatContainer = document.getElementById('conversation-history');
    if (chatContainer) {
        chatContainer.innerHTML = `
    <div class="qna-pair">
        <div class="question-box skeleton" style="width: 60%;"><div class="skeleton-line"></div></div>
        <div class="answer-box">
            <div class="answer-header">
                <div class="skeleton-avatar skeleton"></div>
                <div class="skeleton-line skeleton" style="width: 100px;"></div>
            </div>
            <div class="skeleton-line skeleton" style="width: 90%;"></div>
            <div class="skeleton-line skeleton" style="width: 80%; margin-top: 8px;"></div>
        </div>
    </div>
`;}

    const pageDataContainer = document.getElementById('chat-page-data');
    if (pageDataContainer) {
        pageDataContainer.dataset.channelName = current_channel.channel_name;
        pageDataContainer.dataset.channelThumbnail = current_channel.channel_thumbnail;
    }
}

/**
 * Renders the chat bubbles into the container.
 */
function renderChatHistory(history) {
    const chatContainer = document.getElementById('conversation-history');
    const pageData = document.getElementById('chat-page-data').dataset;
    const channelName = pageData.channelName;
    const channelThumbnail = pageData.channelThumbnail;

    if (!chatContainer) return;
    chatContainer.innerHTML = ''; // Clear previous history

    if (history && Array.isArray(history) && history.length > 0) {
        history.forEach((qa, index) => {
            const isLast = index === history.length - 1;
            const qnaPair = document.createElement('div');
            qnaPair.className = 'qna-pair';

            // --- Construct the full answer box HTML ---
            const avatarHtml = channelThumbnail
                ? `<div class="answer-avatar-container avatar-container"><img src="${channelThumbnail}" alt="${channelName}" class="answer-avatar"><span class="ai-badge">AI</span></div>`
                : `<div class="answer-avatar-container avatar-container"><div class="answer-avatar-placeholder">🤖</div><span class="ai-badge">AI</span></div>`;
            
            const answerLabel = channelName ? `${escapeHtml(channelName)}` : 'Answer';

            let sourcesHtml = '';
            if (qa.sources && qa.sources.length > 0) {
                const sourceLinks = qa.sources.map(s => `<div class="source-item"><a href="${escapeHtml(s.url)}" target="_blank" class="source-link"><span class="source-title">${escapeHtml(s.title)}</span></a></div>`).join('');
                sourcesHtml = `
                    <button class="toggle-sources-btn" onclick="toggleSources(this)">
                        <svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M4,6H2V20a2,2 0 0,0 2,2H18V18H4V6M20,2H8A2,2 0 0,0 6,4V16a2,2 0 0,0 2,2H20a2,2 0 0,0 2-2V4a2,2 0 0,0-2-2Z"></path></svg>
                        Sources (${qa.sources.length})
                        <span class="toggle-indicator">▼</span>
                    </button>
                    <div class="sources-list" style="display: none;">${sourceLinks}</div>
                `;
            }

            let regenerateHtml = '';
            if (isLast) {
                regenerateHtml = `
                    <button class="toggle-sources-btn regenerate-btn-js" onclick="regenerateAnswer(this)">
                        <svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
                        Regenerate
                    </button>
                `;
            }

            const copyButtonHtml = `
                <button class="copy-answer-btn" onclick="copyAnswer(this)" data-tooltip="Copy answer">
                    <svg class="icon-copy-default" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    <svg class="icon-copy-check" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                </button>
            `;

            qnaPair.innerHTML = `
                <div class="question-box"><div class="question-content">${escapeHtml(qa.question)}</div></div>
                <div class="answer-box">
                    <div class="answer-header">
                        ${avatarHtml}
                        <span class="answer-label">${answerLabel}</span>
                        ${copyButtonHtml}
                    </div>
                    <div class="answer-content">${window.marked ? window.marked.parse(qa.answer || '') : qa.answer}</div>
                    <div class="typing-container"><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>
                    <div class="sources-section">
                        <div class="source-buttons">${sourcesHtml}${regenerateHtml}</div>
                    </div>
                </div>
            `;
            chatContainer.appendChild(qnaPair);
        });
    } else {
        chatContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted);">No conversation history yet. Ask a question to get started!</p>`;
    }

    chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Helper function to escape HTML, needed by the function above
const escapeHtml = (text) => {
    if (typeof text !== 'string') return '';
    const p = document.createElement('p');
    p.textContent = text;
    return p.innerHTML;
};

function openShareModal(channelName) {
    const modal = document.getElementById('shareModal');
    const input = document.getElementById('shareLinkInput');
    if (modal && input && channelName) {
        // Construct the full, shareable URL
        const shareUrl = `${window.location.origin}/ask/channel/${encodeURIComponent(channelName)}`;
        input.value = shareUrl;
        modal.style.display = 'flex';
    }
}

function closeShareModal() {
    const modal = document.getElementById('shareModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function copyShareLink() {
    const input = document.getElementById('shareLinkInput');
    input.select();
    input.setSelectionRange(0, 99999); // For mobile device compatibility

    navigator.clipboard.writeText(input.value).then(() => {
        // Use the existing notification function from your app
        if (window.showNotification) {
            window.showNotification('Link copied to clipboard!', 'success');
        }
    }).catch(err => {
        if (window.showNotification) {
            window.showNotification('Failed to copy link.', 'error');
        }
        console.error('Failed to copy link: ', err);
    });
}
function copyShareLink(buttonEl) {
    // This function finds the <input> element that comes just before the button.
    const input = buttonEl.previousElementSibling;
    if (input && typeof input.select === 'function') {
        input.select();
        navigator.clipboard.writeText(input.value).then(() => {
            // Use the global showNotification function from base.html
            if (window.showNotification) {
                showNotification('Link copied to clipboard!', 'success');
            }
        }).catch(err => {
            console.error('Failed to copy text: ', err);
            if (window.showNotification) {
                showNotification('Failed to copy link.', 'error');
            }
        });
    } else {
        console.error('Could not find an input field to copy from.');
    }
}
function buySubscription(planId) {
    if (!IS_USER_LOGGED_IN) {
        localStorage.setItem('action_after_login', JSON.stringify({
            type: 'buy_subscription',
            planId: planId
        }));
        showLoginPopup();
        return;
    }

    fetch('/create_razorpay_subscription', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId })
    })
    .then(res => {
        if (!res.ok) {
            return res.json().then(err => Promise.reject(err));
        }
        return res.json();
    })
    .then(data => {
        if (data.status === 'success') {
            const options = {
                key: data.razorpay_key_id,
                subscription_id: data.subscription_id,
                name: 'YoppyChat AI',
                description: `Subscription for ${data.plan_name} plan`,
                handler: function (response) {
                    showNotification('Payment successful! Your plan has been updated.', 'success');
                    setTimeout(() => window.location.reload(), 2000);
                },
                prefill: {
                    name: data.user_name || "",
                    email: data.user_email || "",
                    method: "upi" // <-- Prioritizes the UPI payment method
                },
                theme: {
                    color: "#ff9a56"
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
    });
}