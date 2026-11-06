from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from ..audit import client_ip, record
from ..models import StoredImage
from ..rbac import ADMIN, DRIVE, EDITOR, QC, TL, has_perm, role_of

__all__ = ["home", "image", "health", "cron_daily", "ThrottledLogin", "PasswordChange", "no_access"]

HOME = {ADMIN: "dashboard", TL: "pod", EDITOR: "today", QC: "qc_queue", DRIVE: "drives"}


@login_required
def home(request):
    r = role_of(request.user)
    if r is None:
        return render(request, "no_access.html", status=403)
    return redirect(HOME[r])


def no_access(request, exception=None):
    return render(request, "no_access.html", status=403)


@login_required
def image(request, pk):
    """Photos are private. Drive photos: drive desk and founders. Employee of the Month photos: everyone signed in."""
    img = get_object_or_404(StoredImage, pk=pk)
    is_drive_photo = img.drives.exists() or img.drivereturn_set.exists()
    if is_drive_photo and not has_perm(request.user, "drives.manage"):
        raise PermissionDenied
    if not is_drive_photo and role_of(request.user) is None:
        raise PermissionDenied
    resp = HttpResponse(bytes(img.data), content_type=img.content_type)
    resp["Cache-Control"] = "private, max-age=86400"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Disposition"] = "inline"
    return resp


def health(request):
    """For a free uptime pinger: no login, no database, keeps the host awake."""
    return HttpResponse("ok", content_type="text/plain")


@csrf_exempt
@require_GET
def cron_daily(request):
    """Called once a day by cron-job.org (Authorization: Bearer CRON_SECRET).
    Touches the database so the free Supabase project never pauses for inactivity,
    turns past-due invoices Overdue, and clears expired sign-in sessions."""
    import hmac

    from django.conf import settings
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    from ..services import refresh_overdue
    expected = f"Bearer {settings.CRON_SECRET}"
    if not settings.CRON_SECRET or not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
        return HttpResponse(status=401)
    refresh_overdue()
    removed, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
    return HttpResponse(f"ok, {removed} expired sessions removed", content_type="text/plain")


class ThrottledLogin(auth_views.LoginView):
    """Blocks a login name for 15 minutes after 8 wrong passwords, and an IP after 30.
    Counters live in the database cache, so every server process sees them."""
    template_name = "login.html"
    redirect_authenticated_user = True
    USER_LIMIT, IP_LIMIT, WINDOW = 8, 30, 15 * 60

    def _keys(self):
        return (f"login-fail:u:{self.request.POST.get('username', '').strip().lower()[:150]}",
                f"login-fail:ip:{client_ip(self.request)}")

    def _locked(self):
        ku, ki = self._keys()
        return cache.get(ku, 0) >= self.USER_LIMIT or cache.get(ki, 0) >= self.IP_LIMIT

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["timed_out"] = self.request.GET.get("timeout") == "1"
        return ctx

    def post(self, request, *args, **kwargs):
        if self._locked():
            record(request, "auth.locked_out", request.POST.get("username", "")[:150])
            form = self.get_form()
            form.add_error(None, "Too many wrong attempts. Try again in 15 minutes or ask a founder.")
            return self.render_to_response(self.get_context_data(form=form, locked=True), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        for k in self._keys():
            cache.set(k, cache.get(k, 0) + 1, self.WINDOW)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._keys()[0])
        return super().form_valid(form)


class PasswordChange(auth_views.PasswordChangeView):
    template_name = "form_page.html"
    success_url = "/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        forced = getattr(getattr(self.request.user, "profile", None), "must_change_password", False)
        ctx.update(title="Choose your own password" if forced else "Change my password", submit="Change password",
                   intro=("A founder set your current password. Choose a new one only you know before you continue."
                          if forced else "At least 8 characters, not only numbers, not too close to your name."))
        return ctx

    def form_valid(self, form):
        resp = super().form_valid(form)
        prof = getattr(self.request.user, "profile", None)
        if prof and prof.must_change_password:
            prof.must_change_password = False
            prof.save(update_fields=["must_change_password"])
        record(self.request, "auth.password_changed", self.request.user.username)
        messages.success(self.request, "Password changed.")
        return resp
