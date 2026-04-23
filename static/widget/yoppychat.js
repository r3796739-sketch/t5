/**
 * YoppyChat Website Embed Widget
 * This script creates a premium floating chat widget that can be embedded on any website.
 * Features: Light/Dark mode sensing, Glassmorphism, Responsive design.
 */

(function () {
    'use strict';

    // Base URL detection
    let scriptSrc = '';
    if (document.currentScript) {
        scriptSrc = document.currentScript.src;
    } else {
        const scripts = document.getElementsByTagName('script');
        scriptSrc = scripts[scripts.length - 1].src;
    }

    let detectedBaseUrl = '';
    try {
        if (scriptSrc && scriptSrc.startsWith('http')) {
            detectedBaseUrl = new URL(scriptSrc).origin;
        } else {
            detectedBaseUrl = window.location.origin;
        }
    } catch (e) {
        detectedBaseUrl = window.location.origin;
    }

    let BASE_URL = window.YOPPYCHAT_BASE_URL || detectedBaseUrl || 'https://yoppychat.com';

    // Widget state
    let isOpen = false;
    let isInitialized = false;
    let config = {
        channel: '',
        color: '#ff9a56',
        position: 'right',
        welcomeMessage: 'Hi! Ask me anything about this creator.',
        placeholder: 'Type your question...',
        avatar: '',
        name: 'AI Assistant',
        hideBranding: false,
        brandingText: ''
    };

    let conversationState = {};

    // ── localStorage helpers ──────────────────────────────────────────
    function getStorageKey() {
        return 'yoppychat_history_' + (config.channel || 'default');
    }

    function saveHistory() {
        try {
            const payload = {
                state: conversationState,
                messages: savedMessages,
                ts: Date.now()
            };
            localStorage.setItem(getStorageKey(), JSON.stringify(payload));
        } catch (e) { /* storage full or private mode */ }
    }

    function loadHistory() {
        try {
            const raw = localStorage.getItem(getStorageKey());
            if (!raw) return null;
            const payload = JSON.parse(raw);
            // Expire after 30 days
            if (Date.now() - payload.ts > 30 * 24 * 60 * 60 * 1000) {
                localStorage.removeItem(getStorageKey());
                return null;
            }
            return payload;
        } catch (e) { return null; }
    }

    function clearHistory() {
        savedMessages = [];
        conversationState = {};
        localStorage.removeItem(getStorageKey());
        const container = document.getElementById('yoppychat-messages');
        if (container) {
            container.innerHTML = `<div class="yoppychat-message bot">${config.welcomeMessage}</div>`;
        }
    }

    // In-memory mirror of messages for saving
    let savedMessages = [];
    // ─────────────────────────────────────────────────────────────────

    // Create widget elements
    function createWidget() {
        // Inject Inter font
        const fontLink = document.createElement('link');
        fontLink.rel = 'stylesheet';
        fontLink.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap';
        document.head.appendChild(fontLink);

        // Inject styles
        const styles = document.createElement('style');
        styles.textContent = `
            #yoppychat-widget-container {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                position: fixed;
                bottom: 24px;
                ${config.position === 'right' ? 'right: 24px;' : 'left: 24px;'}
                z-index: 999999;
                display: flex;
                flex-direction: column;
                align-items: ${config.position === 'right' ? 'flex-end' : 'flex-start'};
            }

            #yoppychat-button {
                width: 64px;
                height: 64px;
                border-radius: 32px;
                background: ${config.color};
                border: none;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
                transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                position: relative;
                overflow: hidden;
                padding: 0;
            }

            #yoppychat-button:hover {
                transform: scale(1.08) translateY(-2px);
                box-shadow: 0 12px 40px rgba(0, 0, 0, 0.2);
            }

            #yoppychat-button:active {
                transform: scale(0.95);
            }

            #yoppychat-button svg {
                width: 28px;
                height: 28px;
                fill: white;
                transition: transform 0.4s ease, opacity 0.3s ease;
            }

            #yoppychat-button .close-icon {
                display: none;
                position: absolute;
            }

            #yoppychat-button.open .chat-icon {
                transform: rotate(90deg) scale(0);
                opacity: 0;
            }

            #yoppychat-button.open .close-icon {
                display: block;
                transform: rotate(0deg) scale(1);
                opacity: 1;
            }

            /* Tooltip on hover */
            #yoppychat-tooltip {
                position: absolute;
                bottom: 80px;
                ${config.position === 'right' ? 'right: 0;' : 'left: 0;'}
                background: white;
                padding: 12px 20px;
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
                white-space: nowrap;
                font-size: 14px;
                font-weight: 600;
                color: #1a1a1a;
                pointer-events: none;
                opacity: 0;
                transform: translateY(10px);
                transition: all 0.3s ease;
            }

            #yoppychat-widget-container:hover #yoppychat-tooltip {
                opacity: ${isOpen ? '0' : '1'};
                transform: translateY(0);
            }

            #yoppychat-popup {
                position: absolute;
                bottom: 88px;
                ${config.position === 'right' ? 'right: 0;' : 'left: 0;'}
                width: 400px;
                max-width: calc(100vw - 48px);
                height: 600px;
                max-height: calc(100vh - 140px);
                background: rgba(255, 255, 255, 0.95);
                backdrop-filter: blur(10px);
                -webkit-backdrop-filter: blur(10px);
                border-radius: 24px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.05);
                display: none;
                flex-direction: column;
                overflow: hidden;
                transition: all 0.4s cubic-bezier(0.165, 0.84, 0.44, 1);
                transform-origin: bottom ${config.position};
                opacity: 0;
                transform: translateY(20px) scale(0.95);
            }

            #yoppychat-popup.open {
                display: flex;
                opacity: 1;
                transform: translateY(0) scale(1);
            }

            .yoppychat-header {
                background: linear-gradient(135deg, ${config.color} 0%, ${adjustColor(config.color, -20)} 100%);
                color: white;
                padding: 10px 10px;
                display: flex;
                align-items: center;
                gap: 16px;
                position: relative;
            }

            .yoppychat-header img {
                width: 48px;
                height: 48px;
                border-radius: 16px;
                object-fit: cover;
                background: rgba(255,255,255,0.2);
                border: 2px solid rgba(255,255,255,0.4);
            }

            .yoppychat-header-info {
                flex: 1;
            }

            .yoppychat-header-info h3 {
                margin: 0;
                font-size: 18px;
                font-weight: 700;
                letter-spacing: -0.01em;
            }

            .yoppychat-header-info p {
                margin: 4px 0 0 0;
                font-size: 13px;
                opacity: 0.9;
                font-weight: 500;
                display: flex;
                align-items: center;
                gap: 4px;
            }

            .yoppychat-header-info p::before {
                content: '';
                display: inline-block;
                width: 8px;
                height: 8px;
                background: #4ade80;
                border-radius: 50%;
                box-shadow: 0 0 8px #4ade80;
            }

            .yoppychat-messages {
                flex: 1;
                overflow-y: auto;
                padding: 24px;
                display: flex;
                flex-direction: column;
                gap: 16px;
                background: #fdfdfd;
                scrollbar-width: thin;
                scrollbar-color: rgba(0,0,0,0.1) transparent;
            }

            .yoppychat-messages::-webkit-scrollbar {
                width: 6px;
            }

            .yoppychat-messages::-webkit-scrollbar-thumb {
                background: rgba(0,0,0,0.1);
                border-radius: 3px;
            }

            .yoppychat-message {
                max-width: 85%;
                padding: 12px 16px;
                border-radius: 20px;
                line-height: 1.6;
                font-size: 14px;
                word-wrap: break-word;
                font-weight: 500;
                box-shadow: 0 2px 8px rgba(0,0,0,0.03);
            }

            .yoppychat-message.bot {
                align-self: flex-start;
                background: white;
                color: #2d3748;
                border: 1px solid #f1f5f9;
                border-bottom-left-radius: 4px;
            }

            .yoppychat-message.user {
                align-self: flex-end;
                background: ${config.color};
                color: white;
                border-bottom-right-radius: 4px;
                box-shadow: 0 4px 15px ${config.color}44;
            }

            .yoppychat-sources {
                margin-top: 8px;
                display: flex;
                flex-wrap: wrap;
                gap: 4px;
            }

            .yoppychat-source-pill {
                font-size: 11px;
                padding: 4px 8px;
                background: #f1f5f9;
                color: #64748b;
                border-radius: 6px;
                text-decoration: none;
                transition: all 0.2s;
                border: 1px solid #e2e8f0;
                max-width: 150px;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }

            .yoppychat-source-pill:hover {
                background: #e2e8f0;
                color: #1e293b;
            }

            .yoppychat-typing {
                align-self: flex-start;
                background: white;
                border: 1px solid #f1f5f9;
                padding: 12px 20px;
                border-radius: 20px;
                border-bottom-left-radius: 4px;
                display: none;
                align-items: center;
                gap: 4px;
            }

            .yoppychat-typing.active {
                display: flex;
            }

            .yoppychat-typing span {
                width: 6px;
                height: 6px;
                background: ${config.color};
                border-radius: 50%;
                opacity: 0.4;
                animation: yoppychat-bounce 1.4s infinite ease-in-out both;
            }

            .yoppychat-typing span:nth-child(1) { animation-delay: -0.32s; }
            .yoppychat-typing span:nth-child(2) { animation-delay: -0.16s; }

            @keyframes yoppychat-bounce {
                0%, 80%, 100% { transform: scale(0.6); opacity: 0.3; }
                40% { transform: scale(1.1); opacity: 1; }
            }

            .yoppychat-input-area {
                padding: 20px;
                border-top: 1px solid #f1f5f9;
                display: flex;
                gap: 12px;
                background: white;
                align-items: center;
            }

            .yoppychat-input-wrapper {
                flex: 1;
                position: relative;
            }

            .yoppychat-input-area input {
                width: 100%;
                padding: 14px 20px;
                border: 2px solid #f1f5f9;
                border-radius: 16px;
                font-size: 15px;
                outline: none;
                transition: all 0.2s ease;
                background: #f8fafc;
                box-sizing: border-box;
                font-family: inherit;
            }

            .yoppychat-input-area input:focus {
                border-color: ${config.color};
                background: white;
                box-shadow: 0 0 0 4px ${config.color}15;
            }

            .yoppychat-send-btn {
                width: 48px;
                height: 48px;
                border-radius: 14px;
                background: ${config.color};
                color: ${getIconColor(config.color)};
                border: none;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: all 0.2s ease;
                flex-shrink: 0;
            }

            .yoppychat-send-btn .yoppychat-send-icon {
                font-size: 22px;
                color: ${getIconColor(config.color)};
                user-select: none;
                line-height: 1;
                font-weight: 700;
            }

            .yoppychat-send-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 12px ${config.color}44;
            }

            .yoppychat-send-btn:active {
                transform: scale(0.9);
            }

            .yoppychat-send-btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }

            .yoppychat-footer {
                padding: 12px;
                text-align: center;
                background: #fdfdfd;
                border-top: 1px solid #f1f5f9;
            }

            .yoppychat-footer a {
                color: #94a3b8;
                text-decoration: none;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.05em;
                text-transform: uppercase;
            }

            .yoppychat-footer a span {
                color: ${config.color};
                font-weight: 700;
            }

            /* Mobile responsiveness */
            @media (max-width: 480px) {
                #yoppychat-popup {
                    width: calc(100vw - 32px);
                    height: calc(100vh - 120px);
                    bottom: 80px;
                    ${config.position === 'right' ? 'right: 16px;' : 'left: 16px;'}
                }
            }
        `;
        document.head.appendChild(styles);

        // Create container
        const container = document.createElement('div');
        container.id = 'yoppychat-widget-container';

        // Create floating button
        container.innerHTML = `
            <div id="yoppychat-tooltip">Chat with us!</div>
            <button id="yoppychat-button" aria-label="Open chat">
                <svg class="chat-icon" viewBox="0 0 24 24">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
                <svg class="close-icon" viewBox="0 0 24 24">
                    <path d="M18 6L6 18M6 6l12 12" stroke="white" stroke-width="2.5" stroke-linecap="round" fill="none"/>
                </svg>
            </button>
            <div id="yoppychat-popup">
                <div class="yoppychat-header">
                    <img id="yoppychat-avatar" src="${config.avatar || ''}" alt="Avatar" style="display: ${config.avatar ? 'block' : 'none'}">
                    <div class="yoppychat-header-info">
                        <h3 id="yoppychat-channel-name">${config.name}</h3>
                        <p>Online</p>
                    </div>
                    <button id="yoppychat-clear-btn" title="Clear chat history" style="background:rgba(255,255,255,0.18);border:none;border-radius:8px;width:32px;height:32px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:background 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.32)'" onmouseout="this.style.background='rgba(255,255,255,0.18)'">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                    </button>
                </div>
                <div class="yoppychat-messages" id="yoppychat-messages">
                    <div class="yoppychat-message bot">${config.welcomeMessage}</div>
                </div>
                <div class="yoppychat-typing" id="yoppychat-typing">
                    <span></span><span></span><span></span>
                </div>
                <div class="yoppychat-input-area">
                    <div class="yoppychat-input-wrapper">
                        <input type="text" id="yoppychat-input" placeholder="${config.placeholder}" maxlength="500">
                    </div>
                    <button id="yoppychat-send" class="yoppychat-send-btn" aria-label="Send message">
                        <span class="yoppychat-send-icon">➤</span>
                    </button>
                </div>
                <div class="yoppychat-footer" id="yoppychat-footer" style="display: ${config.hideBranding ? 'none' : 'block'}">
                    <a href="${BASE_URL}" target="_blank" rel="nofollow noopener noreferrer">${config.brandingText ? config.brandingText : 'Powered by <span>YoppyChat</span>'}</a>
                </div>
            </div>
        `;

        document.body.appendChild(container);

        // Add event listeners
        setupEventListeners();

        // Fetch channel info
        fetchChannelInfo();

        // Restore saved conversation
        restoreHistory();
    }

    function setupEventListeners() {
        const button = document.getElementById('yoppychat-button');
        const popup = document.getElementById('yoppychat-popup');
        const input = document.getElementById('yoppychat-input');
        const sendBtn = document.getElementById('yoppychat-send');

        // Toggle popup
        button.addEventListener('click', () => {
            isOpen = !isOpen;
            button.classList.toggle('open', isOpen);
            if (isOpen) {
                popup.style.display = 'flex';
                setTimeout(() => popup.classList.add('open'), 10);
                input.focus();
                trackEvent('widget_opened');
            } else {
                popup.classList.remove('open');
                setTimeout(() => { if (!isOpen) popup.style.display = 'none' }, 400);
            }
        });

        // Send message
        sendBtn.addEventListener('click', sendMessage);
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // Clear history button
        const clearBtn = document.getElementById('yoppychat-clear-btn');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                if (confirm('Clear your chat history with this assistant?')) {
                    clearHistory();
                }
            });
        }
    }

    function fetchChannelInfo() {
        if (!config.channel) return;

        fetch(`${BASE_URL}/api/widget/channel/${encodeURIComponent(config.channel)}`)
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    const avatar = document.getElementById('yoppychat-avatar');
                    const name = document.getElementById('yoppychat-channel-name');
                    if (data.thumbnail) {
                        avatar.src = data.thumbnail;
                        avatar.style.display = 'block';
                    }
                    if (data.name) {
                        name.textContent = data.name;
                    }
                }
            })
            .catch(err => {
                console.warn('YoppyChat: Could not fetch channel info', err);
            });
    }

    function restoreHistory() {
        const saved = loadHistory();
        if (!saved || !saved.messages || saved.messages.length === 0) return;

        // Restore conversation state
        conversationState = saved.state || {};
        savedMessages = saved.messages;

        // Clear default welcome bubble, replace with saved messages
        const container = document.getElementById('yoppychat-messages');
        if (!container) return;
        container.innerHTML = '';

        saved.messages.forEach(msg => {
            addMessageToUI(msg.text, msg.sender, msg.sources || [], msg.actions || []);
        });

        // Scroll to bottom
        container.scrollTop = container.scrollHeight;
    }

    function sendMessage() {
        const input = document.getElementById('yoppychat-input');
        const sendBtn = document.getElementById('yoppychat-send');
        const messagesContainer = document.getElementById('yoppychat-messages');
        const typing = document.getElementById('yoppychat-typing');

        const question = input.value.trim();
        if (!question) return;

        // Add user message
        addMessage(question, 'user');
        savedMessages.push({ text: question, sender: 'user', sources: [], actions: [] });
        input.value = '';
        sendBtn.disabled = true;

        // Show typing indicator
        typing.classList.add('active');
        messagesContainer.appendChild(typing);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;

        // Send to API
        fetch(`${BASE_URL}/api/widget/ask`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                channel: config.channel,
                question: question,
                referrer: window.location.hostname,
                conversation_state: conversationState
            })
        })
            .then(res => res.json())
            .then(data => {
                typing.classList.remove('active');
                sendBtn.disabled = false;

                if (data.success && (data.answer || (data.actions && data.actions.length > 0))) {
                    if (data.conversation_state) {
                        conversationState = data.conversation_state;
                    }
                    addMessage(data.answer || '', 'bot', data.sources, data.actions);
                    // Save bot reply to history
                    savedMessages.push({ text: data.answer || '', sender: 'bot', sources: data.sources || [], actions: data.actions || [] });
                    saveHistory();
                    trackEvent('question_asked');
                } else {
                    addMessage('Sorry, I couldn\'t process your question at this time. Please try again.', 'bot');
                }
            })
            .catch(err => {
                console.error('YoppyChat: Error sending message', err);
                typing.classList.remove('active');
                sendBtn.disabled = false;
                addMessage('Something went wrong. Please check your connection or try again later.', 'bot');
            });
    }

    // Adds a message to the UI AND saves to in-memory log
    function addMessage(text, sender, sources = [], actions = []) {
        addMessageToUI(text, sender, sources, actions);
    }

    // Pure UI render (used for both live and restored messages)
    function addMessageToUI(text, sender, sources = [], actions = []) {
        const messagesContainer = document.getElementById('yoppychat-messages');
        const messageWrapper = document.createElement('div');
        messageWrapper.style.display = 'flex';
        messageWrapper.style.flexDirection = 'column';
        messageWrapper.style.alignItems = sender === 'user' ? 'flex-end' : 'flex-start';
        messageWrapper.style.gap = '8px';

        let flowMatch;
        let flows = [];
        const flowRegex = /\[TRIGGER_FLOW:\s*"([^"]+)"\]/g;
        if (text) {
            while ((flowMatch = flowRegex.exec(text)) !== null) {
                flows.push(flowMatch[1]);
            }
        }

        let display_text = text ? text.replace(/\[TRIGGER_FLOW:\s*"([^"]+)"\]/g, '').trim() : '';

        if (display_text) {
            const message = document.createElement('div');
            message.className = `yoppychat-message ${sender}`;

            let finalHtml = display_text;
            if (sender === 'bot') {
                // 1. Links: [title](url)
                finalHtml = finalHtml.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
                    '<a href="$2" target="_blank" style="text-decoration: underline; font-weight: 600; color: inherit;">$1</a>');
                // 2. Bullets: * or - at start of line
                finalHtml = finalHtml.replace(/^\s*[\*\-]\s+(.*)$/gm,
                    '<span style="display:block; margin-left:12px; margin-bottom:4px;">• $1</span>');
                // 3. Bold
                finalHtml = finalHtml.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
                // 4. Italics
                finalHtml = finalHtml.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            }

            message.innerHTML = finalHtml.replace(/\n/g, '<br>');
            messageWrapper.appendChild(message);
        }

        // Add flow fallback buttons if backend didn't send actions
        if (flows.length > 0 && (!actions || actions.length === 0)) {
            actions = actions || [];
            flows.forEach(f => {
                actions.push({
                    type: 'buttons',
                    buttons: [{ id: f, title: f }]
                });
            });
        }

        // Add sources if available
        if (sources && sources.length > 0) {
            const sourcesDiv = document.createElement('div');
            sourcesDiv.className = 'yoppychat-sources';
            sources.forEach(source => {
                const pill = document.createElement('a');
                pill.className = 'yoppychat-source-pill';
                pill.href = source.url;
                pill.target = '_blank';
                pill.textContent = source.title;
                pill.title = source.title;
                sourcesDiv.appendChild(pill);
            });
            messageWrapper.appendChild(sourcesDiv);
        }

        // Render Actions (Buttons, Images, etc.)
        if (actions && actions.length > 0) {
            actions.forEach(action => {
                if (action.type === 'text') {
                    const textNode = document.createElement('div');
                    textNode.className = `yoppychat-message ${sender}`;
                    textNode.innerHTML = (action.text || '').replace(/\n/g, '<br>');
                    messageWrapper.appendChild(textNode);
                } else if (action.type === 'image') {
                    const imgNode = document.createElement('img');
                    imgNode.src = action.url;
                    imgNode.style.maxWidth = '100%';
                    imgNode.style.borderRadius = '12px';
                    imgNode.style.marginTop = '4px';
                    messageWrapper.appendChild(imgNode);
                    if (action.caption) {
                        const capNode = document.createElement('div');
                        capNode.style.fontSize = '12px';
                        capNode.style.color = '#64748b';
                        capNode.style.marginTop = '2px';
                        capNode.textContent = action.caption;
                        messageWrapper.appendChild(capNode);
                    }
                } else if ((action.type === 'buttons' || action.type === 'list') && (action.buttons || action.rows)) {
                    if (action.body) {
                        const textNode = document.createElement('div');
                        textNode.className = `yoppychat-message ${sender}`;
                        textNode.innerHTML = action.body.replace(/\n/g, '<br>');
                        messageWrapper.appendChild(textNode);
                    }
                    const btnsContainer = document.createElement('div');
                    btnsContainer.style.display = 'flex';
                    btnsContainer.style.flexWrap = 'wrap';
                    btnsContainer.style.gap = '8px';
                    btnsContainer.style.marginTop = '4px';

                    const btnItems = action.buttons || action.rows || [];
                    btnItems.forEach(btnInfo => {
                        const btn = document.createElement('button');
                        btn.textContent = btnInfo.title || btnInfo.label || btnInfo.id;
                        btn.style.padding = '8px 16px';
                        btn.style.borderRadius = '20px';
                        btn.style.border = '1px solid ' + config.color;
                        btn.style.background = 'white';
                        btn.style.color = config.color;
                        btn.style.cursor = 'pointer';
                        btn.style.fontSize = '14px';
                        btn.style.fontWeight = '600';
                        btn.style.transition = 'all 0.2s';

                        btn.onmouseover = () => {
                            btn.style.background = config.color;
                            btn.style.color = 'white';
                        };
                        btn.onmouseout = () => {
                            btn.style.background = 'white';
                            btn.style.color = config.color;
                        };

                        btn.onclick = () => {
                            const input = document.getElementById('yoppychat-input');
                            input.value = btn.textContent;
                            document.getElementById('yoppychat-send').click();
                            btnsContainer.style.opacity = '0.5';
                            btnsContainer.style.pointerEvents = 'none';
                        };
                        btnsContainer.appendChild(btn);
                    });
                    messageWrapper.appendChild(btnsContainer);
                } else if (action.type === 'cta_url' && action.cta_buttons) {
                    if (action.body) {
                        const textNode = document.createElement('div');
                        textNode.className = `yoppychat-message ${sender}`;
                        textNode.innerHTML = action.body.replace(/\n/g, '<br>');
                        messageWrapper.appendChild(textNode);
                    }
                    const btnsContainer = document.createElement('div');
                    btnsContainer.style.display = 'flex';
                    btnsContainer.style.flexWrap = 'wrap';
                    btnsContainer.style.gap = '8px';
                    btnsContainer.style.marginTop = '4px';

                    action.cta_buttons.forEach(btnInfo => {
                        const btn = document.createElement('a');
                        btn.textContent = btnInfo.text;
                        if (btnInfo.type === 'url') btn.href = btnInfo.url;
                        if (btnInfo.type === 'phone') btn.href = 'tel:' + btnInfo.phone;
                        btn.target = '_blank';
                        btn.style.padding = '8px 16px';
                        btn.style.borderRadius = '20px';
                        btn.style.background = config.color;
                        btn.style.color = 'white';
                        btn.style.textDecoration = 'none';
                        btn.style.fontSize = '14px';
                        btn.style.fontWeight = '600';
                        btn.style.display = 'inline-block';
                        btnsContainer.appendChild(btn);
                    });
                    messageWrapper.appendChild(btnsContainer);
                }
            });
        }

        messagesContainer.appendChild(messageWrapper);

        // Animate message entry
        messageWrapper.animate([
            { opacity: 0, transform: 'translateY(10px)' },
            { opacity: 1, transform: 'translateY(0)' }
        ], { duration: 300, easing: 'ease-out' });

        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function trackEvent(event) {
        try {
            fetch(`${BASE_URL}/api/widget/track`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    channel: config.channel,
                    event: event,
                    referrer: window.location.hostname,
                    url: window.location.href
                })
            }).catch(() => { });
        } catch (e) { }
    }

    // Helper: Decide icon color based on button background
    function getIconColor(hex) {
        try {
            if (!hex || typeof hex !== 'string' || hex[0] !== '#' || (hex.length !== 7 && hex.length !== 4)) {
                return '#ffffff';
            }
            let r, g, b;
            if (hex.length === 4) {
                r = parseInt(hex[1] + hex[1], 16);
                g = parseInt(hex[2] + hex[2], 16);
                b = parseInt(hex[3] + hex[3], 16);
            } else {
                r = parseInt(hex.slice(1, 3), 16);
                g = parseInt(hex.slice(3, 5), 16);
                b = parseInt(hex.slice(5, 7), 16);
            }
            const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            return luminance > 0.75 ? '#1f2937' : '#ffffff';
        } catch (e) {
            return '#ffffff';
        }
    }

    // Helper: Adjust color brightness
    function adjustColor(hex, percent) {
        try {
            let r = parseInt(hex.slice(1, 3), 16);
            let g = parseInt(hex.slice(3, 5), 16);
            let b = parseInt(hex.slice(5, 7), 16);

            r = Math.floor(r * (100 + percent) / 100);
            g = Math.floor(g * (100 + percent) / 100);
            b = Math.floor(b * (100 + percent) / 100);

            r = Math.min(255, Math.max(0, r));
            g = Math.min(255, Math.max(0, g));
            b = Math.min(255, Math.max(0, b));

            const rr = ((r.toString(16).length === 1) ? "0" + r.toString(16) : r.toString(16));
            const gg = ((g.toString(16).length === 1) ? "0" + g.toString(16) : g.toString(16));
            const bb = ((b.toString(16).length === 1) ? "0" + b.toString(16) : b.toString(16));

            return "#" + rr + gg + bb;
        } catch (e) { return hex; }
    }

    // Public API
    window.YoppyChat = {
        init: function (options) {
            if (isInitialized) return;
            if (!options.channel) {
                console.error('YoppyChat: Channel is required');
                return;
            }

            config = { ...config, ...options };
            if (options.baseUrl) {
                BASE_URL = options.baseUrl;
            }

            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', createWidget);
            } else {
                createWidget();
            }

            isInitialized = true;
            trackEvent('widget_loaded');
        },

        updateConfig: function (options) {
            config = { ...config, ...options };
            // Simple approach: re-create if already exists, or just update styles
            const container = document.getElementById('yoppychat-widget-container');
            if (container) {
                container.remove();
                isInitialized = false;
                this.init(config);
            }
        },

        destroy: function () {
            const container = document.getElementById('yoppychat-widget-container');
            if (container) container.remove();
            isInitialized = false;
        },

        open: function () {
            if (!isOpen) document.getElementById('yoppychat-button').click();
        },

        close: function () {
            if (isOpen) document.getElementById('yoppychat-button').click();
        }
    };
})();
