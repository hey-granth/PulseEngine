"""
Feed views — serve ranked posts from Redis with DB fallback.
"""

import json
import logging

import redis as redis_lib
from django.conf import settings
from django.db.models import Count
from rest_framework.response import Response
from rest_framework.views import APIView

from posts.models import Post
from posts.serializers import PostListSerializer
import ranking.constants as rc

logger = logging.getLogger(__name__)

FEED_SIZE = 20
CACHE_TTL = 60  # seconds


def _get_redis():
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _db_fallback_global():
    """Fallback: order by raw engagement count in DB."""
    posts = (
        Post.objects.filter(is_flagged=False)
        .annotate(eng_count=Count("engagement_events"))
        .select_related("author", "category")
        .order_by("-eng_count")[:FEED_SIZE]
    )
    return PostListSerializer(posts, many=True).data


def _db_fallback_category(slug):
    """Fallback: order by raw engagement count in DB for a category."""
    posts = (
        Post.objects.filter(category__slug=slug, is_flagged=False)
        .annotate(eng_count=Count("engagement_events"))
        .select_related("author", "category")
        .order_by("-eng_count")[:FEED_SIZE]
    )
    return PostListSerializer(posts, many=True).data


class GlobalFeedView(APIView):
    """GET /feed/ — top 20 from global leaderboard."""

    def get(self, request):
        try:
            r = _get_redis()

            # Check cache first
            cached = r.get(rc.FEED_CACHE_GLOBAL)
            if cached:
                return Response(json.loads(cached))

            # Read from global leaderboard
            top_ids_scores = r.zrevrange(
                rc.GLOBAL_LEADERBOARD_KEY, 0, FEED_SIZE - 1, withscores=True
            )

            if not top_ids_scores:
                data = _db_fallback_global()
            else:
                post_ids = [int(pid) for pid, _ in top_ids_scores]
                score_map = {int(pid): sc for pid, sc in top_ids_scores}

                posts = Post.objects.select_related("author", "category").filter(pk__in=post_ids)
                posts_dict = {p.pk: p for p in posts}

                # Maintain leaderboard order
                ordered = [posts_dict[pid] for pid in post_ids if pid in posts_dict]
                data = PostListSerializer(ordered, many=True).data

                # Attach scores
                for item in data:
                    item["trending_score"] = score_map.get(item["id"], 0)

            # Cache for 60s
            r.setex(rc.FEED_CACHE_GLOBAL, CACHE_TTL, json.dumps(data))
            return Response(data)

        except redis_lib.ConnectionError:
            logger.warning("Redis unavailable — falling back to DB for global feed.")
            return Response(_db_fallback_global())


class CategoryFeedView(APIView):
    """GET /feed/{slug}/ — top 20 from category leaderboard."""

    def get(self, request, slug):
        try:
            r = _get_redis()
            cache_key = rc.feed_cache_category_key(slug)

            # Check cache first
            cached = r.get(cache_key)
            if cached:
                return Response(json.loads(cached))

            # Read from category leaderboard
            cat_key = rc.category_leaderboard_key(slug)
            top_ids_scores = r.zrevrange(cat_key, 0, FEED_SIZE - 1, withscores=True)

            if not top_ids_scores:
                data = _db_fallback_category(slug)
            else:
                post_ids = [int(pid) for pid, _ in top_ids_scores]
                score_map = {int(pid): sc for pid, sc in top_ids_scores}

                posts = Post.objects.select_related("author", "category").filter(
                    pk__in=post_ids, is_flagged=False
                )
                posts_dict = {p.pk: p for p in posts}

                ordered = [posts_dict[pid] for pid in post_ids if pid in posts_dict]
                data = PostListSerializer(ordered, many=True).data

                for item in data:
                    item["trending_score"] = score_map.get(item["id"], 0)

            r.setex(cache_key, CACHE_TTL, json.dumps(data))
            return Response(data)

        except redis_lib.ConnectionError:
            logger.warning("Redis unavailable — falling back to DB for category feed: %s", slug)
            return Response(_db_fallback_category(slug))
