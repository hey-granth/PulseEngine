"""
Health check view — verifies DB connectivity without exposing credentials.
"""

import logging

from django.db import connection, OperationalError
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def health(request):
    """
    GET /health/

    Returns 200 if the database is reachable, 500 otherwise.
    Never exposes connection details in the response body.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except OperationalError as exc:
        logger.error("Health check: database unreachable — %s", exc)
        return JsonResponse({"status": "error", "detail": "database unreachable"}, status=500)

    return JsonResponse({"status": "ok"})

