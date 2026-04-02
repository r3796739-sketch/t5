import os
import uuid
import requests
import logging
from werkzeug.utils import secure_filename
from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, flash
from functools import wraps
from utils.supabase_client import get_supabase_admin_client

google_reviews_bp = Blueprint('google_reviews', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            return redirect(url_for('channel') + '?login=1')
        return f(*args, **kwargs)
    return decorated_function


def _fetch_and_cache_reviews_context(place_id, business_name, user_id, supabase, business_description="", sort_mode="newest", settings_id=None):
    """
    Fetches top 5 5-star reviews from Google Places API and combines them 
    with the business description to create a comprehensive SEO context.
    """
    google_api_key = os.environ.get('GOOGLE_PLACES_API_KEY')
    reviews_context = ""

    if place_id and google_api_key:
        try:
            res = requests.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={"place_id": place_id, "key": google_api_key, "fields": "reviews", "reviews_sort": sort_mode},
                timeout=10
            )
            place_data = res.json()
            if place_data.get('status') == 'OK' and 'reviews' in place_data.get('result', {}):
                # Fetch only purely 5-star reviews with actual text
                good_reviews = [r['text'] for r in place_data['result']['reviews'] if r.get('rating', 0) == 5 and r.get('text', '').strip()]
                if not good_reviews:
                    # Fallback to 4+ stars if no 5-star textual reviews exist
                    good_reviews = [r['text'] for r in place_data['result']['reviews'] if r.get('rating', 0) >= 4 and r.get('text', '').strip()]
                    
                if good_reviews:
                    reviews_context = "Past 5-star customer reviews: " + " | ".join(good_reviews[:5])
        except Exception as e:
            logging.warning(f"[Google Reviews] Could not fetch Places reviews: {e}")

    # Combine business description and reviews for the AI
    final_context = ""
    if business_description:
        final_context += f"Business Description & SEO Target Keywords: {business_description}\n\n"
    if reviews_context:
        final_context += f"{reviews_context}"

    if not final_context.strip():
        final_context = f"Business context: A local business named {business_name}"

    try:
        query = supabase.table('google_review_settings').update(
            {'cached_reviews_context': final_context}
        ).eq('user_id', user_id)
        if settings_id:
            query = query.eq('id', settings_id)
        query.execute()
    except Exception as e:
        logging.warning(f"[Google Reviews] Could not cache reviews context: {e}")

    return final_context

def _gr_generate_fast(prompt: str, llm_provider: str, llm_model: str, api_key: str) -> str:
    """
    Non-streaming single LLM call optimised for short review text generation.
    Uses generate_content(stream=False) for Gemini which avoids SSE overhead.
    Falls back to collecting stream chunks for other providers.
    """
    try:
        if llm_provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(llm_model)

            # Thinking models (e.g. gemini-3-flash-preview) consume hidden reasoning
            # tokens that count against max_output_tokens.  With a small cap the actual
            # review text gets crowded out and truncated.
            # Fix: disable thinking (budget=0) or boost the cap so there is room for both.
            gen_config_kwargs = {
                'max_output_tokens': 512,
                'temperature': 0.9,
            }
            try:
                thinking_config = genai.types.ThinkingConfig(thinking_budget=0)
                gen_config_kwargs['thinking_config'] = thinking_config
            except (AttributeError, TypeError):
                # SDK doesn't support ThinkingConfig — give the model plenty of headroom
                gen_config_kwargs['max_output_tokens'] = 4096

            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(**gen_config_kwargs),
                stream=False
            )
            return response.text.strip() if response.text else ""
        else:
            from utils.qa_utils import LLM_STREAM_PROVIDER_MAP
            stream_func = LLM_STREAM_PROVIDER_MAP.get(llm_provider)
            if stream_func and api_key:
                return "".join(stream_func(prompt, llm_model, api_key)).strip()
    except Exception as e:
        logging.warning(f"[Google Reviews] _gr_generate_fast failed: {e}")
    return ""



