"""
Health check view — verifies DB, Redis, and Elasticsearch connectivity
without exposing credentials.
"""

import logging

import redis as redis_lib
from django.conf import settings
from django.db import connection, OperationalError
from django.http import JsonResponse
from elasticsearch_dsl import connections as es_connections

logger = logging.getLogger(__name__)


def health(request):
    """
    GET /health/

    Returns 200 if the database, Redis, and Elasticsearch are all reachable,
    500 otherwise. Never exposes connection details in the response body.
    """
    # ── Database ────────────────────────────────────────────────────────────
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except OperationalError as exc:
        logger.error("Health check: database unreachable — %s", exc)
        return JsonResponse({"status": "error", "detail": "database unreachable"}, status=500)

    # ── Redis ────────────────────────────────────────────────────────────────
    try:
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.ping()
    except Exception as exc:
        logger.error("Health check: redis unreachable — %s", exc)
        return JsonResponse({"status": "error", "detail": "redis unreachable"}, status=500)

    # ── Elasticsearch ────────────────────────────────────────────────────────
    try:
        try:
            es_connections.get_connection("default")
        except KeyError:
            es_connections.create_connection(alias="default", hosts=[settings.ELASTICSEARCH_URL])
        es_client = es_connections.get_connection("default")
        es_client.ping()
    except Exception as exc:
        logger.error("Health check: elasticsearch unreachable — %s", exc)
        return JsonResponse({"status": "error", "detail": "elasticsearch unreachable"}, status=500)

    return JsonResponse({"status": "ok"})
