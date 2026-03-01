"""
Django settings for PulseEngine project.
Single settings file — no dev/prod split.
"""

import logging
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Load .env (fail loudly if missing) ──────────────────────────────────────
_env_path = BASE_DIR / ".env"
if not _env_path.is_file():
    raise RuntimeError(
        f".env file not found at {_env_path}. Copy .env.example to .env and fill in the values."
    )
load_dotenv(_env_path)


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Environment variable {key!r} is required but not set.")
    return value


# ── Core ────────────────────────────────────────────────────────────────────
SECRET_KEY = _require_env("SECRET_KEY")
DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

# ── Apps ────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    # Local
    "categories",
    "posts",
    "engagement",
    "ranking",
    "search",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pulseengine.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "pulseengine.wsgi.application"

# ── Database (Neon PostgreSQL via DATABASE_URL) ─────────────────────────────
_DATABASE_URL = os.environ.get("DATABASE_URL")
if not _DATABASE_URL:
    raise ImproperlyConfigured(
        "Environment variable 'DATABASE_URL' is required but not set. "
        "Add it to your .env file (e.g. postgres://user:pass@host/db?sslmode=require)."
    )

DATABASES = {
    "default": dj_database_url.parse(
        _DATABASE_URL,
        conn_max_age=600,
        ssl_require=True,
    )
}

# Enforce ENGINE and sslmode — never allow insecure or non-postgres connection.
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured(
        f"DATABASE_URL must resolve to a PostgreSQL backend, got: {DATABASES['default']['ENGINE']}"
    )
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["sslmode"] = "require"

# ── Test database override ───────────────────────────────────────────────────
# Django will create a test database on Neon using the name below.
# The ENGINE and connection parameters are inherited from DATABASE_URL.
# Never hardcode connection credentials here.
_test_db_name = os.environ.get("TEST_DB_NAME", "test_pulseengine")
DATABASES["default"]["TEST"] = {
    "NAME": _test_db_name,
    # ENGINE, HOST, PORT, USER, PASSWORD all inherited from the parsed DATABASE_URL.
}
# Disable connection pooling during tests — prevents lingering sessions that
# block DROP DATABASE between runs (especially after concurrency tests).
_running_tests = "test" in sys.argv or os.environ.get("CI")
if _running_tests:
    DATABASES["default"]["CONN_MAX_AGE"] = 0

# Startup log — confirms external DB is in use, never logs credentials.
logging.getLogger(__name__).info("Using external PostgreSQL via DATABASE_URL")

# ── Password validators ────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Internationalization ────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ── Static files ────────────────────────────────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Redis / Cache ───────────────────────────────────────────────────────────
REDIS_URL = _require_env("REDIS_URL")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# ── Celery ──────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = _require_env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = _require_env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_ALWAYS_EAGER = True  # Tasks run synchronously during tests
CELERY_TASK_EAGER_PROPAGATES = True  # Propagate exceptions from eager tasks

# ── Elasticsearch ───────────────────────────────────────────────────────────
ELASTICSEARCH_URL = _require_env("ELASTICSEARCH_URL")
# Index name — overridden per-test to isolate test data.
ES_INDEX_NAME = os.environ.get("ES_INDEX_NAME", "posts")

# ── Django REST Framework ───────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}
