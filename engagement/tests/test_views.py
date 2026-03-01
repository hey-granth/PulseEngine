"""
Tests for engagement endpoints — like enforcement, event creation, Redis updates.
"""

import pytest
import redis as redis_lib
from django.conf import settings
from django.contrib.auth import get_user_model

from engagement.models import EngagementEvent, EngagementType, UserPostLike
from ranking.constants import dirty_posts_key, engagement_hash_key

User = get_user_model()


@pytest.mark.django_db
class TestLikeEndpoint:
    """Tests for POST /posts/{id}/like/"""

    def test_like_creates_event_and_record(self, authenticated_client, post, redis_client):
        resp = authenticated_client.post(f"/posts/{post.pk}/like/")
        assert resp.status_code == 201
        assert resp.data["type"] == "LIKE"

        # DB records created
        assert UserPostLike.objects.filter(post=post, user=post.author).exists()
        assert EngagementEvent.objects.filter(
            post=post, user=post.author, type=EngagementType.LIKE
        ).exists()

    def test_like_updates_redis(self, authenticated_client, post, redis_client):
        authenticated_client.post(f"/posts/{post.pk}/like/")

        r = redis_client
        likes = r.hget(engagement_hash_key(post.pk), "likes")
        assert likes == "1"

        # Should be in dirty set
        assert r.zscore(dirty_posts_key(), str(post.pk)) is not None

    def test_duplicate_like_returns_409(self, authenticated_client, post):
        resp1 = authenticated_client.post(f"/posts/{post.pk}/like/")
        assert resp1.status_code == 201

        resp2 = authenticated_client.post(f"/posts/{post.pk}/like/")
        assert resp2.status_code == 409

        # Only one UserPostLike record
        assert UserPostLike.objects.filter(post=post).count() == 1

    def test_different_users_can_like_same_post(self, api_client, post, user, user2):
        api_client.force_authenticate(user=user)
        resp1 = api_client.post(f"/posts/{post.pk}/like/")
        assert resp1.status_code == 201

        api_client.force_authenticate(user=user2)
        resp2 = api_client.post(f"/posts/{post.pk}/like/")
        assert resp2.status_code == 201

        assert UserPostLike.objects.filter(post=post).count() == 2

    def test_like_nonexistent_post_returns_404(self, authenticated_client):
        resp = authenticated_client.post("/posts/99999/like/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestCommentEndpoint:
    """Tests for POST /posts/{id}/comment/"""

    def test_comment_creates_event(self, authenticated_client, post, redis_client):
        resp = authenticated_client.post(f"/posts/{post.pk}/comment/")
        assert resp.status_code == 201
        assert resp.data["type"] == "COMMENT"

        assert EngagementEvent.objects.filter(
            post=post, type=EngagementType.COMMENT
        ).exists()

    def test_comment_updates_redis(self, authenticated_client, post, redis_client):
        authenticated_client.post(f"/posts/{post.pk}/comment/")

        r = redis_client
        comments = r.hget(engagement_hash_key(post.pk), "comments")
        assert comments == "1"

    def test_multiple_comments_allowed(self, authenticated_client, post):
        resp1 = authenticated_client.post(f"/posts/{post.pk}/comment/")
        resp2 = authenticated_client.post(f"/posts/{post.pk}/comment/")
        assert resp1.status_code == 201
        assert resp2.status_code == 201

        assert EngagementEvent.objects.filter(
            post=post, type=EngagementType.COMMENT
        ).count() == 2


@pytest.mark.django_db
class TestShareEndpoint:
    """Tests for POST /posts/{id}/share/"""

    def test_share_creates_event(self, authenticated_client, post, redis_client):
        resp = authenticated_client.post(f"/posts/{post.pk}/share/")
        assert resp.status_code == 201
        assert resp.data["type"] == "SHARE"

        assert EngagementEvent.objects.filter(
            post=post, type=EngagementType.SHARE
        ).exists()

    def test_share_updates_redis(self, authenticated_client, post, redis_client):
        authenticated_client.post(f"/posts/{post.pk}/share/")

        r = redis_client
        shares = r.hget(engagement_hash_key(post.pk), "shares")
        assert shares == "1"

