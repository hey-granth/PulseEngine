"""
Search endpoint with hybrid ES + Redis re-ranking.

GET /search/?q=<query>&category=<slug>

Flow:
1. Query Elasticsearch.
2. Get post IDs + ES scores.
3. Fetch Redis trending scores.
4. Re-rank: final = 0.7 * ES_score + 0.3 * normalized_trending.
5. If Redis unavailable, return ES ranking only.
"""

import logging

import redis as redis_lib
from django.conf import settings
from elasticsearch_dsl import Q as ESQ, Search, connections
from rest_framework.response import Response
from rest_framework.views import APIView

from posts.models import Post
from posts.serializers import PostListSerializer
from ranking.constants import GLOBAL_LEADERBOARD_KEY

logger = logging.getLogger(__name__)


def _ensure_es_connection():
    if not connections.get_connection("default", required=False):
        connections.create_connection(
            alias="default", hosts=[settings.ELASTICSEARCH_URL]
        )


def _get_redis():
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


class SearchView(APIView):
    """GET /search/?q=&category="""

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        category = request.query_params.get("category", "").strip()

        if not query:
            return Response({"detail": "Query parameter 'q' is required."}, status=400)

        try:
            _ensure_es_connection()
        except Exception:
            logger.exception("Failed to connect to Elasticsearch.")
            return Response(
                {"detail": "Search service unavailable."}, status=503
            )

        # Build ES query
        s = Search(index="posts")
        es_query = ESQ("match", content=query)

        if category:
            es_query = es_query & ESQ("term", category_slug=category)

        s = s.query(es_query)
        s = s[:50]  # Get top 50 from ES

        try:
            response = s.execute()
        except Exception:
            logger.exception("Elasticsearch query failed.")
            return Response({"detail": "Search service unavailable."}, status=503)

        if not response.hits:
            return Response([])

        # Extract ES results: [(post_id, es_score), ...]
        es_results = []
        for hit in response.hits:
            try:
                post_id = int(hit.meta.id)
                es_score = float(hit.meta.score)
                es_results.append((post_id, es_score))
            except (ValueError, AttributeError):
                continue

        if not es_results:
            return Response([])

        # Normalize ES scores
        max_es_score = max(score for _, score in es_results)
        if max_es_score > 0:
            es_normalized = {
                pid: score / max_es_score for pid, score in es_results
            }
        else:
            es_normalized = {pid: 0 for pid, _ in es_results}

        # Try to fetch trending scores from Redis
        trending_scores = {}
        redis_available = True
        try:
            r = _get_redis()
            post_ids = [pid for pid, _ in es_results]
            pipe = r.pipeline()
            for pid in post_ids:
                pipe.zscore(GLOBAL_LEADERBOARD_KEY, str(pid))
            scores = pipe.execute()

            for pid, score in zip(post_ids, scores):
                trending_scores[pid] = float(score) if score is not None else 0.0
        except redis_lib.ConnectionError:
            logger.warning("Redis unavailable — returning ES-only ranking.")
            redis_available = False

        # Compute final scores
        if redis_available and trending_scores:
            max_trending = max(trending_scores.values()) if trending_scores else 1.0
            if max_trending == 0:
                max_trending = 1.0

            final_scores = {}
            for pid in es_normalized:
                es_norm = es_normalized[pid]
                trending_norm = trending_scores.get(pid, 0) / max_trending
                final_scores[pid] = 0.7 * es_norm + 0.3 * trending_norm
        else:
            # ES-only ranking
            final_scores = es_normalized

        # Sort by final score descending
        sorted_ids = sorted(final_scores, key=final_scores.get, reverse=True)[:20]

        # Bulk fetch posts
        posts = Post.objects.select_related("author", "category").filter(
            pk__in=sorted_ids
        )
        posts_dict = {p.pk: p for p in posts}

        ordered = [posts_dict[pid] for pid in sorted_ids if pid in posts_dict]
        data = PostListSerializer(ordered, many=True).data

        # Attach scores
        for item in data:
            item["search_score"] = round(final_scores.get(item["id"], 0), 4)

        return Response(data)

