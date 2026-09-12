"""Production settings.  No SQLite fallback -- a missing database must fail loudly."""

import os

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, PRODUCT_NAME, SUPPORT_EMAIL, env_list
from .database import get_database_config

DEBUG = False

# --- Host and origin allow-lists ----------------------------------------
# ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS carry the domains we own and can write
# down (theschoolcord.com and friends). The platform's own domain is appended
# here instead: Railway generates it (schoolcord-production.up.railway.app) and
# exposes it as RAILWAY_PUBLIC_DOMAIN, so hardcoding it into an env var means
# re-editing the dashboard every time the service is renamed or recreated.
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

_platform_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
if _platform_domain and _platform_domain not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_platform_domain)
    # Django matches CSRF origins by scheme://host, so the origin needs the
    # https:// prefix the host entry must not have.
    origin = f"https://{_platform_domain}"
    if origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origin)

DATABASES = {"default": get_database_config(BASE_DIR, allow_fallback=False)}
DATABASES["default"]["CONN_MAX_AGE"] = int(os.environ.get("DB_CONN_MAX_AGE", "60"))

SECURE_SSL_REDIRECT = True
# Railway terminates TLS at its edge and forwards plain HTTP to the container,
# so request.is_secure() is False without this -- which would send the SSL
# redirect above into an infinite loop and mark no cookie as secure.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", f"{PRODUCT_NAME} <{SUPPORT_EMAIL}>"
)
