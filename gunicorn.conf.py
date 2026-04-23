import multiprocessing
import os

# ── Server Socket ──────────────────────────────────────────────
bind = "0.0.0.0:5000"

# ── Workers ────────────────────────────────────────────────────
# A common formula: (2 × CPU cores) + 1
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "gevent"        # Async workers — handles streaming SSE without blocking
worker_connections = 1000      # Max concurrent connections per worker
timeout = 180                  # Allow longer streams (180s) before killing a stuck worker
keepalive = 5                  # seconds to keep idle connections alive

# ── Logging ────────────────────────────────────────────────────
accesslog = "-"                # stdout
errorlog  = "-"                # stderr
loglevel  = "info"

# ── Process Naming ─────────────────────────────────────────────
proc_name = "yoppychat"

# ── Security / Performance ─────────────────────────────────────
# Forward Supabase / Cloudflare headers to Flask's ProxyFix middleware
forwarded_allow_ips = "*"
proxy_allow_ips     = "*"
