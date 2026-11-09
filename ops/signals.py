"""Keeps caches honest: any write clears the per-request memo, and writes to the
records behind a shared cache drop that cache for every server process."""
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from . import perf

HOURS_MODELS = {"DayLog", "TaskEntry", "ExtraEntry", "Project"}
BADGE_MODELS = {"DelayReport", "LeaveRequest", "EmployeeOfMonth"}


def _changed(sender, **kwargs):
    perf.clear()
    name = sender.__name__
    if name in HOURS_MODELS:
        cache.delete("hours_all")
    if name in BADGE_MODELS:
        cache.delete("nav_badges")


def connect():
    post_save.connect(_changed, dispatch_uid="ops-cache-save")
    post_delete.connect(_changed, dispatch_uid="ops-cache-delete")
