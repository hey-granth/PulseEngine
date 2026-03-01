"""
Root conftest.py — shared fixtures for all tests.
"""

import os
import time

import pytest
import redis as redis_lib
from django.conf import settings
from django.contrib.auth import get_user_model

from categories.models import Category
from posts.models import Post

User = get_user_model()


# ── Inject DATABASE_URL for tests if not already present ────────────────────
# Settings now require DATABASE_URL (Neon). In CI/local dev the individual
# DATABASE_* vars may be set instead. Build a DATABASE_URL from them so tests
# run without modifying .env.
def _ensure_database_url() -> None:
    if os.environ.get("DATABASE_URL"):
        return  # Already set — Neon URL or CI-injected, nothing to do.

    user = os.environ.get("DATABASE_USER", "")
    password = os.environ.get("DATABASE_PASSWORD", "")
    host = os.environ.get("DATABASE_HOST", "")
    port = os.environ.get("DATABASE_PORT", "5432")
    name = os.environ.get("DATABASE_NAME", "pulseengine")

    if host:
        # TCP connection (e.g. Docker Postgres)
        auth = f"{user}:{password}@" if password else (f"{user}@" if user else "")
        url = f"postgresql://{auth}{host}:{port}/{name}"
    else:
        # Unix socket / peer auth (local dev)
        auth = f"{user}@" if user else ""
        url = f"postgresql://{auth}/{name}"

    os.environ["DATABASE_URL"] = url


_ensure_database_url()
# ────────────────────────────────────────────────────────────────────────────


def pytest_configure(config):
    """
    Kill any lingering connections to test_pulseengine before the session
    starts. This prevents 'database is being accessed by other users' errors
    that occur when concurrency tests leave open ThreadPoolExecutor connections.
    """
    import subprocess
    db_name = os.environ.get("TEST_DB_NAME", "test_pulseengine")
    try:
        subprocess.run(
            [
                "psql",
                os.environ.get("DATABASE_NAME", "pulseengine"),
                "-c",
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname='{db_name}' AND pid != pg_backend_pid();",
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # Not fatal — if psql isn't available or DB is unreachable, carry on.


@pytest.fixture(autouse=True)
def _flush_redis(request):
    """Flush Redis before each test that uses DB (to avoid stale ranking data)."""
    if "django_db" not in [m.name for m in request.node.iter_markers()] and not hasattr(
        request, "fixturenames"
    ):
        yield
        return
    try:
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.flushdb()
    except redis_lib.ConnectionError:
        pass
    yield
    try:
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.flushdb()
    except redis_lib.ConnectionError:
        pass


@pytest.fixture
def redis_client():
    """Return a Redis client connected to the test Redis."""
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass123")


@pytest.fixture
def user2(db):
    return User.objects.create_user(username="testuser2", password="testpass123")


@pytest.fixture
def category(db):
    return Category.objects.create(name="Technology", slug="technology")


@pytest.fixture
def category2(db):
    return Category.objects.create(name="Science", slug="science")


@pytest.fixture
def post(db, user, category):
    return Post.objects.create(
        author=user,
        category=category,
        content="Test post content about technology.",
    )


@pytest.fixture
def post2(db, user, category2):
    return Post.objects.create(
        author=user,
        category=category2,
        content="Test post about science discoveries.",
    )


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def authenticated_client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client