def _generate_review_text(business_name, reviews_context):
    """
    Generates a unique AI review using the platform's configured LLM.
    Uses a non-streaming call for speed.
    """
    from utils.qa_utils import _get_api_key, LLM_STREAM_PROVIDER_MAP

    prompt = (
        f"Write a unique, authentic-sounding 5-star Google review (2-3 sentences) for a business called '{business_name}'. "
        f"{reviews_context} "
        f"Make it feel personal and genuine — vary the style and focus each time. Do NOT use generic phrases like 'highly recommended'. "
        f"Output ONLY the review text, nothing else."
    )

    llm_provider = (os.environ.get('GR_LLM_PROVIDER') or os.environ.get('LLM_PROVIDER', 'openai')).lower()
    llm_model = os.environ.get('GR_LLM_MODEL') or os.environ.get('MODEL_NAME', 'gpt-3.5-turbo')
    api_key = _get_api_key(llm_provider)

    generated_text = _gr_generate_fast(prompt, llm_provider, llm_model, api_key)

    if not generated_text or "Error:" in generated_text:
        generated_text = f"I had a wonderful experience with {business_name}. The service was top-notch and I'd definitely recommend them!"

    return generated_text


import threading
from flask import current_app

def _send_limit_email_async(app, user_id):
    with app.app_context():
        try:
            from app import mail
            from flask_mail import Message
            from utils.supabase_client import get_supabase_admin_client
            supabase = get_supabase_admin_client()
            user_res = supabase.table('profiles').select('email, full_name').eq('id', user_id).maybe_single().execute()
            if not user_res or not user_res.data:
                return
            user_email = user_res.data.get('email')
            name = user_res.data.get('full_name') or 'Business Owner'
            
            msg = Message(
                subject="Action Required: Review AI Limit Reached",
                recipients=[user_email],
                html=f"<h3>Hi {name},</h3>\n                <p>You have reached your monthly credit limit for generating AI Google Review suggestions.</p>\n                <p>Please <a href='https://app.yoppychat.com/pricing'>upgrade your plan</a> to continue collecting high-quality AI-generated reviews on auto-pilot.</p>\n                <br>\n                <p>Best regards,<br>The YoppyChat Team</p>"
            )
            mail.send(msg)
        except Exception as e:
            logging.error(f"Failed to send Google Review limit email for user {user_id}: {e}")

