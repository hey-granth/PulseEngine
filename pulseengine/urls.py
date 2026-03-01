"""
URL configuration for PulseEngine project.
"""
from django.contrib import admin
from django.urls import include, path

from pulseengine.health import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    # App URLs
    path("", include("categories.urls")),
    path("", include("posts.urls")),
    path("", include("engagement.urls")),
    path("", include("ranking.urls")),
    path("", include("search.urls")),
]
