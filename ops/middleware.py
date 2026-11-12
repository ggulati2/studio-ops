"""Security middleware: headers, forced password change, admin idle timeout."""
import logging
import time

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

SESSION_TOUCH_SECONDS = 300
SLOW_MS = 800
MEMORY_JUMP_MB = 40


def _peak_mb():
    """This process's highest memory use so far (Linux reports KB)."""
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
log = logging.getLogger("ops.perf")

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
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
            if response.get("Content-Type", "").startswith("text/html"):
                response["Speculation-Rules"] = '"/speculation-rules.json"'     # hover-to-preload (ops/views/common.py)
        return response


class PasswordAndSessionMiddleware:
    """• People whose password was set by a founder must choose their own first.
    • Founders are signed out after ADMIN_IDLE_MINUTES without activity, everyone else after
      STAFF_IDLE_MINUTES (a working day), so a shared studio PC doesn't stay signed in overnight."""
    ALLOW = ("/password/", "/logout/", "/static/", "/health/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            from .rbac import ADMIN, role_of
            now = int(time.time())
            limit = settings.ADMIN_IDLE_MINUTES if role_of(user) == ADMIN else settings.STAFF_IDLE_MINUTES
            last = request.session.get("last_seen", now)
            if now - last > limit * 60:
                logout(request)
                return redirect(f"{reverse('login')}?next={request.path}&timeout=1")
            # Touch the session at most every 5 minutes instead of writing it on every click.
            # That still slides the 7-day sign-in and keeps the founders' idle timeout accurate.
            if now - request.session.get("last_seen", 0) >= SESSION_TOUCH_SECONDS:
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
        if (request.method == "POST" and request.user.is_authenticated and response.status_code < 400
                and not getattr(request, "_audited", False)):      # the view already logged it, in more detail
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


class RequestMemoMiddleware:
    """Opens the per-request memo (ops/perf.py), counts database queries, and logs any
    request slower than SLOW_MS with its query count, so slow pages show up in the logs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.db import connection

        from . import perf
        counter = {"n": 0}

        def count(execute, sql, params, many, context):
            counter["n"] += 1
            return execute(sql, params, many, context)

        perf.begin()
        peak_before = _peak_mb()
        start = time.perf_counter()
        try:
            with connection.execute_wrapper(count):
                response = self.get_response(request)
        finally:
            perf.end()
        ms = (time.perf_counter() - start) * 1000
        if ms > SLOW_MS and not request.path.startswith("/static/"):
            log.warning("slow request %s %s: %.0f ms, %d queries", request.method, request.path, ms, counter["n"])
        grew = _peak_mb() - peak_before
        if grew >= MEMORY_JUMP_MB:     # this request pushed the process's memory peak up a lot: say which one
            log.warning("memory jump %s %s: peak +%d MB (now %d MB)", request.method, request.path, grew, _peak_mb())
        if settings.DEBUG:
            response["Server-Timing"] = f'app;dur={ms:.0f};desc="{counter["n"]} queries"'
        return response
