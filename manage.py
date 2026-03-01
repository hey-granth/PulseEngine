#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def _ensure_database_url() -> None:
    """
    Build DATABASE_URL from individual DATABASE_* env vars if not already set.
    This allows local dev (.env with separate vars) to work without modification
    while production uses a single DATABASE_URL (e.g. Neon).
    Called before Django settings are imported.
    """
    if os.environ.get("DATABASE_URL"):
        return

    # Load .env so we can read DATABASE_* vars (dotenv not yet imported here)
    from pathlib import Path

    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_path)

    if os.environ.get("DATABASE_URL"):
        return  # .env had DATABASE_URL

    user = os.environ.get("DATABASE_USER", "")
    password = os.environ.get("DATABASE_PASSWORD", "")
    host = os.environ.get("DATABASE_HOST", "")
    port = os.environ.get("DATABASE_PORT", "5432")
    name = os.environ.get("DATABASE_NAME", "pulseengine")

    if host:
        auth = f"{user}:{password}@" if password else (f"{user}@" if user else "")
        url = f"postgresql://{auth}{host}:{port}/{name}"
    else:
        auth = f"{user}@" if user else ""
        url = f"postgresql://{auth}/{name}"

    os.environ["DATABASE_URL"] = url


def main():
    """Run administrative tasks."""
    _ensure_database_url()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pulseengine.settings")

    # Always keep the test database between runs — avoids the "database already
    # exists" prompt caused by lingering connections after concurrency tests.
    # The DB is still created fresh on the very first run.
    if len(sys.argv) > 1 and sys.argv[1] == "test" and "--keepdb" not in sys.argv:
        sys.argv.insert(2, "--keepdb")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
