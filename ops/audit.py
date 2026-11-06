"""Write audit events. Never raises: auditing must not break the app."""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

log = logging.getLogger("ops.audit")


def client_ip(request):
    if request is None:
        return None
    # Render sits behind Cloudflare, which sets CF-Connecting-IP at its edge (a client can't
    # forge it). Fallback: Render puts the real client first in X-Forwarded-For.
    cf = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
    if cf:
        return cf
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = (fwd.split(",")[0] if fwd else request.META.get("REMOTE_ADDR", "")).strip()
    return ip or None


def record(request, action, target="", detail="", user=None):
    from .models import AuditEvent
    try:
        u = user or (request.user if request is not None and getattr(request.user, "is_authenticated", False) else None)
        AuditEvent.objects.create(user=u, username=getattr(u, "username", "") or "", action=action[:60],
                                  target=str(target)[:200], detail=str(detail)[:500], ip=client_ip(request))
    except Exception:  # pragma: no cover
        log.exception("audit write failed")


@receiver(user_logged_in)
def _in(sender, request, user, **kw):
    record(request, "auth.login", user.username, user=user)


@receiver(user_logged_out)
def _out(sender, request, user, **kw):
    if user:
        record(request, "auth.logout", user.username, user=user)


@receiver(user_login_failed)
def _failed(sender, credentials, request=None, **kw):
    record(request, "auth.login_failed", credentials.get("username", "")[:150])
