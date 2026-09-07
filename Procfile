# Railway (and any Procfile-aware host) start command.
#
# Settings are pinned explicitly rather than left to DJANGO_SETTINGS_MODULE:
# manage.py defaults to config.settings.dev, and dev falls back to SQLite when
# Postgres is unreachable -- a silent wrong-database boot is worse than a crash.
#
# collectstatic runs here, not only at build time, because whitenoise's
# CompressedManifestStaticFilesStorage refuses to serve any asset that is not in
# staticfiles.json. Building the manifest under the same settings that will read
# it is what keeps the logo and app.css from 500-ing on first request.
web: python manage.py migrate --noinput --settings=config.settings.prod && python manage.py collectstatic --noinput --settings=config.settings.prod && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --timeout 60 --access-logfile - --error-logfile -
