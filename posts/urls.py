from django.urls import path

from posts.views import PostCreateView, PostListView, PostDetailView

urlpatterns = [
    path("posts/", PostCreateView.as_view(), name="post-create"),
    path("posts/list/", PostListView.as_view(), name="post-list"),
    path("posts/<int:pk>/", PostDetailView.as_view(), name="post-detail"),
]
