"""Console buttons on device pages, run under nautobot-server test against real cables."""

import logging
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from nautobot.core.testing import TestCase
from nautobot.dcim.models import (
    Cable,
    ConsolePort,
    ConsoleServerPort,
    Device,
    DeviceType,
    Location,
    LocationType,
    Manufacturer,
)
from nautobot.extras.models import Role, Status
from nautobot.users.models import ObjectPermission

from nautobot_ssot_lantronix.bootstrap import ensure_objects
from nautobot_ssot_lantronix.constants import CF_MGMT_IP
from nautobot_ssot_lantronix.template_content import (
    DeviceConsole,
    device_console_paths,
    served_ports,
    ssh_target,
)
from nautobot_ssot_lantronix.tests.fakes import cfg


def grant(user, models, name):
    perm = ObjectPermission.objects.create(name=name, actions=["view"])
    perm.object_types.set([ContentType.objects.get_for_model(m) for m in models])
    perm.users.add(user)


class DeviceConsoleTests(TestCase):
    def setUp(self):
        super().setUp()
        ensure_objects(cfg(), logging.getLogger("test"))
        active = Status.objects.get(name="Active")
        connected = Status.objects.get(name="Connected")
        device_ct = ContentType.objects.get_for_model(Device)
        loc_type = LocationType.objects.create(name="Site")
        loc_type.content_types.add(device_ct)
        site = Location.objects.create(name="site-a", location_type=loc_type, status=active)
        lantronix, _ = Manufacturer.objects.get_or_create(name="Lantronix")
        vendor = Manufacturer.objects.create(name="Vendor")
        slc_type = DeviceType.objects.create(manufacturer=lantronix, model="SLC9032")
        sw_type = DeviceType.objects.create(manufacturer=vendor, model="Switch")
        slc_role, _ = Role.objects.get_or_create(name=cfg().default_role)
        sw_role = Role.objects.create(name="Switch")
        for role in (slc_role, sw_role):
            role.content_types.add(device_ct)
        self.slc = Device.objects.create(
            name="slc-01", device_type=slc_type, role=slc_role, location=site, status=active
        )
        self.slc._custom_field_data["percepxion_device_id"] = "dev-0001"
        self.slc._custom_field_data[CF_MGMT_IP] = "192.0.2.10"
        self.slc.save()
        self.port = ConsoleServerPort.objects.create(device=self.slc, name="Port-02")
        self.idle = ConsoleServerPort.objects.create(device=self.slc, name="Port-03")
        self.switch = Device.objects.create(
            name="sw-01", device_type=sw_type, role=sw_role, location=site, status=active
        )
        self.console = ConsolePort.objects.create(device=self.switch, name="Console")
        Cable.objects.create(termination_a=self.console, termination_b=self.port, status=connected)
        self.other = Device.objects.create(
            name="sw-02", device_type=sw_type, role=sw_role, location=site, status=active
        )
        self.admin = get_user_model().objects.create(username="console-admin", is_superuser=True)

    def set_ssh_port(self, value):
        self.port._custom_field_data["lx_ssh_port"] = value
        self.port.save()

    def page(self, device, user=None):
        context = {"object": device, "request": SimpleNamespace(user=user or self.admin)}
        extension = DeviceConsole(context)
        return extension.buttons(), extension.right_page()

    def test_cabled_device_page_has_the_console_button(self):
        buttons, _ = self.page(self.switch)
        self.assertIn('href="ssh://192.0.2.10:3002"', buttons)
        self.assertIn("slc-01 Port-02", buttons)  # the tooltip says which port it goes through

    def test_button_uses_the_ports_own_ssh_port(self):
        self.set_ssh_port("22002")
        self.assertIn('href="ssh://192.0.2.10:22002"', self.page(self.switch)[0])

    def test_no_button_when_ssh_to_the_port_is_off(self):
        self.set_ssh_port("off")
        self.assertEqual(self.page(self.switch)[0], "")

    def test_device_with_no_console_cable_has_no_button(self):
        self.assertEqual(self.page(self.other), ("", ""))

    def test_no_button_for_an_address_no_console_server_is_at(self):
        for address in ("127.0.0.1", "169.254.1.1", "not-an-ip", ""):
            self.slc._custom_field_data[CF_MGMT_IP] = address
            self.slc.save()
            port = ConsoleServerPort.objects.get(pk=self.port.pk)
            self.assertIsNone(ssh_target(port, 3000), address)

    def test_button_needs_view_on_the_console_server_side(self):
        users = get_user_model().objects
        limited = users.create(username="limited")

        def paths():
            # A fresh user object each time: permissions are cached on the instance.
            return device_console_paths(users.get(pk=limited.pk), self.switch)

        grant(limited, [ConsolePort, Cable], "switch side")
        self.assertEqual(paths(), [])
        grant(limited, [ConsoleServerPort], "server port")
        self.assertEqual(paths(), [])  # not the console server itself
        grant(limited, [Device], "devices")
        [path] = paths()
        self.assertEqual(path["ssh_target"], "192.0.2.10:3002")

    def test_console_server_page_lists_its_ports_in_use(self):
        buttons, panel = self.page(self.slc)
        self.assertEqual(buttons, "")  # the console server has no console cable of its own
        self.assertIn("Console ports in use", panel)
        self.assertIn('href="ssh://192.0.2.10:3002"', panel)
        self.assertIn(self.switch.get_absolute_url(), panel)
        self.assertNotIn(":3003", panel)  # Port-03 has no cable and nothing attached

    def test_console_server_page_lists_a_port_percepxion_reports_attached(self):
        self.idle._custom_field_data["lx_attached"] = True
        self.idle.description = "edge-router-01"
        self.idle.save()
        rows = served_ports(self.admin, self.slc)
        self.assertEqual([r["port"].name for r in rows], ["Port-02", "Port-03"])
        self.assertIn("edge-router-01", self.page(self.slc)[1])

    def test_a_device_that_is_not_a_synced_console_server_gets_no_panel(self):
        self.assertEqual(self.page(self.switch)[1], "")
