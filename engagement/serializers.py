from rest_framework import serializers


class EngagementSerializer(serializers.Serializer):
    """Read-only response serializer for engagement actions."""

    detail = serializers.CharField()
    post_id = serializers.IntegerField()
    type = serializers.CharField()
