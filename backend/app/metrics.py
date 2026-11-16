"""Prometheus metrics. Exposed at /metrics only when METRICS_ENABLED=true (no scraper on the
free hosting tier, and counts of logins are nobody's business by default)."""

from prometheus_client import Counter

EMAILS_SENT = Counter(
    "santa_emails_sent_total", "Emails handed to the provider", ["kind", "outcome"]
)
LOGIN_CODE_REQUESTS = Counter("santa_login_code_requests_total", "Login code requests", ["outcome"])
LOGIN_VERIFICATIONS = Counter(
    "santa_login_verifications_total", "Login code verifications", ["outcome"]
)
DRAWS = Counter("santa_draws_total", "Draw attempts", ["outcome"])
