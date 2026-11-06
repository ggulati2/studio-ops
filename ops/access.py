"""Role-based access. Every view declares who may open it; the server
enforces it, so hiding a menu link is never the only protection."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Profile
from .services import role_of

ADMIN, TL, EDITOR, QC, DRIVE = Profile.ADMIN, Profile.TL, Profile.EDITOR, Profile.QC, Profile.DRIVE
EDITING = (TL, EDITOR)
EVERYONE = (ADMIN, TL, EDITOR, QC, DRIVE)


def roles(*allowed):
    def deco(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            r = role_of(request.user)
            if r not in allowed:
                raise PermissionDenied("Your role can't open this page.")
            request.role = r
            return view(request, *args, **kwargs)
        return wrapper
    return deco
