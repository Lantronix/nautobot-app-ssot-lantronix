"""Console cable management against the Nautobot ORM. Runs under nautobot-server test."""

import dataclasses
import logging
from unittest import mock

from diffsync.enum import DiffSyncFlags
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.core.testing import TransactionTestCase, create_job_result_and_run_job
from nautobot.extras.choices import JobResultStatusChoices

from ..cables import CABLE_LABEL, TAG, manage_cables
from ..diffsync.nautobot import NautobotAdapter
from ..diffsync.percepxion import PercepxionAdapter
from ..jobs import PercepxionDataSource
from ..percepxion.telemetry import PxPortState
from .fakes import RULES, FakeClient, cfg, device, port

MODULE = "nautobot_ssot_lantronix.jobs"
JOB = "PercepxionDataSource"
CISCO_SERIAL = "FOC1234X0AB"
FLAGS = DiffSyncFlags.SKIP_UNMATCHED_DST


class FakeJob:
    logger = logging.getLogger("test")
    source_adapter = None


# The re-cabled booth: the Cisco moved from port 14 to port 3, the EMG on port 5 is still being
# identified, and the seed cables on ports 1, 3 and 4 point at devices no longer there.
STATES = {
    "dev-0001": {
        1: PxPortState(1, "Disconnected", False),
        3: PxPortState(
            3, "Managed", True, "Cisco", "IOS", "", "C9300", CISCO_SERIAL, "edge-switch-01"
        ),
        4: PxPortState(4, "Disconnected", False),
        5: PxPortState(5, "Unidentified", False),
        14: PxPortState(14, "Disconnected", False),
    }
}
PORTS = [port(n) for n in (1, 3, 4, 5, 14)]
SEED = {1: "SLC-DVT1", 3: "firewall-01", 4: "lab-switch", 5: "emg-01", 14: "edge-switch-01"}


def build_world():
    """Device types, a location and the attached devices, each with one Console port."""
    from nautobot.dcim.models import (
        ConsolePort,
        ConsoleServerPortTemplate,
        Device,
        DeviceType,
        Location,
        LocationType,
        Manufacturer,
    )
    from nautobot.extras.models import Role, Status

    lantronix = Manufacturer.objects.create(name="Lantronix")
    dt = DeviceType.objects.create(manufacturer=lantronix, model="SLC9016-16E")
    for n in range(1, 17):
        ConsoleServerPortTemplate.objects.create(device_type=dt, name=f"Port-{n:02d}")
    device_ct = ContentType.objects.get_for_model(Device)
    site = LocationType.objects.create(name="Site")
    site.content_types.add(device_ct)
    active = Status.objects.get(name="Active")
    active.content_types.add(device_ct)
    lab = Location.objects.create(name="Lab", location_type=site, status=active)
    role = Role.objects.create(name="Network")
    role.content_types.add(device_ct)
    box = DeviceType.objects.create(
        manufacturer=Manufacturer.objects.create(name="Generic"), model="Box"
    )
    serials = {"edge-switch-01": CISCO_SERIAL, "emg-01": "0080A3EMG"}
    for name in SEED.values():
        dev = Device.objects.create(
            name=name,
            device_type=box,
            location=lab,
            role=role,
            status=active,
            serial=serials.get(name, ""),
        )
        ConsolePort.objects.create(device=dev, name="Console")


def seed_cables():
    """The cables someone drew before the move, SLC port number -> device name."""
    from nautobot.dcim.models import Cable, ConsolePort, ConsoleServerPort
    from nautobot.extras.models import Status

    connected = Status.objects.get_for_model(Cable).get(name="Connected")
    for number, name in SEED.items():
        Cable(
            termination_a=ConsoleServerPort.objects.get(
                device__name="SLC-A", name=f"Port-{number:02d}"
            ),
            termination_b=ConsolePort.objects.get(device__name=name, name="Console"),
            status=connected,
        ).validated_save()


