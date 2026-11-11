"""The console coverage gate job, run under nautobot-server test against real cables."""

import json
import logging

from django.contrib.contenttypes.models import ContentType
from nautobot.core.testing import TransactionTestCase, create_job_result_and_run_job
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
from nautobot.extras.choices import JobResultStatusChoices
from nautobot.extras.models import Role, Status

from nautobot_ssot_lantronix.bootstrap import ensure_objects
from nautobot_ssot_lantronix.constants import CF_MGMT_IP
from nautobot_ssot_lantronix.coverage.job import ConsoleCoverageGate
from nautobot_ssot_lantronix.coverage.orm import CF_PORT_STATE
from nautobot_ssot_lantronix.tests.fakes import cfg

MODULE = "nautobot_ssot_lantronix.coverage.job"
JOB = "ConsoleCoverageGate"


class CoverageGateJobTests(TransactionTestCase):
    databases = ("default", "job_logs")

    def setUp(self):
        super().setUp()
        # The app's custom fields (percepxion_device_id, lx_mgmt_ip, lx_port_state) and role.
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

        slc = Device.objects.create(
            name="slc-01", device_type=slc_type, role=slc_role, location=site, status=active
        )
        slc._custom_field_data["percepxion_device_id"] = "dev-0001"
        slc._custom_field_data[CF_MGMT_IP] = "192.0.2.10"
        slc.save()
        port = ConsoleServerPort.objects.create(device=slc, name="Port-02")
        port._custom_field_data[CF_PORT_STATE] = "Managed"
        port.save()

        covered = Device.objects.create(
            name="sw-01", device_type=sw_type, role=sw_role, location=site, status=active
        )
        console = ConsolePort.objects.create(device=covered, name="Console")
        Cable.objects.create(termination_a=console, termination_b=port, status=connected)
        uncabled = Device.objects.create(
            name="sw-02", device_type=sw_type, role=sw_role, location=site, status=active
        )
        ConsolePort.objects.create(device=uncabled, name="Console")

    def run_gate(self, devices):
        return create_job_result_and_run_job(MODULE, JOB, devices=devices)

    def report(self, result):
        f = result.files.get(name="console-coverage.json")
        return json.loads(f.file.read())

    def test_passes_when_every_device_is_covered(self):
        result = self.run_gate("sw-01")
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        rep = self.report(result)
        self.assertEqual(rep["result"], "pass", rep["devices"])
        self.assertEqual(rep["devices"][0]["ssh_port"], 3002)
        self.assertEqual(rep["devices"][0]["slc_host"], "192.0.2.10")

    def test_fails_and_says_why_when_a_device_has_no_cable(self):
        result = self.run_gate("sw-01, sw-02")
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_FAILURE)
        by = {d["device"]: d for d in self.report(result)["devices"]}
        self.assertTrue(by["sw-01"]["covered"], by["sw-01"]["reasons"])
        self.assertEqual(by["sw-02"]["reasons"], ["no_console_cable"])

    def test_role_finds_the_job_by_name(self):
        # lantronix.oob's oob_coverage_gate role looks the job up by this name.
        self.assertEqual(ConsoleCoverageGate.Meta.name, "AOOB console coverage gate")
        self.assertTrue(ConsoleCoverageGate.Meta.read_only)
