"""
URL configuration for PulseEngine project.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # App URLs
    path("", include("categories.urls")),
    path("", include("posts.urls")),
    path("", include("engagement.urls")),
    path("", include("ranking.urls")),
    path("", include("search.urls")),
]
