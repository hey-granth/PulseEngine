from django.contrib import admin

from engagement.models import EngagementEvent, UserPostLike


@admin.register(EngagementEvent)
class EngagementEventAdmin(admin.ModelAdmin):
    list_display = ["id", "post", "user", "type", "created_at"]
    list_filter = ["type"]
    raw_id_fields = ["post", "user"]


@admin.register(UserPostLike)
class UserPostLikeAdmin(admin.ModelAdmin):
    list_display = ["id", "post", "user", "created_at"]
    raw_id_fields = ["post", "user"]
