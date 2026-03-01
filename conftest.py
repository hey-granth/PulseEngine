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

