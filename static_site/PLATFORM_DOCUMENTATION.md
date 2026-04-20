# YoppyChat AI - Platform Documentation

<p align="center">
  <strong>Turn Any Knowledge Source into a Conversational AI Persona</strong>
  <br />
  <em>Bridging the gap between creators, businesses, and their audiences through deeply personal, AI-driven engagement.</em>
</p>

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Model Analysis](#business-model-analysis)
3. [Technical Architecture](#technical-architecture)
4. [Feature Breakdown](#feature-breakdown)
5. [API Endpoints Reference](#api-endpoints-reference)
6. [Database Schema](#database-schema)
7. [Environment Variables](#environment-variables)
8. [Developer Recommendations](#developer-recommendations)
9. [Roadmap & Future Improvements](#roadmap--future-improvements)

---

## Executive Summary

**YoppyChat AI** is a SaaS platform that transforms any knowledge source — YouTube channels, websites, and WhatsApp chat exports — into interactive AI chatbot personas. The platform uses advanced AI technologies (LLMs, RAG, and vector embeddings) to create conversational AI assistants that embody the unique knowledge, voice, and style of content creators and businesses.

### Core Problems Solved
- **For Creators:** Overwhelmed by repetitive questions; can't scale personalized community engagement.
- **For Businesses:** Need AI customer support trained on their own data (websites, chat logs).
- **For Fans:** Finding specific information from hours of content is tedious.

### Solution Highlights
- Multi-source AI chatbot creation (YouTube, Website, WhatsApp export)
- Multi-platform deployment (Discord, Telegram, WhatsApp Business, Website Embed)
- Creator monetization via affiliate/referral system
- Chatbot marketplace for buying and selling AI assistants
- Lead capture with email delivery
- Tiered subscription plans for individuals and communities

---

## Business Model Analysis

### Value Proposition

| Stakeholder            | Value Delivered                                                                                                |
| ---------------------- | -------------------------------------------------------------------------------------------------------------- |
| **Content Creators**   | 24/7 AI-powered community engagement, passive monetization via affiliate system, reduced repetitive Q&A burden |
| **Businesses**         | AI customer support bots trained on their own website/chat data, lead capture, WhatsApp Business integration   |
| **Fans/Viewers**       | Instant Q&A with their favorite creators' knowledge base, cited video sources, accessible learning             |
| **Communities (Whop)** | Shared AI assistants for their members, centralized knowledge hub, enhanced community value                    |
| **Marketplace Buyers** | Access to pre-trained chatbots via the marketplace for a monthly subscription                                  |

### Customer Segments

#### 1. Individual Users (B2C)
- **Profile:** YouTube enthusiasts, students, researchers, fans
- **Need:** Quick access to creator knowledge, learning aid
- **Plans:** Free (2 chatbots, 20 queries/month), Personal ($3.60/month), Creator ($18/month)

#### 2. Content Creators (B2B2C)
- **Profile:** YouTubers with established channels
- **Need:** Scale engagement, monetize audience, control brand
- **Plans:** Use the platform as affiliates, earn 40-45% commission on referrals

#### 3. Business Owners
- **Profile:** Small-to-medium businesses needing AI support bots
- **Need:** Deploy chatbots trained on their website/FAQ/chat history data
- **Sources:** Website crawling, WhatsApp chat export (.txt)

#### 4. Community Owners (via Whop Integration)
- **Profile:** Discord/community managers, course creators, paid community operators
- **Need:** Provide AI tools to their community members
- **Plans:** Basic (1 shared channel), Pro (2 shared channels), Rich (5 shared channels)

#### 5. Marketplace Buyers
- **Profile:** Users who want pre-built, expert chatbots without creating them
- **Need:** Access a creator's trained AI chatbot via a monthly subscription
- **Flow:** Purchase via a Razorpay marketplace subscription, get a monthly query allowance

### Revenue Streams

#### 1. Subscription Revenue (Primary)

| Plan     | Price (USD) | Chatbots  | Queries/Month |
| -------- | ----------- | --------- | ------------- |
| Free     | $0          | 2         | 20            |
| Personal | $3.60/month | Unlimited | 500           |
| Creator  | $18/month   | Unlimited | 10,000        |

#### 2. Creator Affiliate Commission
- **Personal Plan Referrals:** 40% recurring commission ($1.44/month per subscriber)
- **Creator Plan Referrals:** 45% recurring commission ($8.10/month per subscriber)

#### 3. Community Subscription (B2B)
- Whop-integrated communities pay based on member count
- Query limits scale with community size (100 queries per member)

#### 4. Chatbot Marketplace Revenue
- The platform operates a marketplace where creators can list their trained chatbots for sale
- Buyers pay a monthly Razorpay subscription to access the chatbot
- Platform earns a per-query fee (`MARKETPLACE_COST_PER_QUERY_PAISE` env var, default: 90 paise/query)
- Creator earns the remainder of the monthly subscription

#### 5. Payment Processors Supported
- **Razorpay:** Primary gateway for INR transactions (subscriptions + marketplace)
- **PayPal:** International transactions and USD payments

---

## Technical Architecture

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND LAYER                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Landing Page  │  Dashboard  │  Chat Interface  │  Admin Panel  │  Creator  │
│                │             │                  │               │  Settings  │
└────────────────┴─────────────┴──────────────────┴───────────────┴───────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              APPLICATION LAYER                               │
├─────────────────────────────────────────────────────────────────────────────┤
│   Flask Application (app.py)                                                 │
│   ├── Authentication (Supabase Auth, Whop OAuth, Discord OAuth)             │
│   ├── API Endpoints (REST)                                                   │
│   ├── Webhook Handlers (Telegram, Discord, Razorpay, PayPal, Whop,         │
│   │                     YCloud/WhatsApp)                                     │
│   └── Session Management (Redis-backed)                                      │
│                                                                              │
│   Blueprints:                                                                │
│   ├── routes_whatsapp.py   — WhatsApp Business via YCloud                   │
│   └── routes_multi_source.py — Multi-source chatbot creation                │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                        ┌───────────────┼───────────────┐
                        ▼               ▼               ▼
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────────────┐
│   TASK QUEUE        │ │   AI SERVICES       │ │   INTEGRATION SERVICES      │
│   (Huey + Redis)    │ │                     │ │                             │
├─────────────────────┤ ├─────────────────────┤ ├─────────────────────────────┤
│ • process_channel   │ │ • Embedding Gen     │ │ • Discord Bot Service       │
│ • sync_channel      │ │   (Gemini/OpenAI)   │ │ • Telegram Bot Handlers     │
│ • delete_channel    │ │ • LLM Queries       │ │ • WhatsApp (YCloud)         │
│ • update_bot_profile│ │   (Groq/OpenAI/     │ │ • Website Embed Support     │
│ • process_telegram  │ │    Gemini)          │ │ • YouTube Data API          │
│ • process_website   │ │ • RAG Pipeline      │ │ • Whop Platform             │
│   _source_task      │ │   (Gemini TaskTypes)│ │   Integration               │
│ • process_whatsapp  │ │ • Topic Extraction  │ └─────────────────────────────┘
│   _source_task      │ │ • Style Analysis    │
│ • post_answer_proc  │ │ • Summarization     │
└─────────────────────┘ └─────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│   Supabase (PostgreSQL + pgvector)                                          │
│   ├── Tables: profiles, channels, embeddings, chat_history,                 │
│   │           communities, usage_stats, discord_bots, telegram_connections, │
│   │           group_connections, user_channels, user_communities,           │
│   │           creator_earnings, creator_payouts, razorpay_subscriptions,   │
│   │           data_sources, whatsapp_configs, whatsapp_conversations,      │
│   │           whatsapp_messages, widget_analytics, chatbot_transfers        │
│   ├── RPC Functions: increment_query_usage, match_embeddings,               │
│   │                  get_visible_channels, get_channels_by_discord_id,      │
│   │                  increment_marketplace_query                            │
│   └── Row Level Security: chat_history access control                       │
│                                                                              │
│   Redis                                                                      │
│   ├── Session caching                                                        │
│   ├── User status caching (5-minute TTL)                                    │
│   ├── Task progress tracking                                                 │
│   └── Shared chat history (24-hour TTL)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Feature Breakdown

### 1. Multi-Source Chatbot Creation

**Supported Data Sources (can be combined):**
- **YouTube Channels/Videos:** Transcripts extracted via yt-dlp, embedded using Gemini/OpenAI
- **Website URLs:** Website pages crawled and scraped, stored as vector embeddings
  - Crawl modes: `auto`, `single_page`, `full_crawl`
- **WhatsApp Chat Export (.txt):** WhatsApp chat export files parsed and embedded
  - Optional: specify a preferred agent name to focus the bot's persona on

**Bot Persona Types (auto-detected or manually set):**
- `youtuber` — YouTube-only sources
- `business` — WhatsApp and/or website sources (customer support style)
- `general` — Mixed sources

**Flow:**
1. User visits `/chatbot/create` UI
2. Fills in chatbot name and selects one or more sources
3. Submits form → `POST /chatbot/create`
4. System creates a parent channel record and individual `data_sources` rows
5. Async tasks are scheduled per source (Huey + Redis)
6. User can poll `/source/<source_id>/status` to track per-source processing
7. Chatbot becomes active when all sources complete

**Key Endpoint:**
- `POST /chatbot/create` — accepts `youtube_urls[]`, `website_url`, `whatsapp_file` (`.txt`), `chatbot_name`

---

### 2. Chatbot Settings Page

Full-page, tabbed interface at `/chatbot/<id>/settings` for chatbot owners to configure every aspect of their bot.

**Tabs:**
1. **General** — Edit chatbot name, creator name, bot type (`youtuber/business/general`), speaking style
2. **Data Sources** — View all processed data sources (YouTube, website, WhatsApp) with their status; add new website sources at any time
3. **Lead Capture** — Enable/disable lead capture form, set recipient email, choose which fields to collect (name, email, phone, custom), and write a **custom intro prompt** (`lead_capture_prompt`) that the AI uses to introduce the form naturally in conversation
4. **Integrations** — Status cards for all 4 integrations (WhatsApp, Discord, Telegram, Website Embed) with quick-link buttons; integrations transfer with the bot during marketplace sales
5. **Danger Zone** — Delete the chatbot entirely

**Other settings features:**
- **Avatar Upload:** `POST /chatbot/<id>/upload-avatar` — accepts `.png/.jpg/.jpeg/.gif/.webp` up to 5MB; stored under `static/uploads/avatars/`
- **Add Website Source on the fly:** `POST /chatbot/<id>/add-website` — adds and processes a new website source into any existing chatbot
- **Promotion Triggers:** Free-text field (`promotion_triggers`) appended as critical instructions to the speaking style, enabling the bot to autonomously promote products/links at the right moment
- **Visual Flow Builder:** See [Section 16](#16-visual-flow-builder)
- **Quick-Reply Buttons (WhatsApp):** See [Section 17](#17-whatsapp-quick-reply-buttons)

---

### 3. AI Chat System

**RAG Pipeline:**
1. User question → Embedding generation (Gemini)
2. Vector similarity search in pgvector (`match_embeddings`)
3. Top-k context retrieval with reranking (Sentence Transformers)
4. LLM prompt construction with channel context, speaking style, chat history
5. Streaming response via SSE (`/stream_answer`)
6. Source citation extraction (video links from YouTube sources)
7. Chat history persisted to Supabase (`chat_history`)

**Features:**
- Streaming responses with SSE (Server-Sent Events)
- Chat history context (last 5 exchanges used in prompt, up to 50 messages stored)
- Regeneration capability (re-runs without the last Q&A pair in context)
- Dynamic Persona Adaptation (matches creator's speaking style extracted from transcripts)
- Source citations with video links (YouTube-sourced bots)
- Query limit enforcement (personal plan limits + community limits)
- Chat history limit: 50 messages per channel (after which user must clear chat)
- Marketplace chatbot query tracking (increments per-transfer monthly counter)
- **Integration source tagging:** every query is tagged with its origin platform (`web`, `whatsapp`, `telegram`, `discord`, `embed`) and stored in `chat_history.integration_source` for per-platform analytics
- **Silent limit enforcement on bots:** when a creator's query limit is reached on WhatsApp / Telegram / Discord, the bot silently discards the reply (no error shown to end-users) and logs a server-side warning

---

### 4. WhatsApp Business Integration (YCloud)

**Overview:** Users link their WhatsApp Business number (via YCloud) to their chatbot. Inbound WhatsApp messages are automatically answered by the AI bot.

**Flow:**
1. User goes to WhatsApp dashboard or chatbot settings → Integrations tab
2. Enters their YCloud API Key and WhatsApp phone number
3. Config saved (encrypted) to `whatsapp_configs` table
4. YCloud webhooks POST to `/api/whatsapp/webhook`
5. Platform verifies YCloud-Signature header and processes incoming messages
6. AI response generated and sent back via YCloud API

**API Endpoints (Blueprint: `/api/whatsapp`):**
- `GET /api/whatsapp/webhook` — Health check (returns 200 for YCloud)
- `POST /api/whatsapp/webhook` — Receive inbound messages
- `GET /api/whatsapp/config` — Get user's config
- `POST /api/whatsapp/config` — Save/update config
- `DELETE /api/whatsapp/config/<id>` — Delete a config
- `GET /api/whatsapp/conversations` — List all WhatsApp conversations
- `GET /api/whatsapp/conversations/<id>/messages` — Get a conversation's messages
- `GET /api/whatsapp/stats` — Usage stats (total messages, conversations, etc.)

**Database Tables:** `whatsapp_configs`, `whatsapp_conversations`, `whatsapp_messages`

---

### 5. Discord Integration

- **Shared Bot:** Single YoppyChat bot serving multiple servers
- **Branded Bots:** User-created custom bots with channel-specific branding
- **Auto-complete:** Channel selection via Discord slash commands
- **Profile Sync:** Bot name/avatar matches the chatbot's profile
- **Discord OAuth:** Users can link their Discord account

**Key Routes:**
- `GET /integrations/discord` — Discord dashboard
- `POST /api/discord/bot` — Create a new branded Discord bot
- `POST /api/discord/bot/<id>/start` — Start a bot
- `POST /api/discord/bot/<id>/toggle` — Enable/disable a bot
- `DELETE /api/discord/bot/<id>` — Delete a bot

---

### 6. Telegram Integration

- **Personal Bot:** One-on-one conversations
- **Group Bot:** Mention-based responses in groups
- **Channel Context:** Persistent channel selection
- **Connection Codes:** Secure account linking

**Key Routes:**
- `GET /integrations/telegram` — Telegram dashboard
- `POST /connect_telegram` — Link personal bot via connection code
- `POST /connect_group` — Link bot to a Telegram group
- `POST /disconnect_telegram` — Unlink personal bot
- `POST /disconnect_group/<channel_id>` — Unlink group

---

### 7. Website Embed Integration

An embeddable JavaScript widget for any website.

**Features:**
- Floating chat widget in the corner of the page for visitor engagement
- Lead capture form integration (collects visitor info before/during chat)
- Event tracking via `widget_analytics` table
- Separate widget API endpoints that don't require user login

**Key Routes:**
- `GET /integrations/embed` — Embed dashboard with JS snippet
- `GET /api/widget/channel/<name>` — Get channel info for widget header
- `POST /api/widget/ask` — Handle questions from the embedded widget
- `POST /api/widget/track` — Track widget events for analytics

---

### 8. Lead Capture

Chatbot owners can configure a lead capture form that appears inside their chatbot widget (web embed or public chat page).

**Configuration (via Chatbot Settings → Lead Capture tab):**
- Enable/disable the form
- Set recipient email address (where leads are emailed)
- Select fields: name, email, phone, or custom fields (stored as JSONB)

**Flow:**
1. User interacts with the chatbot widget
2. At a configured point, the lead form is presented
3. User fills in the form and submits
4. `POST /api/submit-lead` sends the lead data to the chatbot owner's email via Flask-Mail

**Database fields (on `channels` table):**
- `lead_capture_enabled` (bool)
- `lead_capture_email` (string)
- `lead_capture_fields` (JSONB array)

---

### 9. Subscription & Payment System

**Razorpay Flow:**
1. User initiates subscription via `POST /api/razorpay/subscribe`
2. Customer created/found in Razorpay by email
3. Subscription created with a plan ID
4. User redirected to Razorpay checkout
5. Razorpay webhook (`POST /razorpay_webhook`) confirms payment
6. User plan updated in database
7. Creator commission recorded if referral exists

**PayPal Flow:**
- Used for international (non-INR) payments
- `POST /paypal_webhook` handles webhook events

**Features:**
- Multi-currency support (INR via Razorpay, USD via PayPal)
- Automatic Redis cache invalidation on plan change
- Creator commission tracking on every paid subscription
- Webhook signature validation for both processors

---

### 10. Chatbot Marketplace

Users can list their trained chatbots for sale, and buyers can subscribe for monthly access.

**Creator (Seller) Flow:**
1. Goes to `GET /marketplace/transfer/<chatbot_id>` — transfer setup page
2. Sets a monthly price (must cover platform per-query fee) and query limit
3. `POST /api/marketplace/create_transfer` generates a unique transfer link
4. Shares the link with the buyer

**Buyer Flow:**
1. Opens the transfer link: `GET /marketplace/accept/<transfer_code>`
2. Reviews chatbot info and pricing
3. `POST /api/marketplace/subscribe` creates a Razorpay marketplace subscription
4. After payment, the buyer gains access to the chatbot
5. Each query is tracked via `increment_marketplace_query` RPC
6. When monthly limit is reached, user gets an error stream event

**Payout Flow:**
1. Creator requests payout via `POST /api/marketplace/request_payout`
2. Admin processes payout via RazorpayX

**Database Tables:** `chatbot_transfers`, `creator_marketplace_earnings`

The `creator_marketplace_earnings` table records each successful marketplace payment, storing `creator_amount` (in paise), `payment_date`, and `creator_id`. This powers the monthly revenue chart on the creator dashboard.

---

### 11. Creator Affiliate System

**Referral Flow:**
1. Creator processes their channel
2. Public chat page created (`/c/{channel_name}`)
3. Visitor → referral ID stored in session
4. Visitor signs up and subscribes
5. Commission recorded in `creator_earnings`
6. Creator views earnings on `/earnings` dashboard
7. Creator requests payout with bank details via `save_payout_details`
8. Admin processes payout via `api_admin_complete_payout`

**Commission Structure:**
- Personal Plan: 40% ($1.44/month per referral)
- Creator Plan: 45% ($8.10/month per referral)

---

### 12. Dashboard

The main `/dashboard` serves as a hub showing:
- Integration status cards (Discord, Telegram, WhatsApp, Website Embed — green/grey)
- Creator channel cards with per-channel stats (referrals, paid referrals, MRR, current adds)
- Aggregated totals across all creator channels
- Monthly revenue chart (last 6 months)
- Quick access to all integrations and settings

---

### 13. Admin Dashboard

**Capabilities (`/admin/dashboard`):**
- View all communities and their plans
- Manage non-Whop users and their plans (assign custom plans)
- Process creator payout requests (mark as paid)
- Search and filter payouts
- Create custom plans
- Delete users and all their data

**Admin API Endpoints:**
- `PUT /api/admin/payout/<id>/complete`
- `POST /api/admin/plan`
- `POST /api/admin/set_default_plan`
- `POST /api/admin/set_current_plan`
- `DELETE /api/admin/plan`
- `DELETE /api/admin/user/<id>`

---

### 14. Public Chat Pages

- URL: `/c/{channel_name}`
- Publicly accessible (no login required to view)
- Stores referral ID in session for affiliate tracking
- Logged-in users: channel added to their list or temporary session if at limit
- Logged-out users: prompted to sign up to save history

---

### 15. Programmatic SEO

When a new chatbot finishes processing, a background Huey task (`generate_seo_metadata_task`) automatically generates SEO metadata for the public chat page (`/c/<channel_name>`).

**Two-phase pipeline (`utils/seo_utils.py`):**
1. **Keyword Harvesting** — Queries Google Autocomplete with 5 templates (e.g., `{name} AI`, `chat with {name}`) to fetch real user search queries.
2. **LLM Metadata Generation** — Feeds the harvested keywords to the configured LLM to produce an SEO-optimised title (≤60 chars), meta description (140–160 chars), and H1 (≤70 chars).

**LLM provider priority (configurable via `.env`):**
- `SEO_LLM_PROVIDER` / `SEO_MODEL_NAME` (dedicated override)
- `SUMMARY_LLM_PROVIDER` / `SUMMARY_MODEL_NAME` (reuses fast summary model)
- `LLM_PROVIDER` / `MODEL_NAME` (main chat model fallback)

**Database columns written (on `channels` table):**
- `seo_title`
- `seo_meta_description`
- `seo_h1`

The task runs silently — any failure is logged as a warning and never surfaces to the user. Safe fallback defaults are used if LLM generation fails.

---

### 16. Visual Flow Builder

A drag-and-drop conversation flow editor allowing chatbot owners to build structured, branching conversations that activate on WhatsApp.

**Access:** `/flow/builder/<chatbot_id>` (owner-only, login required)

**How it works:**
1. Owner builds a flow in the visual UI (nodes: text, image, video, audio, document, location, buttons, CTA URL, list, lead capture)
2. Flow is saved to the `channel_flows` table via `POST /flow/api/<chatbot_id>`
3. Owner activates a flow via `POST /flow/api/<chatbot_id>/activate`
4. On each inbound WhatsApp message, `routes_whatsapp.py` checks for an active flow first — if the flow handles the message, the AI is skipped entirely
5. The AI can also **trigger a flow** mid-conversation: if the LLM appends `[TRIGGER_FLOW: "<name>"]` to its reply, that flow is started

**Flow state tracking:** stored in `whatsapp_conversations.flow_node_id` and `flow_variables` (JSONB).

**Flow CRUD API (`/flow/api/`):**

| Method | Endpoint                  | Purpose                    |
| ------ | ------------------------- | -------------------------- |
| `GET`  | `/flow/builder/<id>`      | Flow builder UI            |
| `GET`  | `/flow/api/<id>`          | Load current flow          |
| `POST` | `/flow/api/<id>`          | Save/update flow           |
| `POST` | `/flow/api/<id>/activate` | Activate/deactivate a flow |

**Database Table:** `channel_flows` (`id`, `channel_id`, `name`, `flow_data` JSONB, `is_active`)

---

### 17. WhatsApp Quick-Reply Buttons

After every AI reply in WhatsApp, the bot can optionally send interactive button cards to guide users to common actions.

**Modes (configurable via `quick_reply_mode` on the `channels` table):**

| Mode     | Behaviour                                                                                     |
| -------- | --------------------------------------------------------------------------------------------- |
| `off`    | No buttons sent (default)                                                                     |
| `manual` | Fixed buttons defined in `quick_reply_buttons` (JSONB array with `title` + optional `answer`) |
| `ai`     | The same LLM suggests 1–3 contextual follow-up button labels based on the AI reply            |

**Manual mode bonus:** If a user taps a manually configured button that has a pre-written `answer`, the bot sends that fixed reply immediately — skipping the AI entirely. This is useful for FAQ shortcuts.

Buttons with ≤3 options are sent as `interactive/button` messages; 4+ options are sent as an `interactive/list` message.

---

### 18. Channel Management

- **Refresh channel:** `POST /refresh_channel/<channel_id>` — re-runs transcript extraction and re-embeds
- **Delete channel:** `POST /delete_channel/<channel_id>` — if owner: full deletion; if linked user: just unlinks
- **Toggle privacy:** `POST /toggle_channel_privacy/<channel_id>` — make channel public/private

---

## API Endpoints Reference

| Method            | Endpoint                           | Auth | Purpose                         |
| ----------------- | ---------------------------------- | ---- | ------------------------------- |
| `POST`            | `/chatbot/create`                  | ✅    | Create multi-source chatbot     |
| `GET`             | `/chatbot/<id>/settings`           | ✅    | Chatbot settings page           |
| `POST`            | `/chatbot/<id>/settings`           | ✅    | Update settings (JSON)          |
| `POST`            | `/chatbot/<id>/upload-avatar`      | ✅    | Upload chatbot avatar           |
| `POST`            | `/chatbot/<id>/add-website`        | ✅    | Add website source              |
| `DELETE`          | `/chatbot/<id>/delete`             | ✅    | Delete chatbot                  |
| `GET`             | `/chatbot/<id>/sources`            | ✅    | Get all data sources            |
| `GET`             | `/source/<id>/status`              | ✅    | Get source processing status    |
| `GET`             | `/ask/channel/<name>`              | —    | Chat interface                  |
| `POST`            | `/stream_answer`                   | ✅    | Get AI response (SSE)           |
| `POST`            | `/clear_chat`                      | ✅    | Clear chat history              |
| `GET`             | `/c/<channel_name>`                | —    | Public chat page                |
| `POST`            | `/api/submit-lead`                 | —    | Submit lead from widget         |
| `POST`            | `/api/widget/ask`                  | —    | Widget chat endpoint            |
| `POST`            | `/api/widget/track`                | —    | Widget analytics event          |
| `GET`             | `/api/whatsapp/webhook`            | —    | YCloud webhook health check     |
| `POST`            | `/api/whatsapp/webhook`            | —    | YCloud inbound message          |
| `GET/POST/DELETE` | `/api/whatsapp/config`             | ✅    | Manage WhatsApp config          |
| `GET`             | `/api/whatsapp/conversations`      | ✅    | WhatsApp conversation list      |
| `GET`             | `/api/whatsapp/stats`              | ✅    | WhatsApp usage stats            |
| `GET`             | `/marketplace/transfer/<id>`       | ✅    | Create marketplace listing      |
| `POST`            | `/api/marketplace/create_transfer` | ✅    | Generate transfer link          |
| `GET`             | `/marketplace/accept/<code>`       | —    | Buyer checkout page             |
| `POST`            | `/api/marketplace/subscribe`       | ✅    | Create marketplace subscription |
| `POST`            | `/api/marketplace/request_payout`  | ✅    | Request marketplace payout      |
| `POST`            | `/razorpay_webhook`                | —    | Razorpay payment webhook        |
| `POST`            | `/paypal_webhook`                  | —    | PayPal payment webhook          |
| `GET`             | `/dashboard`                       | ✅    | Main dashboard                  |
| `GET`             | `/earnings`                        | ✅    | Creator earnings page           |
| `GET`             | `/admin/dashboard`                 | 🔒    | Admin panel                     |
| `POST`            | `/refresh_channel/<id>`            | ✅    | Re-process a channel            |
| `POST`            | `/delete_channel/<id>`             | ✅    | Delete or unlink a channel      |

---

## Database Schema

### Core Tables

| Table          | Purpose                       | Key Columns                                                                                                                                                                                                                    |
| -------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `profiles`     | User accounts and settings    | `id`, `email`, `full_name`, `plan`, `razorpay_customer_id`, `discord_user_id`                                                                                                                                                  |
| `channels`     | Chatbot/channel data          | `id`, `creator_id`, `channel_name`, `bot_type`, `status`, `is_ready`, `has_youtube`, `has_whatsapp`, `has_website`, `lead_capture_enabled`, `lead_capture_email`, `lead_capture_fields`, `channel_thumbnail`, `speaking_style` |
| `data_sources` | Per-source processing status  | `id`, `chatbot_id`, `source_type` (youtube/website/whatsapp), `source_url`, `status`, `metadata`                                                                                                                               |
| `embeddings`   | Vector embeddings for RAG     | `id`, `channel_id`, `content`, `embedding` (1536-dim)                                                                                                                                                                          |
| `chat_history` | Conversation logs             | `id`, `user_id`, `channel_name`, `question`, `answer`                                                                                                                                                                          |
| `communities`  | Whop community configurations | `id`, `owner_id`, `whop_community_id`, `plan`                                                                                                                                                                                  |

### Integration Tables

| Table                    | Purpose                                        |
| ------------------------ | ---------------------------------------------- |
| `telegram_connections`   | Personal Telegram bot links                    |
| `group_connections`      | Telegram group bot links                       |
| `discord_bots`           | User-created branded Discord bots              |
| `discord_servers`        | Shared bot server configurations               |
| `whatsapp_configs`       | YCloud API keys and linked chatbot (encrypted) |
| `whatsapp_conversations` | WhatsApp conversation threads                  |
| `whatsapp_messages`      | Individual WhatsApp messages                   |
| `widget_analytics`       | Website embed usage events                     |

### Commerce Tables

| Table                          | Purpose                                              |
| ------------------------------ | ---------------------------------------------------- |
| `razorpay_subscriptions`       | Subscription tracking                                |
| `creator_earnings`             | Affiliate commission records                         |
| `creator_payouts`              | Payout requests and status                           |
| `chatbot_transfers`            | Marketplace chatbot listings and purchases           |
| `creator_marketplace_earnings` | Per-payment marketplace revenue records for creators |

### Flow & SEO Tables

| Table           | Purpose                                                                   |
| --------------- | ------------------------------------------------------------------------- |
| `channel_flows` | Visual flow definitions per chatbot (`is_active` flag, `flow_data` JSONB) |

**New `channels` table columns:**
- `seo_title`, `seo_meta_description`, `seo_h1` — auto-generated SEO metadata
- `promotion_triggers` — custom AI instructions for promotional behaviour
- `quick_reply_mode` (`off` / `manual` / `ai`) — WhatsApp button mode
- `quick_reply_buttons` (JSONB) — manual button definitions
- `lead_capture_prompt` — custom intro text for lead capture form

**New `chat_history` table column:**
- `integration_source` (TEXT, default `'web'`) — tracks query origin platform

### RPC Functions

| Function                         | Purpose                                        |
| -------------------------------- | ---------------------------------------------- |
| `match_embeddings`               | Vector similarity search                       |
| `increment_query_usage`          | Decrement community query quota                |
| `increment_personal_query_usage` | Decrement personal plan query quota            |
| `get_visible_channels`           | Channels visible to a user                     |
| `get_channels_by_discord_id`     | Discord channel lookup                         |
| `increment_marketplace_query`    | Track marketplace query usage per transfer     |
| `get_channel_add_counts`         | Count users who added each channel (dashboard) |

---

## Environment Variables

| Variable                            | Purpose                              | Required           |
| ----------------------------------- | ------------------------------------ | ------------------ |
| `SECRET_KEY`                        | Flask session encryption             | Yes                |
| `SUPABASE_URL`                      | Database URL                         | Yes                |
| `SUPABASE_ANON_KEY`                 | Public database key                  | Yes                |
| `SUPABASE_SERVICE_KEY`              | Admin database key                   | Yes                |
| `GEMINI_API_KEY`                    | Embedding generation                 | Yes                |
| `YOUTUBE_API_KEY`                   | YouTube Data API access              | Yes                |
| `REDIS_URL`                         | Caching and task queue               | Yes                |
| `RAZORPAY_KEY_ID`                   | Razorpay payment processing          | For payments       |
| `RAZORPAY_KEY_SECRET`               | Razorpay webhook validation          | For payments       |
| `RAZORPAY_MARKETPLACE_BASE_PLAN_ID` | ₹1 base plan for marketplace         | For marketplace    |
| `MARKETPLACE_COST_PER_QUERY_PAISE`  | Platform fee per query (default: 90) | For marketplace    |
| `PAYPAL_CLIENT_ID`                  | PayPal integration                   | For int'l payments |
| `PAYPAL_CLIENT_SECRET`              | PayPal OAuth                         | For int'l payments |
| `PAYPAL_MODE`                       | `sandbox` or `live`                  | For int'l payments |
| `DISCORD_SHARED_CLIENT_ID`          | Shared Discord bot                   | For Discord        |
| `DISCORD_SHARED_BOT_TOKEN`          | Shared Discord bot token             | For Discord        |
| `TELEGRAM_BOT_TOKEN`                | Telegram bot                         | For Telegram       |
| `YCLOUD_API_KEY`                    | YCloud WhatsApp API                  | For WhatsApp       |
| `YCLOUD_WEBHOOK_SECRET`             | YCloud webhook signature validation  | For WhatsApp       |
| `MAIL_SERVER`                       | SMTP server for emails               | For emails         |
| `MAIL_USERNAME`                     | SMTP username                        | For emails         |
| `MAIL_PASSWORD`                     | SMTP password                        | For emails         |
| `WHOP_API_KEY`                      | Whop platform API                    | For Whop           |
| `WHOP_CLIENT_ID`                    | Whop OAuth                           | For Whop           |
| `FLASK_ENV`                         | `development` for dev login          | Dev only           |
| `SEO_LLM_PROVIDER`                  | LLM provider for SEO generation      | Optional           |
| `SEO_MODEL_NAME`                    | Model name for SEO generation        | Optional           |

---

## Developer Recommendations

### 🔴 Critical Issues

```
⚠️ SECRET_KEY should be loaded from environment (not hardcoded)
⚠️ Admin user ID is hardcoded — should use role-based detection
⚠️ Sensitive API keys should be rotated regularly
```

### 🟡 Performance Improvements

- Add database indexes for frequently queried columns:
  ```sql
  CREATE INDEX idx_channels_creator_id ON channels(creator_id);
  CREATE INDEX idx_embeddings_channel_id ON embeddings(channel_id);
  CREATE INDEX idx_data_sources_chatbot_id ON data_sources(chatbot_id);
  CREATE INDEX idx_whatsapp_configs_user_id ON whatsapp_configs(user_id);
  ```
- Current Redis TTL for user status: 5 minutes (good)
- Move email sending entirely to background task queue

### 🟢 Feature Enhancements

1. **Analytics Dashboard** — Query analytics per chatbot, popular questions clustering
2. **Multi-language support** — translate responses
3. **Voice input/output** — browser Speech API integration
4. **Slack integration** — similar pattern to Discord
5. **Developer API** — public REST API with API key auth

---

## Roadmap & Future Improvements

### Phase 1: Stabilization (Q1 2026)
- [x] Multi-source chatbot creation (YouTube + Website + WhatsApp)
- [x] Chatbot settings page (tabbed, full-page)
- [x] WhatsApp Business via YCloud
- [x] Lead capture with email delivery + custom intro prompt
- [x] Chatbot avatar upload
- [x] Chatbot marketplace (buy/sell)
- [x] Dashboard analytics (referrals, MRR, revenue chart)
- [x] Programmatic SEO for creator public pages
- [x] Visual Flow Builder for WhatsApp conversations
- [x] WhatsApp quick-reply buttons (manual + AI modes)
- [x] Promotion triggers (AI-driven promotional instructions)
- [x] Per-platform integration source tracking in chat history
- [x] Silent credit-limit enforcement on WhatsApp/Telegram/Discord bots
- [ ] Security audit and remediation
- [ ] Comprehensive test suite

### Phase 2: Scaling (Q2 2026)
- [ ] Database optimization and indexing
- [ ] Caching layer improvements
- [ ] API rate limiting
- [ ] Auto-scaling infrastructure

### Phase 3: Feature Expansion (Q3-Q4 2026)
- [ ] Analytics dashboard per chatbot
- [ ] Slack integration
- [ ] Multi-language support
- [ ] Voice interaction capability
- [ ] Developer API launch

### Phase 4: Enterprise (2027)
- [ ] White-label solution
- [ ] Self-hosted deployment
- [ ] SSO/SAML
- [ ] Custom model fine-tuning

---

*Document Version: 2.1*
*Last Updated: March 9, 2026*
*Platform: YoppyChat AI*
