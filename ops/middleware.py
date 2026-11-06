"""Security middleware: headers, forced password change, admin idle timeout."""
import time

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "object-src 'none'",
])


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CSP)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.user.is_authenticated and not request.path.startswith("/static/"):
            response["Cache-Control"] = "private, no-store"
        return response


class PasswordAndSessionMiddleware:
    """• People whose password was set by a founder must choose their own first.
    • Founders are signed out after ADMIN_IDLE_MINUTES without activity."""
    ALLOW = ("/password/", "/logout/", "/static/", "/health/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            from .rbac import ADMIN, role_of
            now = int(time.time())
            if role_of(user) == ADMIN:
                last = request.session.get("last_seen", now)
                if now - last > settings.ADMIN_IDLE_MINUTES * 60:
                    logout(request)
                    return redirect(f"{reverse('login')}?next={request.path}&timeout=1")
            request.session["last_seen"] = now
            prof = getattr(user, "profile", None)
            if prof and prof.must_change_password and not request.path.startswith(self.ALLOW):
                return redirect("password_change")
        return self.get_response(request)


class AuditMiddleware:
    """Every successful change (POST) by a signed-in person goes into the audit log:
    who, which screen, which record, and the non-secret values they chose."""
    SKIP = {"login", "logout", "person_new", "person_edit"}      # recorded with more detail elsewhere
    SECRET = ("password", "csrfmiddlewaretoken", "token")
    SHOW = ("status", "decision", "role", "day_type", "action", "received", "kind", "user", "editor", "date", "month")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method == "POST" and request.user.is_authenticated and response.status_code < 400:
            match = getattr(request, "resolver_match", None)
            name = match.url_name if match else ""
            if name and name not in self.SKIP:
                from .audit import record
                keys = [k for k in request.POST if not any(s in k.lower() for s in self.SECRET)]
                shown = {k: request.POST.get(k)[:40] for k in keys if k in self.SHOW and request.POST.get(k)}
                detail = ", ".join(f"{k}={v}" for k, v in shown.items())
                if request.FILES:
                    detail += f"{', ' if detail else ''}{sum(len(request.FILES.getlist(k)) for k in request.FILES)} photo(s)"
                target = " ".join(str(v) for v in match.kwargs.values()) if match.kwargs else ""
                record(request, f"change.{name}"[:60], target or request.path, detail)
        return response
