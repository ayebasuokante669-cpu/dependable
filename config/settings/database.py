"""Database selection with a graceful local fallback.

Production runs on PostgreSQL.  Local development often does not have a server
running, and a scaffold that refuses to start is a scaffold nobody uses -- so if
Postgres is configured but unreachable, we fall back to SQLite and say so.

Set ``DB_ENGINE=postgres`` to make Postgres mandatory (no fallback), or
``DB_ENGINE=sqlite`` to skip Postgres entirely.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

PROBE_TIMEOUT_SECONDS = 0.75


def _sqlite(base_dir: Path) -> dict:
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(base_dir / "db.sqlite3"),
    }


def _postgres_from_env() -> dict | None:
    """Build a Postgres config from ``DATABASE_URL`` or discrete ``DB_*`` vars."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            return None
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")) or "dependable",
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
        }

    name = os.environ.get("DB_NAME")
    if not name:
        return None
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }


def _is_pooled(host: str, port: str) -> bool:
    """Detect a transaction-mode connection pooler (PgBouncer, Supabase, etc.).

    Transaction pooling hands each statement a different backend, which breaks
    Django's server-side cursors (``.iterator()``). Django's documented fix is
    ``DISABLE_SERVER_SIDE_CURSORS``; the alternative is a confusing
    "cursor does not exist" error the first time a large queryset is streamed.
    """
    if os.environ.get("DB_POOLED"):
        return os.environ["DB_POOLED"].strip().lower() in {"1", "true", "yes", "on"}
    return "pooler" in host.lower() or "pgbouncer" in host.lower() or port == "6543"


def _is_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def get_database_config(base_dir: Path, *, allow_fallback: bool = True) -> dict:
    """Return the ``DATABASES['default']`` dict for this environment."""
    engine_choice = os.environ.get("DB_ENGINE", "").strip().lower()

    if engine_choice == "sqlite":
        return _sqlite(base_dir)

    postgres = _postgres_from_env()
    if postgres is None:
        if engine_choice == "postgres":
            raise RuntimeError(
                "DB_ENGINE=postgres but no DATABASE_URL or DB_NAME was provided."
            )
        return _sqlite(base_dir)

    if _is_pooled(postgres["HOST"], postgres["PORT"]):
        postgres["DISABLE_SERVER_SIDE_CURSORS"] = True

    if engine_choice == "postgres" or not allow_fallback:
        # Explicitly demanded: let a real connection error surface instead of
        # silently running against the wrong database.
        return postgres

    if _is_reachable(postgres["HOST"], int(postgres["PORT"])):
        return postgres

    print(
        f"[schoolcord] PostgreSQL at {postgres['HOST']}:{postgres['PORT']} is not "
        f"reachable - falling back to SQLite for local development. "
        f"Set DB_ENGINE=postgres to disable this fallback.",
        file=sys.stderr,
    )
    return _sqlite(base_dir)
