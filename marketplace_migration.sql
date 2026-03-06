-- Migration script for Chatbot Marketplace (Build & Transfer)

-- 1. Create the chatbot_transfers table
CREATE TABLE public.chatbot_transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    buyer_id UUID NULL REFERENCES public.profiles(id) ON DELETE SET NULL,
    chatbot_id BIGINT NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    transfer_code TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'active', 'cancelled'
    query_limit_monthly INTEGER NOT NULL,
    platform_fee_monthly INTEGER NOT NULL, -- in paise
    creator_price_monthly INTEGER NOT NULL, -- in paise
    razorpay_subscription_id TEXT NULL,
    queries_used_this_month INTEGER NOT NULL DEFAULT 0,
    last_query_reset DATE NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Add indexes for common queries
CREATE INDEX idx_chatbot_transfers_creator_id ON public.chatbot_transfers(creator_id);
CREATE INDEX idx_chatbot_transfers_buyer_id ON public.chatbot_transfers(buyer_id);
CREATE INDEX idx_chatbot_transfers_transfer_code ON public.chatbot_transfers(transfer_code);
CREATE INDEX idx_chatbot_transfers_chatbot_id ON public.chatbot_transfers(chatbot_id);

-- 2. Create the creator_marketplace_earnings table
CREATE TABLE public.creator_marketplace_earnings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transfer_id UUID NOT NULL REFERENCES public.chatbot_transfers(id) ON DELETE CASCADE,
    creator_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    gross_amount INTEGER NOT NULL, -- in paise
    platform_fee INTEGER NOT NULL, -- in paise
    creator_amount INTEGER NOT NULL, -- in paise
    status TEXT NOT NULL DEFAULT 'credited', -- 'credited', 'withdrawn'
    payment_date TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Add indexes
CREATE INDEX idx_creator_marketplace_earnings_creator_id ON public.creator_marketplace_earnings(creator_id);
CREATE INDEX idx_creator_marketplace_earnings_transfer_id ON public.creator_marketplace_earnings(transfer_id);

-- 3. Trigger to update `updated_at` on chatbot_transfers
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_chatbot_transfers_modtime
BEFORE UPDATE ON public.chatbot_transfers
FOR EACH ROW
EXECUTE FUNCTION update_modified_column();

-- 4. RPC Function for atomic query increment
CREATE OR REPLACE FUNCTION increment_marketplace_query(p_transfer_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
  v_limit INTEGER;
  v_used INTEGER;
BEGIN
  -- Lock the row to prevent race conditions
  SELECT query_limit_monthly, queries_used_this_month
  INTO v_limit, v_used
  FROM public.chatbot_transfers 
  WHERE id = p_transfer_id AND status = 'active'
  FOR UPDATE;

  IF NOT FOUND OR v_used >= v_limit THEN
    RETURN FALSE;
  END IF;

  UPDATE public.chatbot_transfers
  SET queries_used_this_month = queries_used_this_month + 1
  WHERE id = p_transfer_id;

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- 5. Enable RLS (Row Level Security) - basic policies
ALTER TABLE public.chatbot_transfers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.creator_marketplace_earnings ENABLE ROW LEVEL SECURITY;

-- Allow creators to see their own transfers and earnings
CREATE POLICY "Creators can view their own transfers" ON public.chatbot_transfers
FOR SELECT USING (auth.uid() = creator_id);

CREATE POLICY "Buyers can view their own transfers" ON public.chatbot_transfers
FOR SELECT USING (auth.uid() = buyer_id);

-- Allow public to view pending transfers by transfer_code
CREATE POLICY "Public can view pending transfers by code" ON public.chatbot_transfers
FOR SELECT USING (status = 'pending');

CREATE POLICY "Creators can view their own marketplace earnings" ON public.creator_marketplace_earnings
FOR SELECT USING (auth.uid() = creator_id);

-- Grant permissions to authenticated role (and anon for public viewing)
GRANT SELECT ON public.chatbot_transfers TO authenticated, anon;
GRANT INSERT, UPDATE ON public.chatbot_transfers TO service_role; -- App backend does inserts/updates
GRANT SELECT ON public.creator_marketplace_earnings TO authenticated;
GRANT INSERT, UPDATE ON public.creator_marketplace_earnings TO service_role;
