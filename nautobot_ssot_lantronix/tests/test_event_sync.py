"""Focused sync, sync requests, the change watch and the events URL. Runs under
nautobot-server test."""

import json
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from nautobot.core.testing import TransactionTestCase, create_job_result_and_run_job
from nautobot.extras.choices import JobResultStatusChoices

from ..jobs import (
    FOCUS_DELAY,
    REQUEUE_DELAY,
    PercepxionDataSource,
    request_sync,
    sync_requests,
)
from ..percepxion.events import sign
from ..percepxion.telemetry import PxPortState
from .fakes import ORG, RULES, FakeClient, cfg, device, port

MODULE = "nautobot_ssot_lantronix.jobs"
A, B = "dev-0001", "dev-0002"
SECRET = "w" * 40


def build_world():
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


def enable_focused_job(enabled=True):
    from nautobot.extras.models import Job as JobModel

    JobModel.objects.filter(module_name=MODULE, job_class_name="PercepxionFocusedSync").update(
        enabled=enabled
    )


def state(number, attached=True, hostname="sw1"):
    if not attached:
        return PxPortState(number, "Disconnected", False)
    return PxPortState(number, "Managed", True, "Cisco", "IOS", "", "C9300", "S1", hostname)


class TwoUnits(FakeClient):
    def __init__(self, names=("a1", "b1"), states=None):
        super().__init__(
            [device(), device(B, "SLC-B", serial="000000000002")],
            [port(1, name=names[0]), port(1, name=names[1], pxid=B)],
            states=states or {A: {1: state(1)}, B: {1: state(1)}},
        )
        self.state_calls = []
        self.logins = 0

    def login(self):
        self.logins += 1

    def port_states(self, pxid, org=None):
        self.state_calls.append(pxid)
        return super().port_states(pxid, org)


def patched(client):
    config = mock.patch.object(
        PercepxionDataSource,
        "source_config",
        new_callable=mock.PropertyMock,
        return_value=cfg(RULES),
    )
    return config, mock.patch(f"{MODULE}.PercepxionClient", return_value=client)


def run(job_class, client, **kwargs):
    config, cls = patched(client)
    with config, cls:
        return create_job_result_and_run_job(MODULE, job_class, **kwargs)


def label(device_name):
    from nautobot.dcim.models import ConsoleServerPort

    return ConsoleServerPort.objects.get(device__name=device_name, name="Port-01").label


class FocusedSyncTests(TransactionTestCase):
    databases = ("default", "job_logs")

    def setUp(self):
        super().setUp()
        cache.clear()
        build_world()
        result = run("PercepxionDataSource", TwoUnits(), dryrun=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)

    def test_only_the_requested_unit_is_read_and_changed(self):
        sync_requests().add([B])
        client = TwoUnits(names=("a1-new", "b1-new"))
        result = run("PercepxionFocusedSync", client, dryrun=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertEqual(client.state_calls, [B])
        self.assertEqual((label("SLC-A"), label("SLC-B")), ("a1", "b1-new"))
        self.assertEqual(sync_requests().take(), set())

    def test_nothing_waiting_means_no_percepxion_call(self):
        client = TwoUnits()
        result = run("PercepxionFocusedSync", client, dryrun=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertEqual(client.logins, 0)

    def test_running_sync_puts_the_request_back_for_later(self):
        from nautobot.extras.models import Job as JobModel
        from nautobot.extras.models import JobResult

        JobResult.objects.create(
            name="running",
            job_model=JobModel.objects.get(
                module_name=MODULE, job_class_name="PercepxionDataSource"
            ),
            status=JobResultStatusChoices.STATUS_STARTED,
        )
        sync_requests().add([B])
        client = TwoUnits()
        with mock.patch(f"{MODULE}.request_sync") as req:
            result = run("PercepxionFocusedSync", client, dryrun=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        self.assertEqual(client.state_calls, [])
        self.assertEqual(req.call_args.args[0], {B})
        self.assertEqual(req.call_args.kwargs["delay"], REQUEUE_DELAY)


class ChangeWatchTests(TransactionTestCase):
    databases = ("default", "job_logs")

    def setUp(self):
        super().setUp()
        cache.clear()
        build_world()
        result = run("PercepxionDataSource", TwoUnits(), dryrun=False)
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)

    def watch(self, client):
        with (
            mock.patch(f"{MODULE}.app_settings.load", return_value=cfg(RULES)),
            mock.patch(f"{MODULE}.PercepxionClient", return_value=client),
            mock.patch(f"{MODULE}.request_sync") as req,
        ):
            result = create_job_result_and_run_job(MODULE, "PercepxionChangeWatch")
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_SUCCESS, result.traceback)
        return result, req

    def test_first_check_is_a_baseline_then_a_change_requests_that_unit(self):
        result, req = self.watch(TwoUnits())
        req.assert_not_called()
        self.assertEqual(result.result, {"watched": 2, "changed": 0, "unread": 0})
        unplugged = TwoUnits(states={A: {1: state(1)}, B: {1: state(1, attached=False)}})
        result, req = self.watch(unplugged)
        self.assertEqual(req.call_args.args[0], {B})
        self.assertEqual(result.result["changed"], 1)

    def test_unreadable_unit_is_no_change(self):
        self.watch(TwoUnits())
        client = TwoUnits()
        client.state_error = {A, B}
        result, req = self.watch(client)
        req.assert_not_called()
        self.assertEqual(result.result["unread"], 2)

    def test_only_units_in_nautobot_are_read(self):
        from nautobot.dcim.models import Device

        Device.objects.filter(name="SLC-B").delete()
        client = TwoUnits()
        self.watch(client)
        self.assertEqual(client.state_calls, [A])


class RequestSyncTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create(username="px-sync")
        enable_focused_job()

    def test_requests_join_one_waiting_job(self):
        from nautobot.extras.models import JobResult

        with mock.patch.object(JobResult, "enqueue_job") as enqueue:
            self.assertTrue(request_sync([A], self.user))
            self.assertFalse(request_sync([B, A], self.user))
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[1], self.user)
        self.assertEqual(enqueue.call_args.kwargs["celery_kwargs"], {"countdown": FOCUS_DELAY})
        self.assertFalse(enqueue.call_args.kwargs["job_kwargs"]["dryrun"])
        self.assertEqual(sync_requests().take(), {A, B})

    def test_disabled_job_refuses_and_keeps_the_devices_for_later(self):
        from nautobot.extras.models import JobResult

        enable_focused_job(False)
        with self.assertRaises(RuntimeError):
            request_sync([A], self.user)
        enable_focused_job(True)
        with mock.patch.object(JobResult, "enqueue_job") as enqueue:
            self.assertTrue(request_sync([B], self.user))
        enqueue.assert_called_once()
        self.assertEqual(sync_requests().take(), {A, B})


