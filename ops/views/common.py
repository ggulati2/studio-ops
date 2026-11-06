from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect

from ..access import ADMIN, DRIVE, EDITOR, QC, TL
from ..models import StoredImage
from ..services import role_of

__all__ = ["home", "image", "health", "ThrottledLogin"]

HOME = {ADMIN: "dashboard", TL: "pod", EDITOR: "today", QC: "qc_queue", DRIVE: "drives"}


@login_required
def home(request):
    return redirect(HOME.get(role_of(request.user), "logout"))


@login_required
def image(request, pk):
    img = get_object_or_404(StoredImage, pk=pk)
    resp = HttpResponse(bytes(img.data), content_type=img.content_type)
    resp["Cache-Control"] = "private, max-age=86400"
    return resp


def health(request):
    """For a free uptime pinger: no login, no database, keeps the host awake."""
    return HttpResponse("ok", content_type="text/plain")


class ThrottledLogin(auth_views.LoginView):
    """Blocks a login name + IP for 15 minutes after 8 wrong passwords."""
    template_name = "login.html"
    redirect_authenticated_user = True
    LIMIT, WINDOW = 8, 15 * 60

    def _key(self):
        ip = (self.request.META.get("HTTP_X_FORWARDED_FOR") or self.request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
        return f"login-fail:{self.request.POST.get('username', '').lower()}:{ip}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(), 0) >= self.LIMIT:
            form = self.get_form()
            form.add_error(None, "Too many wrong attempts. Try again in 15 minutes or ask a founder.")
            return self.render_to_response(self.get_context_data(form=form, locked=True))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        k = self._key()
        cache.set(k, cache.get(k, 0) + 1, self.WINDOW)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key())
        return super().form_valid(form)
