import time

import redis as redis_lib
from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from engagement.models import EngagementEvent, EngagementType, UserPostLike
from posts.models import Post
from ranking.constants import dirty_posts_key, engagement_hash_key


def _get_redis():
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _record_engagement(post, user, engagement_type):
    """
    Common write-path logic:
    1. Insert EngagementEvent row.
    2. Increment Redis counter atomically.
    3. ZADD post into dirty_posts set.
    """
    EngagementEvent.objects.create(post=post, user=user, type=engagement_type)

    try:
        r = _get_redis()
        field_map = {
            EngagementType.LIKE: "likes",
            EngagementType.COMMENT: "comments",
            EngagementType.SHARE: "shares",
        }
        r.hincrby(engagement_hash_key(post.pk), field_map[engagement_type], 1)
        r.zadd(dirty_posts_key(), {str(post.pk): time.time()})
    except redis_lib.ConnectionError:
        # Redis down — engagement recorded in DB; ranking worker will catch up
        pass


class LikeView(APIView):
    """POST /posts/{id}/like/"""

    def post(self, request, post_id):
        post = get_object_or_404(Post, pk=post_id)
        try:
            with transaction.atomic():
                UserPostLike.objects.create(post=post, user=request.user)
        except IntegrityError:
            return Response(
                {"detail": "You have already liked this post."},
                status=status.HTTP_409_CONFLICT,
            )
        _record_engagement(post, request.user, EngagementType.LIKE)
        return Response(
            {"detail": "Liked.", "post_id": post.pk, "type": "LIKE"},
            status=status.HTTP_201_CREATED,
        )


class CommentView(APIView):
    """POST /posts/{id}/comment/"""

    def post(self, request, post_id):
        post = get_object_or_404(Post, pk=post_id)
        _record_engagement(post, request.user, EngagementType.COMMENT)
        return Response(
            {"detail": "Commented.", "post_id": post.pk, "type": "COMMENT"},
            status=status.HTTP_201_CREATED,
        )


class ShareView(APIView):
    """POST /posts/{id}/share/"""

    def post(self, request, post_id):
        post = get_object_or_404(Post, pk=post_id)
        _record_engagement(post, request.user, EngagementType.SHARE)
        return Response(
            {"detail": "Shared.", "post_id": post.pk, "type": "SHARE"},
            status=status.HTTP_201_CREATED,
        )

