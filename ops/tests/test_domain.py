from django.conf import settings
from django.test import SimpleTestCase


class StudioDomain(SimpleTestCase):
    def test_redwed_in_is_allowed_and_trusted_for_forms(self):
        for host in ("redwed.in", "www.redwed.in"):
            self.assertIn(host, settings.ALLOWED_HOSTS)
            self.assertIn(f"https://{host}", settings.CSRF_TRUSTED_ORIGINS)

    def test_requests_on_the_domain_are_not_rejected(self):
        for host in ("redwed.in", "www.redwed.in"):
            r = self.client.get("/health/", HTTP_HOST=host, secure=True)
            self.assertEqual(r.status_code, 200, host)
