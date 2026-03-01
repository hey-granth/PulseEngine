from django.conf import settings
from django.db import models


class EngagementType(models.TextChoices):
    LIKE = "LIKE", "Like"
    COMMENT = "COMMENT", "Comment"
    SHARE = "SHARE", "Share"


class EngagementEvent(models.Model):
    post = models.ForeignKey(
        "posts.Post",
        on_delete=models.CASCADE,
        related_name="engagement_events",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="engagement_events",
        db_index=True,
    )
    type = models.CharField(
        max_length=10,
        choices=EngagementType.choices,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["post", "type", "-created_at"]),
            models.Index(fields=["post", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.type} on Post {self.post_id} by User {self.user_id}"


class UserPostLike(models.Model):
    post = models.ForeignKey(
        "posts.Post",
        on_delete=models.CASCADE,
        related_name="likes",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="liked_posts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["post", "user"],
                name="unique_user_post_like",
            )
        ]

    def __str__(self):
        return f"Like: User {self.user_id} → Post {self.post_id}"