def cabled_to():
    """SLC-A port name -> the device its cable ends on, for every cabled port."""
    from nautobot.dcim.models import ConsoleServerPort

    out = {}
    for csp in ConsoleServerPort.objects.filter(device__name="SLC-A"):
        if csp.cable_id is not None:
            out[csp.name] = csp.get_cable_peer().device.name
    return out


def slc_port(number):
    from nautobot.dcim.models import ConsoleServerPort

    return ConsoleServerPort.objects.get(device__name="SLC-A", name=f"Port-{number:02d}")


def counts():
    from nautobot.dcim.models import Cable
    from nautobot.extras.models import Note, Tag

    return Cable.objects.count(), Note.objects.count(), Tag.objects.filter(name=TAG).count()


class CableApplyTests(TestCase):
    """The apply layer called directly, after the same sync the job runs."""

    @classmethod
    def setUpTestData(cls):
        from ..bootstrap import ensure_objects

        build_world()
        ensure_objects(cfg(), logging.getLogger("test"))

    def sync(self, states=STATES):
        job = FakeJob()
        config = cfg(RULES)
        source = PercepxionAdapter(
            job=job, config=config, client=FakeClient([device()], PORTS, states=states)
        )
        job.source_adapter = source
        source.load()
        target = NautobotAdapter(job=job, config=config)
        target.load()
        source.sync_to(target, flags=FLAGS)
        return source

    def setUp(self):
        self.sync()
        seed_cables()

    def test_recabled_slc_moves_the_device_and_removes_disconnected_cables(self):
        from nautobot.dcim.models import Cable, Device
        from nautobot.extras.models import Note

        emg_cable = slc_port(5).cable
        with self.assertLogs("test", "INFO") as logs:
            plan = manage_cables(self.sync(), logging.getLogger("test"), dryrun=False)
        self.assertEqual(len(plan.deletes), 4)
        self.assertEqual(cabled_to(), {"Port-03": "edge-switch-01", "Port-05": "emg-01"})
        # The unidentified port's cable is the same cable, untouched.
        self.assertTrue(Cable.objects.filter(pk=emg_cable.pk).exists())

        new = slc_port(3).cable
        self.assertEqual(
            (new.status.name, new.label, list(new.tags.values_list("name", flat=True))),
            ("Connected", CABLE_LABEL, [TAG]),
        )
        cisco = Device.objects.get(name="edge-switch-01")
        note = Note.objects.get(assigned_object_id=cisco.pk).note
        self.assertIn("SLC-A Port-14", note)
        self.assertIn("SLC-A Port-03", note)
        self.assertIn("matched on serial", note)
        for name in ("SLC-DVT1", "lab-switch", "firewall-01"):
            dev = Device.objects.get(name=name)
            self.assertTrue(Note.objects.filter(assigned_object_id=dev.pk).exists())
        self.assertTrue(any("Created console cable SLC-A Port-03" in m for m in logs.output))

        # The next run finds nothing to change.
        self.assertTrue(manage_cables(self.sync(), logging.getLogger("test"), False).is_empty())

    def test_dry_run_logs_the_plan_and_writes_nothing(self):
        before = (cabled_to(), counts())
        with self.assertLogs("test", "INFO") as logs:
            plan = manage_cables(self.sync(), logging.getLogger("test"), dryrun=True)
        self.assertEqual((len(plan.deletes), len(plan.creates)), (4, 1))
        self.assertEqual((cabled_to(), counts()), before)
        self.assertEqual(counts()[2], 0)  # not even the tag
        self.assertTrue(any("would create console cable SLC-A Port-03" in m for m in logs.output))

    def test_ports_without_a_telemetry_state_are_never_changed(self):
        before = cabled_to()
        plan = manage_cables(self.sync(states={}), logging.getLogger("test"), dryrun=False)
        self.assertTrue(plan.is_empty())
        self.assertEqual(cabled_to(), before)

    def test_a_same_serial_device_outside_the_slcs_locations_is_not_cabled(self):
        from nautobot.dcim.models import Device, Location

        cisco = Device.objects.get(name="edge-switch-01")
        cisco.location = Location.objects.create(
            name="Remote", location_type=cisco.location.location_type, status=cisco.status
        )
        cisco.validated_save()
        plan = manage_cables(self.sync(), logging.getLogger("test"), dryrun=False)
        self.assertEqual(plan.creates, [])
        self.assertIn("attached_unmatched", {f.kind for f in plan.findings})
        # Port 3 keeps its cable (attached, but nothing in scope matches); port 14 is
        # Disconnected, so its cable goes whatever is on the far end.
        self.assertEqual(cabled_to(), {"Port-03": "firewall-01", "Port-05": "emg-01"})
        self.assertIsNone(Device.objects.get(name="edge-switch-01").console_ports.get().cable)

    def test_a_cable_to_a_patch_panel_is_left_alone(self):
        from nautobot.dcim.models import Cable, Device, FrontPort, RearPort

        panel = Device.objects.get(name="lab-switch")
        rear = RearPort.objects.create(device=panel, name="R1", type="8p8c", positions=1)
        front = FrontPort.objects.create(
            device=panel, name="F1", type="8p8c", rear_port=rear, rear_port_position=1
        )
        status = slc_port(4).cable.status
        slc_port(4).cable.delete()
        Cable(termination_a=slc_port(4), termination_b=front, status=status).validated_save()
        plan = manage_cables(self.sync(), logging.getLogger("test"), dryrun=False)
        self.assertIn("port_cabled_elsewhere", {f.kind for f in plan.findings})
        self.assertEqual(slc_port(4).get_cable_peer(), front)


