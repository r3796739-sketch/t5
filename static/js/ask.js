// =================================================================
// 1. GLOBALLY ACCESSIBLE FUNCTIONS
// =================================================================

/**
 * Helper function to safely escape HTML special characters from text.
 */
const escapeHtml = (text) => {
    if (typeof text !== 'string') return '';
    const p = document.createElement('p');
    p.textContent = text;
    return p.innerHTML;
};

/**
 * Copies the text content of an AI answer to the clipboard.
 * @param {HTMLElement} buttonElement - The copy button that was clicked.
 */
window.copyAnswer = function(buttonElement) {
    const answerBox = buttonElement.closest('.answer-box');
    if (!answerBox) return;

    const answerContent = answerBox.querySelector('.answer-content');
    if (!answerContent) return;

    const textToCopy = answerContent.innerText;

    navigator.clipboard.writeText(textToCopy).then(() => {
        const iconCopy = buttonElement.querySelector('.icon-copy-default');
        const iconCheck = buttonElement.querySelector('.icon-copy-check');
        
        if(iconCopy) iconCopy.style.display = 'none';
        if(iconCheck) iconCheck.style.display = 'inline-block';

        if (window.showNotification) {
            window.showNotification('Answer copied to clipboard!', 'success');
        }

        setTimeout(() => {
            if(iconCopy) iconCopy.style.display = 'inline-block';
            if(iconCheck) iconCheck.style.display = 'none';
        }, 2000);

    }).catch(err => {
        console.error('Failed to copy text: ', err);
        if (window.showNotification) {
            window.showNotification('Failed to copy text.', 'error');
        }
    });
}

/**
 * Displays a custom confirmation dialog.
 * @param {string} title - The dialog title.
 * @param {string} message - The confirmation message.
 * @param {string} confirmText - Text for the confirmation button.
 * @param {function} onConfirm - Callback function executed on confirmation.
 */
window.showCustomConfirm = function(title, message, confirmText, onConfirm) {
    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog';
    dialog.style.display = 'none';
    dialog.innerHTML = `
        <div class="confirm-content">
            <h3>${title}</h3>
            <p>${message}</p>
            <div class="confirm-actions">
                <button class="cancel-btn">Cancel</button>
                <button class="confirm-btn">${confirmText}</button>
            </div>
        </div>
    `;
    document.body.appendChild(dialog);
    dialog.style.display = 'flex';
    const confirmBtn = dialog.querySelector('.confirm-btn');
    const cancelBtn = dialog.querySelector('.cancel-btn');
    const closeDialog = () => document.body.removeChild(dialog);
    cancelBtn.onclick = closeDialog;
    confirmBtn.onclick = () => {
        onConfirm();
        closeDialog();
    };
}

/**
 * Initiates clearing the chat history for a specific channel.
 * @param {string} channel - The name of the channel ('general' or a specific name).
 */
window.clearChat = function(channel) {
    window.showCustomConfirm(
        'Clear Chat History',
        'Are you sure you want to clear this chat? This action cannot be undone.',
        'Clear',
        () => {
            const formData = new FormData();
            formData.append('channel_name', channel);
            fetch('/clear_chat', { method: 'POST', body: formData })
                .then(res => {
                    if (res.ok) {
                        window.location.reload();
                    } else {
                        return Promise.reject('Failed to clear chat.');
                    }
                })
                .catch(err => {
                    if (window.showNotification) {
                        window.showNotification(err.toString(), 'error');
                    }
                });
        }
    );
}

/**
 * Sets a channel as the default for the community.
 * @param {string} channelId - The ID of the channel to set as default.
 * @param {HTMLElement} buttonElement - The button that was clicked.
 */
