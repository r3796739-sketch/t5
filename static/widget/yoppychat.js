/**
 * YoppyChat Website Embed Widget
 * This script creates a floating chat widget that can be embedded on any website.
 * Usage:
 *   <script src="https://yoppychat.com/widget/yoppychat.js"></script>
 *   <script>
 *     YoppyChat.init({
 *       channel: 'channel-name',
 *       color: '#ff9a56',
 *       position: 'right'
 *     });
 *   </script>
 */

(function () {
    'use strict';

    // Base URL for the YoppyChat API
    const BASE_URL = window.YOPPYCHAT_BASE_URL || 'https://yoppychat.com';

    // Widget state
    let isOpen = false;
    let isInitialized = false;
    let config = {
        channel: '',
        color: '#ff9a56',
        position: 'right',
        welcomeMessage: 'Hi! Ask me anything about this creator.',
        placeholder: 'Type your question...'
    };

    // Create widget elements
    function createWidget() {
        // Inject styles
        const styles = document.createElement('style');
        styles.textContent = `
            #yoppychat-widget-container {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                position: fixed;
                bottom: 20px;
                ${config.position === 'right' ? 'right: 20px;' : 'left: 20px;'}
                z-index: 999999;
            }

            #yoppychat-button {
                width: 60px;
                height: 60px;
                border-radius: 50%;
                background: ${config.color};
                border: none;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
                transition: transform 0.3s, box-shadow 0.3s;
            }

            #yoppychat-button:hover {
                transform: scale(1.1);
                box-shadow: 0 6px 30px rgba(0, 0, 0, 0.3);
            }

            #yoppychat-button svg {
                width: 28px;
                height: 28px;
                fill: white;
            }

            #yoppychat-button .close-icon {
                display: none;
            }

            #yoppychat-button.open .chat-icon {
                display: none;
            }

            #yoppychat-button.open .close-icon {
                display: block;
            }

            #yoppychat-popup {
                position: absolute;
                bottom: 80px;
                ${config.position === 'right' ? 'right: 0;' : 'left: 0;'}
                width: 380px;
                max-width: calc(100vw - 40px);
                height: 500px;
                max-height: calc(100vh - 150px);
                background: #ffffff;
                border-radius: 16px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
                display: none;
                flex-direction: column;
                overflow: hidden;
                animation: yoppychat-slideIn 0.3s ease;
            }

            #yoppychat-popup.open {
                display: flex;
            }

            @keyframes yoppychat-slideIn {
                from {
                    opacity: 0;
                    transform: translateY(20px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }

            .yoppychat-header {
                background: ${config.color};
                color: white;
                padding: 16px 20px;
                display: flex;
                align-items: center;
                gap: 12px;
            }

            .yoppychat-header img {
                width: 40px;
                height: 40px;
                border-radius: 50%;
                object-fit: cover;
                border: 2px solid rgba(255,255,255,0.3);
            }

            .yoppychat-header-info h3 {
                margin: 0;
                font-size: 1rem;
                font-weight: 600;
            }

            .yoppychat-header-info p {
                margin: 2px 0 0 0;
                font-size: 0.8rem;
                opacity: 0.9;
            }

            .yoppychat-powered {
                margin-left: auto;
                font-size: 0.7rem;
                opacity: 0.8;
            }

            .yoppychat-powered a {
                color: white;
                text-decoration: none;
            }

            .yoppychat-powered a:hover {
                text-decoration: underline;
            }

            .yoppychat-messages {
                flex: 1;
                overflow-y: auto;
                padding: 16px;
                display: flex;
                flex-direction: column;
                gap: 12px;
                background: #f8f9fa;
            }

            .yoppychat-message {
                max-width: 85%;
                padding: 10px 14px;
                border-radius: 16px;
                line-height: 1.5;
                font-size: 0.9rem;
            }

            .yoppychat-message.bot {
                align-self: flex-start;
                background: white;
                border: 1px solid #e9ecef;
                border-bottom-left-radius: 4px;
            }

            .yoppychat-message.user {
                align-self: flex-end;
                background: ${config.color};
                color: white;
                border-bottom-right-radius: 4px;
            }

            .yoppychat-typing {
                align-self: flex-start;
                background: white;
                border: 1px solid #e9ecef;
                padding: 10px 14px;
                border-radius: 16px;
                border-bottom-left-radius: 4px;
                display: none;
            }

            .yoppychat-typing.active {
                display: flex;
            }

            .yoppychat-typing span {
                width: 8px;
                height: 8px;
                background: #adb5bd;
                border-radius: 50%;
                margin: 0 2px;
                animation: yoppychat-bounce 1.4s infinite ease-in-out both;
            }

            .yoppychat-typing span:nth-child(1) { animation-delay: -0.32s; }
            .yoppychat-typing span:nth-child(2) { animation-delay: -0.16s; }

            @keyframes yoppychat-bounce {
                0%, 80%, 100% { transform: scale(0); }
                40% { transform: scale(1); }
            }

            .yoppychat-input-area {
                padding: 12px;
                border-top: 1px solid #e9ecef;
                display: flex;
                gap: 10px;
                background: white;
            }

            .yoppychat-input-area input {
                flex: 1;
                padding: 12px 16px;
                border: 1px solid #e9ecef;
                border-radius: 24px;
                font-size: 0.9rem;
                outline: none;
                transition: border-color 0.2s;
            }

            .yoppychat-input-area input:focus {
                border-color: ${config.color};
            }

            .yoppychat-input-area button {
                width: 44px;
                height: 44px;
                border-radius: 50%;
                background: ${config.color};
                border: none;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: opacity 0.2s;
            }

            .yoppychat-input-area button:hover {
                opacity: 0.9;
            }

            .yoppychat-input-area button:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }

            .yoppychat-input-area button svg {
                width: 20px;
                height: 20px;
                fill: white;
            }

            /* Mobile responsiveness */
            @media (max-width: 480px) {
                #yoppychat-popup {
                    width: calc(100vw - 40px);
                    height: calc(100vh - 100px);
                    bottom: 70px;
                }
            }
        `;
        document.head.appendChild(styles);

        // Create container
        const container = document.createElement('div');
        container.id = 'yoppychat-widget-container';

        // Create floating button
        container.innerHTML = `
            <button id="yoppychat-button" aria-label="Open chat">
                <svg class="chat-icon" viewBox="0 0 24 24">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
                <svg class="close-icon" viewBox="0 0 24 24">
                    <path d="M18 6L6 18M6 6l12 12" stroke="white" stroke-width="2" stroke-linecap="round" fill="none"/>
                </svg>
            </button>
            <div id="yoppychat-popup">
                <div class="yoppychat-header">
                    <img id="yoppychat-avatar" src="" alt="Channel">
                    <div class="yoppychat-header-info">
                        <h3 id="yoppychat-channel-name"></h3>
                        <p>AI Assistant</p>
                    </div>
                    <div class="yoppychat-powered">
                        <a href="${BASE_URL}" target="_blank">Powered by YoppyChat</a>
                    </div>
                </div>
                <div class="yoppychat-messages" id="yoppychat-messages">
                    <div class="yoppychat-message bot">${config.welcomeMessage}</div>
                </div>
                <div class="yoppychat-typing" id="yoppychat-typing">
                    <span></span><span></span><span></span>
                </div>
                <div class="yoppychat-input-area">
                    <input type="text" id="yoppychat-input" placeholder="${config.placeholder}" maxlength="500">
                    <button id="yoppychat-send" aria-label="Send message">
                        <svg viewBox="0 0 24 24">
                            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
                        </svg>
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(container);

        // Add event listeners
        setupEventListeners();

        // Fetch channel info
        fetchChannelInfo();
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
            popup.classList.toggle('open', isOpen);
            if (isOpen) {
                input.focus();
                trackEvent('widget_opened');
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
    }

    function fetchChannelInfo() {
        fetch(`${BASE_URL}/api/widget/channel/${encodeURIComponent(config.channel)}`)
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('yoppychat-avatar').src = data.thumbnail || '';
                    document.getElementById('yoppychat-channel-name').textContent = data.name || config.channel;
                }
            })
            .catch(err => {
                console.warn('YoppyChat: Could not fetch channel info', err);
                document.getElementById('yoppychat-channel-name').textContent = config.channel;
            });
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
                referrer: window.location.hostname
            })
        })
            .then(res => res.json())
            .then(data => {
                typing.classList.remove('active');
                sendBtn.disabled = false;

                if (data.success && data.answer) {
                    addMessage(data.answer, 'bot');
                    trackEvent('question_asked');
                } else {
                    addMessage('Sorry, I couldn\'t process your question. Please try again.', 'bot');
                }
            })
            .catch(err => {
                console.error('YoppyChat: Error sending message', err);
                typing.classList.remove('active');
                sendBtn.disabled = false;
                addMessage('Something went wrong. Please try again later.', 'bot');
            });
    }

    function addMessage(text, sender) {
        const messagesContainer = document.getElementById('yoppychat-messages');
        const message = document.createElement('div');
        message.className = `yoppychat-message ${sender}`;
        message.textContent = text;
        messagesContainer.appendChild(message);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function trackEvent(event) {
        // Track widget analytics
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
            }).catch(() => { }); // Silently fail
        } catch (e) { }
    }

    // Public API
    window.YoppyChat = {
        init: function (options) {
            if (isInitialized) {
                console.warn('YoppyChat: Widget already initialized');
                return;
            }

            if (!options.channel) {
                console.error('YoppyChat: Channel is required');
                return;
            }

            // Merge options
            config = { ...config, ...options };

            // Create widget when DOM is ready
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', createWidget);
            } else {
                createWidget();
            }

            isInitialized = true;
            trackEvent('widget_loaded');
        },

        open: function () {
            const button = document.getElementById('yoppychat-button');
            const popup = document.getElementById('yoppychat-popup');
            if (button && popup && !isOpen) {
                isOpen = true;
                button.classList.add('open');
                popup.classList.add('open');
            }
        },

        close: function () {
            const button = document.getElementById('yoppychat-button');
            const popup = document.getElementById('yoppychat-popup');
            if (button && popup && isOpen) {
                isOpen = false;
                button.classList.remove('open');
                popup.classList.remove('open');
            }
        },

        destroy: function () {
            const container = document.getElementById('yoppychat-widget-container');
            if (container) {
                container.remove();
                isInitialized = false;
                isOpen = false;
            }
        }
    };
})();
