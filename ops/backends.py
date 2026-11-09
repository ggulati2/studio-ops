"""Sign in with any capitalisation of the login ID (Pratik_Sharma = pratik_sharma)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        User = get_user_model()
        matches = list(User._default_manager.filter(username__iexact=username.strip())[:2])
        if len(matches) != 1:
            exact = [u for u in matches if u.username == username]
            if not exact:
                User().set_password(password)   # same time cost as a real check (no user-enumeration by timing)
                return None
            matches = exact
        user = matches[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user):
        """Contract editors are kept in the portal for planning and payments but never sign in.
        Also ends any session such a person still had (get_user checks this too)."""
        if not super().user_can_authenticate(user):
            return False
        prof = getattr(user, "profile", None)
        return not (prof and prof.employment == "contract")
