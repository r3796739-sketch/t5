# YoppyChat AI - Platform Documentation

<p align="center">
  <img src="https://raw.githubusercontent.com/user-attachments/assets/941e699b-4654-47f4-8a19-93e114777a94/primiry__logo.png" alt="YoppyChat AI Logo" width="350">
</p>

<h1 align="center">YoppyChat AI</h1>

<p align="center">
  <strong>Turn YouTube Channels into Scalable, Conversational AI Personas</strong>
  <br />
  <em>Bridging the gap between content creators and their communities through deeply personal, AI-driven engagement.</em>
</p>

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Model Analysis](#business-model-analysis)
   - [Value Proposition](#value-proposition)
   - [Customer Segments](#customer-segments)
   - [Revenue Streams](#revenue-streams)
   - [Key Resources](#key-resources)
   - [Key Activities](#key-activities)
   - [Key Partnerships](#key-partnerships)
   - [Cost Structure](#cost-structure)
   - [Channels](#channels)
3. [Technical Architecture](#technical-architecture)
4. [Feature Breakdown](#feature-breakdown)
5. [Developer Recommendations](#developer-recommendations)
6. [Roadmap & Future Improvements](#roadmap--future-improvements)

---

## Executive Summary

**YoppyChat AI** is a SaaS platform that transforms YouTube channels into interactive AI personas. The platform uses advanced AI technologies (LLMs, RAG, and vector embeddings) to create conversational AI assistants that embody the unique knowledge, voice, and style of content creators.

### Core Problem Solved
- **For Creators:** They are overwhelmed by repetitive questions and can't scale personalized engagement with their audience.
- **For Fans:** Finding specific information from hours of video content is tedious and time-consuming.

### Solution Highlights
- Instant AI persona creation from any YouTube channel URL
- Multi-platform deployment (Discord, Telegram, Public Web Pages)
- Creator monetization through affiliate/referral system
- Tiered subscription plans for individuals and communities

---

## Business Model Analysis

### Value Proposition

| Stakeholder | Value Delivered |
|-------------|-----------------|
| **Content Creators** | 24/7 AI-powered community engagement, passive monetization via affiliate system, reduced repetitive Q&A burden |
| **Fans/Viewers** | Instant Q&A with their favorite creators' knowledge base, cited video sources, accessible learning |
| **Communities (Whop)** | Shared AI assistants for their members, centralized knowledge hub, enhanced community value |
| **Individual Users** | Personal AI assistants for any YouTube channel, multi-channel support, cross-platform integrations |

### Customer Segments

The platform serves **four distinct customer segments**:

#### 1. Individual Users (B2C)
- **Profile:** YouTube enthusiasts, students, researchers, fans
- **Need:** Quick access to creator knowledge, learning aid
- **Plans:** Free (2 channels, 20 queries/month), Personal ($3.60/month), Creator ($18/month)

#### 2. Content Creators (B2B2C)
- **Profile:** YouTubers with established channels
- **Need:** Scale engagement, monetize audience, control brand
- **Plans:** Use the platform as affiliates, earn 40-45% commission on referrals

#### 3. Community Owners (via Whop Integration)
- **Profile:** Discord/community managers, course creators, paid community operators
- **Need:** Provide AI tools to their community members
- **Plans:** Basic (1 shared channel), Pro (2 shared channels), Rich (5 shared channels)

#### 4. Enterprises (Future Potential)
- **Profile:** Businesses, educational institutions
- **Need:** White-label solutions, custom integrations

### Revenue Streams

The platform employs a **hybrid monetization strategy**:

#### 1. Subscription Revenue (Primary)
```
+--------------------+-------------+------------------+--------------------+
| Plan               | Price (USD) | Channels         | Queries/Month      |
+--------------------+-------------+------------------+--------------------+
| Free               | $0          | 2                | 20                 |
| Personal           | $3.60/month | Unlimited        | 500                |
| Creator            | $18/month   | Unlimited        | 10,000             |
+--------------------+-------------+------------------+--------------------+
```

#### 2. Creator Affiliate Commission
- **Personal Plan Referrals:** 40% recurring commission ($1.44/month per subscriber)
- **Creator Plan Referrals:** 45% recurring commission ($8.10/month per subscriber)

#### 3. Community Subscription (B2B)
- Whop-integrated communities pay based on member count
- Query limits scale with community size (100 queries per member)

#### 4. Payment Processors Supported
- **Razorpay:** Primary gateway for INR transactions
- **PayPal:** International transactions and USD payments

### Key Resources

#### Technical Resources
1. **AI/ML Infrastructure**
   - Embedding Models: Gemini, OpenAI
   - LLM Providers: Groq (speed), OpenAI, Gemini
   - Vector Database: Supabase with pgvector (1536 dimensions)
   - Reranking: Sentence Transformers

2. **Backend Infrastructure**
   - Web Framework: Flask (Python)
   - Task Queue: Huey with Redis
   - Database: Supabase (PostgreSQL)
   - Caching: Redis

3. **Integration Ecosystem**
   - Discord Bot Integration
   - Telegram Bot Integration
   - YouTube Data API
   - Whop Platform Integration

#### Human Resources
- Platform development and maintenance team
- Customer support
- Creator relations

### Key Activities

1. **Content Processing Pipeline**
   - YouTube channel scanning and video extraction
   - Transcript extraction with yt-dlp
   - Embedding generation and storage
   - Intelligent long-form content filtering
   - Speaking style analysis and extraction
   - Topic extraction and channel summarization

2. **AI Interaction Service**
   - Retrieval-Augmented Generation (RAG)
   - Context-aware conversation handling
   - Source citation system
   - Chat history management

3. **Platform Operations**
   - User authentication and authorization
   - Subscription management
   - Affiliate tracking and payouts
   - Admin dashboard management

4. **Integration Maintenance**
   - Discord bot orchestration
   - Telegram webhook handling
   - Whop app integration

### Key Partnerships

1. **Whop Platform** - Community marketplace integration
2. **Razorpay** - Payment processing (India-focused)
3. **PayPal** - International payments
4. **YouTube** - Content source (Data API)
5. **Google (Gemini)**, **OpenAI**, **Groq** - AI providers

### Cost Structure

#### Fixed Costs
| Category | Description |
|----------|-------------|
| Infrastructure | Cloud hosting, Supabase, Redis |
| AI API Costs | Embedding generation, LLM queries |
| Third-party Services | Payment processor fees, email service |
| Development | Platform maintenance and feature development |

#### Variable Costs
| Category | Scaling Factor |
|----------|----------------|
| LLM Query Costs | Per-query cost varies by provider |
| Storage Costs | Scales with channel data/embeddings |
| Creator Payouts | 40-45% of referred subscriptions |
| Payment Processing | ~2-3% per transaction |

### Channels

#### Customer Acquisition
1. **Public Chat Pages** - Shareable links with channel personas (`/c/{channel_name}`)
2. **Creator Referral System** - Each creator becomes an acquisition channel
3. **Whop Marketplace** - Community app installation
4. **Organic Search** - SEO-optimized landing pages with structured data

#### Customer Engagement
1. **Web Application** - Primary dashboard at yoppychat.softvait.in
2. **Discord Bots** - Branded and shared bot deployments
3. **Telegram Bots** - Personal and group integrations
4. **Website Embed Widgets** - Floating chat and embedded forms for creator websites
5. **Email Notifications** - Processing completion, updates

---

## Technical Architecture

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND LAYER                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Landing Page  │  Dashboard  │  Chat Interface  │  Admin Panel  │  Creator │
│                │             │                  │               │  Portal   │
└────────────────┴─────────────┴──────────────────┴───────────────┴───────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              APPLICATION LAYER                               │
├─────────────────────────────────────────────────────────────────────────────┤
│   Flask Application (app.py)                                                 │
│   ├── Authentication (Supabase Auth, Whop OAuth, Discord OAuth)             │
│   ├── API Endpoints (REST)                                                   │
│   ├── Webhook Handlers (Telegram, Discord, Razorpay, PayPal, Whop)          │
│   └── Session Management (Redis-backed)                                      │
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
│ • delete_channel    │ │ • LLM Queries       │ │ • Whop Integration          │
│ • update_bot_profile│ │   (Groq/OpenAI)     │ │ • Website Embed Support     │
│ • process_telegram  │ │ • RAG Pipeline      │ │ • YouTube Data API          │
│ • post_answer_proc  │ │   (Gemini TaskTypes)│ └─────────────────────────────┘
│                     │ │ • Topic Extraction  │
│                     │ │ • Style Analysis    │
│                     │ │ • Summarization     │
│                     │ │                     │
└─────────────────────┘ └─────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│   Supabase (PostgreSQL + pgvector)                                          │
│   ├── Tables: profiles, channels, embeddings, chat_history,                 │
│   │           communities, usage_stats, discord_bots, telegram_connections, │
│   │           user_channels, user_communities, creator_earnings,            │
│   │           creator_payouts, razorpay_subscriptions                       │
│   ├── RPC Functions: increment_query_usage, match_embeddings,               │
│   │                  get_visible_channels, get_channels_by_discord_id       │
│   └── Row Level Security: chat_history access control                       │
│                                                                              │
│   Redis                                                                      │
│   ├── Session caching                                                        │
│   ├── User status caching (5-minute TTL)                                     │
│   ├── Task progress tracking                                                 │
│   └── Shared chat history (24-hour TTL)                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Database Schema Overview

#### Core Tables

| Table | Purpose | Key Relationships |
|-------|---------|-------------------|
| `profiles` | User accounts and settings | FK to auth.users, communities |
| `channels` | YouTube channel data and metadata | FK to profiles (creator_id) |
| `embeddings` | Vector embeddings for RAG | FK to channels |
| `chat_history` | Conversation logs | FK to profiles |
| `communities` | Whop community configurations | FK to profiles (owner) |
| `usage_stats` | Per-user query/channel counts | FK to profiles |

#### Integration Tables

| Table | Purpose |
|-------|---------|
| `telegram_connections` | Personal Telegram bot links |
| `group_connections` | Telegram group bot links |
| `discord_bots` | User-created branded Discord bots |
| `discord_servers` | Shared bot server configurations |

#### Commerce Tables

| Table | Purpose |
|-------|---------|
| `razorpay_subscriptions` | Subscription tracking |
| `creator_earnings` | Commission records |
| `creator_payouts` | Payout requests and status |

---

## Feature Breakdown

### 1. Channel Processing

**Flow:**
1. User submits YouTube channel/video URL
2. System validates and extracts channel information
3. Scans for long-form videos (filters shorts)
4. Extracts transcripts using yt-dlp
5. Generates embeddings and stores in pgvector
6. Extracts topics and generates summary
7. Analyzes creator's speaking style (vocabulary, tone, sentence structure)
8. Updates channel status to "ready"
9. Sends email notification to creator

**Key Features:**
- Intelligent long-form video detection
- Speaking style authentication
- Retry logic for failed channels
- Progress tracking via Redis
- Email notifications on completion

### 2. AI Chat System

**RAG Pipeline:**
1. User question → Embedding generation
2. Vector similarity search in pgvector
3. Top-k context retrieval with reranking
4. LLM prompt construction with context
5. Streaming response generation
6. Source citation extraction
7. Chat history persistence

**Features:**
- Streaming responses with SSE
- Chat history context (last 5 messages)
- Dynamic Persona Adaptation (matches creator's speaking style)
- Source citations with video links
- Regeneration capability
- Query limit enforcement

### 3. Multi-Platform Deployment

#### Discord Integration
- **Shared Bot:** Single bot serving multiple servers
- **Branded Bots:** Custom bots with channel-specific branding
- **Auto-complete:** Channel selection via Discord commands
- **Profile Sync:** Bot name/avatar matches YouTube channel

#### Telegram Integration
- **Personal Bot:** One-on-one conversations
- **Group Bot:** Mention-based responses in groups
- **Channel Context:** Persistent channel selection
- **Connection Codes:** Secure account linking

### 4. Website Integration
- **Embeddable Widget:** JavaScript snippet for any website
- **Floating Chat:** Corner widget for visitor engagement
- **Analytics:** Tracking for external embeds

### 5. Subscription & Payment System

**Payment Flow:**
1. User initiates subscription (Razorpay/PayPal)
2. Customer created/linked in payment system
3. Subscription created with plan ID
4. User redirected to payment gateway
5. Webhook receives payment confirmation
6. User plan updated in database
7. Creator commission recorded (if referral)

**Features:**
- Multi-currency support (INR, USD)
- Webhook-based status updates
- Automatic cache invalidation
- Creator commission tracking

### 5. Creator Affiliate System

**Referral Flow:**
1. Creator processes their channel
2. Public chat page created (`/c/{channel_name}`)
3. User visits page → referral ID stored in session
4. User signs up and subscribes
5. Commission recorded in `creator_earnings`
6. Creator views earnings dashboard
7. Creator requests payout with bank details
8. Admin processes payout via RazorpayX

**Commission Structure:**
- Personal Plan: 40% ($1.44/month)
- Creator Plan: 45% ($8.10/month)

### 6. Admin Dashboard

**Capabilities:**
- View all communities and their plans
- Manage non-Whop users and their plans
- Process creator payout requests
- Search and filter payouts
- Create custom plans
- Delete users and their data

---

## Developer Recommendations

### 🔴 Critical Issues to Address

#### 1. Security Vulnerabilities
```
⚠️ SECRET_KEY is hardcoded as "456456" - CRITICAL
⚠️ Sensitive API keys exposed in .env file committed to repo
⚠️ Admin user ID hardcoded ('2f092c41-e0c5-4533-98a2-9e5da027d0ed')
```
**Recommendation:** Use environment variable rotation, secrets manager, role-based admin detection.

#### 2. Code Duplication
- `create_channel()` function defined twice in db_utils.py
- `inject_user_status()` context processor duplicated
- Multiple similar webhook verification patterns

**Recommendation:** Create base classes, refactor into utilities, use decorators.

#### 3. Error Handling
```python
# Many places have broad exception catching
except Exception as e:
    print(f"Error: {e}")  # Logging but no alerting
```
**Recommendation:** Implement structured error handling, centralized logging, alerting (Sentry/PagerDuty).

### 🟡 Performance Improvements

#### 1. Database Optimization
- Add database indexes for frequently queried columns
- Implement connection pooling
- Use batch operations for embeddings

```sql
-- Suggested indexes
CREATE INDEX idx_channels_creator_id ON channels(creator_id);
CREATE INDEX idx_embeddings_channel_id ON embeddings(channel_id);
CREATE INDEX idx_profiles_discord_user_id ON profiles(discord_user_id);
```

#### 2. Caching Strategy
- Current: 5-minute TTL for user status
- Recommendation: Implement tiered caching (hot/warm/cold)
- Add cache warming for active users

#### 3. Async Processing
- Move email sending to dedicated queue
- Implement retry mechanism with exponential backoff
- Add dead letter queue for failed tasks

### 🟢 Feature Enhancements

#### 1. Analytics Dashboard
- Query analytics per channel
- User engagement metrics
- Popular questions clustering
- Revenue analytics for creators

#### 2. Enhanced AI Capabilities
- Fine-tuned models for specific creators
- Multi-language support
- Voice input/output
- Image analysis for video thumbnails

#### 3. Platform Integrations
- Twitter/X bot integration
- WhatsApp Business integration
- Slack workspace integration
- API access for developers

#### 4. Creator Tools
- Custom prompt engineering UI
- Response review and correction
- Banned topics configuration
- Custom greeting messages

### 🔵 Architecture Recommendations

#### 1. Microservices Migration Path
```
Current: Monolithic Flask Application
Future:
├── API Gateway (Kong/AWS API Gateway)
├── Auth Service (Supabase/Auth0)
├── Channel Processing Service
├── AI Query Service
├── Integration Service (Discord/Telegram)
├── Billing Service
└── Analytics Service
```

#### 2. Infrastructure Improvements
- Containerization with Kubernetes
- Auto-scaling based on query volume
- Multi-region deployment
- CDN for static assets

#### 3. Testing Strategy
```
Current: No visible test suite
Recommended:
├── Unit Tests (pytest)
│   ├── utils/
│   └── models/
├── Integration Tests
│   ├── API endpoints
│   └── Webhook handlers
├── E2E Tests (Playwright)
│   └── User flows
└── Load Tests (Locust)
    └── RAG pipeline performance
```

---

## Roadmap & Future Improvements

### Phase 1: Stabilization (Q1 2026)
- [ ] Security audit and remediation
- [ ] Code refactoring and deduplication
- [ ] Comprehensive test suite implementation
- [ ] Monitoring and alerting setup
- [ ] Documentation improvements

### Phase 2: Scaling (Q2 2026)
- [ ] Database optimization and indexing
- [ ] Caching layer improvements
- [ ] Task queue improvements
- [ ] API rate limiting
- [ ] Auto-scaling infrastructure

### Phase 3: Feature Expansion (Q3-Q4 2026)
- [ ] Analytics dashboard launch
- [ ] WhatsApp/Slack integrations
- [ ] Multi-language support
- [ ] Voice interaction capability
- [ ] Developer API launch

### Phase 4: Enterprise (2027)
- [ ] White-label solution
- [ ] Self-hosted deployment option
- [ ] SSO/SAML integration
- [ ] Custom model fine-tuning
- [ ] SLA-backed enterprise tier

---

## Appendix

### Environment Variables Reference

| Variable | Purpose | Required |
|----------|---------|----------|
| `SECRET_KEY` | Flask session encryption | Yes |
| `SUPABASE_URL` | Database URL | Yes |
| `SUPABASE_ANON_KEY` | Public database key | Yes |
| `SUPABASE_SERVICE_KEY` | Admin database key | Yes |
| `GEMINI_API_KEY` | Embedding generation | Yes |
| `YOUTUBE_API_KEY` | YouTube Data API access | Yes |
| `REDIS_URL` | Caching and task queue | Yes |
| `RAZORPAY_KEY_ID` | Payment processing | For payments |
| `PAYPAL_CLIENT_ID` | PayPal integration | For int'l payments |
| `DISCORD_SHARED_CLIENT_ID` | Discord bot | For Discord |
| `TELEGRAM_BOT_TOKEN` | Telegram bot | For Telegram |

### API Endpoints Summary

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/channel` | Submit new channel |
| GET | `/ask/channel/{name}` | Chat interface |
| POST | `/stream_answer` | Get AI response |
| GET | `/c/{channel_name}` | Public chat page |
| POST | `/razorpay_webhook` | Payment webhook |
| POST | `/paypal_webhook` | PayPal webhook |
| GET | `/dashboard` | Creator dashboard |
| GET | `/earnings` | Earnings page |
| GET | `/admin/dashboard` | Admin panel |

---

*Document Version: 1.1*  
*Last Updated: February 05, 2026*  
*Platform: YoppyChat AI*