window.setDefaultChannel = function(channelId, buttonElement) {
    if (!buttonElement || buttonElement.disabled) return;
    const originalContent = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = `<div class="button-spinner" style="width:16px;height:16px;border-width:2px;"></div><span>Setting...</span>`;
    fetch(`/set-default-channel/${channelId}`, { method: 'POST' })
        .then(res => {
            if (!res.ok) return res.json().then(err => Promise.reject(err));
            return res.json();
        })
        .then(data => {
            if (window.showNotification) {
                window.showNotification(data.message, 'success');
            }
            setTimeout(() => window.location.reload(), 1500);
        })
        .catch(err => {
            if (window.showNotification) {
                window.showNotification(err.message || 'An error occurred.', 'error');
            }
            buttonElement.disabled = false;
            buttonElement.innerHTML = originalContent;
        });
}

/**
 * Toggles a channel's privacy between personal and shared for community admins.
 * @param {string} channelId - The ID of the channel to toggle.
 * @param {boolean} isShared - The new desired state (true for shared, false for personal).
 */
window.toggleChannelPrivacy = function(channelId, isShared) {
    const toggle = document.getElementById('shareToggle');
    if(toggle) toggle.disabled = true;
    fetch(`/api/toggle_channel_privacy/${channelId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(res => {
        if (!res.ok) {
            return res.json().then(err => Promise.reject(err));
        }
        return res.json();
    })
    .then(data => {
        if (window.showNotification) {
            window.showNotification(data.message, 'success');
        }
        setTimeout(() => window.location.reload(), 1500);
    })
    .catch(err => {
        if (window.showNotification) {
            window.showNotification(err.message || 'An error occurred.', 'error');
        }
        if(toggle) toggle.checked = !isShared;
    })
    .finally(() => {
        if(toggle && !document.querySelector('.notification.success')) {
            toggle.disabled = false;
        }
    });
}

/**
 * Toggles the visibility of the sources list associated with a button.
 * @param {HTMLElement} btn - The button element that was clicked.
 */
window.toggleSources = function(btn) {
    const sourcesSection = btn.closest('.sources-section');
    if (!sourcesSection) return;
    const list = sourcesSection.querySelector('.sources-list');
    if (!list) return;
    const indicator = btn.querySelector('.toggle-indicator');
    const isExpanded = list.style.display === 'block';
    list.style.display = isExpanded ? 'none' : 'block';
    if (indicator) {
        indicator.style.transform = isExpanded ? 'rotate(0deg)' : 'rotate(180deg)';
    }
}

/**
 * Initiates a background task to refresh a channel's content.
 * @param {string} channelId - The ID of the channel to refresh.
 * @param {HTMLElement} buttonElement - The button that triggered the refresh.
 */
window.refreshChannel = function(channelId, buttonElement) {
    if (!buttonElement || buttonElement.disabled) return;
    const originalContent = buttonElement.innerHTML;
    buttonElement.disabled = true;
    const spinnerSize = buttonElement.classList.contains('action-btn') ? '20px' : '16px';
    buttonElement.innerHTML = `<div class="button-spinner" style="width:${spinnerSize};height:${spinnerSize};border-width:2px;"></div><span>Refreshing...</span>`;
    const dropdownMenuMobile = document.getElementById('dropdown-menu-mobile');
    if (dropdownMenuMobile?.classList.contains('show')) {
        dropdownMenuMobile.classList.remove('show');
    }
    fetch(`/refresh_channel/${channelId}`, { method: 'POST' })
        .then(response => response.ok ? response.json() : response.json().then(err => Promise.reject(err)))
        .then(data => {
            if (data.status === 'success' && data.task_id) {
                checkTaskStatus(data.task_id, buttonElement, originalContent);
            } else {
                throw new Error(data.message || 'Failed to start refresh task.');
            }
        })
        .catch(error => {
            if (window.showNotification) {
                window.showNotification(error.message || 'Refresh failed to start.', 'error');
            }
            restoreButton(buttonElement, originalContent, 'state-failure');
        });
}

/**
 * Polls the backend to check the status of a background task.
 * @param {string} taskId - The ID of the task to check.
 * @param {HTMLElement} buttonElement - The button associated with the task.
 * @param {string} originalContent - The original HTML content of the button.
 */
function checkTaskStatus(taskId, buttonElement, originalContent) {
    const interval = setInterval(() => {
        fetch(`/task_result/${taskId}`)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'complete' || data.status === 'failed') {
                    clearInterval(interval);
                    if (window.showNotification && data.message) {
                        const messageType = data.status === 'complete' ? 'success' : 'error';
                        window.showNotification(data.message, messageType);
                    }
                    const finalStateClass = data.status === 'complete' ? 'state-success' : 'state-failure';
                    restoreButton(buttonElement, originalContent, finalStateClass);
                    if (data.status === 'complete' && data.message?.toLowerCase().includes('success')) {
                        setTimeout(() => window.location.reload(), 1500);
                    }
                }
            })
            .catch(error => {
                clearInterval(interval);
                if (window.showNotification) window.showNotification('Error checking task status.', 'error');
                restoreButton(buttonElement, originalContent, 'state-failure');
            });
    }, 3000);
}

/**
 * Restores a button to its original state after an async operation.
 * @param {HTMLElement} button - The button to restore.
 * @param {string} originalHTML - The original inner HTML of the button.
 * @param {string} finalStateClass - A class to add temporarily (e.g., 'state-success').
 */
function restoreButton(button, originalHTML, finalStateClass) {
    if (!button) return;
    button.innerHTML = originalHTML;
    button.classList.remove('state-loading');
    if (finalStateClass) button.classList.add(finalStateClass);
    setTimeout(() => {
        button.disabled = false;
        if (finalStateClass) button.classList.remove(finalStateClass);
    }, 3000);
}

let shareHistoryId = null;

/**
 * Closes the share modal.
 */
function closeShareModal() {
    const modal = document.getElementById('shareModal');
    if (modal) modal.style.display = 'none';
}

/**
 * Opens and prepares the new share modal.
 */
function openShareModal() {
    const channelName = document.getElementById('chat-page-data').dataset.channelName;
    if (!channelName) {
        if(window.showNotification) showNotification('Cannot share from this page.', 'error');
        return;
    }
    const modal = document.getElementById('shareModal');
    const input = document.getElementById('shareLinkInput');
    const toggle = document.getElementById('includeHistoryToggle');
    const wrapper = document.getElementById('linkCopyWrapper');
    const subtitle = document.getElementById('shareModalSubtitle');

    if (!modal || !input || !toggle || !wrapper || !subtitle) return;

    const baseUrl = `${window.location.origin}/c/${encodeURIComponent(channelName)}`;
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

/**
 * Copies the share link and provides instant visual feedback inside the modal.
 * @param {HTMLElement} wrapperElement - The link-copy-wrapper element that was clicked.
 */
window.copyShareLinkFromModal = function(wrapperElement) {
    const input = wrapperElement.querySelector('#shareLinkInput');
    const copyText = wrapperElement.querySelector('.copy-text');
    const iconDefault = wrapperElement.querySelector('.icon-copy-default');
    const iconCheck = wrapperElement.querySelector('.icon-copy-check');

    if (!input || !copyText || !iconDefault || !iconCheck) return;

    if (wrapperElement.classList.contains('copied')) return;

    input.select();
    navigator.clipboard.writeText(input.value).then(() => {
        wrapperElement.classList.add('copied');
        copyText.textContent = 'Copied!';
        iconDefault.style.display = 'none';
        iconCheck.style.display = 'block';

        setTimeout(() => {
            copyText.textContent = 'Copy';
            iconDefault.style.display = 'block';
            iconCheck.style.display = 'none';
            wrapperElement.classList.remove('copied');
        }, 2000);

    }).catch(err => {
        console.error('Failed to copy text: ', err);
        if (window.showNotification) {
            window.showNotification('Failed to copy link.', 'error');
        }
    });
}

function askExample(element) {
    const questionText = document.getElementById('questionText');
    const questionForm = document.getElementById('questionForm');
    if (questionText && questionForm) {
        questionText.value = element.textContent;
        questionText.dispatchEvent(new Event('input', { bubbles: true }));
        questionForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    }
}

/**
 * Takes a topic from a tag, formats it as a question, and submits it.
 * @param {HTMLElement} element - The topic tag that was clicked.
 */
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
// 2. SCRIPT INITIALIZATION
// =================================================================
document.addEventListener('DOMContentLoaded', function() {

    const questionForm = document.getElementById('questionForm');
    const questionText = document.getElementById('questionText');
    const submitBtn = document.getElementById('submitBtn');
    const conversationHistory = document.getElementById('conversation-history');
    const charCount = document.querySelector('.char-count');
    const includeHistoryToggle = document.getElementById('includeHistoryToggle');
    const shareLinkInput = document.getElementById('shareLinkInput');
    const shareModal = document.getElementById('shareModal'); // Define shareModal here

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
        const newHeight = Math.min(questionText.scrollHeight, 120);
        questionText.style.height = `${newHeight}px`;
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
            const avatarHtml = channelThumbnail
                ? `<div class="answer-avatar-container avatar-container"><img src="${channelThumbnail}" alt="Avatar" class="answer-avatar"><span class="ai-badge">AI</span></div>`
                : `<div class="answer-avatar-container avatar-container"><div class="answer-avatar-placeholder">ðŸ¤–</div><span class="ai-badge">AI</span></div>`;
            const label = channelName ? `${escapeHtml(channelName)}` : 'Answer';
            html = `
                <div class="answer-box">
                    <div class="answer-header">
                        ${avatarHtml}
                        <span class="answer-label">${label}</span>
                    </div>
                    <div class="answer-content"></div>
                    <div class="typing-container"><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>
                    <div class="sources-section"></div>
                </div>
            `;
        }

        qnaPair.innerHTML = html;
        conversationHistory.appendChild(qnaPair);
        conversationHistory.scrollTop = conversationHistory.scrollHeight;
        return role === 'ai' ? qnaPair.querySelector('.answer-box') : null;
    };

    // 1. First, fix the renderSources function to include the copy button
    const renderSources = (sources, answerBoxElement) => {
        if (!answerBoxElement) return;
        let sourcesSection = answerBoxElement.querySelector('.sources-section');
        if (!sourcesSection) return;
        
        sourcesSection.innerHTML = '';
        
        let sourcesButtonHTML = '';
        let listHTML = '';

        if (sources && sources.length > 0) {
            sourcesButtonHTML = `
                <button class="toggle-sources-btn" onclick="toggleSources(this)">
                    <svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M4,6H2V20a2,2 0 0,0 2,2H18V18H4V6M20,2H8A2,2 0 0,0 6,4V16a2,2 0 0,0 2,2H20a2,2 0 0,0 2-2V4a2,2 0 0,0-2-2Z"></path></svg>
                    Sources (${sources.length})
                    <span class="toggle-indicator">â–¼</span>
                </button>
            `;
            const sourceLinks = sources.map(s => `<div class="source-item"><a href="${escapeHtml(s.url)}" target="_blank" class="source-link"><span class="source-title">${escapeHtml(s.title)}</span></a></div>`).join('');
            listHTML = `<div class="sources-list" style="display: none;">${sourceLinks}</div>`;
        }
        
        const regenerateButtonHTML = `
            <button class="toggle-sources-btn regenerate-btn-js" onclick="regenerateAnswer(this)">
                <svg class="sources-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
                Regenerate
            </button>`;
        
        const copyButtonHTML = `
            <button class="copy-answer-btn" onclick="copyAnswer(this)" data-tooltip="Copy answer">
                <svg class="icon-copy-default" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <svg class="icon-copy-check" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
            </button>`;
            
        sourcesSection.innerHTML = `<div class="source-buttons">${sourcesButtonHTML}${regenerateButtonHTML}${copyButtonHTML}</div>${listHTML}`;
        
        // Also ensure copy button exists in header if it doesn't
        const answerHeader = answerBoxElement.querySelector('.answer-header');
        if (answerHeader && !answerHeader.querySelector('.copy-answer-btn')) {
            answerHeader.insertAdjacentHTML('beforeend', copyButtonHTML);
        }
    };

    const handleFormSubmit = (event) => {
        event.preventDefault();

        if (typeof IS_USER_LOGGED_IN !== 'undefined' && !IS_USER_LOGGED_IN) {
            const question = questionText.value.trim();
            if (question) {
                localStorage.setItem('pending_question', question);
            }
            showLoginPopup();
            return;
        }

        const pageData = document.getElementById('chat-page-data').dataset;
        const channelName = pageData.channelName;
        document.querySelectorAll('.regenerate-btn-js').forEach(btn => btn.remove());
        const question = questionText.value.trim();
        if (!question) return;
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
        formData.append('channel_name', channelName);
        formData.append('is_regenerating', 'true');
        fetch('/stream_answer', { method: 'POST', body: formData })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => Promise.reject(err));
                }
                return response.body.getReader();
            })
            .then(reader => {
                if (reader) processStream(reader, answerContent, typingContainer, aiAnswerBox);
            })
            .catch(err => {
                console.error('Fetch error:', err);
                if (err && err.status === 'limit_reached') {
                    if (window.showUpgradePopup) {
                        window.showUpgradePopup(err.message);
                    } else {
                        answerContent.innerHTML = `<p class="error-message">${escapeHtml(err.message)}</p>`;
                    }
                } else {
                    const message = err.message || 'Failed to send question. Please try again.';
                    answerContent.innerHTML = `<p class="error-message">${escapeHtml(message)}</p>`;
                }
                typingContainer.classList.remove('active');
            });
    };

    const processStream = (reader, answerContent, typingContainer, aiAnswerBox) => {
        const decoder = new TextDecoder();
        let fullAnswerText = '';
        let foundSources = [];

        const push = () => {
            reader.read().then(({ done, value }) => {
                // If the stream is finished, hide the typing indicator and render the final sources and regenerate button.
                if (done) {
                    if (typingContainer) typingContainer.classList.remove('active');
                    renderSources(foundSources, aiAnswerBox);
                    return;
                }

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n\n');

                lines.forEach(line => {
                    if (line.startsWith('data: ')) {
                        const data = line.substring(6);
                        if (data.trim() === '[DONE]') return;
                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.error) {
                                throw new Error(parsed.message || 'An unknown error occurred.');
                            }
                            if (parsed.answer) {
                                fullAnswerText += parsed.answer;
                                answerContent.innerHTML = marked.parse(fullAnswerText);
                            }
                            if (parsed.sources) {
                                foundSources = parsed.sources;
                            }
                            if (parsed.updated_query_string) {
                                const planQueriesElement = document.querySelector('.plan-queries');
                                if (planQueriesElement) {
                                    planQueriesElement.innerHTML = parsed.updated_query_string;
                                }
                            }
                        } catch (e) {
                            console.error('Error parsing stream data:', e);
                            throw e; // Re-throw the error to be caught by the catch block below
                        }
                    }
                });

                conversationHistory.scrollTop = conversationHistory.scrollHeight;
                push(); // Continue reading the stream

            }).catch(error => {
                // This block will now handle any error that occurs during the stream.
                console.error("Stream reading error:", error);
                const errorMessage = error.message || 'An error occurred during generation.';
                answerContent.innerHTML = `<p class="error-message">${escapeHtml(errorMessage)}</p>`;

                // Crucially, we reset the UI here as well.
                if (typingContainer) typingContainer.classList.remove('active');
                renderSources([], aiAnswerBox); // Re-render the regenerate button

                // This cancels the reader to prevent it from getting stuck.
                reader.cancel(); 
            });
        };

        push();
    };

    // 2. Completely replace the regenerateAnswer function
    window.regenerateAnswer = function(btn) {
        console.log('=== REGENERATE START ===');
        
        if (btn.disabled) {
            console.log('Button already disabled, returning');
            return;
        }
        
        // Get references BEFORE changing button state
        const lastQnaPair = btn.closest('.qna-pair');
        const answerBox = btn.closest('.answer-box');
        
        console.log('QnA Pair found:', !!lastQnaPair);
        console.log('Answer Box found:', !!answerBox);
        
        if (!lastQnaPair || !answerBox) {
            console.log('Missing DOM elements, aborting');
            return;
        }
        
        const questionContent = lastQnaPair.querySelector('.question-box .question-content');
        const answerContent = answerBox.querySelector('.answer-content');
        const sourcesSection = answerBox.querySelector('.sources-section');
        const typingContainer = answerBox.querySelector('.typing-container');
        
        console.log('Question content found:', !!questionContent);
        console.log('Answer content found:', !!answerContent);
        console.log('Sources section found:', !!sourcesSection);
        console.log('Typing container found:', !!typingContainer);
        
        if (!questionContent || !answerContent) {
            console.log('Missing essential DOM elements, aborting');
            return;
        }
        
        const lastQuestion = questionContent.textContent;
        console.log('Question to regenerate:', lastQuestion);
        
        // NOW set button state
        btn.disabled = true;
        btn.innerHTML = 'Regenerating...';
        console.log('Button state set to regenerating');

        // Remove other regenerate buttons
        document.querySelectorAll('.regenerate-btn-js').forEach(b => {
            if (b !== btn) b.remove();
        });

        // Clear content and show typing
        answerContent.innerHTML = '';
        if (sourcesSection) sourcesSection.innerHTML = '';
        if (typingContainer) typingContainer.classList.add('active');

        const formData = new FormData();
        formData.append('question', lastQuestion);
        formData.append('channel_name', document.getElementById('chat-page-data').dataset.channelName || '');
        formData.append('is_regenerating', 'true');

        console.log('Making fetch request...');

        fetch('/stream_answer', { method: 'POST', body: formData })
            .then(response => {
                console.log('Fetch response status:', response.status);
                if (!response.ok) {
                    return response.json().then(err => Promise.reject(err));
                }
                return response.body.getReader();
            })
            .then(reader => {
                console.log('Reader obtained, starting stream...');
                processRegenerateStream(reader, answerContent, typingContainer, answerBox);
            })
            .catch(err => {
                console.error('Fetch failed:', err);
                answerContent.innerHTML = `<p class="error-message">Failed to regenerate: ${err.message || 'Unknown error'}</p>`;
                if (typingContainer) typingContainer.classList.remove('active');
                renderSources([], answerBox);
            });
    };

    // 3. Stream processing function
    function processRegenerateStream(reader, answerContent, typingContainer, aiAnswerBox) {
        console.log('Stream processing started');
        
        const decoder = new TextDecoder();
        let fullAnswerText = '';
        let foundSources = [];

        const push = () => {
            reader.read().then(({ done, value }) => {
                if (done) {
                    console.log('Stream completed');
                    if (typingContainer) typingContainer.classList.remove('active');
                    renderSources(foundSources, aiAnswerBox);
                    return;
                }

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n\n');

                lines.forEach(line => {
                    if (line.startsWith('data: ')) {
                        const data = line.substring(6);
                        if (data.trim() === '[DONE]') return;
                        
                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.error) {
                                throw new Error(parsed.message || 'Server error');
                            }
                            if (parsed.answer) {
                                fullAnswerText += parsed.answer;
                                answerContent.innerHTML = window.marked ? 
                                    marked.parse(fullAnswerText) : fullAnswerText;
                            }
                            if (parsed.sources) {
                                foundSources = parsed.sources;
                            }
                        } catch (e) {
                            console.error('Error parsing stream data:', e);
                            throw e;
                        }
                    }
                });

                // Scroll to bottom
                const conversationHistory = document.getElementById('conversation-history');
                if (conversationHistory) {
                    conversationHistory.scrollTop = conversationHistory.scrollHeight;
                }

                push();

            }).catch(error => {
                console.error('Stream error:', error);
                answerContent.innerHTML = `<p class="error-message">Stream error: ${error.message}</p>`;
                if (typingContainer) typingContainer.classList.remove('active');
                renderSources([], aiAnswerBox);
            });
        };

        push();
    }

    if (questionForm) {
        questionForm.addEventListener('submit', handleFormSubmit);
    }

    if (questionText) {
        questionText.addEventListener('input', () => {
            const hasText = questionText.value.trim().length > 0;
            submitBtn.disabled = !hasText;
            submitBtn.classList.toggle('active', hasText);
            adjustTextareaHeight();
            updateCharCount();
        });
        questionText.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                if (!submitBtn.disabled) {
                    questionForm.requestSubmit();
                }
            }
        });
    }

    const setupKebabMenu = (btn, menu) => {
        if (btn && menu) {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                menu.classList.toggle('show');
            });
        }
    };
    setupKebabMenu(document.getElementById('kebab-menu-btn-mobile'), document.getElementById('dropdown-menu-mobile'));

    window.addEventListener('click', () => {
        document.getElementById('dropdown-menu-mobile')?.classList.remove('show');
    });

    const hamburgerBtnMobile = document.getElementById('hamburgerBtnMobile');
    const mainHamburger = document.getElementById('hamburgerBtn');
    if (hamburgerBtnMobile && mainHamburger) {
        hamburgerBtnMobile.addEventListener('click', () => mainHamburger.click());
    }

    if (includeHistoryToggle && shareLinkInput) {
        const subtitle = document.getElementById('shareModalSubtitle'); 

        includeHistoryToggle.addEventListener('change', async (event) => {
            const channelName = document.getElementById('chat-page-data').dataset.channelName;
            const baseUrl = `${window.location.origin}/c/${encodeURIComponent(channelName)}`;
            
            if (subtitle) {
                if (event.target.checked) {
                    subtitle.textContent = 'Anyone with the link can view this chat.';
                } else {
                    subtitle.textContent = 'Share a link to this AI assistant.';
                }
            }

            if (event.target.checked) {
                const qnaPairs = Array.from(document.querySelectorAll('#conversation-history .qna-pair'));
                const history = qnaPairs.map(pair => {
                    const question = pair.querySelector('.question-content')?.textContent || '';
                    const answer = pair.querySelector('.answer-content')?.innerHTML || '';
                    const sources = Array.from(pair.querySelectorAll('.source-link')).map(s => ({ title: s.textContent, url: s.href }));
                    return { question, answer, sources };
                }).filter(qa => qa.question && qa.answer);

                if (history.length === 0) {
                    showNotification('There is no chat history to share.', 'info');
                    event.target.checked = false;
                    if (subtitle) {
                        subtitle.textContent = 'Share a link to this AI assistant.';
                    }
                    return;
                }

                try {
                    const response = await fetch('/api/share_chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ history })
                    });
                    const data = await response.json();
                    if (data.status === 'success') {
                        shareHistoryId = data.history_id;
                        shareLinkInput.value = `${baseUrl}?history_id=${shareHistoryId}`;
                    } else {
                        throw new Error(data.message);
                    }
                } catch (error) {
                    showNotification(error.message || 'Could not create share link.', 'error');
                    event.target.checked = false;
                    if (subtitle) {
                        subtitle.textContent = 'Share a link to this AI assistant.';
                    }
                }
            } else {
                shareLinkInput.value = baseUrl;
                shareHistoryId = null;
            }
        });
    }

    // --- START: New "Click Outside to Close" Logic for Share Modal ---
    if (shareModal) {
        shareModal.addEventListener('click', function(event) {
            // Check if the clicked element is the overlay itself (the parent)
            // and not the content area (which has a class of .login-popup-content).
            if (event.target === shareModal) {
                closeShareModal();
            }
        });
    }
    // --- END: New Logic ---

    const init = () => {
        if (questionText) {
            questionText.focus();
            adjustTextareaHeight();
            updateCharCount();
        }
        if (conversationHistory) {
            conversationHistory.scrollTop = conversationHistory.scrollHeight;
        }
    };
    init();
});