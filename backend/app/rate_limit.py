"""slowapi rate limiter shared by the app.

Limits come from settings (LOGIN_RATE_LIMIT, FNOL_RATE_LIMIT); enforcement is
keyed by client IP. Tests toggle `limiter.enabled` at runtime — slowapi checks
the flag inside the request-time limit check, so disabling mid-suite is safe.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
