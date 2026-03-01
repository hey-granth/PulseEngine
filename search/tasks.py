"""
Celery tasks for search indexing.
"""

import logging

from celery import shared_task
from django.conf import settings
from elasticsearch_dsl import connections

logger = logging.getLogger(__name__)


def _ensure_es_connection():
    """Ensure ES connection is configured."""
    try:
        connections.get_connection("default")
    except KeyError:
        connections.create_connection(alias="default", hosts=[settings.ELASTICSEARCH_URL])


@shared_task(name="search.tasks.index_post_to_es")
def index_post_to_es(post_id: int):
    """Index a single post into Elasticsearch."""
    from posts.models import Post
    from search.documents import PostDocument

    try:
        _ensure_es_connection()

        post = Post.objects.select_related("author", "category").get(pk=post_id)

        # Use index name from settings (allows test override via ES_INDEX_NAME)
        index_name = PostDocument.get_index_name()
        PostDocument._index._name = index_name

        doc = PostDocument.from_post(post)

        # Ensure index exists
        if not PostDocument._index.exists():
            PostDocument.init()

        doc.save()
        logger.info("Indexed post %d to Elasticsearch index '%s'.", post_id, index_name)
    except Post.DoesNotExist:
        logger.warning("Post %d not found — skipping ES indexing.", post_id)
    except Exception:
        logger.exception("Failed to index post %d to Elasticsearch.", post_id)
        raise