class CableJobTests(TransactionTestCase):
    """The setting and the dry-run flag, through Nautobot's job runner."""

    databases = ("default", "job_logs")

    def setUp(self):
        super().setUp()
        build_world()
        result = self.run_job(dryrun=False, manage=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        seed_cables()
        self.before = (cabled_to(), counts())

    def run_job(self, dryrun, manage, **inputs):
        fake = FakeClient([device()], PORTS, states=STATES)
        config = mock.patch.object(
            PercepxionDataSource,
            "source_config",
            new_callable=mock.PropertyMock,
            return_value=dataclasses.replace(cfg(RULES), manage_cables=manage),
        )
        with config, mock.patch(f"{MODULE}.PercepxionClient", return_value=fake):
            return create_job_result_and_run_job(MODULE, JOB, dryrun=dryrun, **inputs)

    def test_setting_off_writes_no_cables(self):
        result = self.run_job(dryrun=False, manage=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertEqual((cabled_to(), counts()), self.before)
        # The per-run choice overrides the setting.
        self.run_job(dryrun=False, manage=True, manage_cables="off")
        self.assertEqual((cabled_to(), counts()), self.before)

    def test_dry_run_writes_nothing_and_a_run_with_no_inputs_follows_the_setting(self):
        from nautobot.dcim.models import Cable
        from nautobot.extras.models import ObjectChange

        result = self.run_job(dryrun=True, manage=True)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertEqual((cabled_to(), counts()), self.before)
        entries = list(result.job_log_entries.values_list("message", flat=True))
        self.assertTrue(any("Dry run, would create console cable" in m for m in entries))

        # No manage_cables input, as an API or event-triggered run sends: the setting decides.
        result = self.run_job(dryrun=False, manage=True)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertEqual(cabled_to(), {"Port-03": "edge-switch-01", "Port-05": "emg-01"})
        cable_ct = ContentType.objects.get_for_model(Cable)
        changes = ObjectChange.objects.filter(changed_object_type=cable_ct)
        self.assertTrue(changes.filter(action="create").exists())
        self.assertTrue(changes.filter(action="delete").exists())
