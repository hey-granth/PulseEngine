from django.urls import path

from engagement.views import LikeView, CommentView, ShareView

urlpatterns = [
    path("posts/<int:post_id>/like/", LikeView.as_view(), name="post-like"),
    path("posts/<int:post_id>/comment/", CommentView.as_view(), name="post-comment"),
    path("posts/<int:post_id>/share/", ShareView.as_view(), name="post-share"),
]
