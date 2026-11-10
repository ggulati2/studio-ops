"""Two-step sign-in: set up an authenticator app, sign in with a code, recovery codes."""
import time

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .. import twofactor as T
from ..audit import record
from ..models import TwoFactor
from ..rbac import requires
from ..services import safe_next

__all__ = ["login_verify", "two_step", "two_step_reset"]
User = get_user_model()
PENDING_SECONDS = 5 * 60
SESSION_TRIES, USER_TRIES, WINDOW = 5, 10, 15 * 60


def start_second_step(request, user, next_url):
    """Called by the login view after a correct password for someone with two-step on:
    nothing is signed in yet; we only remember who passed step one, briefly."""
    request.session.cycle_key()
    request.session["2fa"] = {"uid": user.pk, "at": int(time.time()), "next": next_url,
                              "backend": getattr(user, "backend", "ops.backends.CaseInsensitiveBackend"), "tries": 0}
    return redirect("login_verify")


def login_verify(request):
    pend = request.session.get("2fa")
    if not pend or time.time() - pend["at"] > PENDING_SECONDS:
        request.session.pop("2fa", None)
        if request.method == "POST":
            messages.error(request, "That took too long. Sign in again.")
        return redirect("login")
    user = get_object_or_404(User, pk=pend["uid"], is_active=True)
    tf = TwoFactor.objects.filter(user=user, enabled_on__isnull=False).first()
    if not tf:                                    # turned off meanwhile: password was enough
        request.session.pop("2fa", None)
        login(request, user, backend=pend["backend"])
        return redirect(safe_next(request, pend.get("next"), "/"))
    error = None
    ukey = f"2fa-fail:{user.pk}"
    if request.method == "POST":
        code = request.POST.get("code", "")
        use_recovery = request.POST.get("mode") == "recovery"
        if cache.get(ukey, 0) >= USER_TRIES:
            error = "Too many wrong codes. Wait 15 minutes, or ask the other founder to reset your two-step sign-in."
        else:
            step = None if use_recovery else T.verify(tf.secret, code, tf.last_step)
            ok = step is not None or (use_recovery and T.use_recovery_code(tf, code))
            if ok:
                if step is not None:
                    tf.last_step = step
                    tf.save(update_fields=["last_step"])
                request.session.pop("2fa", None)
                cache.delete(ukey)
                cache.delete(f"login-fail:u:{user.username.lower()}")
                login(request, user, backend=pend["backend"])
                if use_recovery:
                    record(request, "auth.2fa_recovery_used", user.username, f"{len(tf.recovery_codes)} left")
                    messages.warning(request, f"You used a recovery code; {len(tf.recovery_codes)} left. "
                                              "Make new ones under Two-step sign-in if you're running low.")
                return redirect(safe_next(request, pend.get("next"), "/"))
            cache.set(ukey, cache.get(ukey, 0) + 1, WINDOW)
            pend["tries"] = pend.get("tries", 0) + 1
            request.session["2fa"] = pend
            record(request, "auth.2fa_failed", user.username)
            if pend["tries"] >= SESSION_TRIES:
                request.session.pop("2fa", None)
                messages.error(request, "Too many wrong codes. Sign in again.")
                return redirect("login")
            error = "That code isn't right. Use the newest code from your app." if not use_recovery else "That recovery code isn't right, or it was already used."
    return render(request, "login_verify.html", {"error": error, "name": user.profile.name if hasattr(user, "profile") else user.username,
                                                 "recovery": request.POST.get("mode") == "recovery" or request.GET.get("recovery") == "1"})


@login_required
def two_step(request):
    """Turn two-step sign-in on (scan a QR code, confirm a code), make new recovery
    codes, or turn it off (password + code)."""
    user = request.user
    tf = TwoFactor.objects.filter(user=user).first()
    enabled = bool(tf and tf.enabled)
    action = request.POST.get("action") if request.method == "POST" else None
    codes = None

    if not enabled:
        secret = request.session.get("2fa_setup")
        if not secret or request.GET.get("new") == "1":
            secret = T.new_secret()
            request.session["2fa_setup"] = secret
        if action == "enable":
            step = T.verify(secret, request.POST.get("code"))
            if step is None:
                messages.error(request, "That code didn't match. Check the app shows “Red Wed” and type the newest 6 digits.")
            else:
                codes, hashes = T.new_recovery_codes()
                TwoFactor.objects.update_or_create(user=user, defaults={
                    "secret": secret, "enabled_on": timezone.now(), "last_step": step, "recovery_codes": hashes})
                request.session.pop("2fa_setup", None)
                record(request, "auth.2fa_enabled", user.username)
                return render(request, "account/two_step.html", {"enabled": True, "codes": codes, "just_enabled": True})
        issuer = "Red Wed Studio"
        uri = T.otpauth_uri(secret, user.username, issuer)
        return render(request, "account/two_step.html", {"enabled": False, "qr": T.qr_svg(uri), "secret": secret,
                                                         "secret_spaced": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))})

    if action in ("codes", "disable"):
        step = T.verify(tf.secret, request.POST.get("code"), tf.last_step)
        if step is None:
            messages.error(request, "That code isn't right. Use the newest code from your app.")
        elif action == "disable" and not user.check_password(request.POST.get("password", "")):
            messages.error(request, "Your password isn't right.")
        else:
            tf.last_step = step
            if action == "codes":
                codes, tf.recovery_codes = T.new_recovery_codes()
                tf.save(update_fields=["last_step", "recovery_codes"])
                record(request, "auth.2fa_codes_renewed", user.username)
            else:
                tf.delete()
                record(request, "auth.2fa_disabled", user.username)
                messages.success(request, "Two-step sign-in is off. Your password alone now signs you in.")
                return redirect("two_step")
    return render(request, "account/two_step.html", {"enabled": True, "codes": codes, "tf": tf,
                                                     "left": len(tf.recovery_codes)})


@requires("people.manage")
def two_step_reset(request, pk):
    """A founder clears someone's two-step sign-in (lost phone and recovery codes)."""
    u = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        n, _ = TwoFactor.objects.filter(user=u).delete()
        record(request, "auth.2fa_reset", u.username, "by founder")
        messages.success(request, f"Two-step sign-in reset for {u.profile.name}. They sign in with their password and can turn it on again."
                         if n else f"{u.profile.name} didn't have two-step sign-in on.")
    return redirect("person_edit", pk=u.pk)
