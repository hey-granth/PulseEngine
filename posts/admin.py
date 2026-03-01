from django.contrib import admin

from posts.models import Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["id", "author", "category", "created_at", "is_flagged"]
    list_filter = ["is_flagged", "category"]
    raw_id_fields = ["author"]

