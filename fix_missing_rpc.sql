-- Fix for "Could not find the function public.get_visible_channels(p_community_id, p_user_id) in the schema cache"
-- Run this in your Supabase SQL Editor.

CREATE OR REPLACE FUNCTION public.get_visible_channels(p_user_id UUID, p_community_id UUID DEFAULT NULL)
RETURNS SETOF public.channels AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT c.*
    FROM public.channels c
    LEFT JOIN public.user_channels uc ON c.id = uc.channel_id
    WHERE 
        -- 1. Channels created by the user
        c.creator_id = p_user_id 
        OR 
        -- 2. Channels explicitly linked to the user
        uc.user_id = p_user_id
        OR
        -- 3. Channels shared in the active community (if one is provided)
        (p_community_id IS NOT NULL AND c.community_id = p_community_id AND c.is_shared = true);
END;
$$ LANGUAGE plpgsql;
