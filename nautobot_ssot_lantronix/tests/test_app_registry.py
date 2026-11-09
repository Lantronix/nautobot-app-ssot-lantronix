"""Nautobot can list the app. Runs under the Nautobot test runner (nautobot-server test)."""

import importlib

from django.test import TestCase
from django.urls import reverse
from nautobot.users.models import User

from nautobot_ssot_lantronix import LantronixSSoTConfig


class AppRegistryTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create(username="admin", is_superuser=True))

    def test_package_config_is_the_app_config(self):
        # Nautobot reads <package>.config; a submodule named config would replace it at startup.
        self.assertIs(
            importlib.import_module("nautobot_ssot_lantronix").config, LantronixSSoTConfig
        )

    def test_installed_apps_page_loads(self):
        response = self.client.get(reverse("apps:apps_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lantronix SSoT")

    def test_app_detail_page_loads(self):
        response = self.client.get(reverse("apps:app_detail", args=["nautobot_ssot_lantronix"]))
        self.assertEqual(response.status_code, 200)
