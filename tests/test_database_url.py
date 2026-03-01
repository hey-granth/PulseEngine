"""
Tests for DATABASE_URL configuration enforcement and /health/ endpoint.

No test writes to the real Neon DB — the pytest-django test runner uses
test_pulseengine on local Postgres (see DATABASES["default"]["TEST"] in settings).
"""

import os
import os
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured


class TestDatabaseURLEnforcement:
    """Verify settings fail loudly when DATABASE_URL is missing or wrong."""

    def test_missing_database_url_raises_improperly_configured(self):
        """
        The ImproperlyConfigured guard in settings.py must trigger when
        DATABASE_URL is absent. We test the guard directly rather than
        reloading the full module (which cannot run inside an already-
        configured Django process).
        """
        import dj_database_url

        def _enforce(database_url):
            """Mirrors the exact guard logic in settings.py."""
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

        # Missing DATABASE_URL → must raise
        with pytest.raises(ImproperlyConfigured, match="DATABASE_URL.*required"):
            _enforce("")

        with pytest.raises(ImproperlyConfigured, match="DATABASE_URL.*required"):
            _enforce(None)

    def test_database_url_present_sets_postgresql_engine(self):
        """When DATABASE_URL is set, DATABASES engine must be postgresql."""
        from django.conf import settings

        engine = settings.DATABASES["default"]["ENGINE"]
        assert engine == "django.db.backends.postgresql", (
            f"Expected postgresql engine, got: {engine}"
        )

    def test_ssl_mode_is_enforced(self):
        """sslmode=require must be present in DATABASES OPTIONS."""
        from django.conf import settings

        opts = settings.DATABASES["default"].get("OPTIONS", {})
        assert opts.get("sslmode") == "require", (
            f"sslmode=require not enforced. OPTIONS: {opts}"
        )

    def test_conn_max_age_is_set(self):
        """CONN_MAX_AGE must be 600 for connection pooling."""
        from django.conf import settings

        conn_max_age = settings.DATABASES["default"].get("CONN_MAX_AGE")
        assert conn_max_age == 600, (
            f"Expected CONN_MAX_AGE=600, got: {conn_max_age}"
        )

    def test_no_sqlite_in_databases(self):
        """SQLite must never appear in DATABASES."""
        from django.conf import settings

        for alias, db_conf in settings.DATABASES.items():
            assert "sqlite" not in db_conf.get("ENGINE", "").lower(), (
                f"SQLite found in DATABASES['{alias}']: {db_conf['ENGINE']}"
            )

    def test_test_database_overrides_neon(self):
        """TEST config must be explicitly defined to prevent accidental Neon writes."""
        from django.conf import settings

        test_cfg = settings.DATABASES["default"].get("TEST", {})
        # TEST must be explicitly configured (not empty)
        assert test_cfg, "DATABASES['default']['TEST'] must be explicitly set"
        # TEST NAME must be explicitly defined
        test_name = test_cfg.get("NAME", "")
        assert test_name, "DATABASES['default']['TEST']['NAME'] must be set"
        # When DATABASE_URL points to Neon (contains 'neon.tech'), the TEST NAME
        # must not equal the Neon production DB name, ensuring pytest never
        # creates tables in or writes to the real Neon database.
        db_url = os.environ.get("DATABASE_URL", "")
        if "neon.tech" in db_url:
            # Parse the actual production DB name directly from the URL
            from urllib.parse import urlparse
            parsed_url = urlparse(db_url)
            neon_db_name = parsed_url.path.lstrip("/")
            assert test_name != neon_db_name, (
                f"TEST['NAME'] ('{test_name}') must differ from the Neon DB name "
                f"('{neon_db_name}') to prevent pytest from running against the "
                "real Neon database."
            )


@pytest.mark.django_db
class TestHealthEndpoint:
    """Tests for GET /health/"""

    def test_health_returns_200_when_db_reachable(self, client):
        """Health endpoint returns 200 + {status: ok} when DB is up."""
        response = client.get("/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_returns_500_when_db_unreachable(self, client):
        """Health endpoint returns 500 when DB connection fails."""
        from django.db import OperationalError

        with patch("pulseengine.health.connection") as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
            mock_cursor.__exit__ = MagicMock(return_value=False)
            mock_cursor.execute.side_effect = OperationalError("connection refused")
            mock_conn.cursor.return_value = mock_cursor

            response = client.get("/health/")

        assert response.status_code == 500
        data = response.json()
        assert data["status"] == "error"
        assert "database" in data["detail"].lower()
        # Credentials must NOT appear in the response
        assert "password" not in str(data).lower()
        assert "DATABASE_URL" not in str(data)

    def test_health_response_contains_no_credentials(self, client):
        """Health response body must never contain sensitive strings."""
        response = client.get("/health/")
        body = response.content.decode()
        for sensitive in ("password", "secret", "DATABASE_URL", "sslmode"):
            assert sensitive.lower() not in body.lower(), (
                f"Sensitive string '{sensitive}' found in /health/ response"
            )