APP = {
    "url": "https://px.example/api",
    "username": "u",
    "password": "p",
    "organization_id": ORG,
    "default_location": "Lab",
    "webhook_secrets": [SECRET],
    "sync_user": "px-sync",
}


def plugins(**app):
    return {**settings.PLUGINS_CONFIG, "nautobot_ssot_lantronix": {**APP, **app}}


def event(event_id="evt-1", event_type="percepxion.port.attached_changed", org=ORG, pxid=A):
    return json.dumps(
        {
            "spec_version": "1",
            "id": event_id,
            "type": event_type,
            "source": "test",
            "occurred_at": "2026-11-24T00:00:00Z",
            "tenant_id": org,
            "device": {"id": pxid, "name": "SLC-A", "serial": "1", "model": "SLC9016"},
            "data": {"port": 3, "change": "detached"},
        }
    ).encode()


class EventsViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create(username="px-sync")
        enable_focused_job()
        self.url = reverse("plugins-api:nautobot_ssot_lantronix-api:events")

    def post(self, body, secret=SECRET, headers=None):
        h = sign(secret, body) if headers is None else headers
        extra = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in h.items()}
        return self.client.post(self.url, body, content_type="application/json", **extra)

    def test_signed_event_requests_a_focused_sync_as_the_sync_user(self):
        with (
            override_settings(PLUGINS_CONFIG=plugins()),
            mock.patch("nautobot_ssot_lantronix.api.views.request_sync") as req,
        ):
            self.assertEqual(self.post(event()).status_code, 202)
            self.assertEqual(self.post(event()).status_code, 204)  # same event ID
        req.assert_called_once_with([A], self.user)

    def test_other_event_types_are_ignored(self):
        with (
            override_settings(PLUGINS_CONFIG=plugins()),
            mock.patch("nautobot_ssot_lantronix.api.views.request_sync") as req,
        ):
            r = self.post(event(event_type="percepxion.rule.triggered"))
        self.assertEqual(r.status_code, 204)
        req.assert_not_called()

    def test_refusals(self):
        body = event()
        with (
            override_settings(PLUGINS_CONFIG=plugins()),
            mock.patch("nautobot_ssot_lantronix.api.views.request_sync") as req,
        ):
            self.assertEqual(self.post(body, secret="x" * 40).status_code, 401)
            self.assertEqual(self.post(body, headers={}).status_code, 401)
            self.assertEqual(self.post(body, headers=sign(SECRET, body, 1)).status_code, 401)
            self.assertEqual(self.post(event(org="org-other")).status_code, 400)
            self.assertEqual(self.post(b"{}").status_code, 400)
            self.assertEqual(self.post(b"").status_code, 400)
            self.assertEqual(self.post(event(pxid="bad id!")).status_code, 400)
            self.assertEqual(self.client.get(self.url).status_code, 405)
        req.assert_not_called()

    def test_not_found_until_events_are_fully_set_up(self):
        with mock.patch("nautobot_ssot_lantronix.api.views.request_sync") as req:
            for app in ({"webhook_secrets": []}, {"sync_user": ""}, {"sync_user": "nobody"}):
                with override_settings(PLUGINS_CONFIG=plugins(**app)):
                    self.assertEqual(self.post(event()).status_code, 404, app)
            enable_focused_job(False)
            with override_settings(PLUGINS_CONFIG=plugins()):
                self.assertEqual(self.post(event()).status_code, 404)
        req.assert_not_called()
