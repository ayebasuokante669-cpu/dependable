"""Settings shared by every environment.

Environment-specific modules (``dev``, ``prod``) import ``*`` from here and
override only what genuinely differs.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Tenancy foundation. `core` first: the others build on its abstractions.
    "apps.core",
    "apps.schools",
    "apps.accounts",
    # Onboarding step 2: classes and subjects.
    "apps.academics",
    # Onboarding step 3: terms and what each class owes.
    "apps.fees",
    # Onboarding step 4: the student roster.
    "apps.students",
    # Outbound parent messaging (SMS/WhatsApp). Staff-side only.
    "apps.messaging",
    # Money received, recorded against a student. Balances derive from here.
    "apps.payments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must follow AuthenticationMiddleware: it reads request.user.
    "apps.core.middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.tenancy",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Set before the first migration and effectively permanent thereafter.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
# The role-aware dispatcher, not a screen: it forwards to whichever dashboard
# the signed-in role belongs on (apps/core/navigation.py::ROLE_HOME).
LOGIN_REDIRECT_URL = "core:dashboard"
# Signing out returns to the public front door rather than the login form --
# "you are out" reads better than being asked to go straight back in.
LOGOUT_REDIRECT_URL = "core:landing"

# The From: on password-reset mail. In dev the console backend prints the whole
# message, link included, so nothing has to be delivered for the flow to work.
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "Fulfilled Lite <no-reply@dependable.example>"
)
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24 * 3  # three days

# --- Messaging ----------------------------------------------------------
# Which gateway carries parent SMS is normally each school's own choice, stored
# on its SchoolMessagingConfig alongside the Sender ID it registered there.
#
# MESSAGING_PROVIDER is the platform override: when set it wins over every
# school's choice. It defaults to "console", which logs the message (Sender ID
# included) and marks it delivered, so a fresh checkout sends nothing anywhere
# and the whole feature still works with no credentials. Clear it in production
# -- MESSAGING_PROVIDER= -- and each school goes out through its own gateway.
MESSAGING_PROVIDER = os.environ.get("MESSAGING_PROVIDER", "console")

# There is deliberately no platform-wide sender ID. Who a message comes from is
# the school's own registered identity, resolved per send by
# apps/messaging/identity.py -- a school with no approved Sender ID is refused
# rather than falling back to a shared name.

# --- Gateway master credentials -----------------------------------------
# The platform holds the account and pays for the units; each school sends
# under its own Sender ID against it. A school that later takes out its own
# account puts its key on its messaging config, which wins over these.
BULKSMSNIGERIA_API_TOKEN = os.environ.get("BULKSMSNIGERIA_API_TOKEN", "")
BULKSMSNIGERIA_BASE_URL = os.environ.get(
    "BULKSMSNIGERIA_BASE_URL", "https://www.bulksmsnigeria.com"
)
# DND routing: 2 sends via the corporate route so reminders still reach the
# many Nigerian numbers on the Do-Not-Disturb register. See the provider.
BULKSMSNIGERIA_DND = os.environ.get("BULKSMSNIGERIA_DND", "2")

TERMII_API_KEY = os.environ.get("TERMII_API_KEY", "")
TERMII_BASE_URL = os.environ.get("TERMII_BASE_URL", "https://api.ng.termii.com")

AFRICASTALKING_USERNAME = os.environ.get("AFRICASTALKING_USERNAME", "")
AFRICASTALKING_API_KEY = os.environ.get("AFRICASTALKING_API_KEY", "")
AFRICASTALKING_BASE_URL = os.environ.get(
    "AFRICASTALKING_BASE_URL", "https://api.africastalking.com"
)

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
