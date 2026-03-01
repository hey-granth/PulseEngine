from django.urls import path

from ranking.views import GlobalFeedView, CategoryFeedView

urlpatterns = [
    path("feed/", GlobalFeedView.as_view(), name="feed-global"),
    path("feed/<slug:slug>/", CategoryFeedView.as_view(), name="feed-category"),
]
