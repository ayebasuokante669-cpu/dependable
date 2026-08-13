"""Local development settings.  The default for ``manage.py``."""

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env_bool
from .database import get_database_config

DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = ["*"]

# Postgres when it is up, SQLite when it is not.
DATABASES = {"default": get_database_config(BASE_DIR, allow_fallback=True)}

# Plain static serving in dev -- the manifest storage would demand a
# collectstatic run before the first page load.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
