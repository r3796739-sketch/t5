"""
utils/seo_utils.py
------------------
Programmatic SEO automation for YoppyChat creator landing pages.

Phase 1 — Keyword Harvesting:
    Pings Google Autocomplete for real search queries users type for a creator.

Phase 2 — LLM Metadata Generation:
    Uses the SAME LLM provider configured in .env (via qa_utils helpers) to
    produce an optimised Title, Meta Description, and H1.

    Provider priority (read from .env):
        SEO_LLM_PROVIDER / SEO_MODEL_NAME   ← dedicated override (optional)
        SUMMARY_LLM_PROVIDER / SUMMARY_MODEL_NAME ← reuses the fast summary model
        LLM_PROVIDER / MODEL_NAME           ← final fallback (main chat model)

    No OPENAI_API_KEY is required. Works with Gemini, Groq, or any OpenAI-compatible API.
"""

import logging
import requests
import json
import os
import re

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Phase 1: Google Autocomplete keyword harvesting
# ──────────────────────────────────────────────────────────────

AUTOCOMPLETE_URL = "http://suggestqueries.google.com/complete/search"

QUERY_TEMPLATES = [
    "{name} AI",
    "chat with {name}",
    "ask {name}",
    "{name} ai chatbot",
    "{name} advice",
]


def harvest_autocomplete_keywords(creator_name: str, max_results: int = 10) -> list:
    """
    Fetches real Google Autocomplete suggestions for a creator.
    Returns a de-duplicated list of up to max_results lowercase suggestion strings.
    """
    suggestions = set()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    for template in QUERY_TEMPLATES:
        if len(suggestions) >= max_results:
            break
        query = template.format(name=creator_name)
        try:
            resp = requests.get(
                AUTOCOMPLETE_URL,
                params={"output": "chrome", "hl": "en", "q": query},
                headers=headers,
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            # chrome output: [query, [suggestion1, suggestion2, ...], ...]
            raw_suggestions = data[1] if len(data) > 1 else []
            for s in raw_suggestions:
                suggestions.add(s.lower().strip())
                if len(suggestions) >= max_results:
                    break
        except Exception as exc:
            logger.warning(f"[SEO] Autocomplete fetch failed for query '{query}': {exc}")

    result = list(suggestions)[:max_results]
    logger.info(f"[SEO] Harvested {len(result)} autocomplete keywords for '{creator_name}'")
    return result


# ──────────────────────────────────────────────────────────────
# Phase 2: LLM-powered SEO metadata generation
# (uses the same provider/model as the rest of the app)
# ──────────────────────────────────────────────────────────────

def _resolve_seo_llm():
    """
    Determines the LLM provider, model, and API key to use for SEO generation.
    Priority:
      1. SEO_LLM_PROVIDER / SEO_MODEL_NAME  (dedicated override)
      2. SUMMARY_LLM_PROVIDER / SUMMARY_MODEL_NAME  (reuse fast summary model)
      3. LLM_PROVIDER / MODEL_NAME  (main chat model)
    Returns (provider, model, api_key, base_url).
    """
    # Priority 1: dedicated SEO override
    provider = os.environ.get('SEO_LLM_PROVIDER', '').lower()
    model    = os.environ.get('SEO_MODEL_NAME', '')

    # Priority 2: reuse the fast summary model
    if not provider:
        provider = os.environ.get('SUMMARY_LLM_PROVIDER',
                    os.environ.get('LLM_PROVIDER', 'gemini')).lower()
    if not model:
        model = os.environ.get('SUMMARY_MODEL_NAME',
                 os.environ.get('MODEL_NAME', ''))

    # Resolve API key the same way qa_utils does
    key_map = {
        'openai': 'OPENAI_API_KEY',
        'gemini': 'GEMINI_API_KEY2',
        'groq':   'GROQ_API_KEY',
    }
    env_var = key_map.get(provider)
    api_key = os.environ.get(env_var, '') if env_var else ''
    if provider == 'gemini' and not api_key:
        api_key = os.environ.get('GEMINI_API_KEY', '')

    # Base URL (only relevant for openai-compatible / custom endpoints)
    if provider == 'groq':
        base_url = 'https://api.groq.com/openai/v1'
    else:
        base_url = os.environ.get('OPENAI_API_BASE_URL', None)

    return provider, model, api_key, base_url


def _call_llm(prompt: str, provider: str, model: str, api_key: str, base_url: str) -> str:
    """
    Calls the configured LLM for a single, non-streamed response.
    Mirrors the logic already used in qa_utils._get_openai_answer_non_stream.
    Returns raw text or empty string on failure.
    """
    try:
        if provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model_name = f"models/{model}" if not model.startswith('models/') else model
            gemini_model = genai.GenerativeModel(model_name)
            config = genai.types.GenerationConfig(temperature=0.4, max_output_tokens=350)
            response = gemini_model.generate_content(prompt, generation_config=config)
            return response.text if response.text else ''

        if provider == 'groq':
            headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
            payload = {
                'model': model,
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.4,
                'max_tokens': 350,
            }
            resp = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers=headers, json=payload, timeout=20
            )
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content']

        # Fallback: OpenAI-compatible endpoint
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=350,
            temperature=0.4,
        )
        return response.choices[0].message.content or ''

    except Exception as exc:
        logger.error(f"[SEO] LLM call failed (provider={provider}, model={model}): {exc}")
        return ''