@google_reviews_bp.route('/dashboard/google-reviews', methods=['GET', 'POST'])
def google_reviews_dashboard():
    from utils.subscription_utils import get_user_status
    
    if 'user' not in session:
        if request.method == 'POST':
            return redirect(url_for('channel') + '?login=1')
        return render_template(
            'google_reviews_dashboard.html',
            settings_list=[],
            feedbacks=[],
            user_id=None,
            max_businesses=0,
            stats={},
            needs_login=True,
            user_status=None,
        )

    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    user_status = get_user_status(user_id)
    is_creator = False
    if user_status:
        is_creator = (user_status.get('plan_name', '').lower() == 'creator')
    max_businesses = 10 if is_creator else 1

    if request.method == 'POST':
        settings_id = request.form.get('settings_id', '').strip()
        business_name = request.form.get('business_name', '').strip()
        place_id = request.form.get('place_id', '').strip()
        business_description = request.form.get('business_description', '').strip()
        minimum_stars = request.form.get('minimum_stars', '4')

        try:
            minimum_stars = int(minimum_stars)
        except ValueError:
            minimum_stars = 4

        data = {
            'user_id': user_id,
            'business_name': business_name,
            'place_id': place_id,
            'business_description': business_description,
            'minimum_stars': minimum_stars
        }

        # Handle logo upload
        logo_file = request.files.get('business_logo')
        if logo_file and logo_file.filename:
            allowed_ext = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
            ext = logo_file.filename.rsplit('.', 1)[-1].lower() if '.' in logo_file.filename else ''
            if ext in allowed_ext:
                logos_dir = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'logos')
                os.makedirs(logos_dir, exist_ok=True)
                safe_name = f"{uuid.uuid4().hex}.{ext}"
                logo_file.save(os.path.join(logos_dir, safe_name))
                data['logo_url'] = f"/static/uploads/logos/{safe_name}"
        elif settings_id:
            # Preserve existing logo on edit if no new file uploaded
            existing_row = supabase.table('google_review_settings').select('logo_url').eq('id', settings_id).maybe_single().execute()
            if existing_row and existing_row.data and existing_row.data.get('logo_url'):
                data['logo_url'] = existing_row.data['logo_url']

        if settings_id:
            # Update existing
            supabase.table('google_review_settings').update(data).eq('id', settings_id).eq('user_id', user_id).execute()
            s_id_int = int(settings_id) if settings_id.isdigit() else None
            if s_id_int and s_id_int in _public_settings_cache:
                del _public_settings_cache[s_id_int]
            active_settings_id = s_id_int
        else:
            # Check limits before creating new
            existing = supabase.table('google_review_settings').select('id', count='exact').eq('user_id', user_id).execute()
            current_count = existing.count or 0
            if current_count >= max_businesses:
                flash(f'Plan limit reached: You can only add {max_businesses} Google Business{"es" if max_businesses > 1 else ""}. Please upgrade to the Creator Plan for more.', 'error')
                return redirect(url_for('google_reviews.google_reviews_dashboard'))

            insert_res = supabase.table('google_review_settings').insert(data).execute()
            active_settings_id = insert_res.data[0]['id'] if insert_res.data else None

        # Cache context — scoped to this specific business
        _fetch_and_cache_reviews_context(place_id, business_name, user_id, supabase, business_description, settings_id=active_settings_id)

        flash('Google Review Settings saved! Your review link is ready.', 'success')
        return redirect(url_for('google_reviews.google_reviews_dashboard'))

    # Load all settings owned by this user (as creator)
    settings_res = supabase.table('google_review_settings').select('*').eq('user_id', user_id).execute()
    settings_list = settings_res.data if settings_res.data else []

    # Also load Google Review businesses the user has bought via the marketplace
    # (they are the active buyer in chatbot_transfers with a google_review_id)
    buyer_transfers_res = supabase.table('chatbot_transfers').select(
        'google_review_id'
    ).eq('buyer_id', user_id).eq('status', 'active').not_.is_('google_review_id', 'null').execute()
    
    if buyer_transfers_res.data:
        bought_gr_ids = [t['google_review_id'] for t in buyer_transfers_res.data]
        # Exclude any already owned (in case of edge cases)
        owned_ids = {s['id'] for s in settings_list}
        ids_to_fetch = [gid for gid in bought_gr_ids if gid not in owned_ids]
        if ids_to_fetch:
            bought_settings_res = supabase.table('google_review_settings').select('*').in_('id', ids_to_fetch).execute()
            if bought_settings_res.data:
                settings_list = settings_list + bought_settings_res.data

    feedback_res = supabase.table('google_reviews_feedback').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
    feedbacks = feedback_res.data if feedback_res.data else []

    stats = {s.get('id'): {1:0, 2:0, 3:0, 4:0, 5:0, 'total':0, 'avg':0.0} for s in settings_list}
    for fb in feedbacks:
        s_id = fb.get('settings_id')
        r = fb.get('rating', 0)
        if s_id in stats and r in stats[s_id]:
             stats[s_id][r] += 1
             stats[s_id]['total'] += 1
             
    for s_id in stats:
        total = stats[s_id]['total']
        if total > 0:
            sum_ratings = sum(k * stats[s_id][k] for k in range(1, 6))
            stats[s_id]['avg'] = round(sum_ratings / total, 1)

    captured_feedbacks = [fb for fb in feedbacks if (fb.get('rating') or 0) < 4]
    public_reviews = [fb for fb in feedbacks if (fb.get('rating') or 0) >= 4]

    return render_template(
        'google_reviews_dashboard.html',
        settings_list=settings_list,
        feedbacks=feedbacks,
        captured_feedbacks=captured_feedbacks,
        public_reviews=public_reviews,
        user_id=user_id,
        max_businesses=max_businesses,
        stats=stats,
        user_status=user_status,
    )


