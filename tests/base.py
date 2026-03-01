"""
Shared base test classes for real-service integration tests.

RealRedisTestCase
-----------------
- Generates a unique test namespace prefix:  test:{uuid}:
- All ranking key helpers are available via self.key_*(...)  convenience
  wrappers that prepend the namespace automatically.
- setUp / tearDown scan-delete only keys under the test namespace.
- Does NOT flush the entire Redis DB.

RealESTestCase
--------------
- Generates a unique Elasticsearch index:  pulseengine_test_{uuid}
- Sets Django's ES_INDEX_NAME setting to that index before each test.
- Creates the index in setUp, deletes it in tearDown.
- Combines with RealRedisTestCase for tests that need both.

Both classes inherit from django.test.TestCase so Django's DB test
transaction wrapping still applies.
"""

import logging
import uuid

import redis as redis_lib
from django.conf import settings
from django.test import TestCase, TransactionTestCase
from elasticsearch_dsl import connections as es_connections

logger = logging.getLogger(__name__)


# ── Helper: connect to real Redis ────────────────────────────────────────────


def get_real_redis() -> redis_lib.Redis:
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


# ── Helper: connect to real Elasticsearch ───────────────────────────────────


def get_es_client():
    try:
        es_connections.get_connection("default")
    except KeyError:
        es_connections.create_connection(alias="default", hosts=[settings.ELASTICSEARCH_URL])
    return es_connections.get_connection("default")


# ── Redis isolation mixin ───────────────────────────────────────────────────


class RedisNamespaceMixin:
    """
    Mixin that provides per-test Redis key isolation.

    Injects a unique namespace into ranking.constants key functions by
    monkey-patching the module globals during the test.  Restores originals
    in tearDown so other tests are unaffected.
    """

    def _setup_redis_namespace(self):
        self._redis_ns = f"test:{uuid.uuid4().hex}:"
        self.r = get_real_redis()
        logger.info("[TEST] Redis namespace: %s", self._redis_ns)

        # Patch ranking.constants key functions to use the test namespace
        import ranking.constants as rc

        _orig_engagement_hash_key = rc.engagement_hash_key
        _orig_dirty_posts_key = rc.dirty_posts_key
        _orig_category_leaderboard_key = rc.category_leaderboard_key
        _orig_feed_cache_category_key = rc.feed_cache_category_key
        _orig_GLOBAL = rc.GLOBAL_LEADERBOARD_KEY
        _orig_FEED_CACHE_GLOBAL = rc.FEED_CACHE_GLOBAL

        ns = self._redis_ns

        rc.engagement_hash_key = lambda post_id: ns + _orig_engagement_hash_key(post_id)
        rc.dirty_posts_key = lambda: ns + _orig_dirty_posts_key()
        rc.category_leaderboard_key = lambda slug: ns + _orig_category_leaderboard_key(slug)
        rc.feed_cache_category_key = lambda slug: ns + _orig_feed_cache_category_key(slug)
        rc.GLOBAL_LEADERBOARD_KEY = ns + _orig_GLOBAL
        rc.FEED_CACHE_GLOBAL = ns + _orig_FEED_CACHE_GLOBAL

        # Store originals for teardown
        self._rc_orig = {
            "engagement_hash_key": _orig_engagement_hash_key,
            "dirty_posts_key": _orig_dirty_posts_key,
            "category_leaderboard_key": _orig_category_leaderboard_key,
            "feed_cache_category_key": _orig_feed_cache_category_key,
            "GLOBAL_LEADERBOARD_KEY": _orig_GLOBAL,
            "FEED_CACHE_GLOBAL": _orig_FEED_CACHE_GLOBAL,
        }

    def _teardown_redis_namespace(self):
        # Delete all keys under the test namespace using SCAN (safe, scoped)
        cursor = 0
        pattern = f"{self._redis_ns}*"
        while True:
            cursor, keys = self.r.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                self.r.delete(*keys)
            if cursor == 0:
                break

        # Restore ranking.constants originals
        import ranking.constants as rc

        for attr, val in self._rc_orig.items():
            setattr(rc, attr, val)

        self.r.close()

    # Convenience key accessors (tests can call self.engagement_hash_key etc.)
    def engagement_hash_key(self, post_id):
        import ranking.constants as rc

        return rc.engagement_hash_key(post_id)

    def dirty_posts_key(self):
        import ranking.constants as rc

        return rc.dirty_posts_key()

    def category_leaderboard_key(self, slug):
        import ranking.constants as rc

        return rc.category_leaderboard_key(slug)

    @property
    def GLOBAL_LEADERBOARD_KEY(self):
        import ranking.constants as rc

        return rc.GLOBAL_LEADERBOARD_KEY


# ── ES isolation mixin ───────────────────────────────────────────────────────


class ESIndexMixin:
    """
    Mixin that creates a unique Elasticsearch index per test and tears it down.
    """

    def _setup_es_index(self):
        self._es_index_name = f"pulseengine_test_{uuid.uuid4().hex}"
        logger.info("[TEST] ES index: %s", self._es_index_name)

        # Override settings so all ES operations use this index
        self._orig_es_index_name = getattr(settings, "ES_INDEX_NAME", "posts")
        settings.ES_INDEX_NAME = self._es_index_name

        # Update PostDocument index name
        from search.documents import PostDocument

        PostDocument._index._name = self._es_index_name

        # Ensure ES connection
        self._es_client = get_es_client()

        # Create the test index
        from search.documents import PostDocument

        PostDocument.init()

    def _teardown_es_index(self):
        try:
            self._es_client.indices.delete(index=self._es_index_name, ignore_unavailable=True)
        except Exception as exc:
            logger.warning("[TEST] Could not delete ES index %s: %s", self._es_index_name, exc)

        # Restore settings and PostDocument index name
        settings.ES_INDEX_NAME = self._orig_es_index_name
        from search.documents import PostDocument

        PostDocument._index._name = self._orig_es_index_name

    def refresh_es_index(self):
        """Force ES to make all indexed docs immediately searchable."""
        self._es_client.indices.refresh(index=self._es_index_name)


# ── Concrete base classes ────────────────────────────────────────────────────


class RealRedisTestCase(RedisNamespaceMixin, TestCase):
    """TestCase with isolated real Redis namespace."""

    def setUp(self):
        super().setUp()
        self._setup_redis_namespace()

    def tearDown(self):
        self._teardown_redis_namespace()
        super().tearDown()


class RealESTestCase(ESIndexMixin, RealRedisTestCase):
    """TestCase with isolated real Redis namespace AND isolated ES index."""

    def setUp(self):
        super().setUp()
        self._setup_es_index()

    def tearDown(self):
        self._teardown_es_index()
        super().tearDown()


class RealRedisTransactionTestCase(RedisNamespaceMixin, TransactionTestCase):
    """TransactionTestCase with isolated real Redis namespace (for concurrency tests)."""

    def setUp(self):
        super().setUp()
        self._setup_redis_namespace()

    def tearDown(self):
        self._teardown_redis_namespace()
        super().tearDown()
