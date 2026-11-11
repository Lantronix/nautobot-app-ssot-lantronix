"""A failed sync says why in the job log and the worker log. Runs under nautobot-server test."""

from unittest import mock

from nautobot.core.testing import TransactionTestCase, create_job_result_and_run_job
from nautobot.extras.choices import JobResultStatusChoices

from nautobot_ssot_lantronix.jobs import PercepxionDataSource
from nautobot_ssot_lantronix.percepxion.client import AuthError, PercepxionUnavailable
from nautobot_ssot_lantronix.tests.fakes import RULES, FakeClient, cfg, device, port

MODULE = "nautobot_ssot_lantronix.jobs"
JOB = "PercepxionDataSource"
SECRET = "sup3r-secret-pw"


def run_failing(error, source_cfg=None, clients=None):
    # Settings come from the test, not PERCEPXION_* in the environment.
    config = mock.patch.object(
        PercepxionDataSource,
        "source_config",
        new_callable=mock.PropertyMock,
        return_value=source_cfg or cfg(),
    )
    with config, mock.patch(f"{MODULE}.PercepxionClient") as client:
        client.return_value.login.side_effect = error
        result = create_job_result_and_run_job(MODULE, JOB, dryrun=True)
        if clients is not None:
            clients.append(client.return_value)
        return result


class JobFailureLoggingTests(TransactionTestCase):
    databases = ("default", "job_logs")

    def test_rejected_login_reason_reaches_job_and_worker_logs(self):
        with self.assertLogs("nautobot_ssot_lantronix.sync", "WARNING") as logs:
            result = run_failing(AuthError(f"POST /v2/user/login returned 401 {SECRET}"))
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_FAILURE)
        self.assertIn(str(result.pk), logs.output[0])
        self.assertIn("rejected the login", logs.output[0])
        entries = list(result.job_log_entries.values_list("message", flat=True))
        self.assertTrue(any("Sync failed" in m and "rejected the login" in m for m in entries))
        self.assertNotIn(SECRET, "".join(entries) + "".join(logs.output))

    def test_read_failure_reason_reaches_job_log(self):
        error = PercepxionUnavailable("POST /v1/job/jobgroup/search returned HTTP 400")
        with self.assertLogs("nautobot_ssot_lantronix.sync", "WARNING"):
            result = run_failing(error)
        entries = list(result.job_log_entries.values_list("message", flat=True))
        self.assertTrue(any("jobgroup/search returned HTTP 400" in m for m in entries))

    def test_session_is_ended_even_when_the_read_fails(self):
        clients = []
        with self.assertLogs("nautobot_ssot_lantronix.sync", "WARNING"):
            run_failing(
                PercepxionUnavailable("POST /v3/device/search returned HTTP 500"), clients=clients
            )
        clients[0].logout.assert_called_once()

    def test_job_log_says_when_tls_verification_is_off(self):
        from dataclasses import replace

        with self.assertLogs("nautobot_ssot_lantronix.sync", "WARNING"):
            result = run_failing(
                PercepxionUnavailable("POST /v3/device/search returned HTTP 500"),
                source_cfg=replace(cfg(), verify=False),
            )
        entries = list(result.job_log_entries.values_list("message", flat=True))
        self.assertTrue(any("TLS certificate verification is off" in m for m in entries))


class JobRunTests(TransactionTestCase):
    """The whole job through Nautobot's job runner, with a fake Percepxion."""

    databases = ("default", "job_logs")

    def setUp(self):
        super().setUp()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.dcim.models import (
            ConsoleServerPortTemplate,
            Device,
            DeviceType,
            Location,
            LocationType,
            Manufacturer,
        )
        from nautobot.extras.models import Status

        lantronix = Manufacturer.objects.create(name="Lantronix")
        dt = DeviceType.objects.create(manufacturer=lantronix, model="SLC9016-16E")
        for n in range(1, 17):
            ConsoleServerPortTemplate.objects.create(device_type=dt, name=f"Port-{n:02d}")
        site = LocationType.objects.create(name="Site")
        site.content_types.add(ContentType.objects.get_for_model(Device))
        active = Status.objects.get(name="Active")
        active.content_types.add(ContentType.objects.get_for_model(Device))
        Location.objects.create(name="Lab", location_type=site, status=active)

    def run_job(self, dryrun):
        fake = FakeClient([device()], [port(1, name="SLC-B")])
        config = mock.patch.object(
            PercepxionDataSource,
            "source_config",
            new_callable=mock.PropertyMock,
            return_value=cfg(RULES),
        )
        with config, mock.patch(f"{MODULE}.PercepxionClient", return_value=fake):
            result = create_job_result_and_run_job(MODULE, JOB, dryrun=dryrun)
        return result, fake

    def test_dry_run_writes_nothing_and_a_real_run_creates_the_device(self):
        from nautobot.dcim.models import ConsoleServerPort, Device
        from nautobot.extras.models import CustomLink

        result, fake = self.run_job(dryrun=True)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertFalse(Device.objects.filter(name="SLC-A").exists())
        self.assertFalse(CustomLink.objects.filter(name="Lantronix: Open console").exists())
        self.assertEqual(fake.logouts, 1)

        result, fake = self.run_job(dryrun=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        dev = Device.objects.get(name="SLC-A")
        self.assertEqual(dev.cf["percepxion_device_id"], "dev-0001")
        self.assertEqual(ConsoleServerPort.objects.get(device=dev, name="Port-01").label, "SLC-B")
        self.assertTrue(CustomLink.objects.filter(name="Lantronix: Open console").exists())
        self.assertEqual(fake.logouts, 1)

        # A second real run finds nothing to change.
        result, _ = self.run_job(dryrun=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertEqual(Device.objects.count(), 1)
