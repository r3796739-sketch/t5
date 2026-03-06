-- ============================================================================
-- YoppyChat AI — Complete Database Schema Migration
-- Generated: 2026-02-25
-- 
-- This script recreates the ENTIRE schema in a fresh Supabase project.
-- Run this in the Supabase SQL Editor on your new project.
-- ============================================================================

-- 1. Enable Required Extensions
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- 2. CORE TABLES
-- ============================================================================

-- Channels table (chatbots)
-- This is the central table — each row represents one chatbot/channel
CREATE TABLE public.channels (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    channel_url TEXT UNIQUE,  -- Can be NULL for non-YouTube chatbots
    user_id UUID,             -- Legacy FK, kept for backward compat
    creator_id UUID,          -- Who created this chatbot (preferred over user_id)
    status TEXT DEFAULT 'pending' NOT NULL,  -- pending, processing, ready, failed
    is_shared BOOLEAN DEFAULT false NOT NULL,
    community_id UUID NULL,
    channel_name TEXT NULL,
    channel_thumbnail TEXT NULL,
    creator_name TEXT NULL,    -- Display name for the creator/persona
    summary TEXT NULL,
    topics JSONB NULL,
    videos JSONB NULL,
    subscriber_count BIGINT NULL,
    speaking_style TEXT NULL,  -- AI-generated speaking style description
    bot_type TEXT DEFAULT 'youtuber',  -- youtuber, business, general
    
    -- Multi-source flags
    has_youtube BOOLEAN DEFAULT false,
    has_whatsapp BOOLEAN DEFAULT false,
    has_website BOOLEAN DEFAULT false,
    is_ready BOOLEAN DEFAULT false,
    
    -- Lead capture
    lead_capture_enabled BOOLEAN DEFAULT false,
    lead_capture_email TEXT NULL,
    lead_capture_fields JSONB NULL,  -- Array of field configs

    promotion_triggers TEXT NULL, -- Actionable instructions for the AI

    -- Avatar
    avatar_url TEXT NULL,
    
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Communities table (Whop integration)
CREATE TABLE public.communities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whop_community_id TEXT UNIQUE NOT NULL,
    owner_user_id UUID NOT NULL,
    plan_id TEXT DEFAULT 'basic_community' NOT NULL,
    query_limit INTEGER DEFAULT 0 NOT NULL,
    queries_used INTEGER DEFAULT 0 NOT NULL,
    shared_channel_limit INTEGER DEFAULT 1 NOT NULL,
    trial_queries_used INTEGER DEFAULT 0 NOT NULL,
    default_channel_id BIGINT NULL REFERENCES public.channels(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Profiles table (user accounts)
CREATE TABLE public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    whop_user_id TEXT NULL,
    full_name TEXT NULL,
    avatar_url TEXT NULL,
    email TEXT NOT NULL,
    is_community_owner BOOLEAN DEFAULT false NOT NULL,
    community_id UUID NULL REFERENCES public.communities(id),
    personal_plan_id TEXT NULL,
    direct_subscription_plan TEXT NULL,
    discord_user_id TEXT UNIQUE NULL,
    referred_by_channel_id BIGINT NULL,  -- Which chatbot referred this user
    razorpay_customer_id TEXT NULL,       -- Linked Razorpay customer
    payout_details JSONB NULL,            -- Bank/UPI details for payouts
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Add Foreign Keys (now that all primary tables exist)
ALTER TABLE public.channels ADD CONSTRAINT fk_channels_user_id 
    FOREIGN KEY (user_id) REFERENCES public.profiles(id);
ALTER TABLE public.channels ADD CONSTRAINT fk_channels_creator_id 
    FOREIGN KEY (creator_id) REFERENCES public.profiles(id);
ALTER TABLE public.channels ADD CONSTRAINT fk_channels_community_id 
    FOREIGN KEY (community_id) REFERENCES public.communities(id);
ALTER TABLE public.communities ADD CONSTRAINT fk_communities_owner_user_id 
    FOREIGN KEY (owner_user_id) REFERENCES public.profiles(id);
ALTER TABLE public.profiles ADD CONSTRAINT fk_profiles_referred_by_channel 
    FOREIGN KEY (referred_by_channel_id) REFERENCES public.channels(id) ON DELETE SET NULL;

-- ============================================================================
-- 3. JOIN TABLES
-- ============================================================================

CREATE TABLE public.user_channels (
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    channel_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, channel_id)
);

CREATE TABLE public.user_communities (
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    community_id UUID NOT NULL REFERENCES public.communities(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, community_id)
);

-- ============================================================================
-- 4. USAGE & CHAT TABLES
-- ============================================================================

CREATE TABLE public.usage_stats (
    user_id UUID PRIMARY KEY REFERENCES public.profiles(id) ON DELETE CASCADE,
    queries_this_month INTEGER DEFAULT 0 NOT NULL,
    channels_processed INTEGER DEFAULT 0 NOT NULL,
    last_reset_date DATE DEFAULT CURRENT_DATE NOT NULL
);

CREATE TABLE public.chat_history (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    channel_name TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources JSONB NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================================
-- 5. EMBEDDINGS TABLE (Vector storage for RAG)
-- ============================================================================

CREATE TABLE public.embeddings (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    channel_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB NULL,
    user_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    source_id BIGINT NULL,  -- FK to data_sources (for multi-source chatbots)
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================================
-- 6. DATA SOURCES TABLE (Multi-source chatbot)
-- ============================================================================

CREATE TABLE public.data_sources (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    chatbot_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,     -- youtube, website, whatsapp
    source_url TEXT NULL,
    status TEXT DEFAULT 'pending' NOT NULL,  -- pending, processing, ready, failed
    progress INTEGER DEFAULT 0,
    metadata JSONB NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================================
-- 7. TELEGRAM INTEGRATION TABLES
-- ============================================================================

CREATE TABLE public.telegram_connections (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    app_user_id UUID UNIQUE NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    telegram_chat_id BIGINT NOT NULL,
    connection_code TEXT NOT NULL,
    is_active BOOLEAN DEFAULT false NOT NULL,
    telegram_username TEXT NULL,
    last_channel_context TEXT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

CREATE TABLE public.group_connections (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    owner_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    linked_channel_id BIGINT UNIQUE NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    connection_code TEXT NOT NULL,
    is_active BOOLEAN DEFAULT false NOT NULL,
    telegram_group_id BIGINT NULL,
    telegram_group_name TEXT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================================
-- 8. DISCORD INTEGRATION TABLES
-- ============================================================================

CREATE TABLE public.discord_bots (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    bot_token TEXT NOT NULL,
    youtube_channel_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    discord_server_id TEXT NULL,
    is_active BOOLEAN DEFAULT false NOT NULL,
    status TEXT DEFAULT 'offline' NOT NULL,  -- offline, connecting, online, error
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

CREATE TABLE public.discord_servers (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    server_id TEXT UNIQUE NOT NULL,
    linked_channel_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    owner_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================================
-- 9. WHATSAPP INTEGRATION TABLES (YCloud)
-- ============================================================================

CREATE TABLE public.whatsapp_configs (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    channel_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    phone_number_id TEXT NOT NULL,       -- YCloud phone number ID
    access_token TEXT NOT NULL,          -- Encrypted YCloud API key
    verify_token TEXT NULL,              -- Webhook signature secret
    is_active BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE (user_id, phone_number_id)
);

CREATE TABLE public.whatsapp_conversations (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    config_id BIGINT NOT NULL REFERENCES public.whatsapp_configs(id) ON DELETE CASCADE,
    customer_phone TEXT NOT NULL,
    customer_name TEXT NULL,
    last_message_at TIMESTAMPTZ NULL,
    message_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE (config_id, customer_phone)
);

CREATE TABLE public.whatsapp_messages (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    conversation_id BIGINT NOT NULL REFERENCES public.whatsapp_conversations(id) ON DELETE CASCADE,
    message_id TEXT NULL,       -- External message ID from YCloud
    direction TEXT NOT NULL,    -- inbound, outbound
    content TEXT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================================
-- 10. COMMERCE TABLES (Payments, Earnings, Payouts)
-- ============================================================================

CREATE TABLE public.razorpay_subscriptions (
    id TEXT PRIMARY KEY,   -- Razorpay subscription ID
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    plan_id TEXT NULL,
    status TEXT NULL,         -- active, cancelled, paused, etc.
    current_start TIMESTAMPTZ NULL,
    current_end TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

CREATE TABLE public.creator_earnings (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    creator_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    referred_user_id UUID NOT NULL REFERENCES public.profiles(id),
    channel_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    amount_usd NUMERIC(10, 2) NOT NULL,
    plan_id TEXT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

CREATE TABLE public.creator_payouts (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    creator_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    amount_usd NUMERIC(10, 2) NOT NULL,
    status TEXT DEFAULT 'pending' NOT NULL,  -- pending, processing, paid, rejected
    payout_destination_details JSONB NULL,    -- Snapshot of bank/UPI details
    requested_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    processed_at TIMESTAMPTZ NULL
);

-- ============================================================================
-- 11. RPC FUNCTIONS
-- ============================================================================

-- Community query usage increment
CREATE OR REPLACE FUNCTION public.increment_query_usage(p_community_id UUID, p_is_trial BOOLEAN)
RETURNS VOID AS $$
BEGIN
  IF p_community_id IS NOT NULL THEN
    IF p_is_trial THEN
      UPDATE public.communities
      SET trial_queries_used = trial_queries_used + 1
      WHERE id = p_community_id;
    ELSE
      UPDATE public.communities
      SET queries_used = queries_used + 1
      WHERE id = p_community_id;
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql;

-- Personal query usage increment (with monthly auto-reset)
CREATE OR REPLACE FUNCTION public.increment_personal_query_usage(p_user_id UUID)
RETURNS VOID AS $$
BEGIN
  UPDATE public.usage_stats
  SET 
    queries_this_month = CASE 
      WHEN last_reset_date < DATE_TRUNC('month', CURRENT_DATE) THEN 1
      ELSE queries_this_month + 1
    END,
    last_reset_date = CASE
      WHEN last_reset_date < DATE_TRUNC('month', CURRENT_DATE) THEN CURRENT_DATE
      ELSE last_reset_date
    END
  WHERE user_id = p_user_id;
END;
$$ LANGUAGE plpgsql;

-- Increment channel count
CREATE OR REPLACE FUNCTION public.increment_channel_count(p_user_id UUID)
RETURNS VOID AS $$
BEGIN
  UPDATE public.usage_stats
  SET channels_processed = channels_processed + 1
  WHERE user_id = p_user_id;
END;
$$ LANGUAGE plpgsql;

-- Decrement channel count
CREATE OR REPLACE FUNCTION public.decrement_channel_count(p_user_id UUID)
RETURNS VOID AS $$
BEGIN
  UPDATE public.usage_stats
  SET channels_processed = channels_processed - 1
  WHERE user_id = p_user_id AND channels_processed > 0;
END;
$$ LANGUAGE plpgsql;

-- Get channels by Discord ID
CREATE OR REPLACE FUNCTION public.get_channels_by_discord_id(p_discord_id TEXT)
RETURNS TABLE (id BIGINT, channel_name TEXT) AS $$
BEGIN
  RETURN QUERY
  SELECT c.id, c.channel_name
  FROM public.channels c
  JOIN public.user_channels uc ON c.id = uc.channel_id
  JOIN public.profiles p ON uc.user_id = p.id
  WHERE p.discord_user_id = p_discord_id;
END;
$$ LANGUAGE plpgsql;

-- Match embeddings (RAG similarity search)
CREATE OR REPLACE FUNCTION match_embeddings (
  query_embedding vector(1536),
  match_threshold float,
  match_count int,
  p_video_ids text[] DEFAULT NULL,
  p_channel_id bigint DEFAULT NULL
)
RETURNS TABLE (
  id bigint,
  metadata jsonb,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    embeddings.id,
    embeddings.metadata,
    1 - (embeddings.embedding <=> query_embedding) AS similarity
  FROM embeddings
  WHERE 
    (p_video_ids IS NULL OR embeddings.video_id = ANY(p_video_ids))
    AND (p_channel_id IS NULL OR embeddings.channel_id = p_channel_id)
    AND 1 - (embeddings.embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

-- Get add counts per channel (for creator dashboard stats)
CREATE OR REPLACE FUNCTION public.get_channel_add_counts(p_channel_ids BIGINT[])
RETURNS TABLE (channel_id BIGINT, add_count BIGINT) AS $$
BEGIN
  RETURN QUERY
  SELECT uc.channel_id, COUNT(*)::BIGINT AS add_count
  FROM public.user_channels uc
  WHERE uc.channel_id = ANY(p_channel_ids)
  GROUP BY uc.channel_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 12. ROW LEVEL SECURITY (RLS)
-- ============================================================================

ALTER TABLE public.chat_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow users to see their own chat history"
ON public.chat_history FOR SELECT
USING (auth.uid() = user_id);

CREATE POLICY "Allow users to insert their own chat history"
ON public.chat_history FOR INSERT
WITH CHECK (auth.uid() = user_id);

-- ============================================================================
-- 13. RECOMMENDED INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_channels_creator_id ON public.channels(creator_id);
CREATE INDEX IF NOT EXISTS idx_channels_user_id ON public.channels(user_id);
CREATE INDEX IF NOT EXISTS idx_channels_channel_name ON public.channels(channel_name);
CREATE INDEX IF NOT EXISTS idx_channels_status ON public.channels(status);
CREATE INDEX IF NOT EXISTS idx_embeddings_channel_id ON public.embeddings(channel_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_source_id ON public.embeddings(source_id);
CREATE INDEX IF NOT EXISTS idx_profiles_discord_user_id ON public.profiles(discord_user_id);
CREATE INDEX IF NOT EXISTS idx_profiles_razorpay_customer_id ON public.profiles(razorpay_customer_id);
CREATE INDEX IF NOT EXISTS idx_profiles_referred_by ON public.profiles(referred_by_channel_id);
CREATE INDEX IF NOT EXISTS idx_data_sources_chatbot_id ON public.data_sources(chatbot_id);
CREATE INDEX IF NOT EXISTS idx_chat_history_user_channel ON public.chat_history(user_id, channel_name);
CREATE INDEX IF NOT EXISTS idx_whatsapp_configs_user ON public.whatsapp_configs(user_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_configs_phone ON public.whatsapp_configs(phone_number_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_config ON public.whatsapp_conversations(config_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_conversation ON public.whatsapp_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_creator_earnings_creator ON public.creator_earnings(creator_id);
CREATE INDEX IF NOT EXISTS idx_creator_payouts_creator ON public.creator_payouts(creator_id);
CREATE INDEX IF NOT EXISTS idx_razorpay_subs_user ON public.razorpay_subscriptions(user_id);

-- ============================================================================
-- DONE! Your schema is ready.
-- ============================================================================
