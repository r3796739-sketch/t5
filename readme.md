<p align="center">
  <img src="https://raw.githubusercontent.com/user-attachments/assets/941e699b-4654-47f4-8a19-93e114777a94/primiry__logo.png" alt="YoppyChat AI Logo" width="350">
</p>

<h1 align="center">YoppyChat AI</h1>

<p align="center">
  <strong>Turn YouTube Channels into Scalable, Conversational AI Personas.</strong>
  <br />
  <em>Bridging the gap between content creators and their communities through deeply personal, AI-driven engagement.</em>
</p>

<p align="center">
  <a href="#the-problem">The Problem</a> •
  <a href="#the-solution">The Solution</a> •
  <a href="#key-features">Key Features</a> •
  <a href="#technology-stack">Technology</a> •
  <a href="#getting-started">Setup</a> •
  <a href="#roadmap">Roadmap</a>
</p>

---

## The Problem: The Creator Economy's Scaling Dilemma

The creator economy is booming, but engagement doesn't scale. As creators grow their audience, the personal connection that built their community becomes impossible to maintain.

* **For Creators:**
    * **Engagement Bottleneck:** They are overwhelmed by repetitive questions across thousands of comments and DMs.
    * **Missed Monetization:** Valuable expertise locked away in video content is difficult to monetize directly.
    * **Audience Insight is Buried:** It's impossible to manually track the most pressing questions and content desires of their audience.

* **For Viewers & Fans:**
    * **Information is Inaccessible:** Finding a specific answer means scrubbing through hours of video content.
    * **No Direct Connection:** The chances of getting a personal response from their favorite creator are near zero.
    * **Fragmented Learning:** Knowledge is scattered across a vast library of videos, with no way to connect concepts.

> **The result is a frustrating experience for fans and a massive, untapped opportunity for creators.**

---

## The Solution: YoppyChat AI

**YoppyChat AI transforms a creator's entire content library into an intelligent, interactive AI persona that acts as a true extension of their brand.**

We don't just search for keywords; we create a digital clone of the creator's knowledge and personality. Our platform studies every video transcript to understand their unique style, opinions, and expertise, enabling it to hold authentic, human-like conversations.

This creates a powerful new channel for 24/7 engagement, turning passive viewers into an active, engaged community.



---

## Key Features: A Platform for Engagement & Monetization

YoppyChat is more than a chatbot; it's a comprehensive engagement platform.

* 💬 **Instant AI Persona Creation:** Creators simply provide their YouTube channel URL. Our asynchronous backend processes hundreds of videos, creating a knowledgeable AI assistant in minutes.
* 🧠 **Authentic & Context-Aware Conversations:** Powered by advanced LLMs and RAG (Retrieval-Augmented Generation), the AI answers questions in the creator's unique voice and style, citing specific video sources for every claim.
* 🌐 **Multi-Platform Deployment:** Engage fans where they are. YoppyChat provides seamless integrations for:
    * **Discord:** Deploy fully branded, custom bots to a creator's private community.
    * **Telegram:** Enable personal Q&A or group chat interactions on the go.
    * **Public Web Pages:** Each channel gets a shareable, public-facing chat page.
* 💰 **Creator Monetization & Growth Tools:**
    * **Affiliate System:** Every public chat page acts as a referral link. Creators earn a recurring commission on every user who signs up and subscribes.
    * **Creator Dashboard:** A central hub to track earnings, view referral statistics, and manage integrations.
* 📊 **Powerful Admin Dashboard:** Full administrative control over users, community plans, and creator payouts.

---

## Technology Stack: Built for Scale & Performance

Our platform is built on a modern, robust, and scalable technology stack designed for high-performance AI applications.

| Category                | Technology                                                                                                  |
| ----------------------- | ----------------------------------------------------------------------------------------------------------- |
| **Backend** | Python, Flask, Gunicorn                                                                                     |
| **Frontend** | HTML5, CSS3, Vanilla JavaScript (for a lightweight, fast user experience)                                     |
| **Database** | Supabase (PostgreSQL with pgvector for vector storage)                                                      |
| **AI / Machine Learning** | **Embeddings:** Gemini, OpenAI<br>**LLMs:** Groq (for speed), OpenAI, Gemini<br>**Reranking:** Sentence Transformers |
| **Async Task Processing** | Huey with a Redis backend for managing long-running tasks like video processing and AI model updates.      |
| **Payments & Webhooks** | Razorpay for secure subscription management.                                                                |
| **DevOps** | Virtual environment setup scripts, clear separation of services (web, worker).                              |

### High-Level Architecture
