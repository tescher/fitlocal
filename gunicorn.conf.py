import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
# One worker process with threads: each extra worker costs ~110 MB, and
# Opalstack's 512 MB plan kills every process on the account when it's
# exceeded. Threads still serve requests concurrently (slow trainer chat or
# plan calls don't block page loads), and one worker keeps Flask-Limiter's
# in-memory counts accurate. Raise with WEB_CONCURRENCY on a bigger plan.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 120
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Security
limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190