@google_reviews_bp.route('/dashboard/google-reviews/delete/<int:settings_id>', methods=['POST'])
@login_required
def delete_business(settings_id):
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    supabase.table('google_review_settings').delete().eq('id', settings_id).eq('user_id', user_id).execute()
    flash('Business deleted successfully.', 'success')
    return redirect(url_for('google_reviews.google_reviews_dashboard'))


@google_reviews_bp.route('/dashboard/google-reviews/refresh-context', methods=['POST'])
@login_required
def refresh_reviews_context():
    data = request.get_json() or {}
    settings_id = data.get('settings_id')
    sort_mode = data.get('sort_mode', 'newest')
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    if not settings_id:
        return jsonify({'status': 'error', 'message': 'Settings ID required'}), 400
        
    settings_res = supabase.table('google_review_settings').select('business_name, place_id, business_description').eq('id', settings_id).eq('user_id', user_id).execute()
    if not settings_res.data:
        return jsonify({'status': 'error', 'message': 'No settings found'}), 404
        
    s = settings_res.data[0]
    context = _fetch_and_cache_reviews_context(s['place_id'], s['business_name'], user_id, supabase, s.get('business_description', ''), sort_mode, settings_id=settings_id)
    if context:
        return jsonify({'status': 'success', 'message': f'Fetched {len(context.split("|"))} real reviews from Google!', 'context': context})
    else:
        return jsonify({'status': 'warning', 'message': 'No public reviews found on Google yet, or API key is not configured.'})


_public_settings_cache = {}

@google_reviews_bp.route('/r/<business_name>/<int:settings_id>', methods=['GET'])
def public_review_page(business_name, settings_id):
    import time
    now = time.time()
    
    # Try fetching perfectly optimized cached response (0ms latency bypass)
    if settings_id in _public_settings_cache:
        cached_data, timestamp = _public_settings_cache[settings_id]
        if now - timestamp < 3600:  # Cache for 1 hour aggressively!
            return render_template('public_review_page.html', settings=cached_data, business_identifier=cached_data['user_id'])
            
    supabase = get_supabase_admin_client()
    settings_res = supabase.table('google_review_settings').select('*').eq('id', settings_id).execute()
    if not settings_res.data:
        return "Not found", 404

    settings = settings_res.data[0]
    _public_settings_cache[settings_id] = (settings, now)
    
    return render_template('public_review_page.html', settings=settings, business_identifier=settings['user_id'])


@google_reviews_bp.route('/api/google-reviews/submit-feedback', methods=['POST'])
def submit_feedback():
    data = request.get_json()
    business_identifier = data.get('business_identifier')
    rating = data.get('rating')
    customer_name = data.get('name', 'Anonymous')
    customer_email = data.get('email', '')
    feedback_text = data.get('feedback', '')

    settings_id = data.get('settings_id')

    if not business_identifier or not rating:
        return jsonify({'status': 'error', 'message': 'Missing data'}), 400

    supabase = get_supabase_admin_client()
    
    insert_data = {
        'user_id': business_identifier,
        'rating': int(rating),
        'customer_name': customer_name,
        'customer_email': customer_email,
        'feedback_text': feedback_text
    }
    
    if settings_id:
        insert_data['settings_id'] = settings_id
        
    supabase.table('google_reviews_feedback').insert(insert_data).execute()

    return jsonify({'status': 'success'})


