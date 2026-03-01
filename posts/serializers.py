from rest_framework import serializers

from posts.models import Post


class PostCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["id", "category", "content", "created_at", "is_flagged"]
        read_only_fields = ["id", "created_at", "is_flagged"]


class PostListSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source="author.username", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)

    class Meta:
        model = Post
        fields = [
            "id",
            "author",
            "author_username",
            "category",
            "category_slug",
            "content",
            "created_at",
            "is_flagged",
        ]
        read_only_fields = fields

