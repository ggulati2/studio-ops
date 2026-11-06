"""Create (or reset) the founder login from environment variables.
Used at deploy time because free hosts have no shell:
FOUNDER_USERNAME, FOUNDER_PASSWORD, FOUNDER_NAME (optional)."""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from ops.models import Profile


class Command(BaseCommand):
    def handle(self, *args, **opts):
        username, password = os.environ.get("FOUNDER_USERNAME"), os.environ.get("FOUNDER_PASSWORD")
        if not (username and password):
            self.stdout.write("FOUNDER_USERNAME/FOUNDER_PASSWORD not set; skipping.")
            return
        User = get_user_model()
        u, created = User.objects.get_or_create(username=username.lower())
        if created or os.environ.get("FOUNDER_RESET_PASSWORD") == "1":
            u.set_password(password)
        u.first_name = u.first_name or os.environ.get("FOUNDER_NAME", username)
        u.is_staff = u.is_superuser = u.is_active = True
        u.save()
        Profile.objects.get_or_create(user=u, defaults={"role": Profile.ADMIN})
        self.stdout.write(f"Founder '{u.username}' {'created' if created else 'checked'}.")
