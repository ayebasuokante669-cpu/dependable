# Railway's start command (any Procfile-aware host reads this the same way).
#
# --settings is passed explicitly rather than left to DJANGO_SETTINGS_MODULE.
# manage.py defaults to config.settings.dev, and dev falls back to SQLite when
# Postgres is unreachable -- so a missing or wrong DATABASE_URL would boot
# quietly against an empty local file instead of failing. A crash is the better
# outcome.
#
# collectstatic runs here, not only at build time, because whitenoise's
# CompressedManifestStaticFilesStorage refuses to serve any asset missing from
# staticfiles.json. Building that manifest under the same settings that will
# read it is what stops the logo and app.css 500-ing on the first request --
# a build-time run under dev settings would write no manifest at all.
web: python manage.py migrate --noinput --settings=config.settings.prod && python manage.py collectstatic --noinput --settings=config.settings.prod && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --timeout 60 --access-logfile - --error-logfile -
