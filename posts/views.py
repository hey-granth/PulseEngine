from rest_framework import generics, status
from rest_framework.response import Response

from posts.models import Post
from posts.serializers import PostCreateSerializer, PostListSerializer
from search.tasks import index_post_to_es


class PostCreateView(generics.CreateAPIView):
    """POST /posts/ — create a new post."""

    serializer_class = PostCreateSerializer

    def perform_create(self, serializer):
        post = serializer.save(author=self.request.user)
        # Fire async ES indexing
        index_post_to_es.delay(post.pk)
        return post


class PostListView(generics.ListAPIView):
    """GET /posts/ — list all posts."""

    queryset = Post.objects.select_related("author", "category").all()
    serializer_class = PostListSerializer


class PostDetailView(generics.RetrieveAPIView):
    """GET /posts/{id}/ — retrieve a single post."""

    queryset = Post.objects.select_related("author", "category").all()
    serializer_class = PostListSerializer