@google_reviews_bp.route('/api/google-reviews/generate-review', methods=['POST'])
def generate_review():
    from utils.db_utils import check_bot_query_allowed, record_bot_query_usage

    data = request.get_json()
    settings_id = data.get('settings_id')
    user_id = data.get('business_identifier') # Passed from frontend for backward compat

    if not settings_id:
        return jsonify({'status': 'error', 'message': 'Settings ID required'}), 400

    # 1. Deduct 1 credit & verify limits, checking marketplace allocation first
    allowed, err_msg, _, seller_id_to_charge = check_bot_query_allowed(user_id, google_review_settings_id=settings_id)
    if not allowed:
        app_instance = current_app._get_current_object()
        threading.Thread(target=_send_limit_email_async, args=(app_instance, user_id)).start()
        return jsonify({'status': 'error', 'message': err_msg}), 403

    # Deduct from seller if marketplace allocation is active, otherwise buyer
    if seller_id_to_charge:
        record_bot_query_usage(seller_id_to_charge)
    else:
        record_bot_query_usage(user_id)

    supabase = get_supabase_admin_client()
    settings_res = supabase.table('google_review_settings').select('business_name, cached_reviews_context').eq('id', settings_id).limit(1).execute()
    if not settings_res.data:
        return jsonify({'status': 'error', 'message': 'Settings not found'}), 404

    settings = settings_res.data[0]
    business_name = settings.get('business_name', 'this business')
    reviews_context = settings.get('cached_reviews_context') or ""

    # Generate 4 reviews with different length instructions
    length_styles = [
        "Write it in 1 short, punchy sentence (max 12 words).",
        "Write it in 2 concise sentences (25-35 words total).",
        "Write it in 2-3 sentences with a real-feeling specific detail (40-55 words).",
        "Write it in 3-4 warm, personal sentences mentioning why you'd return (60-75 words).",
    ]

    # Build a grounded context block
    if reviews_context:
        context_block = (
            f"Here is information about the business '{business_name}':\n\n{reviews_context}\n\n"
            f"Your task is to write SEO-optimized review ideas that a real customer could copy-paste as their own 5-star Google review. "
            f"If Business Description or target SEO keywords are provided, seamlessly weave them into the review naturally to rank higher on local search. "
            f"Use the themes, sentiments, and specific details from the past customer reviews if provided to sound deeply authentic.\n\n"
        )
    else:
        context_block = (
            f"You are writing an SEO optimized 5-star review for a business called '{business_name}'. "
            f"You do NOT know what specific services they offer, so keep the review focused on generic but strong positive sentiment. "
            f"Do NOT invent any specific destinations, trips, products, or services — stay vague about the exact details.\n\n"
        )

    from utils.qa_utils import _get_api_key, LLM_STREAM_PROVIDER_MAP
    from concurrent.futures import ThreadPoolExecutor, as_completed
    llm_provider = (os.environ.get('GR_LLM_PROVIDER') or os.environ.get('LLM_PROVIDER', 'openai')).lower()
    llm_model = os.environ.get('GR_LLM_MODEL') or os.environ.get('MODEL_NAME', 'gpt-3.5-turbo')
    api_key = _get_api_key(llm_provider)
    stream_func = LLM_STREAM_PROVIDER_MAP.get(llm_provider)

    fallbacks = [
        f"Loved {business_name}!",
        f"Great experience with {business_name}. Would definitely recommend.",
        f"{business_name} exceeded my expectations. The service was smooth and the team was genuinely helpful.",
        f"I've been to many places, but {business_name} stands out. The attention to detail, warm service, and overall experience made it truly memorable. Will absolutely be coming back!"
    ]

    def _generate_one(i, style):
        prompt = (
            f"{context_block}"
            f"Now write ONE unique, highly-authentic 5-star Google review. {style} "
            f"Sound like a genuine happy customer. Avoid robotic phrasing or marketing speak. "
            f"Do NOT invent specific places or names not found in the context. "
            f"Do NOT mention star ratings directly. Output ONLY the raw review text, nothing else."
        )
        text = _gr_generate_fast(prompt, llm_provider, llm_model, api_key)
        return i, text if (text and "Error:" not in text) else fallbacks[i]

    # Run all 4 LLM calls in parallel — reduces latency from 4× to ~1× a single call
    results = [None] * len(length_styles)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_generate_one, i, style): i for i, style in enumerate(length_styles)}
        for future in as_completed(futures):
            i, text = future.result()
            results[i] = text

    reviews = results

    return jsonify({'status': 'success', 'reviews': reviews})
