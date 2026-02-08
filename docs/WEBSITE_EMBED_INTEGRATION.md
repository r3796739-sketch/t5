# 🌐 Website Embed Integration

## Overview

The Website Embed feature allows creators to add a floating chat widget to any website. Visitors can chat with the creator's AI directly on their website, and all statistics are tracked in the dashboard.

---

## ✅ What Was Created

### 1. **Dashboard Page** (`templates/embed_dashboard.html`)
- Channel selector dropdown
- Customization options (button color, position)
- Live preview of the widget
- Copy-paste embed code
- Installation instructions
- Widget statistics (Total Chats, Questions, Unique Visitors, Domains)

### 2. **Widget JavaScript** (`static/widget/yoppychat.js`)
- Self-contained, no dependencies
- Floating chat button
- Expandable chat popup
- Typing indicators
- Responsive design
- Dark/light mode compatible
- Tracks analytics events

### 3. **Backend Routes** (Added to `app.py`)
- `GET /integrations/embed` - Dashboard page
- `GET /api/widget/channel/<name>` - Fetch channel info
- `POST /api/widget/ask` - Handle questions
- `POST /api/widget/track` - Track analytics

### 4. **Database Migration** (`migrations/create_widget_analytics.sql`)
- `widget_analytics` table for storing embed statistics

---

## 🎨 Widget Features

### Appearance
- **Customizable button color**: Match creator's brand
- **Position options**: Left or right corner
- **Animated opening**: Smooth slide-in animation
- **Mobile responsive**: Works on all screen sizes

### Functionality
- **Real-time chat**: Instant AI responses
- **Typing indicator**: Shows when AI is thinking
- **Powered by YoppyChat**: Link back to platform
- **Channel branding**: Shows creator's avatar and name

---

## 📊 Statistics Tracked

| Metric | Description |
|--------|-------------|
| Total Chats | Number of chat sessions opened |
| Questions Asked | Total questions submitted |
| Unique Visitors | Number of unique users |
| Active Domains | Number of websites using the widget |

---

## 🔧 How It Works

### For Creators

1. Go to **Dashboard** → **Integrations** → **Website Embed**
2. Select the channel
3. Customize button color and position
4. Copy the embed code
5. Paste it before `</body>` on their website
6. View statistics in the dashboard

### Embed Code Example

```html
<!-- YoppyChat Widget -->
<script src="https://yoppychat.com/widget/yoppychat.js"></script>
<script>
  YoppyChat.init({
    channel: 'channel-name',
    color: '#ff9a56',
    position: 'right'
  });
</script>
```

### JavaScript API

```javascript
// Open the widget programmatically
YoppyChat.open();

// Close the widget
YoppyChat.close();

// Remove the widget completely
YoppyChat.destroy();
```

---

## 🗄️ Database Schema

```sql
CREATE TABLE widget_analytics (
    id UUID PRIMARY KEY,
    channel_id INTEGER REFERENCES channels(id),
    domain TEXT NOT NULL,
    question_count INTEGER DEFAULT 0,
    chat_count INTEGER DEFAULT 0,
    unique_visitors INTEGER DEFAULT 0,
    last_activity TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (channel_id, domain)
);
```

---

## 🚀 Setup Instructions

### 1. Run Database Migration

```sql
-- Run in Supabase SQL Editor or via psql
\i migrations/create_widget_analytics.sql
```

### 2. Configure CORS (if needed)

Ensure your server allows cross-origin requests from any domain for the widget endpoints:

```python
# In app.py or via Flask-CORS
from flask_cors import CORS, cross_origin

@app.route('/api/widget/ask', methods=['POST', 'OPTIONS'])
@cross_origin()
def widget_ask_question():
    # ...
```

### 3. Integrate with AI Logic

Update the `generate_widget_answer` function in `app.py` to use your existing AI logic:

```python
def generate_widget_answer(channel_id, question):
    # Replace with your actual AI implementation
    from ask_video import generate_answer
    return generate_answer(channel_id, question)
```

---

## 🔒 Security Considerations

1. **Rate Limiting**: Consider adding rate limits to prevent abuse
2. **Domain Whitelist**: Optionally allow creators to whitelist domains
3. **Content Moderation**: Filter harmful questions/responses
4. **Analytics Privacy**: Only track domain, not full URLs or user data

---

## 📈 Future Enhancements

- [ ] Custom welcome message per channel
- [ ] Domain whitelist/blacklist
- [ ] Widget message history (persist across sessions)
- [ ] Custom avatar and name override
- [ ] Multiple language support
- [ ] Offline mode with cached responses
- [ ] Email capture for leads
- [ ] Integration with CRM systems

---

## 🎯 Benefits for Creators

1. **More Touchpoints**: Engage visitors directly on their website
2. **24/7 Support**: AI answers questions even when offline
3. **Lead Generation**: Capture interested visitors
4. **Analytics**: See what visitors ask about
5. **Brand Consistency**: Matches their website with custom colors

---

## 📁 Files Created/Modified

| File | Type | Description |
|------|------|-------------|
| `templates/embed_dashboard.html` | NEW | Dashboard UI |
| `static/widget/yoppychat.js` | NEW | Embeddable widget |
| `templates/dashboard.html` | MODIFIED | Added embed card |
| `app.py` | MODIFIED | Added routes |
| `migrations/create_widget_analytics.sql` | NEW | Database schema |
| `docs/WEBSITE_EMBED_INTEGRATION.md` | NEW | This documentation |

---

*Website Embed Integration - Making creators' AI available everywhere their audience visits.*
