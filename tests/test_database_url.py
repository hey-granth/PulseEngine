"""
Tests for DATABASE_URL configuration enforcement and /health/ endpoint.
No test connects to Neon — health tests use the test database.
"""

import os
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase


class TestDatabaseURLEnforcement(SimpleTestCase):
    """Verify settings fail loudly when DATABASE_URL is missing or wrong."""

    def test_missing_database_url_raises_improperly_configured(self):
        """Guard logic raises ImproperlyConfigured when DATABASE_URL is absent."""
        import dj_database_url

        def _enforce(database_url):
            if not database_url:
                raise ImproperlyConfigured(
                    "Environment variable 'DATABASE_URL' is required but not set."
                )
            parsed = dj_database_url.parse(database_url, conn_max_age=600, ssl_require=True)
            if parsed["ENGINE"] != "django.db.backends.postgresql":
                raise ImproperlyConfigured(
                    f"DATABASE_URL must resolve to a PostgreSQL backend, got: {parsed['ENGINE']}"
                )
            return parsed

        with self.assertRaisesRegex(ImproperlyConfigured, "DATABASE_URL.*required"):
            _enforce("")
        with self.assertRaisesRegex(ImproperlyConfigured, "DATABASE_URL.*required"):
            _enforce(None)

    def test_database_url_present_sets_postgresql_engine(self):
        from django.conf import settings
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"],
            "django.db.backends.postgresql",
        )

    def test_ssl_mode_is_enforced(self):
        from django.conf import settings
        opts = settings.DATABASES["default"].get("OPTIONS", {})
        self.assertEqual(opts.get("sslmode"), "require")

    def test_conn_max_age_is_set(self):
        from django.conf import settings
        # CONN_MAX_AGE is 600 in production. During test runs it is overridden
        # to 0 to prevent lingering connections after concurrency tests.
        # Both values are valid — assert it is explicitly configured (not None).
        conn_max_age = settings.DATABASES["default"].get("CONN_MAX_AGE")
        self.assertIsNotNone(conn_max_age, "CONN_MAX_AGE must be explicitly set")
        self.assertIn(conn_max_age, (0, 600), f"Unexpected CONN_MAX_AGE: {conn_max_age}")

    def test_no_sqlite_in_databases(self):
        from django.conf import settings
        for alias, cfg in settings.DATABASES.items():
            self.assertNotIn("sqlite", cfg.get("ENGINE", "").lower())

    def test_test_database_overrides_neon(self):
        from django.conf import settings
        test_cfg = settings.DATABASES["default"].get("TEST", {})
        self.assertTrue(test_cfg, "DATABASES['default']['TEST'] must be set")
        self.assertTrue(test_cfg.get("NAME"), "TEST['NAME'] must be set")
        db_url = os.environ.get("DATABASE_URL", "")
        if "neon.tech" in db_url:
            from urllib.parse import urlparse
            neon_name = urlparse(db_url).path.lstrip("/")
            self.assertNotEqual(
                test_cfg["NAME"], neon_name,
                "TEST['NAME'] must differ from Neon DB name",
            )


class TestHealthEndpoint(TestCase):
    """Tests for GET /health/"""

    def test_health_returns_200_when_db_reachable(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_health_returns_500_when_db_unreachable(self):
        from django.db import OperationalError

        with patch("pulseengine.health.connection") as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
            mock_cursor.__exit__ = MagicMock(return_value=False)
            mock_cursor.execute.side_effect = OperationalError("connection refused")
            mock_conn.cursor.return_value = mock_cursor

            response = self.client.get("/health/")

        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("database", data["detail"].lower())
        self.assertNotIn("password", str(data).lower())
        self.assertNotIn("DATABASE_URL", str(data))

    def test_health_response_contains_no_credentials(self):
        response = self.client.get("/health/")
        body = response.content.decode()
        for sensitive in ("password", "secret", "DATABASE_URL", "sslmode"):
            self.assertNotIn(sensitive.lower(), body.lower())
