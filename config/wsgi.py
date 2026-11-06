"""WSGI entrypoint (Vercel detects it through WSGI_APPLICATION)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()


def _prepare_preview_demo():
    """Pull-request previews run on a private demo database in /tmp, rebuilt on each cold start,
    so a half-finished branch can never read or change real studio data."""
    from django.conf import settings
    if not getattr(settings, "PREVIEW_DEMO", False):
        return
    import fcntl
    from django.core.management import call_command
    with open("/tmp/studio-ops-preview.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not os.path.exists("/tmp/studio-ops-preview.ready"):
            call_command("migrate", verbosity=0, interactive=False)
            call_command("createcachetable", verbosity=0)
            call_command("seed_demo", verbosity=0)
            open("/tmp/studio-ops-preview.ready", "w").close()


_prepare_preview_demo()