SEO_PROMPT_TEMPLATE = """You are an expert SEO copywriter for YoppyChat — a platform where users chat with AI clones of their favourite YouTubers trained on every video they've ever made.

Creator name: {creator_name}

Real Google search queries users actually type about this creator:
{keyword_list}

Write the optimal SEO metadata for the creator's YoppyChat landing page at yoppychat.com/c/{slug}.
Your goal is to match user search intent as precisely as possible. Prioritise queries related to chatting, asking questions, and getting advice from this creator's AI.

Rules:
- seo_title: max 60 characters, include creator name, mention "AI" or "Chat"
- seo_meta_description: 140–160 characters, compelling, includes primary keyword naturally
- seo_h1: max 70 characters, natural language, different wording from title

Return ONLY valid JSON with exactly these three keys:
{{"seo_title": "...", "seo_meta_description": "...", "seo_h1": "..."}}"""


def _extract_json(raw: str) -> dict | None:
    """Robustly pull the first JSON object out of any LLM response."""
    if not raw:
        return None
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Extract first {...} block
    match = re.search(r'\{.*?\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def generate_seo_metadata(creator_name: str) -> dict:
    """
    Main entry point.
    1. Harvests Google Autocomplete keywords for the creator.
    2. Feeds them to the configured LLM (same provider as the rest of the app).
    3. Returns a dict with seo_title, seo_meta_description, seo_h1.
       Falls back to sensible defaults if anything fails.
    """
    slug = creator_name.lower().replace(' ', '-')

    # --- Phase 1: Harvest keywords ---
    keywords = harvest_autocomplete_keywords(creator_name)

    # --- Phase 2: LLM generation ---
    seo_data = None
    provider, model, api_key, base_url = _resolve_seo_llm()

    if not api_key:
        logger.warning(
            f"[SEO] No API key found for provider '{provider}'. "
            "Using fallback metadata. Set SEO_LLM_PROVIDER / SEO_MODEL_NAME in .env to override."
        )
    elif keywords:
        keyword_list_str = '\n'.join(f'- {kw}' for kw in keywords)
        prompt = SEO_PROMPT_TEMPLATE.format(
            creator_name=creator_name,
            keyword_list=keyword_list_str,
            slug=slug,
        )
        logger.info(f"[SEO] Calling provider='{provider}' model='{model}' for '{creator_name}'")
        raw = _call_llm(prompt, provider, model, api_key, base_url)
        parsed = _extract_json(raw)
        if parsed and all(k in parsed for k in ('seo_title', 'seo_meta_description', 'seo_h1')):
            seo_data = parsed
            logger.info(
                f"[SEO] Generated metadata for '{creator_name}' | "
                f"title={parsed['seo_title']!r}"
            )
        else:
            logger.warning(f"[SEO] Could not parse valid JSON from LLM response: {raw[:200]!r}")

    # --- Fallback: safe defaults ---
    if not seo_data:
        logger.info(f"[SEO] Using fallback metadata for '{creator_name}'")
        seo_data = {
            'seo_title': f'Chat with {creator_name} AI — Ask Anything | YoppyChat',
            'seo_meta_description': (
                f'Chat with an AI clone of {creator_name} trained on their entire YouTube library. '
                f'Get answers in their voice, 24/7. Free to try.'
            )[:160],
            'seo_h1': f"Chat with {creator_name}'s AI — Ask Anything, Anytime",
        }

    return seo_data
