"""
Celery app for PulseEngine.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pulseengine.settings")

app = Celery("pulseengine")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ── Beat schedule ───────────────────────────────────────────────────────────
app.conf.beat_schedule = {
    "recalculate-dirty-scores": {
        "task": "ranking.tasks.recalculate_dirty_scores",
        "schedule": 5.0,
    },
    "merge-global-leaderboard": {
        "task": "ranking.tasks.merge_global_leaderboard",
        "schedule": 10.0,
    },
}

