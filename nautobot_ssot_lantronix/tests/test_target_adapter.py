"""Target adapter and sync under the Nautobot test runner."""

import logging

from diffsync.enum import DiffSyncFlags
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.dcim.models import (
    ConsoleServerPort,
    ConsoleServerPortTemplate,
    Device,
    DeviceType,
    Location,
    LocationType,
    Manufacturer,
)
from nautobot.extras.models import Role, Status

from ..bootstrap import ensure_objects
from ..diffsync.nautobot import NautobotAdapter
from ..diffsync.percepxion import PercepxionAdapter
from .fakes import RULES, FakeClient, cfg, device, port

FLAGS = DiffSyncFlags.SKIP_UNMATCHED_DST


class FakeJob:
    logger = logging.getLogger("test")
    source_adapter = None


class SyncTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        lantronix = Manufacturer.objects.create(name="Lantronix")
        dt = DeviceType.objects.create(manufacturer=lantronix, model="SLC9016-16E")
        for n in range(1, 17):
            ConsoleServerPortTemplate.objects.create(device_type=dt, name=f"Port-{n:02d}")
        lt = LocationType.objects.create(name="Site")
        lt.content_types.add(ContentType.objects.get_for_model(Device))
        active = Status.objects.get(name="Active")
        active.content_types.add(ContentType.objects.get_for_model(Device))
        cls.location = Location.objects.create(name="Lab", location_type=lt, status=active)
        cls.dt = dt
        ensure_objects(cfg(), logging.getLogger("test"))
        Role.objects.get(name="Console Server")

    def sync(self, devices=None, ports=None, config=None):
        job = FakeJob()
        config = config or cfg(RULES)
        client = FakeClient(
            devices or [device()], ports if ports is not None else [port(1, name="SLC-B")]
        )
        source = PercepxionAdapter(job=job, config=config, client=client)
        job.source_adapter = source
        source.load()
        target = NautobotAdapter(job=job, config=config)
        target.load()
        diff = source.diff_to(target, flags=FLAGS)
        source.sync_to(target, flags=FLAGS)
        return diff

    def test_creates_device_with_type_location_role_and_link(self):
        self.sync()
        dev = Device.objects.get(name="SLC-A")
        self.assertEqual(
            (dev.device_type, dev.location.name, dev.role.name, dev.platform.network_driver),
            (self.dt, "Lab", "Console Server", "lantronix_itmgos"),
        )
        self.assertEqual(dev.cf["percepxion_device_id"], "dev-0001")
        self.assertEqual(ConsoleServerPort.objects.get(device=dev, name="Port-01").label, "SLC-B")

    def test_rerun_with_no_changes_has_empty_diff(self):
        self.sync()
        self.assertFalse(self.sync().has_diffs())

    def test_rename_updates_and_never_duplicates(self):
        self.sync()
        self.sync(devices=[device(name="SLC-A2")])
        self.assertEqual(list(Device.objects.values_list("name", flat=True)), ["SLC-A2"])

    def test_port_rename_is_one_update(self):
        self.sync()
        diff = self.sync(ports=[port(1, name="SLC-C")])
        self.assertEqual(diff.summary()["update"], 1)

    def test_non_lantronix_device_with_same_name_left_alone(self):
        other_mfr = Manufacturer.objects.create(name="Cisco")
        other_dt = DeviceType.objects.create(manufacturer=other_mfr, model="C3560")
        cisco = Device.objects.create(
            name="SLC-A",
            device_type=other_dt,
            location=self.location,
            role=Role.objects.get(name="Console Server"),
            status=Status.objects.get(name="Active"),
        )
        self.sync()
        cisco.refresh_from_db()
        self.assertEqual(
            (cisco.device_type, cisco.cf.get("percepxion_device_id")), (other_dt, None)
        )

    def test_name_only_adoption_refused_when_nautobot_has_a_serial(self):
        existing = Device.objects.create(
            name="SLC-A",
            device_type=self.dt,
            location=self.location,
            role=Role.objects.get(name="Console Server"),
            status=Status.objects.get(name="Active"),
            serial="999999999999",
        )
        self.sync(devices=[device(serial="")])
        existing.refresh_from_db()
        self.assertIsNone(existing.cf.get("percepxion_device_id"))

    def other_location_device(self, name, serial):
        from nautobot.tenancy.models import Tenant

        remote = Location.objects.create(
            name="Remote",
            location_type=self.location.location_type,
            status=Status.objects.get(name="Active"),
        )
        return Device.objects.create(
            name=name,
            device_type=self.dt,
            location=remote,
            tenant=Tenant.objects.create(name="Other Tenant"),
            role=Role.objects.get(name="Console Server"),
            status=Status.objects.get(name="Active"),
            serial=serial,
        )

    def test_no_adoption_by_name_outside_the_adoption_locations(self):
        existing = self.other_location_device("SLC-A", "")
        self.sync()
        existing.refresh_from_db()
        self.assertIsNone(existing.cf.get("percepxion_device_id"))
        self.assertEqual((existing.serial, Device.objects.count()), ("", 1))

    def test_no_adoption_or_rename_by_serial_outside_the_adoption_locations(self):
        existing = self.other_location_device("BRANCH-CONSOLE-7", "000000000001")
        self.sync()
        existing.refresh_from_db()
        self.assertEqual(existing.name, "BRANCH-CONSOLE-7")
        self.assertIsNone(existing.cf.get("percepxion_device_id"))

    def test_adoption_in_a_listed_location_or_any(self):
        for scope in (("Remote",), ("*",)):
            with self.subTest(scope=scope):
                existing = self.other_location_device("BRANCH-CONSOLE-7", "000000000001")
                self.sync(config=cfg(RULES, adoption_locations=scope))
                existing.refresh_from_db()
                self.assertEqual(
                    (existing.name, existing.cf.get("percepxion_device_id")), ("SLC-A", "dev-0001")
                )
                existing.delete()
                Location.objects.get(name="Remote").delete()
                existing.tenant.delete()

    def test_no_adoption_when_a_location_name_is_ambiguous(self):
        # Location names are unique only under their parent: a second "Lab" elsewhere
        # must not fall inside the adoption scope by sharing the name.
        region_type = LocationType.objects.create(name="Region")
        self.location.location_type.parent = region_type
        self.location.location_type.save()
        active = Status.objects.get(name="Active")
        east = Location.objects.create(name="East", location_type=region_type, status=active)
        west = Location.objects.create(name="West", location_type=region_type, status=active)
        Location.objects.filter(pk=self.location.pk).update(parent=east)
        other_lab = Location.objects.create(
            name="Lab", location_type=self.location.location_type, parent=west, status=active
        )
        existing = Device.objects.create(
            name="SLC-A",
            device_type=self.dt,
            location=other_lab,
            role=Role.objects.get(name="Console Server"),
            status=active,
        )
        with self.assertLogs("test", "WARNING") as logs:
            self.sync()
        existing.refresh_from_db()
        self.assertIsNone(existing.cf.get("percepxion_device_id"))
        self.assertTrue(any("more than one location is named Lab" in m for m in logs.output))

    def test_adoption_under_a_child_of_the_default_location(self):
        child_type = LocationType.objects.create(name="Room", parent=self.location.location_type)
        child_type.content_types.add(ContentType.objects.get_for_model(Device))
        room = Location.objects.create(
            name="Room 1",
            location_type=child_type,
            parent=self.location,
            status=Status.objects.get(name="Active"),
        )
        existing = Device.objects.create(
            name="SLC-A",
            device_type=self.dt,
            location=room,
            role=Role.objects.get(name="Console Server"),
            status=Status.objects.get(name="Active"),
        )
        self.sync()
        existing.refresh_from_db()
        self.assertEqual(existing.cf.get("percepxion_device_id"), "dev-0001")

    def test_port_nautobot_rejects_is_skipped_and_the_run_finishes(self):
        from unittest import mock

        from django.core.exceptions import ValidationError

        real = ConsoleServerPort.validated_save

        def save(port, *args, **kwargs):
            if port.name == "Port-01":
                raise ValidationError("Label policy forbids this value")
            return real(port, *args, **kwargs)

        ports = [port(1, name="SLC-B"), port(2, name="SLC-C")]
        with (
            mock.patch.object(ConsoleServerPort, "validated_save", save),
            self.assertLogs("test", "WARNING") as logs,
        ):
            self.sync(ports=ports)
        dev = Device.objects.get(name="SLC-A")
        labels = dict(
            ConsoleServerPort.objects.filter(
                device=dev, name__in=["Port-01", "Port-02"]
            ).values_list("name", "label")
        )
        self.assertEqual(labels, {"Port-01": "", "Port-02": "SLC-C"})
        self.assertTrue(any("Port Port-01 on SLC-A: not saved" in line for line in logs.output))

    def test_device_missing_from_percepxion_is_not_deleted(self):
        self.sync()
        self.sync(devices=[device("dev-0002", "SLC-B", serial="000000000002")], ports=[])
        self.assertEqual(Device.objects.count(), 2)

    def test_existing_device_type_never_changes(self):
        self.sync()
        other = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.get(name="Lantronix"), model="SLC9016"
        )
        dev = Device.objects.get(name="SLC-A")
        rules = {("SLC9016", 16, 0): "lantronix-slc9016"}
        job = FakeJob()
        source = PercepxionAdapter(job=job, config=cfg(rules), client=FakeClient([device()], []))
        job.source_adapter = source
        source.load()
        target = NautobotAdapter(job=job, config=cfg(rules))
        target.load()
        source.sync_to(target, flags=FLAGS)
        dev.refresh_from_db()
        self.assertNotEqual(dev.device_type, other)

    def test_last_seen_alone_is_not_a_diff(self):
        import json

        from ..percepxion.telemetry import parse_port_states
        from .fakes import FIXTURE

        states = parse_port_states(json.loads(FIXTURE.read_text()))
        ports = [port(14, name="switch-01")]
        self.sync_with_states(states, ports)
        moved = {n: s for n, s in states.items()}
        from dataclasses import replace

        moved[14] = replace(states[14], last_seen="2030-01-01T00:00:00-00:00")
        self.assertFalse(self.sync_with_states(moved, ports).has_diffs())
        p = ConsoleServerPort.objects.get(device__name="SLC-A", name="Port-14")
        self.assertEqual(p.cf["lx_attached_model"], "WS-C3560CG-8TC-S")
        self.assertTrue(p.cf["lx_attached_last_seen"])

    def render_link(self, port):
        from nautobot.core.utils.data import render_jinja2
        from nautobot.extras.models import CustomLink

        link = CustomLink.objects.get(name="Lantronix: Open console")
        context = {"obj": port}
        return render_jinja2(link.text, context).strip(), render_jinja2(link.target_url, context)

    def test_open_console_link_only_on_numbered_ports_and_brackets_ipv6(self):
        self.sync()
        dev = Device.objects.get(name="SLC-A")
        dev._custom_field_data["lx_mgmt_ip"] = "192.0.2.10"
        dev.validated_save()
        port7 = ConsoleServerPort.objects.get(device=dev, name="Port-07")
        self.assertEqual(self.render_link(port7), ("Open console", "ssh://192.0.2.10:3007"))
        # A port that isn't one of the unit's numbered ports gets no button.
        for name in ("ttyS12", "Port-", "Port-x1", "Console"):
            other = ConsoleServerPort.objects.create(device=dev, name=name)
            self.assertEqual(self.render_link(other)[0], "", name)
        # An IPv6 primary address is bracketed, so the port isn't read as part of it.
        from nautobot.dcim.models import Interface
        from nautobot.ipam.models import IPAddress, Namespace, Prefix

        active = Status.objects.get(name="Active")
        for model in (Prefix, IPAddress, Interface):
            active.content_types.add(ContentType.objects.get_for_model(model))
        Prefix.objects.create(
            prefix="2001:db8::/64", namespace=Namespace.objects.first(), status=active
        )
        ip = IPAddress.objects.create(address="2001:db8::10/64", status=active)
        iface = Interface.objects.create(device=dev, name="eth1", type="1000base-t", status=active)
        iface.ip_addresses.add(ip)
        dev.primary_ip6 = ip
        dev.validated_save()
        port7.refresh_from_db()
        self.assertEqual(self.render_link(port7)[1], "ssh://[2001:db8::10]:3007")

    def test_bootstrap_keeps_admin_changes_to_the_link_but_owns_its_url(self):
        from nautobot.extras.models import CustomLink, ObjectChange

        link = CustomLink.objects.get(name="Lantronix: Open console")
        good_url = link.target_url
        link.weight, link.button_class, link.target_url = 250, "success", "ssh://evil.example:22"
        link.validated_save()
        ensure_objects(cfg(), logging.getLogger("test"))
        link.refresh_from_db()
        self.assertEqual(
            (link.weight, link.button_class, link.target_url), (250, "success", good_url)
        )
        # A second run with nothing to correct writes nothing.
        before = link.last_updated
        changes = ObjectChange.objects.count()
        ensure_objects(cfg(), logging.getLogger("test"))
        link.refresh_from_db()
        self.assertEqual((link.last_updated, ObjectChange.objects.count()), (before, changes))

    def test_management_ip_field_rejects_hostnames(self):
        from nautobot.extras.models import CustomField

        regex = CustomField.objects.get(key="lx_mgmt_ip").validation_regex
        import re

        self.assertTrue(re.match(regex, "192.0.2.7"))
        self.assertFalse(re.match(regex, "evil.example"))

    def sync_with_states(self, states, ports):
        job = FakeJob()
        client = FakeClient([device()], ports, states={"dev-0001": states})
        source = PercepxionAdapter(job=job, config=cfg(RULES), client=client)
        job.source_adapter = source
        source.load()
        target = NautobotAdapter(job=job, config=cfg(RULES))
        target.load()
        diff = source.diff_to(target, flags=FLAGS)
        source.sync_to(target, flags=FLAGS)
        return diff
