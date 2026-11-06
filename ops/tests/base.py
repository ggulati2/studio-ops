from django.core.management import call_command
from django.test import Client, TestCase

PW = "demo-pass-123"


class SeededTestCase(TestCase):
    """Loads the demo studio once per test class (rolled back after each test)."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=open("/dev/null", "w"))

    def login(self, username):
        c = Client()
        self.assertTrue(c.login(username=username, password=PW), username)
        return c
